"""loop.py -- runs one feedback loop end to end: plan -> HR Steel re-design -> verification -> comparison ->
(on the user's say-so) promotion to design of record.

    <job>/feedback/<loop_id>/            one folder per loop run
        state.json                       everything the UI shows (status, steps, log, comparison)
        plan.json, brief.txt             the change set and the brief HR Steel received
        base.zip, candidate.zip          the package before and after
        candidate/                       the unpacked candidate package + its verification outputs
        hr_transcript.txt                HR Steel's streamed narrative
        hinge_params_cleared.json        (mechanism) the re-push parameters with the modifier cleared

HR Steel is driven through its own API (download / restore / run with resume / download): the base job
is copied to a CANDIDATE job named <base>__<kind>, so the design of record is never touched until the
user promotes the candidate. Promotion restores the candidate into the base job name with ?archive=1
(HR Steel moves the previous design aside as <base>__<timestamp>), writes design/design_of_record.json,
and replaces the hub project's package and analyses (the previous set is archived under <job>/archive/).
"""
from __future__ import annotations
import datetime, io, json, os, shutil, subprocess, sys, threading, time, zipfile

from . import feedback as F, hr_client as HR

VERIFY_MODES = ("full", "quick", "none")
PACKAGE_TOP = ("cfg.py", "conversation.json", "run_log.jsonl", "activity_log.jsonl", "report.html", "viewer_3d.html",
               "model_opensees.py", "model_static.py", "model_gmnia.py")
PACKAGE_DIRS = ("design", "figs", "rag")
ANALYSIS_ITEMS = ("pushover", "nlrha", "ddm_report.html", "ddm_results.json", "ddm_viewer_3d.html", "four_analyses.html",
                  "snl_summary.json", "snl_run.json", "snl_run.log", "steltic_viewer_bundle.html", "mesh_convergence",
                  "design_criteria_16_1_4.html", "design_criteria_16_1_4.docx")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def feedback_dir(job):
    return os.path.join(os.path.abspath(job), "feedback")


def loop_dir(job, loop_id):
    return os.path.join(feedback_dir(job), loop_id)


def new_id(job, kind):
    base = "%s-%s" % (kind, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    d = loop_dir(job, base); n = 1
    while os.path.exists(d):
        n += 1; d = loop_dir(job, "%s-%d" % (base, n))
    return os.path.basename(d)


def replace_file(tmp, dst, tries=60):
    """os.replace that tolerates Windows: while another thread holds the target open for reading (the tab polling
    state.json), MoveFileEx fails with PermissionError (WinError 5 / 32). Readers hold the file for milliseconds, so
    retry briefly instead of losing the write -- on POSIX the first attempt always succeeds."""
    for i in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.01)


def load_state(job, loop_id):
    p = os.path.join(loop_dir(job, loop_id), "state.json")
    if not os.path.exists(p):
        return None
    for i in range(20):                                    # a replace may be in flight on Windows
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (PermissionError, json.JSONDecodeError):
            if i == 19:
                raise
            time.sleep(0.01)


def list_loops(job):
    out = []
    fd = feedback_dir(job)
    if os.path.isdir(fd):
        for name in sorted(os.listdir(fd)):
            st = load_state(job, name)
            if st:
                out.append({k: st.get(k) for k in ("id", "kind", "title", "status", "passed", "created", "finished", "candidate", "promoted_at", "verify", "error")})
    return out


