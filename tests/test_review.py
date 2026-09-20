"""The Review step against a fake OpenAI-compatible server and a fake standards server (stdlib only).

The fake model streams reasoning, one tool call (a standards search), then the review text; the test reads the
JSON event lines the step prints -- the same lines the Steltic hub relays -- and the files it writes.
"""
import io, json, os, shutil, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EX22 = os.path.join(ROOT, "examples", "Ex22_SMF")

from snl import llm, rag, review  # noqa: E402


def _job():
    tmp = tempfile.mkdtemp(); job = os.path.join(tmp, "Ex22_SMF")
    shutil.copytree(EX22, job, ignore=shutil.ignore_patterns("*.pkl", "__pycache__", "feedback", "*_viewer_3d.html", "model_*.py"))
    return job


class _Server:
    def __init__(self, handler):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


def _sse(w, obj):
    w.write(("data: " + json.dumps(obj) + "\n\n").encode("utf-8")); w.flush()


class FakeLLM(BaseHTTPRequestHandler):
    """Turn 1: reasoning + a tool call. Turn 2 (after the tool result): the review, with an inline <think> span."""
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FakeLLM.calls.append(body)
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        w = self.wfile
        has_tool_result = any(m.get("role") == "tool" for m in body["messages"])
        if not has_tool_result:
            _sse(w, {"choices": [{"delta": {"reasoning": "The mean drift is 1.46% against "}}]})
            _sse(w, {"choices": [{"delta": {"reasoning_content": "2.0%; look up 16.4.1.2."}}]})
            _sse(w, {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "search_engineering_standards", "arguments": '{"query": "mean story drift'}}]}}]})
            _sse(w, {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ' ratio limit", "document": "ASCE7", "clause": "16.4.1.2"}'}}]}}]})
            _sse(w, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 1200, "completion_tokens": 40}})
        else:
            tool_msg = next(m for m in body["messages"] if m.get("role") == "tool")
            assert "16.4.1.2" in tool_msg["content"] and "two times" in tool_msg["content"], tool_msg["content"]
            _sse(w, {"choices": [{"delta": {"content": "<think>cite the passage</think>## 1. Verdict\n\nThe design passes Chapter 16: mean drift 1.46% vs 2.00% "}}]})
            _sse(w, {"choices": [{"delta": {"content": "[ASCE 7-22 §16.4.1.2, p. 149].\n\n## 7. What to change, and why\n\nNothing is required.\n"}}]})
            _sse(w, {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1900, "completion_tokens": 60}})
        w.write(b"data: [DONE]\n\n"); w.flush()


class FakeRAG(BaseHTTPRequestHandler):
    queries = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FakeRAG.queries.append(body)
        hits = []
        if body.get("clause") == "16.4.1.2" or "drift" in (body.get("query") or ""):
            hits = [{"text": "16.4.1.2 Story Drift. The mean story drift ratio shall not exceed two times the limits of Table 12.12-1.",
                     "doc": "ASCE7", "section_id": "16.4.1.2", "title": "Story Drift", "printed_label": "149", "score": 12.5, "authoritative": True}]
        out = json.dumps({"results": hits, "collection": body.get("collection"), "count": len(hits), "matched": "exact_section" if hits else ""}).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)


def _env(llm_url, rag_url, model="fake-model"):
    env = dict(os.environ)
    env.update({"STELTIC_LLM_BASE_URL": llm_url, "STELTIC_LLM_API_KEY": "sk-test", "STELTIC_LLM_MODEL": model, "RAG_API_URL": rag_url})
    return env


def test_gather_reads_the_run_without_inventing():
    ev = review.gather(EX22)
    assert ev["job"] == "Ex22_SMF" and ev["files"]["snl_summary.json"] and ev["files"]["nlrha/nlrha_package.json"]
    assert ev["summary"]["nlrha"]["verdict"] == "ACCEPTABLE" and ev["nlrha"]["limits"]["risk_category"] == "IV"
    assert len(ev["nlrha"]["records"]) == 11 and ev["nlrha"]["story"][0]["mean_X_pct"] == 0.73
    assert set(ev["pushover"]["directions"]) == {"X", "Y"} and ev["pushover"]["directions"]["X"]["p695"]["Omega"] > 5
    assert ev["ddm"]["gate"]["ok"] and any(r["label"] == "1.2D+1.6L+0.5Lr" for r in ev["ddm"]["runs"])
    assert ev["design"]["worst_members"][0]["DC"] <= 1.0 and ev["design"]["drift"]["limit_pct"] == 1.0
    assert len(json.dumps(ev, default=str)) < 120_000          # bounded: the model's context, not the run folder


