"""NL-R2-17: per-direction design factors of a mixed-system building (ASCE 7-22 12.2.2, Ex24: SMF in X, SCBF in Y).

Parsing fixtures only: package_reader per-direction basis, the pushover P-695 factors, the 16.4.1.2 limits per
direction, the DDM phi_s system R per combination direction -- and single-system buildings unchanged."""
import json, os, sys, types
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pushover import package_reader as PR  # noqa: E402

EX24_CFG = '''
cfg = dict(
    system="mixed per direction (ASCE 7-22 12.2.2): SMF in X (Grids A, D) + SCBF in Y (Grids 1, 4, 7)",
    system_X="SMF", system_Y="SCBF", brace_system="SCBF",
    seis=eng.seis(1.00, 0.58, 0.50, 8, 0.028, 0.8, 1.4, Ie=1.0, Cd=5.5, Om0=3.0, system="SMF"),
    seis_X=dict(R=8, Cd=5.5, Om0=3.0, Ct=0.028, x=0.8, system="SMF"),
    seis_Y=dict(R=6, Cd=5.0, Om0=2.0, Ct=0.02, x=0.75, system="SCBF"),
    drift_limit=0.020,
)
'''
EX24_SBD = {"X": {"system": "SMF", "R": 8, "Cd": 5.5, "Omega0": 3.0, "rho": 1.0, "Tu_s": 1.83, "Cs": 0.044, "V_kip": 678.8, "W_kip": 15426.5, "drift_limit": 0.02},
            "Y": {"system": "SCBF", "R": 6, "Cd": 5.0, "Omega0": 2.0, "rho": 1.3, "Tu_s": 1.09, "Cs": 0.0887, "V_kip": 1367.6, "W_kip": 15426.5, "drift_limit": 0.02}}
REPORT = "<p>R / Cd / Ω0 / Ie 8 / 5.5 / 3.0 / 1.0</p><p>Seismic weight W = 15426 kip</p><p>Base shear ΣF = 1368 kip</p>"


def _basis(tmp_path, cfg_text, calc=None, report=REPORT):
    (tmp_path / "cfg.py").write_text(cfg_text)
    (tmp_path / "report.html").write_text(report)
    return PR.read_basis(tmp_path, calc or {})


def test_mixed_system_reads_per_direction_factors(tmp_path):
    b = _basis(tmp_path, EX24_CFG, calc={"seismic_by_direction": EX24_SBD})
    assert (b.R, b.Cd, b.Om0, b.V_design_kip) == (8.0, 5.5, 3.0, 1368.0)      # the headline (single set) is unchanged
    x, y = PR.dir_basis(b, "X"), PR.dir_basis(b, "Y")
    assert (x["system"], x["R"], x["Cd"], x["Om0"], x["V_design_kip"], x["T_design_s"]) == ("SMF", 8, 5.5, 3.0, 678.8, 1.83)
    assert (y["system"], y["R"], y["Cd"], y["Om0"], y["V_design_kip"], y["T_design_s"]) == ("SCBF", 6, 5.0, 2.0, 1367.6, 1.09)
    assert x["per_direction"] and "seismic_by_direction" in x["source"]["V_design_kip"]
    # without calc_package: cfg seis_X / seis_Y, V per direction from the report text when it gives it
    b2 = _basis(tmp_path, EX24_CFG, report=REPORT + "<p>per-direction ELF: V_X = 678.8 k, V_Y = 1367.6 k</p>")
    assert PR.dir_basis(b2, "Y")["Om0"] == 2.0 and PR.dir_basis(b2, "Y")["source"]["R"].startswith("cfg.py")
    assert PR.dir_basis(b2, "X")["V_design_kip"] == 678.8 and PR.dir_basis(b2, "Y")["source"]["V_design_kip"] == "report.html V_Y"
    # a direction without its own seis dict takes the building set (Ex33: seis_X = OCBF, Y = the cfg seis IMF)
    b3 = _basis(tmp_path, "cfg = dict(system_X='MT-OCBF', system_Y='IMF', seis=eng.seis(0.42, 0.17, 0.10, 4.5, 0.028, 0.8, 1.56, Ie=1.0, Cd=4.0, Om0=3.0, system='IMF'),\n"
                          "           seis_X=dict(R=3.25, Cd=3.25, Om0=2.0, system='OCBF'))\n")
    assert PR.dir_basis(b3, "X")["R"] == 3.25 and PR.dir_basis(b3, "Y")["R"] == 4.5 and PR.dir_basis(b3, "Y")["system"] == "IMF"


def test_single_system_building_keeps_the_single_set(tmp_path):
    for cfg in ('cfg = dict(system_X="SMF", system_Y="SMF", seis=eng.seis(1.0, 0.6, 0.6, 8, 0.028, 0.8, 1.4, Ie=1.0, system="SMF"))\n',
                'cfg = dict(system_X="R=3 X-braced frames Grid A", system_Y="R=3 X-braced frames Grid 1", seis=dict(SDS=0.2, SD1=0.09, S1=0.09, R=3.0, Cd=3.0, Om0=3.0))\n'):
        b = _basis(tmp_path, cfg)
        assert b.by_dir == {}
        for d in ("X", "Y", None):
            db = PR.dir_basis(b, d)
            assert (db["R"], db["Cd"], db["Om0"], db["V_design_kip"], db["T_design_s"], db["per_direction"]) == (b.R, b.Cd, b.Om0, b.V_design_kip, b.T_design_s, False)


