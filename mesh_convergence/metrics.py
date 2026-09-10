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


def select_governing_fc_records(source) -> list:
    """Ordered Gate B FC-refine candidates (governing record(s) only).

    Prefer ``governing_fc_records`` / ``per_record_fc`` from a locked Gate A suite
    package or metrics row. Ranking: accepted (converged, not Ch.16-unacceptable)
    first, then descending per-record D/C on the suite-worst FC column.

    Each item: ``suite_index`` (1-based, for ``--only-records`` / ``--records``),
    ``record``, ``ele``, ``DC``, ``unacceptable``, ``converged``.
    Returns [] when governing motion cannot be identified (never invent first-motion).
    """
    if source is None:
        return []
    if isinstance(source, dict):
        # Direct list on metrics / settings / package / acceptance
        for key in ("governing_fc_records", "governing_fc_candidates"):
            cand = source.get(key)
            if isinstance(cand, list) and cand:
                out = [_normalize_gov_cand(c) for c in cand]
                out = [c for c in out if c]
                if out:
                    return _sort_gov_cands(out)
        acc = source.get("acceptance") if isinstance(source.get("acceptance"), dict) else None
        if acc:
            for key in ("governing_fc_records", "governing_fc_candidates"):
                cand = acc.get(key)
                if isinstance(cand, list) and cand:
                    out = [_normalize_gov_cand(c) for c in cand]
                    out = [c for c in out if c]
                    if out:
                        return _sort_gov_cands(out)
            built = _candidates_from_per_record_fc(
                acc.get("per_record_fc") or source.get("per_record_fc"),
                acc.get("force_controlled_columns") or source.get("force_controlled_columns"),
            )
            if built:
                return built
        built = _candidates_from_per_record_fc(
            source.get("per_record_fc"),
            source.get("force_controlled_columns"),
        )
        if built:
            return built
        # Single governing_fc with suite_index already attached
        gov = source.get("governing_fc")
        if isinstance(gov, dict) and gov.get("suite_index") is not None:
            c = _normalize_gov_cand(dict(gov, ele=gov.get("ele"), DC=gov.get("DC") or gov.get("governing_record_DC")))
            return [c] if c else []
        # FC column row annotated with suite_index
        gov_col = governing_fc_column(source.get("force_controlled_columns"))
        if gov_col and gov_col.get("suite_index") is not None:
            c = _normalize_gov_cand(gov_col)
            return [c] if c else []
    return []


def _sort_gov_cands(cands: list) -> list:
    return sorted(cands, key=lambda c: (
        0 if (c.get("converged") and not c.get("unacceptable")) else 1,
        -float(c["DC"] if c.get("DC") is not None else -1e300),
    ))


def _normalize_gov_cand(c) -> dict | None:
    if not isinstance(c, dict):
        return None
    si = c.get("suite_index")
    if si is None:
        si = c.get("index")
    try:
        si_i = int(si) if si is not None else None
    except (TypeError, ValueError):
        si_i = None
    if si_i is None or si_i < 1:
        return None
    dc = c.get("DC") if c.get("DC") is not None else c.get("governing_ele_DC") or c.get("governing_record_DC")
    try:
        dc_f = float(dc) if dc is not None else None
    except (TypeError, ValueError):
        dc_f = None
    return dict(
        suite_index=si_i,
        record=c.get("record") if c.get("record") is not None else c.get("governing_record"),
        label=c.get("label"),
        ele=c.get("ele") if c.get("ele") is not None else c.get("governing_ele"),
        DC=dc_f,
        Qu=c.get("Qu") if c.get("Qu") is not None else c.get("governing_ele_Qu"),
        unacceptable=bool(c.get("unacceptable")),
        converged=bool(c["converged"]) if "converged" in c else True,
    )


