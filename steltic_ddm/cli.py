"""
cli.py -- `python -m steltic_ddm run <job_dir> [options]`

Runs the whole DDM flow on a finished Steltic job folder:
  ingest -> transfer gate -> combination set -> GMNIA sweeps (parallel workers) -> phi_s policy
  -> sensitivity (optional) -> ddm_report.html + ddm_results.json + ddm_analysis block + model_gmnia.py
  python -m steltic_ddm report <job>   re-applies the current phi_s policy to ddm_results.json and rebuilds report, block, viewer
"""
import argparse, json, multiprocessing as mp, os, sys, time

from . import ingest, loads, phi_s, report_ddm, solver, transfer_gate, imperfections
from .model_gmnia import GMNIAModel


def _worker(args):
    """One sweep in its own process (openseespy is a process singleton)."""
    job, engine_dir, combo_label, imp, opts = args
    if engine_dir and engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)
    import io, contextlib
    nm = ingest.load_package(job, engine_dir)
    cfg = nm.cfg
    cases = loads.steltic_combos(cfg, nm=nm)
    combo = [c for c in cases if c[0] == combo_label][0]
    from . import portal_adapter as PA
    if PA.is_portal(cfg):
        lab = combo[0]
        if "S_bal" in lab or "S_unb" in lab or "+0.3S" in lab or lab.endswith("+0.3S"):
            cfg["_portal_roof_psf"] = PA._snow_ps(cfg)
            if "S_unb_L" in lab: cfg["_portal_snow_case"] = "S_unb_L"
            elif "S_unb_R" in lab: cfg["_portal_snow_case"] = "S_unb_R"
            else: cfg["_portal_snow_case"] = "S_bal"
        else:
            cfg["_portal_roof_psf"] = float(cfg.get("Lr", 20.0))
            cfg["_portal_snow_case"] = None
    pres = loads.present_sets(nm)
    g = GMNIAModel(nm, cfg, nsub=tuple(opts["nsub"]), residual=opts["residual"], Fy=opts.get("Fy"),
                   hardening=opts["hardening"], fast=opts["fast"], nip=opts["nip"],
                   out_of_plumb=(imp["dir"], imp["psi"]), bow=opts["bow"], bow_sign=imp["bow_sign"], brace_bow=opts["bow"])
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        res = solver.sweep(g, combo, pres, dlam=opts["dlam"], max_steps=opts["max_steps"], verbose=True, time_limit=opts["time_limit"])
    cls = solver.classify(res, g)
    # per-role/section member state at the peak
    yr = res["snapshot"].get("yield_ratio", {})
    per_member = {}
    for e in g.elems:
        per_member[e["mtag"]] = max(per_member.get(e["mtag"], 0.0), yr.get(e["tag"], 0.0))
    state = {}
    for m in nm.members:
        key = "%s|%s" % (m.role, m.section)
        d = state.setdefault(key, dict(role=m.role, section=m.section, n=0, ratio=0.0, yielded=0, hinges=0, buckled=0))
        d["n"] += 1
        r = per_member.get(m.tag, 0.0)
        d["ratio"] = max(d["ratio"], r); d["yielded"] += int(r >= 1.0); d["hinges"] += int(r >= 3.0)
        if m.kind == "brace" and m.tag in cls["buckled_braces"]:
            d["buckled"] += 1
    res_small = {k: v for k, v in res.items() if k != "snapshot"}
    snap = res["snapshot"]
    # viewer data at the peak: master displacements (rigid-diaphragm shape), per-member yield ratio, brace states
    res_small["snapshot"] = {"drifts": snap.get("drifts"), "step": snap.get("step"),
                             "disp": {str(t): [round(v, 4) for v in d] for t, d in (snap.get("disp") or {}).items()},
                             "member_ratio": {str(t): round(r, 3) for t, r in per_member.items() if r > 0.05},
                             "braces": {str(t): dict(N=round(b["N"], 1), offset=round(b["offset"], 3), buckled=bool(b["buckled"]))
                                        for t, b in (snap.get("braces") or {}).items()}}
    res_small["hist"] = [(round(a, 4), round(b, 4)) for a, b in res["hist"]]
    res_small["frames"] = [dict(step=f["step"], lam=round(f["lam"], 4), d=round(f["d"], 3),
                                disp={str(t): [round(v, 4) for v in dd] for t, dd in f["disp"].items()},
                                mem={str(m): [round(r, 2), round(fr, 2)] for m, (r, fr) in f["mem"].items()},
                                buckled=[int(t) for t in f["buckled"]]) for f in res.get("frames", [])]
    return dict(label=combo_label, imp=imp["tag"], res=res_small, cls=cls, state=state, section_log=g.builder.log)


