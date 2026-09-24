"""review.py -- the Review step: the model reads what the run measured and writes the engineer's review.

    python -m snl review <job folder> [--focus "..."] [--no-standards] [--max-searches N]

Inputs are what a finished run left in the job folder (snl_summary.json, snl_run.json, nlrha/nlrha_package.json,
pushover/pushover_package.json, ddm_results.json, design/calc_package.json, the §16.1.4 draft, any feedback
loops) -- gathered here into one bounded evidence document, every number from a file, nothing invented. The
model (the hub's connection, via STELTIC_LLM_*) grounds the clauses it cites through the standards search
(snl/rag.py, RAG_API_URL) and writes review.md / review.html / review_transcript.json beside the analyses.

While it works it prints one JSON event per line in the vocabulary the Steltic hub relays -- reasoning,
token, tool, tool_result, milestone, status, usage -- so the hub shows the model's text on the run line, its
reasoning in the separate box, and one line per standards search, exactly as it does for HR Steel and CFS.
Plain lines are the log. Model MOCK writes the review from the evidence alone, offline, with the same searches.
"""
from __future__ import annotations
import datetime, html, json, os, re, sys, time

from . import llm, rag

# Safety ceiling on the agent loop, not a search budget: a capped run stops at its budget long
# before this, and an uncapped one searches until the review is written.
MAX_TURNS = 80
TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_engineering_standards",
        "description": ("Search the licensed standards corpus for the clause, table or equation you are about to cite. "
                        "ONE provision, ONE document per call. When you know the id, ask for it EXACTLY: type=\"exact_section\" | "
                        "\"exact_equation\" | \"exact_table\" with `query` = the id alone (\"16.4.1.2\", \"16.4-1\", \"12.12-1\") -- not a "
                        "sentence, not the document name. Use type=\"fts\" only to NAVIGATE to an id, with `query` in the standard's own "
                        "printed words, one idea, no ids mixed in; read the ids it returns and ask for them exactly next. Search only "
                        "documents the corpus holds (see DOCUMENTS IN THE CORPUS). A miss is escalated for you (exact id, filters dropped, "
                        "reworded, other documents) and the result says which document answered. Cite only what a passage supports; a "
                        "clause the search cannot find is cited from memory and marked (UNVERIFIED)."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The id alone for an exact type; otherwise the standard's own printed words, one idea"},
                "document": {"type": "string", "enum": list(rag.DOCUMENTS), "description": "; ".join("%s = %s" % (k, d) for k, (_c, d) in rag.DOCUMENTS.items())},
                "type": {"type": "string", "enum": ["exact_section", "exact_equation", "exact_table", "id", "fts", "keyword"],
                         "description": "How to look it up. Exact types take the id alone in `query`. fts/keyword navigate."},
                "clause": {"type": "string", "description": "Exact clause / table / equation id, when you did not put it in `query`"},
                "chapter": {"type": "string", "description": "Restrict a navigation query to one chapter, e.g. 16 or F"},
                "purpose": {"type": "string", "description": "Why you need it, short -- recorded in the transcript"},
                "want_commentary": {"type": "boolean", "description": "Commentary instead of the provision. Default false; commentary never supplies a design value."},
                "context_neighbors": {"type": "integer", "minimum": 0, "maximum": 2,
                                      "description": "Surrounding chunks to include. Use 1 for an equation, to get its \"where:\" variables."},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query", "document"],
        },
    },
}]