def _candidates_from_per_record_fc(per_fc, fc_rows) -> list:
    if not per_fc:
        return []
    gov = governing_fc_column(fc_rows) if fc_rows else None
    gov_ele = gov.get("ele") if gov else None
    out = []
    for entry in per_fc:
        if not isinstance(entry, dict):
            continue
        si = entry.get("suite_index")
        try:
            si_i = int(si) if si is not None else None
        except (TypeError, ValueError):
            si_i = None
        if si_i is None or si_i < 1:
            continue
        dc = entry.get("governing_ele_DC")
        ele = entry.get("governing_ele") or gov_ele
        if dc is None and ele is not None:
            for col in entry.get("columns") or []:
                if col.get("ele") == ele:
                    dc = col.get("DC")
                    break
        if dc is None:
            dc = entry.get("worst_DC")
        try:
            dc_f = float(dc) if dc is not None else None
        except (TypeError, ValueError):
            dc_f = None
        if dc_f is None:
            continue
        out.append(dict(
            suite_index=si_i,
            record=entry.get("record"),
            label=entry.get("label"),
            ele=ele,
            DC=dc_f,
            Qu=entry.get("governing_ele_Qu"),
            unacceptable=bool(entry.get("unacceptable")),
            converged=bool(entry["converged"]) if "converged" in entry else True,
        ))
    return _sort_gov_cands(out)


def governing_fc_from_package(pkg: dict) -> dict | None:
    """Governing FC column + primary refine suite_index from an nlrha package dict."""
    if not isinstance(pkg, dict):
        return None
    acc = pkg.get("acceptance") if isinstance(pkg.get("acceptance"), dict) else {}
    fc_rows = (
        acc.get("force_controlled_columns")
        or pkg.get("force_controlled_columns")
        or []
    )
    gov = governing_fc_column(fc_rows)
    cands = select_governing_fc_records(pkg) or select_governing_fc_records(acc)
    if gov is None and not cands:
        return None
    out = dict(gov or {})
    if cands:
        primary = next(
            (c for c in cands if c.get("converged") and not c.get("unacceptable")),
            cands[0],
        )
        out["suite_index"] = primary["suite_index"]
        out["governing_record"] = primary.get("record")
        out["governing_record_DC"] = primary.get("DC")
        if out.get("ele") is None:
            out["ele"] = primary.get("ele")
        if out.get("DC") is None:
            out["DC"] = primary.get("DC")
        out["candidates"] = cands
    return out if out else None


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
    cands = select_governing_fc_records(pkg) or select_governing_fc_records(acc)
    if not cands and gov and gov.get("suite_index") is not None:
        cands = [_normalize_gov_cand(gov)]
        cands = [c for c in cands if c]
    if gov and cands and gov.get("suite_index") is None:
        primary = next(
            (c for c in cands if c.get("converged") and not c.get("unacceptable")),
            cands[0],
        )
        gov = dict(gov, suite_index=primary["suite_index"],
                   governing_record=primary.get("record"),
                   governing_record_DC=primary.get("DC"))
    gov_out = None
    if gov:
        gov_out = {k: gov[k] for k in gov if k != "_dc"}
        if cands:
            gov_out = dict(gov_out, candidates=cands)
    return dict(
        mean_drift_max=v.get("mean_drift_max"),
        roof_mean_X=roof_x,
        roof_mean_Y=roof_y,
        worst_FC_DC=worst_fc,
        force_controlled_ok=fc_ok,
        force_controlled_columns=fc_rows,
        n_fc_columns=n_fc,
        governing_fc=gov_out,
        governing_fc_records=cands,
        governing_fc_suite_index=(cands[0]["suite_index"] if cands else (
            gov.get("suite_index") if gov else None)),
        n_ok=n_ok,
        n_records=n_rec_i,
        n_unacceptable=n_un_i,
        n_accepted=n_accepted,
        ACCEPTABLE=bool(v.get("overall")) if "overall" in v else v.get("ACCEPTABLE"),
        early_aborted=bool((acc.get("meta") or pkg.get("meta") or {}).get("early_aborted")),
        per_record_fc=(acc.get("per_record_fc") or pkg.get("per_record_fc") or []),
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
