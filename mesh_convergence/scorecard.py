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
    doc = dict(
        case=case,
        analysis=analysis,
        method=(extra or {}).get("method") or ("modimk→pz→fibre" if (extra or {}).get("nlrha_method_ladder") else "fibre"),
        tol=ladder.get("tol"),
        tol_note="relative band on primary metrics (default 10%)",
        max_rungs=ladder.get("max_rungs"),
        status=ladder.get("status"),
        stop_level=ladder.get("stop_level"),
        stop_rung=(rungs[ladder["stop_level"]]["level"] if ladder.get("stop_level") is not None and ladder["stop_level"] < len(rungs) else None),
        rungs=[dict(level=r.get("level"), intent=r.get("intent"), knobs={k: r[k] for k in r if k not in ("intent",)}) for r in rungs],
        metrics=list(metric_rows),
        steps=ladder.get("steps"),
        written=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    if extra:
        doc["extra"] = dict(extra)
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
        for k in ("message", "no_fibre_for_fc", "nlrha_method_ladder", "lock_stage"):
            if k in doc["extra"] and doc["extra"][k] is not None:
                lines.append("- %s: %s" % (k, doc["extra"][k]))
    open(md, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return path
