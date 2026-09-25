"""collect.py -- read the component modelling parameters out of the standards, BEFORE the analyses run.

    python -m snl collect <job folder> [--out hinge_params_collected.json]

The nonlinear analyses need a component parameter file (pushover/hinge_params.json's shape): the
backbone a, b, c and the IO / LS / CP acceptance criteria for every hinge kind the building has, the
expected material, the ductility screens. The repository copy is a PLACEHOLDER written from memory and
says so in its own _README, which also says what has to happen instead: send a retrieval plan to the
Query file manager, copy the printed values into the file, set verified=true and fill `source` with
the exact table / equation ids and printed pages.

That is this step. The model is given the building (system, sections, connection type, bracing) and
the file's schema, follows the same retrieval policy the Review uses (snl/standards.py), and hands
back the filled file. Every group is then validated -- shape, ranges, ordering, expressions that
evaluate, a source that names a table and a page -- and the file is written as
`hinge_params_collected.json` ONLY when every group the building needs passed. Otherwise the partial
result is written as `hinge_params_collected.partial.json`, the missing groups are listed, and the
hub's Run analyses button stays closed: an analysis on placeholders is what produced the red reports.

The hub then runs the analyses with `--params hinge_params_collected.json`, and the reports carry the
citations from the first line rather than after a re-issue. Model MOCK (tests, offline) takes the
values from the Ex22 corpus-read example instead of a provider.
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
from .standards import RETRIEVAL_POLICY, TOOLS

OUT_NAME = "hinge_params_collected.json"
PARTIAL_NAME = "hinge_params_collected.partial.json"
EVIDENCE_NAME = "collect_evidence.json"
MAX_TURNS = 80                     # a safety ceiling on the agent loop, not a search budget

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
            "connections": conns, "rbs": rbs, "moment_frame": moment, "braced": braced,
            "beams": {k: {"roles": sorted(v["roles"]), "Lb_over_ry": v["Lb_over_ry"]} for k, v in beams.items()},
            "columns": {k: {"roles": sorted(v["roles"])} for k, v in cols.items()},
            "braces": {k: {"roles": sorted(v["roles"])} for k, v in braces.items()},
            "needed": needed}


# ---------------------------------------------------------------- the ask
SYSTEM = """You are filling the component modelling parameter file for the nonlinear analyses (ASCE 41-23 pushover with AISC 342-22 component models, ASCE 7-22 Chapter 16 NLRHA) of ONE steel building. The file's schema is given to you as JSON: keep every key, change only the VALUES of the groups you are asked for, and give every group a `source`.

The whole point of this step is that every number comes from a passage you retrieved THIS session. The file you are replacing was written from memory and the reports it produced were marked UNVERIFIED for it. A value you cannot find is reported as missing, never typed from memory.

What each group needs, and where it lives:
- material: Fy for the shapes and the expected-strength factor Ry (AISC 341-22 Table A3.2, reached through AISC 342-22 A5.2 / Table A5.2). Fye = Ry Fy.
- beam_flexure: for a moment frame whose beams frame in with fully restrained connections, the hinge is the CONNECTION: AISC 342-22 Table C5.5, the row for the connection type (e.g. "RBS moment connection in conformance with ANSI/AISC 358"). Set mode="fr_connection" and give the row's modelling parameters a and b as printed (an expression of the beam's h/tw, bf/2tf, Lb/ry, L/d where the table gives one -- write it in Python with those symbol names; a constant where it gives a constant), the caps (a_max, b_abs), the residual c_residual, and the acceptance criteria as fractions of a and b (IO_frac_of_a, LS_frac_of_b, CP_frac_of_b) as the table prints them. Note the table's own footnotes on continuity plates, panel zone, clear-span-to-depth and flange slenderness: they are the `modifiers` an engineer applies -- record what the table says, do not decide them. For a beam that is NOT an FR connection use mode="member" with Table C2.2 (a_over_thetay, b_over_thetay, c_residual, IO_over_thetay, LS_over_thetay, CP_over_thetay).
- column_flexure: AISC 342-22 Table C3.6, "Columns and braces in compression", the ductility row that matches the sections (highly ductile for an SMF). The table gives a and b as expressions in h/tw, L/ry and PG/Pye with caps: write a_expr, b_expr, c_expr in Python with exactly those symbols (h, tw, L, ry, PG, Pye), the caps a_max / b_max, and the acceptance fractions IO_frac_of_a, LS_frac_of_b, CP_frac_of_b. Keep force_controlled_above_P_over_Pye and Mpce_axial_reduction as the standard states them (C3.4a.2.b and the expected flexural strength with axial reduction).
- brace_axial (only for a braced frame): the compression and tension backbones and acceptance for the brace section type (AISC 342-22 Chapter C brace tables; ASCE 41-23 Chapter 9 where AISC 342 defers), with the stocky / slender limits on KL/r, and Ry for the brace material (AISC 341-22 Table A3.2, e.g. A500 Gr. C).

