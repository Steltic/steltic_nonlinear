"""NL-R2-06 (remainder): every cfg.py value the readers use comes from the EXECUTED cfg, never from the text.

Decoys taken from the real packages: Ex12 "S1 = 1.5*SD1/Fv" in a comment (S1 read as 1.5), Ex18 `wind_drift_limit=1.0 / 400`
(drift_limit read as 1.0), Ex27 an "SD1 = 2.0" comment, Ex33 seis_X R read as the building R, Ex13 `L_by_level=L_BY_LEVEL`
(a name, unreadable by regex), "RS" anywhere in the text read as an MRSA design, and "Risk Category IV" in prose.
Fallback (same policy as 4e64b9f): cfg.py absent / not executable -> the next independent package source, never the text."""
import os, sys, types
from pathlib import Path
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pushover import package_reader as PR, cfg_values as CV  # noqa: E402

DECOY_CFG = '''"""Decoys. S1 = 1.5*SD1/Fv was considered; the neighbour is Risk Category IV; SD1 = 2.0 in the old brief;
drift_limit = 1.0 % was the wind criterion; TL = 4 s per the old map; RS (response spectrum) NOT used here.
heights = [14, 12] ft."""
import engine3d as eng
L_BY_LEVEL = {1: 65.0, 2: 125.0}
cfg = dict(
    system="SMF", risk_category="II",
    seis=eng.seis(1.05, 0.58, 0.62, 8, 0.028, 0.8, 1.4, Ie=1.0, Cd=5.5, Om0=3.0, system="SMF"),
    seis_X=dict(R=3.25, Cd=3.25, Om0=2.0, system="OCBF"),
    wind_drift_limit=1.0 / 400.0,
    drift_limit=0.020, rho=1.3, analyses=["ELF"],
    L_floor=50.0, Lr=25.0, L_by_level=L_BY_LEVEL, snow=30.0,
    heights=[168.0, 144.0],
)
cfg["seis"]["TL"] = 12.0
'''
REPORT = "<p>S1 = 0.40</p><p>R / Cd / Ω0 / Ie 8 / 5.5 / 3.0 / 1.5</p><p>Seismic weight W = 1000 kip</p>"


def _job(tmp_path, cfg_text=DECOY_CFG, report=REPORT):
    if cfg_text is not None:
        (tmp_path / "cfg.py").write_text(cfg_text)
    (tmp_path / "report.html").write_text(report)
    return tmp_path


def _pkg(job):
    b = PR.read_basis(job, {})
    return types.SimpleNamespace(root=Path(job), basis=b, calc={})


def test_basis_and_per_direction_from_executed_values(tmp_path):
    job = _job(tmp_path)
    b = PR.read_basis(job, {})
    assert (b.SDS, b.SD1, b.S1, b.R, b.Cd, b.Om0, b.Ie) == (1.05, 0.58, 0.62, 8.0, 5.5, 3.0, 1.0)   # not S1 1.5 / SD1 2.0
    assert b.system == "SMF" and b.L_floor_psf == 50.0 and b.heights_in == [168.0, 144.0]
    assert b.sources["S1"] == CV.SOURCE and b.sources["R"] == CV.SOURCE and "cfg" not in b.sources
    assert PR.dir_basis(b, "X")["R"] == 3.25 and PR.dir_basis(b, "Y")["R"] == 8.0           # seis_X only for X


def test_nlrha_readers_from_executed_values(tmp_path):
    from nlrha import gravity as GR, acceptance as AC
    from nlrha.cli import cfg_TL
    pkg = _pkg(_job(tmp_path))
    L = GR.cfg_loads(pkg)
    assert (L["L_floor"], L["Lr"], L["snow"], L["L_by_level"]) == (50.0, 25.0, 30.0, {1: 65.0, 2: 125.0})
    assert AC.risk_category(pkg) == "I_II"                                    # cfg 'II', not the 'IV' of the prose
    assert cfg_TL(pkg) == 12.0                                                # seis TL, not the "TL = 4" comment


def test_snl_readers_from_executed_values(tmp_path):
    from snl import feedback as FB, review as RV
    job = str(_job(tmp_path))
    v = FB.cfg_values(job)
    assert v == dict(drift_limit=0.02, rho=1.3, Ie=1.0, Cd=5.5, R=8.0, SDS=1.05, SD1=0.58, system="SMF", has_relief=False,
                     risk_category="II")
    r = RV.cfg_seismic(job)
    assert r["S1"] == 0.62 and r["drift_limit"] == 0.02 and r["risk_category"] == "II" and r["system"] == "SMF"
    (tmp_path / "cfg.py").write_text(DECOY_CFG + "cfg['drift_relief_16_1_2'] = {'linear_target': 0.0168}\n")
    assert FB.cfg_values(job)["has_relief"] and RV.cfg_seismic(job)["drift_relief_16_1_2"] == {"linear_target": 0.0168}


def test_not_executable_cfg_falls_back_to_other_sources_never_the_text(tmp_path):
    from nlrha import gravity as GR, acceptance as AC
    from nlrha.cli import cfg_TL
    from snl import feedback as FB
    bad = DECOY_CFG.replace("import engine3d as eng", "import engine3d as eng\nraise RuntimeError('broken cfg')")
    job = _job(tmp_path, bad)
    b = PR.read_basis(job, {})
    assert b.S1 == 0.40 and b.sources["S1"] == "report.html"                 # report.html, not the comment's 1.5
    assert (b.R, b.Ie) == (8.0, 1.5) and b.SD1 is None and b.L_floor_psf is None
    assert "not executable" in b.sources["cfg"] and "broken cfg" in b.sources["cfg"]
    pkg = types.SimpleNamespace(root=Path(job), basis=b, calc={})
    assert AC.risk_category(pkg) == "IV"                                      # from Ie = 1.5, not the prose
    assert cfg_TL(pkg) == 8.0
    L = GR.cfg_loads(pkg)
    assert L["L_floor"] is None and L["L_by_level"] == {} and "not executable" in L["sources"]["cfg"]
    assert FB.cfg_values(str(job)) == {"has_relief": False}


def test_no_cfg_and_cache_follows_edits(tmp_path):
    assert CV.executed_cfg(tmp_path) == (None, "no cfg.py")
    (tmp_path / "cfg.py").write_text("cfg = dict(rho=1.0)\n")
    assert CV.executed_cfg(tmp_path)[0]["rho"] == 1.0
    (tmp_path / "cfg.py").write_text("cfg = dict(rho=1.3)\n")                # a feedback loop edits cfg.py
    assert CV.executed_cfg(tmp_path)[0]["rho"] == 1.3
    assert CV.seis_value({"seis": {"R": 6}, "R": 8}, "R") == 6 and CV.seis_value({"R": 8}, "R") == 8
    assert CV.number(True) is None and CV.number("0.5") == 0.5 and CV.num_map({1: "x"}) == {}
