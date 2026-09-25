"""grounding.py -- what provenance this project's component parameters actually have.

There are three states, not two, and the difference between the middle one and the last one is the
whole point of the Revise step:

  verified    the printed values were read out of the converted standard and copied into the
              parameters file, with the table and page in `source` -- the contract hinge_params.json's
              own _README sets out. This is a statement about where the numbers came from.
  grounded    `snl revise` retrieved a clause for each component group and recorded the citation, but
              did NOT read the printed values out of it: the numbers in the file are still whatever
              was there before. Retrieval is not the check.
  unverified  nobody has looked. The backbones are placeholders written from memory.

Before this module the reports asked one boolean (`verified`), so a project whose clauses had just
been retrieved and cited looked exactly like one where nothing had happened -- the red UNVERIFIED
banner came back after every Revise, and the advice to "re-run the analyses to pick the citation up"
sent the engineer round a loop that could not terminate: the run copies the input parameters over
pushover/hinge_params_used.json (pushover/cli.py), erasing the annotation Revise had just written.

So the record Revise leaves lives at the JOB ROOT, in revise_evidence.json, which the analysis run
never writes -- and it carries a fingerprint of the parameters it was taken against, so grounding
recorded for one parameter set can never be shown against another.
"""
from __future__ import annotations

import hashlib
import json
import os

VERIFIED, GROUNDED, UNVERIFIED = "verified", "grounded", "unverified"
EVIDENCE = "revise_evidence.json"

# the keys that ARE the modelling parameters; `verified`, `source`, `grounding` and the readme are
# provenance about them, and a re-run rewriting those must not look like a different parameter set
_META = ("_README", "verified", "source", "grounding")


def fingerprint(prm: dict) -> str:
    """A stable hash of the modelling parameters themselves, ignoring provenance keys."""
    body = {k: v for k, v in (prm or {}).items() if k not in _META}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def job_root(where: str) -> str:
    """The project folder, given the job itself or one of its output folders (pushover/, nlrha/)."""
    where = os.path.abspath(where)
    if os.path.exists(os.path.join(where, EVIDENCE)):
        return where
    up = os.path.dirname(where)
    return up if os.path.exists(os.path.join(up, EVIDENCE)) else where


def evidence(where: str) -> dict | None:
    """The grounding Revise recorded for this project, or None. Never raises."""
    p = os.path.join(job_root(where), EVIDENCE)
    try:
        with open(p, encoding="utf-8") as f:
            ev = json.load(f)
    except (OSError, ValueError):
        return None
    return ev if isinstance(ev, dict) else None


def state(prm: dict, where: str) -> tuple[str, dict | None]:
    """-> (VERIFIED | GROUNDED | UNVERIFIED, the evidence when it applies).

    Evidence taken against different parameters does not count: it is reported as UNVERIFIED, which
    is the honest answer -- the clauses were looked up for something else."""
    prm = prm or {}
    if prm.get("verified"):
        return VERIFIED, None
    ev = evidence(where)
    if not ev:
        return UNVERIFIED, None
    groups = {k: v for k, v in (ev.get("groups") or {}).items() if v.get("grounded")}
    if not groups:
        return UNVERIFIED, None
    fp = (ev.get("params") or {}).get("fingerprint")
    if fp and fp != fingerprint(prm):
        return UNVERIFIED, None                    # recorded against a different parameter set
    return GROUNDED, ev


def citations(ev: dict | None) -> list[str]:
    if not ev:
        return []
    out = []
    for k, v in (ev.get("groups") or {}).items():
        if v.get("grounded") and v.get("citation"):
            out.append("%s %s" % (k.replace("_", " "), v["citation"]))
    return out


def clause_citations(ev: dict | None) -> list[str]:
    if not ev:
        return []
    return ["%s %s" % (k, v.get("citation") or v) for k, v in (ev.get("clauses") or {}).items()
            if (v.get("grounded") if isinstance(v, dict) else v)]


# ---------------------------------------------------------------- how it reads in a report
MARK = 'data-provenance'