def run(args):
    t0 = time.time()
    job = os.path.abspath(args.job_dir)
    engine_dir = args.steltic_engine or os.environ.get("STELTIC_ENGINE_DIR")
    out_dir = args.out or job
    os.makedirs(out_dir, exist_ok=True)
    print(">> ingest", job)
    nm = ingest.load_package(job, engine_dir)
    cfg = nm.cfg
    print("   ", json.dumps(ingest.summary(nm), default=str)[:400])
    from . import portal_adapter as PA
    portal = PA.is_portal(cfg)
    cases = loads.steltic_combos(cfg, nm=nm)
    kept = loads.prune(cases, policy=args.combos, torsion=args.torsion, include_om0=args.om0)
    if args.only:
        kept = [c for c in kept if any(s in c[0] for s in args.only)]
    print(">> %d combinations from Steltic, %d selected%s" % (len(cases), len(kept), " [portal CFS-P]" if portal else ""))

    # transfer gate
    if portal:
        gate = PA.transfer_gate_portal(nm, cfg, tol=args.gate_tol, nsub=tuple(args.nsub))
    else:
        latx = [c for c in cases if "EX+t+" in c[0] and c[1] > 1.0][0][4]
        laty = [c for c in cases if "EY+t+" in c[0] and c[1] > 1.0][0][4]
        gate = transfer_gate.run(nm, cfg, latx, laty, tol=args.gate_tol)
    for r in gate["rows"]:
        print("   gate %-36s steltic %.4f gmnia %.4f ratio %.3f %s" % (r["quantity"], r["steltic"], r["gmnia"], r["ratio"], "ok" if r["ok"] else "FAIL"))
    if not gate["ok"] and not args.force:
        print("!! transfer gate FAILED --", gate["hint"]); sys.exit(2)

    opts = dict(nsub=list(args.nsub), residual=args.residual, Fy=args.fy, hardening=args.hardening, fast=args.fast, nip=args.nip,
                bow=args.bow, psi=args.psi, dlam=args.dlam, max_steps=args.max_steps, time_limit=args.time_limit)
    # task list: (combo, imperfection case)
    tasks = []
    for c in kept:
        for imp in imperfections.cases_for(c, gravity_dirs=args.gravity_dirs, psi=args.psi):
            tasks.append((job, engine_dir, c[0], imp, opts))
    print(">> %d GMNIA sweeps on %d worker(s)" % (len(tasks), args.workers), flush=True)
    results = []
    if args.workers > 1:
        with mp.get_context("spawn").Pool(args.workers) as pool:
            for r in pool.imap_unordered(_worker, tasks):
                results.append(r)
                print("   done %-40s imp %-3s lambda_u %.3f  (%d steps, %.0f s) %s" % (r["label"][:40], r["imp"], r["res"]["lambda_u"], r["res"]["steps"], r["res"]["seconds"], r["cls"]["mechanism"][:60]), flush=True)
    else:
        for t in tasks:
            r = _worker(t); results.append(r)
            print("   done %-40s imp %-3s lambda_u %.3f  (%d steps, %.0f s) %s" % (r["label"][:40], r["imp"], r["res"]["lambda_u"], r["res"]["steps"], r["res"]["seconds"], r["cls"]["mechanism"][:60]), flush=True)

    # keep the governing imperfection case per combination
    by_label = {}
    for r in results:
        if r["label"] not in by_label or r["res"]["lambda_u"] < by_label[r["label"]]["res"]["lambda_u"]:
            by_label[r["label"]] = r
    R = cfg.get("seis", {}).get("R")
    hss = any(m.section.upper().startswith("HSS") for m in nm.members if m.kind == "brace")
    rc = _rc(cfg, args.risk_category)
    print(">> Risk Category %s -> phi_s target-reliability rows (gravity beta_T %.2f, lateral %.2f)" % (rc, phi_s.BETA_TARGET["gravity"][rc], phi_s.BETA_TARGET["lateral"][rc]))
    runs = []
    for c in kept:
        r = by_label.get(c[0])
        if not r:
            continue
        summ = loads.combo_summary(c)
        gov_braces = bool(r["cls"]["buckled_braces"]) or (r["cls"]["mechanism"].startswith("brace"))
        mat = "CFS-P" if portal else "HR"
        ph = phi_s.choose(summ["kind"], R, r["cls"]["cls"], governed_by_braces=gov_braces, hss_braces=hss, material=mat, risk_category=rc)
        runs.append(dict(combo=c, summary=summ, res=r["res"], cls=r["cls"], phi=ph, check=phi_s.check(ph["phi_s"], r["res"]["lambda_u"]),
                         imp=r["imp"], state=r["state"]))
    # member table from the governing strength combination per group
    member_table = []
    groups = sorted({(m.role, m.section) for m in nm.members})
    dcmap = {}
    for m in (nm.calc_package or {}).get("members", []):
        if "inputs" in m:
            dcmap[(m["inputs"].get("role"), m["inputs"].get("section", "").upper())] = m.get("DC")
        else:
            role = m.get("role") or m.get("member") or m.get("id")
            sec = (m.get("section") or "").upper()
            dcmap[(role, sec)] = m.get("DC")
    for role, sec in groups:
        best = None
        for r in runs:
            st = r["state"].get("%s|%s" % (role, sec))
            if st and (best is None or st["ratio"] > best[0]["ratio"]):
                best = (st, r["combo"][0])
        if best:
            st, lab = best
            member_table.append(dict(role=role, section=sec, n=st["n"], DC=dcmap.get((role, sec.upper())), combo=lab,
                                     ratio=round(st["ratio"], 2), yielded=st["yielded"], hinges=st["hinges"], buckled=st["buckled"]))

    # sensitivity on the governing strength combination
    sens = []
    if args.sensitivity and runs:
        strength = [r for r in runs if r["phi"]["phi_s"] is not None]
        gov = min(strength, key=lambda r: r["phi"]["phi_s"] * r["res"]["lambda_u"]) if strength else runs[0]
        lab, ref = gov["combo"][0], gov["res"]["lambda_u"]
        variants = []
        o2 = dict(opts, residual="none"); variants.append(("no residual stress", dict(dir=gov["res"]["lateral"][0] or "X", psi=(gov["res"]["lateral"][1] or 1) * args.psi, bow_sign=gov["res"]["lateral"][1] or 1, tag="nom"), o2))
        o3 = dict(opts, bow=0.0); variants.append(("no member bow (L/1000 -> 0)", dict(dir=gov["res"]["lateral"][0] or "X", psi=(gov["res"]["lateral"][1] or 1) * args.psi, bow_sign=gov["res"]["lateral"][1] or 1, tag="nom"), o3))
        ldir, lsgn = gov["res"]["lateral"]
        if ldir:
            variants.append(("out-of-plumb AGAINST the lateral load", dict(dir=ldir, psi=-lsgn * args.psi, bow_sign=-lsgn, tag="opp"), opts))
        else:
            variants.append(("out-of-plumb -X", dict(dir="X", psi=-args.psi, bow_sign=-1, tag="-X"), opts))
        stasks = [(job, engine_dir, lab, imp, o) for _, imp, o in variants]
        sres = []
        if args.workers > 1:
            with mp.get_context("spawn").Pool(min(args.workers, len(stasks))) as pool:
                sres = list(pool.imap(_worker, stasks))
        else:
            sres = [_worker(t) for t in stasks]
        for (case, _, _), r in zip(variants, sres):
            sens.append(dict(case=case, combo=lab, lambda_u=round(r["res"]["lambda_u"], 3), ref=ref, mechanism=r["cls"]["mechanism"]))
            print("   sensitivity %-34s lambda_u %.3f (ref %.3f)" % (case, r["res"]["lambda_u"], ref), flush=True)

    # exports
    opts_rep = dict(opts, nsub=tuple(opts["nsub"]), section_log=(results[0]["section_log"] if results else []), Fy=(args.fy or cfg.get("Fy", 50.0)),
                    gravity_dirs={"one": "+X only", "two": "+X and +Y", "all": "±X and ±Y"}[args.gravity_dirs], risk_category=rc, n_cases=len(cases))
    rep, block = _finish(job, out_dir, nm, cfg, gate, runs, sens, opts_rep, member_table, t0)
    if not args.no_block:
        report_ddm.write_block(job, block)
    # model export (nominal, +X lean)
    try:
        g = GMNIAModel(nm, cfg, nsub=tuple(args.nsub), residual=args.residual, out_of_plumb=("X", args.psi), bow=args.bow, brace_bow=args.bow, fast=args.fast)
        g.export_py(os.path.join(out_dir, "model_gmnia.py"), header="%s (out-of-plumb +X H/%d, L/%d bows, %s residual)" % (nm.name, round(1 / args.psi), round(1 / args.bow), args.residual))
    except Exception as ex:
        print("   model export skipped:", ex)
    print(">> report:", rep)
    _viewer(out_dir, nm, gate, runs)
    print(">> elapsed %.0f s" % (time.time() - t0))
    return rep


