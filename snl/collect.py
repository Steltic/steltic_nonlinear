"""collect.py -- read the component modelling parameters out of the standards, BEFORE the analyses run.

    python -m snl collect <job folder> [--out hinge_params_collected.json]

The analyses need a component parameter file (pushover/hinge_params.json's shape): the backbone a, b, c
and the IO / LS / CP acceptance criteria for every hinge kind the building has, and the expected
material. The repository copy is a PLACEHOLDER written from memory, and its own _README says what must
happen instead: retrieve the tables, copy the printed values into the file, set verified=true, cite.

That is this step, and the division of labour is the whole point:
  * RETRIEVAL IS DETERMINISTIC. Collect knows which table each group is read from (PLAN) and fetches
    it from the Query file manager itself, as exact lookups. No model chooses what to search.
  * THE ROW IS DECIDED FROM THE BUILDING. RBS connections -> the RBS row of Table C5.5; an SMF -> the
    highly ductile row of Table C3.6 (row_for). No model chooses the row.
  * THE MODEL ONLY TRANSCRIBES. Per group, one short call with the passages and the named fields; it
    answers {value, quote} per field, reasoning effort LOW. Every quote must occur verbatim in the
    passages and every number in a value must be among the digits of its quote (check_field) -- so a
    number the model remembers, with no cell to quote, is rejected. Up to two retries naming exactly
    what was rejected; then the group is missing. No tools, no searching, no loop.
  * THE SOURCE IS BUILT BY THE PROGRAM from the passages' own document / section / page.
The first two live runs are why: given the passages and a free hand, a reasoning model spent its time
weighing the converted text against its memory (68 identical searches; then pages of deliberation).

Every group is then validated (shape, ranges, ordering, expressions that evaluate, a source naming a
table and a page) and `hinge_params_collected.json` is written only when every needed group passed;
otherwise `hinge_params_collected.partial.json`, the missing fields named, and the hub's Run analyses
stays closed. collect_transcript.jsonl holds every prompt, answer and reasoning as it streams, so a
cancelled run still shows what the model did. Model MOCK (tests) takes the values from the Ex22
corpus-read example instead of a provider.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import sys
import time

from . import llm, rag
from .review import Emitter, _tool_title

OUT_NAME = "hinge_params_collected.json"
PARTIAL_NAME = "hinge_params_collected.partial.json"
EVIDENCE_NAME = "collect_evidence.json"
TRANSCRIPT_NAME = "collect_transcript.jsonl"    # every prompt, answer and reasoning, as it streams
MAX_TURNS = 80                     # a safety ceiling on the agent loop, not a search budget
MAX_SEARCHES = 60                  # a backstop against a runaway: the guard below is what actually stops one
REPEAT_LIMIT = 3                   # the same search this many times without moving on = the corpus does not hold it

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(os.path.dirname(_HERE), "pushover", "hinge_params.json")
_EX22 = os.path.join(os.path.dirname(_HERE), "examples", "Ex22_SMF", "pushover", "hinge_params_used.json")

# The groups a file can carry, and when a building needs each. `material` is always needed; the
# others follow what the package actually contains.
GROUP_ORDER = ("material", "beam_flexure", "column_flexure", "brace_axial")


# ---------------------------------------------------------------- the building
def gather(job: str) -> dict:
    """What the model is told about the building, read from the design package -- nothing invented."""
    from pushover import package_reader as PR
    pkg = PR.load(job)
    basis = {k: v for k, v in vars(pkg.basis).items() if k != "sources"} if pkg.basis else {}
    beams, cols, braces = {}, {}, {}
    for m in (pkg.calc or {}).get("members") or []:
        i = m.get("inputs") or {}
        kind, sec, role = (i.get("kind") or "").lower(), i.get("section") or "", i.get("role") or ""
        if not sec:
            continue
        if kind == "beam":
            b = beams.setdefault(sec, {"roles": set(), "Lb_over_ry": None, "d": None})
            b["roles"].add(role)
            if i.get("Lb_in") and i.get("ry"):
                b["Lb_over_ry"] = round(float(i["Lb_in"]) / float(i["ry"]), 1)
        elif kind in ("col", "column"):
            cols.setdefault(sec, {"roles": set()})["roles"].add(role)
        elif kind == "brace":
            braces.setdefault(sec, {"roles": set()})["roles"].add(role)
    conns = [c.get("type") or "" for c in (pkg.calc or {}).get("connections") or []]
    # the RBS flange cut is a design fact from HR Steel's connection design ("RBS a=9.9 b=29.8 c=4.0 in")
    rbs_c_in, rbs_detail = None, ""
    for c in (pkg.calc or {}).get("connections") or []:
        comp = str(c.get("components") or "")
        m = re.search(r"\bc\s*=\s*([0-9.]+)\s*in", comp) if "rbs" in (c.get("type") or "").lower() else None
        if m:
            rbs_c_in, rbs_detail = float(m.group(1)), comp[:120]
            break
    system = str(basis.get("system") or "").upper()
    moment = any("moment" in c.lower() for c in conns) or system in ("SMF", "IMF", "OMF")
    rbs = any("rbs" in c.lower() for c in conns)
    braced = bool(braces) or system in ("SCBF", "OCBF", "EBF", "BRBF")
    needed = ["material"]
    if moment or beams:
        needed.append("beam_flexure")
    needed.append("column_flexure")
    if braced:
        needed.append("brace_axial")
    return {"job": os.path.abspath(job), "name": pkg.name, "basis": basis, "system": system,
            "connections": conns, "rbs": rbs, "rbs_c_in": rbs_c_in, "rbs_detail": rbs_detail,
            "moment_frame": moment, "braced": braced,
            "beams": {k: {"roles": sorted(v["roles"]), "Lb_over_ry": v["Lb_over_ry"]} for k, v in beams.items()},
            "columns": {k: {"roles": sorted(v["roles"])} for k, v in cols.items()},
            "braces": {k: {"roles": sorted(v["roles"])} for k, v in braces.items()},
            "needed": needed}


# ---------------------------------------------------------------- what is read from where
# The tables each group is read from. Collect fetches these itself, before any model is involved: the
# first live run spent 68 searches asking AISC 342 to confirm four numbers the model remembered, while
# the row it needed had come back on its second search. Retrieval is deterministic; the model only
# transcribes what came back.
PLAN = {
    "material":       [("A341", "exact_table", "A3.2", 1), ("A342", "exact_table", "A5.2", 1)],
    "beam_flexure":   [("A342", "exact_table", "C5.5", 2), ("A342", "exact_table", "C2.2", 1)],
    "column_flexure": [("A342", "exact_table", "C3.6", 2), ("A342", "exact_section", "C3.4a", 1)],
    "brace_axial":    [("A342", "exact_table", "C3.6", 2),
                       ("ASCE41", "fts", "steel braces in compression modeling parameters acceptance criteria", 1)],
}


def prefetch(facts: dict, search) -> dict:
    """The passages for every needed group, up front. -> {group: [{"source","section","page","text"}, ...]}"""
    out: dict = {g: [] for g in facts["needed"]}
    for gid in facts["needed"]:
        for doc, how, q, nb in PLAN.get(gid, []):
            if gid == "beam_flexure" and q == "C5.5" and not facts.get("moment_frame"):
                continue                                   # member hinges: Table C2.2 alone
            if gid == "beam_flexure" and q == "C2.2" and facts.get("moment_frame"):
                continue                                   # FR connections: Table C5.5 alone
            res = search({"document": doc, "type": how, "query": q, "context_neighbors": nb, "top_k": 8,
                          "purpose": "table for %s" % gid}, raw=True)
            for h in (res.get("results") or [])[:6]:
                out[gid].append({"document": doc, "how": "%s %s" % (how, q), "source": h.get("source") or doc,
                                 "section": h.get("section") or "", "page": h.get("page") or "",
                                 "text": (h.get("text") or "")[:rag.EXACT_MAX_CHARS]})
    return out


# The fields each group is transcribed into: (field, what to read, kind). kind: number | expr | fraction.
# `fraction` cells read "0.5 a" / "0.75 b" / "b"; `expr` cells are flattened digits or a LaTeX footnote.
FIELDS = {
    "material": [
        ("Ry_expected", "Ry for ASTM A992 in Table A3.2 (the 'W-shapes' / 'A992' row, Ry column)", "number"),
    ],
    "beam_flexure:fr_connection": [
        ("a_expr", "the X expression the FOOTNOTE EQUATIONS block defines for THIS row's `a` cell (X1 for a row marked [d], X2 for a row marked [e]), as Python in h, tw, bf, tf, Lb, ry, L, d -- the pattern is C*(h/tw)**p1*(bf/(2*tf))**p2*(Lb/ry)**p3*(L/d)**p4 with C and the p's from the LaTeX", "expr"),
        ("a_max", "the cap in this row's `a` cell ('X 2 0 07 <= .' -> 0.07)", "number"),
        ("b_abs", "this row's `b` cell (plastic rotation, rad)", "number"),
        ("c_residual", "this row's residual strength ratio `c`", "number"),
        ("IO_frac_of_a", "this row's IO cell as a fraction of a ('0.5 a' -> 0.5)", "fraction"),
        ("LS_frac_of_b", "this row's LS cell as a fraction of b ('0.75 b' -> 0.75)", "fraction"),
        ("CP_frac_of_b", "this row's CP cell as a fraction of b ('b' -> 1.0)", "fraction"),
    ],
    "beam_flexure:member": [
        ("a_over_thetay", "this row's `a` as a multiple of theta_y ('9 thy' -> 9)", "number"),
        ("b_over_thetay", "this row's `b` as a multiple of theta_y", "number"),
        ("c_residual", "this row's residual strength ratio `c`", "number"),
        ("IO_over_thetay", "IO as a multiple of theta_y", "number"),
        ("LS_over_thetay", "LS as a multiple of theta_y", "number"),
        ("CP_over_thetay", "CP as a multiple of theta_y", "number"),
    ],
    "column_flexure": [
        ("a_expr", "this row's `a` cell, as Python in h, tw, L, ry, PG, Pye -- the pattern is C*(h/tw)**p1*(L/ry)**p2*(1-PG/Pye)**p3 with C and the p's read from the flattened digits in print order", "expr"),
        ("a_max", "the cap on `a` in the same cell (the '<= 0.07')", "number"),
        ("b_expr", "this row's `b` cell, same symbols and pattern", "expr"),
        ("b_max", "the cap on `b` in the same cell", "number"),
        ("c_expr", "this row's `c` cell as Python in PG, Pye (the pattern is C1 - C2*PG/Pye)", "expr"),
        ("IO_frac_of_a", "IO as a fraction of a", "fraction"),
        ("LS_frac_of_b", "LS as a fraction of b", "fraction"),
        ("CP_frac_of_b", "CP as a fraction of b", "fraction"),
    ],
    "brace_axial": [
        ("compression.stocky.a_over_dc", "braces in compression, stocky (KL/r at or below the lower limit): `a` as a multiple of delta_c", "number"),
        ("compression.stocky.b_over_dc", "stocky: `b` as a multiple of delta_c", "number"),
        ("compression.stocky.c", "stocky: residual strength ratio c", "number"),
        ("compression.stocky.IO_over_dc", "stocky: IO as a multiple of delta_c", "number"),
        ("compression.stocky.LS_over_dc", "stocky: LS as a multiple of delta_c", "number"),
        ("compression.stocky.CP_over_dc", "stocky: CP as a multiple of delta_c", "number"),
        ("compression.slender.a_over_dc", "braces in compression, slender (KL/r at or above the upper limit): `a` as a multiple of delta_c", "number"),
        ("compression.slender.b_over_dc", "slender: `b`", "number"),
        ("compression.slender.c", "slender: c", "number"),
        ("compression.slender.IO_over_dc", "slender: IO", "number"),
        ("compression.slender.LS_over_dc", "slender: LS", "number"),
        ("compression.slender.CP_over_dc", "slender: CP", "number"),
        ("tension.a_over_dT", "braces in tension: `a` as a multiple of delta_T", "number"),
        ("tension.b_over_dT", "tension: `b`", "number"),
        ("tension.c", "tension: c", "number"),
        ("tension.IO_over_dT", "tension: IO", "number"),
        ("tension.LS_over_dT", "tension: LS", "number"),
        ("tension.CP_over_dT", "tension: CP", "number"),
        ("Ry_expected", "Ry for the brace material (A500 Gr. C round/rectangular HSS) in AISC 341-22 Table A3.2", "number"),
    ],
}


def _ductility(facts: dict) -> str:
    sys_ = (facts.get("system") or "").upper()
    return "highly ductile" if sys_ in ("SMF", "SCBF", "EBF", "BRBF", "SPSW", "") else "moderately ductile"


def row_for(gid: str, facts: dict) -> tuple[str, str]:
    """(variant, the row the model transcribes), decided from the building -- not by the model."""
    if gid == "material":
        return "material", "AISC 341-22 Table A3.2, the ASTM A992 row (W-shapes): Ry"
    if gid == "beam_flexure":
        if facts.get("moment_frame"):
            row = ("'RBS moment connection in conformance with ANSI/AISC 358' [e]" if facts.get("rbs")
                   else "'All ANSI/AISC 358 conforming connections, with the exception of the RBS moment connection' [d]")
            return "beam_flexure:fr_connection", "AISC 342-22 Table C5.5 (continued), row %s" % row
        return "beam_flexure:member", "AISC 342-22 Table C2.2, 'Beams -- flexure', the %s row" % _ductility(facts)
    if gid == "column_flexure":
        return "column_flexure", "AISC 342-22 Table C3.6, 'Columns and Braces in Compression', the row '1. %s' (a, b, c columns)" % _ductility(facts).capitalize()
    if gid == "brace_axial":
        return "brace_axial", "AISC 342-22 Table C3.6, the braces-in-compression (stocky / slender) and braces-in-tension rows; ASCE 41-23 Chapter 9 where AISC 342 defers"
    return gid, gid


# ---------------------------------------------------------------- transcription (the only thing the model does)
TRANSCRIBE = """You are transcribing values from PASSAGES of a converted standard into named fields. This is copying, not engineering judgement, and nothing you remember about the standard counts.

