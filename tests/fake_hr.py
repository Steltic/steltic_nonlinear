"""fake_hr.py -- a stand-in HR Steel server for the feedback-loop tests (stdlib only).

Implements the routes snl/hr_client.py uses: /healthz, /api/log/<b>, /api/download/<b>, /api/restore/<b>[?archive=1],
/api/run (SSE, resume), /api/stop. Its "design agent" edits the package the way the real agent would for each of the
three loop briefs, so package_checks() can be exercised without an LLM:

    drift      cfg.py gets the drift_relief_16_1_2 block + the new drift_limit; report.html's drift table is scaled
    resize     member_schedule.csv / calc_package.json get the section swaps of the change set
    mechanism  calc_package.json gets capacity_design.panel_zone.by_joint (+ SCWB.by_story)

Jobs live in memory as {building: zip bytes}; a run with resume=True on a job without conversation.json returns 409.
"""
from __future__ import annotations
import csv, io, json, os, re, threading, time, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EX22 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "Ex22_SMF")
PACKAGE_TOP = ("cfg.py", "conversation.json", "report.html", "viewer_3d.html", "model_opensees.py", "model_static.py")


def make_base_zip(src=EX22, building="Ex22_SMF", conversation=True):
    """The Ex22 example as HR Steel's download bundle (<building>/... root), with a synthetic conversation.json."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for top in PACKAGE_TOP:
            p = os.path.join(src, top)
            if os.path.isfile(p):
                z.write(p, "%s/%s" % (building, top))
        for f in os.listdir(os.path.join(src, "design")):
            z.write(os.path.join(src, "design", f), "%s/design/%s" % (building, f))
        if conversation and not os.path.exists(os.path.join(src, "conversation.json")):
            z.writestr("%s/conversation.json" % building, json.dumps([{"role": "user", "content": "BUILDING NAME: %s\n\nDESIGN BRIEF:\n6-story SMF office\n\nDesign this building now." % building}]))
    return buf.getvalue()


def _read(zip_bytes):
    out = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for n in z.namelist():
            if n.endswith("/"):
                continue
            rel = n.split("/", 1)[1] if "/" in n else n
            out[rel] = z.read(n)
    return out


def _write(files, building):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in files.items():
            z.writestr("%s/%s" % (building, rel), data)
    return buf.getvalue()


def agent(files, brief):
    """Edit the package per the loop brief. Returns (files, narrative)."""
    kind = re.search(r"=== SNL FEEDBACK LOOP: (\w+) ===", brief)
    kind = kind.group(1) if kind else "free"
    cfg = files.get("cfg.py", b"").decode("utf-8", "replace")
    calc = json.loads(files["design/calc_package.json"].decode("utf-8"))
    sched = files["design/member_schedule.csv"].decode("utf-8")
    report = files.get("report.html", b"").decode("utf-8", "replace")
    notes = []
    if kind == "drift":
        m = re.search(r"drift_relief_16_1_2 = (\{.*\})", brief)
        relief = json.loads(m.group(1)) if m else {}
        tgt = float(re.search(r"cfg\['drift_limit'\] = ([0-9.]+)", brief).group(1))
        cfg = re.sub(r"drift_limit\s*=\s*[0-9.]+", "drift_limit=%.4f" % tgt, cfg, count=1)
        cfg += "\n\ncfg['drift_relief_16_1_2'] = %s\n" % json.dumps(relief)
        calc.setdefault("capacity_design", {})["drift_relief_16_1_2"] = dict(relief, new_drift_X=0.98 * tgt, new_drift_Y=0.95 * tgt)
        # a lighter lateral frame: the report's drift table scaled so the max sits just under the new target
        i0 = report.find("<h3>Seismic design drift</h3>"); i1 = report.find("<h3>", i0 + 10) if i0 >= 0 else -1
        if i0 >= 0 and i1 > i0:
            sec = report[i0:i1]
            rows = re.findall(r"<tr><td>\d+</td>(?:<td>[\d.]+</td>){4}<td>OK</td></tr>", sec)
            vals = [float(v) for r in rows for v in re.findall(r"<td>([\d.]+)</td>", r)[1:]]          # drop the story number
            amp = [vals[i] for i in range(len(vals)) if i % 4 in (1, 3)]
            k = (0.97 * tgt * 100 / max(amp)) if amp else 1.0
            sec = re.sub(r"(<tr><td>\d+</td>)((?:<td>[\d.]+</td>){4})(<td>OK</td></tr>)",
                         lambda mo: mo.group(1) + "".join("<td>%.3f</td>" % (float(v) * k) for v in re.findall(r"<td>([\d.]+)</td>", mo.group(2))) + mo.group(3), sec)
            sec = re.sub(r"&le;([\d.]+)%</th>", "&le;%.2f%%</th>" % (100 * tgt), sec, count=1)
            report = report[:i0] + sec + report[i1:]
        for gid, new in (("floor-W40X277", "W40X249"), ("floor-W36X256", "W36X231")):
            old = gid.split("-", 1)[1]
            sched = sched.replace(",%s," % old, ",%s," % new)
            for mm in calc["members"]:
                if mm.get("id") == gid:
                    mm["id"] = gid.replace(old, new); mm["inputs"]["section"] = new
        notes.append("relief block copied; drift_limit=%.4f; SMF beams lightened" % tgt)
    elif kind == "resize":
        for line in re.findall(r"^\s+(\S+) at levels \[[^\]]*\] \(\d+ members\): (\S+) -> (\S+)", brief, flags=re.M):
            gid, old, new = line
            role = gid.rsplit("-", 1)[0]
            kindmap = {"lateral_col": "col", "gravity_col": "col", "floor": "beam", "roof": "beam", "brace": "brace"}
            rows = list(csv.DictReader(io.StringIO(sched)))
            for r in rows:
                if r["member"] == kindmap.get(role, "beam") and r["section"] == old:
                    r["section"] = new
            out = io.StringIO(); w = csv.DictWriter(out, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows); sched = out.getvalue()
            for mm in calc["members"]:
                if mm.get("id") == gid:
                    mm["id"] = "%s-%s" % (role, new); mm["inputs"]["section"] = new
                    mm["DC"] = round(min(0.95, mm["DC"] * (1.15 if "DOWNSIZE" in brief.split(gid)[1].split("\n")[0] else 0.9)), 3)
            notes.append("%s: %s -> %s" % (gid, old, new))
    elif kind == "mechanism":
        cd = calc.setdefault("capacity_design", {})
        pz = cd.setdefault("panel_zone", {})
        pz["by_joint"] = [dict(joint="interior Y-face", column="W14X730", beams="W40X277 RBS", Ru_kip=2410.0, phiRn_kip=3041.0, doubler_in=0.75, V_pz_over_V_ye=0.82),
                          dict(joint="interior X-face", column="W14X730", beams="W36X232 RBS", Ru_kip=1980.0, phiRn_kip=3041.0, doubler_in=0.5, V_pz_over_V_ye=0.78),
                          dict(joint="corner", column="W14X730", beams="W36X232 RBS", Ru_kip=900.0, phiRn_kip=3041.0, doubler_in=0.0, V_pz_over_V_ye=0.62)]
        sc = cd.setdefault("SCWB", {})
        levels = re.findall(r"level (\d+) \(z = ", brief)
        sc["by_story"] = [dict(story=int(k), ratio=1.55) for k in levels] or [dict(story=k, ratio=1.3) for k in range(1, 7)]
        notes.append("doublers recorded per joint group; SCWB by storey")
    files["cfg.py"] = cfg.encode("utf-8"); files["design/calc_package.json"] = json.dumps(calc, indent=1).encode("utf-8")
    files["design/member_schedule.csv"] = sched.encode("utf-8")
    if report:
        files["report.html"] = report.encode("utf-8")
    return files, "; ".join(notes) or "applied the instruction"


class FakeHR:
    def __init__(self, base_zip=None, building="Ex22_SMF", slow=0.0, fail=None):
        self.jobs = {building: base_zip or make_base_zip(building=building)}
        self.briefs, self.archived, self.slow, self.fail, self.stopped = {}, [], slow, fail, set()
        self.creds = {"model": "MOCK"}
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

            def do_GET(self):
                p = self.path.split("?")[0]
                if p == "/healthz":
                    return self._json(200, {"ok": True})
                m = re.match(r"^/api/log/([^/]+)$", p)
                if m:
                    b = m.group(1); z = srv.jobs.get(b)
                    return self._json(200, {"events": [], "resumable": bool(z and "conversation.json" in _read(z)), "has_report": bool(z)})
                m = re.match(r"^/api/download/([^/]+)$", p)
                if m:
                    z = srv.jobs.get(m.group(1))
                    if not z:
                        return self._json(404, {"detail": "nothing to download yet"})
                    self.send_response(200); self.send_header("Content-Type", "application/zip"); self.send_header("Content-Length", str(len(z))); self.end_headers(); self.wfile.write(z); return
                self._json(404, {"detail": "not found"})

            def do_POST(self):
                p = self.path; n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else b""
                m = re.match(r"^/api/restore/([^/?]+)(\?.*)?$", p)
                if m:
                    b = m.group(1); archived = None
                    if "archive=1" in (m.group(2) or "") and b in srv.jobs:
                        archived = b + "__" + time.strftime("%Y%m%d-%H%M%S"); srv.jobs[archived] = srv.jobs[b]; srv.archived.append(archived)
                    files = _read(body); srv.jobs[b] = _write(files, b)
                    return self._json(200, {"ok": True, "restored": len(files), "has_conversation": "conversation.json" in files, "building": b, "archived": archived})
                if p == "/api/stop":
                    srv.stopped.add(json.loads(body or b"{}").get("building")); return self._json(200, {"ok": True})
                if p == "/api/creds":
                    srv.creds.update(json.loads(body or b"{}")); return self._json(200, {"ok": True})
                if p == "/api/run":
                    req = json.loads(body or b"{}"); b = req.get("building"); brief = req.get("brief") or ""; resume = req.get("resume")
                    if resume and (b not in srv.jobs or "conversation.json" not in _read(srv.jobs[b])):
                        return self._json(409, {"detail": "nothing to resume for '%s'" % b})
                    srv.briefs[b] = brief
                    self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
                    def sse(ev):
                        self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode()); self.wfile.flush()
                    sse({"type": "status", "text": "continuing '%s' with your new instruction" % b})
                    for w in "Applying the change set and re-running the pipeline.".split():
                        sse({"type": "token", "text": w + " "})
                    sse({"type": "tool", "step": 1, "name": "run_python", "title": "run_python: pipeline.design_and_report"})
                    for _ in range(int(srv.slow * 10) if srv.slow else 1):
                        if b in srv.stopped:
                            sse({"type": "paused", "reason": "stopped by user"}); return
                        time.sleep(0.1)
                    if srv.fail == "paused":
                        sse({"type": "paused", "reason": "NOT PERMITTED: drift over the limit at line 1"}); return
                    if srv.fail == "error":
                        sse({"type": "error", "text": "engine crashed"}); return
                    files, note = agent(_read(srv.jobs[b]), brief)
                    srv.jobs[b] = _write(files, b)
                    sse({"type": "tool_result", "step": 1, "name": "run_python", "summary": note, "ms": 120})
                    sse({"type": "milestone", "text": "design complete: " + note})
                    sse({"type": "done", "building": b}); return
                self._json(404, {"detail": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True); self.thread.start()

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


if __name__ == "__main__":
    s = FakeHR(); print(s.url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        s.close()
