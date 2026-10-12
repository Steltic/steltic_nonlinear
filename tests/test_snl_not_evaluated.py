"""snl run: an engine that refuses the package up front (system not supported) is NOT EVALUATED, not a failed step."""
import os
import sys

from snl import cli


def test_not_evaluated_parsed_from_engine_output():
    txt = ("some log\npushover NOT EVALUATED -- system 'SPSW' not supported: special plate shear wall (SPSW, "
           "AISC 341-22 F5): ... -- the analysis is NOT EVALUATED.\n")
    why = cli._not_evaluated(txt)
    assert why.startswith("system 'SPSW' not supported")
    assert cli._not_evaluated("Traceback (most recent call last):\nValueError: boom\n") is None
    assert cli._not_evaluated("DDM NOT EVALUATED -- system 'C-PSW/CF' not supported: x").startswith("system 'C-PSW/CF'")


def test_run_records_refusal_as_not_evaluated(tmp_path):
    log = tmp_path / "snl_run.log"
    log.write_text("earlier step output\nNLRHA NOT EVALUATED -- stale line from an earlier step\n")
    code = "import sys; print(\"NLRHA NOT EVALUATED -- system 'STMF' not supported: special truss moment frame\"); sys.exit(1)"
    r = cli._run([sys.executable, "-c", code], str(log))
    assert r["returncode"] == 1
    assert r["not_evaluated"].startswith("system 'STMF' not supported")
    ok = cli._run([sys.executable, "-c", "print('fine')"], str(log))
    assert ok["returncode"] == 0 and "not_evaluated" not in ok
    bad = cli._run([sys.executable, "-c", "import sys; print('Traceback: real failure'); sys.exit(2)"], str(log))
    assert bad["returncode"] == 2 and "not_evaluated" not in bad      # an earlier NOT EVALUATED line is not re-read
