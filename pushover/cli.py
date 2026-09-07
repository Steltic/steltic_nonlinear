"""cli.py -- one command turns a Steltic design package into a pushover supplement.

    python -m pushover run  <package.zip | job folder>  [--out DIR] [--dirs X Y] [--max-drift 0.08]
                                                        [--site-class D] [--params hinge_params.json]
    python -m pushover inspect <package.zip | job folder>        # read-only: what is in the package

Outputs (in --out, default <job>/pushover/): pushover_report.html, pushover_package.json, curve_X.csv, curve_Y.csv,
hinge_params_used.json, run_log.txt.
"""
from __future__ import annotations
import argparse, json, os, shutil, sys, time


def _run(args):
    from . import package_reader as PR, nonlinear_model as NM, hinge_models as HM, postprocess as PP, report_supplement as RS
    t0 = time.time()
    pkg = PR.load(args.package)
    print(PR.summary(pkg))
    if args.system:
        pkg.basis.system = args.system; pkg.basis.sources["system"] = "--system override"
    missing = [k for k in ("SDS", "SD1", "W_kip") if getattr(pkg.basis, k) is None]
    if missing:
        sys.exit("design basis incomplete (%s) -- add cfg.py to the package or pass --sds/--sd1" % missing)
    prm = HM.load_params(args.params)
    if args.post_cap_ratio is not None:
        prm.setdefault("numerics", {})["post_cap_ratio"] = args.post_cap_ratio
        prm["numerics"]["post_cap_ratio_overridden"] = True
        print("!! post_cap_ratio overridden to %.2f -- a MODELLING CHANGE; the report will disclose it" % args.post_cap_ratio)
    strategies = {"auto": ("fine_step", "arclength"), "fine_step": ("fine_step",), "arclength": ("arclength",), "none": ()}[args.tail]
    if not prm.get("verified"):
        print("!! hinge_params.json is UNVERIFIED -- the report will carry the red banner until the spec lookup is done")
    out = args.out or os.path.join(str(pkg.root), "pushover")
    os.makedirs(out, exist_ok=True)
    loads, gtable = NM.gravity_loads(pkg, prm)
    PG = NM.column_gravity_axials(pkg, loads)
    runs, results, stats = {}, {}, None
    for d in args.dirs:
        hinges, stats = NM.build_nonlinear(pkg, prm, PG)
        run = NM.pushover(pkg, hinges, d, loads, prm, max_roof_drift=args.max_drift, gravity_table=gtable, tail_strategies=strategies)
        nsp = {lvl: PP.nsp_target(run, pkg.basis, prm, f, args.site_class) for lvl, f in prm["nsp"]["hazard_levels"].items()}
        p695 = PP.p695_factors(run, pkg.basis, nsp["BSE-1N"])
        acc = {lvl: PP.acceptance(run, hinges, n["target_disp_in"], lvl) for lvl, n in nsp.items()}
        runs[d] = run; results[d] = dict(nsp=nsp, p695=p695, acc=acc, hinges=hinges)
        for lvl, n in nsp.items():
            print("  [%s %s] Te=%.2fs Sa=%.3fg C0=%.2f C1=%.2f C2=%.2f -> dt=%.2f in (%.2f%% H) mu_str=%.2f mu_max=%.2f NSP ok=%s reached1.5=%s | worst D/C IO %.2f LS %.2f CP %.2f"
                  % (d, lvl, n["Te"], n["Sa"], n["C0"], n["C1"], n["C2"], n["target_disp_in"], 100 * n["target_over_H"], n["mu_strength"],
                     n["mu_max"], n["nsp_permitted"], n["reached_150pct"], acc[lvl]["worst_DC"]["IO"], acc[lvl]["worst_DC"]["LS"], acc[lvl]["worst_DC"]["CP"]))
        print("  [%s P-695] Vmax=%.0f kip Omega=%s (Om0=%s) mu_T=%.2f (%s)" % (d, p695["Vmax_kip"], "%.2f" % p695["Omega"] if p695["Omega"] else "n/a",
                                                                          pkg.basis.Om0, p695["mu_T"], p695["delta_u_basis"]))
        t = run["tail"]
        print("  [%s TAIL] status=%s tried=%s max(theta_pl/a)=%.2f max(theta_pl/b)=%.2f -- %s" % (d, t["status"], [x["strategy"] for x in t["tried"]],
              run["rec"]["a_ratio"][-1], run["rec"]["b_ratio"][-1], t["message"]))
        if t["status"] in ("lower_bound", "max_drift"):
            print("  >> ACTION FOR THE BOT: descending branch not captured. Ask the user before rung 3 "
                  "(--post-cap-ratio 0.5 = modelling change) or a larger --max-drift; see the skill's descending-branch protocol.")
    html = RS.write(out, pkg, prm, runs, results, gtable, stats, time.time() - t0)
    try:
        from . import viewer3d as V3
        print("viewer", V3.write(out, pkg, prm, runs, results, stats))
    except Exception as ex:
        print("viewer failed:", ex)
    shutil.copy(args.params or os.path.join(os.path.dirname(__file__), "hinge_params.json"), os.path.join(out, "hinge_params_used.json"))
    print("wrote", html, "(%.0f s)" % (time.time() - t0))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pushover")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("package"); r.add_argument("--out"); r.add_argument("--dirs", nargs="+", default=["X", "Y"])
    r.add_argument("--max-drift", type=float, default=0.08); r.add_argument("--site-class", default="D"); r.add_argument("--params")
    r.add_argument("--system")
    r.add_argument("--tail", default="auto", help="descending-branch escalation: auto (fine_step then arclength) | fine_step | arclength | none")
    r.add_argument("--post-cap-ratio", type=float, help="RUNG 3 (modelling change, user consent): fraction of `a` over which hinges descend to residual (default 0.15; try 0.5)")
    i = sub.add_parser("inspect"); i.add_argument("package")
    args = ap.parse_args(argv)
    if args.cmd == "inspect":
        from . import package_reader as PR
        print(PR.summary(PR.load(args.package)))
    else:
        _run(args)


if __name__ == "__main__":
    main()
