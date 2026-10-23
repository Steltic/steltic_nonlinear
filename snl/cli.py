"""snl -- Steltic_nonlinear orchestrator.

    python -m snl collect <job folder>                      # FIRST: read the component parameters out of the standards
    python -m snl run <package.zip | folder> [--out DIR] [--steltic-engine DIR] [--params hinge_params.json]
                                            [--only pushover nlrha ddm] [--skip ...] [--parallel 2] [--dt 0.01] ...
    python -m snl report <job folder>            # rebuild four_analyses.html + snl_summary.json from existing outputs
    python -m snl inspect <package.zip | folder> # read the design basis (the Pushover Analyst's reader)
    python -m snl confirm <job folder> [--check] [--json] [--by NAME] [--note TEXT]
                                                 # user-designed building (HR build-only): check the model and confirm it
                                                 # before the long runs; `run` refuses a build-only package until then
    python -m snl review <job folder> [--focus "..."] [--no-standards] [--max-searches N]
                                                 # the model's engineer's review of the run, grounded in the standards (snl/review.py)

One command takes the Steltic output package (the Download .zip or the job folder) and runs the three nonlinear
analyses in sequence -- Pushover (ASCE 41 NSP), NLRHA (ASCE 7-22 Chapter 16) and DDM (GMNIA system capacity) --
each in its own process (openseespy is a process singleton), writes every output the three tools define
(pushover/, nlrha/, ddm_*.{html,json}, the four 3-D viewers and their hub), then the four-analyses comparison
sheet. Steps run independently: a failure in one is logged in snl_run.json and the others still run.
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys, time, zipfile

STEPS = ("pushover", "nlrha", "ddm")


def _unpack(src, out):
    """Return the job folder: an existing folder as is, or a zip extracted next to it (or into --out)."""
    src = os.path.abspath(src)
    if os.path.isdir(src):
        return src
    if not zipfile.is_zipfile(src):
        sys.exit("input must be a Steltic job folder or its .zip")
    name = os.path.splitext(os.path.basename(src))[0]
    dest = os.path.abspath(out) if out else os.path.join(os.path.dirname(src), name)
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        # a zip that wraps everything in one top folder is flattened so report.html sits in the job root
        tops = {n.split("/")[0] for n in names if "/" in n}
        wrap = len(tops) == 1 and not any(n in ("report.html", "model_opensees.py", "cfg.py") for n in names)
        for n in names:
            rel = n.split("/", 1)[1] if wrap and "/" in n else n
            if not rel or rel.endswith("/"):
                continue
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as f, open(target, "wb") as g:
                shutil.copyfileobj(f, g)
    for req in ("report.html", "model_opensees.py", "cfg.py"):
        if not os.path.exists(os.path.join(dest, req)):
            sys.exit(f"{req} missing after unpacking {src} -- is this a Steltic output package?")
    return dest


_NOT_EVALUATED_RX = re.compile(r"^(?:pushover|NLRHA|nlrha|DDM|steltic_ddm)\s+NOT EVALUATED\s+--\s+(.+)$", re.M)


def _not_evaluated(text):
    """NL-R2-L2 / NL-R2-13 follow-up: the reason an engine refused the package up front ('<engine> NOT EVALUATED --
    system ... not supported: ...'), or None. A refusal is a valid outcome, not a failed step."""
    m = _NOT_EVALUATED_RX.search(text or "")
    return m.group(1).strip() if m else None


def _run(cmd, log, env=None, cwd=None):
    t0 = time.time()
    print(">>", " ".join(cmd), flush=True)
    with open(log, "a", encoding="utf-8") as lf:
        lf.write("\n$ " + " ".join(cmd) + "\n"); lf.flush()
        start = lf.tell()
        p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, cwd=cwd)
    out = dict(returncode=p.returncode, seconds=round(time.time() - t0), log=log)
    if p.returncode != 0:
        try:
            with open(log, encoding="utf-8", errors="replace") as lf:
                lf.seek(start); why = _not_evaluated(lf.read())
        except OSError:
            why = None
        if why:
            out["not_evaluated"] = why
    return out


