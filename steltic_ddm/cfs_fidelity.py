"""CFS DDM Du&Hajjar Tier-2 fidelity gate (product rule 2).

Portal / CFS DDM entry must run at analysis_fidelity ≥ 2 (beam fibre path).
Shell is never the product default. Hard-fail below 2 unless --force.
"""
from __future__ import annotations
from typing import Any, Mapping, Optional


CFS_DDM_MIN_FIDELITY = 2


def _read_fidelity(cfg: Mapping[str, Any]) -> Optional[int]:
    raw = cfg.get("analysis_fidelity")
    if raw is None and isinstance(cfg.get("analysis_basis"), dict):
        raw = cfg["analysis_basis"].get("analysis_fidelity") or cfg["analysis_basis"].get("tier")
    if raw is None:
        raw = cfg.get("tier")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def cfs_ddm_fidelity_gate(
    cfg: Mapping[str, Any],
    *,
    force: bool = False,
    min_tier: int = CFS_DDM_MIN_FIDELITY,
) -> dict:
    """Hard-fail CFS DDM when analysis_fidelity < min_tier unless force.

    Returns dict: ok, fidelity, min_tier, forced, message, error (optional).
    Does not consult shell defaults — Tier 2 beam is always required.
    """
    fid = _read_fidelity(cfg)
    if fid is None:
        msg = (
            "CFS DDM requires analysis_fidelity≥%d (Tier 2 beam fibre); "
            "cfg has no analysis_fidelity — set cfg['analysis_fidelity']=%d "
            "(no shell product path)" % (min_tier, min_tier)
        )
        if force:
            return dict(ok=True, fidelity=None, min_tier=min_tier, forced=True,
                        message=msg + " (--force overrides)")
        return dict(ok=False, fidelity=None, min_tier=min_tier, forced=False,
                    message=msg, error=msg)

    if fid < min_tier:
        msg = (
            "CFS DDM fidelity gate: analysis_fidelity=%s < %d (Tier 2 required; "
            "no shell default). Raise cfg['analysis_fidelity'] or pass --force."
            % (fid, min_tier)
        )
        if force:
            return dict(ok=True, fidelity=fid, min_tier=min_tier, forced=True,
                        message=msg + " (--force overrides)")
        return dict(ok=False, fidelity=fid, min_tier=min_tier, forced=False,
                    message=msg, error=msg)

    return dict(
        ok=True,
        fidelity=fid,
        min_tier=min_tier,
        forced=False,
        message="CFS DDM Tier %d gate passed (analysis_fidelity=%d)" % (min_tier, fid),
    )
