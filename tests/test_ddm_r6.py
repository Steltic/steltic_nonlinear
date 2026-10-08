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


ENGINE = os.environ.get("STELTIC_ENGINE_DIR")
needs_engine = pytest.mark.skipif(not ENGINE, reason="STELTIC_ENGINE_DIR not set")


def _hr_box(deck_span="X", infill_spacing=90.0, SX=360.0, SY=300.0, H=144.0):
    """Two storeys, one 30 x 25 ft bay, HR grid conventions (i, j from 0, node k*1e5+i*100+j), pinned beams, a
    one-way deck spanning `deck_span` with virtual infill at `infill_spacing` -- the cfg is a valid HR cfg."""
    from steltic_ddm.ingest import NeutralModel, Member, _assign_roles
    cfg = dict(NX=1, NY=1, SX=SX, SY=SY, heights=[H, H], col="W14X90", beam="W18X35", base="fixed",
               D_floor=80.0, D_roof=30.0, L_floor=50.0, Lr=20.0, snow=0.0, clad=0.0, Fy=50.0,
               floor_system="one-way", deck_span=deck_span, infill_spacing=infill_spacing)
    nm = NeutralModel(job_dir=".", name="box")
    for k in (0, 1, 2):
        for i in (0, 1):
            for j in (0, 1):
                t = k * 100000 + i * 100 + j
                nm.nodes[t] = (i * SX, j * SY, k * H)
                if k == 0:
                    nm.fixes[t] = (1, 1, 1, 1, 1, 1)
        if k:
            nm.nodes[k * 100000 + 99999] = (SX / 2, SY / 2, k * H)
            nm.diaphragms[k * 100000 + 99999] = [k * 100000 + i * 100 + j for i in (0, 1) for j in (0, 1)]
    tg = 1
    for k in (1, 2):
        for i in (0, 1):
            for j in (0, 1):
                nm.members.append(Member(tg, "col", "W14X90", (k - 1) * 100000 + i * 100 + j, k * 100000 + i * 100 + j,
                                         transf=2, dirn="Z")); tg += 1
        for j in (0, 1):
            nm.members.append(Member(tg, "beam", "W18X35", k * 100000 + j, k * 100000 + 100 + j, transf=3, relz=3, dirn="X")); tg += 1
        for i in (0, 1):
            nm.members.append(Member(tg, "beam", "W18X35", k * 100000 + i * 100, k * 100000 + i * 100 + 1, transf=3, relz=3, dirn="Y")); tg += 1
    nm.levels = [0.0, H, 2 * H]
    _assign_roles(nm)
    return nm, cfg


@needs_engine
def test_nl_r2_03_gravity_follows_hr_one_way_load_path():
    """Deck spanning X with infill @ 90 in: the Y column-line beams carry a HALF infill strip (45 in), the X girders
    the infill reactions as point loads -- exactly HR static_model (Ex13 member 133: 58.8 kip two-way vs HR 39.2)."""
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    import openseespy.opensees as ops
    import static_model as SM
    from steltic_ddm.model_gmnia import GMNIAModel
    from steltic_ddm import loads
    nm, cfg = _hr_box()
    fD, fL, fLr = 1.2, 1.6, SM.RoofFactors(Lr=0.5, cfg=cfg)
    w_floor = (1.2 * 80.0 + 1.6 * 50.0) / 144000.0                 # kip/in2 on level 1
    # HR reference total for the same case
    M = SM.build_static(cfg, "Linear", 6); ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    hr_total = SM.apply_case_gravity(cfg, M, fD, fL, fLr)["total"]

    g = GMNIAModel(nm, cfg, nsub=(2, 2, 2), rigid_end_offset=0.05)
    g.build().prepare()
    assert g._grav_geo, g._grav_fail
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    W = g.apply_gravity(fD, fL, fLr, loads.present_sets(nm))
    assert abs(W - hr_total) < 1e-6 * hr_total and not g.gravity_notes
    by = g.gravity_by_member
    lvl1 = [m for m in nm.members if m.kind == "beam" and m.n1 // 100000 == 1]
    for m in lvl1:
        if m.dirn == "Y":                                        # infill-type column-line beam: half strip
            assert abs(by[m.tag] - w_floor * 45.0 * 300.0) < 1e-6
        else:                                                    # girder: 3 infill reactions of a 90 in strip
            assert abs(by[m.tag] - 3 * w_floor * 90.0 * 300.0 / 2.0) < 1e-6
    # the loads really are in the OpenSees domain (uniform + point): base reactions balance them
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack"); ops.test("NormDispIncr", 1e-8, 25, 0)
    ops.algorithm("Newton"); ops.integrator("LoadControl", 0.1); ops.analysis("Static")
    assert ops.analyze(1) == 0
    ops.reactions()
    Rz = sum(ops.nodeReaction(t, 3) for t in nm.fixes)
    assert abs(Rz - 0.1 * W) < 1e-3 * W
    # the legacy two-way tributary (what the DDM used before) overloads the Y beams
    w_two = sum(loads.beam_udl(cfg, nm, loads.present_sets(nm), lvl1[2], s, 8, fD, fL, fLr) * 300.0 / 8 for s in range(8))
    assert w_two > 1.5 * w_floor * 45.0 * 300.0


def test_nl_r2_03_no_hr_geometry_falls_back_loudly():
    """A cfg the HR static model cannot build (or no engine) keeps the legacy distribution and says so in the log."""
    from steltic_ddm.model_gmnia import GMNIAModel
    nm, cfg = _frame()
    g = GMNIAModel(nm, cfg, nsub=(2, 2, 2))
    g.build().prepare()
    assert g._grav_geo is False
    assert any("legacy two-way" in str(row[-1]) for row in g.builder.log)

