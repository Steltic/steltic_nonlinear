"""Extract primary metrics from analysis result JSON / packages for the scorecard."""
from __future__ import annotations
import json, os
from typing import Any, Optional


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def from_nsp(pushover_dir: str, direction: str = "X", hazard: str = "BSE-2N") -> dict:
    """Primary NSP metrics from pushover_package.json."""
    path = os.path.join(pushover_dir, "pushover_package.json")
    if not os.path.exists(path):
        return dict(error="missing pushover_package.json", path=path)
    pkg = _load(path)
    runs = pkg.get("runs") or pkg.get("directions") or {}
    # tolerate nested shapes used by report_supplement
    block = runs.get(direction) or (pkg.get(direction) if isinstance(pkg.get(direction), dict) else None)
    if block is None and "results" in pkg:
        block = (pkg["results"].get(direction) or {})
        run = (pkg.get("runs") or {}).get(direction) or {}
    else:
        run = block or {}
        block = (pkg.get("results") or {}).get(direction) or block or {}
    nsp = (block.get("nsp") or {}).get(hazard) or (block.get("nsp") or {})
    # T1 from modal / Te
    T1 = nsp.get("Te") or nsp.get("T1") or (run.get("T1") if isinstance(run, dict) else None)
    Vy = nsp.get("Vy")
    p695 = block.get("p695") or {}
    Vpeak = p695.get("Vmax_kip") or nsp.get("Vd") or nsp.get("Vpeak")
    delta_t = nsp.get("target_disp_in") or nsp.get("delta_t")
    return dict(T1=T1, Vy=Vy, Vpeak=Vpeak, delta_t=delta_t, direction=direction, hazard=hazard)


def governing_fc_column(fc_rows) -> dict | None:
    """Return the governing (max DC) force-controlled column row, or None."""
    best = None
    for r in fc_rows or []:
        dc = r.get("DC") or r.get("D_over_C") or r.get("dc")
        if dc is None:
            continue
        try:
            dcf = float(dc)
        except (TypeError, ValueError):
            continue
        if best is None or dcf > best["_dc"]:
            best = dict(r, _dc=dcf)
    if best is None:
        return None
    best["DC"] = best.pop("_dc")
    return best


def from_nlrha(nlrha_dir: str) -> dict:
    path = os.path.join(nlrha_dir, "nlrha_package.json")
    if not os.path.exists(path):
        return dict(error="missing nlrha_package.json", path=path)
    pkg = _load(path)
    acc = pkg.get("acceptance") or pkg.get("acc") or {}
    # Flat report packages (no nested acceptance) still carry verdict / FC at top level.
    v = acc.get("verdict") or pkg.get("verdict") or {}
    stories = (
        acc.get("story_drifts") or acc.get("stories") or acc.get("story")
        or pkg.get("story_drifts") or pkg.get("story") or []
    )
    roof_x = roof_y = None
    if stories:
        last = stories[-1]
        roof_x = last.get("mean_X") or last.get("mean_x")
        roof_y = last.get("mean_Y") or last.get("mean_y")
    fc_rows = (
        acc.get("force_controlled_columns")
        or pkg.get("force_controlled_columns")
        or []
    )
    if not isinstance(fc_rows, list):
        fc_rows = []
    worst_fc = None
    for r in fc_rows:
        dc = r.get("DC") or r.get("D_over_C") or r.get("dc")
        if dc is None:
            continue
        worst_fc = float(dc) if worst_fc is None else max(worst_fc, float(dc))
    if worst_fc is None:
        worst_fc = v.get("worst_FC_DC") or acc.get("worst_FC_DC") or pkg.get("worst_FC_DC")
        try:
            worst_fc = float(worst_fc) if worst_fc is not None else None
        except (TypeError, ValueError):
            worst_fc = None
    n_ok = sum(1 for r in (pkg.get("results") or []) if r.get("converged"))
    # Flat packages may store per_record instead of results.
    if not n_ok and pkg.get("per_record"):
        n_ok = sum(1 for r in pkg["per_record"] if r.get("converged"))
    n_rec = v.get("n_records") or len(pkg.get("results") or []) or len(pkg.get("per_record") or [])
    try:
        n_rec_i = int(n_rec) if n_rec is not None else None
    except (TypeError, ValueError):
        n_rec_i = None
    n_un = v.get("n_unacceptable")
    try:
        n_un_i = int(n_un) if n_un is not None else None
    except (TypeError, ValueError):
        n_un_i = None
    # Gate A helper: accepted = records - unacceptable when both known
    n_accepted = None
    if n_rec_i is not None and n_un_i is not None:
        n_accepted = n_rec_i - n_un_i
    n_fc = len(fc_rows)
    fc_ok = v.get("force_controlled_ok")
    # Vacuous True with empty FC columns / null DC is invalid for Gate B.
    if n_fc <= 0 or worst_fc is None:
        fc_ok = False
    elif fc_ok is None:
        fc_ok = float(worst_fc) <= 1.0
    elif fc_ok is True and not (float(worst_fc) <= 1.0):
        fc_ok = False
    gov = governing_fc_column(fc_rows)
    return dict(
        mean_drift_max=v.get("mean_drift_max"),
        roof_mean_X=roof_x,
        roof_mean_Y=roof_y,
        worst_FC_DC=worst_fc,
        force_controlled_ok=fc_ok,
        force_controlled_columns=fc_rows,
        n_fc_columns=n_fc,
        governing_fc=({k: gov[k] for k in gov if k != "_dc"} if gov else None),
        n_ok=n_ok,
        n_records=n_rec_i,
        n_unacceptable=n_un_i,
        n_accepted=n_accepted,
        ACCEPTABLE=bool(v.get("overall")) if "overall" in v else v.get("ACCEPTABLE"),
        early_aborted=bool((acc.get("meta") or pkg.get("meta") or {}).get("early_aborted")),
    )


def from_ddm(ddm_dir: str) -> dict:
    path = os.path.join(ddm_dir, "ddm_results.json")
    if not os.path.exists(path):
        # sometimes results sit in job root
        alt = os.path.join(os.path.dirname(ddm_dir.rstrip("/")), "ddm_results.json")
        path = alt if os.path.exists(alt) else path
    if not os.path.exists(path):
        return dict(error="missing ddm_results.json", path=path)
    d = _load(path)
    runs = d.get("runs") or []
    lambda_u = lambda_G = phi_s_lu = None
    for r in runs:
        kind = (r.get("kind") or "").lower()
        lu = r.get("lambda_u") or r.get("lam_u")
        phi = (r.get("phi") or {}).get("phi_s")
        prod = (phi * lu) if (phi is not None and lu is not None) else r.get("phi_s_lambda_u")
        if kind == "gravity" or "gravity" in (r.get("label") or "").lower():
            if lambda_G is None:
                lambda_G = lu
        if lu is not None:
            if lambda_u is None or lu < lambda_u:
                lambda_u = lu
                phi_s_lu = prod
    return dict(lambda_u=lambda_u, lambda_G=lambda_G, phi_s_lambda_u=phi_s_lu)
