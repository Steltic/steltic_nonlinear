"""rag.py -- the standards search the Review step grounds its clauses with (stdlib only).

Same wire contract as HR Steel's search tool: POST RAG_API_URL {"query", "collection", "top_k", "clause",
"chapter"} -> {"results": [{"text", "source", "section", "title", "page", "score", "authoritative"}], "note"}.
In the hub RAG_API_URL is the Query file manager's rag_server.py, started by the hub for the run
({server.steltic_grokbot}/query); standalone use points it at any server that answers that shape.
Empty RAG_API_URL -> search() says so and the review goes on from the model's own knowledge, flagged.

The search carries the same retrieval skill HR Steel's tool has, so the model is never left to
guess what a zero-hit answer means (2026-09-20: a review spent seven of its eight searches on a
document that had never been converted on that PC, was told "not in the corpus" seven times, and
cited it from memory):

  * before the first search the server's /healthz says which documents the corpus actually holds;
    a document that is absent is reported as a CORPUS GAP at once, the call is not counted against
    the budget, and one wide search across the documents that ARE present is made instead;
  * a miss climbs a ladder before it is allowed to be a miss -- as asked; without the clause /
    chapter filter; the id as an exact lookup; any document -- and the answer says which rung, and
    which document, supplied the passages.
"""
from __future__ import annotations
import json, os, re, time, urllib.error, urllib.request

# what the model may name in `document`; the collection is the QFM tag HR Steel's tool uses
DOCUMENTS = {
    "ASCE7": ("engineering_standards_ASCE7", "ASCE 7-22 Minimum Design Loads (Ch. 11-12, 16 nonlinear response history)"),
    "ASCE41": ("engineering_standards_ASCE_41_23", "ASCE 41-23 Seismic Evaluation and Retrofit (Ch. 7 NSP, performance levels)"),
    "A342": ("engineering_standards_AISC_342_22", "AISC 342-22 Seismic Evaluation and Retrofit of Existing Steel Buildings (component models, Ch. C)"),
    "A341": ("engineering_standards_AISC_341_22", "AISC 341-22 Seismic Provisions (SMF/SCBF/EBF, SCWB, panel zones)"),
    "A360": ("engineering_standards_AISC_360_22", "AISC 360-22 Specification (member strength, App. 1 inelastic analysis)"),
    "A358": ("engineering_standards_AISC_358_22", "AISC 358-22 Prequalified Connections (RBS, WUF-W ...)"),
}
# the canonical stem /healthz lists a converted document under, and what else it may have been converted as
STEMS = {"ASCE7": "ASCE7", "ASCE41": "ASCE_41_23", "A342": "AISC_342_22", "A341": "AISC_341_22", "A360": "AISC_360_22", "A358": "AISC_358_22"}
_STEM_RE = {"ASCE7": r"^ASCE[_\-]?7(?!\d)", "ASCE41": r"^ASCE[_\-]?41", "A342": r"^AISC[_\-]?342", "A341": r"^AISC[_\-]?341",
            "A360": r"^AISC[_\-]?360", "A358": r"^AISC[_\-]?358"}
TITLES = {"ASCE7": "ASCE 7-22", "ASCE41": "ASCE 41-23", "A342": "AISC 342-22", "A341": "AISC 341-22", "A360": "AISC 360-22", "A358": "AISC 358-22"}
STATUS_TTL = 60.0
_status_cache: tuple | None = None


def url() -> str:
    return (os.environ.get("RAG_API_URL") or "").strip()


def configured() -> bool:
    return bool(url())


def _headers() -> dict:
    hdrs = {"Content-Type": "application/json"}
    tok = os.environ.get("RAG_API_TOKEN")
    if tok:
        hdrs["Authorization"] = "Bearer " + tok
    return hdrs


def resolve(document: str) -> str | None:
    """The model's `document` -> the short id in DOCUMENTS (ASCE7, A342 ...), or None."""
    doc = (document or "").strip().upper().replace("-", "").replace("_", "").replace(" ", "")
    if doc.startswith("ENGINEERINGSTANDARDS"):
        doc = doc[len("ENGINEERINGSTANDARDS"):]
    for key, (c, _d) in DOCUMENTS.items():
        if doc == key or doc == c.upper().replace("_", "") or doc == STEMS[key].replace("_", ""):
            return key
    return None


