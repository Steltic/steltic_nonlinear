"""Mesh-convergence driver: run fibre M0→M1→… for NSP / NLRHA / DDM and emit JSON scorecards.

    python -m mesh_convergence --package JOB --analyses nsp nlrha ddm --out DIR
    python -m snl mesh-converge JOB --analyses nsp --dry-run

Does not reorder the NLRHA Newton/algo cascade. Fibre is the method.
Default relative stop band: 10% (0.10). Cap: 4 rungs.
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time
from .rungs import DEFAULT_RUNGS, rung_knobs
from .stop_rule import walk_ladder, DEFAULT_TOL, DEFAULT_MAX_RUNGS
from . import metrics as MX
from .scorecard import write_scorecard


def _env_with(base: dict, knobs: dict) -> dict:
    env = dict(base)
    env.update(knobs.get("env") or {})
    return env


def _run(cmd, log, env=None, cwd=None):
    t0 = time.time()
    print(">>", " ".join(cmd), flush=True)
    with open(log, "a", encoding="utf-8") as lf:
        lf.write("\n$ " + " ".join(cmd) + "\n"); lf.flush()
        p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, cwd=cwd)
    return dict(returncode=p.returncode, seconds=round(time.time() - t0, 1))


def plan_rungs(max_rungs: int = DEFAULT_MAX_RUNGS):
    return list(DEFAULT_RUNGS[:max_rungs])


def run_analysis_ladder(analysis: str, package: str, out_root: str, *, args) -> dict:
    py = sys.executable
    rungs = plan_rungs(args.max_rungs)
    metric_rows = []
    rung_meta = []
    log = os.path.join(out_root, "mesh_convergence_%s.log" % analysis)
    env_base = dict(os.environ)
    if args.steltic_engine:
        env_base["STELTIC_ENGINE_DIR"] = os.path.abspath(args.steltic_engine)

    for i, rung in enumerate(rungs):
        kn = rung_knobs(rung, analysis)
        rung_dir = os.path.join(out_root, "%s_%s" % (analysis, rung["level"]))
        os.makedirs(rung_dir, exist_ok=True)
        print("== %s %s ==" % (analysis, rung["level"]), kn, flush=True)

        if args.dry_run:
            # synthetic metrics so dry-run exercises the stop rule wiring
            base = {"nsp": dict(T1=1.0, Vy=100.0, Vpeak=150.0, delta_t=10.0),
                    "nlrha": dict(mean_drift_max=0.03, roof_mean_X=0.02, roof_mean_Y=0.015,
                                 worst_FC_DC=1.1, n_ok=11, n_records=11,
                                 n_unacceptable=0, ACCEPTABLE=True),
                    "ddm": dict(lambda_u=1.5, lambda_G=1.8, phi_s_lambda_u=1.2)}[analysis if analysis != "pushover" else "nsp"]
            # pretend M0 differs; M1+ within 10% of M0→M1 jump already settled
            scale = 1.0 if i == 0 else (1.12 if i == 1 else 1.12 * (1.0 + 0.02 * (i - 1)))
            row = {k: (v * scale if isinstance(v, (int, float)) and k not in ("n_ok", "n_records", "n_unacceptable", "ACCEPTABLE") else v)
                   for k, v in base.items()}
            metric_rows.append(row)
            rung_meta.append(dict(level=rung["level"], knobs=kn, dry_run=True))
            if i >= 1:
                ladder = walk_ladder(analysis, metric_rows, tol=args.tol, max_rungs=args.max_rungs)
                if ladder["status"] == "converged":
                    break
            continue

        env = _env_with(env_base, kn)
        params = ["--params", os.path.abspath(args.params)] if args.params else []
        site = ["--site-class", args.site_class]
        if analysis in ("nsp", "pushover"):
            cmd = [py, "-m", "pushover", "run", package, "--out", rung_dir,
                   "--plasticity", "fibre", "--member-nseg", str(kn["member_nseg"])] + site + params
            st = _run(cmd, log, env=env)
            row = MX.from_nsp(rung_dir)
        elif analysis == "nlrha":
            cmd = [py, "-m", "nlrha", "run", package, "--out", rung_dir,
                   "--plasticity", "fibre", "--member-nseg", str(kn["member_nseg"]),
                   "--parallel", str(args.parallel), "--dt", str(args.dt),
                   "--n", str(args.n_records)] + site + params
            if args.risk_category:
                cmd += ["--risk-category", args.risk_category]
            st = _run(cmd, log, env=env)
            row = MX.from_nlrha(rung_dir)
        else:  # ddm
            if not env.get("STELTIC_ENGINE_DIR"):
                return dict(error="STELTIC_ENGINE_DIR / --steltic-engine required for DDM ladder")
            nsub = kn["nsub"]
            cmd = [py, "-m", "steltic_ddm", "run", package, "--out", rung_dir,
                   "--nsub", str(nsub[0]), str(nsub[1]), str(nsub[2]),
                   "--nip", str(kn["nip"]), "--workers", str(args.parallel)]
            if args.risk_category:
                cmd += ["--risk-category", args.risk_category]
            st = _run(cmd, log, env=env)
            row = MX.from_ddm(rung_dir)
        row = dict(row, _rung=rung["level"], _run=st)
        metric_rows.append(row)
        rung_meta.append(dict(level=rung["level"], knobs=kn, run=st))
        if i >= 1:
            ladder = walk_ladder(analysis, metric_rows, tol=args.tol, max_rungs=args.max_rungs)
            if ladder["steps"] and ladder["steps"][-1]["stop"]:
                break

    ladder = walk_ladder(analysis, metric_rows, tol=args.tol, max_rungs=args.max_rungs)
    case = os.path.basename(os.path.abspath(package).rstrip("/"))
    path = write_scorecard(out_root, case=case, analysis=analysis, rungs=rungs,
                           ladder=ladder, metric_rows=metric_rows,
                           extra=dict(rung_meta=rung_meta, package=os.path.abspath(package)))
    print(">> scorecard", path, "status=", ladder["status"], "stop_level=", ladder.get("stop_level"))
    return dict(scorecard=path, ladder=ladder, metrics=metric_rows)


def main(args) -> int:
    package = os.path.abspath(args.package)
    out = args.out or os.path.join(package, "mesh_convergence")
    os.makedirs(out, exist_ok=True)
    analyses = []
    for a in args.analyses:
        a = "nsp" if a == "pushover" else a
        if a not in analyses:
            analyses.append(a)
    summary = dict(package=package, out=out, tol=args.tol, max_rungs=args.max_rungs,
                   dry_run=bool(args.dry_run), analyses={})
    rc = 0
    for a in analyses:
        try:
            summary["analyses"][a] = run_analysis_ladder(a, package, out, args=args)
        except Exception as ex:  # noqa: BLE001
            summary["analyses"][a] = dict(error=repr(ex))
            rc = 1
            print("!!", a, ex, flush=True)
    summary_path = os.path.join(out, "mesh_convergence_summary.json")
    json.dump(summary, open(summary_path, "w"), indent=2, default=str)
    print(">> summary", summary_path)
    return rc


def cli_main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mesh_convergence",
                                 description="Fibre mesh-convergence ladder (10% stop band by default)")
    ap.add_argument("--package", "-p", required=True)
    ap.add_argument("--analyses", nargs="+", default=["nsp", "nlrha", "ddm"],
                    choices=["nsp", "nlrha", "ddm", "pushover"])
    ap.add_argument("--out")
    ap.add_argument("--steltic-engine")
    ap.add_argument("--params")
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL)
    ap.add_argument("--max-rungs", type=int, default=DEFAULT_MAX_RUNGS)
    ap.add_argument("--site-class", default="D")
    ap.add_argument("--risk-category", choices=["I", "II", "III", "IV"])
    ap.add_argument("--n-records", type=int, default=11)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--dt", type=float, default=0.01)
    ap.add_argument("--dry-run", action="store_true")
    return main(ap.parse_args(argv))


# snl mesh-converge passes a namespace compatible with main()
def main_from_snl(a) -> int:
    return main(a)