SYSTEM = """You are the independent structural reviewer (ASCE 7-22 §16.5 design review) for a steel building whose Steltic design package has been run through the three nonlinear analyses of Steltic_nonlinear: the ASCE 41-23 pushover (AISC 342-22 component models), the ASCE 7-22 Chapter 16 nonlinear response history analysis, and the DDM system-capacity (GMNIA) check. You are handed the run's measured results as an EVIDENCE document.

Write the review as Markdown with these sections, in this order:
1. **Verdict** -- one paragraph: does the design pass Chapter 16 and the other checks, and how much margin is there.
2. **What the run measured** -- the numbers that matter, each attributed to its analysis.
3. **Chapter 16 acceptance** -- every criterion the run checked (mean storey drift vs 2 x Table 12.12-1, unacceptable responses, deformation-controlled actions, force-controlled actions with Eq. 16.4-1, residual drift where applicable), with the measured value, the limit, and the governing clause.
4. **Pushover** -- mechanism, overstrength Omega vs Omega_0, target displacement, BPON performance levels for the Risk Category, whether the NSP was permitted, the descending branch.
5. **DDM system capacity** -- governing combination, lambda_u and phi_s lambda_u, the transfer gate, mechanism class.
6. **Design basis and the design criteria draft** -- consistency between the linear design (R, C_d, I_e, drift limit) and what the nonlinear analyses used; gaps to close in the §16.1.4 document.
7. **What to change, and why** -- ranked; each item names the member group or parameter, the measured quantity that motivates it, and the clause that governs. If nothing should change, say so and why.
8. **Open items and verification** -- placeholders, unverified parameters, records retried, anything flagged in the evidence.

Rules:
- Every number comes from the EVIDENCE. Never invent a value; when the evidence lacks one, say "not in the run".
- Before you cite a clause, table or equation, look it up with search_engineering_standards and cite it as [ASCE 7-22 §16.4.1.2, p. 149] with the page the passage gives. A clause you could not find: cite it from memory and append (UNVERIFIED).
- Ratios and percentages are written with their basis (e.g. "mean drift 1.46% vs 2.00% = 2 x 1.0% Table 12.12-1, RC IV").
- Be specific and short. No preamble, no closing pleasantries. Write in English.

===== RETRIEVAL POLICY (mandatory -- how every search_engineering_standards call is written) =====
This is the query policy the Query file manager is built for (steltic_grokbot skills/Skill_querying_PACKAGED.md),
the same policy HR Steel and CFS follow. The tool applies it to whatever you send and records the form it
sent; write it that way yourself.

1. ONE document per call, by its key: document="ASCE7" | "ASCE41" | "A342" | "A341" | "A360" | "A358".
   Never search all documents blindly. System -> component -> action -> method -> the one document that
   governs: Chapter 16 acceptance is ASCE 7-22; component models and BPON are ASCE 41-23; modelling
   parameters and acceptance criteria for steel components are AISC 342-22; detailing is AISC 341-22.
2. EXACT ID WHEN KNOWN. type="exact_section" | "exact_equation" | "exact_table", query = the id ALONE:
     {"type":"exact_section","document":"ASCE7","query":"16.4.1.2","purpose":"mean drift limit"}
     {"type":"exact_equation","document":"ASCE7","query":"16.4-1","purpose":"force-controlled action"}
     {"type":"exact_table","document":"A342","query":"C3.6","purpose":"column modelling parameters"}
   Not a sentence. Not the document name. Not "ASCE 7-22 Equation 16.4-1 for force-controlled actions".
3. FULL TEXT ONLY TO NAVIGATE: type="fts", query = the standard's own printed words, one idea, no
   sentence, no ids mixed in ("mean story drift ratio limit", not "what drift is allowed in Chapter 16").
   Read the ids it returns, then ask for them EXACTLY in the next call.
4. One provision per call: provision, definition, equation, limits, table, procedure -- each its own call.
   For every equation you compute from, also fetch its "where:" variables (context_neighbors=1), its
   applicability and its exceptions.
5. Waves: (1) navigation + the core provisions -> (2) definitions, limits and the cross-references the
   results name -> (3) verification of every factor that entered a number you quote.
6. Commentary ids carry a C- prefix; want_commentary stays false unless you need intent, and a commentary
   excerpt never supplies a design value.
7. Search only the documents listed under DOCUMENTS IN THE CORPUS. A document listed as ABSENT was never
   converted on this PC: do not search it (such a call is answered with a corpus-gap note, is not counted,
   and is wasted); cite it from memory, marked (UNVERIFIED), and say in section 8 that it was unavailable.
8. A miss is escalated for you: the exact id, the clause/chapter filter dropped, a reworded query, then
   every other document. When the result says another document answered, cite THAT document, not the one
   you asked for.
9. Never invent an id. NO PASSAGES after the ladder is an honest answer: re-word ONCE in the standard's own
   terms or ask for the parent section; do not fill the gap from memory without marking it (UNVERIFIED).
   Every clause you cite as verified comes from a passage returned this session -- cite document, section,
   eq/table id and the printed page the passage gives.
10. Search as often as the review needs. There is no reward for searching less; an uncited clause you
   could have looked up is the failure, not an extra call.
"""


# ---------------------------------------------------------------- evidence
def _json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        s = open(path, encoding="utf-8").read()
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s))
    except Exception:                                            # noqa: BLE001
        return default


