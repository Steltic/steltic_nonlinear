"""`snl collect` -- the component parameters are read out of the standards BEFORE the analyses run.

The repository hinge_params.json is a placeholder written from memory, and its own _README says what
must happen instead: retrieve the tables, copy the printed values in, set verified=true, cite. Trying
to do that AFTER the run (Revise) could not work -- the run had already used the placeholders, and
re-running to "pick the citation up" put the placeholders straight back. So the order is: collect,
then run. These tests pin the contract the hub's Run analyses gate depends on.
"""
import io
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snl import collect, rag                       # noqa: E402
from test_review import FakeRAG, _Server, _rag_env  # noqa: E402

EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")


def _job(tmp_path):
    job = tmp_path / "Ex22"
    shutil.copytree(EX22, job)
    (job / "pushover" / "hinge_params_used.json").unlink()      # a fresh project: nothing collected yet
    return str(job)


def _events(buf):
    return [json.loads(l) for l in buf.getvalue().splitlines() if l.startswith("{")]


# ---------------------------------------------------------------- what the building needs
def test_gather_reads_the_building_and_nothing_else():
    f = collect.gather(EX22)
    assert f["system"] == "SMF" and f["rbs"] and f["moment_frame"] and not f["braced"]
    assert "W14X730" in f["columns"] and "W36X232" in f["beams"]
    assert f["beams"]["W36X232"]["Lb_over_ry"] == pytest.approx(55.1, abs=0.2)   # from the package, not typed
    assert f["needed"] == ["material", "beam_flexure", "column_flexure"]         # an SMF has no braces to collect


# ---------------------------------------------------------------- the step, offline
def test_collect_writes_a_verified_file_the_engines_accept(tmp_path):
    job = _job(tmp_path)
    FakeRAG.queries.clear()
    R = _Server(FakeRAG)
    try:
        _rag_env(R)
        os.environ["STELTIC_LLM_MODEL"] = "MOCK"; os.environ.pop("STELTIC_LLM_BASE_URL", None)
        buf = io.StringIO()
        r = collect.run(job, emit=collect.Emitter(buf))
        assert r["ok"] and r["verified"] and r["missing"] == []
        assert os.path.basename(r["path"]) == collect.OUT_NAME
        d = json.load(open(r["path"], encoding="utf-8"))
        assert d["verified"] is True
        for g in ("material", "beam_flexure", "column_flexure"):
            assert "Table" in d[g]["source"] and ("p." in d[g]["source"] or "pdf" in d[g]["source"]), g
        # the untouched groups keep the repository values
        tpl = json.load(open(os.path.join(ROOT, "pushover", "hinge_params.json"), encoding="utf-8"))
        assert d["nsp"] == tpl["nsp"] and d["numerics"] == tpl["numerics"]
        # the engines build hinges from it
        from pushover import hinge_models as HM
        prm = HM.load_params(r["path"])
        b = HM.beam_hinge("W36X232", 360.0, prm)
        assert 0 < b.IO < b.LS < b.CP
        # and the paper trail exists for the 16.1.4 document
        assert os.path.exists(os.path.join(job, collect.EVIDENCE_NAME))
        assert os.path.exists(os.path.join(job, "retrieval_log.md"))
        ev = json.load(open(os.path.join(job, collect.EVIDENCE_NAME), encoding="utf-8"))
        assert ev["verified"] and ev["needed"] == ["material", "beam_flexure", "column_flexure"] and len(ev["searches"]) >= 2
        types = [e["type"] for e in _events(buf)]
        assert "tool" in types and "tool_result" in types and types[-1] == "milestone"
    finally:
        R.close(); os.environ.pop("RAG_API_URL", None); os.environ.pop("STELTIC_LLM_MODEL", None); rag._status_cache = None


def test_no_standards_server_collects_nothing_and_says_so(tmp_path):
    job = _job(tmp_path)
    os.environ.pop("RAG_API_URL", None); os.environ["STELTIC_LLM_MODEL"] = "MOCK"; rag._status_cache = None
    try:
        buf = io.StringIO()
        r = collect.run(job, emit=collect.Emitter(buf))
        assert not r["ok"] and r["missing"] == ["material", "beam_flexure", "column_flexure"]
        assert not os.path.exists(os.path.join(job, collect.OUT_NAME))            # the gate stays closed
        assert any(e["type"] == "error" and "RAG_API_URL" in e["text"] for e in _events(buf))
    finally:
        os.environ.pop("STELTIC_LLM_MODEL", None)


