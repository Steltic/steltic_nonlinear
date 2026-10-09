"""
model_gmnia.py -- rebuild a Steltic elastic model as a GMNIA model in openseespy.

  * every column / beam / brace -> `forceBeamColumn` (or `dispBeamColumn` with --fast), 3-D
    `Corotational` transform, `Lobatto` integration (5 points), FIBRE section from sections_fiber
  * members subdivided (default 4 elements/column, 4/beam, 6/brace) so member bows are representable
  * geometric imperfections: whole-building out-of-plumb psi (H/500) in +/-X or +/-Y, member
    out-of-straightness L/1000 (half-sine) in the weak-axis direction of columns and out-of-plane
    for braces (imperfections.py decides directions/magnitudes)
  * Steltic pins (elasticBeamColumn -releasey/-releasez) -> duplicate end node + zeroLength with stiff
    springs on the retained DOFs and NOTHING on the released rotation (true pin, no constraint chains)
  * brace ends: pinned about all three axes (gusset idealisation); at an X-crossing the two diagonals share their
    mid-node translations (pinned crossing) when the HR design says they are connected there (calc_package:
    "connected at the crossing", Lc <= 0.6 Lwp; NL-R2-28 (DDM)), otherwise they are independent (K = 1 on the
    full diagonal)
  * rigid diaphragms and master nodes exactly as Steltic recorded them
  * bases: as recorded (fixed / pinned)
  * optional rigid_end_offset on primary beams: stiff elasticBeamColumn stubs
    (~0.05L, ×1000 EI) for continuous FR beam–column continuity (Liu lesson)
  * buckling-restrained braces (label "BRB-Asc..", NL-02): corotTruss that does NOT buckle -- yield force
    Pysc = Fysc*Asc (AISC 341-22 F4.5b nominal, core area from the label / package, never the HR truss area KF*Asc),
    stiffness = the HR truss (E*KF*Asc/L) or AISC 342-22 Eq. C3-3 when core lengths are supplied; Steel01 with the
    model hardening. Yield ratio = axial deformation / Delta_y (solver.yield_state hook); excluded from the
    brace-buckling census.
  * EBF links (NL-03, pushover.hinge_models.find_links): a zeroLength shear spring (global Z) in series with the
    link's fibre chain -- Vn = 0.6 Fy Alw (AISC 341-22 F3.5b.2, nominal), Ks = G d tw / e (AISC 342-22 Commentary
    C-E2-2), Steel01 with the model hardening -- so link SHEAR yielding limits the GMNIA capacity. In the elastic
    (transfer-gate) build the spring is rigid, reproducing Steltic's shear-rigid elastic model; the inelastic
    build is therefore slightly softer (disclosed in builder.log). Link axial interaction (Pr/Pc > 0.15) not applied.

Steltic orientation decoding (engine3d):
  transf 1: vecxz (1,0,0) -> column strong axis resists Y (weak-axis bow in X)
  transf 2: vecxz (0,1,0) -> column strong axis resists X (weak-axis bow in Y)
  transf 3: vecxz (0,0,1) -> beams; major release = rotation about local y = global Y for X-beams,
                              global X for Y-beams; minor release = global Z
"""
import math
import openseespy.opensees as ops
from .ingest import decode_tag
from .sections_fiber import FiberSectionBuilder, elastic_props

SUB_NODE0 = 10_000_000
PIN_NODE0 = 30_000_000
PIN_ELE0 = 40_000_000
SUB_ELE0 = 100_000
LINK_NODE0 = 50_000_000   # NL-03: link shear-spring node LINK_NODE0 + member tag
LINK_ELE0 = 60_000_000    # NL-03: link shear-spring zeroLength LINK_ELE0 + member tag
BRB_ELE0 = 70_000_000     # NL-02: BRB corotTruss BRB_ELE0 + member tag
SPRING_MAT0 = 80_000_000  # BRB / link spring materials
BRACE_NODE0 = 85_000_000  # NL-R2-27: deck node of a beam lateral-torsional brace (BRACE_NODE0 + running count)
BRACE_ELE0 = 86_000_000   # NL-R2-27: zeroLength deck brace (BRACE_ELE0 + running count)
TIED_BRACE_NSUB = 8      # review D2: minimum sub-elements of an X diagonal tied at its crossing
BRACE_CONTINUOUS_N = 8    # NL-R2-27: L / Lb above this -> continuous bracing (every interior chain node braced)
K_TRANS = 1.0e9      # kip/in   stiff pin springs
K_ROT = 1.0e10       # kip-in/rad


