"""NL-R2-L3: DDM roof-uplift wind cases apply the HR roof suction.

The 0.9D+1.0W "(roof uplift)" cases ran with the full 0.9D on the roof: the GMNIA gravity called the HR piece builder
(static_model._case_pieces) with uplift=None, so the MWFRS roof suction of ASCE 7-22 Eq. 27.3-1 / Fig. 27.3-1 that the
member design applied in those cases was dropped (Ex21 0.9D+1.0WY+: roof load 1435 kip instead of 543 kip).
"""
import os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

ENGINE = os.environ.get("STELTIC_ENGINE_DIR")
needs_engine = pytest.mark.skipif(not ENGINE, reason="STELTIC_ENGINE_DIR not set")

from test_ddm_nl_r2_33_col_bracing import _cp, _column, _model     # noqa: E402


@needs_engine
def test_roof_uplift_applied_as_the_hr_member_design():
    """0.9D+1.0W '(roof uplift)': the GMNIA roof load equals the HR static_model.apply_case_gravity total WITH the
    uplift flag (roof suction qh (G Cp - GCpi) on the roof bays) -- before, the uplift flag was dropped."""
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    import openseespy.opensees as ops
    import static_model as SM
    from test_ddm_r6 import _hr_box
    from steltic_ddm import loads
    nm, cfg = _hr_box()
    cfg["wind"] = dict(V=115.0, exposure="C", Kd=0.85, Kzt=1.0, Ke=1.0, G=0.85, GCpi=0.18, roof_uplift=True)
    fD, fL, fLr = 0.9, 0.0, SM.RoofFactors(cfg=cfg)
    upl = ("X", 1)
    M = SM.build_static(cfg, "Linear", 6); ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    hr_up = SM.apply_case_gravity(cfg, M, fD, fL, fLr, uplift=upl)["total"]
    M = SM.build_static(cfg, "Linear", 6); ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    hr_no = SM.apply_case_gravity(cfg, M, fD, fL, fLr)["total"]
    assert hr_up < hr_no - 1.0                                      # the suction is real on this roof
    g = _model(nm, rigid_end_offset=0.05, residual="lehigh")
    g.cfg = cfg
    g.build().prepare()
    assert g._grav_geo, g._grav_fail
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    W_no = g.apply_gravity(fD, fL, fLr, loads.present_sets(nm))
    assert g.uplift_note is None
    g.build().prepare(); ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    W_up = g.apply_gravity(fD, fL, fLr, loads.present_sets(nm), uplift=upl)
    assert W_no == pytest.approx(hr_no, rel=1e-9) and W_up == pytest.approx(hr_up, rel=1e-9)
    assert "roof uplift WX+" in g.uplift_note and "27.3-1" in g.uplift_note
    qh, pb = loads.roof_uplift_pressures(cfg, g._grav_geo["model"], upl)
    assert qh > 0 and pb and all(p < 0 for p in pb.values())
    # the uplift pressures account exactly for the difference (roof bay area x p)
    area = sum(b["dx"] * b["dy"] for (k, i, j), b in g._grav_geo["model"]["bays"].items() if b["roof"]) / 144.0
    assert hr_no - hr_up == pytest.approx(-sum(pb.values()) / len(pb) * area / 1000.0, rel=1e-6)
    ops.wipe()


def test_roof_uplift_without_hr_geometry_is_refused():
    """No HR load-path geometry (legacy tributary): an uplift case raises -> the sweep fails and the combination is
    NOT EVALUATED (never analysed without its roof suction); non-uplift cases still run on the legacy path."""
    g = _model(_column(_cp()))
    g._grav_geo, g._grav_fail = False, "test"
    with pytest.raises(RuntimeError, match="roof uplift X\\+ not applied"):
        g.apply_gravity(0.9, 0.0, 0.0, {}, uplift=("X", 1))


def test_sweep_passes_the_uplift_flag():
    """solver.sweep hands the case's uplift attribute to apply_gravity (static_model.Case attribute)."""
    from steltic_ddm import solver

    class Case(tuple):
        pass

    c = Case(("0.9D+1.0WX+ (roof uplift)", 0.9, 0.0, 0.0, {1: (10.0, 0.0, 0.0)}, False))
    c.uplift = ("X", 1)
    seen = {}

    class Stop(Exception):
        pass

    class Fake:
        def build(self):
            return self

        def prepare(self):
            import openseespy.opensees as ops
            ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
            return self

        def apply_gravity(self, fD, fL, fLr, pres, uplift=None):
            seen["uplift"] = uplift
            raise Stop()

    with pytest.raises(Stop):
        solver.sweep(Fake(), c, {}, verbose=False)
    assert seen["uplift"] == ("X", 1)
