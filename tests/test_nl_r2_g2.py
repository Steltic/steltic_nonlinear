"""Regression tests for the round-2 findings NL-R2-05, 06, 08, 10 and NL-R2-L1 (package reading, the 16.1.4 criteria
document, the feedback loops, BRB data, mesh-convergence dry run). Small synthetic packages only -- no analysis."""
import json, os, sys, tempfile
from pathlib import Path
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# --------------------------------------------------------------------------- NL-R2-06: heights from cfg VALUES
_CFG_COMMENT = '''"""Two storeys.  heights = [14, 12] ft (inter-level offsets) -- a COMMENT, never to be read."""
H1 = 14 * 12.0
cfg = dict(system="SMF", heights=[H1, 144.0], L_floor=50.0)
'''
_MODEL = """import openseespy.opensees as ops
ops.wipe()
ops.model('basic', '-ndm', 3, '-ndf', 6)
ops.node(1, 0.0, 0.0, 0.0)
ops.node(100001, 0.0, 0.0, 168.0)
ops.node(200001, 0.0, 0.0, 312.0)
ops.node(199999, 0.0, 0.0, 168.0)
ops.node(299999, 0.0, 0.0, 312.0)
ops.fix(1, 1, 1, 1, 1, 1, 1)
ops.mass(199999, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0)
ops.mass(299999, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0)
ops.rigidDiaphragm(3, 199999, 100001)
ops.rigidDiaphragm(3, 299999, 200001)
"""


def _job(cfg_text=_CFG_COMMENT, model=_MODEL):
    d = Path(tempfile.mkdtemp(prefix="nlr2g2_"))
    (d / "model_opensees.py").write_text(model)
    if cfg_text is not None:
        (d / "cfg.py").write_text(cfg_text)
    return d


def _engine_ok():
    try:
        from steltic_ddm.ingest import load_cfg
        d = _job("cfg = dict(heights=[1.0])\n")
        return load_cfg(str(d)).get("heights") == [1.0]
    except Exception:
        return False


@pytest.mark.skipif(not _engine_ok(), reason="the HR engine is not importable (STELTIC_ENGINE_DIR)")
def test_heights_come_from_executed_cfg_not_the_docstring():
    from pushover import package_reader as PR
    p = PR.load(_job())
    assert p.basis.heights_in == [168.0, 144.0]                      # not [14, 12] from the docstring
    assert "executed" in p.basis.sources["heights_in"]
    assert p.basis.L_floor_psf == 50.0


def test_heights_fall_back_to_node_elevations(monkeypatch):
    from pushover import package_reader as PR
    import steltic_ddm.ingest as ING
    def boom(*a, **k):
        raise ImportError("engine not importable")
    monkeypatch.setattr(ING, "load_cfg", boom)
    p = PR.load(_job())
    assert p.basis.heights_in == [168.0, 144.0]
    assert "diaphragm master elevations" in p.basis.sources["heights_in"] and "not executable" in p.basis.sources["heights_in"]
    p2 = PR.load(_job(cfg_text=None))                                # no cfg.py at all
    assert p2.basis.heights_in == [168.0, 144.0] and "no cfg.py" in p2.basis.sources["heights_in"]


# --------------------------------------------------------------------------- NL-R2-08: HR drift table, no KeyError 'brief'
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")
_NEW_REPORT = """<html><body><h2>8. Drift</h2><p>allowable 1.00% of h<sub>sx</sub> = &Delta;<sub>a</sub>/&rho;</p>
<table><tr><th>Story</th><th>&delta;<sub>xe</sub>/h X %</th><th>&Delta;/h X %</th><th>&delta;<sub>xe</sub>/h Y %</th><th>&Delta;/h Y %</th>
<th>&le; limit (X 1.00% / Y 0.90%)</th></tr>
<tr><td>1</td><td>0.168</td><td>0.617</td><td>0.162</td><td>0.594</td><td>OK</td></tr>
<tr><td>2</td><td>0.300</td><td>1.950</td><td>0.290</td><td>1.900</td><td>exempt: split-level inter-diaphragm offset 14'->20' (office L2 to wo</td></tr>
<tr><td>3</td><td>0.246</td><td>0.901</td><td>0.238</td><td>0.873</td><td>OK</td></tr></table>
<p>Same analysis as the engine drift gate (ELF story forces).</p>
<p>Wind drift</p><table><tr><td>1</td><td>0.01</td><td>0.02</td><td>0.03</td><td>0.04</td><td>OK</td></tr>
<tr><td>4</td><td>0.01</td><td>0.02</td><td>0.03</td><td>9.99</td><td>OK</td></tr></table>
<p>Wind base shear (frame): X = 375 kip, Y = 591 kip (plus 34 / 54 kip delivered directly to the foundation)</p>
<p>Design base shear V = C s W = 0.0950 &times; 12,600 = 1,197 kip</p></body></html>"""


