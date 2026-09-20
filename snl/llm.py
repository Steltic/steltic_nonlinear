"""llm.py -- one streaming OpenAI-compatible chat call, stdlib only, for the Review step.

The connection comes from the environment the Steltic hub sets for a `run.llm` CLI run
(STELTIC_LLM_BASE_URL / _API_KEY / _MODEL / _PROVIDER / _REASONING / _MAX_TOKENS); standalone use sets the
same variables. The model's answer streams back as ("reasoning", text) / ("token", text) pieces -- a
separate `reasoning` / `reasoning_content` delta field, or an inline <think>...</think> span, both land in
"reasoning" -- and tool calls are accumulated across deltas the way HR Steel's agent does it. Model "MOCK"
never opens a socket: the caller takes its offline path.
"""
from __future__ import annotations
import json, os, time, urllib.error, urllib.request


class LLMError(RuntimeError):
    def __init__(self, msg, retryable=False):
        super().__init__(msg)
        self.retryable = retryable


class NotConfigured(LLMError):
    pass


def connection() -> dict:
    """The hub's connection from the environment. model 'MOCK' (or nothing set) -> mock=True."""
    c = {"base_url": (os.environ.get("STELTIC_LLM_BASE_URL") or "").rstrip("/"),
         "api_key": os.environ.get("STELTIC_LLM_API_KEY") or "",
         "model": (os.environ.get("STELTIC_LLM_MODEL") or "").strip(),
         "provider": (os.environ.get("STELTIC_LLM_PROVIDER") or "").strip(),
         "reasoning": (os.environ.get("STELTIC_LLM_REASONING") or "high").strip()}
    try:
        c["max_tokens"] = max(256, min(int(os.environ.get("STELTIC_LLM_MAX_TOKENS") or 16000), 200000))
    except ValueError:
        c["max_tokens"] = 16000
    c["mock"] = (not c["model"]) or c["model"].upper() == "MOCK" or not c["base_url"]
    return c


class _ThinkSplitter:
    """Providers that stream the chain-of-thought inline in `content` wrap it in <think>...</think>; route those
    spans to 'reasoning' (safe across deltas that split a tag) and keep them out of the stored answer."""
    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.in_think, self.buf = False, ""

    def feed(self, text):
        self.buf += text
        out = []
        while self.buf:
            tag = self.CLOSE if self.in_think else self.OPEN
            kind = "reasoning" if self.in_think else "token"
            idx = self.buf.find(tag)
            if idx == -1:
                safe = self._safe(self.buf, tag)
                if safe:
                    out.append((kind, self.buf[:safe])); self.buf = self.buf[safe:]
                break
            if idx:
                out.append((kind, self.buf[:idx]))
            self.buf = self.buf[idx + len(tag):]
            self.in_think = not self.in_think
        return out

    def flush(self):
        t, self.buf = self.buf, ""
        return ("reasoning" if self.in_think else "token", t) if t else None

    @staticmethod
    def _safe(buf, tag):
        for k in range(min(len(tag) - 1, len(buf)), 0, -1):
            if buf.endswith(tag[:k]):
                return len(buf) - k
        return len(buf)


def _reasoning_param(mode, base_url):
    if "openrouter" not in (base_url or "") or not mode or mode in ("default", ""):
        return None
    if mode == "off":
        return {"enabled": False}
    if mode in ("low", "medium", "high"):
        return {"effort": mode}
    return None


def _payload(conn, messages, tools, quirks, max_tokens):
    p = {"model": conn["model"], "messages": messages, "stream": True, "temperature": 0.2}
    if tools:
        p["tools"] = tools; p["tool_choice"] = "auto"
    p["max_completion_tokens" if "max_completion_tokens" in quirks else "max_tokens"] = max_tokens
    if "no_temperature" in quirks:
        p.pop("temperature", None)
    if "no_stream_options" not in quirks:
        p["stream_options"] = {"include_usage": True}
    r = _reasoning_param(conn.get("reasoning"), conn["base_url"])
    if r:
        p["reasoning"] = r
    if conn.get("provider") and "openrouter" in conn["base_url"]:
        p["provider"] = {"order": [conn["provider"]], "allow_fallbacks": False, "require_parameters": True}
    return p


