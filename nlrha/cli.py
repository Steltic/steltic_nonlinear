"""cli.py -- Non Linear Dynamic Bot tool.

    python -m nlrha scale   <steltic package>  [--site-class D] [--n 11] [--out DIR]      # select + scale only (fast)
    python -m nlrha run     <steltic package>  [--pushover-dir DIR] [--n 11] [--dt 0.01] [--records 1-11] [--only-records 4] [--out DIR]
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


def _library(args):
    """The record library: the shipped set, or the sets / user folders named by --records-set (several allowed)."""
    dirs = getattr(args, "records_set", None) or []
    if isinstance(dirs, str):
        dirs = [dirs]
    if not dirs:
        from . import ground_motions as GM
        idx, recs = GM.library(None)
        return [dict(dir=None, set=idx.get("set"), n=len(recs))], recs
    from . import site_hazard as SH
    return SH.library_from([os.path.abspath(d) for d in dirs])


def _target(args, pkg):
    """(target tuple or None, deagg or None, pulse_fraction, hazard dict or None) from --target / --site-hazard."""
    kind = getattr(args, "target", None) or "code"
    if kind == "code":
        return None, None, float(getattr(args, "pulse_fraction", 0) or 0), None
    from . import site_hazard as SH
    path = getattr(args, "site_hazard", None) or os.path.join(str(pkg.root), "nlrha", "site_hazard.json")
    if not os.path.exists(path):
        sys.exit("--target %s needs a site hazard file: run `python -m nlrha hazard <package> --lat .. --lon ..` first (looked for %s)" % (kind, path))
    hz = json.load(open(path, encoding="utf-8"))
    cs_i = 0
    if kind == "cs" and getattr(args, "cs_period", None):
        cs_i = min(range(len(hz["targets"]["cs"])), key=lambda i: abs(hz["targets"]["cs"][i]["T_star"] - float(args.cs_period)))
    tgt = SH.target_from_hazard(hz, kind, cs_i)
    key = "%.3f" % hz["targets"]["cs"][cs_i]["T_star"] if hz["targets"].get("cs") else None
    deagg = (hz.get("deagg") or {}).get(key) if key else None
    pf = getattr(args, "pulse_fraction", None)
    pf = float(pf) if pf is not None else float((hz.get("near_fault") or {}).get("pulse_fraction") or 0.0)
    print("[target] %s; disaggregation %s; pulse share %.0f%%" % (tgt[2], ("M %.2f R %.0f km eps %.2f" % (deagg["mean"]["M"], deagg["mean"]["R_km"], deagg["mean"]["eps"])) if deagg else "none", 100 * pf))
    return tgt, deagg, pf, hz


def cmd_hazard(args):
    """Site-specific hazard: USGS design maps + disaggregation -> nlrha/site_hazard.json (+ site_hazard.html)."""
    from . import site_hazard as SH, model as MD
    pkg, prm, ch16, TL = _load(args)
    if args.t1:
        T1x = T1y = float(args.t1); T90 = None
    else:
        loads, gtab, split = MD.ch16_gravity(pkg, ch16)
        PG, modal, lo, hi = _modal_and_range(pkg, prm, ch16, loads)
        T1x, T1y, T90 = modal["T1x"], modal["T1y"], modal["T90"]
    from . import ground_motions as GM
    lo, hi = GM.period_range(T1x, T1y, T90, ch16["period_range"]["upper_factor"])
    from . import acceptance as AC
    rc = AC.risk_category(pkg, args.risk_category)
    design = json.load(open(args.design_json)) if args.design_json else None
    deaggs = json.load(open(args.deagg_json)) if args.deagg_json else None
    sigma = json.load(open(args.sigma)) if args.sigma else None
    cps = [float(x) for x in (args.cs_period or [])] or None
    hz = SH.build_site_hazard(args.lat, args.lon, T1x, T1y, site_class=args.site_class, risk_category={"I_II": "II"}.get(rc, rc), vs30=args.vs30,
                              return_period=args.return_period, T_lower=lo, T_upper=hi, conditioning_periods=cps, sigma_model=sigma,
                              design=design, deaggs=deaggs, fetch=not args.offline)
    out = args.out or os.path.join(str(pkg.root), "nlrha"); os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "site_hazard.json")
    json.dump(hz, open(path, "w", encoding="utf-8"), indent=1)
    print("wrote", path)
    d = hz["design"]
    print("[hazard] SDS %.3f SD1 %.3f (package %.3f / %.3f) SMS %.3f SM1 %.3f TL %s SDC %s" % (d["sds"], d["sd1"], pkg.basis.SDS, pkg.basis.SD1, d["sms"], d["sm1"], d["tl"], d["sdc"]))
    for c in hz["targets"]["cs"]:
        print("[hazard] CS at T* %.2f s: M %.2f R %.1f km eps %.2f (%s, %d yr)" % (c["T_star"], c["M"] or 0, c["R_km"] or 0, c["eps"], c["imt"], c["return_period"]))
    if hz.get("envelope"):
        print("[hazard] CS envelope / MCE_R over %.2f-%.2f s: min %.2f, >= MCE_R over %.0f%% of the range" % (lo, hi, hz["envelope"]["min_ratio_to_mcer"], 100 * hz["envelope"]["covered_share"]))
    nf = hz["near_fault"]
    print("[hazard] near-fault: %s" % ("YES -- %s, pulse share %.0f%%" % (", ".join("%s (M %.1f, %.0f km, %.0f%%)" % (x["name"], x["M"], x["R_km"], x["contribution_pct"]) for x in nf["sources"][:4]), 100 * nf["pulse_fraction"]) if nf["near_fault"] else "no"))
    if abs(d["sds"] - (pkg.basis.SDS or 0)) > 0.05 or abs(d["sd1"] - (pkg.basis.SD1 or 0)) > 0.05:
        print("!! the package's SDS/SD1 differ from the USGS values for this site -- the linear design basis needs a second look")
    try:
        _hazard_html(out, hz, lo, hi)
    except Exception as ex:                                            # noqa: BLE001
        print("[hazard] figure skipped:", ex)


def _hazard_html(out, hz, lo, hi):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from . import site_hazard as SH, report as RP
    import numpy as np
    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    t = hz["targets"]; mp = t["mcer_multi_period"]
    ax.plot([max(x, 0.01) for x in mp["periods"]], mp["sa"], color="#b3261e", lw=2.2, label="multi-period MCE$_R$ (USGS, 16.2.1.1)")
    d = hz["design"]
    if d.get("tl") and d.get("sds"):
        from . import ground_motions as GM
        T = np.geomspace(0.02, 10, 120); ax.plot(T, GM.target_mcer(T, d["sds"], d["sd1"], d["tl"]), color="#b3261e", lw=1, ls="--", label="1.5 x two-period design spectrum (11.4.6)")
    for c in t["cs"]:
        ax.plot(c["periods"], c["sa"], lw=1.8, label="CS at T* = %.2f s (M %.1f, R %.0f km, ε %.2f)" % (c["T_star"], c["M"] or 0, c["R_km"] or 0, c["eps"]))
        ax.axvline(c["T_star"], color="#999", lw=0.7, ls=":")
    ax.axvspan(lo, hi, color="#f0ad4e", alpha=0.15, label="period range 16.2.3.1: %.2f–%.2f s" % (lo, hi))
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("period (s)"); ax.set_ylabel("Sa (g), 5% damped, maximum direction"); ax.grid(alpha=.3, which="both"); ax.legend(fontsize=7.5)
    ax.set_title("Site-specific targets for the Chapter 16 suite", fontsize=11)
    png = RP._png(fig)
    html = ("<style>%s</style><title>Site hazard</title><h1>Site-specific hazard for the Chapter 16 suite</h1>%s<figure><img src='%s'><figcaption>Targets: the USGS multi-period MCE<sub>R</sub> spectrum (Method 1) and the conditional spectra at the conditioning period(s) (Method 2, 16.2.1.2).</figcaption></figure>"
            "<p>Sources: %s.</p><p class='note'>The conditional spectra use a constant-ε reading of the disaggregation and the σ<sub>ln</sub>(T) curve recorded in site_hazard.json (%s); at T* the CS equals the MCE<sub>R</sub> regardless of σ.</p>"
            % (RP.CSS, SH.hazard_summary_html(hz), png, "; ".join(hz["sources"]), hz["sigma_model"]["source"]))
    open(os.path.join(out, "site_hazard.html"), "w", encoding="utf-8").write(html)
    print("wrote", os.path.join(out, "site_hazard.html"))


def cmd_criteria(args):
    from . import design_criteria as DC
    docx, html = DC.write(args.package, out=args.out, project=args.project, engineer=args.engineer, reviewer=args.reviewer, params_path=args.params, risk_category=args.risk_category)
    print("wrote", docx); print("wrote", html)


def cmd_library(args):
    from . import site_hazard as SH
    idx = SH.scan_folder(args.folder, write_index=True)
    known = sum(1 for r in idx["records"] if r.get("M") is not None)
    print("%s: %d pairs indexed (%d with M / R metadata, %d flagged pulse) -> %s" % (args.folder, len(idx["records"]), known, sum(1 for r in idx["records"] if r.get("pulse")), os.path.join(args.folder, "index.json")))


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
    sets, recs = _library(args)
    tgt, deagg, pf, hz = _target(args, pkg)
    gm, chosen = GM.select_and_scale(recs, pkg.basis.SDS, pkg.basis.SD1, TL, lo, hi, n_select=args.n, target=tgt, deagg=deagg, pulse_fraction=pf, sf_bounds=_sf_bounds(args), sets=sets)
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
    if getattr(args, "member_nseg", None) is not None:
        os.environ["SNL_MEMBER_NSEG"] = str(args.member_nseg)
    if getattr(args, "plasticity", None):
        os.environ["SNL_PLASTICITY"] = str(args.plasticity)
    pkg, prm, ch16, TL = _load(args)
    if getattr(args, "plasticity", None):
        prm.setdefault("numerics", {})["plasticity"] = args.plasticity
    if getattr(args, "member_nseg", None) is not None:
        prm.setdefault("numerics", {})["member_nseg"] = int(args.member_nseg)
    if args.xi > ch16["damping"]["xi_max"]:
        sys.exit("xi %.3f exceeds the 16.3.5 cap of %.3f" % (args.xi, ch16["damping"]["xi_max"]))
    ch16["damping"]["xi_used"] = args.xi
    loads, gtab, split = MD.ch16_gravity(pkg, ch16)
    print("[gravity 16.3.2] sum D %.0f kip, sum 0.5L %.0f kip, ratio %.2f -> no-live case %s" % (split["sum_D"], split["sum_Lexp"], split["ratio"], "REQUIRED" if split["no_live_case_needed"] else "not required"))
    PG, modal, lo, hi = _modal_and_range(pkg, prm, ch16, loads)
    sets, recs = _library(args)
    tgt, deagg, pf, hz = _target(args, pkg)
    gm, chosen = GM.select_and_scale(recs, pkg.basis.SDS, pkg.basis.SD1, TL, lo, hi, n_select=args.n, target=tgt, deagg=deagg, pulse_fraction=pf, sf_bounds=_sf_bounds(args), sets=sets)
    sel = range(1, len(chosen) + 1)
    only = getattr(args, "only_records", None)
    if only:
        sel = [int(x) for x in str(only).replace(" ", "").split(",") if x]
        bad = [i for i in sel if i < 1 or i > len(chosen)]
        if bad:
            sys.exit("--only-records out of range for --n %d suite: %s" % (args.n, bad))
    elif args.records:
        a, b = args.records.split("-"); sel = range(int(a), int(b) + 1)
    sample_brace = next((t for t, e in ((e["tag"], e) for e in pkg.model.elements) if pkg.schedule.get(t, {}).get("member") == "brace"), None)
    results = []
    jobs = [(str(pkg.root), args.params, ch16, PG, loads, chosen[i - 1], args.xi, args.dt, args.free_vib, sample_brace, args.integrator) for i in sel]
    ch16["damping"]["integrator"] = args.integrator; ch16["damping"]["dt_s"] = args.dt
    early_nc = int(getattr(args, "early_abort_nc", 0) or 0)
    early_aborted = False
    if args.parallel > 1 and early_nc <= 0:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(args.parallel) as pool:
            results = pool.map(RN.run_record_worker, jobs)
    elif args.parallel > 1 and early_nc > 0:
        import multiprocessing as mp
        with mp.get_context("spawn").Pool(args.parallel) as pool:
            for out in pool.imap(RN.run_record_worker, jobs):
                results.append(out)
                n_nc = sum(1 for r in results if not r.get("converged"))
                if n_nc >= early_nc:
                    early_aborted = True
                    print("[nlrha] early abort: %d NC (≥%d) — abandoning remaining records; next mesh/method"
                          % (n_nc, early_nc), flush=True)
                    pool.terminate()
                    break
    else:
        for j in jobs:
            results.append(RN.run_record_worker(j))
            if early_nc > 0:
                n_nc = sum(1 for r in results if not r.get("converged"))
                if n_nc >= early_nc:
                    early_aborted = True
                    print("[nlrha] early abort: %d NC (≥%d) — abandoning remaining records; next mesh/method"
                          % (n_nc, early_nc), flush=True)
                    break
    out = args.out or os.path.join(str(pkg.root), "nlrha"); os.makedirs(out, exist_ok=True)
    import pickle
    with open(os.path.join(out, "raw_results.pkl"), "wb") as f:            # never lose a 30-minute suite to a report bug
        pickle.dump(dict(results=results, gm=gm, modal=modal, gtab=gtab, split=split, PG=PG, ch16=ch16), f)
    SMS = 1.5 * pkg.basis.SDS
    rc = AC.risk_category(pkg, args.risk_category); print("[16.4] Risk Category", rc.replace("_", "/"))
    acc = AC.evaluate(results, pkg, ch16, PG, split, SMS, Ie=pkg.basis.Ie or 1.0, rc=rc)
    acc.setdefault("meta", {})["early_aborted"] = bool(early_aborted)
    acc["meta"]["early_abort_nc"] = early_nc
    acc["meta"]["n_nc"] = sum(1 for r in results if not r.get("converged"))
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


def _sf_bounds(args):
    v = getattr(args, "sf_bounds", None)
    if not v:
        return None
    lo, hi = str(v).split("-") if "-" in str(v) else str(v).split(",")
    return (float(lo), float(hi))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nlrha"); sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scale", "run", "report", "hazard"):
        p = sub.add_parser(name); p.add_argument("package"); p.add_argument("--out"); p.add_argument("--n", type=int, default=11)
        p.add_argument("--params"); p.add_argument("--site-class", default="D")
        p.add_argument("--records-set", nargs="*", default=None, help="record set folder(s): indexed sets (index.json) and/or user folders of PEER .AT2 / CSV pairs (indexed on the fly); default = the shipped FEMA P-695 far-field set")
        p.add_argument("--risk-category", help="I, II, III or IV (default: read from cfg.py, else from Ie)")
        if name in ("scale", "run"):
            p.add_argument("--target", default="code", choices=["code", "mcer", "cs"], help="scaling target: code = 1.5 x the package's design spectrum; mcer = the USGS multi-period MCE_R; cs = conditional spectrum at T* (both need nlrha hazard first)")
            p.add_argument("--site-hazard", help="site_hazard.json written by `nlrha hazard` (default <package>/nlrha/site_hazard.json)")
            p.add_argument("--cs-period", type=float, help="which conditioning period of the site hazard to use with --target cs (default: the first)")
            p.add_argument("--pulse-fraction", type=float, default=None, help="share of the suite reserved for pulse-type records (default: the near-fault screen of the site hazard, else 0)")
            p.add_argument("--sf-bounds", help="keep only records whose shape-fit scale factor lies in lo-hi (e.g. 0.25-4)")
        if name == "hazard":
            p.add_argument("--lat", type=float, required=True); p.add_argument("--lon", type=float, required=True)
            p.add_argument("--vs30", type=float, help="Vs30 for the disaggregation (default: the USGS site-class value)")
            p.add_argument("--return-period", type=int, default=2475, help="disaggregation return period in years (default 2475)")
            p.add_argument("--cs-period", nargs="*", help="conditioning period(s) for the conditional spectra (default: the longer first-mode period)")
            p.add_argument("--t1", type=float, help="skip the model build and use this first-mode period (s)")
            p.add_argument("--sigma", help="JSON {periods:[...], sigma:[...]} overriding the built-in sigma_ln(T) curve")
            p.add_argument("--offline", action="store_true", help="do not call the USGS services; use --design-json / --deagg-json")
            p.add_argument("--design-json", help="saved response of the USGS ASCE 7-22 service (fetch_design output)")
            p.add_argument("--deagg-json", help="saved disaggregations {\"T*\": fetch_disagg output}")
        if name == "report":
            p.add_argument("--pushover-dir")
        if name == "run":
            p.add_argument("--pushover-dir"); p.add_argument("--dt", type=float, default=0.02); p.add_argument("--records")
            p.add_argument("--only-records", help="comma-separated 1-based suite indices to run (e.g. 4 or 4,5); preferred for Gate B FC refine")
            p.add_argument("--xi", type=float, default=0.025); p.add_argument("--free-vib", type=float, default=5.0)
            p.add_argument("--parallel", type=int, default=1, help="worker processes (one OpenSees instance each)")
            p.add_argument("--integrator", default="hht", choices=["hht", "newmark"], help="HHT alpha=0.9 (default; damps spurious high modes) or Newmark average acceleration")
            p.add_argument("--member-nseg", type=int, default=None, help="member subdivisions (default: 4 fibre / 1 imk)")
            p.add_argument("--plasticity", default="imk", choices=["fibre", "fiber", "imk"], help="product default imk=ModIMK; fibre=distributed forceBeamColumn (ladder climb)")
            p.add_argument("--early-abort-nc", type=int, default=2, help="abandon remaining records once N are NC (0=disable; product default 2)")
    lib = sub.add_parser("library", help="index a folder of PEER .AT2 / CSV record pairs (writes index.json; reads PEER _SearchResults.csv metadata when present)")
    lib.add_argument("folder")
    cr = sub.add_parser("criteria", help="draft the 16.1.4 design criteria document (docx + html) from the package and whatever analyses exist")
    cr.add_argument("package"); cr.add_argument("--out", help="folder for design_criteria_16_1_4.docx/.html (default: the job folder)")
    cr.add_argument("--project"); cr.add_argument("--engineer"); cr.add_argument("--reviewer"); cr.add_argument("--params"); cr.add_argument("--risk-category")
    args = ap.parse_args(argv)
    {"scale": cmd_scale, "run": cmd_run, "report": cmd_report, "hazard": cmd_hazard, "library": cmd_library, "criteria": cmd_criteria}[args.cmd](args)


if __name__ == "__main__":
    main()