def _ex22_copy(report=None, drop=()):
    import shutil
    job = os.path.join(tempfile.mkdtemp(), "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__", "feedback", *drop))
    if report is not None:
        open(os.path.join(job, "report.html"), "w", encoding="utf-8").write(report)
    return job


def test_steltic_facts_reads_the_current_hr_drift_table_and_the_old_one():
    from snl import compare as C
    f = C.steltic_facts(_ex22_copy(_NEW_REPORT))
    assert f["drift_X"] == [0.617, 1.95, 0.901] and f["drift_Y"] == [0.594, 1.9, 0.873]       # the wind-drift rows are not read
    assert f["drift_exempt"] == [2] and f["drift_limit_X_pct"] == 1.0 and f["drift_limit_Y_pct"] == 0.9
    assert f["drift_limit_pct"] == 0.9                                                  # the tighter direction
    assert C.design_drift_max(f) == 0.901 and C.design_drift_max(f, "Y") == 0.873       # exempt storey 2 left out
    assert (f["wind_X"], f["wind_Y"], f["V_kip"]) == (375.0, 591.0, 1197.0)
    old = C.steltic_facts(EX22)                                                         # the pre-October format still parses
    assert old["drift_limit_pct"] == 1.0 and len(old["drift_X"]) == 6 and old["drift_exempt"] == [] and old["wind_X"]


def test_drift_plan_reads_the_new_table_and_rc_iv_reason_is_never_lost():
    from snl import feedback as F
    p = F.drift_plan(F.read_job(_ex22_copy(_NEW_REPORT)))                               # RC IV, numbers complete
    assert p["numbers"]["linear_drift"] == pytest.approx(0.00901) and p["numbers"]["linear_limit"] == pytest.approx(0.009)
    assert not p["eligible"] and any("Risk Category IV" in r for r in p["reasons"]) and p["brief"]
    nodrift = _NEW_REPORT.split("<table>")[0] + "</body></html>"                        # no drift table at all
    p = F.drift_plan(F.read_job(_ex22_copy(nodrift)))
    assert any("Risk Category IV" in r for r in p["reasons"]) and any("incomplete" in r for r in p["reasons"])
    assert "NOT ELIGIBLE" in p["brief"]


def test_every_ineligible_plan_has_a_brief_cli_and_loop_do_not_crash(capsys):
    from snl import feedback as F, cli as CLI, loop as L
    job = _ex22_copy(drop=("nlrha", "pushover", "ddm_results.json"))
    jd = F.read_job(job)
    for k in F.LOOPS:
        p = F.plan(jd, k)
        assert not p["eligible"] and p["brief"].startswith("=== SNL FEEDBACK LOOP: %s ===" % k) and "NOT ELIGIBLE" in p["brief"]
        assert L.apply_edits(p, {"brief_note": "x", "scwb_target": 1.3, "change_set": {"a": "W14X90"}}, jd) is p
    for k in F.LOOPS:
        assert CLI.main(["feedback", job, "--loop", k]) in (0, None)
        assert "NOT ELIGIBLE" in capsys.readouterr().out
    lp = L.Loop(job, "mechanism", verify="none", hr_url="http://127.0.0.1:9", base_building="Ex22_SMF")
    lp.start(); lp.join(60)
    assert lp.state["status"] == "failed" and "not eligible" in lp.state["error"] and "KeyError" not in lp.state["error"]
    assert "NOT ELIGIBLE" in open(os.path.join(lp.dir, "brief.txt")).read()


