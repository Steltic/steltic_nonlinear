"""The design basis (W, V, T) of packages from the current HR engine (October 2026).

The current HR report states W as "Effective seismic weight W = 22,981 kip" and V either as "Design base shear V = C s W
= 0.044 × 21,000 = 924 kip in each direction" or, when the directions differ, "V X = ... ; V Y = ...", all with
thousands separators; its calc_package.json carries the per-direction design under framework_screen.seismic_by_direction
(V, Cs, T, Tu, R, Cd, Om0, rho, system), not at the top level. The older reader found none of these, so the pushover
stopped with "design basis incomplete (['W_kip'])". Older packages read exactly as before."""
import os, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from pushover import package_reader as PR  # noqa: E402
from snl import compare as C  # noqa: E402

EX24_CFG = '''
cfg = dict(
    system="mixed per direction (ASCE 7-22 12.2.2): SMF in X + SCBF in Y",
    system_X="SMF", system_Y="SCBF", brace_system="SCBF",
    seis=eng.seis(1.00, 0.58, 0.50, 8, 0.028, 0.8, 1.4, Ie=1.0, Cd=5.5, Om0=3.0, system="SMF"),
    seis_X=dict(R=8, Cd=5.5, Om0=3.0, Ct=0.028, x=0.8, system="SMF"),
    seis_Y=dict(R=6, Cd=5.0, Om0=2.0, Ct=0.02, x=0.75, system="SCBF"),
)
'''
SMF_CFG = 'cfg = dict(system="SMF", seis=eng.seis(1.0, 0.6, 0.6, 8, 0.028, 0.8, 1.4, Ie=1.0, Cd=5.5, Om0=3.0, system="SMF"))\n'

# what the current HR engine writes (Ex24 and Ex2 of the October 2026 test set)
FS_EX24 = {"X": {"V": 712.239, "Cs": 0.044, "T": 1.9363, "Tu": 1.9363, "Ta": 1.3919, "R": 8.0, "Cd": 5.5, "Om0": 3.0, "rho": 1.0, "Ie": 1.0,
                 "system": "SMF in X (Grids A and D, 6 RBS bays each)", "sfrs": "SMF", "basis": "envelope"},
           "Y": {"V": 1435.0312, "Cs": 0.0887, "T": 1.2824, "Tu": 1.0904, "Ta": 0.779, "R": 6.0, "Cd": 5.0, "Om0": 2.0, "rho": 1.3, "Ie": 1.0,
                 "system": "SCBF", "sfrs": "SCBF", "basis": "envelope"}}
FS_EX2 = {d: {"V": 924.0121, "Cs": 0.044, "T": 2.1969, "Tu": 1.8297, "Ta": 1.3069, "R": 8.0, "Cd": 5.5, "Om0": 3.0, "rho": 1.0, "Ie": 1.0,
              "system": "SMF", "sfrs": "SMF", "basis": "ELF"} for d in ("X", "Y")}
REP_EX2 = ("<p>Effective seismic weight W = 21,000 kip (§12.7.2: dead load, cladding and the partition allowance).</p>"
           "<p>Governing C <sub>s</sub> 0.044 0.044</p><p>Design base shear V = C <sub>s</sub> W = 0.044 × 21,000 = 924 kip in "
           "each direction (Eq. 12.8-1).</p><p>Wind base shear (frames): X = 502 kip, Y = 502.2 kip</p>")
REP_EX24 = ("<p>Effective seismic weight W = 16,187 kip (§12.7.2).</p><p>Design base shear (Eq. 12.8-1): V <sub>X</sub> = 0.044 × "
            "16,187 = 712.2 kip ; V <sub>Y</sub> = 0.08865 × 16,187 = 1,435 kip .</p>")


def _basis(tmp_path, cfg_text, calc, report):
    (tmp_path / "cfg.py").write_text("import engine3d as eng\n" + cfg_text)
    (tmp_path / "report.html").write_text(report)
    return PR.read_basis(tmp_path, calc)


def test_current_report_states_W_and_V(tmp_path):
    b = _basis(tmp_path, SMF_CFG, {"framework_screen": {"seismic_by_direction": FS_EX2}}, REP_EX2)
    assert (b.W_kip, b.V_design_kip) == (21000.0, 924.0)
    assert b.sources["W_kip"] == b.sources["V_design_kip"] == "report.html"
    assert b.T_design_s == pytest.approx(1.8297)                    # the period used, min(T model, Cu Ta) (§12.8.2)
    assert "framework_screen" in b.sources["T_design_s"]
    assert b.by_dir == {}                                           # one system both ways: the single set, as before