def _rc(cfg, override=None):
    return override or cfg.get("risk_category") or {1.5: "IV", 1.25: "III"}.get(float(cfg.get("seis", {}).get("Ie", 1.0) or 1.0), "II")


def _notes(runs, n_cases):
    used = {r["phi"]["cls"] for r in runs if r["phi"]["cls"] in phi_s.TABLE}
    return [
        "Design check φ<sub>s</sub>·λ<sub>u</sub> ≥ 1.0 and modelling protocol: Zhang H., Shayan S., Rasmussen K.J.R., Ellingwood B.R., <i>System-based design of planar steel frames I &amp; II</i>, JCSR 123 (2016) 135–143, 154–161; Shayan, Rasmussen &amp; Zhang, JCSR 98 (2014) 167–177 (imperfections) and JCSR 101 (2014) 407–414 (residual stresses).",
        "φ<sub>s</sub> classes used: " + "; ".join("<b>%s</b> = %s at β<sub>T</sub> = %s [%s] — %s" % (k, ("%.2f" % v["phi"]) if v["phi"] is not None else "n/a", v["beta"], v["status"].upper(), v["desc"]) for k, v in phi_s.TABLE.items() if k in used) + ". Sources: " + "; ".join(phi_s.SOURCES[s] for s in sorted({src for k in used for src in phi_s.TABLE[k]["sources"]})) + ".",
        "US-code vehicle for design by inelastic analysis: AISC 360-22 Appendix 1 §1.3 (ductility limits §1.3.2; analysis requirements §1.3.3 — imperfections, residual stress / partial yielding, material). Retrieve the verbatim text through the Query file manager before citing clause numbers in a deliverable; this report does not quote the specification.",
        "Load combinations: ASCE 7-22 §2.3 set regenerated by Steltic <code>design_pipeline.combos(cfg)</code> — identical to the member design's cases (%d cases, %d analysed)." % (n_cases, len(runs)),
        "Steltic package conventions decoded: node tag k·10⁵ + i·100 + j; column strong axis from geomTransf 1/2; beam major release = <code>-releasey</code>; masses on diaphragm masters. Steltic's <code>model_opensees.py</code> contains the probe build followed by the real build (geomTransf recorded only once) — the parser keeps the last build.",
    ]


