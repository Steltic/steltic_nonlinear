"""hr_client.py -- the HR Steel (steltic) server as the feedback loops use it: stdlib only.

    healthy(url)                       GET  /healthz
    log(url, building)                 GET  /api/log/<b>            -> {events, resumable, has_report}
    download(url, building)            GET  /api/download/<b>       -> zip bytes (the unified package)
    restore(url, building, zip_bytes, archive=False)
                                       POST /api/restore/<b>[?archive=1] -> {ok, restored, has_conversation, archived}
    run(url, building, brief, on_event, should_stop, resume=True)
                                       POST /api/run (SSE) -> outcome dict; on_event(ev) per frame
    stop(url, building)                POST /api/stop

The SSE reader is the same frame parser the Design-variations module uses (frames split on a blank
line, `data:` payloads are JSON); the `bundle` frame (the whole zip base64'd on completion) is
ignored here because /api/download fetches the package afterwards.
"""
from __future__ import annotations
import json, urllib.error, urllib.parse, urllib.request


class HRError(RuntimeError):
    pass


def _url(base, path):
    return base.rstrip("/") + path


def _request(base, path, method="GET", data=None, headers=None, timeout=60):
    req = urllib.request.Request(_url(base, path), data=data, method=method, headers=headers or {})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:600]
        try:
            body = json.loads(body).get("detail", body)
        except Exception:
            pass
        raise HRError("HR Steel %s %s -> HTTP %d: %s" % (method, path, e.code, body)) from None
    except urllib.error.URLError as e:
        raise HRError("HR Steel unreachable at %s: %s" % (base, e.reason)) from None


def healthy(base):
    try:
        with _request(base, "/healthz", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def log(base, building):
    with _request(base, "/api/log/%s" % urllib.parse.quote(building)) as r:
        return json.loads(r.read().decode("utf-8"))


def download(base, building):
    with _request(base, "/api/download/%s" % urllib.parse.quote(building), timeout=300) as r:
        return r.read()


def restore(base, building, zip_bytes, archive=False):
    path = "/api/restore/%s" % urllib.parse.quote(building) + ("?archive=1" if archive else "")
    with _request(base, path, method="POST", data=zip_bytes, headers={"Content-Type": "application/zip"}, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def stop(base, building):
    try:
        with _request(base, "/api/stop", method="POST", data=json.dumps({"building": building}).encode(),
                      headers={"Content-Type": "application/json"}, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:                                                 # noqa: BLE001
        return {"ok": False, "error": str(e)}


def run(base, building, brief, on_event=None, should_stop=None, resume=True, timeout=7200):
    """Stream one /api/run. Returns {"outcome": done|paused|error|stopped|disconnected, "reason": str, "n_events": int}."""
    body = json.dumps({"building": building, "brief": brief, "resume": bool(resume)}).encode()
    resp = _request(base, "/api/run", method="POST", data=body, headers={"Content-Type": "application/json", "Accept": "text/event-stream"}, timeout=timeout)
    outcome = {"outcome": "disconnected", "reason": "stream ended without done/paused/error", "n_events": 0}
    buf = b""
    try:
        while True:
            if should_stop and should_stop():
                stop(base, building)
                outcome = {"outcome": "stopped", "reason": "stopped by the user", "n_events": outcome["n_events"]}
                break
            chunk = resp.readline()
            if not chunk:
                break
            buf += chunk
            if chunk not in (b"\n", b"\r\n"):
                continue
            frame, buf = buf, b""
            payload = "\n".join(l[5:].strip() for l in frame.decode("utf-8", "replace").splitlines() if l.startswith("data:"))
            if not payload:
                continue                                                   # ": ping" keepalive or an empty frame
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            outcome["n_events"] += 1
            t = ev.get("type")
            if t == "bundle":
                continue
            if on_event:
                on_event(ev)
            if t == "done":
                outcome = {"outcome": "done", "reason": "", "n_events": outcome["n_events"]}; break
            if t == "paused":
                outcome = {"outcome": "paused", "reason": "%s%s" % (ev.get("reason", ""), (": " + str(ev.get("detail"))) if ev.get("detail") else ""), "n_events": outcome["n_events"]}; break
            if t == "error":
                outcome = {"outcome": "error", "reason": str(ev.get("text", "")), "n_events": outcome["n_events"]}; break
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return outcome