def block_html(st: str, ev: dict | None, prm: dict | None = None) -> str:
    """The provenance block a supplement carries, marked so Revise can replace it in place."""
    prm = prm or {}
    if st == VERIFIED:
        return ('<div class="note" %s="verified">COMPONENT PARAMETERS VERIFIED — %s</div>'
                % (MARK, _esc(str(prm.get("source") or "reconciled against the printed tables."))))
    if st == GROUNDED:
        cites = "; ".join(citations(ev)) or "the corpus on this PC"
        asked = _esc(str((ev or {}).get("asked") or ""))
        return ('<div class="note" %s="grounded"><b>CLAUSE RETRIEVED — VALUES NOT READ FROM IT.</b> '
                'A clause was retrieved from the licensed corpus on this PC%s and is cited here: %s. The '
                'numeric backbone and acceptance values in hinge_params_used.json were NOT read out of that '
                'table and copied in — they are still the file\'s previous values, and <code>verified</code> '
                'reflects that. Retrieval log: revise_evidence.json.</div>'
                % (MARK, (" on " + asked) if asked else "", _esc(cites)))
    return ('<div class="banner" %s="unverified">UNVERIFIED MODELLING PARAMETERS — hinge_params.json has '
            'verified=false and no clause has been retrieved for it. Backbone and acceptance values are '
            'placeholders written from memory; they have NOT been read out of a licensed ASCE 41-23 / '
            'AISC 342-22. Do not rely on any acceptance ratio below until the Review and Revise steps have '
            'run. Source note: %s</div>' % (MARK, _esc(str(prm.get("source") or ""))))


def summary(st: str, ev: dict | None, prm: dict | None = None, width: int = 110) -> str:
    """One line, for a table cell or the .docx."""
    prm = prm or {}
    if st == VERIFIED:
        return "verified · " + str(prm.get("source") or "")[:width]
    if st == GROUNDED:
        return "clause retrieved (" + ("; ".join(citations(ev)) or "corpus")[:width] + ") · values not read from it"
    return "UNVERIFIED placeholders"


_DISCLOSURE = "Cyclic deterioration"


def _carried_disclosure(block: str) -> str:
    """Any non-provenance disclosure inside a block being replaced, preserved as a note of its own."""
    i = block.find(_DISCLOSURE)
    if i < 0:
        return ""
    tail = block[i:]
    tail = tail[:tail.rfind("</div>")] if "</div>" in tail else tail
    return '<div class="note">%s</div>' % tail.strip()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- patching a report already written
PATCHED, UNCHANGED, ABSENT = "patched", "unchanged", "absent"


def patch(path: str, st: str, ev: dict | None, prm: dict | None = None) -> str:
    """Replace the provenance block in a supplement that is already on disk.

    -> PATCHED (rewritten) | UNCHANGED (already says this) | ABSENT (no block to replace).
    Running Revise twice is not an error, and must not be reported as one.

    This is what lets Revise finish the job. The supplements are written from live analysis objects
    during the run, so they cannot be re-rendered without re-running -- and re-running is exactly
    what erases the grounding. Only the provenance block is touched; not one number moves.
    """
    import re
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return ABSENT
    new = block_html(st, ev, prm)
    # the marked form this module writes, and the legacy unmarked banner that predates it
    pats = [re.compile(r'<div class="(?:banner|note)" %s="[a-z]+">.*?</div>' % MARK, re.S),
            re.compile(r'<div class="banner">UNVERIFIED (?:MODELLING|COMPONENT) PARAMETERS.*?</div>', re.S)]
    for p in pats:
        m = p.search(html)
        if m:
            # The legacy NLRHA banner carried a SECOND disclosure -- cyclic deterioration is off, though
            # 16.3.1 asks for it. That is a modelling disclosure, not provenance, and replacing the block
            # must not quietly delete it.
            keep = _carried_disclosure(m.group(0))
            out = p.sub(lambda _m: new + keep, html, count=1)
            if out == html:
                return UNCHANGED
            with open(path, "w", encoding="utf-8") as f:
                f.write(out)
            return PATCHED
    return ABSENT