For every field answer {"value": ..., "quote": "..."} where `quote` is the VERBATIM text from the passages the value was read from -- copy it exactly, converter artefacts included. The program that reads your answer rejects any value whose quote does not occur in the passages, and any number in a value that does not appear among the digits of its quote. A value you cannot quote: {"value": null, "quote": null, "why": "what is missing"}.

How the converted text reads:
- A table cell that held an expression is flattened to its digits in print order. "5 5 1 0 07 0 95 0 5 2 4" after the symbols "a h t L r P P w y G ye" is  a = 5.5 (h/tw)^-0.95 (L/ry)^-0.5 (1 - PG/Pye)^2.4 <= 0.07 : the coefficient, the 1 of (1 - PG/Pye), the cap, then the exponents in the order the symbols appear. Read the digits back in that order and no other; every number you write must be one of the digit groups in the quote.
- "X 2 0 07 <= ." is the printed "X2 <= 0.07": the cap is 0.07, and X2 is defined in the FOOTNOTE EQUATIONS block after the table, in LaTeX. Read the coefficient and exponents from that LaTeX line; quote the LaTeX line.
- In an IO / LS / CP column, "0.5 a" is the fraction 0.5 (of a) and a bare "b" is the fraction 1.0 (of b). Quote the cell as written.
- LaTeX: \\frac{h}{t_{w}} is h/tw, \\frac{b_{f}}{2t_{f}} is bf/(2*tf), ^{-0.5} is **-0.5. Write expressions as Python with exactly the symbol names the field asks for.

