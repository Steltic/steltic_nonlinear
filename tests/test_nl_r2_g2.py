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
