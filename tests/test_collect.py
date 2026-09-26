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


# ---------------------------------------------------------------- the loop the first live run fell into
from http.server import BaseHTTPRequestHandler   # noqa: E402
from test_review import _sse, _env               # noqa: E402


class LoopingLLM(BaseHTTPRequestHandler):
    """The model as it behaved on g53ex2: it asks AISC 342 to confirm four numbers it remembers, gets the
    same five passages, and asks again. 68 times. This fake asks forever while it has the tool; the
    moment the tool is withdrawn it writes the file (the Ex22 values, cited)."""
    calls = []
    answer = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        LoopingLLM.calls.append(body)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        w = self.wfile
        if body.get("tools"):
            _sse(w, {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c%d" % len(LoopingLLM.calls),
                  "function": {"name": "search_engineering_standards",
                               "arguments": '{"query": "0.025 0.05 0.035 0.07 RBS", "document": "A342"}'}}]}}]})
            _sse(w, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
        else:
            _sse(w, {"choices": [{"delta": {"content": json.dumps(LoopingLLM.answer)}}]})
            _sse(w, {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
        w.write(b"data: [DONE]\n\n"); w.flush()


def test_the_same_fruitless_search_is_refused_after_three_and_the_run_still_ends(tmp_path):
    job = _job(tmp_path)
    FakeRAG.queries.clear(); LoopingLLM.calls.clear()
    LoopingLLM.answer = {k: GOOD[k] for k in ("material", "beam_flexure", "column_flexure")}
    R = _Server(FakeRAG); L = _Server(LoopingLLM)
    old = dict(os.environ)
    try:
        os.environ.update(_env(L.url, R.url + "/query")); rag._status_cache = None
        buf = io.StringIO()
        r = collect.run(job, emit=collect.Emitter(buf))
        ev = json.load(open(os.path.join(job, collect.EVIDENCE_NAME), encoding="utf-8"))
        same = [s for s in ev["searches"] if (s["args"].get("query") or "").startswith("0.025 0.05")]
        sent = [s for s in same if s.get("via") != "refused"]
        refused = [s for s in same if s.get("via") == "refused"]
        assert len(sent) == collect.REPEAT_LIMIT, "the corpus was asked exactly REPEAT_LIMIT times"
        assert len(refused) == collect.REPEAT_LIMIT, "then refused without a round trip until the tool was withdrawn"
        assert len([q for q in FakeRAG.queries if (q.get("query") or "").startswith("0.025 0.05")]) <= collect.REPEAT_LIMIT * 4
        assert any(e.get("type") == "warning" and "withdrawing the search tool" in e["text"] for e in _events(buf))
        assert not LoopingLLM.calls[-1].get("tools"), "the last call had no tool to loop on"
        assert r["ok"] and r["verified"], "the model wrote the file once it was made to"
        assert len(LoopingLLM.calls) < 12, "and it took a handful of turns, not eighty"
    finally:
        R.close(); L.close(); os.environ.clear(); os.environ.update(old); rag._status_cache = None


def test_the_tables_each_group_needs_are_fetched_before_the_model_is_asked(tmp_path):
    job = _job(tmp_path)
    FakeRAG.queries.clear()
    R = _Server(FakeRAG)
    try:
        _rag_env(R); os.environ["STELTIC_LLM_MODEL"] = "MOCK"; os.environ.pop("STELTIC_LLM_BASE_URL", None)
        collect.run(job, emit=collect.Emitter(io.StringIO()))
        # exact-table lookups, in policy form, for the tables an SMF with RBS connections is read from
        asked = [("342" if "AISC_342" in q.get("collection", "") else q.get("collection", ""), q.get("type"), q.get("query")) for q in FakeRAG.queries]
        assert ("342", "exact_table", "C5.5") in asked and ("342", "exact_table", "C3.6") in asked
        assert ("342", "exact_table", "A5.2") in asked
        assert all(q.get("context_neighbors") is not None for q in FakeRAG.queries if q.get("type") == "exact_table")
        msg = collect._facts_message(collect.gather(job), {}, [{"group": "g", "document": "A342", "how": "exact_table C5.5",
                                                                  "passages": [{"source": "AISC_342_22", "section": "C5.5", "page": "110", "text": "RBS row"}], "note": ""}])
        assert msg.startswith("PASSAGES ALREADY RETRIEVED") and "[AISC_342_22 C5.5 p. 110]" in msg
    finally:
        R.close(); os.environ.pop("RAG_API_URL", None); os.environ.pop("STELTIC_LLM_MODEL", None); rag._status_cache = None


def test_the_prompt_names_the_converter_artefacts_and_the_memory_trap():
    assert "X 2 0 07" in collect.SYSTEM and "footnotes [d] and [e]" in collect.SYSTEM
    assert "Never search for a number you remember" in collect.SYSTEM
    assert ("come back %d times" % collect.REPEAT_LIMIT) in collect.SYSTEM