Reply with ONE JSON object -- field name -> {"value", "quote"} -- and nothing else."""


def _ask_message(gid: str, variant: str, row: str, fields: list, passages: list, retry_note: str = "") -> str:
    lines = ["GROUP: %s" % gid, "TABLE AND ROW: %s" % row, "", "FIELDS (name: what to read):"]
    for f, what, kind in fields:
        lines.append("  %s: %s" % (f, what))
    lines += ["", "PASSAGES:"]
    for p in passages:
        lines.append("[%s %s p. %s]" % (p["source"], p["section"], p["page"]))
        lines.append(p["text"])
        lines.append("")
    if retry_note:
        lines += ["YOUR LAST ANSWER WAS NOT ACCEPTED -- these fields again:", retry_note, ""]
    lines.append("Transcribe the fields now. JSON only.")
    return "\n".join(lines)


_NUM = re.compile(r"\d+(?:\.\d+)?")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def _digit_groups(s: str) -> set:
    """Every number-like token in a quote, as bare digits: '0 . 5 5' -> '055', '0 07' -> '007', '5 5' -> '55'."""
    t = re.sub(r"[^0-9.\s]", " ", s or "")
    groups = set()
    for tok in re.findall(r"[0-9][0-9.\s]*", t):
        d = re.sub(r"[^0-9]", "", tok)
        if d:
            groups.add(d)
            # a flattened cell runs its numbers together with single spaces: also offer every
            # contiguous sub-run, so '5 5 1 0 07' yields 55, 1, 007, 5, 51 ...
            parts = tok.split()
            for i in range(len(parts)):
                acc = ""
                for j in range(i, min(len(parts), i + 4)):
                    acc += re.sub(r"[^0-9]", "", parts[j])
                    if acc:
                        groups.add(acc)
    return groups


def _numbers_in(value) -> list:
    return [re.sub(r"[^0-9]", "", n) for n in _NUM.findall(str(value))]


def check_field(kind: str, value, quote: str, passages_norm: str) -> str:
    """'' when the value is backed by the quote and the quote by the passages; else the reason."""
    if value is None:
        return "not read"
    if not quote or not isinstance(quote, str):
        return "no quote"
    if _norm(quote) not in passages_norm:
        return "the quote does not occur in the passages"
    if kind == "fraction":
        q = _norm(quote)
        if q in ("a", "b") and value == 1.0:
            return ""
        if not isinstance(value, (int, float)):
            return "a fraction must be a number"
    if kind == "number" and not isinstance(value, (int, float)):
        return "must be a number"
    if kind == "expr" and not isinstance(value, (str, int, float)):
        return "must be an expression string"
    groups = _digit_groups(quote)
    for d in _numbers_in(value):
        if d and d.lstrip("0") and d not in groups and d.lstrip("0") not in {g.lstrip("0") for g in groups}:
            return "the number %s is not in the quote" % d
    return ""


def transcribe(gid: str, facts: dict, passages: list, conn: dict, em, trace) -> tuple[dict, dict]:
    """One group. -> (fields {name: value}, problems {name: why}). At most 3 model calls, no tools."""
    variant, row = row_for(gid, facts)
    fields = FIELDS[variant]
    if not passages:
        return {}, {f: "no passage was returned for this table (%s)" % ", ".join("%s %s" % (d, q) for d, h, q, n in PLAN.get(gid, []))
                    for f, _w, _k in fields}
    passages_norm = _norm("\n".join(p["text"] for p in passages))
    got: dict = {}
    probs: dict = {}
    retry_note = ""
    # transcription is copying: a reasoning model at "high" deliberates for pages over it
    tconn = dict(conn, reasoning="low", max_tokens=min(int(conn.get("max_tokens") or 4000), 6000))
    for attempt in range(3):
        msg = _ask_message(gid, variant, row, fields, passages, retry_note)
        trace({"type": "prompt", "group": gid, "attempt": attempt + 1, "chars": len(msg)})
        messages = [{"role": "system", "content": TRANSCRIBE}, {"role": "user", "content": msg}]
        pieces: list = []

        def on_piece(kind, text):
            pieces.append((kind, text))
            em.event(type=kind, text=text)
        out = llm.chat_with_retry(tconn, messages, None, on_piece)
        if out.get("usage"):
            em.usage(out["usage"])
        trace({"type": "answer", "group": gid, "attempt": attempt + 1, "reasoning": out.get("reasoning", "")[:20000],
               "content": (out.get("content") or "")[:20000]})
        ans = _parse_json(out.get("content") or "")
        if not isinstance(ans, dict):
            retry_note = "your reply was not a JSON object"
            continue
        probs = {}
        rejected = {}                       # the model quoted something and the check refused it
        for f, what, kind in fields:
            a = ans.get(f)
            if not isinstance(a, dict):
                probs[f] = "missing from the reply"
                rejected[f] = probs[f]
                continue
            why = check_field(kind, a.get("value"), a.get("quote"), passages_norm)
            if why:
                probs[f] = why + ((" -- " + str(a.get("why"))) if a.get("why") else "")
                if why != "not read":
                    rejected[f] = probs[f]
            else:
                got[f] = a["value"]
                got.setdefault("_quotes", {})[f] = a.get("quote")
        if not probs:
            break
        if not rejected and attempt >= 1:
            break                           # asked twice, both times "not in the passages": it is not
        if rejected:
            retry_note = "\n".join("  %s: %s" % (f, w) for f, w in rejected.items()) + \
                "\n(copy the quote EXACTLY from the passage; a number that is not in the quote cannot be used)"
        else:
            # every remaining field was reported absent: one more look, with the size of what was
            # handed over, in case the row sits further down the table than the first reading went
            retry_note = "\n".join("  %s: %s" % (f, w) for f, w in probs.items()) + \
                "\n(the passages above run %d characters; the rows of a continued table and the FOOTNOTE EQUATIONS block come AFTER its first page -- read to the end before answering that a cell is absent)" % sum(len(p["text"]) for p in passages)
    return got, probs


def _set(d: dict, dotted: str, v):
    cur = d
    keys = dotted.split(".")
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = v


def assemble(gid: str, facts: dict, template: dict, got: dict, passages: list) -> dict:
    """The group as the engines read it: the template's shape, the transcribed values, a source built
    from the passages' own document / section / page (not from the model)."""
    variant, row = row_for(gid, facts)
    g = json.loads(json.dumps(template.get(gid) or {}))
    quotes = got.pop("_quotes", {})
    for f, v in got.items():
        _set(g, f, v)
    pages = sorted({str(p["page"]) for p in passages if p.get("page")}, key=lambda x: (len(x), x))
    docs = sorted({p["source"] for p in passages if p.get("source")})
    g["source"] = "%s, pdf p. %s" % (row.split(",")[0] if gid != "material" else "AISC 341-22 Table A3.2",
                                     "-".join(pages[:1] + pages[-1:]) if pages else "?")
    g["quotes"] = quotes
    g["basis"] = row + " -- transcribed by `snl collect` from the converted %s; every value carries the cell it was read from in `quotes`." % ", ".join(docs)
    if variant == "beam_flexure:fr_connection":
        g["mode"] = "fr_connection"
        lbs = [b["Lb_over_ry"] for b in facts.get("beams", {}).values() if b.get("Lb_over_ry")]
        if lbs:
            g["Lb_over_ry"] = round(max(lbs), 1)
            g["Lb_note"] = "Lb/ry from the design package's beam inputs (max over the SMF beams)"
        else:
            g["Lb_divisor"] = 1
            g["Lb_note"] = "no beam bracing length in the package: Lb taken as the full span"
        if facts.get("rbs_c_in"):
            g["rbs_c_in"] = facts["rbs_c_in"]
            g["rbs_note"] = "RBS flange cut c from the HR Steel connection design (%s)" % facts.get("rbs_detail", "")
        g.pop("modifiers", None)
        g["modifier_note"] = ("Table C5.5 footnotes / Sec. C5.4a.1.a.1(a)-(d) modifiers (continuity plates, panel zone "
                              "V_pz/V_ye, clear span to depth, flange slenderness) are NOT evaluated by Collect; none applied.")
        for k in ("a_over_thetay", "b_over_thetay", "IO_over_thetay", "LS_over_thetay", "CP_over_thetay"):
            g.pop(k, None)
    elif variant == "beam_flexure:member":
        g["mode"] = "member"
    return g


