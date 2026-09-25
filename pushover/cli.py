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
    if getattr(args, "plasticity", None):
        os.environ["SNL_PLASTICITY"] = str(args.plasticity)
        prm.setdefault("numerics", {})["plasticity"] = args.plasticity
    if getattr(args, "member_nseg", None) is not None:
        os.environ["SNL_MEMBER_NSEG"] = str(args.member_nseg)
        prm.setdefault("numerics", {})["member_nseg"] = int(args.member_nseg)
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
    _copy_params(args.params or os.path.join(os.path.dirname(__file__), "hinge_params.json"),
                 os.path.join(out, "hinge_params_used.json"))
    print("wrote", html, "(%.0f s)" % (time.time() - t0))


def _copy_params(src, dst):
    """Copy the parameters the run used into the job folder, keeping any grounding already recorded.

    This copy is what used to erase `snl revise`'s work: the engineer ran Revise, was told to re-run
    the analyses to "pick the citation up", and the re-run put the un-annotated file straight back --
    a loop with no exit. The record itself lives in revise_evidence.json at the job root, which the
    run never writes, so it is simply re-applied here when it was taken against these same numbers.
    """
    import shutil as _sh
    from snl import grounding as _G
    _sh.copy(src, dst)
    try:
        prm = json.load(open(dst, encoding="utf-8"))
        st, ev = _G.state(prm, os.path.dirname(dst))
        if st == _G.GROUNDED and ev:
            prm["grounding"] = (prm.get("grounding") or {}) | {
                "asked": ev.get("asked"),
                "groups": {g: {k: r.get(k) for k in ("document", "citation", "section", "page")}
                           for g, r in (ev.get("groups") or {}).items() if r.get("grounded")},
                "clauses": {c: (r.get("citation") if isinstance(r, dict) else r)
                            for c, r in (ev.get("clauses") or {}).items()
                            if (r.get("grounded") if isinstance(r, dict) else r)},
                "evidence": _G.EVIDENCE, "reapplied_by": "pushover run"}
            cites = "; ".join(_G.citations(ev))
            prm["source"] = ("cited from the corpus on this PC %s -- %s. The numeric backbone values in this "
                             "file were NOT read out of those tables; `verified` stays false until "
                             "they are." % (ev.get("asked") or "", cites))
            json.dump(prm, open(dst, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
            print("params: grounding from %s re-applied (%d groups)" % (_G.EVIDENCE, len(_G.citations(ev))))
    except Exception as ex:                       # never fail a completed run over a provenance note
        print("params: could not re-apply grounding:", ex)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pushover")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("package"); r.add_argument("--out"); r.add_argument("--dirs", nargs="+", default=["X", "Y"])
    r.add_argument("--max-drift", type=float, default=0.08); r.add_argument("--site-class", default="D"); r.add_argument("--params")
    r.add_argument("--system")
    r.add_argument("--tail", default="auto", help="descending-branch escalation: auto (fine_step then arclength) | fine_step | arclength | none")
    r.add_argument("--post-cap-ratio", type=float, help="RUNG 3 (modelling change, user consent): fraction of `a` over which hinges descend to residual (default 0.15; try 0.5)")
    r.add_argument("--plasticity", default="fibre", choices=["fibre", "fiber", "imk"], help="fibre=distributed forceBeamColumn (default); imk=concentrated ModIMK")
    r.add_argument("--member-nseg", type=int, default=4, help="member subdivisions (default 4 for fibre)")
    i = sub.add_parser("inspect"); i.add_argument("package")
    args = ap.parse_args(argv)
    if args.cmd == "inspect":
        from . import package_reader as PR
        print(PR.summary(PR.load(args.package)))
    else:
        _run(args)


if __name__ == "__main__":
    main()
