"""The loop runner against the fake HR Steel server: plan -> candidate -> re-design -> package checks -> comparison -> promotion.
Verification analyses are switched off (verify=none) so the suite stays fast; compare() is exercised on canned outputs."""
import json, os, shutil, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")

import fake_hr as FH  # noqa: E402
from snl import feedback as F, hr_client as HR, loop as L  # noqa: E402


def _job(rc3=False):
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__", "feedback"))
    if rc3:
        nl = json.load(open(os.path.join(job, "nlrha", "nlrha_package.json")))
        nl["limits"].update(risk_category="III", mean_limit=0.03, table_12_12_1=0.015)
        json.dump(nl, open(os.path.join(job, "nlrha", "nlrha_package.json"), "w"))
        cfg = open(os.path.join(job, "cfg.py")).read().replace("Ie=1.5", "Ie=1.25")
        open(os.path.join(job, "cfg.py"), "w").write(cfg)
    return job


def _run(job, kind, hr, **kw):
    ev = []
    lp = L.Loop(job, kind, verify="none", hr_url=hr.url, base_building="Ex22_SMF", on_event=ev.append, **kw)
    lp.start(); lp.join(180)
    assert not lp.is_alive()
    return lp, ev


def test_hr_client_roundtrip():
    hr = FH.FakeHR()
    try:
        assert HR.healthy(hr.url) and not HR.healthy("http://127.0.0.1:9")
        assert HR.log(hr.url, "Ex22_SMF")["resumable"]
        z = HR.download(hr.url, "Ex22_SMF"); assert len(z) > 10000
        r = HR.restore(hr.url, "Ex22_SMF__x", z, archive=True); assert r["ok"] and r["restored"] > 5 and r["archived"] is None
        r = HR.restore(hr.url, "Ex22_SMF__x", z, archive=True); assert r["archived"].startswith("Ex22_SMF__x__")
        seen = []
        out = HR.run(hr.url, "Ex22_SMF__x", "=== SNL FEEDBACK LOOP: mechanism ===\nlevel 2 (z = 360 in", on_event=seen.append)
        assert out["outcome"] == "done" and any(e["type"] == "tool" for e in seen) and any(e["type"] == "token" for e in seen)
        try:
            HR.download(hr.url, "nope"); assert False
        except HR.HRError as e:
            assert "404" in str(e)
    finally:
        hr.close()


def test_mechanism_loop_end_to_end():
    job = _job(); hr = FH.FakeHR()
    try:
        lp, ev = _run(job, "mechanism", hr)
        st = lp.state
        assert st["status"] == "verified" and st["passed"] is True, st.get("error")
        assert [s["name"] for s in st["steps"]] == ["plan", "copy", "hr", "fetch", "check", "compare"]
        assert all(s["status"] == "done" for s in st["steps"])
        assert st["candidate"] == "Ex22_SMF__mechanism" and "Ex22_SMF__mechanism" in hr.jobs
        assert hr.briefs["Ex22_SMF__mechanism"].startswith("=== SNL FEEDBACK LOOP: mechanism ===")
        items = {c["text"].split(" (")[0]: c["ok"] for c in st["comparison"]["checks"]["items"]}
        assert items["capacity_design.panel_zone.by_joint recorded"] and items["V_pz/V_ye within 0.6-0.9 at every joint group"]
        d = os.path.join(job, "feedback", lp.id)
        for f in ("state.json", "plan.json", "brief.txt", "base.zip", "candidate.zip", "hr_transcript.txt", "candidate/design/calc_package.json"):
            assert os.path.exists(os.path.join(d, f)), f
        assert L.list_loops(job)[0]["id"] == lp.id and L.load_state(job, lp.id)["status"] == "verified"
        assert any(e["type"] == "hr_text" for e in ev) and any(e["type"] == "status" and e["status"] == "verified" for e in ev)
    finally:
        hr.close()