# ---------------------------------------------------------------- validation
_ID_PAGE = re.compile(r"(Table|Tbl\.?|Eq\.?|Equation|Sec(?:tion)?\.?|§)\s*[A-Z]?\d+(?:[.\-]\d+)*[a-z]?", re.I)
_PAGE = re.compile(r"\b(p\.?|pp\.?|page|pdf|printed)\s*\d+", re.I)
_SAMPLE = dict(h=30.0, tw=0.5, L=168.0, ry=4.0, PG=100.0, Pye=1000.0, bf=16.0, tf=1.2, Lb=100.0, d=36.0,
               E=29000.0, Fye=55.0, Fy=50.0, KL=168.0, r=3.0, sqrt=math.sqrt, min=min, max=max, abs=abs)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _expr_ok(expr) -> tuple[bool, str]:
    if _num(expr):
        return (expr > 0, "must be > 0")
    if not isinstance(expr, str) or not expr.strip():
        return False, "missing"
    if re.search(r"[^0-9A-Za-z_+\-*/(). ]", expr):
        return False, "characters outside a plain arithmetic expression"
    try:
        v = eval(expr, {"__builtins__": {}}, dict(_SAMPLE))      # noqa: S307 -- whitelisted symbols only
    except Exception as e:                                       # noqa: BLE001
        return False, "does not evaluate: %s" % e
    return (_num(v) and v > 0, "evaluates to %r at sample inputs" % (v,))


