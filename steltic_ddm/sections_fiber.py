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
    """'HSS8X8X1/2' -> (H, B, t_nominal). Returns None for round/other."""
    m = re.match(r"HSS(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:/\d+)?(?:\.\d+)?)$", label.upper().replace("-", ""))
    if not m:
        return None
    H, B = float(m.group(1)), float(m.group(2))
    t = m.group(3)
    t = float(t.split("/")[0]) / float(t.split("/")[1]) if "/" in t else float(t)
    return H, B, t


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
    def _mat(self, sig0_frac):
        """Steel01 (+InitStress) for a residual-stress level sig0_frac*Fy; cached per level."""
        key = round(sig0_frac, 4)
        if key in self._mat_cache:
            return self._mat_cache[key]
        tag = self.next_mat; self.next_mat += 1
        if self.elastic:
            self.ops.uniaxialMaterial("Elastic", tag, self.E)
            self._mat_cache[key] = tag
            return tag
        self.ops.uniaxialMaterial("Steel01", tag, self.Fy, self.E, self.b)
        if abs(sig0_frac) > 1e-9:
            wtag = self.next_mat; self.next_mat += 1
            self.ops.uniaxialMaterial("InitStressMaterial", wtag, tag, sig0_frac * self.Fy)
            self._mat_cache[key] = wtag
            return wtag
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
            sig_web = a                                              # ECCS: web +a at flanges -> -a mid; use mean + for simplicity
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
        m = self._mat(sig_web)
        dy = hw / nwy; dzw = tw / nwz
        for j in range(nwy):
            yc = -hw / 2 + (j + 0.5) * dy
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
    def hss_rect(self, secTag, label, t_design_factor=0.93, n_per_side=8, n_thick=2, residual=None):
        """Fibre rectangular HSS (A500: design thickness = 0.93 t_nom, AISC B4.2). Corners squared off,
        area then scaled to the CSV gross area by adjusting the fibre areas (keeps A, ~I)."""
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
        nfib = 0
        def fib(y, z, a, sig):
            self.ops.fiber(y, z, a * scale, self._mat(sig))
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
                    fib(yc, zc, (B / n_per_side) * (t / n_thick), sig); nfib += 1
        for sgn in (+1, -1):
            for i in range(n_per_side):
                yc = -(H - 2 * t) / 2 + (i + 0.5) * (H - 2 * t) / n_per_side
                sig = 0.0
                if res == "cf_hss_membrane":
                    frac = abs(yc) / ((H - 2 * t) / 2)
                    sig = -0.15 + 0.30 * frac ** 2
                for j in range(n_thick):
                    zc = sgn * ((B - t) / 2 - t / 2 + (j + 0.5) * t / n_thick)
                    fib(yc, zc, ((H - 2 * t) / n_per_side) * (t / n_thick), sig); nfib += 1
        self.log.append((secTag, label, "HSS", nfib, "residual=%s t_des=%.3f" % (res, t)))
        return dict(A=A_csv or A_model, H=H, B=B, t=t, nfib=nfib)

    # ---- generic dispatcher ----------------------------------------------------------------
    def build(self, secTag, label, kind, axis=None):
        lab = str(label).upper()
        if lab.startswith("HSS") and hss_dims(lab):
            return self.hss_rect(secTag, lab, residual=("none" if self.residual == "none" else "cf_hss_membrane"))
        if lab.startswith(("W", "HP", "M", "S")) and not lab.startswith("MC"):
            return self.w_shape(secTag, lab, axis=(axis or ("y" if kind == "col" else "z")))
        raise ValueError("no fibre builder for section %s (kind %s)" % (label, kind))


def elastic_props(label):
    """(A, Ix, Iy, J) from the CSV -- for gates and reporting."""
    r = shape(label)
    return _f(r["A"]), _f(r["Ix"]), _f(r["Iy"]), _f(r["J"])