def test_resize_loop_checks_the_change_set_by_element_tag():
    job = _job(); hr = FH.FakeHR()
    try:
        lp, _ = _run(job, "resize", hr)
        st = lp.state
        assert st["status"] == "verified", st.get("error")
        texts = [c["text"] for c in st["comparison"]["checks"]["items"]]
        assert any("roof-W24X76 levels [6]: asked W24X84 -> got W24X84" in t for t in texts)
        assert any("gravity_col-W14X90 levels [5, 6]: asked W14X82 -> got W14X82" in t for t in texts)
        ch = [r for r in st["comparison"]["groups"] if r["changed"]]
        assert {(r["role"], r["level"]) for r in ch} >= {("roof", 6), ("gravity_col", 5), ("gravity_col", 6)}
        assert abs(st["comparison"]["tons"]["delta"]) < 5
    finally:
        hr.close()


def test_resize_loop_with_user_edits():
    job = _job(); hr = FH.FakeHR()
    try:
        edits = {"change_set": {"roof-W24X76": None, "gravity_col-W14X90": "W14X68", "floor-W27X94": "W27X102"}}
        lp, _ = _run(job, "resize", hr, edits=edits)
        st = lp.state
        ids = {c["id"]: c["proposed"] for c in st["plan"]["change_set"]}
        assert "roof-W24X76" not in ids and ids["gravity_col-W14X90"] == "W14X68" and ids["floor-W27X94"] == "W27X102"
        assert st["status"] == "verified", [c["text"] for c in st["comparison"]["checks"]["items"]]
    finally:
        hr.close()


def test_drift_loop_and_promotion():
    job = _job(rc3=True); hr = FH.FakeHR(base_zip=FH.make_base_zip(src=job))
    try:
        lp, _ = _run(job, "drift", hr)
        st = lp.state
        assert st["status"] == "verified", (st.get("error"), [c["text"] for c in (st.get("comparison") or {}).get("checks", {}).get("items", [])])
        rel = st["plan"]["relief"]
        cand_cfg = open(os.path.join(job, "feedback", lp.id, "candidate", "cfg.py")).read()
        assert "drift_relief_16_1_2" in cand_cfg and ("drift_limit=%.4f" % rel["linear_target"]) in cand_cfg
        c = st["comparison"]
        assert c["drift"]["candidate_limit"] > c["drift"]["base_limit"] and c["tons"]["delta"] < 0
        # promotion: HR Steel base job replaced (previous archived), hub project archived + replaced, marker written
        lines = []
        st2 = L.promote(job, lp.id, hr.url, base_building="Ex22_SMF", on_log=lines.append)
        assert st2["status"] == "promoted" and st2["archived"]["hr"].startswith("Ex22_SMF__") and hr.archived
        assert b"drift_relief_16_1_2" in FH._read(hr.jobs["Ex22_SMF"])["cfg.py"]
        assert "drift_relief_16_1_2" in open(os.path.join(job, "cfg.py")).read()
        dor = json.load(open(os.path.join(job, "design", "design_of_record.json")))
        assert dor["loop"] == lp.id and dor["passed"] and "16.4" not in dor["supersedes"]
        arch = os.listdir(os.path.join(job, "archive"))
        assert len(arch) == 1 and os.path.exists(os.path.join(job, "archive", arch[0], "cfg.py")) and os.path.exists(os.path.join(job, "archive", arch[0], "nlrha", "nlrha_package.json"))
        assert not os.path.exists(os.path.join(job, "nlrha", "nlrha_package.json"))       # the promoted design has no Chapter 16 result yet (verify=none)
        assert L.load_state(job, lp.id)["status"] == "promoted"
    finally:
        hr.close()


def test_loop_reports_hr_pause_and_errors():
    job = _job(); hr = FH.FakeHR(fail="paused")
    try:
        lp, _ = _run(job, "mechanism", hr)
        assert lp.state["status"] == "hr_paused" and "NOT PERMITTED" in lp.state["error"]
        assert lp.state["steps"][2]["name"] == "hr" and lp.state["steps"][2]["status"] == "failed"
    finally:
        hr.close()
    job = _job(); hr = FH.FakeHR(fail="error")
    try:
        lp, _ = _run(job, "mechanism", hr)
        assert lp.state["status"] == "hr_failed" and "engine crashed" in lp.state["error"]
    finally:
        hr.close()
    # no HR Steel at all
    job = _job()
    lp = L.Loop(job, "mechanism", verify="none", hr_url="http://127.0.0.1:9", base_building="Ex22_SMF"); lp.start(); lp.join(60)
    assert lp.state["status"] == "failed" and "not reachable" in lp.state["error"]
    # not eligible (RC IV drift)
    job = _job(); hr = FH.FakeHR()
    try:
        lp, _ = _run(job, "drift", hr)
        assert lp.state["status"] == "failed" and "not eligible" in lp.state["error"] and "Ex22_SMF__drift" not in hr.jobs
    finally:
        hr.close()