def package_zip(folder, root_name):
    """Zip the resumable package files of a job folder under <root_name>/ (the layout HR Steel's /api/restore reads)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for top in PACKAGE_TOP:
            p = os.path.join(folder, top)
            if os.path.isfile(p):
                z.write(p, "%s/%s" % (root_name, top))
        for name in sorted(os.listdir(folder)):
            if name.startswith("report_v") and name.endswith(".html"):
                z.write(os.path.join(folder, name), "%s/%s" % (root_name, name))
        for d in PACKAGE_DIRS:
            dp = os.path.join(folder, d)
            if os.path.isdir(dp):
                for dirpath, _, files in os.walk(dp):
                    for f in files:
                        full = os.path.join(dirpath, f)
                        z.write(full, "%s/%s" % (root_name, os.path.relpath(full, folder).replace(os.sep, "/")))
    return buf.getvalue()


def unpack_zip(data, dest):
    """Unpack a package zip, flattening the single top folder HR Steel wraps everything in."""
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        tops = {n.split("/")[0] for n in names if "/" in n}
        wrap = len(tops) == 1 and not any(n in ("report.html", "model_opensees.py", "cfg.py") for n in names)
        n_written = 0
        for n in names:
            rel = n.split("/", 1)[1] if wrap and "/" in n else n
            if not rel or rel.endswith("/") or rel.startswith("/") or ".." in rel.split("/"):
                continue
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as f, open(target, "wb") as g:
                shutil.copyfileobj(f, g)
            n_written += 1
    return n_written


def _rc_flag(rc):
    return {"I_II": "II", "III": "III", "IV": "IV"}.get(rc, rc)


class Loop(threading.Thread):
    """One loop run. `on_event(dict)` receives every state change for the UI; state.json is the record."""

    def __init__(self, job, kind, options=None, edits=None, verify="full", hr_url="", base_building=None,
                 engine_dir=None, python=None, parallel=2, n_records=11, dt=0.01, integrator="hht", site_class="D",
                 params=None, on_event=None, loop_id=None):
        super().__init__(daemon=True)
        self.job = os.path.abspath(job); self.kind = kind; self.options = dict(options or {}); self.edits = dict(edits or {})
        self.verify_mode = verify if verify in VERIFY_MODES else "full"
        self.hr_url = (hr_url or "").rstrip("/"); self.base = base_building or os.path.basename(self.job)
        self.engine_dir = engine_dir or os.environ.get("STELTIC_ENGINE_DIR") or ""
        self.python = python or sys.executable
        self.parallel, self.n_records, self.dt, self.integrator, self.site_class = int(parallel), int(n_records), float(dt), integrator, site_class
        self.params = params
        self.on_event = on_event or (lambda ev: None)
        self.id = loop_id or new_id(self.job, kind)
        self.dir = loop_dir(self.job, self.id); os.makedirs(self.dir, exist_ok=True)
        self.candidate = "%s__%s" % (self.base, kind)
        self._stop_ev = threading.Event(); self._proc = None
        self.state = dict(id=self.id, kind=kind, title=F.LOOPS[kind], status="planned", passed=None, created=now(), finished=None,
                          base=self.base, candidate=self.candidate, verify=self.verify_mode, steps=[], log=[], error=None,
                          plan=None, comparison=None, hr=dict(outcome=None, reason=None, n_events=0, tools=0), promoted_at=None)

    # ------------------------------------------------------------------ bookkeeping
    def _save(self):
        p = os.path.join(self.dir, "state.json"); tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1, default=str)
        replace_file(tmp, p)

    def _log(self, text):
        self.state["log"].append(dict(t=now(), text=str(text)))
        if len(self.state["log"]) > 800:
            self.state["log"] = self.state["log"][-800:]
        self.on_event(dict(type="log", id=self.id, text=str(text)))

    def _step(self, name, status, note=None):
        steps = self.state["steps"]
        cur = next((s for s in steps if s["name"] == name), None)
        if cur is None:
            cur = dict(name=name, status=status, t0=now(), t1=None, note=note); steps.append(cur)
        else:
            cur["status"] = status; cur["note"] = note if note is not None else cur.get("note")
            if status in ("done", "failed", "skipped"):
                cur["t1"] = now()
        self._save(); self.on_event(dict(type="step", id=self.id, step=cur, status=self.state["status"]))

    def _set(self, status, **kw):
        self.state["status"] = status; self.state.update(kw); self._save()
        self.on_event(dict(type="status", id=self.id, status=status, passed=self.state.get("passed"), error=self.state.get("error")))

    def stop(self):
        self._stop_ev.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=_NO_WINDOW)
                else:
                    p.terminate()
            except Exception:
                pass

    def stopped(self):
        return self._stop_ev.is_set()

    # ------------------------------------------------------------------ the run
    def run(self):
        try:
            self._run()
        except Exception as ex:                                            # noqa: BLE001
            self._log("!! %s: %s" % (type(ex).__name__, ex))
            self._set("failed", error="%s: %s" % (type(ex).__name__, ex), finished=now(), passed=False)

    def _run(self):
        # 1. plan
        self._step("plan", "running")
        jd = F.read_job(self.job)
        plan = F.plan(jd, self.kind, self.options)
        plan = apply_edits(plan, self.edits, jd)
        self.state["plan"] = F.plan_as_json(plan)
        json.dump(self.state["plan"], open(os.path.join(self.dir, "plan.json"), "w", encoding="utf-8"), indent=1)
        open(os.path.join(self.dir, "brief.txt"), "w", encoding="utf-8").write(plan["brief"])
        if not plan.get("eligible"):
            self._step("plan", "failed", "; ".join(plan.get("reasons") or ["not eligible"]))
            self._set("failed", error="not eligible: " + "; ".join(plan.get("reasons") or []), finished=now(), passed=False)
            return
        self._step("plan", "done", "%s -> HR Steel candidate '%s'" % (plan["title"], self.candidate))
        # 2. base package -> candidate job on HR Steel
        self._step("copy", "running")
        if not self.hr_url or not HR.healthy(self.hr_url):
            raise RuntimeError("HR Steel is not reachable at %r -- start it (the hub starts it with this module) and retry" % self.hr_url)
        base_zip = None
        try:
            base_zip = HR.download(self.hr_url, self.base)
            self._log("downloaded the current design of record from HR Steel (%d kB)" % (len(base_zip) // 1024))
        except HR.HRError as e:
            self._log("HR Steel holds no job '%s' (%s) -- using the package in this project folder instead" % (self.base, e))
            base_zip = package_zip(self.job, self.base)
        open(os.path.join(self.dir, "base.zip"), "wb").write(base_zip)
        with zipfile.ZipFile(io.BytesIO(base_zip)) as z:
            if not any(n.endswith("conversation.json") for n in z.namelist()):
                raise RuntimeError("the base package has no conversation.json, so HR Steel cannot continue this design. "
                                   "Restore the design's download zip into HR Steel (project '%s') first." % self.base)
        r = HR.restore(self.hr_url, self.candidate, base_zip, archive=True)
        self._log("candidate job '%s' created on HR Steel (%d files%s)" % (self.candidate, r.get("restored", 0), (", previous candidate archived as " + r["archived"]) if r.get("archived") else ""))
        self._step("copy", "done", self.candidate)
        if self.stopped():
            self._set("stopped", finished=now()); return
        # 3. the re-design
        self._step("hr", "running")
        self._set("hr_running")
        transcript = open(os.path.join(self.dir, "hr_transcript.txt"), "w", encoding="utf-8")
        text_buf = []
        reason_buf = []                       # the model's reasoning stream, shown in its own box like the hub does
        def on_ev(ev):
            t = ev.get("type")
            if t == "token":
                text_buf.append(str(ev.get("text", ""))); transcript.write(str(ev.get("text", "")))
                if sum(len(x) for x in text_buf) > 400:
                    self.on_event(dict(type="hr_text", id=self.id, text="".join(text_buf))); text_buf.clear()
            elif t == "reasoning":
                reason_buf.append(str(ev.get("text", "")))
                if sum(len(x) for x in reason_buf) > 400:
                    self.on_event(dict(type="hr_reason", id=self.id, text="".join(reason_buf))); reason_buf.clear()
            elif t == "tool":
                self.state["hr"]["tools"] += 1
                self._log("HR step %s: %s" % (ev.get("step", "?"), ev.get("title") or ev.get("name")))
                transcript.write("\n[tool %s] %s\n" % (ev.get("step", "?"), ev.get("title") or ev.get("name")))
            elif t == "tool_result":
                transcript.write("[result] %s (%s ms)\n" % (str(ev.get("summary", ""))[:300], ev.get("ms")))
            elif t in ("status", "milestone"):
                self._log("HR: %s" % ev.get("text")); transcript.write("\n[%s] %s\n" % (t, ev.get("text")))
            elif t == "paused":
                self._log("HR paused: %s %s" % (ev.get("reason"), ev.get("detail") or ""))
            elif t == "error":
                self._log("HR error: %s" % ev.get("text"))
            self.state["hr"]["n_events"] += 1
            if self.state["hr"]["n_events"] % 25 == 0:
                self._save()
        outcome = HR.run(self.hr_url, self.candidate, plan["brief"], on_event=on_ev, should_stop=self.stopped, resume=True)
        if text_buf:
            self.on_event(dict(type="hr_text", id=self.id, text="".join(text_buf)))
        if reason_buf:
            self.on_event(dict(type="hr_reason", id=self.id, text="".join(reason_buf)))
        transcript.close()
        self.state["hr"].update(outcome=outcome["outcome"], reason=outcome["reason"])
        if outcome["outcome"] == "stopped":
            self._step("hr", "failed", "stopped"); self._set("stopped", finished=now()); return
        if outcome["outcome"] != "done":
            self._step("hr", "failed", "%s: %s" % (outcome["outcome"], outcome["reason"]))
            self._set("hr_" + ("paused" if outcome["outcome"] == "paused" else "failed"), error="HR Steel %s: %s" % (outcome["outcome"], outcome["reason"]), finished=now(), passed=False)
            return
        self._step("hr", "done", "%d events, %d tool calls" % (outcome["n_events"], self.state["hr"]["tools"]))
        # 4. the candidate package
        self._step("fetch", "running")
        cand_zip = HR.download(self.hr_url, self.candidate)
        open(os.path.join(self.dir, "candidate.zip"), "wb").write(cand_zip)
        cdir = os.path.join(self.dir, "candidate")
        if os.path.isdir(cdir):
            shutil.rmtree(cdir)
        n = unpack_zip(cand_zip, cdir)
        for req in ("cfg.py", "model_opensees.py", "design/calc_package.json", "design/member_schedule.csv"):
            if not os.path.exists(os.path.join(cdir, req)):
                raise RuntimeError("the candidate package from HR Steel lacks %s -- the re-design did not complete" % req)
        self._log("candidate package fetched: %d files" % n)
        self._step("fetch", "done", "%d files" % n)
        # the base hinge parameters travel with the candidate (same building, same component tables)
        base_params = self.params or (os.path.join(self.job, "pushover", "hinge_params_used.json") if os.path.exists(os.path.join(self.job, "pushover", "hinge_params_used.json")) else None)
        if base_params:
            shutil.copy(base_params, os.path.join(cdir, "hinge_params_base.json"))
        # 5. package-level checks (no analysis)
        self._step("check", "running")
        cand = F.read_job(cdir)
        checks = package_checks(self.kind, plan, jd, cand)
        for c in checks["items"]:
            self._log(("ok  " if c["ok"] else "NG  ") + c["text"])
        self._step("check", "done" if checks["ok"] else "failed", "%d of %d package checks pass" % (sum(1 for c in checks["items"] if c["ok"]), len(checks["items"])))
        if self.stopped():
            self._set("stopped", finished=now()); return
        # 6. verification analyses
        self._set("verifying")
        ver = dict(mode=self.verify_mode, runs=[])
        if self.verify_mode != "none":
            for cmd, label, cwd in self._verify_commands(plan, jd, cand, cdir, base_params):
                self._step(label, "running")
                rc = self._sub(cmd, cwd, label)
                ver["runs"].append(dict(label=label, cmd=cmd, rc=rc))
                self._step(label, "done" if rc == 0 else "failed", "rc %s" % rc)
                if self.stopped():
                    self._set("stopped", finished=now()); return
        else:
            self._log("verification skipped (mode none)")
        self.state["verification"] = ver
        # 7. comparison + verdict
        self._step("compare", "running")
        cand = F.read_job(cdir)
        comp = compare(self.kind, plan, jd, cand, checks, ver)
        self.state["comparison"] = comp
        try:
            from . import compare as C
            C.build(cdir)
        except Exception as ex:                                            # noqa: BLE001
            self._log("four-analyses sheet for the candidate skipped: %s" % ex)
        self._step("compare", "done", comp["verdict_text"])
        self._set("verified" if comp["passed"] else "failed", passed=comp["passed"], finished=now())
        self._log("loop finished: %s" % comp["verdict_text"])

    def _sub(self, cmd, cwd, label):
        env = dict(os.environ)
        if self.engine_dir:
            env["STELTIC_ENGINE_DIR"] = self.engine_dir
        logp = os.path.join(self.dir, "%s.log" % label.replace(":", "_"))
        self._log("$ " + " ".join(cmd))
        with open(logp, "a", encoding="utf-8") as lf:
            lf.write("\n$ " + " ".join(cmd) + "\n"); lf.flush()
            self._proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=cwd, env=env, creationflags=_NO_WINDOW)
            rc = self._proc.wait(); self._proc = None
        return rc

    def _verify_commands(self, plan, jd, cand, cdir, base_params):
        py = self.python
        params = ["--params", os.path.join(cdir, "hinge_params_base.json")] if base_params else []
        rc = F.risk_category(jd)
        nl_cmd = [py, "-m", "nlrha", "run", cdir, "--out", os.path.join(cdir, "nlrha"), "--parallel", str(self.parallel), "--dt", str(self.dt),
                  "--integrator", self.integrator, "--n", str(self.n_records), "--site-class", self.site_class, "--plasticity", "imk", "--member-nseg", "1",
                  "--risk-category", _rc_flag(rc)] + params
        if self.verify_mode == "quick" and jd.get("nlrha"):
            recs = sorted(jd["nlrha"].get("per_record", []), key=lambda r: -(r.get("peak_drift") or 0))[:3]
            idx = sorted({jd["nlrha"]["per_record"].index(r) + 1 for r in recs})
            if idx:
                nl_cmd += ["--only-records", ",".join(str(i) for i in idx)]
        cmds = []
        if self.kind == "drift":
            cmds.append((nl_cmd, "verify:nlrha", cdir))
        elif self.kind == "resize":
            combos = plan.get("verify", {}).get("combos") or []
            if self.engine_dir:
                ddm = [py, "-m", "steltic_ddm", "run", cdir, "--workers", str(self.parallel), "--no-block", "--risk-category", _rc_flag(rc)]
                if combos:
                    ddm += ["--only"] + combos
                ddm += ddm_like_for_like((jd.get("ddm") or {}).get("options") or {})
                cmds.append((ddm, "verify:ddm", cdir))
            else:
                self._log("DDM re-check skipped: STELTIC_ENGINE_DIR not set")
            if self.verify_mode == "full":
                cmds.append((nl_cmd, "verify:nlrha", cdir))
        elif self.kind == "mechanism":
            prm, why = F.cleared_params(jd.get("params") or {}, cand.get("calc") or {}, tuple(plan.get("panel_zone", {}).get("window", (0.6, 0.9))) if plan.get("panel_zone") else (0.6, 0.9))
            pp = os.path.join(self.dir, "hinge_params_cleared.json")
            json.dump(prm, open(pp, "w", encoding="utf-8"), indent=1)
            self.state["modifier_decision"] = why; self._log("panel-zone modifier: " + why)
            push = [py, "-m", "pushover", "run", cdir, "--out", os.path.join(cdir, "pushover"), "--site-class", self.site_class,
                    "--plasticity", "fibre", "--member-nseg", "4", "--params", pp]
            pcr = ((jd.get("pushover") or {}).get("numerics") or {}).get("post_cap_ratio")
            if pcr:
                push += ["--post-cap-ratio", str(pcr)]
            cmds.append((push, "verify:pushover", cdir))
            if self.verify_mode == "full":
                nl_cmd = [x for x in nl_cmd]
                if base_params:
                    nl_cmd[nl_cmd.index("--params") + 1] = pp
                cmds.append((nl_cmd, "verify:nlrha", cdir))
        return cmds


def ddm_like_for_like(opts):
    """CLI flags reproducing the base DDM run's modelling options, so the re-check differs only in the sections."""
    out = []
    if isinstance(opts.get("nsub"), list) and len(opts["nsub"]) == 3:
        out += ["--nsub"] + [str(int(x)) for x in opts["nsub"]]
    for key, flag in (("nip", "--nip"), ("residual", "--residual"), ("hardening", "--hardening"), ("bow", "--bow"), ("psi", "--psi"), ("dlam", "--dlam")):
        if opts.get(key) is not None:
            out += [flag, str(opts[key])]
    if opts.get("rigid_end_offset") is not None:
        out += ["--rigid-end-offset", str(opts["rigid_end_offset"])]
    elif opts:
        out += ["--no-rigid-end-offset"]
    return out