def test_a_group_the_corpus_cannot_answer_keeps_the_gate_closed(tmp_path, monkeypatch):
    """A braced frame needs brace_axial; the MOCK (like a corpus without the table) has nothing for it."""
    job = _job(tmp_path)
    real = collect.gather
    monkeypatch.setattr(collect, "gather", lambda j: dict(real(j), braced=True,
                                                          needed=["material", "beam_flexure", "column_flexure", "brace_axial"]))
    R = _Server(FakeRAG)
    try:
        _rag_env(R)
        os.environ["STELTIC_LLM_MODEL"] = "MOCK"; os.environ.pop("STELTIC_LLM_BASE_URL", None)
        r = collect.run(job, emit=collect.Emitter(io.StringIO()))
        assert not r["ok"] and r["missing"] == ["brace_axial"]
        assert os.path.basename(r["path"]) == collect.PARTIAL_NAME
        assert not os.path.exists(os.path.join(job, collect.OUT_NAME))
        d = json.load(open(r["path"], encoding="utf-8"))
        assert d["verified"] is False and "brace_axial" in d["source"] and "MISSING" in d["source"]
    finally:
        R.close(); os.environ.pop("RAG_API_URL", None); os.environ.pop("STELTIC_LLM_MODEL", None); rag._status_cache = None


# ---------------------------------------------------------------- validation: what a model may not hand back
GOOD = json.load(open(os.path.join(EX22, "pushover", "hinge_params_used.json"), encoding="utf-8"))
for _g in ("material", "beam_flexure", "column_flexure"):
    GOOD[_g]["source"] = "AISC 342-22 Table C3.6, p. 46"


def _bad(**over):
    d = json.loads(json.dumps(GOOD))
    for path, v in over.items():
        keys = path.split("."); cur = d
        for k in keys[:-1]:
            cur = cur[k]
        if v is None:
            cur.pop(keys[-1], None)
        else:
            cur[keys[-1]] = v
    return d


def test_validation_passes_the_corpus_read_example():
    ok, probs = collect.validate(GOOD, ["material", "beam_flexure", "column_flexure"])
    assert ok, probs


@pytest.mark.parametrize("over, group", [
    ({"column_flexure.source": "from memory"}, "column_flexure"),                # no table id, no page
    ({"column_flexure.a_expr": "5.5*(h/tw)**-0.95*(L/ry)**-0.5*(1-PG/Pye)**2.4 + __import__('os')"}, "column_flexure"),
    ({"column_flexure.a_expr": "5.5*(h/tw)**-0.95*(L/ry)**-0.5*(1-PG/Pyee)**2.4"}, "column_flexure"),  # unknown symbol
    ({"column_flexure.LS_frac_of_b": 1.5}, "column_flexure"),                     # a fraction above 1
    ({"column_flexure.b_max": None}, "column_flexure"),
    ({"beam_flexure.c_residual": 0.0}, "beam_flexure"),
    ({"beam_flexure.b_abs": -0.07}, "beam_flexure"),
    ({"material.Ry_expected": 0.5}, "material"),
    ({"material": None}, "material"),
])
def test_validation_refuses_what_it_should(over, group):
    ok, probs = collect.validate(_bad(**over), ["material", "beam_flexure", "column_flexure"])
    assert not ok and probs[group], probs


def test_validation_orders_the_acceptance_criteria():
    d = _bad(**{"beam_flexure.mode": "member", "beam_flexure.a_over_thetay": 9, "beam_flexure.b_over_thetay": 11,
                "beam_flexure.IO_over_thetay": 6, "beam_flexure.LS_over_thetay": 1, "beam_flexure.CP_over_thetay": 8})
    ok, probs = collect.validate(d, ["beam_flexure"])
    assert not ok and any("IO <= LS <= CP" in p for p in probs["beam_flexure"])


def test_the_json_the_model_returns_may_be_fenced_or_wrapped():
    assert collect._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert collect._parse_json('Here it is:\n{"a": {"b": 2}} thanks') == {"a": {"b": 2}}
    assert collect._parse_json("no json here") is None


