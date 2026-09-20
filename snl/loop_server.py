"""loop_server.py -- the Feedback tab of the Nonlinear module inside the Steltic hub.

    python -m uvicorn snl.loop_server:app --port 8xxx
        SNL_JOBS            the hub's jobs folder (one project = one sub-folder = one SNL job)
        STELTIC_URL         HR Steel's base URL (the hub passes {server.steltic})
        STELTIC_ENGINE_DIR  steltic/steel_engine (the DDM re-check needs it)

Shows what the Chapter 16 run (and the pushover / DDM) measured, the three loops with their eligibility
and change sets, lets the user edit a change set and press Go, streams the HR Steel re-design and the
verification, and offers "Make design of record" on a verified candidate. Everything durable lives in
<job>/feedback/<loop_id>/ (see snl/loop.py).
"""
from __future__ import annotations
import json, os, pathlib, re, threading, time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import feedback as F, hr_client as HR, loop as L

HERE = pathlib.Path(__file__).resolve().parent
UI = HERE / "loop_ui"
JOBS = pathlib.Path(os.environ.get("SNL_JOBS") or (pathlib.Path.cwd() / "jobs")).resolve()
STELTIC_URL = (os.environ.get("STELTIC_URL") or "").rstrip("/")
ENGINE_DIR = os.environ.get("STELTIC_ENGINE_DIR") or ""
KEEPALIVE = 20.0

app = FastAPI(title="Nonlinear (SNL) -- feedback loops")
app.mount("/static", StaticFiles(directory=str(UI)), name="static")


@app.middleware("http")
async def _no_stale_assets(request, call_next):
    """Every Steltic module page loads /static/app.js by the same absolute path and the browser caches per
    origin (127.0.0.1:<port>): an asset served on a port another module used earlier would be the wrong
    module's script. Revalidate on every load."""
    resp = await call_next(request)
    if "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def clean_name(s):
    s = re.sub(r"[^A-Za-z0-9_-]", "", (s or "").strip())
    return s or "Project"


def job_dir(project):
    return JOBS / clean_name(project)


class Bus:
    """Per-project event log the SSE endpoint replays from `since`."""
    def __init__(self):
        self.events, self.seq, self.cv = [], 0, threading.Condition()

    def push(self, ev):
        with self.cv:
            self.seq += 1; self.events.append({"seq": self.seq, "t": time.time(), **ev})
            if len(self.events) > 4000:
                del self.events[:1000]
            self.cv.notify_all()


_BUS: dict[str, Bus] = {}
_LOOPS: dict[str, L.Loop] = {}
_GUARD = threading.Lock()


def bus(project):
    with _GUARD:
        return _BUS.setdefault(clean_name(project), Bus())


def running(project):
    lp = _LOOPS.get(clean_name(project))
    return lp if (lp and lp.is_alive()) else None


def _job_data(project):
    jd = job_dir(project)
    if not (jd / "model_opensees.py").exists():
        return None
    return F.read_job(str(jd))


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/me")
async def me():
    return {"jobs": str(JOBS), "steltic_url": STELTIC_URL, "hr_ok": HR.healthy(STELTIC_URL) if STELTIC_URL else False,
            "engine_ok": bool(ENGINE_DIR and os.path.isdir(ENGINE_DIR)), "cpu_count": os.cpu_count() or 1,
            "defaults": F.DEFAULTS, "loops": F.LOOPS}


@app.get("/api/project/{project}")
async def project(project: str):
    jd = _job_data(project)
    out = {"project": clean_name(project), "exists": jd is not None, "running": None, "loops": L.list_loops(str(job_dir(project)))}
    lp = running(project)
    if lp:
        out["running"] = lp.id
    if jd is None:
        out["hint"] = "no Steltic package in this project folder yet -- run HR Steel, then the Nonlinear analyses (Run analyses tab)"
        return out
    out["status"] = F.status(jd)
    plans = {}
    for k in F.LOOPS:
        try:
            plans[k] = F.plan_as_json(F.plan(jd, k))
        except Exception as ex:                                            # noqa: BLE001
            plans[k] = {"kind": k, "title": F.LOOPS[k], "eligible": False, "reasons": ["plan failed: %s" % ex]}
    out["plans"] = plans
    if STELTIC_URL:
        try:
            lg = HR.log(STELTIC_URL, clean_name(project))
            out["hr_job"] = {"resumable": lg.get("resumable"), "has_report": lg.get("has_report")}
        except Exception as ex:                                            # noqa: BLE001
            out["hr_job"] = {"resumable": False, "error": str(ex)}
    return out


