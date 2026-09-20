"""rag.py -- the standards search the Review step grounds its clauses with (stdlib only).

Same wire contract as HR Steel's search tool: POST RAG_API_URL {"query", "collection", "top_k", "clause",
"chapter"} -> {"results": [{"text", "source", "section", "title", "page", "score", "authoritative"}], "note"}.
In the hub RAG_API_URL is the Query file manager's rag_server.py, started by the hub for the run
({server.steltic_grokbot}/query); standalone use points it at any server that answers that shape.
Empty RAG_API_URL -> search() says so and the review goes on from the model's own knowledge, flagged.
"""
from __future__ import annotations
import json, os, time, urllib.error, urllib.request

# what the model may name in `document`; the collection is the QFM stem HR Steel's tool uses
DOCUMENTS = {
    "ASCE7": ("engineering_standards_ASCE7", "ASCE 7-22 Minimum Design Loads (Ch. 11-12, 16 nonlinear response history)"),
    "ASCE41": ("engineering_standards_ASCE_41_23", "ASCE 41-23 Seismic Evaluation and Retrofit (Ch. 7 NSP, performance levels)"),
    "A342": ("engineering_standards_AISC_342_22", "AISC 342-22 Seismic Evaluation and Retrofit of Existing Steel Buildings (component models, Ch. C)"),
    "A341": ("engineering_standards_AISC_341_22", "AISC 341-22 Seismic Provisions (SMF/SCBF/EBF, SCWB, panel zones)"),
    "A360": ("engineering_standards_AISC_360_22", "AISC 360-22 Specification (member strength, App. 1 inelastic analysis)"),
    "A358": ("engineering_standards_AISC_358_22", "AISC 358-22 Prequalified Connections (RBS, WUF-W ...)"),
}


def url() -> str:
    return (os.environ.get("RAG_API_URL") or "").strip()


def configured() -> bool:
    return bool(url())


def search(query: str, document: str = "ASCE7", top_k: int = 5, clause: str = "", chapter: str = "", timeout: float = 60.0) -> dict:
    """One search. -> {"ok", "results": [...], "note", "collection", "ms"}; never raises."""
    t0 = time.time()
    doc = (document or "ASCE7").strip().upper().replace("-", "").replace("_", "")
    coll = None
    for key, (c, _d) in DOCUMENTS.items():
        if doc == key or doc == c.upper().replace("_", ""):
            coll = c
    if coll is None:
        if document and document.lower().startswith("engineering_standards"):
            coll = document
        else:
            return {"ok": False, "results": [], "collection": document,
                    "note": "unknown document %r -- use one of %s" % (document, ", ".join(DOCUMENTS)), "ms": 0}
    if not url():
        return {"ok": False, "results": [], "collection": coll, "ms": 0,
                "note": "no standards server (RAG_API_URL is empty): cite from memory and mark the clause UNVERIFIED"}
    payload = {"query": query, "collection": coll, "top_k": max(1, min(int(top_k or 5), 8))}
    if clause:
        payload["clause"] = clause
    if chapter:
        payload["chapter"] = chapter
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    tok = os.environ.get("RAG_API_TOKEN")
    if tok:
        hdrs["Authorization"] = "Bearer " + tok
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url(), data=body, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if not isinstance(data, dict):
                data = {"results": data}
            res = []
            for h in (data.get("results") or [])[:payload["top_k"]]:
                if not isinstance(h, dict):
                    continue
                res.append({"text": str(h.get("text") or h.get("snippet") or "")[:2500], "source": str(h.get("source") or h.get("doc") or coll),
                            "section": str(h.get("section") or h.get("section_id") or ""), "title": str(h.get("title") or ""),
                            "page": h.get("page") or h.get("printed_label") or h.get("pdf_page"), "score": h.get("score"),
                            "authoritative": bool(h.get("authoritative"))})
            return {"ok": True, "results": res, "collection": coll, "note": data.get("note") or "", "matched": data.get("matched"),
                    "ms": int((time.time() - t0) * 1000)}
        except Exception as e:                                   # noqa: BLE001
            last = e
    return {"ok": False, "results": [], "collection": coll, "ms": int((time.time() - t0) * 1000),
            "note": "standards server unreachable (%s): cite from memory and mark the clause UNVERIFIED" % last}


def render(result: dict) -> str:
    """The tool result as the model reads it."""
    if not result.get("results"):
        return "NO PASSAGES. " + (result.get("note") or "")
    out = []
    for i, h in enumerate(result["results"], 1):
        head = " ".join(x for x in (h.get("source"), h.get("section"), ("p. %s" % h["page"]) if h.get("page") else "") if x)
        out.append("[%d] %s%s\n%s" % (i, head, (" -- " + h["title"]) if h.get("title") else "", h["text"]))
    if result.get("note"):
        out.append("note: " + result["note"])
    return "\n\n".join(out)
