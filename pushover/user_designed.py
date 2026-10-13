"""user_designed.py -- a building the USER designed, built by HR in build-only mode (user decision 12 Oct).

HR models the user's design exactly as given (steltic.build_only: nothing resized, nothing invented) and writes
provenance.json beside cfg.py: the source of every cfg value (the user's file, an answer, an accepted default, a
derived rule). Before the long runs the user confirms the model here (`snl confirm`), which writes nl_basis.json.
`snl run` refuses a build-only package that has not been confirmed, and every report carries the statement
"User-designed building: design basis supplied by the user, not checked by Steltic HR".

Confirmation checks (checks()):
  hard -- complete (nothing open in missing_items.json), supported (version 1 systems), built as described
          (cfg.py is the generated one; the model's element counts and sections match the description),
          stable (no mechanism: T1 below 20 s, HR's equilibrium / modal-mass checks pass),
          mass in the model (the model file's masses carry the seismic weight HR computed);
  warn -- the user's own T1, V and W against the model (user decision: warning only, the run proceeds; the
          difference is printed in every report).
"""
from __future__ import annotations

import csv
import hashlib
import html as _h
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

G_IN = 386.088                       # in/s^2
V1_SYSTEMS = ("SMF", "IMF", "OMF", "SCBF", "OCBF", "BRBF", "EBF")
TOL = dict(W=0.10, V=0.10, T=0.15)   # warning thresholds for the user's own results


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_user_designed(root) -> bool:
    pv = _load(Path(root) / "provenance.json") if root else None
    return bool(pv) and pv.get("schema") == "steltic.provenance"


def _sha(p):
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def info(root=None):
    """None for a normal HR package; else dict(declared, confirmed, provenance, basis, defaults, warnings, ...)."""
    if root is None or not is_user_designed(root):
        return None
    root = Path(root)
    pv = _load(root / "provenance.json") or {}
    nb = _load(root / "nl_basis.json")
    cfg_sha = _sha(root / "cfg.py")
    confirmed = bool(nb and nb.get("confirmed") and nb.get("cfg_sha256") == cfg_sha)
    src = ((pv.get("cfg") or {}).get("build_only") or {})
    desc = _load(root / "building_description.json") or {}
    return dict(declared=True, confirmed=confirmed, stale=bool(nb and nb.get("cfg_sha256") != cfg_sha),
                source=(desc.get("source") or {}), description_sha256=pv.get("description_sha256"),
                accepted_defaults=pv.get("accepted_defaults") or [], derived=pv.get("derived") or [],
                warnings=(nb or {}).get("warnings") or [], override_note=(nb or {}).get("note"),
                confirmed_by=(nb or {}).get("by"), confirmed_at=(nb or {}).get("at"), nl_basis=nb, provenance_note=src)


def _defaults_text(inf, cap=12):
    ad = inf.get("accepted_defaults") or []
    if not ad:
        return "none"
    s = "; ".join("%s = %s" % (a.get("item"), json.dumps(a.get("value")) if not isinstance(a.get("value"), str) else a.get("value"))
                  for a in ad[:cap])
    return s + ("; ... and %d more (provenance.json)" % (len(ad) - cap) if len(ad) > cap else "")


def warning_text(inf, engine="this analysis") -> str:
    if not inf:
        return ""
    src = inf.get("source") or {}
    what = ("%s %s" % (src.get("kind") or "", src.get("file") or "")).strip() or "the user's description"
    conf = ("confirmed by %s on %s" % (inf.get("confirmed_by") or "the user", inf.get("confirmed_at"))
            if inf.get("confirmed") else "NOT CONFIRMED")
    warns = "; ".join(inf.get("warnings") or []) or "none"
    note = (" Override note: %s." % inf["override_note"]) if inf.get("override_note") else ""
    return ("USER-DESIGNED BUILDING: design basis supplied by the user, not checked by Steltic HR. HR built the model in "
            "build-only mode from %s (nothing resized, nothing invented; provenance.json gives the source of every "
            "value) -- %s. Accepted defaults: %s. Confirmation warnings: %s.%s HR member D/C ratios were not computed; "
            "%s reports the user's design as given."
            % (what, conf, _defaults_text(inf), warns, note, engine))


def banner_html(inf, engine="this analysis") -> str:
    if not inf:
        return ""
    return ('<div class="banner user-designed" style="border:2px solid #1f4e8c;background:#eaf1fb;color:#0d2a4f;'
            'padding:10px 12px;margin:10px 0;font-weight:600">%s</div>' % _h.escape(warning_text(inf, engine)))


