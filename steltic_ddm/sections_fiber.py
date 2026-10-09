"""
sections_fiber.py -- fibre-section builders for the GMNIA model (kip, inch, ksi).

Sections are built from the SAME AISC shape database Steltic uses (aisc_shapes.csv: d, tw, bf, tf
for W-shapes; A, Ix for HSS with the outside dimensions parsed from the label). Fillets / k-area
are NOT in the CSV, so W-shape A and I come out 1-3 % low -- a known, conservative bias.

Residual stresses are introduced fibre-by-fibre with `InitStressMaterial` wrappers:
  * "lehigh"  -- Galambos & Ketter (1959) linear pattern for hot-rolled I-sections:
                 -sigma_rc*Fy at the flange tips, +sigma_rt*Fy at the web/flange junction and
                 uniform +sigma_rt*Fy in the web, sigma_rt = sigma_rc*Af/(Af+Aw) (self-equilibrating).
                 sigma_rc = 0.3 is the value adopted as the nominal pattern by Shayan, Rasmussen &
                 Zhang (JCSR 101, 2014) and by AISC/SSRC column-curve work.
  * "ecCS"    -- ECCS (1984) bilinear pattern, +/-0.5Fy for h/b <= 1.2 else +/-0.3Fy.
  * "none"    -- no residual stress (sensitivity runs).
  * "cf_hss_membrane" -- simplified membrane pattern for cold-formed HSS (compression at the flat
                 centres, tension at the corners; see Liu, Rasmussen & Zhang, Eng. Struct. 150, 2018).
                 PROVISIONAL: magnitudes are placeholders until the paper's pattern is transcribed.

Fibre orientation follows Steltic's element conventions (engine3d.add_column / add_beam):
  columns : strong axis = element local z  -> depth runs along local y  (axis="y")
  beams   : strong axis = element local y  -> depth runs along local z  (axis="z")
"""
import csv, math, os, re

E_KSI = 29000.0
G_KSI = 11200.0

_CSV_CACHE = None


def shapes_csv_path():
    """Locate aisc_shapes.csv: $AISC_CSV, then a steltic checkout on sys.path, then the bundled copy."""
    p = os.environ.get("AISC_CSV")
    if p and os.path.exists(p):
        return p
    eng = os.environ.get("STELTIC_ENGINE_DIR")
    if eng and os.path.exists(os.path.join(eng, "aisc_shapes.csv")):
        return os.path.join(eng, "aisc_shapes.csv")
    import sys
    for d in sys.path:
        cand = os.path.join(d, "aisc_shapes.csv")
        if os.path.exists(cand):
            return cand
        cand = os.path.join(d, "steel_engine", "aisc_shapes.csv")
        if os.path.exists(cand):
            return cand
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "aisc_shapes.csv")
    if os.path.exists(here):
        return here
    raise FileNotFoundError("aisc_shapes.csv not found -- set AISC_CSV or put steltic/steel_engine on sys.path")


def shapes():
    global _CSV_CACHE
    if _CSV_CACHE is None:
        _CSV_CACHE = {}
        with open(shapes_csv_path(), newline="") as f:
            for r in csv.DictReader(f):
                _CSV_CACHE[r["AISC_Manual_Label"].upper()] = r
    return _CSV_CACHE


def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def shape(label):
    r = shapes().get(str(label).upper().strip())
    if r is None:
        raise KeyError("section %r not in aisc_shapes.csv" % label)
    return r


def hss_dims(label):
    """'HSS8X8X1/2' -> (H, B, t_nominal); fractional dimensions too ('HSS5-1/2X5-1/2X3/8' -> 5.5, 5.5, 0.375;
    NL-R2-28: the old pattern stripped the '-' and read '51/2'). Returns None for round HSS / pipe / other shapes."""
    from pushover.sections_db import parse_hss_label
    g = parse_hss_label(label)
    if not g or g["kind"] != "rect":
        return None
    return g["H"], g["B"], g["t_nom"]