def _finish(job, out_dir, nm, cfg, gate, runs, sens, opts_rep, member_table, t0, elapsed=None):
    """ddm_report.html + ddm_analysis block + ddm_results.json from the assembled runs (shared by `run` and `report`)."""
    rep = report_ddm.build(out_dir, nm, cfg, gate, runs, sens, opts_rep, member_table, _notes(runs, opts_rep.get("n_cases", len(runs))))
    block = report_ddm.ddm_block(nm, gate, runs, sens, opts_rep, member_table)
    opts_json = {k: v for k, v in opts_rep.items() if k != "section_log"}
    opts_json["section_log"] = [list(x) for x in opts_rep.get("section_log", [])]
    json.dump(dict(job=job, options=opts_json, gate=gate, runs=[dict(label=r["combo"][0], imp=r["imp"], kind=r["summary"]["kind"], lambda_u=r["res"]["lambda_u"],
                                                                   first_yield=r["res"]["first_yield"], phi=r["phi"], check=r["check"], cls=r["cls"], hist=r["res"]["hist"],
                                                                   steps=r["res"]["steps"], fails=r["res"].get("fails"), lam_at_1p25d=r["res"].get("lam_at_1p25d"),
                                                                   seconds=r["res"]["seconds"], log=r["res"]["log"], state=r["state"],
                                                                   snapshot=r["res"]["snapshot"], control=r["res"].get("control"), lateral=r["res"].get("lateral"),
                                                                   d_at_max=r["res"].get("d_at_max"), frames=r["res"].get("frames", [])) for r in runs],
                   sensitivity=sens, member_table=member_table, elapsed_s=(elapsed if elapsed is not None else round(time.time() - t0))),
              open(os.path.join(out_dir, "ddm_results.json"), "w"), indent=1, default=str)
    return rep, block


