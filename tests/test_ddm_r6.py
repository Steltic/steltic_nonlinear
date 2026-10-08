"""R6 DDM fixes (NL-R2-02, -03, -11, -23) on small synthetic models -- no building package, no Steltic engine."""
import os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def _frame(H=144.0, S=300.0):
    """One storey, one bay each way: four W14X90 columns (fixed bases), four rigid W18X35 beams, rigid diaphragm."""
    from steltic_ddm.ingest import NeutralModel, Member, _assign_roles
    nm = NeutralModel(job_dir=".", name="t")
    for k in (0, 1):
        for i in (1, 2):
            for j in (1, 2):
                t = k * 100000 + i * 100 + j
                nm.nodes[t] = ((i - 1) * S, (j - 1) * S, k * H)
                if k == 0:
                    nm.fixes[t] = (1, 1, 1, 1, 1, 1)
    nm.nodes[199999] = (S / 2, S / 2, H)
    nm.diaphragms[199999] = [100101, 100102, 100201, 100202]
    tg = 1
    for i in (1, 2):
        for j in (1, 2):
            nm.members.append(Member(tg, "col", "W14X90", i * 100 + j, 100000 + i * 100 + j, transf=1 + (i + j) % 2, dirn="Z")); tg += 1
    for j in (1, 2):
        nm.members.append(Member(tg, "beam", "W18X35", 100100 + j, 100200 + j, transf=3, dirn="X")); tg += 1
    for i in (1, 2):
        nm.members.append(Member(tg, "beam", "W18X35", 100000 + i * 100 + 1, 100000 + i * 100 + 2, transf=3, dirn="Y")); tg += 1
    nm.levels = [0.0, H]
    _assign_roles(nm)
    cfg = dict(heights=[H], NX=1, NY=1, SX=S, SY=S, D_floor=80.0, D_roof=30.0, L_floor=50.0, snow=20.0, Fy=50.0)
    return nm, cfg


def test_nl_r2_02_control_direction_ignores_lambda_independent_state():
    """A lambda-independent +Y state (stand-in for the Lehigh locked-in state, Ex13 roof +0.011 in) under a -Y wind
    pattern: the old code took the control sign from the TOTAL probe displacement (+Y), lambda fell and the sweep
    stopped at the probe as a false 'geometric instability'. Now the control follows the -Y pattern and lambda rises."""
    import openseespy.opensees as ops
    from steltic_ddm.model_gmnia import GMNIAModel
    from steltic_ddm import solver, phi_s, report_ddm
    nm, cfg = _frame()

    class Locked(GMNIAModel):
        def apply_lateral(self, lat):
            super().apply_lateral(lat)
            ops.timeSeries("Constant", 2); ops.pattern("Plain", 2, 2)           # NOT scaled by lambda
            ops.load(199999, 0.0, 30.0, 0.0, 0.0, 0.0, 0.0)

    g = Locked(nm, cfg, nsub=(2, 2, 2))
    combo = ("0.9D+1.0WY-", 0.9, 0.0, 0.0, {1: (0.0, -20.0, 0.0)}, False)
    pres = {1: {(1, 1), (1, 2), (2, 1), (2, 2)}}
    r = solver.sweep(g, combo, pres, dlam=0.05, max_steps=12, verbose=False)
    assert r["control"] == (199999, 2) and r["lateral"] == ("Y", -1)
    assert r["d_zero"] > 0.1                                     # the locked-in state is measured at lambda = 0 ...
    assert r["probe_slope"] < 0                                  # ... and the load-proportional part is -Y
    lams = [l for l, _ in r["hist"]]; ds = [d for _, d in r["hist"]]
    assert all(b > a for a, b in zip(lams, lams[1:]))            # lambda rises step after step
    assert all(b < a for a, b in zip(ds, ds[1:]))                # the roof is pushed in -Y
    assert r["lambda_u"] > 0.5 and r["step_at_max"] > 0
    # a run whose lambda never rose above the probe is NUMERICAL / NOT EVALUATED, never a structural FAIL
    stuck = dict(r, step_at_max=0, lambda_u=0.05, snapshot=dict(yield_ratio={}, braces={}))
    c = solver.classify(stuck, g)
    assert c["cls"] == "numerical" and "NUMERICAL" in c["mechanism"]
    assert phi_s.check(0.85, 0.05, c["cls"]) == (None, "NOT EVALUATED")
    assert phi_s.check(0.85, 0.05, "instability") == (0.043, "FAIL")
    runs = [dict(check=(1.2, "PASS")), dict(check=(None, "NOT EVALUATED"))]
    assert report_ddm.verdict(runs) == "INCOMPLETE"
    assert report_ddm.verdict(runs + [dict(check=(0.9, "FAIL"))]) == "FAIL"
    assert report_ddm.verdict(runs[:1]) == "PASS"
