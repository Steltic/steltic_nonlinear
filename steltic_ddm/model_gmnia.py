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
  * brace ends: pinned about all three axes (gusset idealisation); braces do NOT share a node at the
    X-crossing (conservative: K = 1 on the full diagonal)
  * rigid diaphragms and master nodes exactly as Steltic recorded them
  * bases: as recorded (fixed / pinned)

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
K_TRANS = 1.0e9      # kip/in   stiff pin springs
K_ROT = 1.0e10       # kip-in/rad


class GMNIAModel:
    def __init__(self, nm, cfg, nsub=(4, 4, 6), residual="lehigh", Fy=None, hardening=0.002,
                 elastic=False, fast=False, out_of_plumb=(None, 0.0), bow=1 / 1000.0, bow_sign=+1,
                 brace_bow=1 / 1000.0, nip=5, brace_pins=True):
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
        self.elems = []          # dict(tag, mtag, kind, role, section, secTag, s, n1, n2, L)
        self.sub_nodes = {}      # mtag -> [node tags along member incl. ends]
        self.secs = {}           # (label, kind, axis) -> secTag
        self.sec_props = {}
        self.pins = []           # zeroLength tags
        self.masters = sorted(t for t in nm.nodes if t % 100000 == 99999)
        self.builder = None
        self.Ecol = {}           # mtag -> transf tag

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
    def build(self, with_mass=False):
        nm = self.nm
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
        for m in nm.members:
            self._add_member(m)
        for master, slaves in nm.diaphragms.items():
            ops.rigidDiaphragm(3, master, *slaves)
        if with_mass:
            for t, mm in nm.masses.items():
                ops.mass(t, *mm)
            mmin = min((mm[0] for mm in nm.masses.values() if mm[0] > 0), default=1.0)
            for t in ops.getNodeTags():
                if t not in nm.masses:
                    ops.mass(t, *([1e-8 * mmin] * 6))
        return self

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

    def _add_member(self, m):
        nsub = {"col": self.nsub_col, "beam": self.nsub_beam, "brace": self.nsub_brace}[m.kind]
        secTag = self._section(m)
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
        chain = [end1]
        for s in range(1, nsub):
            f = s / nsub
            off = bmag * L * math.sin(math.pi * f)
            x = p1[0] + (p2[0] - p1[0]) * f + bv[0] * off
            y = p1[1] + (p2[1] - p1[1]) * f + bv[1] * off
            z = p1[2] + (p2[2] - p1[2]) * f + bv[2] * off
            t = SUB_NODE0 + m.tag * 100 + s
            ops.node(t, x, y, z)
            chain.append(t)
        chain.append(end2)
        self.sub_nodes[m.tag] = chain
        etype = "dispBeamColumn" if self.fast else "forceBeamColumn"
        for s in range(nsub):
            tag = SUB_ELE0 + m.tag * 100 + s
            extra = () if self.fast else ("-iter", 20, 1e-8)
            ops.element(etype, tag, chain[s], chain[s + 1], tr, secTag, *extra)
            self.elems.append(dict(tag=tag, mtag=m.tag, kind=m.kind, role=m.role, section=m.section,
                                   secTag=secTag, s=s, n1=chain[s], n2=chain[s + 1], L=L / nsub, dirn=m.dirn))

    # ------------------------------------------------------------------ loads
    def apply_gravity(self, fD, fL, fLr, pres):
        """Two-way tributary UDL on every grid beam sub-element (kip/in, local z down)."""
        from .loads import beam_udl
        total = 0.0
        for e in self.elems:
            if e["kind"] != "beam":
                continue
            m = self.nm.by_tag()[e["mtag"]] if not hasattr(self, "_bt") else self._bt[e["mtag"]]
            w = beam_udl(self.cfg, self.nm, pres, m, e["s"], self.nsub_beam, fD, fL, fLr)
            if w:
                ops.eleLoad("-ele", e["tag"], "-type", "-beamUniform", 0.0, -w, 0.0)
                total += w * e["L"]
        return total

    def apply_lateral(self, lat):
        for k, (fx, fy, mz) in lat.items():
            mt = k * 100000 + 99999
            if mt in self.nm.nodes:
                ops.load(mt, fx, fy, 0.0, 0.0, 0.0, mz)

    def prepare(self):
        self._bt = self.nm.by_tag()
        return self

    # ------------------------------------------------------------------ export
    def export_py(self, path, header=""):
        """Write a standalone replay of this model (same style as Steltic's model_opensees.py)."""
        rec = []
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
