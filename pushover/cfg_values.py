"""cfg_values.py -- the package's cfg.py values as the EXECUTED cfg dict, never regexed out of the text (NL-R2-06).

cfg.py is the HR engine's input: a `cfg = dict(...)` that calls engine helpers (e.g. `seis=eng.seis(SDS, SD1, S1, R, ...)`)
and carries a docstring / comments that may contain the same words ("heights = [14, 12] ft", "Risk Category IV
neighbour", "drift_limit = 1.0 % of ..."). A regex over the text reads whatever comes first; the executed dict is what
the design used. Same policy as NL-R2-06 (commit 4e64b9f, heights):

  * the value comes from the executed cfg (steltic_ddm.ingest.load_cfg: cfg.py run with the HR engine importable);
  * when cfg.py is absent or cannot be executed, or the key is not in the executed cfg, the caller falls back to the
    next INDEPENDENT package source it already had (report.html, calc_package.json, Ie, a documented default) and
    names that source -- the cfg text is never searched.

Seismic scalars live in cfg['seis'] (the dict eng.seis returns); `seis_value` looks there first, then at the top level.
"""
from __future__ import annotations
import hashlib
from pathlib import Path

SOURCE = "cfg.py (executed)"
_CACHE = {}


def executed_cfg(root):
    """(cfg dict, SOURCE) of the package at `root`, or (None, reason). Cached on the cfg.py content, so an edited cfg.py
    (feedback loops) is executed again."""
    p = Path(str(root)) / "cfg.py"
    if not p.exists():
        return None, "no cfg.py"
    try:
        key = (str(p.resolve()), hashlib.sha1(p.read_bytes()).hexdigest())
    except OSError as ex:
        return None, "cfg.py unreadable (%s)" % str(ex)[:80]
    if key not in _CACHE:
        try:
            from steltic_ddm.ingest import load_cfg
            _CACHE[key] = (load_cfg(str(p.parent)), SOURCE)
        except BaseException as ex:                               # noqa: BLE001 -- engine missing, cfg error, sys.exit in cfg
            if isinstance(ex, KeyboardInterrupt):
                raise
            _CACHE[key] = (None, "cfg.py not executable (%s)" % str(ex)[:80])
    return _CACHE[key]


def get(cfg, key, default=None):
    """Top-level cfg[key] (None-safe)."""
    if not isinstance(cfg, dict):
        return default
    v = cfg.get(key)
    return default if v is None else v


def seis_value(cfg, key, default=None):
    """A seismic design value: cfg['seis'][key] (eng.seis / dict literal), else the top-level cfg[key]."""
    if not isinstance(cfg, dict):
        return default
    s = cfg.get("seis")
    if isinstance(s, dict) and s.get(key) is not None:
        return s[key]
    v = cfg.get(key)
    return default if v is None else v


def number(v):
    """float(v) for a real number (not bool); None otherwise."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def num_map(v):
    """{int level: float} from a dict of numbers (L_by_level / snow_by_level); {} when not such a dict."""
    out = {}
    if isinstance(v, dict):
        for k, x in v.items():
            try:
                out[int(k)] = float(x)
            except (TypeError, ValueError):
                return {}
    return out


def clear_cache():
    _CACHE.clear()


__all__ = ["executed_cfg", "get", "seis_value", "number", "num_map", "SOURCE", "clear_cache"]