# ---------------------------------------------------------------------------------------------- edits
def apply_edits(plan, edits, jd):
    """User edits from the UI: resize -> {'change_set': {id: proposed | null}}; drift -> {'target_fraction': f} is an
    option (handled before); mechanism -> {'scwb_target': x, 'joints': [...]} . The brief is rebuilt after editing."""
    if not edits:
        return plan
    if plan["kind"] == "resize" and isinstance(edits.get("change_set"), dict):
        cs = []
        for c in plan["change_set"]:
            v = edits["change_set"].get(c["id"], c["proposed"])
            if v:
                c = dict(c, proposed=str(v).upper().strip()); cs.append(c)
        for gid, v in edits["change_set"].items():                     # rows the user added by hand
            if v and not any(c["id"] == gid for c in cs) and gid in jd["groups"]:
                g = jd["groups"][gid]; r = next((r for r in plan["rows"] if r["id"] == gid), {})
                cs.append(dict(id=gid, role=g["role"], section=g["section"], levels=g["levels"], proposed=str(v).upper().strip(),
                               verdict=("upsize" if F.section_weight_lb_ft(v) and F.section_weight_lb_ft(v) > (g.get("lb_ft") or 0) else "downsize"), reason="user's call: " + str(r.get("reason", "")), n=g["n"]))
        plan["change_set"] = cs
        plan["eligible"] = bool(cs); plan["reasons"] = [] if cs else ["the change set is empty"]
        plan["brief"] = F.resize_brief(plan, jd)
    elif plan["kind"] == "mechanism":
        if isinstance(edits.get("scwb_target"), (int, float)):
            for s in plan["storeys"]:
                s["scwb_target"] = float(edits["scwb_target"])
        if isinstance(edits.get("joints"), list) and plan.get("panel_zone"):
            for m in plan["panel_zone"]["modifiers"]:
                m["joints"] = [dict(joint=str(j.get("joint") if isinstance(j, dict) else j), ratio=(j.get("ratio") if isinstance(j, dict) else None),
                                    action=(j.get("action", "doublers") if isinstance(j, dict) else "doublers")) for j in edits["joints"]]
        plan["brief"] = F.mechanism_brief(plan, jd)
    elif plan["kind"] == "drift" and edits.get("brief_note"):
        plan["brief"] += "\n\nNOTE FROM THE ENGINEER: " + str(edits["brief_note"])
    return plan


