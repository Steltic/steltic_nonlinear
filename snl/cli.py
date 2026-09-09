"""snl -- Steltic_nonlinear orchestrator.

    python -m snl run <package.zip | folder> [--out DIR] [--steltic-engine DIR] [--params hinge_params.json]
                                            [--only pushover nlrha ddm] [--skip ...] [--parallel 2] [--dt 0.01] ...
    python -m snl report <job folder>            # rebuild four_analyses.html + snl_summary.json from existing outputs
    python -m snl inspect <package.zip | folder> # read the design basis (the Pushover Analyst's reader)

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
            cmd += ["--plasticity", a.plasticity, "--member-nseg", str(a.member_nseg)]
        elif s == "nlrha":
            cmd = [py, "-m", "nlrha", "run", job, "--parallel", str(a.parallel), "--dt", str(a.dt), "--integrator", a.integrator, "--n", str(a.n_records)] + site + params
            cmd += ["--plasticity", a.plasticity, "--member-nseg", str(a.member_nseg)]
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
    r.add_argument("--plasticity", default="fibre", choices=["fibre", "fiber", "imk"], help="NSP/NLRHA plasticity (default fibre)")
    r.add_argument("--member-nseg", type=int, default=4, help="NSP/NLRHA member subdivisions (default 4)")
    mc = sub.add_parser("mesh-converge", help="fibre mesh-convergence ladder (NSP/NLRHA/DDM) with JSON scorecard")
    mc.add_argument("package"); mc.add_argument("--analyses", nargs="+", default=["nsp", "nlrha", "ddm"], choices=["nsp", "nlrha", "ddm", "pushover"])
    mc.add_argument("--out"); mc.add_argument("--steltic-engine"); mc.add_argument("--params")
    mc.add_argument("--tol", type=float, default=0.10, help="relative stop band on primary metrics (default 0.10 = 10%%)")
    mc.add_argument("--max-rungs", type=int, default=4); mc.add_argument("--site-class", default="D")
    mc.add_argument("--risk-category", choices=["I", "II", "III", "IV"]); mc.add_argument("--n-records", type=int, default=11)
    mc.add_argument("--parallel", type=int, default=1); mc.add_argument("--dt", type=float, default=0.01)
    mc.add_argument("--dry-run", action="store_true", help="print rung plan + stop-rule only; no OpenSees")
    p = sub.add_parser("report"); p.add_argument("job")
    i = sub.add_parser("inspect"); i.add_argument("package"); i.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "mesh-converge":
        from mesh_convergence.driver import main as mc_main
        return mc_main(a)
    return {"run": cmd_run, "report": cmd_report, "inspect": cmd_inspect}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