def _source_ok(g: dict) -> bool:
    s = str(g.get("source") or "")
    return bool(_ID_PAGE.search(s)) and bool(_PAGE.search(s))


def _ordered(*vals) -> bool:
    vals = [v for v in vals if _num(v)]
    return all(a <= b for a, b in zip(vals, vals[1:]))


def validate(params: dict, needed: list) -> tuple[bool, dict]:
    """-> (every needed group passed, {group: [problems]}). An empty list means the group passed."""
    probs: dict = {}
    for gid in needed:
        g = params.get(gid)
        p: list = []
        if not isinstance(g, dict):
            probs[gid] = ["group missing"]
            continue
        if not _source_ok(g):
            p.append("source must name a table/equation/section id AND a printed page")
        if gid == "material":
            if not (_num(g.get("Fy_ksi")) and g["Fy_ksi"] > 0):
                p.append("Fy_ksi")
            if not (_num(g.get("Ry_expected")) and 1.0 <= g["Ry_expected"] <= 2.0):
                p.append("Ry_expected outside 1.0-2.0")
        elif gid == "beam_flexure":
            mode = str(g.get("mode") or "member")
            if mode == "fr_connection":
                ok, why = _expr_ok(g.get("a_expr", g.get("a_abs")))
                if not ok:
                    p.append("a_expr/a_abs: " + why)
                if not (_num(g.get("b_abs")) and g["b_abs"] > 0):
                    p.append("b_abs")
                for k in ("IO_frac_of_a", "LS_frac_of_b", "CP_frac_of_b"):
                    if not (_num(g.get(k)) and 0 < g[k] <= 1.0):
                        p.append(k)
            else:
                for k in ("a_over_thetay", "b_over_thetay", "IO_over_thetay", "LS_over_thetay", "CP_over_thetay"):
                    if not (_num(g.get(k)) and g[k] > 0):
                        p.append(k)
                if not _ordered(g.get("IO_over_thetay"), g.get("LS_over_thetay"), g.get("CP_over_thetay")):
                    p.append("IO <= LS <= CP")
                if not _ordered(g.get("a_over_thetay"), g.get("b_over_thetay")):
                    p.append("a <= b")
            if not (_num(g.get("c_residual")) and 0 < g["c_residual"] <= 1.0):
                p.append("c_residual outside (0, 1]")
        elif gid == "column_flexure":
            for k in ("a_expr", "b_expr", "c_expr"):
                ok, why = _expr_ok(g.get(k))
                if not ok:
                    p.append("%s: %s" % (k, why))
            for k in ("IO_frac_of_a", "LS_frac_of_b", "CP_frac_of_b"):
                if not (_num(g.get(k)) and 0 < g[k] <= 1.0):
                    p.append(k)
            if not _ordered(g.get("LS_frac_of_b"), g.get("CP_frac_of_b")):
                p.append("LS <= CP")
            if "a_max" in g and not (_num(g["a_max"]) and g["a_max"] > 0):
                p.append("a_max")
            if not (_num(g.get("b_max")) and g["b_max"] > 0):
                p.append("b_max")
        elif gid == "brace_axial":
            comp = g.get("compression") or {}
            for side in ("stocky", "slender"):
                blk = comp.get(side) or {}
                for k in ("a_over_dc", "b_over_dc", "IO_over_dc", "LS_over_dc", "CP_over_dc"):
                    if not (_num(blk.get(k)) and blk[k] > 0):
                        p.append("compression.%s.%s" % (side, k))
                if not _ordered(blk.get("IO_over_dc"), blk.get("LS_over_dc"), blk.get("CP_over_dc")):
                    p.append("compression.%s IO <= LS <= CP" % side)
                if not (_num(blk.get("c")) and 0 < blk["c"] <= 1.0):
                    p.append("compression.%s.c" % side)
            ten = g.get("tension") or {}
            for k in ("a_over_dT", "b_over_dT", "IO_over_dT", "LS_over_dT", "CP_over_dT"):
                if not (_num(ten.get(k)) and ten[k] > 0):
                    p.append("tension." + k)
            if not _ordered(ten.get("IO_over_dT"), ten.get("LS_over_dT"), ten.get("CP_over_dT")):
                p.append("tension IO <= LS <= CP")
            if not (_num(g.get("Ry_expected")) and 1.0 <= g["Ry_expected"] <= 2.0):
                p.append("Ry_expected outside 1.0-2.0")
        probs[gid] = p
    return all(not v for v in probs.values()), probs


