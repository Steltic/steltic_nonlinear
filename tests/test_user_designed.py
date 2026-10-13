"""User-designed building (HR build-only mode): the confirmation checks, nl_basis.json, the `snl run` gate and the
report statement (pushover/user_designed.py, snl confirm)."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pushover import user_designed as UD          # noqa: E402

MODEL = """import openseespy.opensees as ops
ops.wipe()
ops.model('basic', '-ndm', 3, '-ndf', 6)
ops.node(1, 0, 0, 0)
ops.element('elasticBeamColumn', 1, 1, 2, 10.0, 29000.0, 11200.0, 1.0, 1.0, 1.0, 1)
ops.mass(99, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0)
ops.wipe()
ops.model('basic', '-ndm', 3, '-ndf', 6)
ops.element('elasticBeamColumn', 1, 1, 2, 10.0, 29000.0, 11200.0, 1.0, 1.0, 1.0, 1)
ops.element('elasticBeamColumn', 2, 2, 3, 10.0, 29000.0, 11200.0, 1.0, 1.0, 1.0, 3)
ops.element('Truss', 3, 1, 3, 5.0, 1)
ops.mass(199999, %(m)r, %(m)r, 0.0, 0.0, 0.0, 100.0)
"""


def make_job(td, W=1000.0, user=None, systems=("SMF", "SCBF")):
    j = Path(td) / "ud"
    (j / "design").mkdir(parents=True)
    (j / "cfg.py").write_text("cfg = dict(system='x')\n")
    sha = hashlib.sha256((j / "cfg.py").read_bytes()).hexdigest()
    (j / "model_opensees.py").write_text(MODEL % dict(m=W / 386.088))
    (j / "missing_items.json").write_text(json.dumps({"ready": True, "counts": {}}))
    (j / "provenance.json").write_text(json.dumps({
        "schema": "steltic.provenance", "cfg_sha256": sha, "description_sha256": "d" * 64,
        "members": {"elements": {"col": 1, "beam": 1, "brace": 1}},
        "accepted_defaults": [{"item": "loads.Lr_psf", "value": 20.0}], "derived": [{"what": "x", "rule": "y"}],
        "cfg": {"build_only": {"source": {"kind": "template", "file": "b.xlsx"}}}}))
    (j / "building_description.json").write_text(json.dumps({
        "source": {"kind": "template", "file": "b.xlsx"}, "seismic": {"system": {"X": systems[0], "Y": systems[1]}},
        "user_results": user}))
    (j / "design" / "pipeline_results.json").write_text(json.dumps({
        "cfg_sha256": sha, "checks": {"model_declared": True, "model_consistent": True},
        "summary": {"T": [1.0, 0.8, 0.3], "V": 100.0, "W": W}}))
    (j / "design" / "member_schedule.csv").write_text("ele_tag,member,section\n1,col,W14X90\n2,beam,W24X76\n3,brace,HSS6X6X1/2\n")
    return j


@pytest.fixture
def td():
    d = tempfile.mkdtemp(prefix="ud-test-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_normal_package_is_not_user_designed(td):
    assert UD.info(td) is None and not UD.is_user_designed(td)
    assert UD.banner_html(None) == ""


def test_checks_pass_and_confirm_writes_nl_basis(td):
    j = make_job(td)
    c = UD.checks(j)
    assert c["ok"], c["checks"]
    assert {x["id"]: x["status"] for x in c["checks"]}["user_results"] == "skipped"
    assert not UD.info(j)["confirmed"]
    r = UD.confirm(j, by="tester", note="checked the grid")
    assert r["confirmed"] and (j / "nl_basis.json").exists()
    inf = UD.info(j)
    assert inf["confirmed"] and inf["confirmed_by"] == "tester"
    t = UD.warning_text(inf, "the NLRHA")
    assert "USER-DESIGNED BUILDING" in t and "not checked by Steltic HR" in t and "loads.Lr_psf" in t and "checked the grid" in t
    assert "user-designed" in UD.banner_html(inf)


def test_user_results_differences_warn_but_do_not_block(td):
    j = make_job(td, W=1000.0, user={"W_kip": 1300.0, "T1_s": {"X": 1.02}, "V_kip": {"X": 100.0}})
    c = UD.checks(j)
    st = {x["id"]: x["status"] for x in c["checks"]}
    assert st["user_results"] == "warn" and c["ok"]
    assert any("W differs" in w for w in c["warnings"])
    r = UD.confirm(j)
    assert r["confirmed"]
    assert any("W differs" in w for w in UD.info(j)["warnings"])


def test_edited_cfg_fails_and_unconfirms(td):
    j = make_job(td)
    UD.confirm(j)
    (j / "cfg.py").write_text("cfg = dict(system='resized')\n")
    c = UD.checks(j)
    assert not c["ok"] and {x["id"]: x["status"] for x in c["checks"]}["built"] == "fail"
    inf = UD.info(j)
    assert not inf["confirmed"] and inf["stale"]
    assert not UD.confirm(j)["confirmed"]


def test_element_count_and_mass_mismatch_fail(td):
    j = make_job(td)
    pv = json.loads((j / "provenance.json").read_text())
    pv["members"]["elements"]["brace"] = 2
    (j / "provenance.json").write_text(json.dumps(pv))
    pr = json.loads((j / "design" / "pipeline_results.json").read_text())
    pr["summary"]["W"] = 2000.0
    (j / "design" / "pipeline_results.json").write_text(json.dumps(pr))
    st = {x["id"]: x["status"] for x in UD.checks(j)["checks"]}
    assert st["built"] == "fail" and st["mass"] == "fail"


def test_a_mechanism_fails(td):
    j = make_job(td)
    pr = json.loads((j / "design" / "pipeline_results.json").read_text())
    pr["summary"]["T"] = [6283185.3, 6.4, 1.9]; pr["failed"] = ["equil_X", "stability"]
    (j / "design" / "pipeline_results.json").write_text(json.dumps(pr))
    c = UD.checks(j)
    assert not c["ok"] and {x["id"]: x["status"] for x in c["checks"]}["stable"] == "fail"


def test_unsupported_system_fails(td):
    j = make_job(td, systems=("SMF", "SPSW"))
    st = {x["id"]: x["status"] for x in UD.checks(j)["checks"]}
    assert st["supported"] == "fail"


def test_snl_run_refuses_an_unconfirmed_package(td, capsys):
    j = make_job(td)
    (j / "report.html").write_text("<html></html>")
    from snl import cli
    rc = cli.main(["run", str(j), "--only", "pushover"])
    assert rc == 3
    assert "snl confirm" in capsys.readouterr().out
    rc = cli.main(["confirm", str(j), "--check"])
    assert rc == 0 and not (j / "nl_basis.json").exists()
    rc = cli.main(["confirm", str(j), "--by", "me"])
    assert rc == 0 and (j / "nl_basis.json").exists()