# ---------------------------------------------------------------------------------------------- checks
def _dc_max(jd):
    ms = [m for m in jd["members"].values() if isinstance(m.get("DC"), (int, float))]
    if not ms:
        return None, None
    m = max(ms, key=lambda x: x["DC"])
    return m["DC"], m["id"]


def _drift(jd):
    st = jd["steltic"]
    if not st.get("drift_X"):
        return None, None
    return max(max(st["drift_X"] or [0]), max(st["drift_Y"] or [0])) / 100.0, (st.get("drift_limit_pct") or 0) / 100.0 or None


def package_checks(kind, plan, base, cand):
    """What can be read off the candidate package before any analysis runs."""
    items = []
    dc, gid = _dc_max(cand)
    items.append(dict(ok=dc is not None and dc <= 1.0 + 1e-9, text="candidate member D/C max %s (%s)" % (("%.3f" % dc) if dc is not None else "n/a", gid)))
    over = [m["id"] for m in cand["members"].values() if isinstance(m.get("DC"), (int, float)) and m["DC"] > 1.0]
    if over:
        items.append(dict(ok=False, text="members over 1.0: " + ", ".join(over)))
    cd, cl = _drift(cand)
    if cd is not None and cl:
        items.append(dict(ok=cd <= cl + 1e-9, text="candidate linear drift %.2f%% vs allowable %.2f%%" % (100 * cd, 100 * cl)))
    if kind == "drift":
        rel = plan["relief"]
        items.append(dict(ok=cand["cfg"].get("has_relief", False), text="cfg.py carries drift_relief_16_1_2"))
        got = cand["cfg"].get("drift_limit")
        items.append(dict(ok=got is not None and abs(got - rel["linear_target"]) < 5e-5, text="cfg drift_limit = %s (target %.4f)" % (got, rel["linear_target"])))
        if cd is not None:
            items.append(dict(ok=cd <= plan["numbers"]["new_linear_target"] * 1.02 + 1e-9, text="candidate linear drift %.2f%% against the relaxed target %.2f%%" % (100 * cd, 100 * plan["numbers"]["new_linear_target"])))
    elif kind == "resize":
        cand_by_tag = {e["tag"]: e for e in cand["elements"]}
        for c in plan["change_set"]:
            want = c["proposed"]
            tags = (base["groups"].get(c["id"]) or {}).get("tags") or []
            got = sorted({cand_by_tag[t]["section"] for t in tags if t in cand_by_tag})      # the same members, by element tag
            if not got:                                                                     # topology changed: fall back to role + level
                g = base["groups"].get(c["id"]) or {}
                got = sorted({e["section"] for e in cand["elements"] if e["role"] == c["role"] and e["level"] in c["levels"] and e["kind"] == g.get("kind") and e["section"] in (want, c["section"])})
            ok = got == [want]
            items.append(dict(ok=ok, text="%s levels %s: asked %s -> got %s" % (c["id"], c["levels"], want, ", ".join(got) or "nothing")))
    elif kind == "mechanism":
        cp = (cand.get("calc") or {}).get("capacity_design") or {}
        pz = cp.get("panel_zone") if isinstance(cp, dict) else None
        if plan.get("panel_zone"):
            bj = pz.get("by_joint") if isinstance(pz, dict) else None
            items.append(dict(ok=bool(bj), text="capacity_design.panel_zone.by_joint recorded (%d joint groups)" % (len(bj) if bj else 0)))
            if bj:
                lo_, hi_ = plan["panel_zone"]["window"]
                bad = [j.get("joint") for j in bj if not isinstance(j.get("V_pz_over_V_ye"), (int, float)) or not (lo_ <= j["V_pz_over_V_ye"] <= hi_)]
                items.append(dict(ok=not bad, text="V_pz/V_ye within %.1f-%.1f at every joint group%s" % (lo_, hi_, (" -- outside at " + ", ".join(map(str, bad))) if bad else "")))
        if plan.get("storeys"):
            sc = cp.get("SCWB") if isinstance(cp, dict) else None
            bs = sc.get("by_story") if isinstance(sc, dict) else None
            items.append(dict(ok=bool(bs), text="capacity_design.SCWB.by_story recorded (%d storeys)" % (len(bs) if bs else 0)))
            if bs:
                for s in plan["storeys"]:
                    row = next((b for b in bs if str(b.get("story", b.get("level"))) == str(s["level"])), None)
                    v = row.get("ratio") if row else None
                    items.append(dict(ok=isinstance(v, (int, float)) and v >= s["scwb_target"] - 1e-9, text="SCWB ratio at level %s: %s (target %.2f)" % (s["level"], v, s["scwb_target"])))
    return dict(ok=all(i["ok"] for i in items), items=items)