class GMNIAModel:
    def __init__(self, nm, cfg, nsub=(4, 4, 6), residual="lehigh", Fy=None, hardening=0.002,
                 elastic=False, fast=False, out_of_plumb=(None, 0.0), bow=1 / 1000.0, bow_sign=+1,
                 brace_bow=1 / 1000.0, nip=5, brace_pins=True, rigid_end_offset=False):
        self.nm, self.cfg = nm, cfg
        self.nsub_col, self.nsub_beam, self.nsub_brace = nsub
        self.residual = residual
        self.Fy = Fy if Fy is not None else float(cfg.get("Fy", 50.0))
        self.hardening = hardening
        self.elastic = elastic
        self.fast = fast
        self.oop_dir, self.psi = out_of_plumb
        self.bow, self.bow_sign, self.brace_bow = bow, bow_sign, brace_bow
        self.nip = nip
        self.brace_pins = brace_pins
        # Liu lesson: continuous FR beam–column continuity via stiff end stubs.
        # False/0 = off; True -> 0.05L; float = fraction of L (clamped 3–12 in).
        self.rigid_end_offset = rigid_end_offset
        self.elems = []          # dict(tag, mtag, kind, role, section, secTag, s, n1, n2, L)
        self.sub_nodes = {}      # mtag -> [node tags along member incl. ends]
        self.secs = {}           # (label, kind, axis) -> secTag
        self.sec_props = {}
        self.pins = []           # zeroLength tags
        self.masters = sorted(t for t in nm.nodes if t % 100000 == 99999)
        self.builder = None
        self.Ecol = {}           # mtag -> transf tag
        self.nonbuckling = set() # member tags excluded from the brace-buckling census (BRBs)
        self.links = {}          # member tag -> link info (NL-03)
        self.special_log = []    # BRB / link modelling notes (also appended to builder.log)
        self._grav_geo = None    # NL-R2-03: HR static-model load-path geometry (False = unavailable -> legacy two-way)
        self._grav_fail = None
        self._grav_loc = {}
        self.gravity_by_member = {}
        self.gravity_notes = []
        self.beam_bracing = True     # NL-R2-27: brace diaphragm-level beams where the HR design relies on deck / infill bracing
        self.braced = {}             # member tag -> (mode, brace positions in in from n1, Lb_in)

    # ------------------------------------------------------------------ geometry helpers
    def _coord(self, tag):
        x, y, z = self.nm.nodes[tag]
        if self.oop_dir == "X":
            x += self.psi * z
        elif self.oop_dir == "Y":
            y += self.psi * z
        return x, y, z

    def _transf_for(self, m):
        if m.kind == "col":
            return m.transf if m.transf in (1, 2) else 1
        if m.kind == "beam":
            return 3
        # brace: out-of-plane vector
        i1, j1, _ = decode_tag(m.n1); i2, j2, _ = decode_tag(m.n2)
        return 4 if j1 == j2 else 5        # X-frame brace -> vecxz (0,1,0) ; Y-frame -> (1,0,0)

    def _bow_vector(self, m):
        """Unit vector of the member bow and its magnitude (fraction of L)."""
        if m.kind == "col":
            tr = self._transf_for(m)
            v = (0.0, 1.0, 0.0) if tr == 2 else (1.0, 0.0, 0.0)     # weak-axis direction
            return v, self.bow * self.bow_sign
        if m.kind == "brace":
            tr = self._transf_for(m)
            v = (0.0, 1.0, 0.0) if tr == 4 else (1.0, 0.0, 0.0)     # out of the frame plane
            return v, self.brace_bow
        return (0.0, 0.0, 0.0), 0.0

    # ------------------------------------------------------------------ build
    def _gravity_geometry(self):
        """NL-R2-03: HR static-model geometry for the gravity distribution, built once per model (it wipes the
        OpenSees domain, so it runs before the GMNIA build)."""
        if self._grav_geo is None:
            from . import portal_adapter as PA
            from .loads import hr_gravity_geometry
            if PA.is_portal(self.cfg):
                self._grav_geo = False; self._grav_fail = "portal frame (portal_adapter loads)"
            else:
                try:
                    self._grav_geo = hr_gravity_geometry(self.cfg)
                except Exception as ex:                                   # never silent: logged in builder.log
                    self._grav_geo = False; self._grav_fail = "%s: %s" % (type(ex).__name__, ex)
        return self._grav_geo

    def build(self, with_mass=False):
        nm = self.nm
        if not self.elastic:
            self._gravity_geometry()             # NL-R2-03 (before ops.wipe: builds the HR static model)
        self.elems, self.sub_nodes, self.secs, self.sec_props, self.pins = [], {}, {}, {}, []
        ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
        for t in nm.nodes:
            ops.node(t, *self._coord(t))
        for t, fl in nm.fixes.items():
            ops.fix(t, *fl)
        for t in self.masters:
            if t not in nm.fixes:
                ops.fix(t, 0, 0, 1, 1, 1, 0)
        ops.geomTransf("Corotational", 1, 1.0, 0.0, 0.0)
        ops.geomTransf("Corotational", 2, 0.0, 1.0, 0.0)
        ops.geomTransf("Corotational", 3, 0.0, 0.0, 1.0)
        ops.geomTransf("Corotational", 4, 0.0, 1.0, 0.0)
        ops.geomTransf("Corotational", 5, 1.0, 0.0, 0.0)
        ops.uniaxialMaterial("Elastic", 1, K_TRANS)
        ops.uniaxialMaterial("Elastic", 2, K_ROT)
        self.builder = FiberSectionBuilder(ops, Fy=self.Fy, hardening=self.hardening,
                                           residual=self.residual, elastic=self.elastic, mat_tag0=1000)
        self.nonbuckling, self.special_log, self._spring_mat = set(), [], SPRING_MAT0
        self.braced, self._nbrace, self._brace_slaves = {}, 0, {}
        self._prepare_special()
        if self._grav_geo is False and not self.elastic:
            self.special_log.append("gravity: HR static-model distribution unavailable (%s) -- legacy two-way 45-degree "
                                    "tributary used (ignores one-way decks / infill)" % self._grav_fail)
        self._x_tie_plan()                                         # NL-R2-28 (DDM), before the chains are built
        for m in nm.members:
            self._add_member(m)
        if self.braced:
            nc = sum(1 for v in self.braced.values() if v[0] == "continuous")
            self.special_log.append(
                "beam bracing (NL-R2-27): %d diaphragm-level beams braced against lateral displacement and twist at their "
                "chain nodes by the deck (rigid-diaphragm deck node + zeroLength) -- %d continuously (HR Lb <= L/%d), %d at "
                "the HR design's brace spacing Lb (infill / joist points); lateral-torsional buckling BETWEEN brace points "
                "is not represented and stays a Chapter F check of the HR design (AISC 360-22 App. 1, 1.3.1)"
                % (len(self.braced), nc, BRACE_CONTINUOUS_N, len(self.braced) - nc))
        if getattr(self, "_lb_conflicts", None):
            self.special_log.append("beam bracing: HR groups with different Lb for the same role/section -> the LARGEST Lb "
                                    "used: %s" % "; ".join("%s %s %s" % (r, sct, v) for (r, sct), v in sorted(self._lb_conflicts.items())[:6]))
        self._tie_x_crossings()                                    # NL-R2-28 (DDM)
        for note in self.special_log:
            self.builder.log.append((0, "special", "note", 0, note))
        for master, slaves in nm.diaphragms.items():
            ops.rigidDiaphragm(3, master, *slaves)
        for master, deck in self._brace_slaves.items():             # NL-R2-27 deck brace nodes follow the floor
            ops.rigidDiaphragm(3, master, *deck)
        if with_mass:
            for t, mm in nm.masses.items():
                ops.mass(t, *mm)
            mmin = min((mm[0] for mm in nm.masses.values() if mm[0] > 0), default=1.0)
            for t in ops.getNodeTags():
                if t not in nm.masses:
                    ops.mass(t, *([1e-8 * mmin] * 6))
        return self

    def _is_secondary(self, m):
        """Purlins/girts/eave struts — keep elastic (fibre secondaries cause spurious local buckling)."""
        sec = str(m.section).upper().replace(" ", "")
        if ("Z250" in sec) or sec.startswith("800Z") or sec.startswith("600Z"):
            return True
        # Sena/CFS09–10 export labels (not catalog Z sections)
        for tok in ("EAVE_STRUT", "RESTRAINT_PURLIN", "RESTRAINT_GIRT", "PURLIN", "GIRT", "EAVESTRUT"):
            if tok in sec:
                return True
        return False

    def _section(self, m):
        axis = "y" if m.kind == "col" else "z"
        key = (m.section.upper(), m.kind, axis)
        if key not in self.secs:
            tag = len(self.secs) + 1
            props = self.builder.build(tag, m.section, m.kind, axis=axis)
            ops.beamIntegration("Lobatto", tag, tag, self.nip)
            self.secs[key] = tag
            self.sec_props[tag] = dict(label=m.section, kind=m.kind, **props)
        return self.secs[key]

    def _pin(self, grid_node, dup_node, released, axis=None):
        """zeroLength from grid_node to dup_node: stiff on all DOFs except `released` (1..6).
        With `axis` (a unit vector) the spring's local x is aligned with the member so that dir 4 is the
        member's own torsion -- a brace pin releases bending (5, 6) but keeps torsion, otherwise the
        brace would have a free rigid-body twist and a singular stiffness matrix."""
        dirs = [d for d in range(1, 7) if d not in released]
        mats = [1 if d <= 3 else 2 for d in dirs]
        tag = PIN_ELE0 + len(self.pins) + 1
        args = ["-mat", *mats, "-dir", *dirs]
        if axis is not None:
            ax, ay, az = axis
            # any vector not parallel to the axis for the local y direction
            ref = (0.0, 0.0, 1.0) if abs(az) < 0.9 else (1.0, 0.0, 0.0)
            yx, yy, yz = (ref[1] * az - ref[2] * ay, ref[2] * ax - ref[0] * az, ref[0] * ay - ref[1] * ax)
            args += ["-orient", ax, ay, az, yx, yy, yz]
        ops.element("zeroLength", tag, grid_node, dup_node, *args)
        self.pins.append(tag)
        return tag


    # ------------------------------------------------------------------ NL-R2-28 (DDM): X-brace crossings
    def _hr_x_crossing_sections(self):
        """Brace sections the HR design treats as X-braces CONNECTED AT THE CROSSING (calc_package members[].inputs:
        configuration / note mentions the crossing and Lc_in <= 0.6 x the work-point length)."""
        out = set()
        for m in (self.nm.calc_package or {}).get("members", []) or []:
            inp = m.get("inputs") or {}
            if str(inp.get("role", "")) != "brace" or not inp.get("section"):
                continue
            txt = " ".join(str(inp.get(k, "")) for k in ("configuration", "note", "Lc_note")).lower()
            try:
                lc = float(inp.get("Lc_in")); lwp = float(inp.get("Lwp_in") or inp.get("workpoint_length_in") or inp.get("length_in"))
            except (TypeError, ValueError):
                continue
            if "cross" in txt and lwp > 0 and lc <= 0.6 * lwp:
                out.add(str(inp["section"]).upper().replace(" ", ""))
        return out

    @staticmethod
    def x_pairs(braces, tol=1.0):
        """Pairs of brace members whose work-point lines cross at both their mid-lengths (an X in one bay).
        braces: [(tag, p1, p2)] -> [(tag_a, tag_b)]. tol: in."""
        mids = [(t, tuple((a[q] + b[q]) / 2 for q in range(3)), tuple(b[q] - a[q] for q in range(3))) for t, a, b in braces]
        out, used = [], set()
        for i in range(len(mids)):
            ta, ma, da = mids[i]
            if ta in used:
                continue
            for j in range(i + 1, len(mids)):
                tb, mb, db = mids[j]
                if tb in used or math.dist(ma, mb) > tol:
                    continue
                cr = (da[1] * db[2] - da[2] * db[1], da[2] * db[0] - da[0] * db[2], da[0] * db[1] - da[1] * db[0])
                if math.sqrt(sum(c * c for c in cr)) < 0.1 * math.sqrt(sum(c * c for c in da)) * math.sqrt(sum(c * c for c in db)):
                    continue                                       # (anti)parallel: not an X
                out.append((ta, tb)); used.update((ta, tb))
                break
        return out

    def _x_tie_plan(self):
        """NL-R2-28 (DDM): {brace tag: partner tag} of the X pairs that will be connected at the crossing -- decided from
        the work-point geometry BEFORE the chains are built, so the tied diagonals get their half-length imperfection
        (review D2) and at least TIED_BRACE_NSUB sub-elements."""
        self.x_ties = {}
        if self.elastic:
            return
        secs = self._hr_x_crossing_sections()
        self._x_secs = secs
        if not secs:
            return
        from pushover import sections_db as SDB
        cand = [m for m in self.nm.members if m.kind == "brace" and not SDB.is_brb(m.section)
                and str(m.section).upper().replace(" ", "") in secs]
        for ta, tb in self.x_pairs([(m.tag, self._coord(m.n1), self._coord(m.n2)) for m in cand]):
            self.x_ties[ta] = tb; self.x_ties[tb] = ta

    @staticmethod
    def bow_offset(f, L, bmag, tied=False):
        """Member bow at fraction f of a chain of length L: half-sine bmag * L. A diagonal tied at its crossing (review D2)
        adds an antisymmetric half-sine of bmag * L / 2 on each half -- the half-length buckling mode with the crossing at
        rest, out-of-straightness L/2 / 1000 between brace points (AISC 360-22 App. 1, 1.2.2a User Note: 1/1,000
        member out-of-straightness; 1.3.3b: the pattern with the greatest destabilizing effect)."""
        off = bmag * L * math.sin(math.pi * f)
        if tied:
            off += bmag * (L / 2) * math.sin(2 * math.pi * f)
        return off

    def _tie_x_crossings(self):
        """NL-R2-28 (DDM): where the HR design says the two diagonals of an X are connected at the crossing (and designs
        the compression diagonal on half its work-point length), the GMNIA connects them too -- the mid chain nodes share
        their translations (pinned crossing, equalDOF 1-3). The restraint the tension diagonal gives the compression
        diagonal then comes out of the analysis instead of an assumed K (AISC 360-22 App. 1, 1.3.1(a): all component and
        connection deformations of the structure as designed). Before: the diagonals were independent (K = 1 on the full
        length), i.e. ~1/4 of the buckling load the HR design relied on (Ex21: HSS5-1/2X5-1/2X3/8, KL/r 319 vs 171).
        Review D2: a tied diagonal also carries an antisymmetric (L/2)/1000 half-sine on each half (see _add_member):
        the full-length L/1000 bow alone is symmetric about the crossing and never seeds the half-length mode (pinned X
        HSS5X5X3/8, 600 x 384 in: 81.1 kip with the full bow only vs 50.4 with the seed; Euler half length 48.8)."""
        done = set()
        for ta, tb in sorted(self.x_ties.items()):
            if ta in done:
                continue
            na = self.sub_nodes[ta][len(self.sub_nodes[ta]) // 2]
            nb = self.sub_nodes[tb][len(self.sub_nodes[tb]) // 2]
            ops.equalDOF(na, nb, 1, 2, 3)
            done.update((ta, tb))
        if self.x_ties:
            self.special_log.append(
                "X-brace crossings (NL-R2-28): %d X pairs of %s connected at the crossing (mid chain nodes share their "
                "translations, rotations free) as the HR design assumes (calc_package: connected at the crossing, Lc <= "
                "0.6 Lwp); each tied diagonal carries the L/1000 full-length bow plus an antisymmetric (L/2)/1000 half-sine "
                "per half (out-of-straightness between brace points, AISC 360-22 App. 1, 1.2.2a User Note / 1.3.3b)%s"
                % (len(self.x_ties) // 2, ", ".join(sorted(self._x_secs)),
                   "; tied diagonals use >= %d sub-elements" % TIED_BRACE_NSUB))

    # ------------------------------------------------------------------ NL-R2-27 beam bracing by the deck / infill
    def _hr_lb(self):
        """(role, SECTION) -> unbraced length Lb (in) the HR member design used (calc_package members[].inputs.Lb_in).
        When groups of the same role and section disagree the LARGEST Lb is kept (least bracing, conservative)."""
        if getattr(self, "_lb_map", None) is None:
            out, conflicts = {}, {}
            for m in (self.nm.calc_package or {}).get("members", []) or []:
                inp = m.get("inputs") or {}
                lb, sec, role = inp.get("Lb_in"), inp.get("section"), inp.get("role")
                try:
                    lb = float(lb)
                except (TypeError, ValueError):
                    continue
                if not sec or not role or lb < 0:
                    continue
                key = (str(role), str(sec).upper().replace(" ", ""))
                if key in out and abs(out[key] - lb) > 1e-6:
                    conflicts.setdefault(key, sorted({out[key]}))
                    conflicts[key] = sorted(set(conflicts[key]) | {lb})
                out[key] = max(out.get(key, 0.0), lb)
            self._lb_map, self._lb_conflicts = out, conflicts
        return self._lb_map

    def _diaphragm_of(self, node):
        if getattr(self, "_slave_master", None) is None:
            self._slave_master = {t: mst for mst, sl in self.nm.diaphragms.items() for t in sl}
        return self._slave_master.get(node)

    def _brace_plan(self, m, L, off):
        """NL-R2-27: how the deck braces beam m -- None (unbraced: no diaphragm at both ends, no HR Lb, or Lb ~ L),
        ("continuous", (), Lb) or ("discrete", positions_in, Lb) with brace points every Lb from n1 (Lb from the HR
        design). AISC 360-22 App. 1, 1.3.2c defines Lb between points braced against lateral displacement of the
        compression flange or against twist; the GMNIA braces exactly those points, so the inelastic analysis does
        not invent lateral-torsional buckling over a length the design (and the building) braces."""
        if not self.beam_bracing or self._is_secondary(m) or m.tag in self.links:
            return None
        mst = self._diaphragm_of(m.n1)
        if mst is None or self._diaphragm_of(m.n2) != mst:
            return None
        lb = self._hr_lb().get((str(m.role), str(m.section).upper().replace(" ", "")))
        if lb is None:
            return None
        if lb <= L / BRACE_CONTINUOUS_N:
            return ("continuous", (), lb)
        # review: brace spacing never shorter than the design Lb (floor, not round: L/Lb 2.6 gave 0.87 Lb) -- conservative
        n = int(math.floor(L / lb + 1e-6))
        if n < 2:
            return None
        xs = [k * L / n for k in range(1, n)]
        xs = [x for x in xs if off + 1.0 < x < L - off - 1.0]
        # chain nodes are moved TO the brace points only when the gravity is applied by position (NL-R2-03 HR load
        # path); the legacy tributary loads (and the elastic transfer-gate build) keep the regular chain and brace
        # only the chain nodes that already sit at a brace point
        return ("discrete", xs, lb, bool(self._grav_geo)) if xs else None

    @staticmethod
    def _brace_select(plan, xs_chain, nodes, L):
        """Interior chain nodes to brace: all of them for continuous bracing, else those within 2 % of L of a brace point."""
        if plan[0] == "continuous":
            return list(nodes)
        return [n for n, x in zip(nodes, xs_chain) if any(abs(x - xb) <= 0.02 * L for xb in plan[1])]

    def _brace_chain(self, m, nodes, plan):
        """NL-R2-27: brace the interior chain nodes of beam m: a deck node at the same point, slaved to the level's
        rigid diaphragm (fixed out of plane), joined to the beam node by a zeroLength that is stiff ONLY in the
        horizontal direction normal to the beam (local 2) and in twist about the beam axis (local 4); the vertical,
        axial and bending DOFs stay free, so gravity and frame action are unchanged."""
        mst = self._diaphragm_of(m.n1)
        if mst is None or not nodes:
            return
        (x1, y1, _z1), (x2, y2, _z2) = self._coord(m.n1), self._coord(m.n2)
        dx, dy = x2 - x1, y2 - y1
        h = math.hypot(dx, dy)
        if h < 1e-9:
            return
        ax = (dx / h, dy / h, 0.0); yp = (-ax[1], ax[0], 0.0)
        for nd in nodes:
            self._nbrace += 1
            dn = BRACE_NODE0 + self._nbrace
            ops.node(dn, *ops.nodeCoord(nd))
            ops.fix(dn, 0, 0, 1, 1, 1, 0)
            ops.element("zeroLength", BRACE_ELE0 + self._nbrace, dn, nd, "-mat", 1, 2, "-dir", 2, 4,
                        "-orient", *ax, *yp)
            self._brace_slaves.setdefault(mst, []).append(dn)
        self.braced[m.tag] = (plan[0], tuple(round(x, 1) for x in plan[1]), plan[2])

    def _offset_frac(self):
        """Return rigid-end offset fraction, or 0 if disabled."""
        ro = self.rigid_end_offset
        if ro is True:
            return 0.05
        if ro in (False, None, 0, 0.0):
            return 0.0
        try:
            return float(ro)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------ NL-02 / NL-03 special members
    def _hm_params(self):
        """Parameters for pushover.hinge_models in DDM terms: nominal Fy (Ry = 1); BRB / link groups from the template
        unless the cfg carries a `snl_params` dict (same schema as hinge_params.json)."""
        prm = dict((self.cfg or {}).get("snl_params") or {})
        prm["material"] = {"Fy_ksi": self.Fy, "Ry_expected": 1.0}
        return prm

    def _prepare_special(self):
        from pushover import hinge_models as HM
        prm = self._hm_params()
        mem = [dict(tag=m.tag, kind=m.kind, section=m.section, n1=m.n1, n2=m.n2, released=bool(m.relz)) for m in self.nm.members]
        system = " / ".join(str(v) for v in dict.fromkeys((self.cfg or {}).get(k) for k in ("system", "system_X", "system_Y")) if v)   # NL-R2-13
        try:
            self.links = {t: v for t, v in HM.find_links(self.nm.nodes, mem, prm, system).items() if not v.get("skipped")}
        except Exception as ex:                                   # never silent: a census failure is logged and links stay beams
            self.links = {}
            self.special_log.append("EBF link census failed (%s) -- links modelled as plain fibre beams" % ex)
        if self.links:
            self.special_log.append("EBF links: %d shear springs (Vn = 0.6 Fy Alw, Ks = G d tw / e)%s" % (
                len(self.links), " -- RIGID in this elastic build (Steltic model is shear-rigid)" if self.elastic else ""))
        self._brb_pkg = HM.brb_package_data(self.nm.calc_package or {}, self.cfg)     # NL-R2-10: + cfg['brb'] / BRB_adjusted_strengths

    def _next_spring_mat(self):
        self._spring_mat += 1
        return self._spring_mat

    def _add_brb(self, m):
        """NL-02: non-buckling BRB truss (see module docstring)."""
        from pushover import hinge_models as HM
        p1, p2 = self._coord(m.n1), self._coord(m.n2)
        L = math.dist(p1, p2)
        A_model = float(m.A) if m.A else None
        spec = HM.brb_spec(m.section, L, self._hm_params(), A_model=A_model, pkg_data=self._brb_pkg)
        Pysc = spec.Fysc_ksi * spec.Asc                          # nominal core yield (AISC 341-22 F4.5b)
        K = spec.K_axial
        A_el = spec.A_model
        E_mat = K * L / A_el
        mt = self._next_spring_mat()
        if self.elastic:
            ops.uniaxialMaterial("Elastic", mt, E_mat)
        else:
            ops.uniaxialMaterial("Steel01", mt, Pysc / A_el, E_mat, self.hardening)
        et = BRB_ELE0 + m.tag
        ops.element("corotTruss", et, m.n1, m.n2, A_el, mt)
        dy = Pysc / K
        self.elems.append(dict(tag=et, mtag=m.tag, kind=m.kind, role=m.role, section=m.section, secTag=-1, s=0,
                               n1=m.n1, n2=m.n2, L=L, brb=True,
                               yield_fn=(lambda et=et, dy=dy: abs((ops.eleResponse(et, "deformation") or [0.0])[0]) / dy)))
        self.sub_nodes[m.tag] = [m.n1, m.n2]
        self.nonbuckling.add(m.tag)
        if len(self.nonbuckling) == 1:
            self.special_log.append("BRB: non-buckling corotTruss, Pysc = Fysc*Asc (%s)" % "; ".join(spec.flags[:3]))

    def _add_link_spring(self, m, end1, p1):
        """NL-03: zeroLength shear spring end1 -> new node; returns the new node (start of the fibre chain)."""
        from pushover import sections_db as SDB
        lp = SDB.link_shear_props(m.section, self.Fy)
        e = math.dist(self._coord(m.n1), self._coord(m.n2))
        Ks = 11200.0 * lp["As"] / e
        Vn = lp["Vp"]                                             # 0.6 Fy Alw with the nominal Fy (AISC 341-22 F3.5b.2)
        mt = self._next_spring_mat()
        if self.elastic:
            ops.uniaxialMaterial("Elastic", mt, K_TRANS)
        else:
            ops.uniaxialMaterial("Steel01", mt, Vn, Ks, self.hardening)
        nn = LINK_NODE0 + m.tag
        ops.node(nn, *p1)
        zl = LINK_ELE0 + m.tag
        ops.element("zeroLength", zl, end1, nn, "-mat", 1, 1, mt, 2, 2, 2, "-dir", 1, 2, 3, 4, 5, 6)
        dy = Vn / Ks
        self.elems.append(dict(tag=zl, mtag=m.tag, kind="link_shear", role=m.role, section=m.section, secTag=-1, s=0,
                               n1=end1, n2=nn, L=0.0, Vn=Vn,
                               yield_fn=(lambda zl=zl, dy=dy: abs((ops.eleResponse(zl, "deformation") or [0, 0, 0])[2]) / dy)))
        return nn

    def _add_member(self, m):
        from pushover import sections_db as SDB
        if m.kind == "brace" and SDB.is_brb(m.section):
            return self._add_brb(m)
        nsub = {"col": self.nsub_col, "beam": self.nsub_beam, "brace": self.nsub_brace}[m.kind]
        if m.kind == "brace" and m.tag in self.x_ties:
            # review D2: a tied diagonal buckles over each half -- at least 4 elements per half (2 per half over-predict:
            # pinned X HSS5X5X3/8: 57.7 kip with 2, 50.4 with 4; Euler half length 48.8) and an even count (crossing node)
            nsub = max(TIED_BRACE_NSUB, nsub + nsub % 2)
        tr = self._transf_for(m)
        p1, p2 = self._coord(m.n1), self._coord(m.n2)
        L = math.dist(p1, p2)
        bv, bmag = self._bow_vector(m)
        # end nodes (with pins if released)
        end1, end2 = m.n1, m.n2
        rel1, rel2 = set(), set()
        if m.kind == "beam":
            major = 5 if m.dirn == "X" else 4
            if m.relz in (1, 3): rel1.add(major)
            if m.relz in (2, 3): rel2.add(major)
            if m.rely in (1, 3): rel1.add(6)
            if m.rely in (2, 3): rel2.add(6)
        axis = None
        if m.kind == "brace" and self.brace_pins:
            rel1, rel2 = {5, 6}, {5, 6}                 # local: release bending, keep torsion (dir 4)
            axis = tuple((p2[q] - p1[q]) / L for q in range(3))
        if rel1:
            end1 = PIN_NODE0 + m.tag * 10 + 1
            ops.node(end1, *p1); self._pin(m.n1, end1, rel1, axis)
        if rel2:
            end2 = PIN_NODE0 + m.tag * 10 + 2
            ops.node(end2, *p2); self._pin(m.n2, end2, rel2, axis)
        if self._is_secondary(m):
            from .sections_fiber import cfs_section_props, G_KSI, E_KSI
            pr = cfs_section_props(m.section) or {}
            A = float(pr.get("A") or getattr(m, "A", 0.5) or 0.5)
            Ix = float(pr.get("Ix") or 5.0); Iy = float(pr.get("Iy") or 1.0); J = float(pr.get("J") or 1e-3)
            if tr in (1, 2):
                Iy_el, Iz_el = Iy, Ix
            else:
                Iy_el, Iz_el = Ix, Iy
            et = SUB_ELE0 + m.tag * 100
            ops.element("elasticBeamColumn", et, end1, end2, A, E_KSI, G_KSI, J, Iy_el, Iz_el, tr)
            self.elems.append(dict(tag=et, mtag=m.tag, kind=m.kind, role=m.role, section=m.section,
                                   secTag=0, s=0, n1=end1, n2=end2, L=L))
            self.sub_nodes[m.tag] = [end1, end2]
            return

        is_link = m.tag in self.links and m.kind == "beam"
        if is_link:                                               # NL-03: shear spring in series, no rigid end offsets
            end1 = self._add_link_spring(m, end1, p1)
        # Optional rigid end offsets on primary beams (continuous FR joint continuity).
        off_frac = self._offset_frac() if (m.kind == "beam" and not self._is_secondary(m) and not is_link) else 0.0
        if off_frac > 0:
            from .sections_fiber import elastic_props, E_KSI, G_KSI
            off = max(3.0, min(12.0, off_frac * L))
            if 2 * off >= 0.5 * L:
                off = 0.1 * L
            ux = (p2[0] - p1[0]) / L
            uy = (p2[1] - p1[1]) / L
            uz = (p2[2] - p1[2]) / L
            n_i = SUB_NODE0 + m.tag * 100 + 90
            n_j = SUB_NODE0 + m.tag * 100 + 91
            xi = (p1[0] + ux * off, p1[1] + uy * off, p1[2] + uz * off)
            xj = (p2[0] - ux * off, p2[1] - uy * off, p2[2] - uz * off)
            ops.node(n_i, *xi); ops.node(n_j, *xj)
            A, Ix, Iy, J = elastic_props(m.section)
            A = A or 10.0; Ix = Ix or 100.0; Iy = Iy or 10.0; J = J or 1.0
            scale = 1000.0
            # Steltic beam transf 3: strong = local y → Iy_el=Ix, Iz_el=Iy
            Iy_el, Iz_el = Ix * scale, Iy * scale
            et_i = SUB_ELE0 + m.tag * 100 + 80
            et_j = SUB_ELE0 + m.tag * 100 + 81
            ops.element("elasticBeamColumn", et_i, end1, n_i, A * scale, E_KSI, G_KSI, J * scale, Iy_el, Iz_el, tr)
            ops.element("elasticBeamColumn", et_j, n_j, end2, A * scale, E_KSI, G_KSI, J * scale, Iy_el, Iz_el, tr)
            self.elems.append(dict(tag=et_i, mtag=m.tag, kind=m.kind, role=m.role, section=m.section,
                                   secTag=0, s=-1, n1=end1, n2=n_i, L=off, dirn=m.dirn, rigid_stub=True))
            self.elems.append(dict(tag=et_j, mtag=m.tag, kind=m.kind, role=m.role, section=m.section,
                                   secTag=0, s=nsub, n1=n_j, n2=end2, L=off, dirn=m.dirn, rigid_stub=True))
            secTag = self._section(m)
            L_fib = L - 2 * off
            fr, seg_L = [s / nsub for s in range(1, nsub)], [L_fib / nsub] * nsub
            plan = self._brace_plan(m, L, off) if m.kind == "beam" else None
            if plan and plan[0] == "discrete" and plan[3]:         # NL-R2-27: chain nodes AT the brace points
                fr = [(x - off) / L_fib for x in plan[1]]
                fb = [0.0] + fr + [1.0]
                seg_L = [(fb[q + 1] - fb[q]) * L_fib for q in range(len(fr) + 1)]
            nseg = len(fr) + 1
            chain = [n_i]
            for s, f in enumerate(fr, start=1):
                offb = bmag * L * math.sin(math.pi * (off + f * L_fib) / L)
                x = xi[0] + (xj[0] - xi[0]) * f + bv[0] * offb
                y = xi[1] + (xj[1] - xi[1]) * f + bv[1] * offb
                z = xi[2] + (xj[2] - xi[2]) * f + bv[2] * offb
                t = SUB_NODE0 + m.tag * 100 + s
                ops.node(t, x, y, z)
                chain.append(t)
            chain.append(n_j)
            self.sub_nodes[m.tag] = [end1] + chain + [end2]
            etype = "dispBeamColumn" if self.fast else "forceBeamColumn"
            for s in range(nseg):
                tag = SUB_ELE0 + m.tag * 100 + s
                extra = () if self.fast else ("-iter", 20, 1e-8)
                ops.element(etype, tag, chain[s], chain[s + 1], tr, secTag, *extra)
                self.elems.append(dict(tag=tag, mtag=m.tag, kind=m.kind, role=m.role, section=m.section,
                                       secTag=secTag, s=s, n1=chain[s], n2=chain[s + 1],
                                       L=seg_L[s], dirn=m.dirn, nseg=nseg))
            if plan:
                self._brace_chain(m, self._brace_select(plan, [off + f * L_fib for f in fr], chain[1:-1], L), plan)
            return

        secTag = self._section(m)
        fr, seg_L = [s / nsub for s in range(1, nsub)], [L / nsub] * nsub
        plan = self._brace_plan(m, L, 0.0) if m.kind == "beam" and not is_link else None
        if plan and plan[0] == "discrete" and plan[3]:             # NL-R2-27: chain nodes AT the brace points
            fr = [x / L for x in plan[1]]
            fb = [0.0] + fr + [1.0]
            seg_L = [(fb[q + 1] - fb[q]) * L for q in range(len(fr) + 1)]
        nseg = len(fr) + 1
        chain = [end1]
        for s, f in enumerate(fr, start=1):
            off = self.bow_offset(f, L, bmag, tied=(m.kind == "brace" and m.tag in self.x_ties))
            x = p1[0] + (p2[0] - p1[0]) * f + bv[0] * off
            y = p1[1] + (p2[1] - p1[1]) * f + bv[1] * off
            z = p1[2] + (p2[2] - p1[2]) * f + bv[2] * off
            t = SUB_NODE0 + m.tag * 100 + s
            ops.node(t, x, y, z)
            chain.append(t)
        chain.append(end2)
        self.sub_nodes[m.tag] = chain
        etype = "dispBeamColumn" if self.fast else "forceBeamColumn"
        for s in range(nseg):
            tag = SUB_ELE0 + m.tag * 100 + s
            extra = () if self.fast else ("-iter", 20, 1e-8)
            ops.element(etype, tag, chain[s], chain[s + 1], tr, secTag, *extra)
            self.elems.append(dict(tag=tag, mtag=m.tag, kind=m.kind, role=m.role, section=m.section,
                                   secTag=secTag, s=s, n1=chain[s], n2=chain[s + 1], L=seg_L[s], dirn=m.dirn, nseg=nseg))
        if plan:
            self._brace_chain(m, self._brace_select(plan, [f * L for f in fr], chain[1:-1], L), plan)

    # ------------------------------------------------------------------ loads
    def _frac(self, m, n):
        """Position of GMNIA node n along member m as a fraction of its length (pins, stubs, links included)."""
        p1, p2 = self._coord(m.n1), self._coord(m.n2)
        c = ops.nodeCoord(n)
        d = [p2[q] - p1[q] for q in range(3)]
        L2 = sum(v * v for v in d) or 1.0
        return min(max(sum((c[q] - p1[q]) * d[q] for q in range(3)) / L2, 0.0), 1.0)

    def apply_gravity(self, fD, fL, fLr, pres):
        """Gravity of one combination on every beam element (kip/in and kip, local z down). NL-R2-03: distributed
        by the HR static model (floor_system / deck_span / infill one-way load path, roof bays, cladding); the
        legacy two-way tributary only when that geometry is unavailable."""
        if self._grav_geo:
            return self._apply_gravity_hr(fD, fL, fLr)
        from .loads import beam_udl
        total = 0.0
        for e in self.elems:
            if e["kind"] != "beam" or e.get("rigid_stub"):
                continue
            m = self.nm.by_tag()[e["mtag"]] if not hasattr(self, "_bt") else self._bt[e["mtag"]]
            w = beam_udl(self.cfg, self.nm, pres, m, e["s"], e.get("nseg", self.nsub_beam), fD, fL, fLr)
            if w:
                ops.eleLoad("-ele", e["tag"], "-type", "-beamUniform", 0.0, -w, 0.0)
                total += w * e["L"]
        return total

    def _apply_gravity_hr(self, fD, fL, fLr):
        import static_model as SM
        from .loads import hr_member_location
        geo = self._grav_geo
        bt = self._bt if hasattr(self, "_bt") else self.nm.by_tag()
        pcs = SM._case_pieces(self.cfg, geo["model"], fD, fL, fLr, None, geo["modes"])
        total = 0.0; by_mem = {}; on_span = {}; notes = []
        for e in self.elems:
            if e["kind"] != "beam":
                continue
            m = bt[e["mtag"]]
            if m.tag not in self._grav_loc:
                self._grav_loc[m.tag] = hr_member_location(geo, self.nm, m)
            loc = self._grav_loc[m.tag]
            if loc is None:
                continue
            what, key, sa, sb, Lp = loc
            ua = sa + (sb - sa) * self._frac(m, e["n1"]); ub = sa + (sb - sa) * self._frac(m, e["n2"])
            u0, u1 = min(ua, ub), max(ua, ub)
            if what == "span":
                plist = [pc for pc, _o in pcs["span"].get(key, [])]
                on_span.setdefault(key, []).append((u0, u1, ua, e))
            else:
                plist = list(pcs["infill"].get(key, []))
            W = sum(SM._piece_int(pc, u0, u1, Lp) for pc in plist if pc[0] != "pt")
            if u1 - u0 > 1e-9 and W:
                ops.eleLoad("-ele", e["tag"], "-type", "-beamUniform", 0.0, -W / (u1 - u0), 0.0)
                total += W; by_mem[m.tag] = by_mem.get(m.tag, 0.0) + W
        # point pieces (virtual-infill reactions on the girders): each to exactly ONE element of its span
        for key, plist in pcs["span"].items():
            pts = [pc for pc, _o in plist if pc[0] == "pt"]
            if not pts:
                continue
            els = on_span.get(key, [])
            hmax = max((u1 for _u0, u1, _ua, _e in els), default=None)
            for (_t, s, P) in pts:
                hit = next(((u0, u1, ua, e) for (u0, u1, ua, e) in els
                            if u0 - 1e-9 <= s < u1 - 1e-9 or (abs(s - hmax) <= 1e-9 and abs(u1 - hmax) <= 1e-9)), None)
                if hit is None:
                    notes.append("point load %.1f kip at s = %.0f in on span %s not applied (no GMNIA beam there)" % (P, s, key)); continue
                u0, u1, ua, e = hit
                xL = min(max(abs(s - ua) / max(u1 - u0, 1e-9), 0.0), 1.0)
                ops.eleLoad("-ele", e["tag"], "-type", "-beamPoint", 0.0, -P, xL)
                total += P; by_mem[e["mtag"]] = by_mem.get(e["mtag"], 0.0) + P
        self.gravity_by_member, self.gravity_notes = by_mem, notes
        return total

    def apply_lateral(self, lat):
        for k, (fx, fy, mz) in lat.items():
            # multi-storey: level index -> rigid-diaphragm master
            mt = k * 100000 + 99999
            if mt in self.nm.nodes:
                ops.load(mt, fx, fy, 0.0, 0.0, 0.0, mz)
            elif k in self.nm.nodes:
                # portal / no-diaphragm: keys are real node tags
                ops.load(k, fx, fy, 0.0, 0.0, 0.0, mz)

    def prepare(self):
        self._bt = self.nm.by_tag()
        return self

    # ------------------------------------------------------------------ export
    def export_py(self, path, header=""):
        """Write a standalone replay of this model (same style as Steltic's model_opensees.py)."""
        rec = []
        self._gravity_geometry()                 # NL-R2-03: the HR static build must not be recorded into the export
        funcs = ["wipe", "model", "node", "fix", "mass", "geomTransf", "uniaxialMaterial", "section",
                 "fiber", "beamIntegration", "element", "rigidDiaphragm"]
        orig = {f: getattr(ops, f) for f in funcs}
        def shim(fn, real):
            def w(*a):
                rec.append((fn, list(a))); return real(*a)
            return w
        for f, real in orig.items():
            setattr(ops, f, shim(f, real))
        try:
            self.build()
        finally:
            for f, real in orig.items():
                setattr(ops, f, real)
        lines = ['"""GMNIA model exported by steltic_ddm -- %s' % header,
                 'Fibre forceBeamColumn / Corotational / imperfections as recorded. Run: python model_gmnia.py"""',
                 "import openseespy.opensees as ops", ""]
        for cmd, a in rec:
            lines.append("ops.%s(%s)" % (cmd, ", ".join(repr(x) for x in a)))
        lines += ["", 'print("nodes:", len(ops.getNodeTags()), " elements:", len(ops.getEleTags()))']
        open(path, "w").write("\n".join(lines) + "\n")
        return path