def _runs_from_results(d):
    runs = []
    for r in d["runs"]:
        res = dict(lambda_u=r["lambda_u"], first_yield=r.get("first_yield"), hist=r["hist"], steps=r["steps"], fails=r.get("fails"), lam_at_1p25d=r.get("lam_at_1p25d"),
                   seconds=r["seconds"], snapshot=r.get("snapshot"), control=r.get("control"), lateral=r.get("lateral"), d_at_max=r.get("d_at_max"),
                   log=r.get("log", []), frames=r.get("frames", []))
        runs.append(dict(combo=(r["label"],), summary=dict(kind=r["kind"]), res=res, cls=r["cls"], phi=r["phi"], check=r["check"], imp=r["imp"], state=r["state"]))
    return runs


def report(args):
    from . import portal_adapter as PA
    """Rebuild ddm_report.html, the ddm_analysis block, ddm_results.json and the viewer from an existing ddm_results.json,
    re-applying the CURRENT phi_s policy (no re-analysis). Use after a phi_s.py update or to change the Risk Category row."""
    job = os.path.abspath(args.job_dir)
    engine_dir = args.steltic_engine or os.environ.get("STELTIC_ENGINE_DIR")
    out_dir = args.out or job
    nm = ingest.load_package(job, engine_dir)
    cfg = nm.cfg
    d = json.load(open(os.path.join(out_dir, "ddm_results.json")))
    runs = _runs_from_results(d)
    R = cfg.get("seis", {}).get("R")
    hss = any(m.section.upper().startswith("HSS") for m in nm.members if m.kind == "brace")
    rc = _rc(cfg, args.risk_category or d.get("options", {}).get("risk_category"))
    print(">> Risk Category %s -> phi_s target-reliability rows (gravity beta_T %.2f, lateral %.2f)" % (rc, phi_s.BETA_TARGET["gravity"][rc], phi_s.BETA_TARGET["lateral"][rc]))
    for r in runs:
        gov_braces = bool(r["cls"]["buckled_braces"]) or (r["cls"]["mechanism"].startswith("brace"))
        mat = "CFS-P" if PA.is_portal(cfg) else "HR"
        r["phi"] = phi_s.choose(r["summary"]["kind"], R, r["cls"]["cls"], governed_by_braces=gov_braces, hss_braces=hss, material=mat, risk_category=rc)
        r["check"] = phi_s.check(r["phi"]["phi_s"], r["res"]["lambda_u"])
        print("   %-40s lambda_u %.3f  %-7s phi_s %s  -> %s" % (r["combo"][0][:40], r["res"]["lambda_u"], r["phi"]["cls"], r["phi"]["phi_s"], r["check"][1]))
    opts_rep = dict(d.get("options", {}))
    opts_rep["nsub"] = tuple(opts_rep.get("nsub", (2, 2, 4)))
    opts_rep["section_log"] = [tuple(x) for x in opts_rep.get("section_log", [])]
    if opts_rep.get("Fy") is None: opts_rep["Fy"] = cfg.get("Fy", 50.0)
    opts_rep.setdefault("gravity_dirs", "+X and +Y"); opts_rep["risk_category"] = rc
    opts_rep.setdefault("n_cases", len(loads.steltic_combos(cfg)))
    rep, block = _finish(job, out_dir, nm, cfg, d.get("gate", {}), runs, d.get("sensitivity", []), opts_rep, d.get("member_table", []), None, elapsed=d.get("elapsed_s"))
    if not args.no_block:
        report_ddm.write_block(job, block)
    print(">> report:", rep)
    _viewer(out_dir, nm, d.get("gate", {}), runs)
    return rep


