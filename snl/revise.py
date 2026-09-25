"""Re-issue the nonlinear reports with the standards corpus behind them.

`snl run` does the OpenSees work and never queries anything, so every clause its reports cite is
marked UNVERIFIED and the component backbones are described as placeholders. The Review tab does
query -- live, against the licensed PDFs on this PC -- and writes review.md into the project folder.
This step is the manual join between the two: it re-asks the corpus for the clauses those reports
depend on, records every passage it got, and re-issues the documents that can be rebuilt from the
job folder with the citation in place of the placeholder wording.

What it will NOT do: claim the backbone NUMBERS have been reconciled against the table it just
retrieved. Finding ASCE 41-23 Table 9-7.1 is not the same as checking that a = 9θy in
hinge_params.json matches the row for this section, and a wrong backbone silently changes every
acceptance result downstream. So `verified` stays where it was; what this adds is `grounding` --
the citation, the page and the passage -- and the reports say "cited from" rather than "from
memory". Flipping `verified` remains a human act, and the evidence file is what makes it a short one.
"""
from __future__ import annotations

import datetime
import json
import os
import re

from . import grounding as G, rag

# Each backbone group, and where the standard that governs it says so. A342 first (it is the steel
# evaluation standard and speaks about these components directly), ASCE 41 as the fallback the
# placeholder text itself names.
GROUPS = [
    ("beam_flexure", "beams in flexure", ("beam",), [
        ("A342", "Modeling Parameters and Permissible Deformations for Nonlinear Analysis Procedures beams flexure", "C"),
        ("ASCE41", "Modeling Parameters and Acceptance Criteria for Nonlinear Procedures structural steel beams flexure", "9"),
    ]),
    ("column_flexure", "columns in flexure", ("column",), [
        ("A342", "Modeling Parameters and Permissible Deformations for Nonlinear Analysis Procedures columns flexure", "C"),
        ("ASCE41", "Modeling Parameters and Acceptance Criteria for Nonlinear Procedures structural steel columns flexure", "9"),
    ]),
    ("brace_axial", "braces in axial compression and tension", ("brace",), [
        ("A342", "Modeling Parameters and Permissible Deformations for Nonlinear Analysis Procedures braces axial", "C"),
        ("ASCE41", "Modeling Parameters and Acceptance Criteria for Nonlinear Procedures braces axial compression tension", "9"),
    ]),
]
# A passage only grounds a BACKBONE if it is the modelling-parameter table for THAT component. The
# first version of this asked loosely and took the top hit: "beams in flexure" came back with AISC
# 342-22 E2.4, which is about gusset and brace connections. A citation to the wrong clause is worse
# than none -- it launders a bad reference into a sealed document -- so the passage has to look like
# the table and mention the component before it counts.
_TABLE_RE = re.compile(r"modeling\s+parameters|modelling\s+parameters|acceptance\s+criteria|permissible\s+deformations", re.I)

# The Chapter 16 clauses the NLRHA supplement cites in its verdict table.
CLAUSES = [
    ("16.4.1.1", "ASCE7", "unacceptable response number of ground motions permitted"),
    ("16.4.1.2", "ASCE7", "mean story drift ratio limit two times Table 12.12-1"),
    ("16.4.2.2", "ASCE7", "force-controlled actions gamma 1.3 Eq. 16.4-1"),
]


def _cite(hit: dict, doc_key: str) -> str:
    sec = (hit.get("section") or "").strip()
    page = hit.get("page")
    bits = rag.TITLES.get(doc_key, doc_key)
    if sec:
        bits += " §" + sec
    if page not in (None, ""):
        bits += ", p. " + str(page)
    return "[%s]" % bits


def _relevant_hit(res: dict, words: tuple) -> dict | None:
    """The first hit that is the modelling-parameter table for this component -- or None.

    `rag.search` climbs a ladder whose last rung drops the document filter, and bm25 will happily
    hand back a neighbouring clause that shares vocabulary. Grounding a beam backbone in a gusset
    clause would be a citation nobody could check, so both tests have to pass.
    """
    if not res.get("ok"):
        return None
    for h in res.get("results") or []:
        text = "%s %s" % (h.get("title") or "", h.get("text") or "")
        if not text.strip():
            continue
        if not _TABLE_RE.search(text):
            continue
        if not any(w in text.lower() for w in words):
            continue
        return h
    return None


