"""JSON scorecard writer for a mesh-convergence case."""
from __future__ import annotations
import json, os, time
from typing import Any, Mapping, Sequence


def write_scorecard(
    out_dir: str,
    *,
    case: str,
    analysis: str,
    rungs: Sequence[Mapping[str, Any]],
    ladder: Mapping[str, Any],
    metric_rows: Sequence[Mapping[str, Any]],
    extra: Mapping[str, Any] | None = None,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    extra_d = dict(extra or {})
    # Dual-gate honesty: never let override + failed A look like silent complete.
    status = ladder.get("status")
    if analysis == "nlrha" or str(analysis).lower() == "nlrha":
        from .stop_rule import finalize_nlrha_score_status, STATUS_NLRHA_COMPLETE
        ga = ladder.get("gate_a") or extra_d.get("gate_a") or {}
        gb = ladder.get("gate_b") or extra_d.get("gate_b") or {}
        override = bool(
            extra_d.get("michael_override")
            or ga.get("overridden")
            or ga.get("michael_override")
            or (ladder.get("flags") or {}).get("michael_override")
        )
        honest = finalize_nlrha_score_status(
            ga, gb, michael_override=override, dual_status=status,
        )
        # Only force-downgrade a bogus complete; leave refine/continue as-is when A passed.
        if status == STATUS_NLRHA_COMPLETE and not honest["nlrha_complete"]:
            status = honest["status"]
            extra_d["nlrha_complete"] = False
            extra_d["honesty"] = honest
            if honest.get("message"):
                extra_d["message"] = honest["message"]
        elif override and not ga.get("passed"):
            status = honest["status"]
            extra_d["nlrha_complete"] = False
            extra_d["honesty"] = honest
            if honest.get("message"):
                extra_d.setdefault("message", honest["message"])
        else:
            extra_d.setdefault("nlrha_complete", status == STATUS_NLRHA_COMPLETE)
            extra_d.setdefault("honesty", honest)

        # Surface suite FC vs probe FC separately when present on rows / ladder.
        suite_fc = extra_d.get("suite_worst_FC_DC")
        probe_fc = extra_d.get("probe_worst_FC_DC")
        if suite_fc is None:
            locked = ladder.get("locked_suite") or extra_d.get("locked_suite") or {}
            if "worst_FC_DC" in (locked or {}):
                suite_fc = locked.get("worst_FC_DC")
            elif metric_rows:
                # First full-suite / lock row
                suite_fc = metric_rows[0].get("suite_worst_FC_DC", metric_rows[0].get("worst_FC_DC"))
        if probe_fc is None and metric_rows:
            last = metric_rows[-1]
            probe_fc = last.get("probe_worst_FC_DC", last.get("worst_FC_DC"))
        if suite_fc is not None or probe_fc is not None:
            extra_d.setdefault("suite_worst_FC_DC", suite_fc)
            extra_d.setdefault("probe_worst_FC_DC", probe_fc)
        if ladder.get("gate_b") and ladder["gate_b"].get("fail_reason"):
            extra_d.setdefault("gate_b_fail_reason", ladder["gate_b"]["fail_reason"])

    doc = dict(
        case=case,
        analysis=analysis,
        method=extra_d.get("method") or ("modimk→pz→fibre" if extra_d.get("nlrha_method_ladder") else "fibre"),
        tol=ladder.get("tol"),
        tol_note="relative band on primary metrics (default 10%)",
        max_rungs=ladder.get("max_rungs"),
        status=status,
        stop_level=ladder.get("stop_level"),
        stop_rung=(rungs[ladder["stop_level"]]["level"] if ladder.get("stop_level") is not None and ladder["stop_level"] < len(rungs) else None),
        rungs=[dict(level=r.get("level"), intent=r.get("intent"), knobs={k: r[k] for k in r if k not in ("intent",)}) for r in rungs],
        metrics=list(metric_rows),
        steps=ladder.get("steps"),
        written=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    if extra_d:
        doc["extra"] = dict(extra_d)
    path = os.path.join(out_dir, "mesh_convergence_scorecard_%s.json" % analysis)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, default=str)
    md = os.path.join(out_dir, "mesh_convergence_scorecard_%s.md" % analysis)
    lines = [
        "# Mesh-convergence scorecard — %s / %s" % (case, analysis),
        "",
        "- method: `%s`" % doc.get("method"),
        "- status: `%s`" % doc.get("status"),
        "- stop_level: %s (%s)" % (doc.get("stop_level"), doc.get("stop_rung")),
        "- tol: %s" % doc.get("tol"),
        "- max_rungs: %s" % doc.get("max_rungs"),
    ]
    if doc.get("extra"):
        for k in (
            "message", "no_fibre_for_fc", "nlrha_method_ladder", "lock_stage",
            "nlrha_complete", "michael_override", "suite_worst_FC_DC", "probe_worst_FC_DC",
            "gate_b_fail_reason",
        ):
            if k in doc["extra"] and doc["extra"][k] is not None:
                lines.append("- %s: %s" % (k, doc["extra"][k]))
    open(md, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return path