def test_review_streams_events_grounds_a_clause_and_writes_the_files():
    FakeLLM.calls.clear(); FakeRAG.queries.clear()
    L, R = _Server(FakeLLM), _Server(FakeRAG)
    job = _job()
    try:
        for k, v in _env(L.url, R.url).items():
            os.environ[k] = v
        buf = io.StringIO()
        r = review.run(job, focus="is the drift margin real?", emit=review.Emitter(buf), max_searches=4)
        assert r["ok"] and len(r["searches"]) == 1
        lines = buf.getvalue().splitlines()
        events = [json.loads(l) for l in lines if l.startswith("{")]
        types = [e["type"] for e in events]
        assert types[:2] == ["status", "milestone"]
        assert "reasoning" in types and "token" in types and "tool" in types and "tool_result" in types and "usage" in types
        # reasoning arrives from the separate delta field AND from the inline <think> span; neither lands in the review
        reasoning = "".join(e["text"] for e in events if e["type"] == "reasoning")
        assert "look up 16.4.1.2" in reasoning and "cite the passage" in reasoning
        tokens = "".join(e["text"] for e in events if e["type"] == "token")
        assert "<think>" not in tokens and "## 1. Verdict" in tokens
        tool = next(e for e in events if e["type"] == "tool")
        assert tool["name"] == "search_engineering_standards" and "16.4.1.2" in tool["title"] and tool["step"] == 1
        assert next(e for e in events if e["type"] == "tool_result")["summary"].startswith("1 passage")
        assert FakeRAG.queries[0]["collection"] == "engineering_standards_ASCE7" and FakeRAG.queries[0]["clause"] == "16.4.1.2"
        usage = [e for e in events if e["type"] == "usage"]
        assert usage[-1]["cum_in"] == 3100 and usage[-1]["cum_out"] == 100
        # the evidence went to the model, with the focus
        user = FakeLLM.calls[0]["messages"][1]["content"]
        assert "EVIDENCE" in user and '"verdict": "ACCEPTABLE"' in user and "is the drift margin real?" in user
        assert FakeLLM.calls[0]["tools"][0]["function"]["name"] == "search_engineering_standards"
        # every plain line is a log line, every event line is exactly one JSON object
        for l in lines:
            if l.startswith("{"):
                json.loads(l)
        md = open(os.path.join(job, "review.md"), encoding="utf-8").read()
        assert "[ASCE 7-22 §16.4.1.2, p. 149]" in md and "<think>" not in md
        h = open(os.path.join(job, "review.html"), encoding="utf-8").read()
        assert "<h2>1. Verdict</h2>" in h and "fake-model" in h and "1 standards searches" in h
        tr = json.load(open(os.path.join(job, "review_transcript.json"), encoding="utf-8"))
        assert tr["searches"][0]["passages"][0].startswith("16.4.1.2 Story Drift") and tr["evidence"]["job"] == "Ex22_SMF"
    finally:
        L.close(); R.close()
        for k in ("STELTIC_LLM_BASE_URL", "STELTIC_LLM_API_KEY", "STELTIC_LLM_MODEL", "RAG_API_URL"):
            os.environ.pop(k, None)