def _run(direction):
    u = np.linspace(0, 40, 41); V = np.concatenate([np.linspace(0, 3000, 21), np.linspace(3000, 2000, 20)])
    return dict(direction=direction, rec=dict(u=u.tolist(), V=V.tolist()), pattern=dict(T1=1.5), tail={})


def test_p695_overstrength_uses_the_push_direction_design(tmp_path):
    from pushover import postprocess as PP
    b = _basis(tmp_path, EX24_CFG, calc={"seismic_by_direction": EX24_SBD})
    nsp = dict(W_kip=15426.0, C0=1.3)
    px, py = PP.p695_factors(_run("X"), b, nsp), PP.p695_factors(_run("Y"), b, nsp)
    assert px["Omega"] == pytest.approx(3000.0 / 678.8) and px["Omega0_design"] == 3.0 and px["R_design"] == 8
    assert py["Omega"] == pytest.approx(3000.0 / 1367.6) and py["Omega0_design"] == 2.0 and py["R_design"] == 6 and py["system_design"] == "SCBF"
    assert px["T_used_s"] == pytest.approx(1.83) and py["T_used_s"] == pytest.approx(1.5)
    s = _basis(tmp_path, 'cfg = dict(system="SMF", seis=eng.seis(1.0, 0.6, 0.6, 8, 0.028, 0.8, 1.4, Ie=1.0, Cd=5.5, Om0=3.0, system="SMF"))\n')
    ps = PP.p695_factors(_run("Y"), s, nsp)
    assert ps["Omega"] == pytest.approx(3000.0 / 1368.0) and ps["Omega0_design"] == 3.0 and not ps["per_direction_basis"]


def test_chapter16_drift_limit_per_direction():
    from nlrha import acceptance as AC
    ch16 = json.load(open(os.path.join(ROOT, "nlrha", "ch16_params.json")))
    single = AC.drift_limits(ch16, 1188.0, [148.5] * 8, "I_II")
    mixed = AC.drift_limits(ch16, 1188.0, [148.5] * 8, "I_II", systems={"X": "SMF", "Y": "SCBF"})
    assert "by_dir" not in single and single["mean_limit"] == pytest.approx(0.04)
    assert mixed["by_dir"]["X"]["system"] == "SMF" and mixed["by_dir"]["Y"]["system"] == "SCBF"
    assert mixed["by_dir"]["Y"]["mean_limit"] == single["mean_limit"] and mixed["by_dir"]["X"]["table_row"] == "all other structures"
    assert AC._dir_limit(mixed, 1) == single["mean_limit"] and AC._dir_limit(single, 1) == single["mean_limit"]
    # evaluate(): the mixed building is screened per direction, with the system named
    from test_nlrha_fixes import _col_pkg, _rec, _ch16
    pkg = _col_pkg(); pkg.basis.by_dir = {"X": {"system": "SMF"}, "Y": {"system": "SCBF"}}
    recs = [_rec(i, drift=0.01) for i in range(11)]
    recs[0]["peak_story_drift"] = [[0.01, 0.07]]
    acc = AC.evaluate(recs, pkg, _ch16(), {7: 200.0}, dict(sum_D=1000.0, sum_Lexp=200.0, no_live_case_needed=False), 1.5, Ie=1.0, rc="I_II",
                      prm={"material": {"Fy_ksi": 50.0, "Ry_expected": 1.1}}, planned=[dict(record="R%d" % i) for i in range(11)], suite_size=11)
    assert acc["limits"]["by_dir"]["Y"]["system"] == "SCBF"
    assert acc["per_record"][0]["unacceptable"] and "(Y, SCBF)" in acc["per_record"][0]["flags"][0]


def test_ddm_phi_s_uses_the_direction_system_R():
    from steltic_ddm import phi_s
    mixed = dict(seis=dict(R=8, Cd=5.5, Om0=3.0), seis_X=dict(R=8, Cd=5.5, Om0=3.0), seis_Y=dict(R=3, Cd=3, Om0=3.0))
    assert phi_s.system_R(mixed, "X") == 8 and phi_s.system_R(mixed, "Y") == 3 and phi_s.system_R(mixed, None) == 8
    assert phi_s.choose("seismic", phi_s.system_R(mixed, "Y"), "x")["cls"] != "SEIS"     # R <= 3: elastic-force lateral class
    assert phi_s.choose("seismic", phi_s.system_R(mixed, "X"), "x")["cls"] == "SEIS"
    single = dict(seis=dict(R=8, Cd=5.5, Om0=3.0))
    assert phi_s.system_R(single, "Y") == 8
