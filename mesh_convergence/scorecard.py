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
        method="fibre",
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
    return path