def test_mock_model_writes_the_review_offline_and_still_searches():
    FakeRAG.queries.clear()
    R = _Server(FakeRAG)
    job = _job()
    try:
        os.environ.update({"STELTIC_LLM_MODEL": "MOCK", "RAG_API_URL": R.url})
        os.environ.pop("STELTIC_LLM_BASE_URL", None)
        buf = io.StringIO()
        r = review.run(job, emit=review.Emitter(buf))
        assert r["ok"] and len(r["searches"]) == 3 and len(FakeRAG.queries) == 3
        md = r["review_md"]
        assert "model MOCK" in md and "ACCEPTABLE" in md and "1.46%" in md and "2.00%" in md
        assert "[ASCE 7-22 §16.4.1.2, p. 149]" in md                       # found in the corpus
        assert "§16.4.1.1] (UNVERIFIED)" in md                             # the fake corpus has no passage for it
        assert os.path.exists(os.path.join(job, "review.html"))
        events = [json.loads(l) for l in buf.getvalue().splitlines() if l.startswith("{")]
        assert [e["type"] for e in events if e["type"] == "tool"] == ["tool"] * 3
    finally:
        R.close()
        for k in ("STELTIC_LLM_MODEL", "RAG_API_URL"):
            os.environ.pop(k, None)


def test_no_standards_server_is_said_not_hidden():
    job = _job()
    os.environ.update({"STELTIC_LLM_MODEL": "MOCK"}); os.environ.pop("RAG_API_URL", None)
    try:
        buf = io.StringIO()
        r = review.run(job, emit=review.Emitter(buf))
        assert r["ok"] and r["searches"] == []
        events = [json.loads(l) for l in buf.getvalue().splitlines() if l.startswith("{")]
        assert "no standards server" in events[1]["text"]
        assert r["review_md"].count("(UNVERIFIED)") >= 3
        res = rag.search("anything", "ASCE7")
        assert res["ok"] is False and "RAG_API_URL is empty" in res["note"] and rag.render(res).startswith("NO PASSAGES")
        assert rag.search("x", "NOT-A-DOC")["ok"] is False
    finally:
        os.environ.pop("STELTIC_LLM_MODEL", None)


def test_search_budget_is_enforced_and_told_to_the_model():
    FakeRAG.queries.clear()
    R = _Server(FakeRAG)
    job = _job()
    try:
        os.environ.update({"STELTIC_LLM_MODEL": "MOCK", "RAG_API_URL": R.url})
        r = review.run(job, emit=review.Emitter(io.StringIO()), max_searches=1)
        assert r["ok"] and len(r["searches"]) == 3 and len(FakeRAG.queries) == 1
        assert "budget" in (r["searches"][1]["note"] or "")
    finally:
        R.close()
        for k in ("STELTIC_LLM_MODEL", "RAG_API_URL"):
            os.environ.pop(k, None)


def test_an_empty_job_is_refused_with_a_reason():
    tmp = tempfile.mkdtemp()
    os.environ.update({"STELTIC_LLM_MODEL": "MOCK"})
    try:
        buf = io.StringIO()
        r = review.run(tmp, emit=review.Emitter(buf))
        assert not r["ok"]
        events = [json.loads(l) for l in buf.getvalue().splitlines() if l.startswith("{")]
        assert events[-1]["type"] == "error" and "run the analyses first" in events[-1]["text"]
    finally:
        os.environ.pop("STELTIC_LLM_MODEL", None)


def test_cli_entry_point_runs_the_review():
    job = _job()
    env = dict(os.environ); env.update({"STELTIC_LLM_MODEL": "MOCK"}); env.pop("RAG_API_URL", None)
    p = subprocess.run([sys.executable, "-m", "snl", "review", job, "--focus", "cost", "--no-standards"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    lines = p.stdout.splitlines()
    assert any(l.startswith('{"type": "milestone"') for l in lines) and any(l.startswith(">> review:") for l in lines)
    assert os.path.exists(os.path.join(job, "review.md")) and "Focus asked:** cost" in open(os.path.join(job, "review.md")).read()


def test_llm_client_splits_inline_think_spans_across_deltas():
    sp = llm._ThinkSplitter()
    out = sp.feed("hello <thi"); out += sp.feed("nk>secret</think> world")
    assert out == [("token", "hello "), ("reasoning", "secret"), ("token", " world")]
    assert sp.flush() is None
    c = llm.connection()
    assert c["mock"] is True                     # nothing set -> offline path, never a socket