@app.post("/api/project/{project}/plan")
async def plan(project: str, request: Request):
    body = await request.json()
    kind = body.get("kind")
    if kind not in F.LOOPS:
        raise HTTPException(400, "kind must be one of %s" % list(F.LOOPS))
    jd = _job_data(project)
    if jd is None:
        raise HTTPException(404, "no package in this project")
    p = F.plan(jd, kind, body.get("options") or {})
    p = L.apply_edits(p, body.get("edits") or {}, jd)
    return F.plan_as_json(p)


@app.post("/api/project/{project}/loop")
async def start_loop(project: str, request: Request):
    body = await request.json()
    kind = body.get("kind")
    if kind not in F.LOOPS:
        raise HTTPException(400, "kind must be one of %s" % list(F.LOOPS))
    if running(project):
        raise HTTPException(409, "a loop is already running for this project")
    jd = job_dir(project)
    if not (jd / "model_opensees.py").exists():
        raise HTTPException(404, "no package in this project")
    if not STELTIC_URL:
        raise HTTPException(400, "STELTIC_URL is not set -- this server must run inside the hub with HR Steel")
    b = bus(project)
    lp = L.Loop(str(jd), kind, options=body.get("options") or {}, edits=body.get("edits") or {}, verify=body.get("verify") or "full",
                hr_url=STELTIC_URL, base_building=clean_name(project), engine_dir=ENGINE_DIR,
                parallel=int(body.get("parallel") or max(1, min(4, (os.cpu_count() or 2) // 2))), n_records=int(body.get("n_records") or 11),
                on_event=b.push)
    _LOOPS[clean_name(project)] = lp
    lp.start()
    return {"ok": True, "id": lp.id, "candidate": lp.candidate}


@app.get("/api/project/{project}/loop/{loop_id}")
async def loop_state(project: str, loop_id: str):
    st = L.load_state(str(job_dir(project)), clean_name(loop_id))
    if not st:
        raise HTTPException(404, "no such loop")
    return st


@app.post("/api/project/{project}/loop/{loop_id}/stop")
async def stop_loop(project: str, loop_id: str):
    lp = running(project)
    if not lp or lp.id != clean_name(loop_id):
        return {"ok": True, "running": False}
    lp.stop()
    return {"ok": True, "running": True}


@app.post("/api/project/{project}/loop/{loop_id}/promote")
async def promote(project: str, loop_id: str):
    if running(project):
        raise HTTPException(409, "wait for the running loop to finish")
    lines = []
    try:
        st = L.promote(str(job_dir(project)), clean_name(loop_id), STELTIC_URL, base_building=clean_name(project), on_log=lines.append)
    except Exception as ex:                                                # noqa: BLE001
        raise HTTPException(400, str(ex))
    bus(project).push({"type": "promoted", "id": clean_name(loop_id), "lines": lines})
    return {"ok": True, "state": st, "lines": lines}


@app.get("/api/project/{project}/events")
async def events(project: str, since: int = 0):
    b = bus(project)

    def gen():
        last, beat = since, time.time()
        while True:
            with b.cv:
                pending = [e for e in b.events if e["seq"] > last]
                if not pending:
                    b.cv.wait(timeout=1.0)
                    pending = [e for e in b.events if e["seq"] > last]
            for e in pending:
                last = e["seq"]
                yield "data: " + json.dumps(e, default=str) + "\n\n"
            if not pending and not running(project):
                yield "data: " + json.dumps({"type": "idle", "seq": last}) + "\n\n"
                return
            if time.time() - beat > KEEPALIVE:
                beat = time.time(); yield ": keepalive\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/project/{project}/loop/{loop_id}/file/{path:path}")
async def loop_file(project: str, loop_id: str, path: str):
    base = pathlib.Path(L.loop_dir(str(job_dir(project)), clean_name(loop_id))).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(target))


@app.get("/api/project/{project}/file/{path:path}")
async def project_file(project: str, path: str):
    base = job_dir(project).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(target))


@app.get("/", response_class=HTMLResponse)
async def index():
    return (UI / "index.html").read_text(encoding="utf-8")