# ---------------------------------------------------------------- the run picks the collected file up
def test_snl_run_uses_the_collected_file_by_default(tmp_path, monkeypatch, capsys):
    from snl import cli
    job = _job(tmp_path)
    open(os.path.join(job, collect.OUT_NAME), "w").write(json.dumps(GOOD))
    seen = {}
    monkeypatch.setattr(cli, "_run", lambda cmd, log, env=None, cwd=None: (seen.setdefault("cmds", []).append(cmd), dict(returncode=0, seconds=0, log=log))[1])
    monkeypatch.setattr(cli, "_unpack", lambda p, out: job)
    import types
    a = types.SimpleNamespace(package=job, out=None, steltic_engine=None, only=["pushover"], skip=[], params=None,
                              site_class="D", tail=None, post_cap_ratio=None, parallel=1, dt=0.01, integrator="hht",
                              n_records=11, target=None, records_set=None, pulse_fraction=None, sf_bounds=None,
                              plasticity=None, member_nseg=None, no_block=True, no_criteria=True, risk_category=None,
                              project=None, engineer=None, reviewer=None)
    try:
        cli.cmd_run(a)
    except Exception:
        pass                                                        # later steps of cmd_run are not under test
    out = capsys.readouterr().out
    assert "component parameters: " + os.path.join(job, collect.OUT_NAME) in out and "(collected from the corpus)" in out
    if seen.get("cmds"):
        assert "--params" in seen["cmds"][0] and seen["cmds"][0][seen["cmds"][0].index("--params") + 1] == os.path.join(job, collect.OUT_NAME)




# ---------------------------------------------------------------- transcription, against the real converted cells
from http.server import BaseHTTPRequestHandler   # noqa: E402
from test_review import _sse, _env               # noqa: E402

FX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "collect_tables.json"), encoding="utf-8"))
RBS_ROW = next(l for l in FX["C5.5"].split("\n") if l.startswith("| RBS moment connection"))
RBS_CELLS = [x.strip() for x in RBS_ROW.strip("|").split("|")]
FN_E = next(l for l in FX["C5.5"].split("\n") if "X _ { 2 } = 0 . 5 5" in l).strip()
C36_ROW = next(l for l in FX["C3.6"].split("\n") if l.startswith("| 1. Highly ductile"))
C36_A = [x.strip() for x in C36_ROW.strip("|").split("|")][0]
A992_ROW = next(l for l in FX["A3.2"].split("\n") if "A992" in l)


class TableRAG(BaseHTTPRequestHandler):
    """The Query file manager answering exact-table lookups with the cells exactly as its converter left them."""
    queries = []
    docs = ["ASCE7", "ASCE_41_23", "AISC_342_22", "AISC_341_22"]

    def log_message(self, *a):
        pass

    def do_GET(self):
        out = json.dumps({"ok": True, "spec_index": True, "indexed_docs": TableRAG.docs, "converted": TableRAG.docs}).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        TableRAG.queries.append(body)
        q = (body.get("query") or "").strip()
        hits = []
        for tid, doc, sec, page in (("C5.5", "AISC_342_22", "C5.4b", "110"), ("C3.6", "AISC_342_22", "C3.4a", "78"), ("A3.2", "AISC_341_22", "A3.2", "60")):
            if (body.get("type") == "exact_table" and q == tid) or (body.get("clause") == tid):
                hits.append({"text": FX[tid], "doc": doc, "section_id": sec, "title": "Table " + tid, "printed_label": page, "score": 20, "authoritative": True})
        out = json.dumps({"results": hits, "collection": body.get("collection", ""), "count": len(hits), "matched": "exact_table" if hits else ""}).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


class ScriptedLLM(BaseHTTPRequestHandler):
    """Answers each call with the next item of `script` (a dict -> JSON reply). Records every request."""
    calls = []
    script = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        ScriptedLLM.calls.append(body)
        ans = ScriptedLLM.script.pop(0) if ScriptedLLM.script else {}
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        w = self.wfile
        _sse(w, {"choices": [{"delta": {"reasoning": "copying the cells"}}]})
        _sse(w, {"choices": [{"delta": {"content": json.dumps(ans)}}]})
        _sse(w, {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 500, "completion_tokens": 80}})
        w.write(b"data: [DONE]\n\n"); w.flush()