Rules:
- Cite in each group's `source`: the document, the table or equation id, and the printed page, e.g. "AISC 342-22 Table C3.6, p. 46 (pdf 78)". A group without a real table behind it is reported under "missing" with the reason, and its values are left as they were.
- The converted text can flatten a table's equations into digit strings ("5 5 1 0 07 0 95 0 5 2 4"). Read them back into the expression the table prints and say in `decode_note` that you did, quoting the string. If a string cannot be read back unambiguously, that value is missing.
- Never change the schema, never add groups, never touch nsp / numerics / gravity_for_pushover / fema_p695 / panel_zones. Do not set `verified` yourself; it is computed from what you sourced.
- When you are done, reply with ONLY the JSON object (the whole file), no prose, no code fence.

""" + RETRIEVAL_POLICY


def _facts_message(facts: dict, template: dict) -> str:
    f = {k: v for k, v in facts.items() if k not in ("job",)}
    return ("BUILDING (from the design package):\n" + json.dumps(f, indent=1, ensure_ascii=False)
            + "\n\nGROUPS TO FILL: " + ", ".join(facts["needed"])
            + "\n\nFILE SCHEMA (the repository placeholder; keep every key, replace the values of the groups above, "
              "give each a `source`; leave the other groups exactly as they are):\n"
            + json.dumps({k: v for k, v in template.items() if k != "_README"}, indent=1, ensure_ascii=False)
            + "\n\nFill the file now. Reply with the complete JSON object only.")


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
          "searches": searches}
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

    def do_search(args: dict) -> str:
        n = len(searches) + 1
        em.event(type="tool", name="search_engineering_standards", step=n, title=_tool_title(args))
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
        em.event(type="tool_result", step=n, summary=summ, ms=res.get("ms") or 0)
        return rag.render(res)

    usage: dict = {}
    if conn.get("mock"):
        em.event(type="status", text="model MOCK: taking the values from the Ex22 corpus-read example")
        filled = mock_collect(facts, do_search)
    else:
        em.event(type="status", text="asking %s (standards searches as needed)" % conn["model"])
        sys_msg = SYSTEM + "\nDOCUMENTS IN THE CORPUS -- " + corpus_line + \
            "\nThere is no limit on the number of searches: every value comes from a passage retrieved this session."
        messages = [{"role": "system", "content": sys_msg},
                    {"role": "user", "content": _facts_message(facts, template)}]
        filled = None
        for turn in range(MAX_TURNS):
            def on_piece(kind, text):
                em.event(type=kind, text=text)
            try:
                out = llm.chat_with_retry(conn, messages, TOOLS, on_piece)
            except llm.LLMError as e:
                em.event(type="error", text=str(e))
                return {"ok": False, "verified": False, "path": "", "missing": facts["needed"], "searches": searches, "usage": usage}
            if out.get("usage"):
                em.usage(out["usage"]); usage = out["usage"]
            if out["tool_calls"]:
                messages.append({"role": "assistant", "content": out["content"] or None, "tool_calls": out["tool_calls"]})
                for tc in out["tool_calls"]:
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {"query": tc["function"]["arguments"], "document": "A342"}
                    result = do_search(args if isinstance(args, dict) else {}) if tc["function"]["name"] == "search_engineering_standards" \
                        else "unknown tool %s" % tc["function"]["name"]
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                continue
            filled = _parse_json(out["content"] or "")
            if filled:
                break
            messages.append({"role": "assistant", "content": out["content"] or ""})
            messages.append({"role": "user", "content": "That was not a JSON object. Reply with the complete parameter file as ONE JSON object and nothing else."})
        if not filled:
            em.event(type="error", text="the model did not return the parameter file after %d turns" % MAX_TURNS)
            return {"ok": False, "verified": False, "path": "", "missing": facts["needed"], "searches": searches, "usage": usage}

    params = _merge(template, filled, facts["needed"])
    ok, probs = validate(params, facts["needed"])
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
    """Offline: the corpus-read Ex22 values, labelled as such. Makes the same searches so the log is real."""
    ex = json.load(open(_EX22, encoding="utf-8"))
    if search:
        search({"type": "exact_table", "document": "A342", "query": "C3.6", "purpose": "column modelling parameters"})
        search({"type": "exact_table", "document": "A342", "query": "C5.5", "purpose": "FR connection modelling parameters"})
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
