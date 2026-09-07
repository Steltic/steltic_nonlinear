"""SNL monorepo tests: packaging, shared viewer core, orchestrator plumbing, four-analyses sheet on the shipped examples.
Run from the repo root:  python -m pytest tests -q
(the engine tests in test_pushover.py / test_nlrha.py / test_ddm.py need openseespy; the DDM Ex18 test needs STELTIC_ENGINE_DIR)."""
import hashlib, json, os, shutil, sys, tempfile, zipfile
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")
EX18 = os.path.join(ROOT, "examples", "Ex18_R3")


def _md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def test_packages_import():
    import snl, pushover, nlrha, steltic_ddm, records  # noqa: F401
    from snl import cli, compare  # noqa: F401
    assert callable(cli.main) and callable(compare.build)


def test_viewer_core_identical_across_engines():
    """The three engines carry byte-identical copies of the shared viewer core (html + three.js)."""
    for fn in ("viewer_core.html", "viewer_core.py", os.path.join("vendor", "three.min.js")):
        sums = {_md5(os.path.join(ROOT, pkg, fn)) for pkg in ("pushover", "nlrha", "steltic_ddm")}
        assert len(sums) == 1, fn


def test_records_library_packaged():
    idx = os.path.join(ROOT, "records", "p695_farfield", "index.json")
    assert os.path.exists(idx)
    n = len(json.load(open(idx))["records"])
    assert n == 22


def test_unpack_zip_flattens_top_folder():
    from snl.cli import _unpack
    tmp = tempfile.mkdtemp()
    zp = os.path.join(tmp, "Job.zip")
    with zipfile.ZipFile(zp, "w") as z:
        for fn in ("report.html", "model_opensees.py", "model_static.py", "cfg.py"):
            z.write(os.path.join(EX22, fn), "Job/" + fn)
        z.write(os.path.join(EX22, "design", "calc_package.json"), "Job/design/calc_package.json")
    job = _unpack(zp, None)
    assert job == os.path.join(tmp, "Job")
    assert os.path.exists(os.path.join(job, "report.html")) and os.path.exists(os.path.join(job, "design", "calc_package.json"))
    assert _unpack(job, None) == job                       # a folder passes through
    out = os.path.join(tmp, "elsewhere")
    assert _unpack(zp, out) == out and os.path.exists(os.path.join(out, "cfg.py"))


def test_unpack_rejects_non_steltic_zip():
    from snl.cli import _unpack
    tmp = tempfile.mkdtemp(); zp = os.path.join(tmp, "x.zip")
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("readme.txt", "nope")
    with pytest.raises(SystemExit):
        _unpack(zp, None)


def test_steltic_facts_ex22():
    from snl import compare
    f = compare.steltic_facts(EX22)
    assert f["V_kip"] > 1000 and f["Cs"] > 0 and len(f["drift_X"]) >= 5 and len(f["drift_Y"]) >= 5
    assert f["dc_max"]["DC"] > 0 and f["drift_limit_pct"] > 0 and f["building"] == "Ex22_SMF"


@pytest.mark.parametrize("ex", [EX22, EX18])
def test_four_analyses_sheet(ex):
    """compare.build regenerates the sheet from the shipped outputs into a scratch copy of the example."""
    from snl import compare
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, os.path.basename(ex))
    shutil.copytree(ex, job, ignore=shutil.ignore_patterns("*.pkl", "*.html", "__pycache__"))
    # the sheet reads report.html for the Steltic drift/base-shear rows
    shutil.copy(os.path.join(ex, "report.html"), job)
    out = compare.build(job)
    html = os.path.join(job, "four_analyses.html"); summ = os.path.join(job, "snl_summary.json")
    assert os.path.exists(html) and os.path.exists(summ)
    s = json.load(open(summ))
    assert {"steltic", "pushover", "nlrha", "ddm"} <= set(s)
    assert s["pushover"]["Omega"]["X"] > 1 and s["ddm"]["lambda_u"] > 1 and s["nlrha"]["mean_drift_max"] > 0
    assert s["nlrha"]["n_records"] == 11 and s["pushover"]["bpon_ok"] is not None
    t = open(html, encoding="utf-8").read()
    assert "<svg" in t and "λ" in t and "snl_summary.json" in t and len(t) > 20_000
    assert os.path.abspath(out) == os.path.abspath(html)


def test_report_command_and_hub():
    """`snl report <job>` rebuilds the sheet and the viewer hub without running any analysis."""
    from snl import cli
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__"))
    os.remove(os.path.join(job, "four_analyses.html")); os.remove(os.path.join(job, "steltic_viewer_bundle.html"))
    assert cli.main(["report", job]) in (0, None)
    assert os.path.exists(os.path.join(job, "four_analyses.html"))
    hub = open(os.path.join(job, "steltic_viewer_bundle.html"), encoding="utf-8").read()
    assert "pushover/pushover_viewer_3d.html" in hub and "nlrha/nlrha_viewer_3d.html" in hub and "ddm_viewer_3d.html" in hub


def test_run_step_selection(monkeypatch):
    """`snl run --only/--skip` builds the right subprocess commands; the analyses themselves are stubbed."""
    from snl import cli
    calls = []

    def fake_run(cmd, log, env=None, cwd=None):
        calls.append(cmd); return dict(returncode=0, seconds=0, log=log)
    monkeypatch.setattr(cli, "_run", fake_run)
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__"))
    rc = cli.main(["run", job, "--skip", "nlrha", "--steltic-engine", tmp, "--risk-category", "IV", "--parallel", "3"])
    assert rc == 0
    mods = [c[2] for c in calls]
    assert mods == ["pushover", "steltic_ddm"]
    assert "--workers" in calls[1] and calls[1][calls[1].index("--workers") + 1] == "3" and "--risk-category" in calls[1]
    st = json.load(open(os.path.join(job, "snl_run.json")))
    assert set(st["steps"]) == {"pushover", "ddm"} and st["steps"]["pushover"]["returncode"] == 0
    # without an engine dir the DDM step is skipped, not failed
    calls.clear()
    monkeypatch.delenv("STELTIC_ENGINE_DIR", raising=False)
    rc = cli.main(["run", job, "--only", "ddm"])
    assert rc == 0 and calls == []
    st = json.load(open(os.path.join(job, "snl_run.json")))
    assert st["steps"]["ddm"]["returncode"] is None and "skipped" in st["steps"]["ddm"]
