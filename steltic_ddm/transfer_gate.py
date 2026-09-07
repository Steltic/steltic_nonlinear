"""
transfer_gate.py -- prove the GMNIA rebuild IS the Steltic building before anything nonlinear runs.

Compares, elastic vs elastic:
  * the first three periods (Steltic replay model with its recorded masses vs. the GMNIA topology
    with elastic fibre sections, no imperfections, same masses)
  * roof displacement under the same ELF-pattern lateral load in X and in Y
A mismatch beyond `tol` (default 5 %) blocks the run and names the likely cause (orientation,
release decoding, diaphragm membership, section mapping). Fibre W-sections ignore fillets, so a
1-3 % softer GMNIA model is expected and reported, not flagged.
"""
import math, re
import openseespy.opensees as ops
from .model_gmnia import GMNIAModel


def _replay(path):
    """Execute the recorded ops.* lines. Steltic's export contains the probe build followed by the real
    build, and geomTransf is only recorded in the first one -- so the transforms are re-issued after
    every ops.model(...) line (the standalone file itself fails at its first element for this reason)."""
    ops.wipe()
    ns = {"ops": ops, "math": math}
    transf = []
    for line in open(path):
        s = line.strip()
        if not s.startswith("ops.") or s.startswith(("ops.eigen", "ops.getNodeTags", "ops.getEleTags")):
            continue
        if s.startswith("ops.geomTransf"):
            transf.append(s)
        exec(s, ns)
        if s.startswith("ops.model("):
            for t in transf:
                exec(t, ns)


def _periods(n=3):
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    try:
        w2 = ops.eigen("-genBandArpack", n)
    except Exception:
        w2 = ops.eigen("-fullGenLapack", n)
    return [2 * math.pi / math.sqrt(max(w, 1e-12)) for w in w2]


def _lateral_disp(masters, lat, dirn):
    ops.wipe_analysis() if hasattr(ops, "wipe_analysis") else ops.wipeAnalysis()
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for k, (fx, fy, mz) in lat.items():
        ops.load(k * 100000 + 99999, fx, fy, 0.0, 0.0, 0.0, mz)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Linear")
    ops.integrator("LoadControl", 1.0); ops.analysis("Static"); ok = ops.analyze(1)
    d = ops.nodeDisp(masters[-1], 1 if dirn == "X" else 2)
    ops.remove("loadPattern", 1)
    return ok, d


def run(nm, cfg, lat_x, lat_y, tol=0.05, nsub=(2, 2, 2)):
    """lat_x / lat_y: {level: (fx, fy, mz)} unit lateral patterns (e.g. the ELF pattern)."""
    replay = nm.job_dir + "/model_opensees.py"
    masters = sorted(t for t in nm.nodes if t % 100000 == 99999)
    # --- Steltic elastic model
    _replay(replay)
    T_ref = _periods(3)
    _replay(replay); okx, dx_ref = _lateral_disp(masters, lat_x, "X")
    _replay(replay); oky, dy_ref = _lateral_disp(masters, lat_y, "Y")
    # --- GMNIA topology, elastic
    g = GMNIAModel(nm, cfg, nsub=nsub, elastic=True, residual="none", out_of_plumb=(None, 0.0), bow=0.0, brace_bow=0.0)
    g.build(with_mass=True)
    T_new = _periods(3)
    g.build(); _, dx_new = _lateral_disp(masters, lat_x, "X")
    g.build(); _, dy_new = _lateral_disp(masters, lat_y, "Y")
    rows = []
    def cmp(name, a, b):
        r = b / a if abs(a) > 1e-12 else float("nan")
        rows.append(dict(quantity=name, steltic=a, gmnia=b, ratio=r, ok=abs(r - 1) <= tol))
    for i, (a, b) in enumerate(zip(T_ref, T_new)):
        cmp("T%d (s)" % (i + 1), a, b)
    cmp("roof disp X under ELF pattern (in)", dx_ref, dx_new)
    cmp("roof disp Y under ELF pattern (in)", dy_ref, dy_new)
    ok = all(r["ok"] for r in rows)
    hint = None
    if not ok:
        worst = max(rows, key=lambda r: abs(r["ratio"] - 1))
        if "disp" in worst["quantity"] and worst["ratio"] > 1.3:
            hint = "GMNIA model much softer in that direction: check column strong_dir decoding (transf 1/2) or a lost brace pin/diaphragm membership"
        elif worst["ratio"] < 0.8:
            hint = "GMNIA model stiffer: a beam release was not decoded (relz -> -releasey) or a pin has no released DOF"
        else:
            hint = "check section-label mapping (member_schedule.csv) and node/element ordering"
    return dict(ok=ok, rows=rows, tol=tol, hint=hint, elements_steltic=len(nm.members), elements_gmnia=len(g.elems))
