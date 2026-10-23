"""DDM on packages of the current HR engine (October 2026).

* Wind load Cases 2-4 (ASCE 7-22 Fig. 27.3-8): HR's "(roof uplift)" flags are (dirn, sgn, scale) with the roof suction
  scaled by 0.75 / 0.563. roof_uplift_pressures unpacked two values, so every Case 2-4 uplift sweep failed ("too many
  values to unpack") and 20 combinations per building were NOT EVALUATED.
* Flexible buildings: the member design's roof suction uses the MWFRS Gf of 26.11.5 (cfg['_wind_G'], resolved by
  static_model.demand_envelope); the DDM now resolves it the same way once per run and gives it to every sweep.
* STMF designed on a stand-in model (each truss an analysis W44, the truss members designed by hand from its forces):
  the GMNIA would check the stand-in, so the DDM refuses it as NOT EVALUATED."""
import inspect, os, sys, types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

ENGINE = os.environ.get("STELTIC_ENGINE_DIR")
needs_engine = pytest.mark.skipif(not ENGINE, reason="STELTIC_ENGINE_DIR not set")

from steltic_ddm import loads  # noqa: E402


def _engine_has_case_scale():
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    import static_model as SM
    return "uplift[2]" in inspect.getsource(SM._case_pieces)


@needs_engine
def test_case_2_to_4_roof_uplift_scaled_with_the_design_gust_factor():
    if not _engine_has_case_scale():
        pytest.skip("HR engine before the Fig. 27.3-8 Cases 2-4 uplift scale")
    import openseespy.opensees as ops
    import static_model as SM
    from test_ddm_r6 import _hr_box
    from test_ddm_nl_r2_33_col_bracing import _model
    nm, cfg = _hr_box()
    cfg["wind"] = dict(V=115.0, exposure="C", Kd=0.85, Kzt=1.0, Ke=1.0, G=0.85, GCpi=0.18, roof_uplift=True)
    cfg["_wind_G"] = {"X": 0.95, "Y": 0.93}                         # a flexible building's Gf, as demand_envelope sets it
    fD, fL, fLr = 0.9, 0.0, SM.RoofFactors(cfg=cfg)
    upl = ("X", 1, 0.75)                                            # Case 2 (0.75 of the Case 1 pressures)
    M = SM.build_static(cfg, "Linear", 6); ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    hr_up = SM.apply_case_gravity(cfg, M, fD, fL, fLr, uplift=upl)["total"]
    M = SM.build_static(cfg, "Linear", 6); ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    hr_no = SM.apply_case_gravity(cfg, M, fD, fL, fLr)["total"]
    g = _model(nm, rigid_end_offset=0.05, residual="lehigh")
    g.cfg = cfg
    g.build().prepare()
    assert g._grav_geo, g._grav_fail
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    W_up = g.apply_gravity(fD, fL, fLr, loads.present_sets(nm), uplift=upl)     # raised "too many values to unpack"
    assert W_up == pytest.approx(hr_up, rel=1e-9)
    assert "WX+ x 0.75 (Fig. 27.3-8 Cases 2-4)" in g.uplift_note and "G = 0.950" in g.uplift_note
    qh, pb = loads.roof_uplift_pressures(cfg, g._grav_geo["model"], upl)
    area = sum(b["dx"] * b["dy"] for (k, i, j), b in g._grav_geo["model"]["bays"].items() if b["roof"]) / 144.0
    assert hr_no - hr_up == pytest.approx(-sum(pb.values()) / len(pb) * area / 1000.0, rel=1e-6)
    qh1, pb1 = loads.roof_uplift_pressures(cfg, g._grav_geo["model"], ("X", 1))
    assert qh == pytest.approx(0.75 * qh1)
    # G: the design's Gf where resolved, cfg['wind']['G'] otherwise
    c2 = dict(cfg); c2.pop("_wind_G")
    _, pb2 = loads.roof_uplift_pressures(c2, g._grav_geo["model"], ("X", 1))
    k0 = sorted(pb1)[0]
    cp_gcpi = lambda G, p: (p / qh1 + 0.18) / G                    # the bay's Cp from p = qh (G Cp - GCpi)
    assert cp_gcpi(0.95, pb1[k0]) == pytest.approx(cp_gcpi(0.85, pb2[k0]))
    ops.wipe()


