"""NL-R2-04 safeguard (user decision: warning only). A package that declares an existing structure (Ex32:
cfg['existing_structure'], cfg['materials']['existing_1968']) gets, from every engine, a console warning and, in every
report and the 16.1.4 document, the statement that it was assessed with NEW-building criteria (BPON, MCER / BSE-2N, one
material grade, no knowledge factor) and is NOT an ASCE 41 evaluation (BPOE, BSE-1E / BSE-2E). Unaffected packages are
unchanged. The analysis is never changed."""
import json, os, shutil, sys
from pathlib import Path
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")

from pushover import existing as EX  # noqa: E402

EX32_CFG = dict(system="OMF", existing_structure=dict(levels=[1, 2, 3], stories=[1, 2, 3], year=1968, grade="A36"),
                materials={"existing_1968": dict(spec="ASTM A36 (1968 drawings)", Fy=36.0), "new": dict(spec="ASTM A992", Fy=50.0)})
EX32_APPEND = ("\ncfg['existing_structure'] = dict(levels=[1, 2, 3], stories=[1, 2, 3], year=1968, grade='A36')\n"
               "cfg['materials'] = {'existing_1968': dict(spec='ASTM A36 (1968 drawings)', Fy=36.0)}\n")


def test_detection_and_statement():
    info = EX.existing_structure(cfg=EX32_CFG)
    assert info["declared"] and info["markers"] == ["cfg['existing_structure']", "cfg['materials']['existing_1968']"]
    assert info["summary"] == "storeys 1, 2, 3 · 1968 · A36"
    assert EX.existing_structure(cfg=dict(system="SMF", materials={"main": dict(spec="ASTM A992")})) is None
    assert EX.existing_structure(cfg={"materials": {"m": dict(spec="existing A36")}})["markers"] == ["cfg['materials']['m']"]
    assert EX.existing_structure(cfg=None) is None and EX.existing_structure(root=None) is None
    t = EX.warning_text(info, "the pushover")
    for s in ("NEW-BUILDING criteria", "BPON", "Table 2-5", "MCER / BSE-2N", "ONE material grade", "knowledge factor",
              "Table 6-1", "NOT an ASCE 41 evaluation", "BPOE", "Table 2-3", "BSE-1E", "BSE-2E", "unchanged"):
        assert s in t, s
    d = EX.warning_text(info, "the DDM", EX.DDM_CRITERIA)
    assert "AISC 360-22 / ASCE 7-22 new-building load combinations" in d and "BPON performance levels from" not in d
    assert EX.warning_text(None) == "" and EX.banner_html(None) == ""
    assert "existing-structure" in EX.banner_html(info) and "&gt;" not in EX.banner_html(info)


def test_detection_from_the_executed_cfg(tmp_path):
    (tmp_path / "cfg.py").write_text('"""existing_structure mentioned in a comment only"""\ncfg = dict(system="SMF")\n')
    assert EX.existing_structure(tmp_path) is None                      # the text is never searched
    (tmp_path / "cfg.py").write_text("cfg = dict(system='OMF')" + EX32_APPEND)
    assert EX.existing_structure(tmp_path)["declared"]


def test_print_warning(capsys):
    EX.print_warning(EX.existing_structure(cfg=EX32_CFG), "the NLRHA")
    assert capsys.readouterr().out.startswith("!! EXISTING STRUCTURE DECLARED")
    EX.print_warning(None)
    assert capsys.readouterr().out == ""


def _ex22(tmp_path, existing):
    job = tmp_path / "Ex22"
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "*.html", "__pycache__", "feedback"))
    shutil.copy(os.path.join(EX22, "report.html"), job)
    if existing:
        with open(job / "cfg.py", "a") as f:
            f.write(EX32_APPEND)
    return str(job)


@pytest.mark.parametrize("existing", [True, False])
def test_design_criteria_and_four_analyses(tmp_path, existing):
    from nlrha import design_criteria as DC
    from snl import compare
    from test_design_criteria import _docx_text
    job = _ex22(tmp_path, existing)
    docx, html = DC.write(job, out=str(tmp_path))
    x, h = _docx_text(docx), open(html, encoding="utf-8").read()
    compare.build(job)
    four = open(os.path.join(job, "four_analyses.html"), encoding="utf-8").read()
    summ = json.load(open(os.path.join(job, "snl_summary.json")))
    for t in (x, h, four):
        assert ("EXISTING STRUCTURE" in t) == existing
        assert ("NOT an ASCE 41 evaluation" in t) == existing
    assert ("existing_structure" in summ) == existing
    if existing:
        assert "BPOE (ASCE 41-23 Section 2.4.1, Table 2-3)" in x and "existing-structure" in h and "existing-structure" in four
        assert h.index("EXISTING STRUCTURE DECLARED") < h.index("1. Purpose")       # prominent: before section 1 text


def test_pushover_report_and_package(tmp_path):
    from pushover import report_supplement as RS
    from test_nl_pushover_fixes import _portal_results
    pkg, prm, table, stats, run, R = _portal_results()
    out = tmp_path / "po"
    (tmp_path / "cfg.py").write_text("cfg = dict(system='OMF')" + EX32_APPEND)
    pkg.root = tmp_path
    html = open(RS.write(str(out), pkg, prm, {"X": run}, {"X": R}, table, stats, 1.0), encoding="utf-8").read()
    po = json.load(open(out / "pushover_package.json"))
    assert "EXISTING STRUCTURE DECLARED" in html and html.index("EXISTING STRUCTURE") < html.index("1. Design basis")
    assert po["existing_structure"]["declared"] and "NOT an ASCE 41 evaluation" in po["existing_structure"]["statement"]
    (tmp_path / "cfg.py").write_text("cfg = dict(system='SMF')\n")
    html = open(RS.write(str(out), pkg, prm, {"X": run}, {"X": R}, table, stats, 1.0), encoding="utf-8").read()
    assert "EXISTING STRUCTURE" not in html and "existing_structure" not in json.load(open(out / "pushover_package.json"))


def test_ddm_report_and_nlrha_load_warn(tmp_path, capsys):
    """The DDM (report rebuild from the shipped ddm_results.json, no re-analysis) and the NLRHA loader print the warning;
    the DDM report carries the banner with the DDM's own (non-BPON) criteria."""
    import types
    from steltic_ddm import cli as DCLI
    from nlrha import cli as NCLI
    job = _ex22(tmp_path, True)
    DCLI.main(["report", job, "--no-block"])
    out = capsys.readouterr().out
    assert "!! EXISTING STRUCTURE DECLARED" in out and "the DDM (design by GMNIA)" in out
    rep = open(os.path.join(job, "ddm_report.html"), encoding="utf-8").read()
    assert "existing-structure" in rep and "new-building load combinations" in rep and "NOT an ASCE 41 evaluation" in rep
    NCLI._load(types.SimpleNamespace(package=job, params=None))
    assert "!! EXISTING STRUCTURE DECLARED" in capsys.readouterr().out
    plain = _ex22(tmp_path / "plain", False)
    NCLI._load(types.SimpleNamespace(package=plain, params=None))
    assert "EXISTING STRUCTURE" not in capsys.readouterr().out
