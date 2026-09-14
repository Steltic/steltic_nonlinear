"""The 16.1.4 design criteria document: written from the package, valid docx + html, reflects what exists."""
import json, os, shutil, sys, tempfile, zipfile
import xml.dom.minidom as minidom
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

from nlrha import design_criteria as DC, docx_writer as DW  # noqa: E402


def _docx_text(path):
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        for name in ("word/document.xml", "word/styles.xml", "word/numbering.xml", "word/footer1.xml", "[Content_Types].xml"):
            minidom.parseString(z.read(name))
        return z.read("word/document.xml").decode("utf-8")


def test_docx_writer_minimal():
    tmp = tempfile.mkdtemp(); p = os.path.join(tmp, "t.docx")
    d = DW.Doc(title="T & <x>", subtitle="s"); d.heading("H", 1); d.para("a\nb"); d.bullet("item", bold_lead="Lead --"); d.table([["h1", "h2"], ["1", "2 & 3"]]); d.note("n"); d.code("x = 1"); d.page_break()
    d.save(p)
    x = _docx_text(p)
    assert "T &amp; &lt;x&gt;" in x and "<w:br/>" in x and "ListBullet" in x and "2 &amp; 3" in x and 'w:type="page"' in x


def test_design_criteria_from_ex22():
    tmp = tempfile.mkdtemp()
    docx, html = DC.write(EX22, out=tmp, project="Ex22 SMF office", engineer="EOR", reviewer="Reviewer")
    x = _docx_text(docx); h = open(html, encoding="utf-8").read()
    for s in ("16.1.4", "Ex22 SMF office", "Risk Category IV", "AISC 342 Table C5.5", "16.4.1.2", "0.010", "Puente" if False else "16.5", "not applicable (Risk Category IV keeps the 12.12.1 limits)"):
        assert s in x, s
    assert "&lt;= 2.0%" in x or "2.0% of h_sx" in x                        # RC IV: 2 x 0.010
    assert "x0.80" in x and "C5.4a.1.a.1" in x                             # the panel-zone modifier is documented
    assert "Selected suite" in x and "FEMA P-695" in x                     # the suite of the shipped nlrha_package
    assert "Results on file" in x and "ACCEPTABLE" in x
    assert "No retrieval_log.md" in x
    assert "<h2>" in h and "Design criteria" in h and "draft" in h.lower()


def test_design_criteria_reflects_site_hazard_relief_and_retrieval_log():
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__", "feedback", "nlrha"))
    os.makedirs(os.path.join(job, "nlrha"))
    from nlrha import site_hazard as SH
    hz = SH.build_site_hazard(34.05, -118.25, 0.97, 1.12, design=json.load(open(os.path.join(FIX, "usgs_design_la.json"))), deaggs=json.load(open(os.path.join(FIX, "usgs_disagg_la.json"))), fetch=False, conditioning_periods=[1.0])
    json.dump(hz, open(os.path.join(job, "nlrha", "site_hazard.json"), "w"))
    open(os.path.join(job, "retrieval_log.md"), "w").write("# retrieval log\n- plan: exact_section 16.2.2 -> found\n")
    cfg = open(os.path.join(job, "cfg.py")).read().replace("Ie=1.5", "Ie=1.25")
    cfg += "\ncfg['drift_relief_16_1_2'] = {\"clause\": \"ASCE 7-22 16.1.2\", \"nlrha_job\": \"Ex22\", \"nlrha_mean_drift\": 0.0146, \"nlrha_limit\": 0.03, \"linear_target\": 0.0168}\n"
    open(os.path.join(job, "cfg.py"), "w").write(cfg)
    docx, html = DC.write(job, out=tmp)
    x = _docx_text(docx)
    assert "USGS" in x and "Puente Hills" in x and "NEAR-FAULT" in x and "DIFFER from the package" in x
    assert "IN FORCE: linear target 0.0168" in x and "Risk Category III" in x
    assert "exact_section 16.2.2" in x and "The suite has not been selected yet" in x
    assert "[No retrieval_log.md" not in x