def round_hss_dims(label):
    """Round HSS / pipe -> (OD, t) of the equivalent ring whose area and moment of inertia equal the tabulated
    A and Ix (so the design wall thickness of the table is honoured: 0.93 t_nom for A500 HSS, AISC 360-22 B4.2).
    None for other shapes or when A / Ix are missing."""
    from pushover.sections_db import parse_hss_label
    g = parse_hss_label(label)
    if not g or g["kind"] not in ("round", "pipe"):
        return None
    try:
        r = shape(label)
    except KeyError:
        return None
    A, Ix = _f(r["A"]), _f(r["Ix"])
    if not A or not Ix:
        return None
    s2 = 4.0 * Ix / A                       # Ro^2 + Ri^2
    d2 = A / math.pi                        # Ro^2 - Ri^2
    Ro = math.sqrt((s2 + d2) / 2.0); Ri = math.sqrt(max((s2 - d2) / 2.0, 0.0))
    return 2.0 * Ro, Ro - Ri


# Hardcoded secondary Z props (same as export_cfs_portal_3d_bay.py)
_CFS_Z_PROPS = {
    "800Z250-54": dict(A=0.515, Ix=5.60, Iy=0.98, J=0.0008),
    "800Z250-68": dict(A=0.647, Ix=7.15, Iy=1.25, J=0.0012),
    "800Z250-97": dict(A=0.910, Ix=9.80, Iy=1.70, J=0.0020),
}


def cfs_section_props(label):
    """Gross A/Ix/Iy/J for CFS designators (built-up Nx... or hardcoded Z)."""
    lab = str(label).strip()
    key = lab.upper().replace(" ", "")
    for k, v in _CFS_Z_PROPS.items():
        if key == k.upper():
            return dict(v)
    # Named secondaries from Sena portal export (elastic-only path)
    if any(t in key for t in ("EAVE_STRUT", "RESTRAINT_PURLIN", "RESTRAINT_GIRT", "PURLIN", "GIRT", "EAVESTRUT")):
        return dict(A=0.5, Ix=5.0, Iy=1.0, J=1e-3)
    # built-up e.g. 6x1200S300-118
    try:
        import cfs_frame as CF
        import cfs_sections as SEC
        fs = CF.frame_section(lab)
        gp = SEC.gross_props(fs["base"])
        n = int(fs["n_ply"])
        return dict(A=n * gp["A"], Ix=n * gp["Ix"], Iy=n * gp["Iy"], J=n * gp["J"])
    except Exception:
        return None


