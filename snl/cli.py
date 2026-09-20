"""snl -- Steltic_nonlinear orchestrator.

    python -m snl run <package.zip | folder> [--out DIR] [--steltic-engine DIR] [--params hinge_params.json]
                                            [--only pushover nlrha ddm] [--skip ...] [--parallel 2] [--dt 0.01] ...
    python -m snl report <job folder>            # rebuild four_analyses.html + snl_summary.json from existing outputs
    python -m snl inspect <package.zip | folder> # read the design basis (the Pushover Analyst's reader)
    python -m snl review <job folder> [--focus "..."] [--no-standards] [--max-searches 8]
                                                 # the model's engineer's review of the run, grounded in the standards (snl/review.py)

One command takes the Steltic output package (the Download .zip or the job folder) and runs the three nonlinear
analyses in sequence -- Pushover (ASCE 41 NSP), NLRHA (ASCE 7-22 Chapter 16) and DDM (GMNIA system capacity) --
each in its own process (openseespy is a process singleton), writes every output the three tools define
(pushover/, nlrha/, ddm_*.{html,json}, the four 3-D viewers and their hub), then the four-analyses comparison
sheet. Steps run independently: a failure in one is logged in snl_run.json and the others still run.
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time, zipfile

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


def _run(cmd, log, env=None, cwd=None):
    t0 = time.time()
    print(">>", " ".join(cmd), flush=True)
    with open(log, "a", encoding="utf-8") as lf:
        lf.write("\n$ " + " ".join(cmd) + "\n"); lf.flush()
        p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, cwd=cwd)
    return dict(returncode=p.returncode, seconds=round(time.time() - t0), log=log)


def cmd_run(a):
    job = _unpack(a.package, a.out)
    py = sys.executable
    env = dict(os.environ)
    if a.steltic_engine:
        env["STELTIC_ENGINE_DIR"] = os.path.abspath(a.steltic_engine)
    steps = [s for s in STEPS if (not a.only or s in a.only) and s not in (a.skip or [])]
    status = dict(job=job, started=time.strftime("%Y-%m-%dT%H:%M:%S"), steps={})
    log = os.path.join(job, "snl_run.log")
    params = ["--params", os.path.abspath(a.params)] if a.params else []
    site = ["--site-class", a.site_class]
    for s in steps:
        if s == "pushover":
            cmd = [py, "-m", "pushover", "run", job] + site + params + (["--tail", a.tail] if a.tail else []) + (["--post-cap-ratio", str(a.post_cap_ratio)] if a.post_cap_ratio else [])
            plast = a.plasticity if a.plasticity else "fibre"
            nseg = a.member_nseg if a.member_nseg is not None else (4 if plast == "fibre" else 1)
            cmd += ["--plasticity", plast, "--member-nseg", str(nseg)]
        elif s == "nlrha":
            cmd = [py, "-m", "nlrha", "run", job, "--parallel", str(a.parallel), "--dt", str(a.dt), "--integrator", a.integrator, "--n", str(a.n_records)] + site + params
            if a.records_set: cmd += ["--records-set"] + list(a.records_set)
            if a.target and a.target != "code": cmd += ["--target", a.target]
            if a.site_hazard: cmd += ["--site-hazard", os.path.abspath(a.site_hazard)]
            if a.pulse_fraction is not None: cmd += ["--pulse-fraction", str(a.pulse_fraction)]
            if a.sf_bounds: cmd += ["--sf-bounds", a.sf_bounds]
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
        print("   %s -> rc %s (%s s)" % (s, status["steps"][s]["returncode"], status["steps"][s]["seconds"]), flush=True)
    # comparison sheet + summary (whatever ran)
    from . import compare
    try:
        status["four_analyses"] = compare.build(job)
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
    bad = [s for s, v in status["steps"].items() if v.get("returncode") not in (0, None)]
    print(">> done.", ("FAILED steps: " + ", ".join(bad) + " (see snl_run.log)") if bad else "all steps completed.", "Outputs in", job)
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


def cmd_inspect(a):
    job = _unpack(a.package, a.out)
    subprocess.run([sys.executable, "-m", "pushover", "inspect", job])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="snl", description="Steltic_nonlinear: Pushover + NLRHA + DDM on one Steltic package, then the four-analyses sheet.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("package"); r.add_argument("--out", help="folder to unpack a .zip into (default: next to the zip)")
    r.add_argument("--steltic-engine", help="path to steltic/steel_engine (or set STELTIC_ENGINE_DIR); required by the DDM step")
    r.add_argument("--params", help="job copy of hinge_params.json filled from AISC 342 / ASCE 41 (default: the repository placeholder -> UNVERIFIED banner)")
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
    i = sub.add_parser("inspect"); i.add_argument("package"); i.add_argument("--out")
    rv = sub.add_parser("review", help="the model reads what the run measured, looks the governing clauses up in the standards (RAG_API_URL) and writes review.md / review.html")
    rv.add_argument("job"); rv.add_argument("--focus", default="", help="what the engineer wants the review to concentrate on")
    rv.add_argument("--no-standards", action="store_true", help="do not query the standards server; clauses come from the model's memory, marked UNVERIFIED")
    rv.add_argument("--max-searches", type=int, default=8, help="standards searches the model may make (default 8)")
    a = ap.parse_args(argv)
    if a.cmd == "mesh-converge":
        from mesh_convergence.driver import main as mc_main
        return mc_main(a)
    if a.cmd == "review":
        from .review import cmd_review
        return cmd_review(a)
    return {"run": cmd_run, "report": cmd_report, "inspect": cmd_inspect, "feedback": cmd_feedback}[a.cmd](a)


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
        print("\n" + p["brief"])
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
