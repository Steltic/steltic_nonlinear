"""NL-R2-L2: plate shear walls (SPSW, AISC 341-22 F5; C-PSW/CF and CC-PSW/CF, H7 / H8) are refused up front as NOT
EVALUATED by the pushover, the NLRHA and the DDM -- the same gate as the STMF (NL-R2-13) -- instead of crashing in the
model build (Ex9 / Ex27: "fibre section RIGID_ZONE (beam): section 'RIGID_ZONE' not in aisc_shapes.csv")."""
import os, sys, types
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pushover import hinge_models as HM
from pushover import nonlinear_model as NM

B = lambda **k: types.SimpleNamespace(**{"system": None, "system_X": None, "system_Y": None, **k})

EX9 = dict(system="SPSW", system_X="SPSW (piers X1, X2, X3; penthouse storey 13: 2-bay SMF on grid C, RBS)",
           system_Y="SPSW (piers Y1, Y2 levels 1-13, Y3 levels 1-12)")
EX27 = dict(system="steel and concrete coupled composite plate shear walls (CC-PSW/CF, AISC 341-22 H8)")


@pytest.mark.parametrize("basis,clause", [
    (EX9, "F5"), (EX27, "H7 / H8"),
    (dict(system="special plate shear wall"), "F5"), (dict(system="C-PSW/CE"), "H7"),
    (dict(system="SMF", system_Y="SpeedCore core walls"), "H8"),
])
def test_plate_shear_walls_refused_by_every_engine(basis, clause):
    for engine in ("nonlinear", "ddm"):
        why = HM.unsupported_system(B(**basis), engine=engine)
        assert why and "NOT EVALUATED" in why and clause in why, (engine, why)


def test_ddm_keeps_the_stmf_and_supported_systems():
    assert HM.unsupported_system(B(system="STMF"), engine="ddm") is None          # the DDM models an STMF (NL-R2-13)
    assert "STMF" in HM.unsupported_system(B(system="STMF"))
    for ok in ("EBF", "SMF", "dual SMF+BRBF", "SCBF", "IMF (Y, transverse) + MT-OCBF (X, longitudinal)", "OCBF", None,
               "steel system not specifically detailed for seismic resistance"):
        assert HM.unsupported_system(B(system=ok)) is None
        assert HM.unsupported_system(B(system=ok), engine="ddm") is None


def test_build_nonlinear_backstop():
    with pytest.raises(HM.UnsupportedSystem, match="NOT EVALUATED.*SPSW"):
        NM.build_nonlinear(types.SimpleNamespace(basis=B(**EX9)), HM.load_params(), {})


def test_cli_gates(tmp_path, monkeypatch):
    """pushover run / nlrha run / steltic_ddm run exit with the refusal before any model is built."""
    from pushover import cli as PC
    from nlrha import cli as NC
    from steltic_ddm import cli as DC
    pkg = types.SimpleNamespace(basis=B(**EX27))
    with pytest.raises(SystemExit, match="NLRHA NOT EVALUATED.*composite plate shear wall"):
        NC._refuse_unsupported(pkg)
    nm = types.SimpleNamespace(cfg=dict(EX9))
    monkeypatch.setattr(DC.ingest, "load_package", lambda job, eng: nm)
    args = types.SimpleNamespace(job_dir=str(tmp_path), steltic_engine=None, out=str(tmp_path))
    with pytest.raises(SystemExit, match="DDM NOT EVALUATED.*SPSW"):
        DC.run(args)
    from pushover import package_reader as PR
    monkeypatch.setattr(PR, "load", lambda path: pkg)
    monkeypatch.setattr(PR, "summary", lambda p: "")
    pargs = types.SimpleNamespace(package=str(tmp_path), out=None, system=None)
    with pytest.raises(SystemExit, match="pushover NOT EVALUATED.*H7"):
        PC._run(pargs)