def test_per_direction_V_and_T_from_the_structured_design(tmp_path):
    b = _basis(tmp_path, EX24_CFG, {"framework_screen": {"seismic_by_direction": FS_EX24}}, REP_EX24)
    assert b.W_kip == 16187.0 and b.sources["W_kip"] == "report.html"
    assert b.V_design_kip == pytest.approx(1435.0312) and b.T_design_s == pytest.approx(1.9363)   # the larger direction
    x, y = PR.dir_basis(b, "X"), PR.dir_basis(b, "Y")
    assert (x["R"], x["Cd"], x["Om0"], x["V_design_kip"], x["T_design_s"]) == (8.0, 5.5, 3.0, 712.239, 1.9363)
    assert (y["R"], y["Cd"], y["Om0"], y["V_design_kip"], y["T_design_s"]) == (6.0, 5.0, 2.0, 1435.0312, 1.0904)
    assert x["per_direction"] and "framework_screen.seismic_by_direction.X" in x["source"]["V_design_kip"]
    assert b.by_dir["Y"]["rho"] == 1.3


def test_W_from_V_over_Cs_when_the_report_does_not_state_it(tmp_path):
    b = _basis(tmp_path, EX24_CFG, {"framework_screen": {"seismic_by_direction": FS_EX24}}, "<p>no basis text</p>")
    assert b.W_kip == pytest.approx((712.239 / 0.044 + 1435.0312 / 0.0887) / 2)
    assert "V / Cs" in b.sources["W_kip"]


def test_older_packages_read_as_before(tmp_path):
    # the August report text and a top-level seismic_by_direction keep precedence over the newer forms
    old_rep = "<p>R / Cd / Ω0 / Ie 8 / 5.5 / 3.0 / 1.0</p><p>Seismic weight W = 15426 kip</p><p>Base shear ΣF = 1368 kip</p>"
    sbd = {"X": {"system": "SMF", "R": 8, "Cd": 5.5, "Omega0": 3.0, "Tu_s": 1.83, "Cs": 0.044, "V_kip": 678.8, "W_kip": 15426.5},
           "Y": {"system": "SCBF", "R": 6, "Cd": 5.0, "Omega0": 2.0, "Tu_s": 1.09, "Cs": 0.0887, "V_kip": 1367.6, "W_kip": 15426.5}}
    b = _basis(tmp_path, EX24_CFG, {"seismic_by_direction": sbd, "framework_screen": {"seismic_by_direction": FS_EX24}}, old_rep)
    assert (b.W_kip, b.V_design_kip) == (15426.0, 1368.0)
    assert PR.dir_basis(b, "X")["V_design_kip"] == 678.8 and PR.dir_basis(b, "Y")["T_design_s"] == 1.09
    assert PR.dir_basis(b, "X")["source"]["V_design_kip"] == "calc_package.json seismic_by_direction.X"


def test_beam_bracing_text_stud_spacing_is_not_a_bracing_length():
    oct_txt = ("SMF beams: the slab is connected between the protected zones with 3/4 in headed studs at 12 in o.c. and the "
               "beams, columns and beam weights are within W36 / W36 / 302 lb/ft, so supplemental top and bottom flange "
               "bracing at the reduced section is not required (AISC 358-22 5.3.1(7) Exception); along the span the top "
               "flange is braced by the slab and the bottom flange by kickers at the spacing listed per beam (<= 0.086 ry "
               "E/(Ry Fy), AISC 341-22 D1.2b), with no attachment in the protected zones")
    assert PR._length_in(oct_txt) is None                                        # was 12.0 (the stud spacing)
    pkg = type("P", (), {})(); pkg.calc = {"capacity_design": {"beam_bracing": oct_txt}, "members": []}
    assert PR.beam_details(pkg)["Lb_frame"] is None
    assert PR._length_in("bottom flange braced by kickers at 8 ft o.c.; studs at 12 in o.c.") == 96.0
    assert PR._length_in("Lb = 90 in (quarter points)") == 90.0
    assert PR._length_in("braced at a spacing of 7 ft, welded kickers") == 84.0


def test_four_analyses_sheet_reads_the_current_report(tmp_path):
    (tmp_path / "report.html").write_text(REP_EX2)
    f = C.steltic_facts(str(tmp_path))
    assert (f["Cs"], f["W_kip"], f["V_kip"], f["wind_X"], f["wind_Y"]) == (0.044, 21000.0, 924.0, 502.0, 502.2)
    (tmp_path / "report.html").write_text(REP_EX24)
    f = C.steltic_facts(str(tmp_path))
    assert (f["Cs"], f["W_kip"], f["V_kip"]) == (0.08865, 16187.0, 1435.0)          # the larger direction
    (tmp_path / "report.html").write_text("<p>Design base shear V = C <sub>s</sub> W = 0.1385 × 1,898 = 262.8 kip in each direction</p>")
    assert C.steltic_facts(str(tmp_path))["V_kip"] == 262.8