def _q(v, quote):
    return {"value": v, "quote": quote}


GOOD_MATERIAL = {"Ry_expected": _q(1.1, A992_ROW.strip())}
GOOD_BEAM = {"a_expr": _q("0.55*(h/tw)**-0.5*(bf/(2*tf))**-0.7*(Lb/ry)**-0.5*(L/d)**0.8", FN_E),
             "a_max": _q(0.07, RBS_CELLS[1]), "b_abs": _q(0.07, RBS_CELLS[2]), "c_residual": _q(0.3, RBS_CELLS[3]),
             "IO_frac_of_a": _q(0.5, RBS_CELLS[4]), "LS_frac_of_b": _q(0.75, RBS_CELLS[5]), "CP_frac_of_b": _q(1.0, RBS_CELLS[6])}
GOOD_COLUMN = {"a_expr": _q("5.5*(h/tw)**-0.95*(L/ry)**-0.5*(1-PG/Pye)**2.4", C36_A), "a_max": _q(0.07, C36_A),
               "b_expr": _q("20*(h/tw)**-0.9*(L/ry)**-0.5*(1-PG/Pye)**3.4", C36_ROW), "b_max": _q(0.07, C36_ROW),
               "c_expr": _q("0.4-0.4*PG/Pye", C36_ROW),
               "IO_frac_of_a": _q(0.5, "0.5 a"), "LS_frac_of_b": _q(0.75, "0.75 b"), "CP_frac_of_b": _q(1.0, "b")}


def _live(tmp_path, script):
    job = _job(tmp_path)
    TableRAG.queries.clear(); ScriptedLLM.calls.clear(); ScriptedLLM.script = list(script)
    R = _Server(TableRAG); L = _Server(ScriptedLLM)
    old = dict(os.environ)
    os.environ.update(_env(L.url, R.url + "/query")); rag._status_cache = None
    return job, R, L, old


def _done(R, L, old):
    R.close(); L.close(); os.environ.clear(); os.environ.update(old); rag._status_cache = None


def test_the_model_only_transcribes_and_every_value_is_backed_by_a_cell(tmp_path):
    """The real converted cells of Table C5.5 (RBS row + footnote [e]), Table C3.6 and Table A3.2, and a
    model that copies them: a verified file, no searching by the model, reasoning low."""
    job, R, L, old = _live(tmp_path, [GOOD_MATERIAL, GOOD_BEAM, GOOD_COLUMN])
    try:
        buf = io.StringIO()
        r = collect.run(job, emit=collect.Emitter(buf))
        assert r["ok"] and r["verified"] and r["missing"] == [], r
        d = json.load(open(r["path"], encoding="utf-8"))
        assert d["verified"] is True
        assert d["beam_flexure"]["mode"] == "fr_connection" and d["beam_flexure"]["a_max"] == 0.07 and d["beam_flexure"]["b_abs"] == 0.07
        assert d["beam_flexure"]["a_expr"].startswith("0.55*") and d["beam_flexure"]["rbs_c_in"] == 4.0 and d["beam_flexure"]["Lb_over_ry"] == pytest.approx(55.1, abs=0.2)
        assert d["column_flexure"]["a_expr"].startswith("5.5*") and d["column_flexure"]["b_max"] == 0.07
        assert d["material"]["Ry_expected"] == 1.1 and d["material"]["Fy_ksi"] == 50.0
        for g in ("material", "beam_flexure", "column_flexure"):
            assert "Table" in d[g]["source"] and "p." in d[g]["source"], d[g]["source"]     # built from the passages, not typed
            assert d[g]["quotes"], "the cell every value was read from travels with it"
        assert all("tools" not in c for c in ScriptedLLM.calls), "the model has no search tool to wander with"
        assert len(ScriptedLLM.calls) == 3, "one call per group"
        assert all(q.get("type") == "exact_table" for q in TableRAG.queries if q.get("query") in ("C5.5", "C3.6", "A3.2"))
        # the engines build hinges from it
        from pushover import hinge_models as HM
        prm = HM.load_params(r["path"])
        b = HM.beam_hinge("W36X232", 360.0, prm); c = HM.column_hinge("W14X730", 192.0, 651.3, prm)
        assert 0 < b.IO < b.LS < b.CP and 0 < c.IO < c.LS < c.CP
        # the trace is on disk, with the reasoning
        tr = [json.loads(l) for l in open(os.path.join(job, collect.TRANSCRIPT_NAME), encoding="utf-8")]
        assert any(t["type"] == "answer" and "copying the cells" in t["reasoning"] for t in tr)
    finally:
        _done(R, L, old)