def compare(kind, plan, base, cand, checks, ver):
    """Base vs candidate, and the loop's pass/fail."""
    b_t, c_t = F.steel_tons(base), F.steel_tons(cand)
    b_dc, b_gid = _dc_max(base); c_dc, c_gid = _dc_max(cand)
    b_dr, b_dl = _drift(base); c_dr, c_dl = _drift(cand)
    out = dict(tons=dict(base=b_t, candidate=c_t, delta=round(c_t["total"] - b_t["total"], 1), delta_pct=(round(100 * (c_t["total"] - b_t["total"]) / b_t["total"], 1) if b_t["total"] else None)),
               dc=dict(base=b_dc, base_id=b_gid, candidate=c_dc, candidate_id=c_gid),
               drift=dict(base=b_dr, base_limit=b_dl, candidate=c_dr, candidate_limit=c_dl),
               groups=group_diff(base, cand), checks=checks, verification=ver, criteria=[])
    crit = out["criteria"]
    crit.append(dict(ok=checks["ok"], text="package checks"))
    nl_b, nl_c = base.get("nlrha"), cand.get("nlrha")
    if nl_c:
        out["nlrha"] = dict(base=_nl_summary(nl_b), candidate=_nl_summary(nl_c))
        ok = bool(nl_c["verdict"].get("overall")) and (nl_c["verdict"].get("mean_drift_max") or 0) <= (nl_c["limits"].get("mean_limit") or 0) + 1e-9
        crit.append(dict(ok=ok, text="Chapter 16 on the candidate: %s, mean drift %.2f%% vs %.2f%%" % ("ACCEPTABLE" if nl_c["verdict"].get("overall") else "NOT ACCEPTABLE", 100 * (nl_c["verdict"].get("mean_drift_max") or 0), 100 * (nl_c["limits"].get("mean_limit") or 0))))
    elif kind == "drift" and ver.get("mode") != "none":
        crit.append(dict(ok=False, text="Chapter 16 verification did not produce nlrha/nlrha_package.json"))
    dd_b, dd_c = base.get("ddm"), cand.get("ddm")
    if dd_c:
        rows = []
        for r in dd_c.get("runs", []):
            rb = next((x for x in (dd_b or {}).get("runs", []) if x["label"] == r["label"]), None)
            chk = r.get("check") or [None, "n/a"]
            rows.append(dict(label=r["label"], kind=r["kind"], lambda_u=r["lambda_u"], base_lambda_u=(rb or {}).get("lambda_u"), check=chk[1], phi_lambda=chk[0], mechanism=(r.get("cls") or {}).get("mechanism", "")[:120]))
        out["ddm"] = rows
        ok = all((r["check"] in ("PASS", "n/a")) and (r["lambda_u"] or 0) >= 1.0 for r in rows) and bool(rows)
        crit.append(dict(ok=ok, text="DDM on the governing combination(s): " + "; ".join("%s lambda_u %.2f (%s%s)" % (r["label"], r["lambda_u"], r["check"], (", was %.2f" % r["base_lambda_u"]) if r["base_lambda_u"] else "") for r in rows)))
    elif kind == "resize" and ver.get("mode") != "none" and any(r["label"] == "verify:ddm" for r in ver.get("runs", [])):
        crit.append(dict(ok=False, text="the DDM re-check did not write ddm_results.json"))
    po_b, po_c = base.get("pushover"), cand.get("pushover")
    if po_c:
        out["pushover"] = dict(base=_po_census(po_b, base["levels"]), candidate=_po_census(po_c, cand["levels"]),
                               modifier=(cand.get("params") or {}).get("beam_flexure", {}).get("modifiers"))
        if kind == "mechanism":
            named = {s["level"] for s in plan.get("storeys", [])}
            bad = [lvl for lvl, c in out["pushover"]["candidate"].items() if (not named or lvl in named) and c["col_yielded"] > 0]
            crit.append(dict(ok=not bad, text=("no column yielding above the base at BSE-2N" + ((" -- still at levels %s" % bad) if bad else ""))))
            mods = out["pushover"]["modifier"] or []
            pzmods = [m for m in mods if float(m.get("factor", 1)) < 1]
            crit.append(dict(ok=not pzmods, text="panel-zone modifier cleared in the re-push" if not pzmods else "panel-zone modifier still applied (x%.2f)" % float(pzmods[0]["factor"])))
    elif kind == "mechanism" and ver.get("mode") != "none":
        crit.append(dict(ok=False, text="the re-push did not write pushover/pushover_package.json"))
    out["passed"] = all(c["ok"] for c in crit)
    out["verdict_text"] = ("PASSED -- %d of %d criteria" if out["passed"] else "NOT PASSED -- %d of %d criteria") % (sum(1 for c in crit if c["ok"]), len(crit))
    return out


