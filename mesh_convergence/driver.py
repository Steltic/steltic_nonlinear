"""Mesh-convergence driver: product ladders for NSP / NLRHA / DDM + JSON/MD scorecards.

    python -m mesh_convergence --package JOB --analyses nsp nlrha ddm --out DIR
    python -m snl mesh-converge JOB --analyses nsp --dry-run

Product rules (see docs/PRODUCT_DEFAULTS.md):
  NLRHA: ModIMK → PZ×1 → fibre+mesh 10%; Gate A on ModIMK ⇒ stay ModIMK for FC;
         early abort once 2 records NC → next method/mesh.
  NSP / HR DDM: fibre + mesh 10%.
  Dual-gate A∧B; after A no full 11-suite for FC.
  Max ~4 rungs; Newton cascade unchanged.
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time
from .rungs import DEFAULT_RUNGS, rung_knobs
from .stop_rule import (
    walk_ladder, evaluate_nlrha_dual_gate, DEFAULT_TOL, DEFAULT_MAX_RUNGS,
    STATUS_NLRHA_COMPLETE, STATUS_GATE_A_LOCKED_FC_REFINE,
    STATUS_GATE_A_LOCKED_FC_PENDING, gate_a_suite, gate_b_fc,
    merge_fc_probe_with_suite, finalize_nlrha_score_status,
    FC_FAIL_NOT_COMPUTED, FC_FAIL_REFINE_RECORD_UNACCEPTABLE,
)
from .nlrha_ladder import (
    METHOD_STAGES, should_early_abort, lock_fc_plasticity, EARLY_ABORT_NC,
    evaluate_product_nlrha, plan_after_row,
)
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


def _write_temp_params(base_params: str | None, panel_zone: str, out_dir: str, tag: str) -> str | None:
    """Copy hinge_params (or repo default) with panel_zones.mode overridden."""
    import json as _json
    src = base_params
    if not src:
        cand = os.path.join(os.path.dirname(__file__), "..", "pushover", "hinge_params.json")
        src = os.path.abspath(cand) if os.path.isfile(cand) else None
    if not src or not os.path.isfile(src):
        return base_params
    doc = _json.load(open(src, encoding="utf-8"))
    doc.setdefault("panel_zones", {})["mode"] = panel_zone
    path = os.path.join(out_dir, "hinge_params_%s.json" % tag)
    _json.dump(doc, open(path, "w"), indent=2)
    return path


def run_nsp_or_ddm_ladder(analysis: str, package: str, out_root: str, *, args) -> dict:
    """Fibre + mesh 10% for NSP / DDM (rules 3–4, 8)."""
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
            base = {"nsp": dict(T1=1.0, Vy=100.0, Vpeak=150.0, delta_t=10.0),
                    "ddm": dict(lambda_u=1.5, lambda_G=1.8, phi_s_lambda_u=1.2)}[
                        "nsp" if analysis in ("nsp", "pushover") else "ddm"]
            scale = 1.0 if i == 0 else (1.12 if i == 1 else 1.12 * (1.0 + 0.02 * (i - 1)))
            row = {k: (v * scale if isinstance(v, (int, float)) else v) for k, v in base.items()}
            metric_rows.append(row)
            rung_meta.append(dict(level=rung["level"], knobs=kn, dry_run=True))
        else:
            env = _env_with(env_base, kn)
            params = ["--params", os.path.abspath(args.params)] if args.params else []
            site = ["--site-class", args.site_class]
            if analysis in ("nsp", "pushover"):
                cmd = [py, "-m", "pushover", "run", package, "--out", rung_dir,
                       "--plasticity", "fibre", "--member-nseg", str(kn["member_nseg"])] + site + params
                st = _run(cmd, log, env=env)
                row = MX.from_nsp(rung_dir)
            else:
                if not env.get("STELTIC_ENGINE_DIR"):
                    return dict(error="STELTIC_ENGINE_DIR / --steltic-engine required for DDM ladder")
                nsub = kn["nsub"]
                cmd = [py, "-m", "steltic_ddm", "run", package, "--out", rung_dir,
                       "--nsub", str(nsub[0]), str(nsub[1]), str(nsub[2]),
                       "--nip", str(kn["nip"]), "--workers", str(args.parallel)]
                # HR DDM product: rigid end offsets on (rule 9)
                if getattr(args, "no_rigid_end_offset", False):
                    cmd += ["--no-rigid-end-offset"]
                elif getattr(args, "rigid_end_offset", None) is not None:
                    cmd += ["--rigid-end-offset", str(args.rigid_end_offset)]
                if args.risk_category:
                    cmd += ["--risk-category", args.risk_category]
                st = _run(cmd, log, env=env)
                row = MX.from_ddm(rung_dir)
            row = dict(row, _rung=rung["level"], _run=st)
            metric_rows.append(row)
            rung_meta.append(dict(level=rung["level"], knobs=kn, run=st))

        if i >= 1:
            ladder = walk_ladder(analysis if analysis != "pushover" else "nsp",
                                 metric_rows, tol=args.tol, max_rungs=args.max_rungs)
            if ladder["steps"] and ladder["steps"][-1]["stop"]:
                break

    ladder = walk_ladder(analysis if analysis != "pushover" else "nsp",
                         metric_rows, tol=args.tol, max_rungs=args.max_rungs)
    case = os.path.basename(os.path.abspath(package).rstrip("/"))
    path = write_scorecard(out_root, case=case, analysis=analysis, rungs=rungs,
                           ladder=ladder, metric_rows=metric_rows,
                           extra=dict(rung_meta=rung_meta, package=os.path.abspath(package),
                                      method="fibre", product_rule="NSP/DDM fibre+mesh 10%"))
    print(">> scorecard", path, "status=", ladder["status"], "stop_level=", ladder.get("stop_level"))
    return dict(scorecard=path, ladder=ladder, metrics=metric_rows)


def _dry_nlrha_row(stage, i, nlrha_mode, locked_suite):
    """Synthetic metrics for dry-run product ladder."""
    # Nonempty FC columns so Gate B can evaluate numeric DC (null/empty never pass).
    _fc_cols = [dict(ele=1, section="W14X90", DC=1.1)]
    base = dict(mean_drift_max=0.03, roof_mean_X=0.02, roof_mean_Y=0.015,
                worst_FC_DC=1.1, n_ok=11, n_records=11,
                n_unacceptable=0, n_accepted=11, ACCEPTABLE=True,
                force_controlled_ok=False, n_nc=0, early_aborted=False,
                force_controlled_columns=list(_fc_cols), n_fc_columns=1)
    sid = stage.get("id")
    if sid == "modimk":
        # Fail Gate A (like MC4 L0) — climb to PZ
        row = dict(base, n_unacceptable=6, n_accepted=5, ACCEPTABLE=False,
                   worst_FC_DC=1.25, force_controlled_ok=False, n_nc=1)
    elif sid == "pz_once":
        # Still fail Gate A → fibre
        row = dict(base, n_unacceptable=3, n_accepted=8, ACCEPTABLE=False,
                   worst_FC_DC=1.2, force_controlled_ok=False, n_nc=1)
    else:
        # fibre mesh: M0 may fail A; later pass A with FC refine
        mesh_i = 0
        if stage.get("mesh_level"):
            mesh_i = {"M0": 0, "M1": 1, "M2": 2, "M3": 3}.get(stage["mesh_level"], i)
        if mesh_i == 0 and nlrha_mode != "fc_refine":
            row = dict(base, n_unacceptable=2, n_accepted=9, ACCEPTABLE=False,
                       worst_FC_DC=1.18, force_controlled_ok=False, n_nc=0)
        else:
            row = dict(base, n_unacceptable=0, n_accepted=11, ACCEPTABLE=True, n_nc=0)
            dc = 1.15 * (0.92 ** max(0, mesh_i))
            if nlrha_mode == "fc_refine":
                dc = 1.15 * (0.92 ** max(0, i))
            row["worst_FC_DC"] = round(dc, 4)
            row["force_controlled_ok"] = row["worst_FC_DC"] <= 1.0
            row["force_controlled_columns"] = [dict(ele=1, section="W14X90", DC=row["worst_FC_DC"])]
            row["n_fc_columns"] = 1
    # Keep FC column evidence in sync with worst_FC_DC for Gate B honesty.
    dc = row.get("worst_FC_DC")
    if dc is not None:
        row["force_controlled_columns"] = [dict(ele=1, section="W14X90", DC=dc)]
        row["n_fc_columns"] = 1
        row["force_controlled_ok"] = bool(dc <= 1.0)
    else:
        row["force_controlled_columns"] = []
        row["n_fc_columns"] = 0
        row["force_controlled_ok"] = False
    # Synthetic governing FC record (suite index 4) for dry-run Gate B refine path.
    gov = dict(ele=1, section="W14X90", DC=row.get("worst_FC_DC"), suite_index=4,
               governing_record=12081, governing_record_DC=row.get("worst_FC_DC"))
    row["governing_fc"] = gov
    row["governing_fc_suite_index"] = 4
    row["governing_fc_records"] = [
        dict(suite_index=4, record=12081, ele=1, DC=row.get("worst_FC_DC"),
             unacceptable=False, converged=True),
        dict(suite_index=5, record=12122, ele=1, DC=(row.get("worst_FC_DC") or 1.0) * 0.99,
             unacceptable=False, converged=True),
    ]
    if row.get("force_controlled_columns"):
        row["force_controlled_columns"][0] = dict(
            row["force_controlled_columns"][0], suite_index=4, governing_record=12081)
    row["_stage"] = dict(id=stage.get("id"), plasticity=stage.get("plasticity"),
                         panel_zone=stage.get("panel_zone"),
                         mesh_level=stage.get("mesh_level"), mode=stage.get("mode"))
    return row


def run_nlrha_product_ladder(package: str, out_root: str, *, args) -> dict:
    """ModIMK → PZ×1 → fibre+mesh; dual-gate; early abort 2 NC; ModIMK A ⇒ no fibre FC."""
    py = sys.executable
    fibre_rungs = plan_rungs(args.max_rungs)
    metric_rows = []
    rung_meta = []
    log = os.path.join(out_root, "mesh_convergence_nlrha.log")
    env_base = dict(os.environ)
    if args.steltic_engine:
        env_base["STELTIC_ENGINE_DIR"] = os.path.abspath(args.steltic_engine)

    nlrha_mode = "full_suite"  # or fc_refine
    locked_suite = None
    lock_level = None
    lock_stage = None
    fc_settings = None

    # Build stage list: method stages then fibre mesh
    stages = []
    for s in METHOD_STAGES:
        stages.append(dict(s, mesh_level=None, mode="method", rung=None))
    for r in fibre_rungs:
        stages.append(dict(
            id="fibre_%s" % r["level"], plasticity="fibre", panel_zone="rigid",
            mesh_level=r["level"], mode="fibre_mesh", rung=r,
            intent="fibre + mesh %s" % r["level"],
        ))

    stage_i = 0
    fibre_start = len(METHOD_STAGES)
    max_total = len(stages)

    while stage_i < max_total:
        stage = stages[stage_i]
        tag = stage["id"]
        rung_dir = os.path.join(out_root, "nlrha_%s%s" % (
            tag, "_fc" if nlrha_mode == "fc_refine" else ""))
        os.makedirs(rung_dir, exist_ok=True)

        # FC refine: freeze plasticity to Gate-A lock method (rule 1.4)
        if nlrha_mode == "fc_refine" and fc_settings:
            plast = fc_settings["plasticity"]
            pz = fc_settings.get("panel_zone") or "rigid"
            # Use fibre mesh knobs only when lock was fibre; else ModIMK nseg=1-ish
            if plast == "fibre" and stage.get("rung"):
                kn = rung_knobs(stage["rung"], "nlrha")
            elif plast == "fibre":
                kn = rung_knobs(fibre_rungs[min(stage_i, len(fibre_rungs) - 1)], "nlrha")
            else:
                kn = dict(plasticity="imk", member_nseg=1, env={
                    "SNL_PLASTICITY": "imk", "SNL_MEMBER_NSEG": "1",
                })
            kn = dict(kn, plasticity=plast)
            kn.setdefault("env", {})["SNL_PLASTICITY"] = plast
        else:
            if stage.get("rung"):
                kn = rung_knobs(stage["rung"], "nlrha")
            elif stage["plasticity"] == "imk":
                kn = dict(plasticity="imk", member_nseg=1, env={
                    "SNL_PLASTICITY": "imk", "SNL_MEMBER_NSEG": "1",
                })
            else:
                kn = rung_knobs(fibre_rungs[0], "nlrha")
            plast = stage["plasticity"]
            pz = stage.get("panel_zone") or "rigid"

        print("== nlrha %s mode=%s plast=%s pz=%s ==" % (
            tag, nlrha_mode, plast, pz), kn, flush=True)

        if args.dry_run:
            row = _dry_nlrha_row(stage, stage_i, nlrha_mode, locked_suite)
            if nlrha_mode == "fc_refine" and locked_suite:
                for k in ("mean_drift_max", "roof_mean_X", "roof_mean_Y",
                          "n_ok", "n_records", "n_unacceptable", "n_accepted", "ACCEPTABLE"):
                    if k in locked_suite:
                        row[k] = locked_suite[k]
                suite_fc = {
                    "worst_FC_DC": locked_suite.get("worst_FC_DC"),
                    "force_controlled_columns": locked_suite.get("force_controlled_columns") or [],
                    "n_fc_columns": locked_suite.get("n_fc_columns"),
                    "force_controlled_ok": locked_suite.get("force_controlled_ok"),
                }
                row = merge_fc_probe_with_suite(suite_fc, row)
            metric_rows.append(row)
            rung_meta.append(dict(stage=stage, knobs=kn, dry_run=True, mode=nlrha_mode,
                                  plasticity=plast, panel_zone=pz))
        else:
            env = _env_with(env_base, kn)
            params_path = _write_temp_params(args.params, pz, rung_dir, tag)
            params = ["--params", os.path.abspath(params_path)] if params_path else []
            site = ["--site-class", args.site_class]
            n_suite = int(args.n_records)
            if locked_suite and locked_suite.get("n_records"):
                try:
                    n_suite = int(locked_suite["n_records"])
                except (TypeError, ValueError):
                    pass
            # FC refine: keep the locked suite's --n so --only-records indices match
            # Gate A ordering. Never arbitrary first-motion --n 1 without a governing index.
            n_for_run = n_suite if nlrha_mode == "fc_refine" else int(args.n_records)
            gov_rec_args = []
            skip_fc_run = False
            if nlrha_mode == "fc_refine":
                cands = list((fc_settings or {}).get("governing_fc_candidates") or [])
                if not cands and locked_suite:
                    cands = list(locked_suite.get("governing_fc_candidates")
                                 or locked_suite.get("governing_fc_records") or [])
                if not cands:
                    cands = (MX.select_governing_fc_records(fc_settings or {})
                    or MX.select_governing_fc_records(locked_suite or {}))
                cand_i = int((fc_settings or {}).get("governing_fc_candidate_i") or 0)
                gov = None
                if cands and 0 <= cand_i < len(cands):
                    gov = cands[cand_i].get("suite_index")
                    if fc_settings is not None:
                        fc_settings["governing_fc_suite_index"] = gov
                        fc_settings["governing_fc_candidates"] = cands
                if gov is None:
                    gov = (fc_settings or {}).get("governing_fc_suite_index")
                if gov is None and locked_suite:
                    gov = locked_suite.get("governing_fc_suite_index")
                print(">> NLRHA FC refine: Gate A locked — governing record(s) only "
                      "(not full 11; not arbitrary --n 1); suite --n %d; plasticity=%s" % (
                          n_for_run, plast), flush=True)
                if gov is not None:
                    try:
                        gi = int(gov)
                        if gi >= 1:
                            gov_rec_args = ["--only-records", str(gi)]
                            print(">> FC refine targeting governing suite index", gi,
                                  "(candidate %d/%d; column %s)" % (
                                      cand_i + 1, max(len(cands), 1),
                                      (fc_settings or {}).get("governing_fc_ele")),
                                  flush=True)
                    except (TypeError, ValueError):
                        gov_rec_args = []
                if not gov_rec_args:
                    print(">> FC refine: no governing suite index identified — "
                          "failing fc_not_computed (refusing arbitrary first motion)",
                          flush=True)
                    skip_fc_run = True
                gov_ele = (fc_settings or {}).get("governing_fc_ele") or (
                    ((locked_suite or {}).get("governing_fc") or {}).get("ele")
                )
                if gov_ele is not None:
                    print(">> FC refine governing column ele=", gov_ele, flush=True)
            if skip_fc_run:
                row = dict(
                    worst_FC_DC=None, force_controlled_ok=False,
                    force_controlled_columns=[], n_fc_columns=0,
                    fc_not_computed=True, fc_fail_reason=FC_FAIL_NOT_COMPUTED,
                    n_nc=0, early_aborted=False, _run=dict(skipped=True),
                    _stage=dict(id=tag, plasticity=plast, panel_zone=pz,
                                mesh_level=stage.get("mesh_level"), mode=nlrha_mode),
                )
                if locked_suite:
                    for k in ("mean_drift_max", "roof_mean_X", "roof_mean_Y",
                              "n_ok", "n_records", "n_unacceptable", "n_accepted", "ACCEPTABLE"):
                        if k in locked_suite:
                            row[k] = locked_suite[k]
                    suite_fc = {
                        "worst_FC_DC": locked_suite.get("worst_FC_DC"),
                        "force_controlled_columns": locked_suite.get("force_controlled_columns") or [],
                        "n_fc_columns": locked_suite.get("n_fc_columns"),
                        "force_controlled_ok": locked_suite.get("force_controlled_ok"),
                    }
                    row = merge_fc_probe_with_suite(suite_fc, row)
                metric_rows.append(row)
                rung_meta.append(dict(stage=stage, knobs=kn, run=dict(skipped=True),
                                      mode=nlrha_mode, plasticity=plast, panel_zone=pz,
                                      fc_not_computed=True))
                print(">>", "FC refine aborted: governing record unknown", flush=True)
                break
            cmd = [py, "-m", "nlrha", "run", package, "--out", rung_dir,
                   "--plasticity", plast, "--member-nseg", str(kn.get("member_nseg", 4)),
                   "--parallel", str(args.parallel), "--dt", str(args.dt),
                   "--n", str(n_for_run),
                   "--early-abort-nc", str(getattr(args, "early_abort_nc", EARLY_ABORT_NC))] + site + params + gov_rec_args
            if args.risk_category:
                cmd += ["--risk-category", args.risk_category]
            st = _run(cmd, log, env=env)
            row = MX.from_nlrha(rung_dir)
            # Detect early abort from package if present
            pkg_path = os.path.join(rung_dir, "nlrha_package.json")
            n_nc = 0
            early = False
            if os.path.isfile(pkg_path):
                try:
                    pkg = json.load(open(pkg_path))
                    results = pkg.get("results") or []
                    n_nc = sum(1 for r in results if not r.get("converged"))
                    early = bool((pkg.get("meta") or {}).get("early_aborted")) or should_early_abort(results)
                except Exception:
                    pass
            row = dict(row, n_nc=n_nc, early_aborted=early, _run=st,
                       _stage=dict(id=tag, plasticity=plast, panel_zone=pz,
                                   mesh_level=stage.get("mesh_level"), mode=nlrha_mode))
            if nlrha_mode == "fc_refine" and locked_suite:
                for k in ("mean_drift_max", "roof_mean_X", "roof_mean_Y",
                          "n_ok", "n_records", "n_unacceptable", "n_accepted", "ACCEPTABLE"):
                    if k in locked_suite:
                        row[k] = locked_suite[k]
                # Never overwrite known suite worst_FC_DC with probe null; surface both.
                suite_fc = {
                    "worst_FC_DC": locked_suite.get("worst_FC_DC",
                                                    (fc_settings or {}).get("suite_worst_FC_DC")),
                    "force_controlled_columns": locked_suite.get("force_controlled_columns") or [],
                    "n_fc_columns": locked_suite.get("n_fc_columns"),
                    "force_controlled_ok": locked_suite.get("force_controlled_ok"),
                }
                row = merge_fc_probe_with_suite(suite_fc, row)
                if row.get("fc_not_computed"):
                    print(">> FC refine probe fc_not_computed — Gate B will fail "
                          "(suite_worst_FC_DC=%s retained separately)" % suite_fc.get("worst_FC_DC"),
                          flush=True)
                # If the refine motion is Ch.16-unacceptable at the finer mesh, do not
                # vacuous-pass — try next governing candidate or fail clearly.
                probe_unacc = False
                try:
                    pkg2 = json.load(open(pkg_path)) if os.path.isfile(pkg_path) else {}
                    per = (pkg2.get("acceptance") or {}).get("per_record") or pkg2.get("per_record") or []
                    if per and all(p.get("unacceptable") or not p.get("converged") for p in per):
                        probe_unacc = True
                    elif per and len(per) == 1 and (per[0].get("unacceptable") or not per[0].get("converged")):
                        probe_unacc = True
                except Exception:
                    probe_unacc = False
                if probe_unacc:
                    row["fc_refine_record_unacceptable"] = True
                    row["fc_fail_reason"] = FC_FAIL_REFINE_RECORD_UNACCEPTABLE
                    row["force_controlled_ok"] = False
                    print(">> FC refine record Ch.16-unacceptable at finer mesh — "
                          "not vacuous-passing Gate B", flush=True)
                    cands = list((fc_settings or {}).get("governing_fc_candidates") or [])
                    cand_i = int((fc_settings or {}).get("governing_fc_candidate_i") or 0)
                    if cands and cand_i + 1 < len(cands):
                        fc_settings["governing_fc_candidate_i"] = cand_i + 1
                        nxt = cands[cand_i + 1]
                        fc_settings["governing_fc_suite_index"] = nxt.get("suite_index")
                        print(">> trying next governing FC candidate suite index",
                              nxt.get("suite_index"), flush=True)
                        metric_rows.append(row)
                        rung_meta.append(dict(stage=stage, knobs=kn, run=st, mode=nlrha_mode,
                                              plasticity=plast, panel_zone=pz,
                                              early_aborted=early, n_nc=n_nc,
                                              fc_refine_record_unacceptable=True))
                        # Re-run same mesh stage with next candidate (do not advance mesh yet)
                        continue
            metric_rows.append(row)
            rung_meta.append(dict(stage=stage, knobs=kn, run=st, mode=nlrha_mode,
                                  plasticity=plast, panel_zone=pz, early_aborted=early, n_nc=n_nc,
                                  fc_not_computed=bool(row.get("fc_not_computed")),
                                  fc_refine_record_unacceptable=bool(row.get("fc_refine_record_unacceptable"))))

        # ---- decisions ----
        if nlrha_mode == "fc_refine":
            ladder = evaluate_product_nlrha(metric_rows, tol=args.tol, max_rungs=args.max_rungs)
            if ladder["status"] == STATUS_NLRHA_COMPLETE:
                print(">> NLRHA dual-gate COMPLETE (A ∧ B); FC stayed",
                      (fc_settings or {}).get("plasticity"), flush=True)
                break
            if ladder.get("schedule_fc_refine"):
                # Advance fibre mesh only if FC settings are fibre; else stay imk and bump tag index
                stage_i += 1
                if stage_i >= max_total:
                    break
                continue
            print(">>", ladder.get("message") or ladder["status"], flush=True)
            break

        # Full-suite / method path
        row = metric_rows[-1]
        early = bool(row.get("early_aborted")) or should_early_abort(
            row.get("record_results") or []) or int(row.get("n_nc") or 0) >= EARLY_ABORT_NC
        decision = plan_after_row(stage, row, early_aborted=early)
        print(">>", decision.get("message"), flush=True)

        if decision.get("lock_fc") and decision.get("gate_a_passed"):
            locked_suite = (decision.get("gate_a") or {}).get("locked_edps") or {
                k: row.get(k) for k in (
                    "mean_drift_max", "roof_mean_X", "roof_mean_Y",
                    "n_ok", "n_records", "n_unacceptable", "n_accepted", "ACCEPTABLE")
            }
            # Preserve suite FC for Gate B refine honesty / governing demand.
            locked_suite = dict(locked_suite)
            for fk in ("worst_FC_DC", "force_controlled_ok", "force_controlled_columns",
                       "n_fc_columns", "governing_fc", "governing_fc_records",
                       "governing_fc_suite_index", "per_record_fc"):
                if fk in row:
                    locked_suite[fk] = row.get(fk)
            lock_level = len(metric_rows) - 1
            lock_stage = stage
            fc_settings = dict(decision.get("fc_settings") or lock_fc_plasticity(stage))
            fc_settings["suite_worst_FC_DC"] = locked_suite.get("worst_FC_DC")
            # Governing FC record(s): motion(s) behind suite-worst FC D/C (not first --n 1).
            cands = (
                MX.select_governing_fc_records(row)
                or MX.select_governing_fc_records(locked_suite)
                or list(row.get("governing_fc_records") or [])
            )
            # Also try reading the just-written suite package if candidates still missing.
            if not cands:
                try:
                    suite_pkg_path = os.path.join(rung_dir, "nlrha_package.json")
                    if os.path.isfile(suite_pkg_path):
                        cands = MX.select_governing_fc_records(json.load(open(suite_pkg_path)))
                except Exception:
                    cands = []
            if cands:
                fc_settings["governing_fc_candidates"] = cands
                fc_settings["governing_fc_candidate_i"] = 0
                fc_settings["governing_fc_suite_index"] = int(cands[0]["suite_index"])
                locked_suite["governing_fc_candidates"] = cands
                locked_suite["governing_fc_suite_index"] = int(cands[0]["suite_index"])
                locked_suite["governing_fc_records"] = cands
                print(">> governing FC refine candidates (suite indices):",
                      [c.get("suite_index") for c in cands[:5]],
                      ("..." if len(cands) > 5 else ""),
                      "primary=", cands[0].get("suite_index"),
                      "ele=", cands[0].get("ele"),
                      flush=True)
            gov = row.get("governing_fc") or {}
            if isinstance(gov, dict):
                if gov.get("ele") is not None:
                    fc_settings["governing_fc_ele"] = gov.get("ele")
                    locked_suite["governing_fc"] = gov
                if gov.get("suite_index") is not None and "governing_fc_suite_index" not in fc_settings:
                    fc_settings["governing_fc_suite_index"] = int(gov["suite_index"])
                    locked_suite["governing_fc_suite_index"] = int(gov["suite_index"])
            if cands and isinstance(gov, dict) and gov.get("ele") is None:
                fc_settings["governing_fc_ele"] = cands[0].get("ele")
            gb = gate_b_fc(row, prev_fc_metrics=None, tol=args.tol)
            if gb["passed"]:
                print(">> NLRHA complete at method stage", tag, "(A ∧ B)", flush=True)
                break
            nlrha_mode = "fc_refine"
            print(">> Gate A locked on", tag, "— FC refine stays",
                  fc_settings["plasticity"],
                  "(no fibre for FC)" if fc_settings.get("no_fibre_for_fc") else "",
                  flush=True)
            # If lock was on fibre mesh, continue mesh climb for FC; if method, stay on same plast
            if stage.get("mode") == "fibre_mesh":
                stage_i += 1
            # else remain / use synthetic FC stages by incrementing through remaining
            stage_i += 0 if stage.get("mode") == "method" else 0
            # Always move to next slot for FC refine probe directory uniqueness
            if stage.get("mode") == "method":
                # Jump to first fibre slot only for directory naming of FC probes, but keep imk
                stage_i = fibre_start
            else:
                stage_i += 1
            continue

        if decision.get("advance_method") or decision.get("start_fibre_mesh") or decision.get("advance_fibre_mesh"):
            stage_i += 1
            continue

        # Cap / stop
        break

    ladder = evaluate_product_nlrha(metric_rows, tol=args.tol, max_rungs=args.max_rungs)
    if fc_settings:
        ladder = dict(ladder, fc_settings=fc_settings,
                      no_fibre_for_fc=fc_settings.get("no_fibre_for_fc", False))
    case = os.path.basename(os.path.abspath(package).rstrip("/"))
    path = write_scorecard(
        out_root, case=case, analysis="nlrha", rungs=fibre_rungs,
        ladder=ladder, metric_rows=metric_rows,
        extra=dict(
            rung_meta=rung_meta, package=os.path.abspath(package),
            nlrha_dual_gate=True, nlrha_method_ladder=True,
            method_stages=[s["id"] for s in METHOD_STAGES],
            locked_suite=ladder.get("locked_suite") or locked_suite,
            lock_stage=(lock_stage or {}).get("id") if lock_stage else None,
            fc_settings=fc_settings,
            no_fibre_for_fc=(fc_settings or {}).get("no_fibre_for_fc"),
            gate_a=ladder.get("gate_a"), gate_b=ladder.get("gate_b"),
            schedule_full_suite=ladder.get("schedule_full_suite"),
            schedule_fc_refine=ladder.get("schedule_fc_refine"),
            early_abort_nc=EARLY_ABORT_NC,
            message=ladder.get("message"),
            suite_worst_FC_DC=(locked_suite or {}).get("worst_FC_DC") if locked_suite else (
                (fc_settings or {}).get("suite_worst_FC_DC")),
            probe_worst_FC_DC=(metric_rows[-1].get("probe_worst_FC_DC")
                               if metric_rows else None),
            gate_b_fail_reason=(ladder.get("gate_b") or {}).get("fail_reason"),
            michael_override=False,
        ),
    )
    print(">> scorecard", path, "status=", ladder["status"],
          "stop_level=", ladder.get("stop_level"),
          "no_fibre_for_fc=", (fc_settings or {}).get("no_fibre_for_fc"))
    return dict(scorecard=path, ladder=ladder, metrics=metric_rows)


def run_analysis_ladder(analysis: str, package: str, out_root: str, *, args) -> dict:
    if analysis == "nlrha":
        return run_nlrha_product_ladder(package, out_root, args=args)
    return run_nsp_or_ddm_ladder(analysis, package, out_root, args=args)


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
    # short md summary
    md_path = os.path.join(out, "mesh_convergence_summary.md")
    lines = ["# Mesh-convergence summary", "",
             "- package: `%s`" % package,
             "- tol: %s" % args.tol,
             "- max_rungs: %s" % args.max_rungs,
             "- dry_run: %s" % bool(args.dry_run), ""]
    for a, res in summary["analyses"].items():
        if "error" in res:
            lines.append("- **%s**: ERROR %s" % (a, res["error"]))
        else:
            lad = res.get("ladder") or {}
            lines.append("- **%s**: status=`%s` stop_level=%s" % (
                a, lad.get("status"), lad.get("stop_level")))
    open(md_path, "w").write("\n".join(lines) + "\n")
    print(">> summary", summary_path, md_path)
    return rc


def cli_main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mesh_convergence",
                                 description="Product mesh-convergence (NSP/DDM fibre; NLRHA ModIMK→PZ→fibre)")
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
    ap.add_argument("--early-abort-nc", type=int, default=EARLY_ABORT_NC)
    ap.add_argument("--rigid-end-offset", type=float, default=None)
    ap.add_argument("--no-rigid-end-offset", action="store_true")
    return main(ap.parse_args(argv))


def main_from_snl(a) -> int:
    return main(a)