def cmd_run(a):
    job = _unpack(a.package, a.out)
    from pushover import user_designed as UD                      # user-designed building (HR build-only mode)
    _ud = UD.info(job)
    if _ud and not _ud["confirmed"]:
        why = ("its cfg.py changed after the confirmation" if _ud.get("stale") else "it has not been confirmed")
        print("!! user-designed building: %s. Review the model and the checks, then run `snl confirm %s` "
              "(nothing was run)." % (why, job), flush=True)
        return 3
    UD.print_warning(_ud, "every SNL analysis (pushover, NLRHA, DDM)")
    py = sys.executable
    env = dict(os.environ)
    if a.steltic_engine:
        env["STELTIC_ENGINE_DIR"] = os.path.abspath(a.steltic_engine)
    steps = [s for s in STEPS if (not a.only or s in a.only) and s not in (a.skip or [])]
    status = dict(job=job, started=time.strftime("%Y-%m-%dT%H:%M:%S"), steps={})
    log = os.path.join(job, "snl_run.log")
    if a.steltic_engine:
        os.environ.setdefault("STELTIC_ENGINE_DIR", os.path.abspath(a.steltic_engine))
    from pushover import existing as EX                           # NL-R2-04 (warning only): new-building criteria
    _exist = EX.existing_structure(job)
    EX.print_warning(_exist, "every SNL analysis (pushover, NLRHA, DDM)")
    if _exist:
        status["existing_structure"] = dict(_exist, statement=EX.warning_text(_exist, "every SNL analysis (pushover, NLRHA, DDM)"))
    if _ud:
        status["user_designed"] = dict(confirmed=True, statement=UD.warning_text(_ud, "every SNL analysis (pushover, NLRHA, DDM)"))
    # The component parameters: the file `snl collect` read out of the standards for THIS project when
    # it exists, else what --params names, else the repository placeholder (and the UNVERIFIED banner
    # that goes with it). Collect first is the point: the analyses then run on cited values and the
    # reports need no re-issue.
    from . import collect as _C
    from pushover import params_schema as _PS
    collected = os.path.join(job, _C.OUT_NAME)
    params_path = os.path.abspath(a.params) if a.params else (collected if os.path.exists(collected) else None)
    # --no-collect: the user chose to run WITHOUT collecting the specification values (no standards corpus on
    # this PC, or they prefer not to add one). The run is the same; the reports say why every check that uses
    # the component parameters is UNVERIFIED (the marker file below is what their banners read).
    opted_out = bool(getattr(a, "no_collect", False))
    marker = os.path.join(job, _PS.NOT_COLLECTED_NAME)
    if params_path or not opted_out:
        if os.path.exists(marker):
            os.remove(marker)                                     # a run on cited values: the note no longer applies
    if params_path:
        print(">> component parameters: %s%s" % (params_path, " (collected from the corpus)" if params_path == collected else ""))
        if opted_out and params_path == collected:
            print("   (--no-collect has no effect: the specification values were already collected for this project and are used)")
    elif opted_out:
        print(">> component parameters: repository placeholder -- specification values NOT collected (%s): every specification "
              "check that uses them comes back marked UNVERIFIED in the reports" % _PS.NOT_COLLECTED_WHY)
        json.dump({"opted_out": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "statement": _PS.NOT_COLLECTED_STATEMENT},
                  open(marker, "w", encoding="utf-8"), indent=1)
        status["spec_values"] = {"collected": False, "opted_out": True, "statement": _PS.NOT_COLLECTED_STATEMENT}
    else:
        print(">> component parameters: repository placeholder -- run `snl collect` first to read them from the standards")
    params = ["--params", params_path] if params_path else []
    site = ["--site-class", a.site_class]
    for s in steps:
        if s == "pushover":
            cmd = [py, "-m", "pushover", "run", job] + site + params + (["--tail", a.tail] if a.tail else []) + (["--post-cap-ratio", str(a.post_cap_ratio)] if a.post_cap_ratio else [])
            plast = a.plasticity if a.plasticity else "fibre"
            nseg = a.member_nseg if a.member_nseg is not None else (4 if plast == "fibre" else 1)
            cmd += ["--plasticity", plast, "--member-nseg", str(nseg)]
            if a.risk_category: cmd += ["--risk-category", a.risk_category]     # BPON levels, ASCE 41-23 Table 2-5
        elif s == "nlrha":
            cmd = [py, "-m", "nlrha", "run", job, "--parallel", str(a.parallel), "--dt", str(a.dt), "--integrator", a.integrator, "--n", str(a.n_records)] + site + params
            if a.records_set: cmd += ["--records-set"] + list(a.records_set)
            if a.target and a.target != "code": cmd += ["--target", a.target]
            if a.site_hazard: cmd += ["--site-hazard", os.path.abspath(a.site_hazard)]
            if a.pulse_fraction is not None: cmd += ["--pulse-fraction", str(a.pulse_fraction)]
            if a.sf_bounds: cmd += ["--sf-bounds", a.sf_bounds]
            if a.record_budget_s is not None: cmd += ["--record-budget-s", str(a.record_budget_s)]     # NL-R2-15
            if a.trim_records and a.trim_records != "off": cmd += ["--trim-records", a.trim_records, "--trim-ends", a.trim_ends]   # NL-R2-18
            if a.resume: cmd += ["--resume"]                                                       # NL-R2-26
            # Product rule 1: NLRHA starts ModIMK; fibre via mesh-converge ladder
            plast = a.plasticity if a.plasticity else "imk"
            nseg = a.member_nseg if a.member_nseg is not None else (4 if plast in ("fibre", "fiber") else 1)
            cmd += ["--plasticity", plast, "--member-nseg", str(nseg)]
            if a.risk_category: cmd += ["--risk-category", a.risk_category]
        else:
            if not env.get("STELTIC_ENGINE_DIR"):
                status["steps"][s] = dict(returncode=None, skipped="STELTIC_ENGINE_DIR / --steltic-engine not set (the DDM regenerates the load combinations with Steltic's design_pipeline)")
                print("!! ddm skipped:", status["steps"][s]["skipped"]); continue
            cmd = [py, "-m", "steltic_ddm", "run", job, "--workers", str(a.parallel)] + (["--no-block"] if a.no_block else [])
            if a.risk_category: cmd += ["--risk-category", a.risk_category]
        status["steps"][s] = _run(cmd, log, env=env)
        _ne = status["steps"][s].get("not_evaluated")
        print("   %s -> %s (%s s)" % (s, ("NOT EVALUATED: " + _ne) if _ne else "rc %s" % status["steps"][s]["returncode"],
                                     status["steps"][s]["seconds"]), flush=True)
    not_evaluated = {k: v["not_evaluated"] for k, v in status["steps"].items() if v.get("not_evaluated")}
    # comparison sheet + summary (whatever ran)
    from . import compare
    try:
        status["four_analyses"] = compare.build(job, not_evaluated=not_evaluated)
        print(">> four analyses:", status["four_analyses"])
    except Exception as ex:                                            # noqa: BLE001
        status["four_analyses_error"] = repr(ex); print("!! four_analyses failed:", ex)
    _hub(job)
    # the 16.1.4 design criteria draft, refreshed from whatever the run produced (cheap; no physics)
    if not a.no_criteria:
        try:
            from nlrha import design_criteria as DC
            docx, htmlp = DC.write(job, project=a.project, engineer=a.engineer, reviewer=a.reviewer, params_path=(os.path.abspath(a.params) if a.params else None), risk_category=a.risk_category)
            status["design_criteria"] = [docx, htmlp]; print(">> design criteria (16.1.4):", docx)
        except Exception as ex:                                        # noqa: BLE001
            status["design_criteria_error"] = repr(ex); print("!! design criteria failed:", ex)
    status["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    json.dump(status, open(os.path.join(job, "snl_run.json"), "w"), indent=1)
    bad = [s for s, v in status["steps"].items() if v.get("returncode") not in (0, None) and not v.get("not_evaluated")]
    msg = ("FAILED steps: " + ", ".join(bad) + " (see snl_run.log)") if bad else "no step failed."
    if not_evaluated:
        msg += " NOT EVALUATED (system not supported): " + ", ".join(not_evaluated) + "."
    print(">> done.", msg, "Outputs in", job)
    return 1 if bad else 0


def _hub(job):
    try:
        from pushover import viewer_core as VC
        VC.write_hub(job)
    except Exception:
        pass


def cmd_report(a):
    from . import compare
    job = os.path.abspath(a.job)
    print(">> four analyses:", compare.build(job)); _hub(job)


def cmd_revise(a):
    from . import revise
    revise.run(a.job)


def cmd_collect(a):
    from . import collect
    r = collect.run(a.job, out_name=a.out, emit=collect.Emitter())
    return 0 if r["ok"] else 2


def cmd_confirm(a):
    from pushover import user_designed as UD
    job = os.path.abspath(a.job)
    if not UD.is_user_designed(job):
        print("not a user-designed (build-only) package: no provenance.json in %s -- nothing to confirm" % job)
        return 2
    r = UD.checks(job) if a.check else UD.confirm(job, by=a.by, note=a.note)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        for c in r["checks"]:
            print("  [%s] %-20s %s" % (c["status"].upper(), c["label"], c["detail"]))
        if r.get("accepted_defaults"):
            print("  accepted defaults: " + "; ".join("%s = %s" % (x["item"], x["value"]) for x in r["accepted_defaults"]))
        if a.check:
            print(">> checks %s" % ("pass: ready to confirm" if r["ok"] else "FAIL: cannot be confirmed"))
        else:
            print(">> %s" % ("confirmed: nl_basis.json written; `snl run` may start" if r.get("confirmed") else "NOT confirmed: a hard check fails"))
    return 0 if r["ok"] else 1


def cmd_inspect(a):
    job = _unpack(a.package, a.out)
    subprocess.run([sys.executable, "-m", "pushover", "inspect", job])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="snl", description="Steltic_nonlinear: Pushover + NLRHA + DDM on one Steltic package, then the four-analyses sheet.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("package"); r.add_argument("--out", help="folder to unpack a .zip into (default: next to the zip)")
    r.add_argument("--steltic-engine", help="path to steltic/steel_engine (or set STELTIC_ENGINE_DIR); required by the DDM step")
    r.add_argument("--params", help="job copy of hinge_params.json filled from AISC 342 / ASCE 41 (default: the repository placeholder -> UNVERIFIED banner)")
    r.add_argument("--no-collect", action="store_true",
                   help="run without collected specification values on purpose (no standards corpus, or you prefer not to add one): "
                        "the repository placeholders are used and every report says the specification checks are UNVERIFIED "
                        "because the values were not collected. No effect when `snl collect` has already written them.")
    r.add_argument("--only", nargs="*", choices=STEPS); r.add_argument("--skip", nargs="*", choices=STEPS)
    r.add_argument("--parallel", type=int, default=2); r.add_argument("--dt", type=float, default=0.01); r.add_argument("--integrator", default="hht", choices=["hht", "newmark"])
    r.add_argument("--n-records", type=int, default=11); r.add_argument("--site-class", default="D"); r.add_argument("--risk-category", choices=["I", "II", "III", "IV"])
    r.add_argument("--tail", choices=["auto", "fine_step", "arclength", "none"]); r.add_argument("--post-cap-ratio", type=float, help="tail-protocol rung 3 (disclosed modelling change)")
    r.add_argument("--no-block", action="store_true", help="DDM: do not write the ddm_analysis block into calc_package.json")
    r.add_argument("--plasticity", default=None, choices=["fibre", "fiber", "imk"], help="override plasticity (defaults: NSP fibre, NLRHA imk)")
    r.add_argument("--member-nseg", type=int, default=None, help="member subdivisions (default 4 fibre / 1 imk)")
    r.add_argument("--records-set", nargs="*", default=None, help="NLRHA record set folder(s): indexed sets and/or user folders of PEER .AT2 / CSV pairs (default: the shipped P-695 far-field set)")
    r.add_argument("--target", default="code", choices=["code", "mcer", "cs"], help="NLRHA scaling target (mcer / cs need `nlrha hazard` first)")
    r.add_argument("--site-hazard", help="site_hazard.json from `nlrha hazard` (default <job>/nlrha/site_hazard.json)")
    r.add_argument("--pulse-fraction", type=float, default=None, help="share of the suite reserved for pulse-type records")
    r.add_argument("--sf-bounds", help="NLRHA: keep records whose shape-fit scale factor lies in lo-hi, e.g. 0.25-4")
    r.add_argument("--record-budget-s", default=None, help="NLRHA wall-time budget per record: 'auto' (default: scaled to the record and the model, "
                                                            "at least 4 h) or seconds (0 = none); a record that runs out is 'incomplete (time-out)'")
    r.add_argument("--trim-records", nargs="?", const="standard", default="off", choices=["off", "standard", "aggressive"],
                   help="NLRHA optional record trimming (default off; --trim-records alone = standard 0.1-99.5%% Arias, aggressive = 0.5-99%%); "
                        "2%% spectral check per record, disclosed in the NLRHA report and the 16.1.4 document")
    r.add_argument("--trim-ends", default="both", choices=["both", "head", "tail"], help="which quiet end(s) --trim-records trims")
    r.add_argument("--resume", action="store_true", help="NLRHA: keep the records already finished by an interrupted run (<job>/nlrha/records/r<i>.pkl) and run only the rest")
    r.add_argument("--no-criteria", action="store_true", help="do not write the 16.1.4 design criteria draft at the end of the run")
    r.add_argument("--project", help="project name for the design criteria document"); r.add_argument("--engineer"); r.add_argument("--reviewer")
    fb = sub.add_parser("feedback", help="the three re-design loops back to HR Steel: plan (and optionally run) one, or promote a verified candidate")
    fb.add_argument("job"); fb.add_argument("--loop", choices=["drift", "resize", "mechanism"], help="which loop to plan / run")
    fb.add_argument("--run", action="store_true", help="send the brief to HR Steel and verify (needs --steltic-url)")
    fb.add_argument("--steltic-url", default=os.environ.get("STELTIC_URL", ""), help="HR Steel server (default $STELTIC_URL)")
    fb.add_argument("--building", help="HR Steel job name of the design of record (default: the job folder name)")
    fb.add_argument("--verify", default="full", choices=["full", "quick", "none"]); fb.add_argument("--parallel", type=int, default=2)
    fb.add_argument("--option", action="append", default=[], help="threshold override key=value (see snl/feedback.py DEFAULTS)")
    fb.add_argument("--promote", help="loop id whose verified candidate becomes the design of record")
    fb.add_argument("--steltic-engine", help="path to steltic/steel_engine for the DDM re-check (or STELTIC_ENGINE_DIR)")
    mc = sub.add_parser("mesh-converge", help="fibre mesh-convergence ladder (NSP/NLRHA/DDM) with JSON scorecard")
    mc.add_argument("package"); mc.add_argument("--analyses", nargs="+", default=["nsp", "nlrha", "ddm"], choices=["nsp", "nlrha", "ddm", "pushover"])
    mc.add_argument("--out"); mc.add_argument("--steltic-engine"); mc.add_argument("--params")
    mc.add_argument("--tol", type=float, default=0.10, help="relative stop band on primary metrics (default 0.10 = 10%%)")
    mc.add_argument("--max-rungs", type=int, default=4); mc.add_argument("--site-class", default="D")
    mc.add_argument("--risk-category", choices=["I", "II", "III", "IV"]); mc.add_argument("--n-records", type=int, default=11)
    mc.add_argument("--parallel", type=int, default=1); mc.add_argument("--dt", type=float, default=0.01)
    mc.add_argument("--dry-run", action="store_true", help="print rung plan + stop-rule only; no OpenSees")
    mc.add_argument("--early-abort-nc", type=int, default=2, help="NLRHA: abandon suite after N NC records (product default 2)")
    mc.add_argument("--rigid-end-offset", type=float, default=None, help="HR DDM rigid end offset fraction")
    mc.add_argument("--no-rigid-end-offset", action="store_true")
    p = sub.add_parser("report"); p.add_argument("job")
    rs = sub.add_parser("revise", help="re-issue the reports with the standards corpus behind them: needs review.md "
                                       "from the Review tab, re-asks ASCE 7 / ASCE 41 / AISC 342 through RAG_API_URL, "
                                       "records every passage and replaces the placeholder wording with the citation")
    rs.add_argument("job")
    co = sub.add_parser("collect", help="read the component modelling parameters (backbones, acceptance, expected material) out of "
                                        "the standards corpus (RAG_API_URL) for this building and write hinge_params_collected.json; "
                                        "the analyses then run on cited values. Do this BEFORE `run`.")
    co.add_argument("job"); co.add_argument("--out", default="hinge_params_collected.json", help="file name written into the job folder")
    i = sub.add_parser("inspect"); i.add_argument("package"); i.add_argument("--out")
    cf = sub.add_parser("confirm", help="user-designed building (HR build-only): run the confirmation checks and, unless --check, "
                                        "write nl_basis.json so `run` may start (warnings on your own T1 / V / W do not block)")
    cf.add_argument("job"); cf.add_argument("--check", action="store_true", help="only run the checks; do not confirm")
    cf.add_argument("--json", action="store_true"); cf.add_argument("--by", help="who confirms (recorded and printed in the reports)")
    cf.add_argument("--note", help="a note recorded with the confirmation and printed in every report")
    rv = sub.add_parser("review", help="the model reads what the run measured, looks the governing clauses up in the standards (RAG_API_URL) and writes review.md / review.html")
    rv.add_argument("job"); rv.add_argument("--focus", default="", help="what the engineer wants the review to concentrate on")
    rv.add_argument("--no-standards", action="store_true", help="do not query the standards server; clauses come from the model's memory, marked UNVERIFIED")
    rv.add_argument("--max-searches", type=int, default=0, help="cap the standards searches the model may make; 0 (the default) is no cap -- it searches as often as the review needs")
    a = ap.parse_args(argv)
    if a.cmd == "mesh-converge":
        from mesh_convergence.driver import main as mc_main
        return mc_main(a)
    if a.cmd == "review":
        from .review import cmd_review
        return cmd_review(a)
    return {"run": cmd_run, "report": cmd_report, "revise": cmd_revise, "collect": cmd_collect,
            "inspect": cmd_inspect, "feedback": cmd_feedback, "confirm": cmd_confirm}[a.cmd](a)


def cmd_feedback(a):
    from . import feedback as F, loop as L
    job = os.path.abspath(a.job)
    if a.promote:
        st = L.promote(job, a.promote, a.steltic_url, base_building=a.building, on_log=lambda s_: print(">>", s_))
        print(">> design of record:", st["status"], st.get("archived")); return 0
    opts = {}
    for kv in a.option:
        k, v = kv.split("=", 1); opts[k] = float(v)
    jd = F.read_job(job)
    print(json.dumps(F.status(jd), indent=1))
    kinds = [a.loop] if a.loop else list(F.LOOPS)
    for k in kinds:
        p = F.plan(jd, k, opts)
        print("\n== %s: %s" % (k, "ELIGIBLE" if p["eligible"] else "not eligible -- " + "; ".join(p["reasons"])))
        if k == "drift": print(json.dumps(p["numbers"], indent=None))
        if k == "resize": print("\n".join("  %-24s %-9s %-9s %s" % (r["id"], r["verdict"], r["proposed"] or "", r["reason"][:80]) for r in p["rows"]))
        if k == "mechanism": print("  storeys:", [(s_["level"], s_["col_yielded"]) for s_ in p["storeys"]], " panel zone:", bool(p["panel_zone"]))
        print("\n" + (p.get("brief") or ""))                         # NL-R2-08
    if a.run:
        if not a.loop:
            sys.exit("--run needs --loop")
        lp = L.Loop(job, a.loop, options=opts, verify=a.verify, hr_url=a.steltic_url, base_building=a.building, engine_dir=a.steltic_engine,
                    parallel=a.parallel, on_event=lambda ev: print("  [%s] %s" % (ev.get("type"), ev.get("text") or ev.get("status") or (ev.get("step") or {}).get("name", ""))))
        lp.start(); lp.join()
        st = lp.state
        print(">> loop %s: %s (%s)" % (lp.id, st["status"], (st.get("comparison") or {}).get("verdict_text") or st.get("error")))
        return 0 if st.get("passed") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