# ---------------------------------------------------------------- assembling the file
def _merge(template: dict, filled: dict, needed: list) -> dict:
    """The template with the needed groups replaced by what the model returned. Nothing else moves."""
    out = json.loads(json.dumps(template))
    for gid in needed:
        if isinstance(filled.get(gid), dict):
            out[gid] = filled[gid]
    return out


def _parse_json(text: str) -> dict | None:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _readme(facts: dict, ok: bool, probs: dict, conn: dict, n_searches: int) -> list:
    when = datetime.datetime.now().isoformat(timespec="seconds")
    lines = ["RUN-SPECIFIC parameter file for %s, collected from the standards corpus on this PC %s by `snl collect`." % (facts["name"], when),
             "Model: %s. Standards searches: %d. Retrieval log: %s. Every group's `source` names the table and page it was read from." % (conn.get("model") or "MOCK", n_searches, EVIDENCE_NAME),
             "Groups this building needs: %s." % ", ".join(facts["needed"])]
    if ok:
        lines.append("verified=true: every needed group was read from a retrieved passage and passed validation (shape, ranges, ordering, expressions).")
    else:
        lines.append("verified=false: " + "; ".join("%s (%s)" % (g, ", ".join(p)) for g, p in probs.items() if p))
    lines.append("Groups not listed above keep the repository values (procedure constants, numerics, panel-zone modelling settings).")
    return lines