def print_warning(inf, engine="this analysis"):
    if inf:
        print("!! " + warning_text(inf, engine), flush=True)


# ------------------------------------------------------------------------------------------- the checks
def _model_counts(root):
    t = (Path(root) / "model_opensees.py").read_text(encoding="utf-8", errors="replace")
    i = t.rfind("ops.wipe()")                                    # the export replays the builder; the last build is the model
    if i > 0:
        t = t[i:]
    kinds = Counter()
    for m in re.finditer(r"ops\.element\(\s*['\"](\w+)['\"]", t):
        kinds[m.group(1)] += 1
    mass = 0.0
    for m in re.finditer(r"ops\.mass\(\s*\d+\s*,\s*([-\d.eE+]+)", t):
        mass += float(m.group(1))
    return kinds, mass * G_IN


def _schedule(root):
    p = Path(root) / "design" / "member_schedule.csv"
    c = Counter()
    if p.exists():
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                c[(str(r.get("member") or r.get("kind") or "").lower(), str(r.get("section") or "").upper())] += 1
    return c


def _rel(a, b):
    return abs(a - b) / abs(b) if b else float("inf")


def checks(root) -> dict:
    root = Path(root)
    out = []

    def add(cid, label, status, detail, hard=True):
        out.append(dict(id=cid, label=label, status=status, detail=detail, hard=hard))

    pv = _load(root / "provenance.json") or {}
    mi = _load(root / "missing_items.json") or {}
    desc = _load(root / "building_description.json") or {}
    pr = _load(root / "design" / "pipeline_results.json") or {}
    # 1 complete
    if mi.get("ready"):
        add("complete", "Complete", "pass", "nothing open in missing_items.json; every cfg value has a source in provenance.json")
    else:
        add("complete", "Complete", "fail", "open items: %s" % (mi.get("counts") or "missing_items.json not found"))
    # 2 supported
    se = desc.get("seismic") or {}
    sysd = se.get("system") or {}
    bad = []
    for d in ("X", "Y"):
        s = str(sysd.get(d) or "").upper().replace("DUAL_", "").replace("_", "+")
        parts = [p for p in re.split(r"[+ /]", s) if p]
        if not parts or any(p not in V1_SYSTEMS for p in parts):
            bad.append("%s: %s" % (d, sysd.get(d)))
    add("supported", "Supported", "fail" if bad else "pass",
        ("system not in version 1 -- " + "; ".join(bad)) if bad else "systems X %s, Y %s are version 1 systems" % (sysd.get("X"), sysd.get("Y")))
    # 3 built as described
    probs = []
    cfg_sha = _sha(root / "cfg.py")
    if cfg_sha != pv.get("cfg_sha256"):
        probs.append("cfg.py differs from the generated one (provenance cfg_sha256)")
    if pr.get("cfg_sha256") and pr.get("cfg_sha256") != cfg_sha:
        probs.append("the HR pipeline ran on a different cfg.py")
    ch = pr.get("checks") or {}
    for k in ("model_declared", "model_consistent"):
        if ch.get(k) is False:
            probs.append("HR %s check failed" % k)
    try:
        kinds, Wm = _model_counts(root)
    except OSError:
        kinds, Wm = Counter(), 0.0
        probs.append("model_opensees.py missing")
    want = ((pv.get("members") or {}).get("elements") or {})
    n_frame = kinds.get("elasticBeamColumn", 0) + kinds.get("forceBeamColumn", 0) + kinds.get("dispBeamColumn", 0)
    n_truss = kinds.get("Truss", 0) + kinds.get("corotTruss", 0)
    if want:
        if n_frame != want.get("col", 0) + want.get("beam", 0):
            probs.append("model has %d beam-column elements, the description %d columns + beams (segments)"
                         % (n_frame, want.get("col", 0) + want.get("beam", 0)))
        if n_truss != want.get("brace", 0):
            probs.append("model has %d truss elements, the description %d braces" % (n_truss, want.get("brace", 0)))
    sched = _schedule(root)
    if sched and want:
        n_sched = sum(sched.values())
        if n_sched != sum(want.values()):
            probs.append("member schedule lists %d members, the description %d element segments" % (n_sched, sum(want.values())))
    add("built", "Built as described", "fail" if probs else "pass",
        "; ".join(probs) if probs else "cfg.py is the generated one; %d column/beam segments and %d braces as described"
        % (n_frame, n_truss))
    # 3b a stable model: a mechanism shows as a period of thousands of seconds and failed equilibrium checks
    T_ = list((pr.get("summary") or {}).get("T") or [])
    bad_eq = [k for k in (pr.get("failed") or []) if k.startswith("equil") or k in ("stability", "modalmass_X", "modalmass_Y")]
    if T_ and (T_[0] > 20.0 or bad_eq):
        add("stable", "Stable model", "fail", "T1 = %.3g s%s: the model is a mechanism or nearly so -- check the supports, "
            "releases and the members that frame into each joint" % (T_[0], ("; HR checks failed: " + ", ".join(bad_eq)) if bad_eq else ""))
    elif T_:
        add("stable", "Stable model", "pass", "T1 = %.3f s; HR's equilibrium and modal-mass checks pass" % T_[0])
    else:
        add("stable", "Stable model", "fail", "no modal result in design/pipeline_results.json")
    # 4 mass in the model
    W_hr = (pr.get("summary") or {}).get("W")
    if W_hr and Wm:
        r = _rel(Wm, W_hr)
        add("mass", "Mass in the model", "pass" if r <= 0.02 else "fail",
            "model masses %.0f kip vs HR seismic weight %.0f kip (%.1f %%)" % (Wm, W_hr, 100 * r))
    else:
        add("mass", "Mass in the model", "fail", "could not read the model masses or HR's seismic weight")
    # 5 the user's own results (warning only)
    ur = desc.get("user_results") or {}
    warns = []
    if not ur:
        add("user_results", "Your own results", "skipped", "no results of your own analysis were given: check skipped", hard=False)
    else:
        sm = pr.get("summary") or {}
        rows = []
        T = list(sm.get("T") or [])
        for d in ("X", "Y"):
            u = (ur.get("T1_s") or {}).get(d)
            if u and T:
                m = min(T[:3], key=lambda t: abs(t - float(u)))
                r = _rel(m, float(u))
                rows.append("T1 %s: yours %.3f s, model %.3f s (%+.0f %%)" % (d, float(u), m, 100 * (m - float(u)) / float(u)))
                if r > TOL["T"]:
                    warns.append("T1 %s differs by %.0f %% (yours %.3f s, model %.3f s)" % (d, 100 * r, float(u), m))
            u = (ur.get("V_kip") or {}).get(d)
            if u and sm.get("V"):
                r = _rel(float(sm["V"]), float(u))
                rows.append("V %s: yours %.0f kip, model ELF %.0f kip (%+.0f %%)" % (d, float(u), sm["V"], 100 * (sm["V"] - float(u)) / float(u)))
                if r > TOL["V"]:
                    warns.append("V %s differs by %.0f %% (yours %.0f kip, model %.0f kip)" % (d, 100 * r, float(u), sm["V"]))
        u = ur.get("W_kip")
        if u and W_hr:
            r = _rel(W_hr, float(u))
            rows.append("W: yours %.0f kip, model %.0f kip (%+.0f %%)" % (float(u), W_hr, 100 * (W_hr - float(u)) / float(u)))
            if r > TOL["W"]:
                warns.append("W differs by %.0f %% (yours %.0f kip, model %.0f kip)" % (100 * r, float(u), W_hr))
        add("user_results", "Your own results", "warn" if warns else "pass",
            ("; ".join(rows) or "nothing comparable given") + (" -- WARNING ONLY: the run may proceed" if warns else ""), hard=False)
    hard_ok = all(c["status"] == "pass" for c in out if c["hard"])
    return dict(ok=hard_ok, checks=out, warnings=warns, accepted_defaults=pv.get("accepted_defaults") or [],
                derived=pv.get("derived") or [], cfg_sha256=cfg_sha, description_sha256=pv.get("description_sha256"),
                tolerances=TOL)


def confirm(root, by=None, note=None) -> dict:
    """Write nl_basis.json when every hard check passes (warnings are recorded, not blocking). Returns the checks
    with "confirmed"."""
    root = Path(root)
    c = checks(root)
    c["confirmed"] = False
    if not c["ok"]:
        return c
    nb = dict(schema="steltic.nl_basis", version=1, confirmed=True, at=time.strftime("%Y-%m-%d %H:%M"),
              by=by or os.environ.get("USER") or os.environ.get("USERNAME") or "user", note=note,
              cfg_sha256=c["cfg_sha256"], description_sha256=c["description_sha256"], checks=c["checks"],
              warnings=c["warnings"], accepted_defaults=c["accepted_defaults"], tolerances=c["tolerances"])
    (root / "nl_basis.json").write_text(json.dumps(nb, indent=1), encoding="utf-8")
    c["confirmed"] = True
    c["nl_basis"] = str(root / "nl_basis.json")
    return c