def test_stop_during_the_re_design():
    job = _job(); hr = FH.FakeHR(slow=6.0)
    try:
        ev = []
        lp = L.Loop(job, "mechanism", verify="none", hr_url=hr.url, base_building="Ex22_SMF", on_event=ev.append)
        lp.start()
        t0 = time.time()
        while not any(e["type"] == "status" and e["status"] == "hr_running" for e in ev) and time.time() - t0 < 30:
            time.sleep(0.05)
        lp.stop(); lp.join(60)
        assert lp.state["status"] == "stopped" and "Ex22_SMF__mechanism" in hr.stopped
    finally:
        hr.close()


def test_package_zip_and_unpack_roundtrip():
    job = _job()
    data = L.package_zip(job, "Ex22_SMF")
    dest = tempfile.mkdtemp()
    n = L.unpack_zip(data, dest)
    assert n > 5 and os.path.exists(os.path.join(dest, "cfg.py")) and os.path.exists(os.path.join(dest, "design", "calc_package.json"))
    assert not os.path.exists(os.path.join(dest, "nlrha"))               # analyses are not part of the package


def test_compare_reads_verification_outputs():
    """compare() with canned candidate outputs: a passing NLRHA, a DDM --only result and a re-push with the modifier cleared."""
    job = _job(); base = F.read_job(job)
    cdir = os.path.join(tempfile.mkdtemp(), "cand")
    shutil.copytree(job, cdir, ignore=shutil.ignore_patterns("*.pkl", "__pycache__", "feedback"))
    prm = json.load(open(os.path.join(cdir, "pushover", "hinge_params_used.json")))
    prm["beam_flexure"]["modifiers"] = []
    json.dump(prm, open(os.path.join(cdir, "pushover", "hinge_params_used.json"), "w"))
    cand = F.read_job(cdir)
    plan = F.mechanism_plan(base)
    checks = L.package_checks("mechanism", plan, base, cand)
    comp = L.compare("mechanism", plan, base, cand, checks, dict(mode="full", runs=[]))
    texts = {c["text"]: c["ok"] for c in comp["criteria"]}
    assert texts["panel-zone modifier cleared in the re-push"] and any("Chapter 16 on the candidate" in t for t in texts)
    assert "pushover" in comp and comp["nlrha"]["candidate"]["verdict"] == "ACCEPTABLE"
    plan = F.resize_plan(base)
    comp = L.compare("resize", plan, base, cand, L.package_checks("resize", plan, base, cand), dict(mode="quick", runs=[dict(label="verify:ddm", rc=0)]))
    assert "ddm" in comp and any(r["label"] == "1.4D" for r in comp["ddm"]) and any("DDM on the governing" in c["text"] for c in comp["criteria"])


def test_ddm_like_for_like_flags():
    opts = json.load(open(os.path.join(EX22, "ddm_results.json")))["options"]
    f = L.ddm_like_for_like(opts)
    assert f[:4] == ["--nsub", "2", "2", "4"] and f[f.index("--residual") + 1] == "lehigh" and "--no-rigid-end-offset" in f
    assert L.ddm_like_for_like({}) == [] and "--rigid-end-offset" in L.ddm_like_for_like({"rigid_end_offset": 0.05})


def test_state_write_survives_windows_replace_race(monkeypatch):
    """On Windows os.replace raises PermissionError while the tab holds state.json open; the writer must retry, not lose the write."""
    job = _job(); lp = L.Loop(job, "mechanism", verify="none", hr_url="http://127.0.0.1:9", base_building="Ex22_SMF")
    real = os.replace; calls = {"n": 0}
    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", flaky)
    lp.state["status"] = "probe"; lp._save()
    assert calls["n"] == 3 and L.load_state(job, lp.id)["status"] == "probe"