class FiberSectionBuilder:
    """Stateful builder: hands out unique material tags and records what it made."""

    def __init__(self, ops, Fy=50.0, E=E_KSI, hardening=0.002, residual="lehigh", sigma_rc=0.3,
                 mat_tag0=1000, elastic=False):
        self.ops = ops
        self.Fy, self.E, self.b = Fy, E, hardening
        self.residual = residual
        self.sigma_rc = sigma_rc
        self.next_mat = mat_tag0
        self.elastic = elastic          # elastic sections (transfer gate)
        self.log = []                   # (secTag, label, kind, nfib, note)
        self._mat_cache = {}

    # ---- materials -------------------------------------------------------------------------
    def _mat(self, sig0_frac, Fy=None):
        """Steel01 (+InitStress) for a residual-stress level sig0_frac*Fy; cached per level. Fy: a yield stress other
        than the builder's (NL-R2-28: HSS members of a different material grade), cached separately."""
        key = round(sig0_frac, 4) if Fy is None else (round(sig0_frac, 4), round(float(Fy), 4))
        Fy = self.Fy if Fy is None else float(Fy)
        if key in self._mat_cache:
            return self._mat_cache[key]
        tag = self.next_mat; self.next_mat += 1
        if self.elastic:
            self.ops.uniaxialMaterial("Elastic", tag, self.E)
            self._mat_cache[key] = tag
            return tag
        self.ops.uniaxialMaterial("Steel01", tag, Fy, self.E, self.b)
        if abs(sig0_frac) > 1e-9:
            wtag = self.next_mat; self.next_mat += 1
            self.ops.uniaxialMaterial("InitStressMaterial", wtag, tag, sig0_frac * Fy)
            self._mat_cache[key] = wtag
            return wtag
        self._mat_cache[key] = tag
        return tag

    def _mat_spec(self, spec):
        """Explicit fibre material (NL-10 physical-theory brace): spec = dict(kind="Steel02", Fy, b, R0, fatigue=(eps0, m)).
        Steel02 (Menegotto-Pinto, cR1 0.925, cR2 0.15) wrapped in a Fatigue material (Coffin-Manson, rainflow counting:
        the fibre loses its strength when the damage reaches 1 -- Uriz & Mahin 2008)."""
        fat = tuple(spec.get("fatigue") or ())
        key = ("spec", spec.get("kind", "Steel02"), round(float(spec["Fy"]), 4), float(spec.get("b", 0.003)),
               float(spec.get("R0", 20.0)), fat)
        if key in self._mat_cache:
            return self._mat_cache[key]
        tag = self.next_mat; self.next_mat += 1
        if self.elastic:
            self.ops.uniaxialMaterial("Elastic", tag, self.E)
            self._mat_cache[key] = tag
            return tag
        self.ops.uniaxialMaterial("Steel02", tag, float(spec["Fy"]), self.E, float(spec.get("b", 0.003)),
                                  float(spec.get("R0", 20.0)), 0.925, 0.15)
        if fat:
            wtag = self.next_mat; self.next_mat += 1
            self.ops.uniaxialMaterial("Fatigue", wtag, tag, "-E0", float(fat[0]), "-m", float(fat[1]))
            tag = wtag
        self._mat_cache[key] = tag
        return tag

    # ---- W-shape ---------------------------------------------------------------------------
    def w_shape(self, secTag, label, axis="y", nf_flange=(8, 2), nf_web=(12, 1), residual=None):
        """Fibre W-section. axis="y": depth along local y (Steltic column); "z": depth along local z (beam)."""
        r = shape(label)
        d, tw, bf, tf = (_f(r["d"]), _f(r["tw"]), _f(r["bf"]), _f(r["tf"]))
        if None in (d, tw, bf, tf):
            raise ValueError("%s has no d/tw/bf/tf in the CSV" % label)
        J = _f(r["J"]) or 1.0
        hw = d - 2 * tf
        Af, Aw = bf * tf, hw * tw
        res = self.residual if residual is None else residual
        if res == "lehigh":
            rc = self.sigma_rc; rt = rc * Af / (Af + Aw)
            def sig_fl(zfrac): return rt + (-rc - rt) * zfrac       # zfrac 0 (junction) .. 1 (tip)
            sig_web = rt
        elif res == "eccs":
            a = 0.5 if d / bf <= 1.2 else 0.3
            def sig_fl(zfrac): return a * (1 - 2 * zfrac)            # +a at junction, -a at tips
            # NL-R2-27: web +a at the flanges -> -a at mid-depth (linear). The old uniform +a web left a net tension
            # a*Fy*Aw locked into every member at zero load (not self-equilibrating); the linear web has zero mean.
            sig_web = lambda yfrac: a * (2 * yfrac - 1)              # yfrac 0 (mid-depth) .. 1 (flange)
        else:
            def sig_fl(zfrac): return 0.0
            sig_web = 0.0
        self.ops.section("Fiber", secTag, "-GJ", G_KSI * J)
        nb, nt = nf_flange
        dz = bf / nb
        nfib = 0
        for i in range(nb):
            zc = -bf / 2 + (i + 0.5) * dz
            m = self._mat(sig_fl(abs(zc) / (bf / 2)))
            for j in range(nt):
                for sgn in (+1, -1):
                    yc = sgn * (d / 2 - (j + 0.5) * tf / nt)
                    if axis == "y":
                        self.ops.fiber(yc, zc, dz * tf / nt, m)
                    else:
                        self.ops.fiber(zc, yc, dz * tf / nt, m)
                    nfib += 1
        nwy, nwz = nf_web
        dy = hw / nwy; dzw = tw / nwz
        for j in range(nwy):
            yc = -hw / 2 + (j + 0.5) * dy
            m = self._mat(sig_web(abs(yc) / (hw / 2)) if callable(sig_web) else sig_web)
            for q in range(nwz):
                zc = -tw / 2 + (q + 0.5) * dzw
                if axis == "y":
                    self.ops.fiber(yc, zc, dy * dzw, m)
                else:
                    self.ops.fiber(zc, yc, dy * dzw, m)
                nfib += 1
        self.log.append((secTag, label, "W", nfib, "residual=%s axis=%s" % (res, axis)))
        return dict(A=2 * Af + Aw, Ix=2 * (bf * tf ** 3 / 12 + Af * ((d - tf) / 2) ** 2) + tw * hw ** 3 / 12,
                    Iy=2 * tf * bf ** 3 / 12 + hw * tw ** 3 / 12, d=d, bf=bf, tf=tf, tw=tw, nfib=nfib)

    # ---- rectangular HSS --------------------------------------------------------------------
    def hss_rect(self, secTag, label, t_design_factor=0.93, n_per_side=8, n_thick=2, residual=None, material=None, Fy=None):
        """Fibre rectangular HSS (A500: design thickness = 0.93 t_nom, AISC B4.2). Corners squared off,
        area then scaled to the CSV gross area by adjusting the fibre areas (keeps A, ~I).
        material: optional explicit fibre material spec (see _mat_spec; residual stresses are then not applied)."""
        r = shape(label)
        dims = hss_dims(label)
        if dims is None:
            raise ValueError("%s is not a rectangular HSS label" % label)
        H, B, tn = dims
        t = tn * t_design_factor
        J = _f(r["J"]) or 1.0
        A_csv = _f(r["A"])
        res = self.residual if residual is None else residual
        self.ops.section("Fiber", secTag, "-GJ", G_KSI * J)
        # walls as strips: two flanges (width B, thick t) at +/-(H-t)/2 ; two webs (height H-2t) at +/-(B-t)/2
        A_model = 2 * B * t + 2 * (H - 2 * t) * t
        scale = (A_csv / A_model) if A_csv else 1.0
        # fibre layout (y, z, area, residual fraction): two flanges (width B) then two webs (height H - 2t)
        fibs = []
        for sgn in (+1, -1):
            for i in range(n_per_side):
                zc = -B / 2 + (i + 0.5) * B / n_per_side
                # membrane residual: compression at flat centre, tension near corners (provisional)
                sig = 0.0
                if res == "cf_hss_membrane":
                    frac = abs(zc) / (B / 2)
                    sig = -0.15 + 0.30 * frac ** 2
                for j in range(n_thick):
                    yc = sgn * ((H - t) / 2 - t / 2 + (j + 0.5) * t / n_thick)
                    fibs.append((yc, zc, (B / n_per_side) * (t / n_thick), sig))
        for sgn in (+1, -1):
            for i in range(n_per_side):
                yc = -(H - 2 * t) / 2 + (i + 0.5) * (H - 2 * t) / n_per_side
                sig = 0.0
                if res == "cf_hss_membrane":
                    frac = abs(yc) / ((H - 2 * t) / 2)
                    sig = -0.15 + 0.30 * frac ** 2
                for j in range(n_thick):
                    zc = sgn * ((B - t) / 2 - t / 2 + (j + 0.5) * t / n_thick)
                    fibs.append((yc, zc, ((H - 2 * t) / n_per_side) * (t / n_thick), sig))
        if res == "cf_hss_membrane" and not material:
            # NL-R2-27: make the membrane pattern self-equilibrating. -0.15 + 0.30 frac^2 has a wall mean of about
            # -0.05 Fy, i.e. a net COMPRESSION of ~0.05 Fy A locked into every HSS at zero load (Ex13: brace -27 kip
            # at lambda = 0, roof sway +0.011 in). Shift by the area-weighted mean: same shape, zero net force (the
            # pattern is doubly symmetric, so the moments are zero too).
            mean = sum(a * s for _y, _z, a, s in fibs) / sum(a for _y, _z, a, _s in fibs)
            fibs = [(y, z, a, s - mean) for y, z, a, s in fibs]
        nfib = 0
        for yc, zc, a, sig in fibs:
            self.ops.fiber(yc, zc, a * scale, self._mat_spec(material) if material else self._mat(sig, Fy)); nfib += 1
        self.log.append((secTag, label, "HSS", nfib, "residual=%s t_des=%.3f" % (res, t)))
        return dict(A=A_csv or A_model, H=H, B=B, t=t, nfib=nfib)

    # ---- round HSS / pipe (NL-R2-28) --------------------------------------------------------
    def hss_round(self, secTag, label, n_circ=16, n_thick=2, material=None, Fy=None):
        """Fibre ring for round HSS / pipe: OD and wall from the tabulated A and Ix (round_hss_dims), so the fibre
        A and I match the table. No residual stress pattern (none calibrated here)."""
        dims = round_hss_dims(label)
        if dims is None:
            raise ValueError("%s is not a round HSS / pipe label with A, Ix in the table" % label)
        OD, t = dims
        r = shape(label)
        J = _f(r["J"]) or (math.pi * (OD ** 4 - (OD - 2 * t) ** 4) / 32.0)
        self.ops.section("Fiber", secTag, "-GJ", G_KSI * J)
        Ro = OD / 2.0
        nfib = 0
        for j in range(n_thick):
            r_in = Ro - t + j * t / n_thick; r_out = r_in + t / n_thick
            rc = 0.5 * (r_in + r_out); a = math.pi * (r_out ** 2 - r_in ** 2) / n_circ
            for i in range(n_circ):
                th = 2.0 * math.pi * (i + 0.5) / n_circ
                self.ops.fiber(rc * math.cos(th), rc * math.sin(th), a, self._mat_spec(material) if material else self._mat(0.0, Fy))
                nfib += 1
        self.log.append((secTag, label, "HSS-round", nfib, "OD=%.3f t=%.3f (ring matching A, Ix)" % (OD, t)))
        return dict(A=_f(r["A"]), OD=OD, t=t, nfib=nfib)

    # ---- generic dispatcher ----------------------------------------------------------------

    # ---- cold-formed built-up / Z (equivalent rectangle matching A, Ix) ---------------------
    def cfs_equiv_rect(self, secTag, label, props, residual="none", axis="y"):
        """Thin-walled C/channel fibre from label dims (Cdddxbbxtt) or props.

        Matches gross A/Ix/Iy much better than a solid A+Ix rectangle (which crushed Iy by
        ~1e4 and caused false OOP geometric instability on Sena portals). Still NO local /
        distortional buckling — disclose. Residual ignored (no calibrated CFS pattern).

        axis='y' (columns): web along local y (strong Ix about z).
        axis='z' (beams): web along local z (strong Ix about y).
        """
        import re as _re
        A = float(props["A"]); Ix = float(props["Ix"]); Iy = float(props.get("Iy") or Ix * 0.05)
        J = float(props.get("J") or 1e-4)
        labU = str(label).upper().replace(" ", "")
        # built-up e.g. 2xC203x76x2p4 or single C302x96x1p5
        m = _re.match(r"(?:(\d+)X)?C(\d+)X(\d+)X(\d+)P(\d+)", labU)
        n_ply = 1
        if m:
            if m.group(1):
                n_ply = max(int(m.group(1)), 1)
            d_mm, bf_mm, t_int, t_dec = map(int, m.groups()[1:])
            d = d_mm / 25.4
            bf = bf_mm / 25.4
            t = float("%d.%d" % (t_int, t_dec)) / 25.4
            lip = min(1.0, 0.3 * bf)
            # n_ply side-by-side (built-up): thicken web stack in weak dir by n_ply
            # fibre mesh below is single C; scale area by n_ply after A_model
        else:
            n_ply = 1
            d = (12.0 * Ix / max(A, 1e-9)) ** 0.5
            t = max(A / (2.0 * d), 0.04)
            bf = max(Iy / max(t * (d / 2.0) ** 2, 1e-9), 4.0 * t)
            lip = 0.0
        hw = max(d - 2.0 * t, d * 0.85)
        A_model = n_ply * (hw * t + 2.0 * bf * t + (2.0 * lip * t if lip > 1.5 * t else 0.0))
        scale = A / max(A_model, 1e-9)
        self.ops.section("Fiber", secTag, "-GJ", max(G_KSI * J, 1.0))
        nfib = 0
        mat = self._mat(0.0)
        def add_rect(y0, z0, hy, hz, ny=4, nz=2):
            nonlocal nfib
            for i in range(ny):
                for j in range(nz):
                    yc = y0 - hy / 2 + (i + 0.5) * hy / ny
                    zc = z0 - hz / 2 + (j + 0.5) * hz / nz
                    area = scale * (hy / ny) * (hz / nz)
                    if axis == "z":
                        self.ops.fiber(zc, yc, area, mat)
                    else:
                        self.ops.fiber(yc, zc, area, mat)
                    nfib += 1
        y_fl = (d / 2 - t / 2)
        add_rect(0.0, 0.0, hw, t, ny=10, nz=2)
        add_rect(+y_fl, bf / 2 - t / 2, t, bf, ny=2, nz=6)
        add_rect(-y_fl, bf / 2 - t / 2, t, bf, ny=2, nz=6)
        if lip > 1.5 * t:
            z_lip = bf - t / 2
            add_rect(+y_fl - lip / 2, z_lip, lip, t, ny=4, nz=2)
            add_rect(-y_fl + lip / 2, z_lip, lip, t, ny=4, nz=2)
        self.log.append((secTag, label, "CFS-C", nfib,
                         "A=%.3f Ix=%.2f Iy=%.2f d=%.2f bf=%.2f t=%.3f n_ply=%d axis=%s scale=%.3f (NO local/distortional)" % (
                             A, Ix, Iy, d, bf, t, n_ply, axis, scale)))
        return dict(A=A, H=d, B=bf, t=t, nfib=nfib, Ix=Ix, Iy=Iy, J=J)

    def build(self, secTag, label, kind, axis=None):
        lab = str(label).upper()
        if lab.startswith("HSS") and hss_dims(lab):
            return self.hss_rect(secTag, lab, residual=("none" if self.residual == "none" else "cf_hss_membrane"))
        if lab.startswith(("HSS", "PIPE")) and round_hss_dims(lab):
            return self.hss_round(secTag, lab)
        if lab.startswith(("W", "HP", "M", "S")) and not lab.startswith("MC"):
            return self.w_shape(secTag, lab, axis=(axis or ("y" if kind == "col" else "z")))
        props = cfs_section_props(label)
        if props:
            return self.cfs_equiv_rect(secTag, lab, props, residual="none", axis=(axis or ("y" if kind == "col" else "z")))
        raise ValueError("no fibre builder for section %s (kind %s)" % (label, kind))


def elastic_props(label):
    """(A, Ix, Iy, J) from the CSV -- for gates and reporting."""
    r = shape(label)
    return _f(r["A"]), _f(r["Ix"]), _f(r["Iy"]), _f(r["J"])
