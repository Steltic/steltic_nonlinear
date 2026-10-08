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


# --------------------------------------------------------------------------- NL-R2-18 record window / optional trimming
def _synthetic_pair(dt=0.01, seed=1, tail_wave=0.0):
    """quiet head (12 s of noise at 1 % amplitude), 15 s strong motion, quiet tail (10 s); optionally a 4-s wave in
    the tail that carries little Arias intensity but drives a 4-s oscillator."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, 37.0, dt); n = len(t)
    env = np.where(t < 12, 0.01, np.where(t < 15, 0.01 + 0.99 * (t - 12) / 3, np.where(t < 27, 1.0, np.maximum(0.01, np.exp(-(t - 27) / 1.5)))))
    a1 = 0.3 * env * rng.standard_normal(n); a2 = 0.25 * env * rng.standard_normal(n)
    if tail_wave:
        m = t >= 27.5
        a1[m] += tail_wave * np.sin(2 * np.pi * (t[m] - 27.5) / 4.0); a2[m] += tail_wave * np.sin(2 * np.pi * (t[m] - 27.5) / 4.0)
    return a1, a2, dt


def test_default_window_is_unchanged_and_checked():
    from nlrha import run as RN
    a1, a2, dt = _synthetic_pair()
    P = np.geomspace(0.2, 2.0, 10)
    w = RN.record_window(a1, a2, dt, 5.0, periods=P)
    t0, tsig = RN.arias_window(a1, a2, dt)
    assert w["mode"] == "off" and not w["trimmed"] and w["t_start"] == 0.0 and w["t_offset"] == 0.0
    assert w["t_sig"] == pytest.approx(tsig) and w["t_end_rel"] == pytest.approx(tsig + 5.0)       # the pre-NL-R2-18 window
    assert w["check"]["ok"] and w["check"]["max_change"] <= RN.SPECTRAL_TOL and w["check"]["n_widened"] == 0
    b1, b2 = RN.analysed_motion(a1, a2, dt, w)
    assert b1 is not None and np.array_equal(b1, a1)                                    # the record itself is applied
    # without a period range the window is still the old one, and says it was not checked
    w0 = RN.record_window(a1, a2, dt, 5.0)
    assert w0["check"]["ok"] is None and "not checked" in w0["check"]["note"] and w0["t_end_rel"] == pytest.approx(tsig + 5.0)


def test_standard_trim_skips_quiet_head_at_zero_crossing_with_taper():
    from nlrha import run as RN
    a1, a2, dt = _synthetic_pair()
    P = np.geomspace(0.2, 2.0, 10)
    w = RN.record_window(a1, a2, dt, 5.0, periods=P, mode="standard")
    off = RN.record_window(a1, a2, dt, 5.0, periods=P)
    assert w["trimmed"] and 8.0 < w["t_start"] < 13.0 and w["t_offset"] == w["t_start"]
    assert w["t_end_rel"] < off["t_end_rel"] - 5.0                                      # >= 5 s of quiet head saved
    assert w["check"]["ok"] and w["check"]["max_change"] <= 0.02
    strong = a1 if np.sum(a1 ** 2) >= np.sum(a2 ** 2) else a2
    i = w["i_start"]; assert strong[i] * strong[i + 1] <= 0                              # starts at a zero crossing
    b1, b2 = RN.analysed_motion(a1, a2, dt, w)
    assert len(b1) == w["i_end"] - w["i_start"] and b1[0] == 0.0 and abs(b1[10]) < abs(a1[w["i_start"] + 10]) + 1e-12   # cosine taper
    nt = int(round(RN.TAPER_S / dt)); assert np.allclose(b1[nt:len(b1) - nt], a1[w["i_start"] + nt:w["i_end"] - nt])  # untouched inside
    # head only / tail only
    wh = RN.record_window(a1, a2, dt, 5.0, periods=P, mode="standard", ends="head")
    assert wh["t_start"] == w["t_start"] and not wh["taper_tail"] and wh["t_sig"] == pytest.approx(off["t_sig"])
    wt = RN.record_window(a1, a2, dt, 5.0, periods=P, mode="standard", ends="tail")
    assert wt["t_start"] == 0.0 and not wt["taper_head"]
    with pytest.raises(ValueError):
        RN.record_window(a1, a2, dt, 5.0, periods=P, mode="brutal")


def test_spectral_check_widens_a_cut_that_changes_the_long_period_spectrum():
    """A late 4-s wave carries < 1 % of the Arias intensity: the aggressive 99 % cut drops it and Sa(4 s) falls far more
    than 2 % -- the check must catch it and widen the window back until the spectra agree (the finding's 4-s case)."""
    from nlrha import run as RN
    a1, a2, dt = _synthetic_pair(tail_wave=0.02)
    P = np.geomspace(0.4, 4.0, 10)
    w = RN.record_window(a1, a2, dt, 5.0, periods=P, mode="aggressive")
    c = w["check"]
    assert c["steps"][0]["max_change"] > 0.02 and c["n_widened"] >= 1
    assert c["ok"] and c["max_change"] <= 0.02 and w["t_sig"] > c["steps"][0]["t_sig"]
    # the same record over a short-period range needs no widening (the wave does not matter there)
    w2 = RN.record_window(a1, a2, dt, 5.0, periods=np.geomspace(0.1, 0.5, 8), mode="aggressive")
    assert w2["check"]["ok"] and w2["t_sig"] < w["t_sig"]


def test_pair_spectra_matches_rotd100():
    from nlrha import ground_motions as GM
    a1, a2, dt = _synthetic_pair()
    P = np.geomspace(0.2, 3.0, 6)
    s1, s2, rd = GM.pair_spectra(a1, a2, dt, P)
    assert np.allclose(rd, GM.rotd100(a1, a2, dt, P), rtol=1e-10) and np.allclose(s1, GM.sdof_peak(a1, dt, P))


def test_attach_windows_reports_and_discloses():
    from nlrha import cli as NC, report as RP
    a1, a2, dt = _synthetic_pair()
    P = np.geomspace(0.1, 3.0, 20)
    chosen = [dict(id="R1", earthquake="Synth", station="A", a1=a1, a2=a2, dt=dt), dict(id="R2", earthquake="Synth", station="B", a1=a2, a2=a1, dt=dt)]
    gm = dict(periods=P.tolist(), T_lower=0.2, T_upper=2.0, selected=[dict(id="R1"), dict(id="R2")])
    tot = NC._attach_windows(gm, chosen, 5.0, "standard", "both")
    assert gm["record_window"] is tot and tot["mode"] == "standard" and tot["n_trimmed"] == 2 and tot["saved_s"] > 10
    assert chosen[0]["window"] is gm["selected"][0]["window"] and chosen[0]["window"]["saved_s"] > 5
    cell = RP.window_cell(gm["selected"][0]["window"]); txt = RP.window_text(tot)
    assert "trimmed (standard" in cell and "ok" in cell
    assert "STANDARD" in txt and "16.1.4" in txt and "2 %" in txt and "saved" in txt
    tot0 = NC._attach_windows(gm, chosen, 5.0, "off", "both")
    assert tot0["saved_s"] == 0 and not chosen[0]["window"]["trimmed"] and "no trimming" in RP.window_text(tot0)
    assert "predate" in RP.window_cell(None)


def test_trim_options_passed_by_snl_run(monkeypatch):
    from snl import cli
    calls = []
    monkeypatch.setattr(cli, "_run", lambda cmd, log, env=None, cwd=None: calls.append(cmd) or dict(returncode=0, seconds=0, log=log))
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__"))
    assert cli.main(["run", job, "--only", "nlrha", "--trim-records", "--no-criteria"]) == 0
    c = calls[-1]; assert c[c.index("--trim-records") + 1] == "standard" and c[c.index("--trim-ends") + 1] == "both"
    assert cli.main(["run", job, "--only", "nlrha", "--trim-records", "aggressive", "--trim-ends", "head", "--no-criteria"]) == 0
    c = calls[-1]; assert c[c.index("--trim-records") + 1] == "aggressive" and c[c.index("--trim-ends") + 1] == "head"
    assert cli.main(["run", job, "--only", "nlrha", "--no-criteria"]) == 0 and "--trim-records" not in calls[-1]     # off by default
