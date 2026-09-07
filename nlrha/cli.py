"""cli.py -- Non Linear Dynamic Bot tool.

    python -m nlrha scale   <steltic package>  [--site-class D] [--n 11] [--out DIR]      # select + scale only (fast)
    python -m nlrha run     <steltic package>  [--pushover-dir DIR] [--n 11] [--dt 0.01] [--records 1-11] [--out DIR]
                                               [--xi 0.025] [--params hinge_params.json] [--free-vib 5]
Outputs (default <job>/nlrha/): nlrha_report.html, nlrha_package.json, gm_scaling.json, per-record peaks in the package.
"""
from __future__ import annotations
import argparse, json, os, sys, time


def _load(args):
    from pushover import package_reader as PR, hinge_models as HM, nonlinear_model as NM
    from . import model as MD
    pkg = PR.load(args.package); print(PR.summary(pkg))
    prm = HM.load_params(args.params)
    ch16 = json.load(open(os.path.join(os.path.dirname(__file__), "ch16_params.json")))
    b = pkg.basis
    if b.SDS is None or b.SD1 is None:
        sys.exit("design basis incomplete (SDS/SD1) -- add cfg.py to the package")
    TL = 8.0
    try:                                                  # TL from cfg if present
        import re
        src = open(os.path.join(str(pkg.root), "cfg.py")).read()
        m = re.search(r"TL\s*=\s*([0-9.]+)", src); TL = float(m.group(1)) if m else TL
    except Exception:
        pass
    return pkg, prm, ch16, TL


def _modal_and_range(pkg, prm, ch16, loads):
    from pushover import nonlinear_model as NM
    from . import model as MD, ground_motions as GM
    PG = NM.column_gravity_axials(pkg, loads)
    hinges, stats, elastic = MD.build(pkg, prm, ch16, PG)
    modal = MD.modal(pkg, 12)
    lo, hi = GM.period_range(modal["T1x"], modal["T1y"], modal["T90"], ch16["period_range"]["upper_factor"])
    print("[modal] T1x=%.3f T1y=%.3f T90=%s  cum mass X %.2f Y %.2f -> period range %.2f-%.2f s" % (modal["T1x"], modal["T1y"], modal["T90"], modal["cum_x"], modal["cum_y"], lo, hi))
    return PG, modal, lo, hi


def cmd_scale(args):
    from . import ground_motions as GM, model as MD
    pkg, prm, ch16, TL = _load(args)
    loads, gtab, split = MD.ch16_gravity(pkg, ch16)
    PG, modal, lo, hi = _modal_and_range(pkg, prm, ch16, loads)
    idx, recs = GM.library(args.records_set)
    gm, chosen = GM.select_and_scale(recs, pkg.basis.SDS, pkg.basis.SD1, TL, lo, hi, n_select=args.n)
    out = args.out or os.path.join(str(pkg.root), "nlrha"); os.makedirs(out, exist_ok=True)
    json.dump(dict(modal=modal, gm={k: v for k, v in gm.items()}), open(os.path.join(out, "gm_scaling.json"), "w"), indent=1, default=str)
    print("wrote", os.path.join(out, "gm_scaling.json"))


def cmd_report(args):
    """Rebuild the report from raw_results.pkl (after a report-code fix; no re-analysis)."""
    import pickle
    from . import acceptance as AC, report as RP
    pkg, prm, ch16, TL = _load(args)
    out = args.out or os.path.join(str(pkg.root), "nlrha")
    d = pickle.load(open(os.path.join(out, "raw_results.pkl"), "rb"))
    rc = AC.risk_category(pkg, args.risk_category); print("[16.4] Risk Category", rc.replace("_", "/"))
    acc = AC.evaluate(d["results"], pkg, d["ch16"], d["PG"], d["split"], 1.5 * pkg.basis.SDS, Ie=pkg.basis.Ie or 1.0, rc=rc)
    pdir = args.pushover_dir or os.path.join(str(pkg.root), "pushover"); pp = None
    if os.path.exists(os.path.join(pdir, "pushover_package.json")):
        pp = json.load(open(os.path.join(pdir, "pushover_package.json")))
    print("wrote", RP.write(out, pkg, d["ch16"], prm, d["gm"], d["results"], acc, d["gtab"], d["split"], d["modal"], sum(r["seconds"] for r in d["results"]), pushover_pkg=pp))
    _viewer(out, pkg, prm, d["ch16"], d["gm"], d["results"], acc, d["modal"], d["ch16"]["damping"].get("xi_used", 0.025), pp)