def _viewer(out_dir, nm, gate, runs):
    """ddm_viewer_3d.html (Steltic viewer bundle: Pushover / NLRHA / DDM share one core). Never hides the report."""
    try:
        from . import viewer3d
        print(">> viewer:", viewer3d.write(out_dir, nm, gate, runs))
    except Exception as ex:                                            # noqa: BLE001
        print("   viewer skipped:", ex)


def viewer(args):
    """Rebuild ddm_viewer_3d.html from ddm_results.json (no re-analysis)."""
    job = os.path.abspath(args.job_dir)
    engine_dir = args.steltic_engine or os.environ.get("STELTIC_ENGINE_DIR")
    out_dir = args.out or job
    nm = ingest.load_package(job, engine_dir)
    d = json.load(open(os.path.join(out_dir, "ddm_results.json")))
    _viewer(out_dir, nm, d.get("gate", {}), _runs_from_results(d))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="steltic_ddm")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run the DDM on a Steltic job folder")
    r.add_argument("job_dir")
    r.add_argument("--out", default=None, help="output folder (default: the job folder)")
    r.add_argument("--steltic-engine", default=None, help="path to steltic/steel_engine (or set STELTIC_ENGINE_DIR)")
    r.add_argument("--combos", default="default", choices=["default", "all"])
    r.add_argument("--only", nargs="*", default=None, help="substrings of combination labels to run")
    r.add_argument("--torsion", default="plus", choices=["plus", "minus", "both"])
    r.add_argument("--om0", action="store_true", help="include the Omega0 [col] cases")
    r.add_argument("--gravity-dirs", default="two", choices=["one", "two", "all"])
    r.add_argument("--nsub", nargs=3, type=int, default=[2, 2, 4], metavar=("COL", "BEAM", "BRACE"))
    r.add_argument("--nip", type=int, default=5)
    r.add_argument("--residual", default="lehigh", choices=["lehigh", "eccs", "none"])
    r.add_argument("--fy", type=float, default=None)
    r.add_argument("--hardening", type=float, default=0.002)
    r.add_argument("--bow", type=float, default=1 / 1000.0)
    r.add_argument("--psi", type=float, default=1 / 500.0)
    r.add_argument("--dlam", type=float, default=0.05)
    r.add_argument("--max-steps", type=int, default=250)
    r.add_argument("--time-limit", type=float, default=2400.0, help="seconds per sweep")
    r.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2))))
    r.add_argument("--fast", action="store_true", help="dispBeamColumn instead of forceBeamColumn")
    r.add_argument("--sensitivity", action="store_true")
    r.add_argument("--gate-tol", type=float, default=0.05)
    r.add_argument("--risk-category", default=None, choices=["I", "II", "III", "IV"], help="ASCE 7 Risk Category (default: from cfg / Ie)")
    r.add_argument("--force", action="store_true", help="continue even if the transfer gate fails")
    r.add_argument("--no-block", action="store_true", help="do not write ddm_analysis into calc_package.json")
    v = sub.add_parser("viewer", help="rebuild ddm_viewer_3d.html from ddm_results.json")
    v.add_argument("job_dir"); v.add_argument("--out", default=None); v.add_argument("--steltic-engine", default=None)
    p = sub.add_parser("report", help="rebuild ddm_report.html, the ddm_analysis block and the viewer from ddm_results.json with the current phi_s policy (no re-analysis)")
    p.add_argument("job_dir"); p.add_argument("--out", default=None); p.add_argument("--steltic-engine", default=None)
    p.add_argument("--risk-category", default=None, choices=["I", "II", "III", "IV"]); p.add_argument("--no-block", action="store_true")
    s = sub.add_parser("selftest", help="column-curve regression + small frame")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a)
    if a.cmd == "report":
        return report(a)
    if a.cmd == "viewer":
        return viewer(a)
    if a.cmd == "selftest":
        from .selftest import main as st
        return st()
    ap.print_help()


if __name__ == "__main__":
    main()