def _nl_summary(nl):
    if not nl:
        return None
    v, l = nl["verdict"], nl["limits"]
    return dict(verdict=("ACCEPTABLE" if v.get("overall") else "NOT ACCEPTABLE"), mean_drift_max=v.get("mean_drift_max"), mean_limit=l.get("mean_limit"),
                n_records=v.get("n_records"), n_unacceptable=v.get("n_unacceptable"), worst_DC_CP=max([g.get("DC_CP") or 0 for g in nl.get("deformation_groups", [])] or [0]),
                worst_FC_DC=v.get("worst_FC_DC"))


def _po_census(po, levels):
    out = {}
    if not po:
        return out
    for d in po["directions"].values():
        for c in (d["acceptance"].get("BSE-2N") or {}).get("census", []):
            z = c.get("z_in", 0)
            if z <= 1e-6:
                continue
            k = F._level_of(levels, z) or z
            e = out.setdefault(k, dict(col_yielded=0, beam_yielded=0))
            e["col_yielded"] = max(e["col_yielded"], c.get("col_yielded") or 0); e["beam_yielded"] = max(e["beam_yielded"], c.get("beam_yielded") or 0)
    return out


def group_diff(base, cand):
    """Sections per (role, level) before and after -- the table an engineer reads first."""
    def table(jd):
        t = {}
        for e in jd["elements"]:
            t.setdefault((e["role"], e["level"]), set()).add(e["section"])
        return t
    tb, tc = table(base), table(cand)
    rows = []
    for key in sorted(set(tb) | set(tc), key=lambda k: (k[0], k[1] or 0)):
        b, c = sorted(tb.get(key, [])), sorted(tc.get(key, []))
        rows.append(dict(role=key[0], level=key[1], base=b, candidate=c, changed=(b != c)))
    return rows