def _text_of_html(path, limit=6000):
    if not os.path.exists(path):
        return ""
    h = open(path, encoding="utf-8", errors="replace").read()
    t = re.sub(r"<style.*?</style>|<script.*?</script>", "", h, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()[:limit]


def _r(v, n=4):
    if isinstance(v, float):
        return round(v, n)
    if isinstance(v, dict):
        return {k: _r(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [_r(x, n) for x in v]
    return v


def gather(job: str) -> dict:
    """Everything the reviewer may cite, bounded (a few tens of kB), every number from a file in the job folder."""
    job = os.path.abspath(job)
    ev: dict = {"job": os.path.basename(job), "files": {}}
    for rel in ("snl_summary.json", "snl_run.json", "nlrha/nlrha_package.json", "pushover/pushover_package.json", "ddm_results.json",
                "design/calc_package.json", "design_criteria_16_1_4.html", "four_analyses.html", "report.html"):
        ev["files"][rel] = os.path.exists(os.path.join(job, rel))
    summ = _json(os.path.join(job, "snl_summary.json"), {}) or {}
    ev["summary"] = _r(summ)
    run = _json(os.path.join(job, "snl_run.json"), {}) or {}
    ev["run"] = {"started": run.get("started"), "finished": run.get("finished"),
                 "steps": {k: {"returncode": v.get("returncode"), "seconds": v.get("seconds"), "skipped": v.get("skipped")}
                           for k, v in (run.get("steps") or {}).items() if isinstance(v, dict)}}
    # Chapter 16
    nl = _json(os.path.join(job, "nlrha", "nlrha_package.json"))
    if nl:
        recs = nl.get("per_record") or []
        ev["nlrha"] = {
            "verdict": _r(nl.get("verdict")), "limits": _r(nl.get("limits")),
            "story": [{"story": s.get("story"), "h_in": s.get("h_in"), "mean_X_pct": _r(100 * (s.get("mean_X") or 0), 2), "mean_Y_pct": _r(100 * (s.get("mean_Y") or 0), 2),
                       "max_X_pct": _r(100 * (s.get("max_X") or 0), 2), "max_Y_pct": _r(100 * (s.get("max_Y") or 0), 2), "ok": s.get("ok")} for s in (nl.get("story") or [])],
            "records": [{"label": r.get("label"), "sf": _r(r.get("sf"), 2), "converged": r.get("converged"), "peak_drift_pct": _r(100 * (r.get("peak_drift") or 0), 2),
                         "residual_pct": _r(100 * (r.get("residual") or 0), 3), "unacceptable": r.get("unacceptable"), "flags": r.get("flags"), "retry": bool(r.get("retry"))} for r in recs],
            "deformation_groups": [{k: _r(g.get(k)) for k in ("kind", "section", "z_in", "n", "Qu_rad", "CP", "b", "DC_CP", "DC_valid")} for g in (nl.get("deformation_groups") or [])][:24],
            "force_controlled_columns_worst": sorted([{k: _r(c.get(k)) for k in ("ele", "section", "z_in", "demand", "phiBRn", "KLr", "DC")} for c in (nl.get("force_controlled_columns") or [])],
                                                     key=lambda c: -(c.get("DC") or 0))[:8],
            "n_force_controlled_columns": len(nl.get("force_controlled_columns") or []),
            "modal": _r(nl.get("modal")), "component_params_verified": nl.get("component_params_verified"),
            "ch16_rules": {k: {"clause": v.get("clause"), "page": v.get("pdf_page"), "rule": v.get("rule")} for k, v in (nl.get("ch16") or {}).items()
                           if isinstance(v, dict) and v.get("clause")},
            "damping": _r((nl.get("ch16") or {}).get("damping")),
        }
        gm = nl.get("ground_motions") or {}
        if isinstance(gm, dict):
            ev["nlrha"]["ground_motions"] = {"target": gm.get("target") or gm.get("target_kind"), "n_selected": len(gm.get("selected") or []),
                                             "period_range_s": _r(gm.get("period_range") or gm.get("T_range")), "sf_range": _r([min(r.get("sf", 0) for r in recs), max(r.get("sf", 0) for r in recs)] if recs else None, 2)}
    # pushover
    po = _json(os.path.join(job, "pushover", "pushover_package.json"))
    if po:
        d = {}
        for k, x in (po.get("directions") or {}).items():
            acc = x.get("acceptance") or {}
            nsp = x.get("nsp") or {}
            d[k] = {"T1": _r(x.get("T1")), "meff_frac": _r(x.get("meff_frac")), "stop_reason": x.get("stop_reason"),
                    "tail": {kk: (x.get("tail") or {}).get(kk) for kk in ("status", "captured", "message")},
                    "p695": {kk: _r((x.get("p695") or {}).get(kk)) for kk in ("Vmax_kip", "V_design_kip", "Omega", "Omega0_design", "delta_u_in", "delta_u_basis", "delta_y_eff_in", "mu_T", "Vmax_over_W")},
                    "nsp": {lvl: {kk: _r(v.get(kk)) for kk in ("Te", "Vy", "Sa", "C0", "C1", "C2", "mu_strength", "mu_max", "nsp_permitted", "target_disp_in", "target_over_H", "reached_target")}
                            for lvl, v in nsp.items() if isinstance(v, dict)},
                    "acceptance": {lvl: {"roof_disp_in": _r(a.get("roof_disp_in")), "max_story_drift": _r(a.get("max_story_drift")), "worst_DC": _r(a.get("worst_DC")),
                                         "groups": [{kk: _r(g.get(kk)) for kk in ("kind", "section", "z_in", "n", "n_yielded", "theta_pl_max", "IO", "LS", "CP", "DC_IO", "DC_LS", "DC_CP")}
                                                    for g in sorted(a.get("groups") or [], key=lambda g: -(g.get("DC_CP") or 0))[:8]]}
                                   for lvl, a in acc.items() if isinstance(a, dict)}}
        ev["pushover"] = {"params_verified": po.get("params_verified"), "params_source": po.get("params_source"), "hinge_stats": po.get("hinge_stats"),
                          "numerics": po.get("numerics"), "directions": d}
    # DDM
    dd = _json(os.path.join(job, "ddm_results.json"))
    if dd:
        runs = []
        for r in dd.get("runs") or []:
            phi = r.get("phi") or {}
            cls = r.get("cls") or {}
            runs.append({"label": r.get("label"), "lambda_u": _r(r.get("lambda_u")), "check": _r(r.get("check")), "first_yield": _r(r.get("first_yield")) if not isinstance(r.get("first_yield"), dict) else _r({k: v for k, v in r["first_yield"].items() if k in ("lambda", "member", "kind")}),
                         "phi_s": phi.get("phi_s"), "phi_class": phi.get("cls"), "phi_status": phi.get("status"), "beta_T": phi.get("beta_T"),
                         "mechanism": (cls.get("mechanism") or "")[:160], "mechanism_class": cls.get("cls"), "ductile_post_peak": cls.get("ductile_post_peak"),
                         "n_hinges": len(cls.get("hinge_members") or []), "buckled_braces": len(cls.get("buckled_braces") or [])})
        gate = dd.get("gate") or {}
        ev["ddm"] = {"runs": runs, "gate": {"ok": gate.get("ok"), "rows": [{k: _r(x.get(k)) for k in ("quantity", "steltic", "gmnia", "ratio", "ok")} for x in (gate.get("rows") or [])]},
                     "options": {k: v for k, v in (dd.get("options") or {}).items() if isinstance(v, (int, float, str, bool))}}
    # the linear design
    cp = _json(os.path.join(job, "design", "calc_package.json"), {}) or {}
    members = [m for m in (cp.get("members") or []) if isinstance(m, dict) and isinstance(m.get("DC"), (int, float))]
    ev["design"] = {"building": cp.get("building"), "code": cp.get("code"),
                    "capacity_design": {k: (v if isinstance(v, str) else json.dumps(v)[:400]) for k, v in (cp.get("capacity_design") or {}).items()} if isinstance(cp.get("capacity_design"), dict) else cp.get("capacity_design"),
                    "worst_members": [{"id": m.get("id"), "section": (m.get("inputs") or {}).get("section"), "DC": _r(m.get("DC")), "limit_state": m.get("limit_state"),
                                       "combo": (m.get("inputs") or {}).get("governing_combo")} for m in sorted(members, key=lambda m: -m["DC"])[:10]],
                    "n_members_checked": len(members), "framework_screen": cp.get("framework_screen")}
    try:
        from . import compare as C
        st = C.steltic_facts(job)
        ev["design"]["drift"] = {"limit_pct": st.get("drift_limit_pct"), "X_pct": st.get("drift_X"), "Y_pct": st.get("drift_Y")}
        ev["design"]["base_shear"] = {"V_kip": st.get("V_kip"), "W_kip": st.get("W_kip"), "Cs": st.get("Cs"), "wind_X_kip": st.get("wind_X"), "wind_Y_kip": st.get("wind_Y")}
    except Exception:                                            # noqa: BLE001
        pass
    cfg = os.path.join(job, "cfg.py")
    if os.path.exists(cfg):
        txt = open(cfg, encoding="utf-8", errors="replace").read()
        ev["design"]["cfg_seismic"] = {k: v for k, v in re.findall(r"^\s*(SDS|SD1|S1|R|Cd|Om0|Ie|system|site_class|risk_category|drift_limit|drift_relief_16_1_2)\s*=\s*([^\n#]+)", txt, re.M)}
    # the 16.1.4 draft and the four-analyses readings
    ev["design_criteria_draft"] = _text_of_html(os.path.join(job, "design_criteria_16_1_4.html"), 5000)
    fa = _text_of_html(os.path.join(job, "four_analyses.html"), 12000)
    m = re.search(r"Disclosures read from the outputs(.*?)Files:", fa)
    ev["four_analyses_disclosures"] = m.group(1).strip()[:2500] if m else ""
    # feedback loops already run
    fb = os.path.join(job, "feedback")
    loops = []
    if os.path.isdir(fb):
        for d in sorted(os.listdir(fb)):
            st = _json(os.path.join(fb, d, "state.json"))
            if st:
                loops.append({"id": d, "kind": st.get("kind"), "status": st.get("status"), "passed": st.get("passed"), "title": st.get("title"),
                              "verdict": ((st.get("comparison") or {}).get("verdict_text") or "")[:300], "promoted_at": st.get("promoted_at")})
    ev["feedback_loops"] = loops
    return ev


# ---------------------------------------------------------------- events
class Emitter:
    """One JSON event per line on stdout (what the hub relays), plain lines for the log."""
    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.cum_in = self.cum_out = 0

    def event(self, **ev):
        self.out.write(json.dumps(ev, ensure_ascii=False) + "\n"); self.out.flush()

    def log(self, text):
        self.out.write(str(text).replace("\n", " ") + "\n"); self.out.flush()

    def usage(self, u):
        if not isinstance(u, dict):
            return
        i, o = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        self.cum_in += i; self.cum_out += o
        self.event(type="usage", last_in=i, last_out=o, cum_in=self.cum_in, cum_out=self.cum_out)


# ---------------------------------------------------------------- the review
def _evidence_message(ev: dict, focus: str) -> str:
    txt = json.dumps(ev, indent=None, ensure_ascii=False, default=str)
    return ("EVIDENCE (the run's measured results, JSON):\n" + txt + "\n\n"
            + ("FOCUS (the engineer's question -- address it first, in the Verdict and in section 7):\n" + focus.strip() + "\n\n" if focus and focus.strip() else "")
            + "Write the review now.")


def _tool_title(args: dict) -> str:
    q = (args.get("query") or "").strip()
    kind = (args.get("type") or "").strip().lower()
    how = {"exact_section": "\u00a7", "exact_equation": "eq ", "exact_table": "table ", "id": "id "}.get(kind, "")
    return "%s%s: %s%s" % (args.get("document") or "?", (" " + args["clause"]) if args.get("clause") else "", how, q[:90])


def run(job: str, focus: str = "", use_standards: bool = True, max_searches: int = 0, emit: Emitter | None = None, conn: dict | None = None) -> dict:
    """The whole step. Returns {"ok", "review_md", "paths", "searches", "usage"}."""
    em = emit or Emitter()
    job = os.path.abspath(job)
    conn = conn or llm.connection()
    t0 = time.time()
    em.event(type="status", text="reading what the run measured")
    ev = gather(job)
    have = [k for k in ("nlrha", "pushover", "ddm") if k in ev]
    if not have and not ev.get("summary"):
        em.event(type="error", text="nothing to review: no snl_summary.json, nlrha/, pushover/ or ddm_results.json in %s -- run the analyses first" % job)
        return {"ok": False, "review_md": "", "paths": {}, "searches": [], "usage": {}}
    em.event(type="milestone", text="evidence: %s%s" % (", ".join(have) or "summary only", "" if use_standards and rag.configured() else
                                                         (" -- standards search off" if not use_standards else " -- no standards server (RAG_API_URL empty): clauses will be cited from memory, marked UNVERIFIED")))
    em.log("evidence gathered from %s: %d kB, analyses: %s" % (os.path.basename(job), len(json.dumps(ev, default=str)) // 1000, ", ".join(have) or "none"))
    searches: list = []
    # `budget` is None when the review may search as often as the work needs (the default), an int when
    # the engineer capped it, and 0 when there is nothing to search -- then the tool is not offered at all
    # and the review says so. A cap is a cost control, never a quality target.
    searching = use_standards and rag.configured()
    limit = max(0, int(max_searches or 0))
    budget = (limit or None) if searching else 0
    corpus_line = ""
    if searching:
        # what the corpus actually holds, before the first search: the model is told, and a search for a
        # document that is not here is answered as a gap without spending the budget
        st = rag.status(force=True)
        cm = rag.corpus_map(st)
        corpus_line = rag.describe_corpus(cm)
        if not st.get("ok") and st.get("note"):
            em.event(type="warning", text="standards server: %s -- searches may fail; clauses then come from memory, marked UNVERIFIED" % st["note"])
        em.event(type="milestone", text="standards corpus -- " + corpus_line)
        if cm.get("known") and cm.get("absent"):
            em.log("standards corpus: %s absent on this PC -- the model is told not to search %s; convert on the Query file manager's Convert tab (stems %s), then Rebuild index"
                   % (", ".join(rag.TITLES[k] for k in cm["absent"]), "them" if len(cm["absent"]) > 1 else "it", ", ".join(rag.STEMS[k] for k in cm["absent"])))
    spent = {"n": 0}                                             # searches that reached a document the corpus holds

    def do_search(args: dict) -> str:
        n = len(searches) + 1
        em.event(type="tool", name="search_engineering_standards", step=n, title=_tool_title(args))
        if budget is not None and spent["n"] >= budget:
            res = {"ok": False, "results": [], "counted": False, "via": "", "note": "search budget of %d used up -- cite the remaining clauses from memory, marked UNVERIFIED" % budget, "ms": 0}
        else:
            res = rag.search(args.get("query") or "", args.get("document") or args.get("doc") or "ASCE7",
                             int(args.get("top_k") or 5), args.get("clause") or "", args.get("chapter") or "",
                             qtype=args.get("type") or "", want_commentary=bool(args.get("want_commentary")),
                             neighbors=args.get("context_neighbors"))
            if res.get("counted", True):
                spent["n"] += 1
        searches.append({"n": n, "args": args, "hits": len(res.get("results") or []), "note": res.get("note"), "ms": res.get("ms"),
                         "via": res.get("via"), "counted": bool(res.get("counted", True)), "missing_document": res.get("missing_document"),
                         "attempts": res.get("attempts"),
                         "results": [{k: h.get(k) for k in ("source", "section", "title", "page", "score")} for h in (res.get("results") or [])],
                         "passages": [h.get("text") for h in (res.get("results") or [])]})
        nres = len(res.get("results") or [])
        summ = "%d passage(s)" % nres
        if res.get("via") and res["via"] not in ("", "as-asked"):
            summ += " via " + str(res["via"]).split(" (")[0]
        if res.get("missing_document"):
            summ += " -- %s is not in the corpus (not counted)" % res["missing_document"]
        elif res.get("note") and (not nres or res.get("via") == "any-document"):
            summ += (" -- " + str(res["note"]))[:160]
        em.event(type="tool_result", step=n, summary=summ, ms=res.get("ms") or 0)
        return rag.render(res)

    if conn.get("mock"):
        em.event(type="status", text="model MOCK: writing the review from the evidence alone")
        md = mock_review(ev, focus, do_search if searching else None)
        usage = {}
    else:
        em.event(type="status", text="asking %s (%s)" % (conn["model"], "standards search off" if not searching
                 else ("up to %d standards searches" % budget) if budget else "standards searches as needed"))
        sys_msg = SYSTEM
        if searching:
            sys_msg += "\nDOCUMENTS IN THE CORPUS -- " + corpus_line + (
                "\nYou may make at most %d searches (a search for an ABSENT document is not counted, and is wasted)." % budget
                if budget else
                "\nThere is no limit on the number of searches: look up every clause, table and equation you cite. "
                "A search for an ABSENT document is not counted, and is wasted.")
        else:
            sys_msg += "\nThe standards search is unavailable for this run: cite from memory and mark every clause (UNVERIFIED)."
        messages = [{"role": "system", "content": sys_msg},
                    {"role": "user", "content": _evidence_message(ev, focus)}]
        md = ""
        usage = {}
        # one turn per search, plus a few to write the review; the ceiling only stops a runaway loop
        max_turns = MAX_TURNS if budget is None else min(MAX_TURNS, budget + 6)
        for turn in range(max_turns):
            def on_piece(kind, text):
                em.event(type=kind, text=text)
            try:
                out = llm.chat_with_retry(conn, messages, TOOLS if searching else None, on_piece)
            except llm.LLMError as e:
                em.event(type="error", text=str(e))
                return {"ok": False, "review_md": "", "paths": {}, "searches": searches, "usage": usage}
            if out.get("usage"):
                em.usage(out["usage"]); usage = out["usage"]
            if out["tool_calls"]:
                messages.append({"role": "assistant", "content": out["content"] or None, "tool_calls": out["tool_calls"]})
                for tc in out["tool_calls"]:
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {"query": tc["function"]["arguments"], "document": "ASCE7"}
                    if tc["function"]["name"] != "search_engineering_standards":
                        result = "unknown tool %s" % tc["function"]["name"]
                    else:
                        result = do_search(args if isinstance(args, dict) else {})
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                continue
            md = (out["content"] or "").strip()
            if md:
                break
            messages.append({"role": "assistant", "content": ""})
            messages.append({"role": "user", "content": "Your reply was empty. Write the review now, in the eight sections."})
        if not md:
            em.event(type="error", text="the model did not produce the review after %d turns" % max_turns)
            return {"ok": False, "review_md": "", "paths": {}, "searches": searches, "usage": usage}
    paths = write_outputs(job, md, ev, searches, conn, focus, time.time() - t0)
    em.event(type="milestone", text="review written: review.md, review.html (%d standards searches, %d s)" % (len(searches), int(time.time() - t0)))
    em.log(">> review: " + paths["md"])
    return {"ok": True, "review_md": md, "paths": paths, "searches": searches, "usage": usage}


# ---------------------------------------------------------------- MOCK: the review from the evidence alone
def _pct(x, n=2):
    return "%.*f%%" % (n, 100 * x) if isinstance(x, (int, float)) else "not in the run"


def _f(x, n=2):
    return ("%.*f" % (n, x)) if isinstance(x, (int, float)) else "not in the run"


def mock_review(ev: dict, focus: str = "", search=None) -> str:
    """A deterministic review from the evidence (model MOCK). The searches still run, so the offline
    pipeline exercises the standards server the way a real run does."""
    s = ev.get("summary") or {}
    nl, po, dd, de = ev.get("nlrha") or {}, ev.get("pushover") or {}, ev.get("ddm") or {}, ev.get("design") or {}
    cite = {}
    if search:
        for key, args in (("16.4.1.2", {"query": "mean story drift ratio limit two times Table 12.12-1", "document": "ASCE7", "clause": "16.4.1.2"}),
                          ("16.4.1.1", {"query": "unacceptable response number of ground motions", "document": "ASCE7", "clause": "16.4.1.1"}),
                          ("16.4.2.2", {"query": "force-controlled actions gamma 1.3 Eq. 16.4-1", "document": "ASCE7", "clause": "16.4.2.2"})):
            txt = search(args)
            m = re.search(r"\[1\] (\S+) (\S+)(?: p\. (\S+))?", txt)
            grounded = "NO PASSAGES" not in txt and "CORPUS GAP" not in txt and "(answered by: any-document" not in txt
            cite[key] = ("[ASCE 7-22 §%s%s]" % (key, (", p. " + m.group(3)) if m and m.group(3) else "")) if grounded else "[ASCE 7-22 §%s] (UNVERIFIED)" % key
    else:
        cite = {k: "[ASCE 7-22 §%s] (UNVERIFIED)" % k for k in ("16.4.1.2", "16.4.1.1", "16.4.2.2")}
    v = (nl.get("verdict") or {}); L = nl.get("limits") or {}
    sn, sp, sd = s.get("nlrha") or {}, s.get("pushover") or {}, s.get("ddm") or {}
    lines = ["# Review of %s — nonlinear analyses (model MOCK)" % ev.get("job"), ""]
    if focus.strip():
        lines += ["**Focus asked:** " + focus.strip(), ""]
    lines += ["## 1. Verdict", ""]
    if sn:
        lines.append("Chapter 16: **%s** — %d unacceptable of %d records (allowed %s); mean storey drift %s against %s %s." % (
            sn.get("verdict"), sn.get("n_unacceptable") or 0, sn.get("n_records") or 0, v.get("unacceptable_allowed", "not in the run"), _pct(sn.get("mean_drift_max")), _pct(sn.get("mean_limit")), cite["16.4.1.2"]))
    else:
        lines.append("Chapter 16: not in the run.")
    if sp:
        lines.append("Pushover: Ω = %s (Ω₀ = %s); BPON %s — %s; NSP %s." % ("/".join(_f(x, 1) for x in (sp.get("Omega") or {}).values()), _f((s.get("basis") or {}).get("Om0"), 1),
                                                                            "/".join(sp.get("bpon_levels") or []), "both pass" if sp.get("bpon_ok") else "NOT satisfied", "permitted" if sp.get("nsp_permitted") else "not permitted"))
    if sd:
        lines.append("DDM: governing %s, λᵤ = %s, φₛλᵤ = %s, %s of %s combinations pass, transfer gate %s." % (sd.get("governing"), _f(sd.get("lambda_u")), _f(sd.get("phi_lambda")), sd.get("n_pass"), sd.get("n_checked"), "ok" if sd.get("gate_ok") else "FAILED"))
    lines += ["", "## 2. What the run measured", ""]
    st = s.get("steltic") or {}
    lines.append("- Linear design: governing D/C %s; design drift %s of the %s limit; V = %s kip, W = %s kip." % (_f(st.get("dc_max")), _f(st.get("drift_max_pct")), _f(st.get("drift_limit_pct")), _f(st.get("V_kip"), 0), _f(st.get("W_kip"), 0)))
    for r in nl.get("story") or []:
        lines.append("- Storey %s: mean drift X %s%% / Y %s%%, record max X %s%% / Y %s%% — %s." % (r.get("story"), r.get("mean_X_pct"), r.get("mean_Y_pct"), r.get("max_X_pct"), r.get("max_Y_pct"), "ok" if r.get("ok") else "OVER"))
    lines += ["", "## 3. Chapter 16 acceptance", ""]
    if nl:
        lines.append("- Mean storey drift %s vs %s (2 × %s, Risk Category %s) — %s %s" % (_pct(v.get("mean_drift_max")), _pct(L.get("mean_limit")), _pct(L.get("table_12_12_1"), 1), L.get("risk_category"), "OK" if v.get("mean_drift_ok") else "NOT OK", cite["16.4.1.2"]))
        lines.append("- Unacceptable responses %s of %s (allowed %s) — %s %s" % (v.get("n_unacceptable"), v.get("n_records"), v.get("unacceptable_allowed"), "OK" if v.get("unacceptable_ok") else "NOT OK", cite["16.4.1.1"]))
        lines.append("- Deformation-controlled actions: worst CP D/C %s — %s; valid range %s" % (_f(sn.get("worst_DC_CP")), "OK" if v.get("deformation_ok") else "NOT OK", "OK" if v.get("valid_range_ok") else "NOT OK"))
        lines.append("- Force-controlled columns: worst D/C %s — %s %s" % (_f(sn.get("worst_DC_force_controlled")), "OK" if v.get("force_controlled_ok") else "NOT OK", cite["16.4.2.2"]))
        lines.append("- Residual drift: %s" % ("not applicable at this height" if not v.get("residual_applicable") else ("OK" if v.get("residual_ok") else "NOT OK")))
    else:
        lines.append("Not in the run.")
    lines += ["", "## 4. Pushover", ""]
    for k, d in (po.get("directions") or {}).items():
        p6, t = d.get("p695") or {}, d.get("tail") or {}
        lines.append("- %s: T₁ %s s, Vmax %s kip (%s × V), Ω %s, μ_T %s (%s); descending branch %s." % (k, _f(d.get("T1")), _f(p6.get("Vmax_kip"), 0), _f((p6.get("Vmax_kip") or 0) / (p6.get("V_design_kip") or 1), 2), _f(p6.get("Omega"), 2), _f(p6.get("mu_T"), 2), p6.get("delta_u_basis") or "", t.get("status")))
    if not po:
        lines.append("Not in the run.")
    lines += ["", "## 5. DDM system capacity", ""]
    for r in (dd.get("runs") or [])[:12]:
        lines.append("- %s: λᵤ %s, φₛ %s (%s, %s) → %s; %s." % (r.get("label"), _f(r.get("lambda_u")), _f(r.get("phi_s")), r.get("phi_class"), r.get("phi_status"), r.get("check"), r.get("mechanism")))
    if not dd:
        lines.append("Not in the run.")
    lines += ["", "## 6. Design basis and the design criteria draft", ""]
    lines.append("- cfg.py: " + ", ".join("%s = %s" % kv for kv in (de.get("cfg_seismic") or {}).items()) if de.get("cfg_seismic") else "- cfg.py not read")
    lines.append("- §16.1.4 draft %s." % ("present" if ev.get("design_criteria_draft") else "not in the run"))
    lines += ["", "## 7. What to change, and why", ""]
    if nl and v.get("overall") and sp.get("bpon_ok", True) and (sd.get("gate_ok", True)):
        lines.append("Nothing is required by the checks that ran: every Chapter 16 criterion passes with the margins above. The candidates for a lighter design are the groups whose deformation D/C is far below 1.0 (see the deformation groups in the evidence) — a resize loop in the Feedback tab quantifies that.")
    else:
        lines.append("Address the failing check first (see sections 3–5); the Feedback tab's loops are the mechanised path back to HR Steel.")
    lines += ["", "## 8. Open items and verification", ""]
    if po and not po.get("params_verified"):
        lines.append("- Component parameters are UNVERIFIED placeholders (pushover and NLRHA): fill hinge_params.json from AISC 342-22 / ASCE 41-23 before anyone relies on these results.")
    for r in (nl.get("records") or []):
        if r.get("retry"):
            lines.append("- Record %s was re-run at a finer step (%s)." % (r.get("label"), "converged" if r.get("converged") else "still non-converged"))
    if ev.get("four_analyses_disclosures"):
        lines.append("- Disclosures from the four-analyses sheet: " + ev["four_analyses_disclosures"][:600])
    lines.append("- This review was written offline (model MOCK) from the evidence; clauses marked UNVERIFIED were not read from the corpus.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- outputs
_CSS = """body{font:15px/1.5 -apple-system,'Segoe UI',system-ui,sans-serif;color:#1d1d1b;max-width:900px;margin:32px auto;padding:0 20px;background:#fbfaf7}
h1{font-size:24px;border-bottom:2px solid #ff8a3d;padding-bottom:6px}h2{font-size:18px;margin-top:28px;color:#8a3d00}
code{background:#f1ede6;padding:1px 4px;border-radius:3px;font-size:13px}pre{background:#f1ede6;padding:10px;overflow:auto}
table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #d9d2c6;padding:4px 8px;font-size:13.5px}th{background:#f1ede6}
.meta{color:#6b655c;font-size:12.5px;margin-bottom:18px}.disc{margin-top:36px;padding:10px 12px;border:1px solid #e0b341;background:#fff8e6;font-size:12.5px}
.src{font-size:12px;color:#6b655c}"""


def _md_to_html(md: str) -> str:
    """Enough Markdown for a review: headings, lists, bold/italic/code, paragraphs, pipe tables."""
    out, para, inlist, intable = [], [], False, False

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
        return t

    def flush():
        nonlocal para, inlist, intable
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>"); para = []
        if inlist:
            out.append("</ul>"); inlist = False
        if intable:
            out.append("</table>"); intable = False
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush(); continue
        m = re.match(r"^(#{1,4})\s+(.*)", s)
        if m:
            flush(); out.append("<h%d>%s</h%d>" % (len(m.group(1)), inline(m.group(2)), len(m.group(1)))); continue
        if s.lstrip().startswith("|"):
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if not intable:
                if para or inlist:
                    flush()
                out.append("<table>"); intable = True
                out.append("<tr>" + "".join("<th>%s</th>" % inline(c) for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in cells) + "</tr>")
            continue
        m = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.*)", s)
        if m:
            if intable or para:
                flush()
            if not inlist:
                out.append("<ul>"); inlist = True
            out.append("<li>" + inline(m.group(1)) + "</li>"); continue
        if inlist or intable:
            flush()
        para.append(s.strip())
    flush()
    return "\n".join(out)


def write_outputs(job: str, md: str, ev: dict, searches: list, conn: dict, focus: str, seconds: float) -> dict:
    md_path = os.path.join(job, "review.md")
    open(md_path, "w", encoding="utf-8").write(md if md.endswith("\n") else md + "\n")
    date = datetime.date.today().isoformat()
    model = "MOCK (offline)" if conn.get("mock") else conn.get("model")
    doc = ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>Review — %s</title><style>%s</style></head><body>"
           "<div class=\"meta\">Steltic_nonlinear · Review of <b>%s</b> · %s · model %s · %d standards searches · %d s</div>%s"
           "<div class=\"disc\">Written by a language model from the run's measured results (see review_transcript.json for the evidence and every passage it read). "
           "Not for construction. Every statement requires review by the engineer of record; a clause marked UNVERIFIED was cited from the model's memory, not from the corpus.</div>"
           "<p class=\"src\">Files: review.md · review_transcript.json · four_analyses.html · nlrha/nlrha_report.html · pushover/pushover_report.html · ddm_report.html · design_criteria_16_1_4.html</p>"
           "</body></html>") % (html.escape(ev.get("job") or ""), _CSS, html.escape(ev.get("job") or ""), date, html.escape(str(model)), len(searches), int(seconds), _md_to_html(md))
    html_path = os.path.join(job, "review.html")
    open(html_path, "w", encoding="utf-8").write(doc)
    tr_path = os.path.join(job, "review_transcript.json")
    json.dump({"job": ev.get("job"), "date": date, "model": model, "focus": focus, "seconds": round(seconds, 1), "searches": searches, "evidence": ev},
              open(tr_path, "w", encoding="utf-8"), indent=1, default=str)
    return {"md": md_path, "html": html_path, "transcript": tr_path}


# ---------------------------------------------------------------- CLI
def cmd_review(a) -> int:
    em = Emitter()
    r = run(a.job, focus=a.focus or "", use_standards=not a.no_standards, max_searches=a.max_searches, emit=em)
    return 0 if r["ok"] else 1
