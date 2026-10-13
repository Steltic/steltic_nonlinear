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
  * columns braced where the HR design braces them (NL-R2-33): calc_package column records (Lcy_in / Lcx_in / Lb_in or
    brace_points_in, read by nlrha.acceptance.design_column_lengths) -> brace points every floor(L / Lc) of the member;
    lateral displacement on the braced axis held RELATIVE to the column chord (pinned-pinned elastic strongback, no
    axial / vertical stiffness) and twist held at the brace points -- a lateral direction only where the column is
    pinned at both ends in that plane (otherwise the strongback would add sway stiffness; skipped and logged);
    inelastic build only (the transfer gate compares the HR elastic model). See _col_brace_plan / _brace_column.
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
COLBR_NODE0 = 87_000_000  # NL-R2-33: column brace strongback node (COLBR_NODE0 + running count)
COLBR_ELE0 = 88_000_000   # NL-R2-33: column brace strongback element / zeroLength (COLBR_ELE0 + running count)
COL_BRACE_MAX_N = 8       # NL-R2-33: at most 8 braced segments per column (closer HR bracing -> spacing L/8, conservative)
SB_TRANSF = 6             # NL-R2-33: Linear transformation of the column brace strongbacks
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
        self._lb_group_used = {}     # gold-run fix (Ex32): (role/section, dirn, moment-connected) -> Lb from Lb_in_by_group
        self.uplift_note = None      # NL-R2-L3: roof-uplift disclosure of the last apply_gravity
        self.column_bracing = True   # NL-R2-33: brace GMNIA columns where the HR design braces them
        self.col_braced = {}         # member tag -> plan dict (weak / strong / twist positions in in from n1, Lc, source)
        self._col_brace_skips = []   # NL-R2-33 (review): (member, L, [(axis, reason)]) brace directions not applied
        self._node_mem = None

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
        self._lb_group_used = {}
        self.col_braced, self._ncolbr, self._sb_transf = {}, 0, False
        self._col_brace_skips, self._node_mem = [], None
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
        if getattr(self, "_lb_group_used", None):
            self.special_log.append("beam bracing: HR Lb per member group (calc_package Lb_in_by_group) used: %s" % "; ".join(
                "%s %s %s-beams%s Lb %g in" % (r, sct, d, " (moment-connected)" if rg else "", v)
                for ((r, sct), d, rg), v in sorted(self._lb_group_used.items(), key=str)[:12]))
        if getattr(self, "_lb_conflicts", None):
            self.special_log.append("beam bracing: HR groups with different Lb for the same role/section -> the LARGEST Lb "
                                    "used: %s" % "; ".join("%s %s %s" % (r, sct, v) for (r, sct), v in sorted(self._lb_conflicts.items())[:6]))
        if self.col_braced or self._col_brace_skips:
            self.special_log.extend(self._col_brace_notes())         # NL-R2-33
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
            out, conflicts, groups = {}, {}, {}
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
                grp = inp.get("Lb_in_by_group")
                if isinstance(grp, dict):
                    lst = groups.setdefault(key, [])
                    for name, v in grp.items():
                        try:
                            lst.append((str(name), float(v)))
                        except (TypeError, ValueError):
                            continue
            self._lb_map, self._lb_conflicts, self._lb_groups = out, conflicts, groups
        return self._lb_map

    @staticmethod
    def _lb_group_match(name, m):
        """Does the HR Lb group `name` (free text, e.g. "X girders (infill-braced)", "Y moment-frame girders",
        "Y gravity girders") describe beam m? A direction word X / Y must equal the beam's plan direction; "moment"
        must go with a moment-connected beam (no end release) and "gravity" with a pinned one. A group naming neither
        side of a criterion is not constrained by it."""
        import re as _re
        t = name.lower()
        dirs = set(_re.findall(r"(?<![a-z0-9])([xy])(?![a-z0-9])", t))
        if dirs and str(m.dirn).lower() not in dirs:
            return False
        rigid = (m.relz == 0)
        if "moment" in t and not rigid:
            return False
        if "gravity" in t and rigid:
            return False
        return True

    def _member_lb(self, m):
        """Gold-run fix (Ex32): the HR Lb of beam m. A record may give its Lb per member group (inputs.Lb_in_by_group,
        e.g. X girders braced by the infill at 100 in, Y moment-frame girders 360 in, Y gravity girders 0 = deck) with
        Lb_in = the largest; that largest value used to brace NO X girder of Ex32 (n = L / Lb = 1), so the GMNIA buckled
        the infill-braced roof girders laterally-torsionally over the full 30 ft at lambda 0.91 although the design
        braces them every 100 in. The group that matches the member is used when exactly one distinct Lb matches;
        otherwise (no group data, no match, or matching groups that disagree) the largest Lb, as before."""
        key = (str(m.role), str(m.section).upper().replace(" ", ""))
        lb = self._hr_lb().get(key)
        grp = (getattr(self, "_lb_groups", None) or {}).get(key)
        if lb is None or not grp:
            return lb
        hits = {v for name, v in grp if self._lb_group_match(name, m)}
        if len(hits) == 1:
            v = hits.pop()
            self._lb_group_used[(key, m.dirn, m.relz == 0)] = v
            return v
        return lb

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
        lb = self._member_lb(m)
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

    # ------------------------------------------------------------------ NL-R2-33 column bracing (girts / flange braces)
    def _col_brace_plan(self, m, L):
        """NL-R2-33: where the HR design braces column m -> None (unbraced) or dict(weak, strong, twist, Lcx, Lcy, Lb,
        source): brace positions (in from n1) for lateral displacement about the weak axis (from Lcy), the strong axis
        (from Lcx) and twist (from Lb). Lengths: nlrha.acceptance.design_column_lengths -- the same reading the NLRHA
        column acceptance uses (calc_package members[].inputs with a whole-word column role and this section;
        brace_points_in elevations cut at this member, or Lcx_in / Lcy_in / Lb_in when the record's length_in is this
        member's; several records -> per-axis maxima, conservative). Positions: floor(L / Lc + 1e-6) equal segments
        (spacing never shorter than the design's longest unbraced length, as the NL-R2-27 beam bracing); L / Lc < 2
        -> that axis unbraced; at most COL_BRACE_MAX_N segments. AISC 360-22 App. 1, 1.3.2c: for members in flexure
        and axial compression Lb is the length between points braced against BOTH lateral displacement in the minor-axis
        direction and twist -- the GMNIA braces exactly the points the design relied on, so it does not buckle a column
        over a length the design (and the building: girts, flange braces) braces (Ex21: W10X45 32 ft, KL/r 191 unbraced
        vs Lcy = 16 ft in the HR design). Inelastic build only (the elastic transfer gate compares the HR model)."""
        if not self.column_bracing or self.elastic or m.kind != "col" or self._is_secondary(m):
            return None
        cp = self.nm.calc_package or {}
        if not cp.get("members"):
            return None
        from types import SimpleNamespace
        from nlrha.acceptance import design_column_lengths
        z1, z2 = self.nm.nodes[m.n1][2], self.nm.nodes[m.n2][2]
        L0 = math.dist(self.nm.nodes[m.n1], self.nm.nodes[m.n2])
        dl = design_column_lengths(SimpleNamespace(calc=cp), m.section, z1, z2, L0)
        if not dl:
            return None
        def pts(Lc):
            try:
                n = int(math.floor(L / float(Lc) + 1e-6))
            except (TypeError, ValueError, ZeroDivisionError):
                return []
            n = min(n, COL_BRACE_MAX_N)
            return [k * L / n for k in range(1, n)] if n >= 2 else []
        plan = dict(weak=pts(dl["Lcy"]), strong=pts(dl["Lcx"]), twist=pts(dl["Lb"]),
                    Lcx=dl["Lcx"], Lcy=dl["Lcy"], Lb=dl["Lb"], L=L, source=dl["source"])
        # NL-R2-33 (review): the strongback holds the brace points on the column CHORD, so it is inert only under pure
        # chord rotation -- a column pinned at both ends in that plane. A column fixed at its base or moment-connected
        # bends in sway (cantilever / double curvature) and the strongback would add storey stiffness (reviewer: fixed-base
        # W24X76 cantilever, weak axis 0.0757 -> 0.173 kip/in, strong axis x 2.29). Such a direction is NOT braced (the
        # column buckles over its full length in that plane -- conservative) and the skip is disclosed in builder.log.
        tr = self._transf_for(m)
        dirs = dict(weak=(2 if tr == 2 else 1), strong=(1 if tr == 2 else 2))
        skipped = []
        for axis in ("weak", "strong"):
            if not plan[axis]:
                continue
            why = self._col_end_restraint(m, dirs[axis])
            if why:
                skipped.append((axis, why)); plan[axis] = []
        if skipped:
            self._col_brace_skips.append((m, L, skipped))
        plan["skipped"] = skipped
        return plan if (plan["weak"] or plan["strong"] or plan["twist"]) else None

    def _col_end_restraint(self, m, gdir):
        """NL-R2-33 (review): None when column m is free to rotate at BOTH ends in the plane of a lateral displacement in
        global direction gdir (1 = X -> rotation about Y, DOF 5; 2 = Y -> about X, DOF 4), i.e. pinned-pinned in that
        plane so sway is a pure chord rotation and the strongback carries nothing; else the reason. An end is restrained
        by a support fixing that rotation, a diaphragm master, another column (continuity), a beam whose MAJOR axis bends
        in that plane and is not released at that end (moment connection), a non-pinned brace, or any other member. The
        torsion of a beam framing along the displacement direction (GJ / L, ~2 % of the column's EI / L for a W24X76
        on a W10X45) is not counted as restraint."""
        rot = 5 if gdir == 1 else 4
        if getattr(self, "_node_mem", None) is None:
            self._node_mem = {}
            for o in self.nm.members:
                self._node_mem.setdefault(o.n1, []).append(o); self._node_mem.setdefault(o.n2, []).append(o)
        from pushover import sections_db as SDB
        for n, end in ((m.n1, "bottom"), (m.n2, "top")):
            fl = self.nm.fixes.get(n)
            if fl and len(fl) >= rot and fl[rot - 1]:
                return "%s end fixed against rotation in that plane (support)" % end
            if n in self.masters:
                return "%s end is a diaphragm master node" % end
            for o in self._node_mem.get(n, []):
                if o is m or o.tag == m.tag:
                    continue
                if o.kind == "col":
                    return "%s end continuous into column %s" % (end, o.tag)
                if o.kind == "brace":
                    if self.brace_pins or SDB.is_brb(o.section):
                        continue
                    return "%s end: brace %s not pinned" % (end, o.tag)
                if o.kind == "beam" and o.dirn in ("X", "Y"):
                    major = 5 if o.dirn == "X" else 4
                    if rot != major:
                        continue                                    # beam torsion: neglected
                    code = 1 if o.n1 == n else 2
                    if o.relz in (code, 3):
                        continue                                    # pinned (major released) at this end
                    return "%s end moment-connected to beam %s" % (end, o.tag)
                return "%s end: member %s (%s) restrains it" % (end, o.tag, o.kind)
        return None

    @staticmethod
    def col_chain_fracs(L, plan, nsub):
        """NL-R2-33: chain fractions of a braced column -- every brace point is a chain node and each braced segment has
        max(2, ceil(nsub / nseg)) sub-elements (a force-based Corotational element carries no P-delta inside itself, so
        a segment needs an interior node to buckle between its brace points)."""
        bps = sorted({round(x, 6) for x in plan["weak"] + plan["strong"] + plan["twist"]})
        cuts = [0.0] + bps + [L]
        per = max(2, -(-nsub // (len(cuts) - 1)))
        fr = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            fr += [(a + (b - a) * q / per) / L for q in range(per)]
        return fr[1:]

    @staticmethod
    def segment_seed(f, L, bmag, cuts):
        """NL-R2-33: out-of-straightness BETWEEN brace points: an alternating half-sine of bmag x Lseg on each braced
        segment (cuts = brace positions in in, ends included) -- the buckling mode between brace points, which the
        full-length L/1000 bow (symmetric about a mid brace) never seeds (AISC 360-22 App. 1, 1.2.2a User Note:
        1/1,000 out-of-straightness between brace points; 1.3.3b: the pattern with the greatest destabilizing effect).
        Added to the full-length bow, as for the X diagonals tied at the crossing (review D2)."""
        x = f * L
        for s, (a, b) in enumerate(zip(cuts[:-1], cuts[1:])):
            if a - 1e-9 <= x <= b + 1e-9 and b - a > 1e-9:
                return (-1) ** s * bmag * (b - a) * math.sin(math.pi * (x - a) / (b - a))
        return 0.0

    def _brace_column(self, m, chain, xs_chain, plan):
        """NL-R2-33: brace the chain nodes of column m at the HR brace points.
        Lateral displacement (weak and/or strong axis, global X / Y from the column orientation): held RELATIVE to the
        column's own chord by a pinned-pinned elastic strongback n1 -> brace nodes -> n2 (Linear transformation, both
        bending planes released at n1 and n2, EI = 1e4 x the column's larger I -> a near-rigid brace, EA and GJ ~0 so it
        adds no axial / vertical / torsional path) joined to each brace-point chain node by a zeroLength stiff ONLY in
        the braced horizontal direction(s). Under pure chord rotation (frame sway) the strongback stays straight and
        carries nothing, so storey stiffness and strength are unchanged; it resists only the column's bow between its
        ends -- what girts / flange braces anchored to the wall do. That holds only for a column pinned at both ends in
        the braced plane: _col_brace_plan drops a direction whose column end is fixed / moment-connected / continuous
        (_col_end_restraint, NL-R2-33 review). Twist: rotation about the column axis held at the
        twist brace points (fixed rz; the column's St-Venant torsional share of the building's twist is negligible).
        Brace strength / stiffness (AISC 360-22 App. 6, 6.2) is not checked here: the brace is the HR design's."""
        tr = self._transf_for(m)
        weak_dir, strong_dir = (2, 1) if tr == 2 else (1, 2)
        L = plan["L"]
        def near(x, lst):
            return any(abs(x - xb) <= 1e-3 * L + 1e-6 for xb in lst)
        tnodes = []          # (chain node, [global translation dirs])
        for nd, x in zip(chain[1:-1], xs_chain):
            dirs = ([weak_dir] if near(x, plan["weak"]) else []) + ([strong_dir] if near(x, plan["strong"]) else [])
            if dirs:
                tnodes.append((nd, dirs))
            if near(x, plan["twist"]):
                ops.fix(nd, 0, 0, 0, 0, 0, 1)
        if tnodes:
            if not self._sb_transf:
                ops.geomTransf("Linear", SB_TRANSF, 1.0, 0.0, 0.0)
                self._sb_transf = True
            A, Ix, Iy, J = elastic_props(m.section)
            Isb = 1.0e4 * max(Ix or 0.0, Iy or 0.0, 1.0)
            from .sections_fiber import E_KSI, G_KSI
            prev = chain[0]
            sb_nodes = []
            for nd, dirs in tnodes:
                self._ncolbr += 1
                sn = COLBR_NODE0 + self._ncolbr
                ops.node(sn, *ops.nodeCoord(nd))
                sb_nodes.append(sn)
                ops.element("zeroLength", COLBR_ELE0 + self._ncolbr, sn, nd, "-mat", *([1] * len(dirs)), "-dir", *dirs)
            seq = [chain[0]] + sb_nodes + [chain[-1]]
            for q in range(len(seq) - 1):
                self._ncolbr += 1
                rel = []
                if q == 0:
                    rel += ["-releasez", 1, "-releasey", 1]
                if q == len(seq) - 2:
                    code = 3 if q == 0 else 2
                    rel = ["-releasez", code, "-releasey", code]
                ops.element("elasticBeamColumn", COLBR_ELE0 + self._ncolbr, seq[q], seq[q + 1],
                            1.0e-4, E_KSI, G_KSI, 1.0e-4, Isb, Isb, SB_TRANSF, *rel)
        self.col_braced[m.tag] = plan

    def _col_brace_notes(self):
        """NL-R2-33 builder.log / report disclosure: which columns were braced, where, and from which HR record."""
        groups, bt = {}, self.nm.by_tag()
        for t, p in self.col_braced.items():
            m = bt[t]
            key = (m.section, round(p["L"]), tuple(round(x) for x in p["weak"]), tuple(round(x) for x in p["strong"]),
                   tuple(round(x) for x in p["twist"]), round(p["Lcx"]), round(p["Lcy"]), round(p["Lb"]), p["source"])
            groups.setdefault(key, []).append(m)
        out = []
        for (sec, L, wk, st, tw, Lcx, Lcy, Lb, src), ms in sorted(groups.items(), key=str):
            roles = "/".join(sorted({str(m.role) for m in ms}))
            what = "; ".join(x for x in (
                ("weak-axis lateral displacement at %s in" % "/".join(map(str, wk))) if wk else "",
                ("strong-axis lateral displacement at %s in" % "/".join(map(str, st))) if st else "",
                ("twist at %s in" % "/".join(map(str, tw))) if tw else "") if x)
            out.append("column bracing (NL-R2-33): %d %s %s columns (L %d in) braced -- %s -- from the HR design Lcx %d / "
                       "Lcy %d / Lb %d in (%s); brace = elastic strongback on the column chord + zeroLength in the braced "
                       "direction only (no axial / vertical / sway stiffness), twist fixed at the brace points; segment "
                       "out-of-straightness Lseg/1000 alternating, plus the L/1000 full-length bow (AISC 360-22 App. 1, "
                       "1.3.2c / 1.2.2a)" % (len(ms), roles, sec, L, what, Lcx, Lcy, Lb, src))
        sk = {}
        for m, L, skipped in self._col_brace_skips:                # NL-R2-33 (review)
            for axis, why in skipped:
                g = sk.setdefault((m.section, round(L), axis), [0, why]); g[0] += 1
        for (sec, L, axis), (n, why) in sorted(sk.items(), key=str):
            out.append("column bracing (NL-R2-33): %d %s columns (L %d in): the HR %s-axis brace points NOT modelled -- the "
                       "column is not pinned at both ends in that plane (e.g. %s), so the chord strongback would add sway "
                       "stiffness; the column buckles over its full length in that plane (conservative vs the HR design)"
                       % (n, sec, L, axis, why))
        return out

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
        axis = None
        if m.kind == "beam":
            # Gold-run fix (Ex28): the pins are oriented on the member (local x = member axis, local y horizontal), so a
            # released end frees the beam's own major (local 5) / minor (local 6) bending whatever its direction -- the
            # same global DOFs as before for a beam along X or Y, and never its torsion (an off-grid or plan-diagonal
            # beam used to lose its torsional restraint at both ends -> singular stiffness).
            if m.relz in (1, 3): rel1.add(5)
            if m.relz in (2, 3): rel2.add(5)
            if m.rely in (1, 3): rel1.add(6)
            if m.rely in (2, 3): rel2.add(6)
            if rel1 or rel2:
                axis = tuple((p2[q] - p1[q]) / L for q in range(3))
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
        cplan = self._col_brace_plan(m, L) if m.kind == "col" else None
        wcuts = None
        if cplan:                                                  # NL-R2-33: chain nodes AT the column brace points
            fr = self.col_chain_fracs(L, cplan, nsub)
            fb = [0.0] + fr + [1.0]
            seg_L = [(fb[q + 1] - fb[q]) * L for q in range(len(fr) + 1)]
            wcuts = [0.0] + list(cplan["weak"]) + [L] if cplan["weak"] else None
        nseg = len(fr) + 1
        chain = [end1]
        for s, f in enumerate(fr, start=1):
            off = self.bow_offset(f, L, bmag, tied=(m.kind == "brace" and m.tag in self.x_ties))
            if wcuts:                                              # NL-R2-33: bow between the weak-axis brace points
                off += self.segment_seed(f, L, bmag, wcuts)
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
        if cplan:
            self._brace_column(m, chain, [f * L for f in fr], cplan)

    # ------------------------------------------------------------------ loads
    def _frac(self, m, n):
        """Position of GMNIA node n along member m as a fraction of its length (pins, stubs, links included)."""
        p1, p2 = self._coord(m.n1), self._coord(m.n2)
        c = ops.nodeCoord(n)
        d = [p2[q] - p1[q] for q in range(3)]
        L2 = sum(v * v for v in d) or 1.0
        return min(max(sum((c[q] - p1[q]) * d[q] for q in range(3)) / L2, 0.0), 1.0)

    def apply_gravity(self, fD, fL, fLr, pres, uplift=None):
        """Gravity of one combination on every beam element (kip/in and kip, local z down). NL-R2-03: distributed
        by the HR static model (floor_system / deck_span / infill one-way load path, roof bays, cladding); the
        legacy two-way tributary only when that geometry is unavailable.
        NL-R2-L3: uplift = the case's (dirn, sgn) roof-uplift flag (design_pipeline.combos, 0.9D+1.0W "(roof uplift)"):
        the HR engine's MWFRS roof pressure p = qh (G Cp - GCpi) on every roof bay (ASCE 7-22 Eq. 27.3-1, roof Cp of
        Fig. 27.3-1 by distance from the windward edge, static_model._case_pieces) is added to the roof bay load, so the
        GMNIA sweep scales the SAME net roof load the member design used. Without the HR geometry the uplift cannot be
        placed -> RuntimeError (the sweep fails and the combination is reported NOT EVALUATED, never run without it)."""
        if uplift and not self._grav_geo:
            raise RuntimeError("roof uplift %s%s not applied: the HR static-model load path is unavailable (%s) -- "
                               "combination NOT EVALUATED rather than analysed without its roof suction (NL-R2-L3)"
                               % (uplift[0], "+" if uplift[1] > 0 else "-", self._grav_fail or "no HR geometry"))
        if self._grav_geo:
            return self._apply_gravity_hr(fD, fL, fLr, uplift)
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

    def _apply_gravity_hr(self, fD, fL, fLr, uplift=None):
        import static_model as SM
        from .loads import hr_member_location
        geo = self._grav_geo
        bt = self._bt if hasattr(self, "_bt") else self.nm.by_tag()
        # NL-R2-L3: the roof-uplift flag goes to the HR piece builder exactly as static_model.apply_case_gravity does
        pcs = SM._case_pieces(self.cfg, geo["model"], fD, fL, fLr, tuple(uplift) if uplift else None, geo["modes"])
        self.uplift_note = None
        if uplift:
            from .loads import roof_uplift_pressures
            qh, pb = roof_uplift_pressures(self.cfg, geo["model"], uplift)
            self.uplift_note = ("roof uplift W%s%s (NL-R2-L3): MWFRS roof pressure p = qh (G Cp - GCpi), qh = %.1f psf "
                                "(ASCE 7-22 Eq. 27.3-1, roof Cp Fig. 27.3-1 -- HR static_model), %.1f to %.1f psf on %d roof "
                                "bays, added to %.2fD on the roof and scaled with lambda"
                                % (uplift[0], "+" if uplift[1] > 0 else "-", qh, min(pb.values(), default=0.0),
                                   max(pb.values(), default=0.0), len(pb), fD))
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