def _quirk(body: str):
    b = (body or "").lower()
    if "max_completion_tokens" in b or ("max_tokens" in b and ("unsupported" in b or "not supported" in b)):
        return "max_completion_tokens"
    if "temperature" in b and ("unsupported" in b or "not supported" in b or "does not support" in b):
        return "no_temperature"
    if "stream_options" in b and ("unsupported" in b or "not supported" in b):
        return "no_stream_options"
    return None


_QUIRKS: dict = {}


def stream_chat(conn: dict, messages: list, tools: list | None, on_piece, max_tokens: int | None = None, timeout: float = 600.0) -> dict:
    """One call. on_piece(kind, text) receives the stream as it arrives; the return value is the completed
    assistant turn: {"content", "reasoning", "tool_calls": [{id, type, function:{name, arguments}}], "finish_reason", "usage"}."""
    if conn.get("mock"):
        raise NotConfigured("model MOCK: no provider call")
    url = conn["base_url"] + "/chat/completions"
    quirks = _QUIRKS.setdefault((conn["base_url"], conn["model"]), set())
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + conn["api_key"], "Accept": "text/event-stream"}
    if "openrouter" in conn["base_url"]:
        headers["HTTP-Referer"] = "https://github.com/Steltic/steltic_nonlinear"; headers["X-Title"] = "Steltic Nonlinear review"
    resp = None
    for _ in range(4):
        body = json.dumps(_payload(conn, messages, tools, quirks, max_tokens or conn.get("max_tokens") or 16000)).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            break
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")[:1200]
            q = _quirk(txt)
            if q and q not in quirks:
                quirks.add(q); continue
            raise LLMError("LLM API %s: %s" % (e.code, txt), retryable=e.code in (408, 429) or e.code >= 500)
        except (urllib.error.URLError, OSError) as e:
            raise LLMError("LLM API unreachable: %s" % e, retryable=True)
    if resp is None:
        raise LLMError("LLM API 400: could not satisfy the model's parameter requirements")
    content, reasoning, tool_acc, finish, usage = [], [], {}, None, None
    think = _ThinkSplitter()
    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                chunk = json.loads(line)
            except ValueError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            ch = (chunk.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            if delta.get("content"):
                for kind, txt in think.feed(delta["content"]):
                    if not txt:
                        continue
                    (content if kind == "token" else reasoning).append(txt)
                    on_piece(kind, txt)
            rt = delta.get("reasoning") or delta.get("reasoning_content") or delta.get("thinking")
            if rt:
                reasoning.append(rt); on_piece("reasoning", rt)
            for tc in (delta.get("tool_calls") or []):
                slot = tool_acc.setdefault(tc.get("index", 0) or 0, {"id": None, "name": None, "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    finally:
        try:
            resp.close()
        except Exception:
            pass
    tail = think.flush()
    if tail and tail[1]:
        (content if tail[0] == "token" else reasoning).append(tail[1]); on_piece(tail[0], tail[1])
    tool_calls = [{"id": s["id"] or "call_%d" % i, "type": "function", "function": {"name": s["name"], "arguments": s["arguments"] or "{}"}}
                  for i, s in sorted(tool_acc.items()) if s["name"]]
    return {"content": "".join(content), "reasoning": "".join(reasoning), "tool_calls": tool_calls, "finish_reason": finish, "usage": usage}


def chat_with_retry(conn, messages, tools, on_piece, attempts=3, **kw) -> dict:
    last = None
    for i in range(attempts):
        try:
            return stream_chat(conn, messages, tools, on_piece, **kw)
        except LLMError as e:
            last = e
            if not e.retryable or i == attempts - 1:
                raise
            time.sleep(2.0 * (i + 1))
    raise last  # pragma: no cover