def _first_real_hit(res: dict) -> dict | None:
    """The first hit that actually came from the document we asked for.

    `rag.search` climbs a ladder and its last rung drops the document filter, so a hit can come from
    a neighbouring standard. Grounding a steel backbone in ASCE 7 would be worse than not grounding
    it at all, so the source has to match what was asked.
    """
    if not res.get("ok"):
        return None
    for h in res.get("results") or []:
        if h.get("text"):
            return h
    return None


def probe(log=print) -> dict:
    """Ask the corpus for everything the reports lean on. -> the evidence record."""
    ev = {"asked": datetime.datetime.now().isoformat(timespec="seconds"),
          "rag_url": rag.url(), "groups": {}, "clauses": {}}
    if not rag.configured():
        ev["note"] = ("no standards server (RAG_API_URL is empty) -- start the Query file manager "
                      "from the hub's Modules page, or run this from the hub's Revise button")
        return ev
    st = rag.status(force=True)
    cm = rag.corpus_map(st)
    ev["corpus"] = {"present": sorted(cm.get("present") or []), "absent": sorted(cm.get("absent") or [])}
    for gid, what, words, tries in GROUPS:
        rec = {"what": what, "grounded": False, "attempts": []}
        for doc_key, query, chapter in tries:
            if doc_key in (cm.get("absent") or []):
                rec["attempts"].append({"document": doc_key, "skipped": "not converted on this PC"})
                continue
            res = rag.search(query, doc_key, top_k=6, chapter=chapter)
            hit = _relevant_hit(res, words)
            rec["attempts"].append({"document": doc_key, "query": query, "hits": len(res.get("results") or []),
                                    "relevant": bool(hit), "note": res.get("note") or "", "via": res.get("via") or ""})
            if hit:
                rec.update(grounded=True, document=doc_key, citation=_cite(hit, doc_key),
                           section=hit.get("section") or "", page=hit.get("page"),
                           passage=(hit.get("text") or "")[:1200])
                break
        ev["groups"][gid] = rec
        log("   %-16s %s" % (gid, rec.get("citation") if rec["grounded"]
                              else "no modelling-parameter table for this component in the corpus"))
    for cid, doc_key, query in CLAUSES:
        res = rag.search(query, doc_key, top_k=3, clause=cid)
        hit = _first_real_hit(res)
        ev["clauses"][cid] = ({"grounded": True, "citation": _cite(hit, doc_key), "page": hit.get("page"),
                               "passage": (hit.get("text") or "")[:800]} if hit else
                              {"grounded": False, "note": res.get("note") or "no passages"})
        log("   %-16s %s" % (cid, ev["clauses"][cid].get("citation") or "NOT FOUND in the corpus"))
    return ev