# ---------------------------------------------------------------------------------------------- promotion
def promote(job, loop_id, hr_url, base_building=None, on_log=None):
    """Make the verified candidate the design of record: HR Steel job <base> (previous archived), the hub project's
    package and analyses (previous archived under <job>/archive/<stamp>/), and design/design_of_record.json."""
    job = os.path.abspath(job); base = base_building or os.path.basename(job)
    st = load_state(job, loop_id)
    if not st:
        raise RuntimeError("no such loop run")
    if st.get("status") not in ("verified", "failed"):
        raise RuntimeError("the loop has not finished (status %s)" % st.get("status"))
    log = on_log or (lambda s: None)
    cdir = os.path.join(loop_dir(job, loop_id), "candidate")
    if not os.path.isdir(cdir):
        raise RuntimeError("candidate package missing")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    comp = st.get("comparison") or {}
    marker = dict(loop=loop_id, kind=st["kind"], loop_title=st["title"], promoted_at=now(), candidate_building=st.get("candidate"),
                  supersedes="%s (HR Steel archive %s__%s; hub archive/%s)" % (base, base, stamp, stamp),
                  verification={c["text"]: ("ok" if c["ok"] else "NG") for c in comp.get("criteria", [])},
                  passed=st.get("passed"), tons=comp.get("tons"))
    os.makedirs(os.path.join(cdir, "design"), exist_ok=True)
    json.dump(marker, open(os.path.join(cdir, "design", "design_of_record.json"), "w", encoding="utf-8"), indent=1)
    # HR Steel: the candidate becomes the base job; the previous base is archived by the server
    archived = None
    if hr_url and HR.healthy(hr_url):
        r = HR.restore(hr_url, base, package_zip(cdir, base), archive=True)
        archived = r.get("archived"); log("HR Steel job '%s' now holds the promoted design (%d files%s)" % (base, r.get("restored", 0), (", previous archived as " + archived) if archived else ""))
    else:
        log("HR Steel not reachable -- the hub project was promoted; restore %s/candidate.zip into HR Steel project '%s' by hand" % (loop_id, base))
    # the hub project folder: archive, then copy the candidate (package + its verification outputs) in
    arch = os.path.join(job, "archive", stamp); os.makedirs(arch, exist_ok=True)
    moved = 0
    for name in list(os.listdir(job)):
        if name in ("feedback", "archive"):
            continue
        p = os.path.join(job, name)
        if name in PACKAGE_TOP or name in PACKAGE_DIRS or name in ANALYSIS_ITEMS or (name.startswith("report_v") and name.endswith(".html")) or name.endswith(".pyc"):
            shutil.move(p, os.path.join(arch, name)); moved += 1
    copied = 0
    for dirpath, dirs, files in os.walk(cdir):
        rel = os.path.relpath(dirpath, cdir)
        for f in files:
            src = os.path.join(dirpath, f); dst = os.path.join(job, rel, f) if rel != "." else os.path.join(job, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(src, dst); copied += 1
    log("hub project: %d items archived to archive/%s, %d files of the candidate copied in" % (moved, stamp, copied))
    try:
        from . import compare as C
        C.build(job)
        from pushover import viewer_core as VC
        VC.write_hub(job)
    except Exception as ex:                                                # noqa: BLE001
        log("four-analyses sheet not rebuilt: %s" % ex)
    st["status"] = "promoted"; st["promoted_at"] = now(); st["archived"] = dict(hr=archived, hub="archive/" + stamp)
    sp = os.path.join(loop_dir(job, loop_id), "state.json")
    with open(sp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, default=str)
    replace_file(sp + ".tmp", sp)
    return st