def test_wind_gust_factors(monkeypatch):
    assert loads.wind_gust_factors({}) is None                                       # no wind: nothing to resolve
    assert loads.wind_gust_factors({"wind": {"V": 115}, "_wind_G": {"X": 0.9, "Y": 0.91}}) == {"X": 0.9, "Y": 0.91}
    fake = types.SimpleNamespace(wind_detail=lambda cfg, d: {"G": {"X": 0.923, "Y": 0.922}[d]})
    monkeypatch.setitem(sys.modules, "engine3d", fake)
    assert loads.wind_gust_factors({"wind": {"V": 115}}) == {"X": 0.923, "Y": 0.922}
    monkeypatch.setitem(sys.modules, "engine3d", types.SimpleNamespace(wind_detail=lambda cfg, d: 1 / 0))
    assert loads.wind_gust_factors({"wind": {"V": 115}}) is None                     # as demand_envelope: keep G


def test_sweep_gets_the_run_gust_factor(monkeypatch):
    """_sweep_one puts the run's G into the worker's cfg before the model is built (never overriding a package value)."""
    from steltic_ddm import cli as DC
    seen = {}

    class Stop(Exception):
        pass

    nm = types.SimpleNamespace(cfg={"system": "SMF", "wind": {"V": 115}})
    monkeypatch.setattr(DC.ingest, "load_package", lambda job, eng: nm)
    monkeypatch.setattr(DC.loads, "steltic_combos", lambda cfg, nm=None: [("0.9D+1.0WX+ (roof uplift)",)])

    def fake_model(nm_, cfg, **kw):
        seen["G"] = cfg.get("_wind_G"); raise Stop()
    monkeypatch.setattr(DC, "GMNIAModel", fake_model)
    monkeypatch.setattr(DC.loads, "present_sets", lambda nm_: {})
    opts = dict(nsub=[4, 4, 6], residual="lehigh", hardening=0.002, fast=False, nip=5, bow=0.001, wind_G={"X": 0.95, "Y": 0.93})
    with pytest.raises(Stop):
        DC._sweep_one(("job", None, "0.9D+1.0WX+ (roof uplift)", dict(dir="X", psi=0.002, bow_sign=1, tag="+X"), opts))
    assert seen["G"] == {"X": 0.95, "Y": 0.93}


def test_stand_in_model_refused(tmp_path, monkeypatch):
    from steltic_ddm import cli as DC
    calc = {"members": [{"id": "floor-STMF-truss-X", "inputs": {"section": "STMF truss X, 6 ft deep (analysis stand-in W44X230)"}},
                        {"id": "roof-STMF-truss-X", "inputs": {"section": "STMF truss X, 6 ft deep (analysis stand-in W44X230)"}},
                        {"id": "lateral_col-W14X342", "inputs": {"section": "W14X342"}}]}
    assert loads.stand_in_members(calc) == [("floor-STMF-truss-X", calc["members"][0]["inputs"]["section"]),
                                            ("roof-STMF-truss-X", calc["members"][1]["inputs"]["section"])]
    assert loads.stand_in_members({"members": [{"id": "c", "inputs": {"section": "W14X342"}}]}) == []
    assert loads.stand_in_members(None) == []
    nm = types.SimpleNamespace(cfg=dict(system="STMF"), calc_package=calc)
    monkeypatch.setattr(DC.ingest, "load_package", lambda job, eng: nm)
    args = types.SimpleNamespace(job_dir=str(tmp_path), steltic_engine=None, out=str(tmp_path))
    with pytest.raises(SystemExit, match=r"DDM NOT EVALUATED -- the HR analysis model represents floor-STMF-truss-X/"
                                         r"roof-STMF-truss-X by an analysis stand-in section"):
        DC.run(args)