# --------------------------------------------------------------------------- NL-R2-10: BRB data in the HR engine's own fields
_HR_CFG = dict(brb=dict(Asc={"BRBX-44": 44.0, "BRBY-40": 40.0}, Fysc=44.0, Fysc_min=38.0, Ry=1.0, beta=1.10, omega=1.45, KF=1.4))
_HR_CALC = {"members": [{"id": "brace-BRB-X-g0", "inputs": {"kind": "brace", "section": "BRBX-44", "Asc_in2": 44.0}}],
            "capacity_design": {"BRB_adjusted_strengths": {"omega": 1.45, "beta": 1.1, "KF": 1.4, "by_group": {
                "X-g0": {"Asc_in2": 44.0, "label": "BRBX-44", "T_adj_kip": 2807.2, "C_adj_kip": 3087.9, "Fysc_ksi": [38.0, 44.0], "omega": 1.45, "beta": 1.1}}}}}


def test_brb_reads_hr_cfg_brb():
    from pushover import hinge_models as HM
    prm = HM.load_params(None)
    calc = {"members": _HR_CALC["members"]}                         # Ex23: members carry Asc only, no Fysc_ksi
    with pytest.raises(ValueError, match="Fysc unknown"):
        HM.brb_spec("BRBX-44", 387.0, prm, pkg_data=HM.brb_package_data(calc))          # genuinely absent -> still refused
    d = HM.brb_package_data(calc, _HR_CFG)
    s = HM.brb_spec("BRBY-40", 387.0, prm, pkg_data=d)              # a label only cfg['brb'] knows
    assert (s.Asc, s.Fysc_ksi, s.Ry, s.omega, s.beta) == (40.0, 44.0, 1.0, 1.45, 1.1)
    assert any("cfg['brb']['Fysc']" in f and "Fysc,max" in f and "38.0" in f for f in s.flags)
    assert not any("FALLBACK" in f for f in s.flags)
    assert abs(s.dT - s.Pye_kip * 387.0 / (29000.0 * 1.4 * 40.0)) < 1e-9          # Delta_y with cfg KF


def test_brb_reads_brb_adjusted_strengths_range_at_fysc_max():
    from pushover import hinge_models as HM
    d = HM.brb_package_data(_HR_CALC)                               # no cfg: the calc package's HR block alone
    s = HM.brb_spec("BRBX-44", 387.0, HM.load_params(None), pkg_data=d)
    assert (s.Asc, s.Fysc_ksi, s.omega, s.beta) == (44.0, 44.0, 1.45, 1.1) and abs(s.Ry - 1.0) < 1e-3
    assert abs(s.omega * s.Ry * s.Fysc_ksi * s.Asc - 2807.2) < 1.0                 # reproduces HR's T_adj
    assert any("[38.0, 44.0]" in f and "Fysc,max" in f for f in s.flags)


def test_brb_member_inputs_still_take_precedence():
    from pushover import hinge_models as HM
    calc = {"members": [{"id": "b", "inputs": {"kind": "brace", "section": "BRB-Asc10.5", "Asc_in2": 10.5, "Fysc_ksi": 42.0, "KF": 1.45}}]}
    d = HM.brb_package_data(calc, dict(brb=dict(Asc={"BRB-Asc10.5": 10.5}, Fysc=44.0, Ry=1.0, beta=1.1, omega=1.45)))
    s = HM.brb_spec("BRB-Asc10.5", 390.0, HM.load_params(None), pkg_data=d)
    assert s.Fysc_ksi == 42.0 and s.omega == 1.45 and any("members[b].inputs.Fysc_ksi" in f for f in s.flags)


# --------------------------------------------------------------------------- NL-R2-L1: mesh-converge --dry-run
def test_mesh_converge_dry_run_is_labelled_and_kept_apart(tmp_path, capsys):
    from snl import cli as CLI
    job = tmp_path / "job"; job.mkdir()
    assert CLI.main(["mesh-converge", str(job), "--analyses", "nsp", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "status= dry-run" in out and "status= converged" not in out and "synthetic metrics" in out
    assert not (job / "mesh_convergence").exists()                   # nothing under the real result folder
    d = job / "mesh_convergence_dryrun"
    names = sorted(p.name for p in d.iterdir())
    assert names and all("DRYRUN" in n for n in names), names         # no rung folders, no real-looking file names
    doc = json.loads((d / "mesh_convergence_DRYRUN_scorecard_nsp.json").read_text())
    assert doc["status"] == "dry-run" and doc["dry_run"] and doc["rehearsal_status"]
    summ = json.loads((d / "mesh_convergence_DRYRUN_summary.json").read_text())
    assert summ["dry_run"] and summ["analyses"]["nsp"]["ladder"]["status"] == "dry-run"
    assert "DRY RUN" in (d / "mesh_convergence_DRYRUN_summary.md").read_text()
