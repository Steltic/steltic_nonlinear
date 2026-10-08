"""NLRHA run time and robustness (NL-R2-15 budget, NL-R2-18 record window / trimming, NL-R2-26 per-record resume).
No real record is analysed: the budget and the window are pure functions, the suite runner is driven with a mocked
record worker."""
import json, os, shutil, sys, tempfile
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")


# --------------------------------------------------------------------------- NL-R2-15 work budget
def test_record_budget_scales_with_record_and_model(monkeypatch):
    from nlrha import run as RN
    monkeypatch.delenv("SNL_NLRHA_RECORD_BUDGET_S", raising=False)
    # Ex18 Imperial Valley Delta: 96.5 s window at dt 0.01, 1,846 nodes (IMK model) -> never the old 1800 s; at least the 4 h floor
    s, basis = RN.record_wall_budget(None, 96.52, 0.01, 1846)
    assert s >= 4 * 3600 and s > 1800 and basis.startswith("auto")
    # a 10,000-node model on the same record gets more than the floor, and the dt/2 retry twice that
    s1, _ = RN.record_wall_budget("auto", 96.52, 0.01, 10000)
    s2, _ = RN.record_wall_budget("auto", 96.52, 0.005, 10000)
    assert s1 > 4 * 3600 and s2 == pytest.approx(2 * s1, rel=1e-3)
    # explicit values: fixed seconds, 0 = unlimited, env var when the option is absent
    assert RN.record_wall_budget(1800, 96.52, 0.01, 4046)[0] == 1800
    assert RN.record_wall_budget("0", 96.52, 0.01, 4046) == (0.0, "unlimited")
    monkeypatch.setenv("SNL_NLRHA_RECORD_BUDGET_S", "900")
    assert RN.record_wall_budget(None, 96.52, 0.01, 1846)[0] == 900


def test_record_budget_cli_and_snl_passthrough(monkeypatch):
    from nlrha import cli as NC
    assert NC._wall_budget_arg(None) is None and NC._wall_budget_arg("auto") == "auto" and NC._wall_budget_arg("7200") == 7200.0
    with pytest.raises(SystemExit):
        NC._wall_budget_arg("lots")
    from snl import cli
    calls = []
    monkeypatch.setattr(cli, "_run", lambda cmd, log, env=None, cwd=None: calls.append(cmd) or dict(returncode=0, seconds=0, log=log))
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__"))
    assert cli.main(["run", job, "--only", "nlrha", "--record-budget-s", "14400", "--no-criteria"]) == 0
    c = calls[-1]
    assert c[2] == "nlrha" and c[c.index("--record-budget-s") + 1] == "14400"
    calls.clear()
    assert cli.main(["run", job, "--only", "nlrha", "--no-criteria"]) == 0
    assert "--record-budget-s" not in calls[-1]                     # nlrha's own default (auto) applies