# ---------------------------------------------------------------- what the corpus holds
def status(timeout: float = 15.0, force: bool = False) -> dict:
    """GET /healthz of the standards server -> {"ok", "known", "indexed_docs", "spec_index", "note"}; cached a minute.
    `known` is False when the server did not answer or does not list its documents (an older backend)."""
    global _status_cache
    if not force and _status_cache and time.time() - _status_cache[0] < STATUS_TTL and _status_cache[2] == url():
        return _status_cache[1]
    out = {"ok": False, "known": False, "indexed_docs": [], "spec_index": None, "note": ""}
    u = url()
    if not u:
        out["note"] = "no standards server (RAG_API_URL is empty)"
        return out
    base = u
    for tail in ("/api/query", "/query"):
        if base.endswith(tail):
            base = base[: -len(tail)]
            break
    try:
        req = urllib.request.Request(base.rstrip("/") + "/healthz", headers=_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        if isinstance(d, dict):
            out["ok"] = bool(d.get("ok", True))
            if isinstance(d.get("indexed_docs"), list):
                out["known"] = True
                out["indexed_docs"] = [str(x) for x in d["indexed_docs"]]
            if "spec_index" in d:
                out["spec_index"] = bool(d["spec_index"])
    except Exception as e:                                       # noqa: BLE001
        out["note"] = "the standards server did not answer /healthz (%s)" % e
    _status_cache = (time.time(), out, u)
    return out


def corpus_map(st: dict | None = None) -> dict:
    """Which of DOCUMENTS the corpus holds, by /healthz -> {"known", "present": {id: stem}, "absent": [id], "other": [stem]}."""
    st = st or status()
    if not st.get("known"):
        return {"known": False, "present": {}, "absent": [], "other": []}
    docs = list(st.get("indexed_docs") or [])
    present, used = {}, set()
    for key, stem in STEMS.items():
        if stem in docs:
            present[key] = stem; used.add(stem)
            continue
        m = next((d for d in docs if re.match(_STEM_RE[key], d, re.I)), None)
        if m:
            present[key] = m; used.add(m)
    return {"known": True, "present": present, "absent": [k for k in DOCUMENTS if k not in present],
            "other": [d for d in docs if d not in used]}


def describe_corpus(cm: dict) -> str:
    """One line for the log and the model: what is here and what is not."""
    if not cm.get("known"):
        return "the standards server did not say which documents it holds"
    pres = ", ".join("%s (%s)" % (TITLES[k], cm["present"][k]) for k in DOCUMENTS if k in cm["present"]) or "none of the six"
    absn = ", ".join("%s (stem %s)" % (TITLES[k], STEMS[k]) for k in cm["absent"])
    other = ", ".join(cm["other"][:12]) + (" ..." if len(cm["other"]) > 12 else "")
    return ("present: " + pres + ("; ABSENT: " + absn + " -- convert on the Query file manager's Convert tab, then Rebuild index" if absn else "")
            + ("; also indexed: " + other if other else ""))


# ---------------------------------------------------------------- one call on the wire
def _post(payload: dict, timeout: float) -> tuple[dict | None, Exception | None]:
    body = json.dumps(payload).encode("utf-8")
    last = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url(), data=body, headers=_headers(), method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if not isinstance(data, dict):
                data = {"results": data}
            return data, None
        except Exception as e:                                   # noqa: BLE001
            last = e
    return None, last


# How much of one hit's text is passed on. An exact lookup returns ONE provision whole -- a table is
# its every row plus the footnote equations the server appends after them, and in this corpus that runs
# 5,000-27,000 characters (Table C5.5 puts the RBS row 8,450 characters in, Table A3.2 the A992 row
# 6,800 in, Table C3.6 the highly-ductile row 7,100 in). A cap of 2,500 here silently cut every one of
# those rows off and the collector, reading only what it was handed, rightly reported them absent. Full
# text navigation returns snippets, which the server already caps at 4,000.
EXACT_MAX_CHARS = 60000
FTS_MAX_CHARS = 4000


def _shape(data: dict, top_k: int, coll: str, max_chars: int = EXACT_MAX_CHARS) -> list:
    res = []
    for h in (data.get("results") or [])[:top_k]:
        if not isinstance(h, dict):
            continue
        text = str(h.get("text") or h.get("snippet") or "")
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "\n[... %d more characters not shown]" % (len(text) - max_chars)
        res.append({"text": text, "source": str(h.get("source") or h.get("doc") or coll),
                    "section": str(h.get("section") or h.get("section_id") or ""), "title": str(h.get("title") or ""),
                    "page": h.get("page") or h.get("printed_label") or h.get("pdf_page"), "score": h.get("score"),
                    "authoritative": bool(h.get("authoritative"))})
    return res


# ---------------------------------------------------------------- the search, with its ladder
# ---------------------------------------------------------------- the retrieval policy
# steltic_grokbot skills/Skill_querying_PACKAGED.md, the same policy HR Steel and CFS follow: ONE
# document, an EXACT id when the provision is known, full text only to navigate to an id. The model is
# told to write calls that way; because it will still type a sentence some of the time, the tool puts
# every call into policy form itself and records the form it sent.
_DOCNAME_RE = re.compile(r"\b(?:AISC|AISI|ASCE(?:/SEI)?|ANSI)\s*/?\s*[A-Z]?\d+(?:[-\u2013]\d+)?\b", re.I)
_ID_RE = re.compile(r"\b(?:[A-Za-z]{1,2}\d+(?:\.\d+)*[a-z]?(?:-\d+[a-z]?)?"    # C3.6  C5.4a  E3.4a  F2-1
                    r"|\d+(?:\.\d+)+(?:-\d+[a-z]?)?"                          # 16.4.1.2  12.12-1
                    r"|\d+-\d+[a-z]?)\b")                                      # 16.4-1
_EXACT_TYPES = ("exact_section", "exact_equation", "exact_table", "id")


def policy_plan(query: str, clause: str = "", chapter: str = "", qtype: str = "") -> dict:
    """What the policy makes of one call: exact lookups first, then at most one navigation query.

    -> {"exact": [(kind, id), ...], "nav": text | "", "chapter": letter/number, "label": "..."}"""
    q = (query or "").strip()
    qtype = (qtype or "").strip().lower()
    ch = (chapter or "").strip().upper()
    exact: list = []
    nav = ""
    if qtype in _EXACT_TYPES:
        exact = [(qtype, (q or clause).strip())]          # the model named the kind: no guessing
    elif qtype in ("fts", "keyword"):
        nav = q                                            # the model chose navigation: send its words
        if clause:
            exact = [("id", clause.strip())]               # ...but an id it also gave is still asked for first
    else:
        stripped = _DOCNAME_RE.sub(" ", q)
        bare = stripped.strip()
        if bare and _ID_RE.fullmatch(bare):
            exact = [("id", bare)]                         # a bare id typed into `query`
        else:
            ids = [m.group(0) for m in _ID_RE.finditer(stripped)][:2]
            exact = [("id", i) for i in ids]
            if clause and clause.strip().upper() not in [i.upper() for _k, i in exact]:
                exact.insert(0, ("id", clause.strip()))
            nav = re.sub(r"\s+", " ", _ID_RE.sub(" ", stripped)).strip(" ,;:-")
            if len(nav.split()) < 2:
                nav = ""                                   # nothing left to navigate with
    seen, uniq = set(), []
    for k, i in exact:
        if i and i.upper() not in seen:
            seen.add(i.upper()); uniq.append((k, i))
    steps = ["exact-id %s" % i if k == "id" else "%s %s" % (k, i) for k, i in uniq]
    if nav:
        steps.append("fts \u00ab%s\u00bb" % nav[:60] + (" chapter %s" % ch if ch else ""))
    return {"exact": uniq, "nav": nav, "chapter": ch, "label": " \u00b7 ".join(steps) or "as-asked"}


def search(query: str, document: str = "ASCE7", top_k: int = 5, clause: str = "", chapter: str = "",
           timeout: float = 60.0, qtype: str = "", want_commentary: bool = False, neighbors=None) -> dict:
    """One search as the model asked it, escalated before it may report nothing.

    -> {"ok", "results", "collection", "note", "matched", "ms", "via", "counted", "missing_document", "attempts"}
    `counted` is False when the call cost the corpus nothing to answer -- the document is not on this
    PC, the server is unreachable -- so the review's budget is not spent on it. Never raises."""
    t0 = time.time()
    top_k = max(1, min(int(top_k or 5), 20))   # the server's own cap
    key = resolve(document)
    if key is None:
        if document and document.lower().startswith("engineering_standards"):
            coll, key = document, None
        else:
            return {"ok": False, "results": [], "collection": document, "via": "", "counted": False, "attempts": 0, "ms": 0,
                    "note": "unknown document %r -- use one of %s" % (document, ", ".join(DOCUMENTS))}
    else:
        coll = DOCUMENTS[key][0]
    if not url():
        return {"ok": False, "results": [], "collection": coll, "ms": 0, "via": "", "counted": False, "attempts": 0,
                "note": "no standards server (RAG_API_URL is empty): cite from memory and mark the clause UNVERIFIED"}
    query = (query or "").strip()
    clause = (clause or "").strip()
    chapter = (chapter or "").strip()
    cm = corpus_map(status())
    attempts: list = []

    sent: set = set()

    def rung(how: str, q: str, collection: str, cl: str = "", ch: str = "", kind: str = ""):
        """-> (results | None on transport failure, note, matched), or ([], "", "") if already sent."""
        key = (collection, (q or "").strip().lower(), (cl or "").upper(), (ch or "").upper(), kind)
        if key in sent:
            return [], "", ""                       # an identical rung: nothing new to learn, no round trip
        sent.add(key)
        payload = {"query": q, "collection": collection, "top_k": top_k}
        if cl:
            payload["clause"] = cl
        if ch:
            payload["chapter"] = ch
        if kind:
            payload["type"] = kind
        if want_commentary:
            payload["want_commentary"] = True
        if neighbors is not None:
            payload["context_neighbors"] = max(0, min(int(neighbors), 2))
        data, err = _post(payload, timeout)
        if data is None:
            attempts.append({"how": how, "hits": None, "note": "unreachable: %s" % err})
            return None, str(err), ""
        res = _shape(data, top_k, collection, EXACT_MAX_CHARS if kind in _EXACT_TYPES else FTS_MAX_CHARS)
        attempts.append({"how": how, "hits": len(res), "note": data.get("note") or ""})
        return res, str(data.get("note") or ""), data.get("matched") or ""

    def finish(res, via, note, matched="", counted=True, missing=None):
        return {"ok": True, "results": res, "collection": coll, "note": note, "matched": matched, "via": via, "counted": counted,
                "missing_document": missing, "attempts": attempts, "ms": int((time.time() - t0) * 1000)}

    def unreachable(note):
        return {"ok": False, "results": [], "collection": coll, "via": "", "counted": False, "attempts": attempts, "ms": int((time.time() - t0) * 1000),
                "note": "standards server unreachable (%s): cite from memory and mark the clause UNVERIFIED" % note}

    def gap(key, extra_hits=None, hits_note=""):
        """The document is not on this PC: say so, name what is, and hand over whatever the other documents said."""
        pres = ", ".join("%s (%s)" % (k, cm["present"][k]) for k in DOCUMENTS if k in cm["present"]) if cm["known"] else "(the server did not say)"
        note = ("CORPUS GAP -- %s (%s) is not in the corpus on this PC: it was never converted (stem %s). Nothing in it can be "
                "confirmed or denied here, and this is NOT evidence that the clause is absent from the standard. Present: %s. "
                "Do not search %s again (this call was not counted). Cite %s from memory, marked (UNVERIFIED)"
                % (key, TITLES[key], STEMS[key], pres or "none", key, TITLES[key]))
        if extra_hits:
            docs = sorted({h["source"] for h in extra_hits if h.get("source")})
            note += " -- unless one of the passages below, from %s, governs this check; cite THAT document if so." % (", ".join(docs) or "another document")
        else:
            note += "."
        return finish(extra_hits or [], "any-document" if extra_hits else "corpus-gap", note, counted=False, missing=key)

    # ---- the document is not here: one wide search across what is, then the gap note (not counted)
    if key and cm["known"] and key not in cm["present"]:
        wide = query or clause
        res, note, _m = rung("any-document (%s absent)" % key, wide, "", "", "") if wide else ([], "", "")
        if res is None:
            return unreachable(note)
        return gap(key, res)
    collection = coll
    if key and cm["known"] and cm["present"].get(key) not in (None, STEMS[key]):
        collection = cm["present"][key]                       # converted under another stem: ask for it by that name
    plan = policy_plan(query, clause, chapter, qtype)
    exact = list(plan["exact"])
    nav = plan["nav"]
    tried_ids = {i.upper() for _k, i in exact}

    def corpus_note(note, how):
        """The two answers that are about the corpus, not about this query. -> a result, or None."""
        if "is not in the corpus" in note:          # an older /healthz did not list documents; the server says so now
            wide = query or clause
            res2, _n2, _m = rung("any-document (%s absent)" % key, wide, "", "", "") if (wide and key) else ([], "", "")
            return gap(key, res2 or []) if key else finish([], how, note, counted=False)
        if "no specification index" in note:
            return finish([], how, "CORPUS GAP -- there is no specification index on this PC at all: nothing was converted. "
                          "Stop searching; cite every clause from memory, marked (UNVERIFIED), and say so in section 8.", counted=False)
        return None

    # ---- rung 1: the policy's exact lookups, BEFORE any full text. An id the model knows is asked
    # for as an id -- a sentence sent at a corpus that indexes provisions by id is how a lookup that
    # was always going to work gets reported as "not found".
    for n, (kind, ident) in enumerate(exact):
        how = ("exact-id %s" % ident) if kind == "id" else ("%s %s" % (kind, ident))
        res, note, matched = rung(how, ident, collection, "", "", kind)
        if res is None:
            return unreachable(note)
        if n == 0:
            hit = corpus_note(note, how)
            if hit is not None:
                return hit
        if res:
            return finish(res, how, note, matched)
    # ---- rung 2: navigate with full text, in the standard's own words
    q_nav = nav or query
    if q_nav or clause:
        how = "as-asked" if not exact else "fts-navigate"
        # the clause filter is only worth sending when the policy did not already ask for it as an id
        cl = "" if exact else clause
        res, note, matched = rung(how, q_nav or clause, collection, cl, chapter)
        if res is None:
            return unreachable(note)
        if not exact:
            hit = corpus_note(note, how)
            if hit is not None:
                return hit
        if res:
            return finish(res, how, note, matched)
    # ---- rung 3: without the clause / chapter filter -- only when rung 2 carried one. With an exact
    # id in hand rung 2 already went out unfiltered, and re-sending it learns nothing.
    if (clause and not exact) or chapter:
        res, note, matched = rung("no-filter", q_nav or query or clause, collection)
        if res is None:
            return unreachable(note)
        if res:
            return finish(res, "no-filter (the clause/chapter filter dropped)", note, matched)
    # ---- rung 4: an id the policy did not spot, as an exact lookup
    cid = clause or (query if re.fullmatch(r"(?:Table |Eq\.? ?|Fig\.? ?)?[A-Z]?\d+(?:\.\d+)*(?:-\d+[a-z]?)?", query or "", re.I) else "")
    if cid and cid.upper() not in tried_ids:
        res, note, matched = rung("exact-id %s" % cid, cid, collection, "", "", "id")
        if res is None:
            return unreachable(note)
        if res:
            return finish(res, "exact-id %s" % cid, note, matched)
    # ---- rung 5: any document
    wide = query or clause
    if wide:
        res, note, matched = rung("any-document", wide, "", "", "")
        if res is None:
            return unreachable(note)
        if res:
            docs = sorted({h["source"] for h in res if h.get("source")})
            return finish(res, "any-document", "answered by %s, NOT by %s (%s), which holds nothing for this wording -- cite the document the passage is from"
                          % (", ".join(docs) or "another document", key or coll, TITLES.get(key, "")), matched)
    tried = "; ".join("%s -> %s" % (a["how"], "unreachable" if a["hits"] is None else "%d hits" % a["hits"]) for a in attempts)
    return finish([], "exhausted", "NOT FOUND after %d attempts against a corpus that holds %s (%s): the wording is not in the indexed text. "
                  "Re-word ONCE in the standard's own terms or ask for the parent section; if that misses too, cite from memory, marked (UNVERIFIED), "
                  "and do not invent a clause number or page." % (len(attempts), TITLES.get(key, coll), tried))


def render(result: dict) -> str:
    """The tool result as the model reads it."""
    out = []
    via = result.get("via") or ""
    if not result.get("results"):
        head = "NO PASSAGES."
        if via and via not in ("", "as-asked", "corpus-gap"):
            head += " (tried: %s)" % via
        return head + " " + (result.get("note") or "")
    if via and via != "as-asked":
        out.append("(answered by: %s)" % via)
    for i, h in enumerate(result["results"], 1):
        head = " ".join(x for x in (h.get("source"), h.get("section"), ("p. %s" % h["page"]) if h.get("page") else "") if x)
        out.append("[%d] %s%s\n%s" % (i, head, (" -- " + h["title"]) if h.get("title") else "", h["text"]))
    if result.get("note"):
        out.append("note: " + result["note"])
    return "\n\n".join(out)