def test_a_remembered_number_is_rejected_and_the_retry_names_it(tmp_path):
    """The failure of the first live run: the model 'remembers' a = 0.025 for RBS (the ASCE 41-17
    placeholder) and hands it in against the row. No cell holds it, so it is rejected, and the retry
    tells the model exactly which field and why."""
    bad_beam = dict(GOOD_BEAM, a_max=_q(0.025, RBS_ROW))                     # quoted the real row, typed a memory
    job, R, L, old = _live(tmp_path, [GOOD_MATERIAL, bad_beam, GOOD_BEAM, GOOD_COLUMN])
    try:
        r = collect.run(job, emit=collect.Emitter(io.StringIO()))
        assert r["ok"] and r["verified"]
        assert len(ScriptedLLM.calls) == 4, "material, beam (rejected), beam again, column"
        retry = ScriptedLLM.calls[2]["messages"][-1]["content"]
        assert "REJECTED LAST TIME" in retry and "a_max: the number 0025 is not in the quote" in retry
        d = json.load(open(r["path"], encoding="utf-8"))
        assert d["beam_flexure"]["a_max"] == 0.07
    finally:
        _done(R, L, old)


def test_a_quote_that_is_not_in_the_passage_is_rejected(tmp_path):
    invented = dict(GOOD_COLUMN, a_expr=_q("0.8*(h/tw)**-0.6*(L/ry)**-0.8*(1-PG/Pye)**2.2", "a = 0.8 (h/tw)^-0.6 (L/ry)^-0.8 (1-PG/Pye)^2.2"))
    job, R, L, old = _live(tmp_path, [GOOD_MATERIAL, GOOD_BEAM, invented, invented, invented])
    try:
        r = collect.run(job, emit=collect.Emitter(io.StringIO()))
        assert not r["ok"] and r["missing"] == ["column_flexure"]
        assert os.path.basename(r["path"]) == collect.PARTIAL_NAME and not os.path.exists(os.path.join(job, collect.OUT_NAME))
        ev = json.load(open(os.path.join(job, collect.EVIDENCE_NAME), encoding="utf-8"))
        assert any("a_expr: the quote does not occur in the passages" in p for p in ev["problems"]["column_flexure"])
        assert len(ScriptedLLM.calls) == 5, "three attempts for the column, then it is missing -- no loop"
    finally:
        _done(R, L, old)


def test_the_trace_survives_a_run_that_dies(tmp_path):
    job, R, L, old = _live(tmp_path, [GOOD_MATERIAL])
    try:
        L.close()                                                          # the provider goes away mid-run
        r = collect.run(job, emit=collect.Emitter(io.StringIO()))
        assert not r["ok"]
        tr = [json.loads(l) for l in open(os.path.join(job, collect.TRANSCRIPT_NAME), encoding="utf-8")]
        assert tr and tr[0]["type"] == "prefetch" and any(t["type"] == "prompt" for t in tr)
    finally:
        R.close(); os.environ.clear(); os.environ.update(old); rag._status_cache = None


def test_rows_are_decided_from_the_building_not_by_the_model():
    f = collect.gather(EX22)
    assert f["rbs"] and f["rbs_c_in"] == 4.0
    assert collect.row_for("beam_flexure", f)[0] == "beam_flexure:fr_connection" and "RBS" in collect.row_for("beam_flexure", f)[1]
    assert "Highly ductile" in collect.row_for("column_flexure", f)[1]
    f2 = dict(f, rbs=False)
    assert "exception of the RBS" in collect.row_for("beam_flexure", f2)[1]
    f3 = dict(f, moment_frame=False, rbs=False)
    assert collect.row_for("beam_flexure", f3)[0] == "beam_flexure:member"