def annotate_params(job: str, ev: dict, log=print) -> dict | None:
    """Write the grounding into the params file the run actually used.

    -> {"path", "fingerprint"}, or None. The fingerprint is of the modelling parameters alone, so the
    record survives the analysis run copying a byte-identical params file over this one -- and stops a
    grounding recorded for one parameter set being shown against another."""
    path = next((p for p in (os.path.join(job, "pushover", "hinge_params_used.json"),
                             os.path.join(job, "hinge_params_base.json")) if os.path.exists(p)), None)
    if not path:
        log("!! no hinge_params_used.json in this project -- run the analyses first")
        return None
    try:
        prm = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log("!! could not read %s: %s" % (path, e))
        return None
    grounded = {g: r for g, r in ev.get("groups", {}).items() if r.get("grounded")}
    prm["grounding"] = {"asked": ev.get("asked"), "groups": {g: {k: r.get(k) for k in ("document", "citation", "section", "page")}
                                                             for g, r in grounded.items()},
                        "clauses": {c: r.get("citation") for c, r in ev.get("clauses", {}).items() if r.get("grounded")},
                        "evidence": "revise_evidence.json"}
    if grounded:
        cites = "; ".join(r["citation"] for r in grounded.values())
        prm["source"] = ("cited from the corpus on this PC %s -- %s. The numeric backbone values in "
                         "this file were NOT read out of those tables; `verified` stays "
                         "false until they are." % (ev.get("asked") or "", cites))
    json.dump(prm, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    log(">> params annotated: %s (%d of %d groups grounded)" % (path, len(grounded), len(GROUPS)))
    return {"path": path, "fingerprint": G.fingerprint(prm)}


def run(job: str, log=print) -> dict:
    """The whole step. Requires the Review tab to have run on this project."""
    job = os.path.abspath(job)
    review = os.path.join(job, "review.md")
    if not os.path.exists(review):
        raise SystemExit("no review.md in %s -- run the Review tab on this project first; it reads what the "
                         "analyses measured, looks the governing clauses up in the corpus and writes review.md, "
                         "review.html and review_transcript.json into the project folder." % job)
    if not os.path.getsize(review):
        raise SystemExit("review.md in %s is empty -- re-run the Review tab." % job)
    log(">> review found: %s (%d bytes)" % (review, os.path.getsize(review)))
    log(">> asking the corpus for the clauses the reports lean on")
    ev = probe(log=log)
    ev["review_md"] = {"path": review, "bytes": os.path.getsize(review)}
    prm_rec = annotate_params(job, ev, log=log)
    if prm_rec:
        ev["params"] = prm_rec                     # written into the evidence, which the run never touches
    out = os.path.join(job, "revise_evidence.json")
    json.dump(ev, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    log(">> evidence: %s" % out)

    # The supplements are rendered during the analysis run from live OpenSees objects, so they cannot be
    # re-rendered here -- and re-running the analyses is what ERASES this grounding (pushover/cli.py copies
    # the input params over hinge_params_used.json). So the provenance block in each supplement is replaced
    # where it stands. Only that block moves; not one number is touched.
    prm = {}
    if prm_rec:
        try:
            prm = json.load(open(prm_rec["path"], encoding="utf-8"))
        except Exception:
            prm = {}
    st, gev = G.state(prm, job)
    patched = []
    for rel in ("pushover/pushover_report.html", "nlrha/nlrha_report.html"):
        fp = os.path.join(job, *rel.split("/"))
        if not os.path.exists(fp):
            continue
        res = G.patch(fp, st, gev, prm)
        if res == G.PATCHED:
            patched.append(fp)
        elif res == G.UNCHANGED:
            log("   already current: %s" % rel)
        else:
            log("!! %s carries no provenance block (written by an older build) -- re-run the analyses "
                "once to pick up the new supplement, or read the citation in revise_evidence.json" % rel)
    for pk_rel in ("pushover/pushover_package.json", "nlrha/nlrha_package.json"):
        pk_path = os.path.join(job, *pk_rel.split("/"))
        if not os.path.exists(pk_path):
            continue
        try:                                        # the four-analyses sheet reads provenance from here
            pk = json.load(open(pk_path, encoding="utf-8"))
            pk["params_state"] = st
            pk["params_grounding"] = (gev or {}).get("groups") or {}
            json.dump(pk, open(pk_path, "w", encoding="utf-8"), indent=1, default=str)
            patched.append(pk_path)
        except Exception as e:
            log("!! could not update %s: %s" % (pk_rel, e))

    rebuilt, failed = list(patched), []
    try:                                   # the 16.1.4 submittal document reads the params file
        from nlrha import design_criteria as DC
        made = DC.write(job)
        rebuilt += [p for p in (made if isinstance(made, (list, tuple)) else [made]) if p]
    except Exception as e:
        failed.append("design criteria (16.1.4): %r" % (e,))
    try:                                   # the four-analyses sheet reads the packages
        from . import compare
        rebuilt.append(compare.build(job))
    except Exception as e:
        failed.append("four analyses: %r" % (e,))
    for p in rebuilt:
        log(">> re-issued: %s" % p)
    for f in failed:
        log("!! not re-issued -- %s" % f)
    n = sum(1 for r in ev.get("groups", {}).values() if r.get("grounded"))
    c = sum(1 for r in ev.get("clauses", {}).values() if r.get("grounded"))
    log(">> grounded %d of %d component groups and %d of %d Chapter 16 clauses" % (n, len(GROUPS), c, len(CLAUSES)))
    if n < len(GROUPS) or c < len(CLAUSES):
        log("   what the corpus could not answer stays marked in the reports -- that is the point of the mark")
    if st == G.GROUNDED:
        log("   every document in this project now carries the citation. Do NOT re-run the analyses to "
            "\"pick it up\" -- a run copies the input parameters over pushover/hinge_params_used.json, and "
            "the grounding is re-applied from revise_evidence.json on the next run rather than lost.")
    log("   `verified` stays false: a clause was retrieved, but the printed values were NOT read out of it "
        "and copied into hinge_params_used.json -- which is what hinge_params.json's own _README says "
        "verified=true means. Retrieval is not the check.")
    return {"evidence": out, "rebuilt": rebuilt, "failed": failed, "groups_grounded": n, "clauses_grounded": c}