def cmd_run(args):
    from . import ground_motions as GM, model as MD, run as RN, acceptance as AC, report as RP
    from pushover import nonlinear_model as NM
    t0 = time.time()
    pkg, prm, ch16, TL = _load(args)
    if args.xi > ch16["damping"]["xi_max"]:
        sys.exit("xi %.3f exceeds the 16.3.5 cap of %.3f" % (args.xi, ch16["damping"]["xi_max"]))
    ch16["damping"]["xi_used"] = args.xi
    loads, gtab, split = MD.ch16_gravity(pkg, ch16)
    print("[gravity 16.3.2] sum D %.0f kip, sum 0.5L %.0f kip, ratio %.2f -> no-live case %s" % (split["sum_D"], split["sum_Lexp"], split["ratio"], "REQUIRED" if split["no_live_case_needed"] else "not required"))
    PG, modal, lo, hi = _modal_and_range(pkg, prm, ch16, loads)
    idx, recs = GM.library(args.records_set)
    gm, chosen = GM.select_and_scale(recs, pkg.basis.SDS, pkg.basis.SD1, TL, lo, hi, n_select=args.n)
    sel = range(1, len(chosen) + 1)
    if args.records:
        a, b = args.records.split("-"); sel = range(int(a), int(b) + 1)
    sample_brace = next((t for t, e in ((e["tag"], e) for e in pkg.model.elements) if pkg.schedule.get(t, {}).get("member") == "brace"), None)
    results = []
    jobs = [(str(pkg.root), args.params, ch16, PG, loads, chosen[i - 1], args.xi, args.dt, args.free_vib, sample_brace, args.integrator) for i in sel]
    ch16["damping"]["integrator"] = args.integrator; ch16["damping"]["dt_s"] = args.dt
    if args.parallel > 1:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(args.parallel) as pool:
            results = pool.map(RN.run_record_worker, jobs)
    else:
        for j in jobs:
            results.append(RN.run_record_worker(j))
    out = args.out or os.path.join(str(pkg.root), "nlrha"); os.makedirs(out, exist_ok=True)
    import pickle
    with open(os.path.join(out, "raw_results.pkl"), "wb") as f:            # never lose a 30-minute suite to a report bug
        pickle.dump(dict(results=results, gm=gm, modal=modal, gtab=gtab, split=split, PG=PG, ch16=ch16), f)
    SMS = 1.5 * pkg.basis.SDS
    rc = AC.risk_category(pkg, args.risk_category); print("[16.4] Risk Category", rc.replace("_", "/"))
    acc = AC.evaluate(results, pkg, ch16, PG, split, SMS, Ie=pkg.basis.Ie or 1.0, rc=rc)
    v = acc["verdict"]
    print("[16.4] unacceptable %d/%d (allowed %d) | mean drift max %.2f%% vs %.2f%% -> %s | deformation CP ok %s valid-range ok %s | force-controlled ok %s | OVERALL %s"
          % (v["n_unacceptable"], v["n_records"], v["unacceptable_allowed"], 100 * (v["mean_drift_max"] or 0), 100 * acc["limits"]["mean_limit"], v["mean_drift_ok"],
             v["deformation_ok"], v["valid_range_ok"], v["force_controlled_ok"], "ACCEPTABLE" if v["overall"] else "NOT ACCEPTABLE"))
    pp = None
    pdir = args.pushover_dir or os.path.join(str(pkg.root), "pushover")
    if os.path.exists(os.path.join(pdir, "pushover_package.json")):
        pp = json.load(open(os.path.join(pdir, "pushover_package.json")))
    html = RP.write(out, pkg, ch16, prm, gm, results, acc, gtab, split, modal, time.time() - t0, pushover_pkg=pp)
    print("wrote", html, "(%.0f s)" % (time.time() - t0))
    _viewer(out, pkg, prm, ch16, gm, results, acc, modal, args.xi, pp)


def _viewer(out, pkg, prm, ch16, gm, results, acc, modal, xi, pp):
    """nlrha_viewer_3d.html (Steltic viewer bundle). Never lets a viewer problem hide the report."""
    try:
        from . import viewer3d as V3
        print("wrote", V3.write(out, pkg, prm, ch16, gm, results, acc, modal, xi, pushover_pkg=pp))
    except Exception as ex:                                            # noqa: BLE001
        print("[viewer] skipped:", ex)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nlrha"); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scale", "run", "report"):
        p = sub.add_parser(name); p.add_argument("package"); p.add_argument("--out"); p.add_argument("--n", type=int, default=11)
        p.add_argument("--params"); p.add_argument("--records-set"); p.add_argument("--site-class", default="D")
        p.add_argument("--risk-category", help="I, II, III or IV (default: read from cfg.py, else from Ie)")
        if name == "report":
            p.add_argument("--pushover-dir")
        if name == "run":
            p.add_argument("--pushover-dir"); p.add_argument("--dt", type=float, default=0.02); p.add_argument("--records")
            p.add_argument("--xi", type=float, default=0.025); p.add_argument("--free-vib", type=float, default=5.0)
            p.add_argument("--parallel", type=int, default=1, help="worker processes (one OpenSees instance each)")
            p.add_argument("--integrator", default="hht", choices=["hht", "newmark"], help="HHT alpha=0.9 (default; damps spurious high modes) or Newmark average acceleration")
    args = ap.parse_args(argv)
    {"scale": cmd_scale, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