def write_evidence(job: str, facts: dict, searches: list, probs: dict, ok: bool, conn: dict, out_path: str) -> str:
    ev = {"asked": datetime.datetime.now().isoformat(timespec="seconds"), "rag_url": rag.url(),
          "model": conn.get("model") or "MOCK", "building": facts, "needed": facts["needed"],
          "verified": ok, "problems": {g: p for g, p in probs.items() if p}, "written": out_path,
          "refused": [x for x in searches if x.get("via") == "refused"], "searches": searches}
    p = os.path.join(job, EVIDENCE_NAME)
    json.dump(ev, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False, default=str)
    # the §16.1.4 criteria document reads retrieval_log.md for its "standards consulted" section
    md = ["# Standards consulted -- component parameters (snl collect, %s)" % ev["asked"], ""]
    for s in searches:
        a = s.get("args") or {}
        md.append("- **%s** %s `%s` -> %d passage(s)%s" % (a.get("document") or "?", a.get("type") or "", (a.get("query") or "")[:90],
                                                          s.get("hits") or 0, (" via " + s["via"]) if s.get("via") and s["via"] != "as-asked" else ""))
        for r in (s.get("results") or [])[:3]:
            md.append("    - %s %s p. %s" % (r.get("source") or "", r.get("section") or "", r.get("page") or "?"))
    open(os.path.join(job, "retrieval_log.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    return p


# ---------------------------------------------------------------- the step
def run(job: str, out_name: str = OUT_NAME, emit: Emitter | None = None, conn: dict | None = None) -> dict:
    em = emit or Emitter()
    job = os.path.abspath(job)
    conn = conn or llm.connection()
    t0 = time.time()
    em.event(type="status", text="reading the design package")
    try:
        facts = gather(job)
    except Exception as e:                                       # noqa: BLE001
        em.event(type="error", text="no design package in %s -- run HR Steel on this project first (%s)" % (job, e))
        return {"ok": False, "verified": False, "path": "", "missing": ["design package"], "searches": [], "usage": {}}
    template = json.load(open(_TEMPLATE, encoding="utf-8"))
    em.event(type="milestone", text="%s: %s, %s%s; groups to collect: %s" % (
        facts["name"], facts["system"] or "system not stated",
        "RBS moment connections" if facts["rbs"] else ("moment connections" if facts["moment_frame"] else "no moment connections"),
        ", braced" if facts["braced"] else "", ", ".join(facts["needed"])))

    searching = rag.configured()
    searches: list = []
    corpus_line = ""
    if searching:
        st = rag.status(force=True)
        cm = rag.corpus_map(st)
        corpus_line = rag.describe_corpus(cm)
        em.event(type="milestone", text="standards corpus -- " + corpus_line)
        if cm.get("known") and cm.get("absent"):
            em.log("standards corpus: %s absent on this PC -- a group whose table lives there cannot be collected"
                   % ", ".join(rag.TITLES[k] for k in cm["absent"]))
    else:
        em.event(type="error", text="no standards server (RAG_API_URL is empty): nothing can be collected -- start the Query file manager from the hub's Modules page")
        return {"ok": False, "verified": False, "path": "", "missing": facts["needed"], "searches": [], "usage": {}}

    repeats: dict = {}                                       # normalised search -> times the model sent it
    refused: list = []                                       # searches the guard closed, for the record

    def _key(args: dict) -> tuple:
        q = re.sub(r"[^0-9a-z./ -]", " ", (args.get("query") or "").lower())
        return ((args.get("document") or args.get("doc") or "A342").upper(), (args.get("type") or "").lower(),
                " ".join(q.split()), (args.get("clause") or "").upper(), (args.get("chapter") or "").upper())

    def do_search(args: dict, raw: bool = False):
        n = len(searches) + 1
        em.event(type="tool", name="search_engineering_standards", step=n, title=_tool_title(args))
        # The same search again is the model not accepting what it was given. Three times is the
        # limit: after that the corpus is declared not to hold it and the search is refused without
        # a round trip, so the run ends with a missing group instead of 68 identical queries.
        k = _key(args)
        repeats[k] = repeats.get(k, 0) + 1
        if repeats[k] > REPEAT_LIMIT or len(searches) >= MAX_SEARCHES:
            why = ("ABSENT -- this exact search has been made %d times and returned the same passages each time. The "
                   "corpus does not hold what you are looking for in a form this search can find. Do NOT send it again: "
                   "put the group under \"missing\" with what you did find, and move on."
                   % (repeats[k] - 1)) if repeats[k] > REPEAT_LIMIT else \
                  ("ABSENT -- %d searches is the ceiling for this step. Write the file now with what you have; a group "
                   "you could not source goes under \"missing\"." % MAX_SEARCHES)
            refused.append({"n": n, "args": args, "why": why[:80]})
            em.event(type="tool_result", step=n, summary="refused: " + why[:90], ms=0)
            searches.append({"n": n, "args": args, "hits": 0, "note": why, "ms": 0, "via": "refused", "results": [], "passages": []})
            return {"ok": False, "results": [], "note": why, "via": "refused"} if raw else "NO PASSAGES. " + why
        res = rag.search(args.get("query") or "", args.get("document") or args.get("doc") or "A342",
                         int(args.get("top_k") or 5), args.get("clause") or "", args.get("chapter") or "",
                         qtype=args.get("type") or "", want_commentary=bool(args.get("want_commentary")),
                         neighbors=args.get("context_neighbors"))
        searches.append({"n": n, "args": args, "hits": len(res.get("results") or []), "note": res.get("note"),
                         "ms": res.get("ms"), "via": res.get("via"), "missing_document": res.get("missing_document"),
                         "results": [{k: h.get(k) for k in ("source", "section", "title", "page", "score")} for h in (res.get("results") or [])],
                         "passages": [h.get("text") for h in (res.get("results") or [])]})
        nres = len(res.get("results") or [])
        summ = "%d passage(s)" % nres + ((" via " + str(res["via"]).split(" (")[0]) if res.get("via") not in ("", "as-asked", None) else "")
        if repeats[k] == REPEAT_LIMIT:
            summ += " -- asked %d times now; one more and it is declared absent" % REPEAT_LIMIT
        em.event(type="tool_result", step=n, summary=summ, ms=res.get("ms") or 0)
        return res if raw else rag.render(res)

    usage: dict = {}
    # the trace, written as it happens -- a cancelled run still leaves it on disk
    trace_path = os.path.join(job, TRANSCRIPT_NAME)
    tf = open(trace_path, "w", encoding="utf-8")

    def trace(rec: dict):
        rec = dict(rec, t=time.time())
        tf.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n"); tf.flush()

    em.event(type="status", text="fetching the tables each group is read from")
    fetched = prefetch(facts, do_search)
    trace({"type": "prefetch", "groups": {g: [(p["how"], p["section"], p["page"], len(p["text"])) for p in ps] for g, ps in fetched.items()}})
    em.event(type="milestone", text="tables fetched -- %s" % "; ".join(
        "%s: %d passage(s)" % (g, len(ps)) for g, ps in fetched.items()))

    filled: dict = {}
    tprobs: dict = {}
    if conn.get("mock"):
        em.event(type="status", text="model MOCK: taking the values from the Ex22 corpus-read example")
        filled = mock_collect(facts, None)
    else:
        em.event(type="status", text="asking %s to transcribe each group from its table (reasoning low, no searching)" % conn["model"])
        for gid in facts["needed"]:
            em.event(type="milestone", text="transcribing %s -- %s" % (gid, row_for(gid, facts)[1]))
            try:
                got, probs = transcribe(gid, facts, fetched.get(gid) or [], conn, em, trace)
            except llm.LLMError as e:
                em.event(type="error", text=str(e))
                tf.close()
                return {"ok": False, "verified": False, "path": "", "missing": facts["needed"], "searches": searches, "usage": usage}
            trace({"type": "group", "group": gid, "fields": {k: v for k, v in got.items() if k != "_quotes"}, "problems": probs})
            if probs:
                tprobs[gid] = probs
                em.log("   %-15s NOT READ: %s" % (gid, "; ".join("%s (%s)" % (f, w) for f, w in probs.items())))
            if got:
                filled[gid] = assemble(gid, facts, template, got, fetched.get(gid) or [])
    tf.close()

    params = _merge(template, filled, facts["needed"])
    ok, probs = validate(params, facts["needed"])
    for g, tp in tprobs.items():
        probs[g] = (probs.get(g) or []) + ["%s: %s" % (f, w) for f, w in tp.items()]
    ok = all(not v for v in probs.values())
    missing = [g for g, p in probs.items() if p]
    params["verified"] = bool(ok)
    params["source"] = ("collected from the standards corpus on this PC by `snl collect` %s -- "
                        % datetime.datetime.now().isoformat(timespec="seconds")
                        + "; ".join("%s: %s" % (g, (params.get(g) or {}).get("source") or "MISSING") for g in facts["needed"]))
    params["_README"] = _readme(facts, ok, probs, conn, len(searches))
    out_path = os.path.join(job, out_name if ok else PARTIAL_NAME)
    json.dump(params, open(out_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    write_evidence(job, facts, searches, probs, ok, conn, out_path)
    for g in facts["needed"]:
        em.log("   %-15s %s" % (g, ("ok -- " + str((params.get(g) or {}).get("source") or ""))[:140] if not probs.get(g)
                                else "NOT COLLECTED -- " + "; ".join(probs[g])))
    if ok:
        em.event(type="milestone", text="collected: %s (%d groups, %d standards searches, %d s) -- Run analyses is now open"
                 % (os.path.basename(out_path), len(facts["needed"]), len(searches), int(time.time() - t0)))
    else:
        em.event(type="error", text="not every group could be read from the corpus: %s. Written as %s; Run analyses stays closed until every group is collected."
                 % (", ".join(missing), PARTIAL_NAME))
    em.log(">> params: " + out_path)
    return {"ok": ok, "verified": ok, "path": out_path, "missing": missing, "searches": searches, "usage": usage}


# ---------------------------------------------------------------- MOCK
def mock_collect(facts: dict, search=None) -> dict:
    """Offline: the corpus-read Ex22 values, labelled as such. The pre-fetch already made the searches."""
    ex = json.load(open(_EX22, encoding="utf-8"))
    out = {}
    for gid in facts["needed"]:
        g = json.loads(json.dumps(ex.get(gid) or {}))
        if gid == "brace_axial" or "PLACEHOLDER" in str(g.get("basis", "")).upper():
            continue                                             # the example holds no corpus-read brace values
        src = {"material": "AISC 342-22 Table A5.2, pdf 43 -> AISC 341-22 Table A3.2 (Ry)",
               "beam_flexure": "AISC 342-22 Table C5.5, pdf 113-114, row 'RBS moment connection in conformance with ANSI/AISC 358'",
               "column_flexure": "AISC 342-22 Table C3.6, printed p. 46 (pdf 78), 'Columns and braces in compression, highly ductile'"}[gid]
        g["source"] = "MOCK (values from the Ex22 corpus-read example) -- " + src
        out[gid] = g
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="snl collect")
    ap.add_argument("job")
    ap.add_argument("--out", default=OUT_NAME, help="file name written into the job folder (default %s)" % OUT_NAME)
    a = ap.parse_args(argv)
    r = run(a.job, out_name=a.out, emit=Emitter())
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
