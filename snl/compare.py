"""compare.py -- the four-analyses sheet: Steltic design package + Pushover + NLRHA + DDM outputs of one building, side by side.

Reads what exists in the job folder (report.html, design/calc_package.json, pushover/pushover_package.json,
nlrha/nlrha_package.json, ddm_results.json) and writes <job>/four_analyses.html plus <job>/snl_summary.json. Every
number comes from those files; the "readings" are rule-based sentences. A package that was not run is shown as
'not run', never invented. The SNL bot's engineering narrative supplements this sheet in the chat; it does not
replace the figures here.
"""
from __future__ import annotations
import datetime, html, json, os, re

_HERE = os.path.dirname(os.path.abspath(__file__))
RC_BPON = {"I_II": ("LS", "CP"), "III": ("LS", "CP"), "IV": ("IO", "LS")}     # ASCE 41-23 Table 2-5 structural levels at BSE-1N / BSE-2N
RC_BPON_NOTE = {"I_II": "Life Safety at BSE-1N, Collapse Prevention at BSE-2N", "III": "Damage Control at BSE-1N (read here as LS), Limited Safety at BSE-2N (read here as CP)", "IV": "Immediate Occupancy at BSE-1N, Life Safety at BSE-2N"}


def _load_json(path, default=None):
    if not os.path.exists(path):
        return default
    s = open(path, encoding="utf-8").read()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s))


def _text(path):
    if not os.path.exists(path):
        return ""
    h = open(path, encoding="utf-8", errors="replace").read()
    t = re.sub(r"<style.*?</style>|<script.*?</script>", "", h, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t)


def steltic_facts(job):
    """Design drift table (C_d delta_e / I_e per storey), drift limit, design V and W from report.html; D/C from calc_package."""
    t = _text(os.path.join(job, "report.html"))
    out = dict(drift_limit_pct=None, drift_X=None, drift_Y=None, V_kip=None, W_kip=None, Cs=None, wind_X=None, wind_Y=None)
    m = re.search(r"Story δe X % δ X % δe Y % δ Y % ≤([\d.]+)%(.{0,1200})", t)
    if m:
        out["drift_limit_pct"] = float(m.group(1))
        rows = re.findall(r"(\d+) ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) (?:OK|NG|FAIL|EXCEEDS)", m.group(2))
        if rows:
            out["drift_X"] = [float(r[2]) for r in rows]; out["drift_Y"] = [float(r[4]) for r in rows]
    m = re.search(r"Design base shear V = C s W = ([\d.]+) × ([\d,]+) = ([\d,]+) kip", t)
    if m:
        out["Cs"] = float(m.group(1)); out["W_kip"] = float(m.group(2).replace(",", "")); out["V_kip"] = float(m.group(3).replace(",", ""))
    m = re.search(r"Wind base shear: X = ([\d,]+) kip, Y = ([\d,]+) kip", t)
    if m:
        out["wind_X"] = float(m.group(1).replace(",", "")); out["wind_Y"] = float(m.group(2).replace(",", ""))
    cp = _load_json(os.path.join(job, "design", "calc_package.json"), {})
    members = [x for x in cp.get("members", []) if isinstance(x.get("DC"), (int, float))]
    out["members"] = members
    out["dc_max"] = max(members, key=lambda x: x["DC"]) if members else None
    out["capacity_design"] = cp.get("capacity_design", {})
    out["connections"] = cp.get("connections", [])
    out["building"] = cp.get("building") or os.path.basename(os.path.abspath(job))
    return out


def fmt(x, n=2):
    return "—" if x is None else (f"{x:,.{n}f}" if isinstance(x, (int, float)) else str(x))


def pct(x, n=2):
    return "—" if x is None else f"{100 * x:.{n}f}%"


def pill(cls, txt):
    return f'<span class="pill {cls}">{txt}</span>'


# --------------------------------------------------------------------------------------------- figures
def svg_curves(po, V_design):
    dirs = po["directions"]
    W, H = 560, 270; ml, mr, mt, mb = 54, 16, 16, 34; pw, ph = W - ml - mr, H - mt - mb
    umax = max(max(d["curve_u_in"]) for d in dirs.values()) * 1.05; vmax = max(max(max(d["curve_V_kip"]) for d in dirs.values()), V_design or 0) * 1.12
    X = lambda u: ml + u / umax * pw; Y = lambda v: mt + ph - v / vmax * ph
    o = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;display:block">']
    for i in range(5):
        v = vmax * i / 4; o.append(f'<line x1="{ml}" y1="{Y(v):.1f}" x2="{ml+pw}" y2="{Y(v):.1f}" stroke="var(--grid)"/><text class="ax" x="{ml-6}" y="{Y(v)+4:.1f}" text-anchor="end">{v:,.0f}</text>')
        u = umax * i / 4; o.append(f'<text class="ax" x="{X(u):.1f}" y="{H-14}" text-anchor="middle">{u:.0f}</text>')
    o.append(f'<text class="lab" x="{ml+pw}" y="{H-2}" text-anchor="end">roof displacement (in)</text><text class="lab" x="{ml}" y="{mt-4}">base shear (kip)</text>')
    if V_design:
        o.append(f'<line x1="{ml}" y1="{Y(V_design):.1f}" x2="{ml+pw}" y2="{Y(V_design):.1f}" stroke="var(--muted)" stroke-dasharray="2,4"/><text class="lab" x="{ml+pw-2}" y="{Y(V_design)-4:.1f}" text-anchor="end">design V = {V_design:,.0f} kip (R-reduced)</text>')
    cols = {"X": "var(--hot)", "Y": "var(--steel)"}
    for k, (name, d) in enumerate(dirs.items()):
        col = cols.get(name, "var(--teal)")
        pts = " ".join(f"{X(u):.1f},{Y(v):.1f}" for u, v in zip(d["curve_u_in"], d["curve_V_kip"])); o.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
        for lvl, dash in (("BSE-1N", "3,3"), ("BSE-2N", "7,3")):
            if lvl in d["nsp"]:
                dt = d["nsp"][lvl]["target_disp_in"]; o.append(f'<line x1="{X(dt):.1f}" y1="{mt}" x2="{X(dt):.1f}" y2="{mt+ph}" stroke="{col}" stroke-dasharray="{dash}" opacity=".75"/>')
        o.append(f'<text class="lab" x="{ml+10}" y="{mt+16+14*k}" style="fill:{col}">push {name} · V<tspan font-size="9">max</tspan> {d["p695"]["Vmax_kip"]:,.0f} kip · Ω {fmt(d["p695"].get("Omega"),1)}</text>')
    o.append(f'<text class="lab" x="{ml+10}" y="{mt+16+14*len(dirs)}">δ<tspan font-size="9">t</tspan> BSE-1N dotted · BSE-2N dashed</text>')
    o.append("</svg>"); return "".join(o)


def svg_drifts(st_profile, po_profile, nl_mean, nl_max, lim_mean_pct, lim_design_pct, dirn):
    n = max(len(x) for x in (st_profile or [], po_profile or [], nl_mean or [], nl_max or []) if x) if any((st_profile, po_profile, nl_mean, nl_max)) else 0
    if not n:
        return ""
    W, H = 560, 60 + 32 * n; ml, mr, mt, mb = 40, 16, 18, 30; pw, ph = W - ml - mr, H - mt - mb
    allv = [v for x in (st_profile, po_profile, nl_mean, nl_max) if x for v in x] + [lim_mean_pct or 0, lim_design_pct or 0]
    dmax = max(allv) * 1.25; X = lambda v: ml + v / dmax * pw; Y = lambda i: mt + ph - (i + 0.5) / n * ph
    o = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;display:block">']
    for i in range(6):
        v = dmax * i / 5; o.append(f'<line x1="{X(v):.1f}" y1="{mt}" x2="{X(v):.1f}" y2="{mt+ph}" stroke="var(--grid)"/><text class="ax" x="{X(v):.1f}" y="{H-12}" text-anchor="middle">{v:.1f}%</text>')
    for i in range(n): o.append(f'<text class="ax" x="{ml-6}" y="{Y(i)+4:.1f}" text-anchor="end">S{i+1}</text>')
    if lim_mean_pct:
        o.append(f'<line x1="{X(lim_mean_pct):.1f}" y1="{mt}" x2="{X(lim_mean_pct):.1f}" y2="{mt+ph}" stroke="var(--warn)" stroke-dasharray="5,3"/><text class="lab" x="{X(lim_mean_pct)-4:.1f}" y="{mt+10}" text-anchor="end" style="fill:var(--warn)">Ch. 16 mean limit {lim_mean_pct:.2f}%</text>')
    if lim_design_pct:
        o.append(f'<line x1="{X(lim_design_pct):.1f}" y1="{mt}" x2="{X(lim_design_pct):.1f}" y2="{mt+ph}" stroke="var(--steel)" stroke-dasharray="2,3"/><text class="lab" x="{X(lim_design_pct)-4:.1f}" y="{mt+ph-6}" text-anchor="end" style="fill:var(--steel)">ASCE 7 design limit {lim_design_pct:.2f}%</text>')
    o.append(f'<rect x="{ml+pw-196}" y="{mt+18}" width="194" height="62" fill="var(--sheet)" opacity=".92"/>')
    k = [0]
    def line(vals, col, dash, w, lab):
        if not vals: return
        pts = " ".join(f"{X(v):.1f},{Y(i):.1f}" for i, v in enumerate(vals)); o.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="{w}" stroke-dasharray="{dash}"/>')
        o.append(f'<text class="lab" x="{ml+pw-6}" y="{mt+32+14*k[0]}" text-anchor="end" style="fill:{col}">{lab}</text>'); k[0] += 1
    line(st_profile, "var(--steel)", "", 1.6, f"Steltic C<tspan font-size=\"9\">d</tspan>δ<tspan font-size=\"9\">e</tspan>/I<tspan font-size=\"9\">e</tspan>, {dirn}")
    line(po_profile, "var(--hot)", "", 2, f"Pushover at δ<tspan font-size=\"9\">t</tspan> BSE-2N, {dirn}")
    line(nl_mean, "var(--teal)", "", 2.2, f"NLRHA suite mean, {dirn}")
    line(nl_max, "var(--teal)", "4,3", 1.2, f"NLRHA record maximum, {dirn}")
    o.append("</svg>"); return "".join(o)


def svg_lambda(sel):
    W, H = 560, 260; ml, mr, mt, mb = 44, 16, 16, 34; pw, ph = W - ml - mr, H - mt - mb
    xmax = max(max(abs(p[1]) for p in r["hist"]) for r, _, _ in sel) * 1.05; ymax = max(r["lambda_u"] for r, _, _ in sel) * 1.18
    X = lambda x: ml + x / xmax * pw; Y = lambda y: mt + ph - y / ymax * ph
    o = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-width:{W}px;display:block">']
    for i in range(5):
        y = ymax * i / 4; o.append(f'<line x1="{ml}" y1="{Y(y):.1f}" x2="{ml+pw}" y2="{Y(y):.1f}" stroke="var(--grid)"/><text class="ax" x="{ml-6}" y="{Y(y)+4:.1f}" text-anchor="end">{y:.1f}</text>')
        x = xmax * i / 4; o.append(f'<text class="ax" x="{X(x):.1f}" y="{H-14}" text-anchor="middle">{x:.0f}</text>')
    o.append(f'<line x1="{ml}" y1="{Y(1):.1f}" x2="{ml+pw}" y2="{Y(1):.1f}" stroke="var(--muted)" stroke-dasharray="2,4"/><text class="lab" x="{ml+pw-2}" y="{Y(1)-4:.1f}" text-anchor="end">λ = 1.0 · factored ASCE 7 loads</text>')
    o.append(f'<text class="lab" x="{ml+pw}" y="{H-2}" text-anchor="end">control displacement |Δ| (in)</text><text class="lab" x="{ml}" y="{mt-4}">λ</text>')
    for k, (r, col, kind) in enumerate(sel):
        pts = " ".join(f"{X(abs(p[1])):.1f},{Y(p[0]):.1f}" for p in r["hist"]); o.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
        px, py = max(r["hist"], key=lambda p: p[0]); o.append(f'<circle cx="{X(abs(py)):.1f}" cy="{Y(px):.1f}" r="3.5" fill="{col}"/>')
        o.append(f'<text class="lab" x="{ml+6}" y="{mt+14+14*k}" style="fill:{col}">{html.escape(r["label"])} · λ<tspan font-size="9">u</tspan> {r["lambda_u"]:.2f} ({kind})</text>')
    o.append("</svg>"); return "".join(o)


# --------------------------------------------------------------------------------------------- the sheet
def build(job, out_name="four_analyses.html", title=None):
    job = os.path.abspath(job)
    st = steltic_facts(job)
    po = _load_json(os.path.join(job, "pushover", "pushover_package.json"))
    nl = _load_json(os.path.join(job, "nlrha", "nlrha_package.json"))
    dd = _load_json(os.path.join(job, "ddm_results.json"))
    prm = _load_json(os.path.join(job, "pushover", "hinge_params_used.json"), {})
    name = st["building"]
    basis = (po or nl or {}).get("basis", {})
    rc = (nl or {}).get("limits", {}).get("risk_category") or ("IV" if (basis.get("Ie") or 1.0) >= 1.5 else ("III" if (basis.get("Ie") or 1.0) >= 1.25 else "I_II"))
    date = datetime.date.today().isoformat()
    summary = dict(building=name, date=date, risk_category=rc, basis=basis, steltic={}, pushover={}, nlrha={}, ddm={})

    # ---------------- Steltic tile
    if st["dc_max"]:
        dcm = st["dc_max"]; sec = dcm["inputs"].get("section", "")
        st_v = f"D/C {dcm['DC']:.2f}"; st_vl = f"governing member {dcm['id']} ({sec}), {dcm['inputs'].get('governing_combo', '')}"
    else:
        st_v, st_vl = "—", "calc_package.json has no D/C values"
    drift_max_st = max(max(st["drift_X"] or [0]), max(st["drift_Y"] or [0])) if st["drift_X"] else None
    st_foot = []
    if st["V_kip"]: st_foot.append(f"Design base shear V = {st['V_kip']:,.0f} kip (C<sub>s</sub> {st['Cs']:.4f}, W = {st['W_kip']:,.0f} kip)")
    if st["wind_X"]: st_foot.append(f"wind base shear {st['wind_X']:,.0f} / {st['wind_Y']:,.0f} kip")
    if drift_max_st is not None: st_foot.append(f"design drift C<sub>d</sub>δ<sub>e</sub>/I<sub>e</sub> up to {drift_max_st:.2f}% vs the {st['drift_limit_pct']:.2f}% limit")
    if st["members"]: st_foot.append(f"{len(st['members'])} member groups checked, all with cited AISC 360/341 clauses")
    summary["steltic"] = dict(dc_max=st["dc_max"]["DC"] if st["dc_max"] else None, drift_max_pct=drift_max_st, drift_limit_pct=st["drift_limit_pct"], V_kip=st["V_kip"], W_kip=st["W_kip"])

    # ---------------- Pushover
    if po:
        dirs = po["directions"]; lv1, lv2 = RC_BPON[rc]
        def wd(d, lvl, key): return d["acceptance"].get(lvl, {}).get("worst_DC", {}).get(key)
        om = " / ".join(fmt(d["p695"].get("Omega"), 1) for d in dirs.values())
        vmx = " / ".join(f"{d['p695']['Vmax_kip']:,.0f}" for d in dirs.values())
        dt2 = " / ".join(f"{d['nsp']['BSE-2N']['target_disp_in']:.1f}" for d in dirs.values())
        bpon1 = " / ".join(fmt(wd(d, "BSE-1N", lv1)) for d in dirs.values()); bpon2 = " / ".join(fmt(wd(d, "BSE-2N", lv2)) for d in dirs.values())
        bpon_ok = all((wd(d, "BSE-1N", lv1) or 0) <= 1.0 and (wd(d, "BSE-2N", lv2) or 0) <= 1.0 for d in dirs.values())
        nsp_ok = all(n.get("nsp_permitted", True) for d in dirs.values() for n in d["nsp"].values())
        tails = {k: d.get("tail", {}).get("status", "") for k, d in dirs.items()}
        stats = po.get("hinge_stats", {})
        po_v = f"Ω {om}"; po_vl = f"V<sub>max</sub> {vmx} kip vs V = {fmt(po['basis'].get('V_design_kip'), 0)} kip ({' / '.join(dirs)})"
        po_foot = (f"BPON for Risk Category {rc.replace('_', '/')}: {lv1} at BSE-1N D/C {bpon1}; {lv2} at BSE-2N D/C {bpon2} — {'both pass' if bpon_ok else 'NOT satisfied'}. "
                   f"δ<sub>t</sub> BSE-2N {dt2} in. NSP {'permitted' if nsp_ok else 'NOT permitted (μstrength > μmax) — NDP required'}; "
                   f"hinges: {stats.get('col', 0)} column, {stats.get('beam', 0)} beam, {stats.get('brace_nonlinear', 0)} brace; descending branch {', '.join(f'{k} {v}' for k, v in tails.items())}. "
                   f"Component parameters {'verified' if po.get('params_verified') else 'UNVERIFIED placeholders'}.")
        summary["pushover"] = dict(Omega={k: d["p695"].get("Omega") for k, d in dirs.items()}, Vmax={k: d["p695"]["Vmax_kip"] for k, d in dirs.items()},
                                   target_disp_BSE2N={k: d["nsp"]["BSE-2N"]["target_disp_in"] for k, d in dirs.items()}, bpon_levels=[lv1, lv2], bpon_ok=bpon_ok, nsp_permitted=nsp_ok,
                                   max_story_drift_BSE2N={k: d["acceptance"]["BSE-2N"]["max_story_drift"] for k, d in dirs.items()}, tail=tails, params_verified=po.get("params_verified"))
    else:
        po_v, po_vl, po_foot = "not run", "", "pushover/pushover_package.json not found in the job folder."

    # ---------------- NLRHA
    if nl:
        V = nl["verdict"]; L = nl["limits"]; recs = nl["per_record"]; story = nl["story"]
        ok_recs = [r for r in recs if not r["unacceptable"]] or recs
        mean_X = [100 * s["mean_X"] for s in story]; mean_Y = [100 * s["mean_Y"] for s in story]; max_X = [100 * (s["max_X"] or 0) for s in story]; max_Y = [100 * (s["max_Y"] or 0) for s in story]
        roof_mean = [sum(r["peak_roof_in"][i] for r in ok_recs) / len(ok_recs) for i in (0, 1)]
        dg = nl["deformation_groups"]; worst_def = max(dg, key=lambda g: g["DC_CP"]) if dg else None
        fc = nl["force_controlled_columns"]; worst_fc = max(fc, key=lambda c: c["DC"]) if fc else None
        sfs = [r["sf"] for r in nl["ground_motions"]["selected"]]
        nl_verdict = "ACCEPTABLE" if V["overall"] else "NOT ACCEPTABLE"
        nl_v = nl_verdict; nl_vl = f"{V['n_unacceptable']} unacceptable of {V['n_records']} (allowed {V['unacceptable_allowed']}) · mean drift {pct(V['mean_drift_max'])} vs {pct(L['mean_limit'])}"
        damp = nl["ch16"].get("damping", {})
        nl_foot = (f"{V['n_records']} pairs scaled to MCE<sub>R</sub> (SF {min(sfs):.2f}–{max(sfs):.2f}), {damp.get('integrator', 'newmark').upper()} dt {damp.get('dt_s', 0.02)} s, ξ {100*damp.get('xi_used', 0.025):.1f}%. "
                   f"Record peak drifts {100*min(r['peak_drift'] for r in ok_recs):.2f}–{100*max(r['peak_drift'] for r in ok_recs):.2f}%. "
                   + (f"Deformation-controlled CP D/C {worst_def['DC_CP']:.2f} ({worst_def['kind']} {worst_def['section']}); " if worst_def else "")
                   + (f"force-controlled columns D/C {worst_fc['DC']:.2f}. " if worst_fc else "")
                   + f"Risk Category {rc.replace('_', '/')} rules (Table 12.12-1 row {L.get('table_12_12_1', 0.02):.3f}).")
        summary["nlrha"] = dict(verdict=nl_verdict, n_records=V["n_records"], n_unacceptable=V["n_unacceptable"], mean_drift_max=V["mean_drift_max"], mean_limit=L["mean_limit"],
                                roof_mean_in=roof_mean, worst_DC_CP=(worst_def["DC_CP"] if worst_def else None), worst_DC_force_controlled=(worst_fc["DC"] if worst_fc else None), retried=[r["label"] for r in recs if r.get("retry")])
    else:
        nl_v, nl_vl, nl_foot = "not run", "", "nlrha/nlrha_package.json not found in the job folder."
        mean_X = mean_Y = max_X = max_Y = None; roof_mean = None

    # ---------------- DDM
    if dd:
        runs = dd["runs"]; checked = [r for r in runs if r["check"] and r["check"][0] is not None]
        gov = min(checked, key=lambda r: r["check"][0]) if checked else min(runs, key=lambda r: r["lambda_u"])
        n_pass = sum(1 for r in checked if r["check"][1] == "PASS")
        gate = dd.get("gate", {})
        dd_v = f"φ<sub>s</sub>λ<sub>u</sub> {fmt(gov['check'][0])}" if checked else f"λ<sub>u</sub> {gov['lambda_u']:.2f}"
        dd_vl = f"governing: {html.escape(gov['label'])}, λ<sub>u</sub> = {gov['lambda_u']:.2f}" + (f", φ<sub>s</sub> = {fmt(gov['phi'].get('phi_s'))} ({gov['phi'].get('cls')}{', ' + gov['phi']['status'] if gov['phi'].get('status') else ''})" if checked else "")
        seis_na = [r for r in runs if r["check"] and r["check"][1] == "n/a"]
        dd_foot = (f"{n_pass} of {len(checked)} checked combinations pass" + (f"; {len(seis_na)} seismic cases reported without φ<sub>s</sub> (R &gt; 3, outside the calibrations)" if seis_na else "") + ". "
                   + f"Governing mechanism: {html.escape(gov['cls']['mechanism'][:120])}. Transfer gate {'passed' if gate.get('ok') else 'FAILED'} (worst {max(abs(r['ratio']-1) for r in gate.get('rows', [{'ratio': 1}]))*100:.1f}%).")
        summary["ddm"] = dict(governing=gov["label"], lambda_u=gov["lambda_u"], phi_lambda=gov["check"][0] if gov["check"] else None, n_pass=n_pass, n_checked=len(checked), gate_ok=gate.get("ok"))
    else:
        dd_v, dd_vl, dd_foot = "not run", "", "ddm_results.json not found in the job folder."

    # ---------------- quantities table
    def row(q, a, b, c, d, reading): return f'<tr><td>{q}</td><td class="num">{a}</td><td class="num">{b}</td><td class="num">{c}</td><td class="num">{d}</td><td>{reading}</td></tr>'
    Q = []
    T_st = None
    t_rep = _text(os.path.join(job, "report.html")); m = re.search(r"Mode T \(s\) mX % ΣmX % mY % ΣmY % 1 ([\d.]+) [\d.]+ [\d.]+ [\d.]+ [\d.]+ 2 ([\d.]+)", t_rep)
    if m: T_st = f"{float(m.group(2)):.2f} / {float(m.group(1)):.2f} (modes 2 / 1)"
    Q.append(row("Fundamental periods (s)", T_st or "—", (" / ".join(f"{d['T1']:.2f}" for d in po["directions"].values()) if po else "—"), (f"{nl['modal']['T1x']:.2f} / {nl['modal']['T1y']:.2f}" if nl else "—"),
                 (f"gate {'ok' if dd.get('gate', {}).get('ok') else 'FAIL'}" if dd else "—"), "Independent model builds of the same package; the hinge model is the design model with springs added."))
    if st["V_kip"]:
        Q.append(row("Design lateral force (kip)", f"{st['V_kip']:,.0f} seismic" + (f" · {st['wind_X']:,.0f} / {st['wind_Y']:,.0f} wind" if st["wind_X"] else ""),
                     (" / ".join(f"V<sub>y</sub> {d['nsp']['BSE-2N']['Vy']:,.0f}" for d in po["directions"].values()) if po else "—"), "—", "λ = 1.0 on each combination" if dd else "—",
                     ("Effective yield strength is " + " / ".join(f"{d['nsp']['BSE-2N']['Vy']/st['V_kip']:.1f}" for d in po["directions"].values()) + " × the R-reduced design shear.") if po else ""))
    if po:
        Q.append(row("System strength / overstrength", f"Ω<sub>0</sub> = {fmt(po['basis'].get('Om0'), 1)}", f"V<sub>max</sub> {vmx} kip · Ω {om}", "—",
                     (f"λ<sub>u</sub> {min(r['lambda_u'] for r in dd['runs'] if r['kind'] != 'gravity'):.2f}–{max(r['lambda_u'] for r in dd['runs'] if r['kind'] != 'gravity'):.2f} (lateral cases)" if dd and any(r['kind'] != 'gravity' for r in dd['runs']) else "—"),
                     "Ω is measured at the system level without R, Ω<sub>0</sub> or C<sub>d</sub>. The DDM λ<sub>u</sub> of a lateral case scales gravity with the lateral pattern and is not an overstrength."))
        dt_row = " / ".join(f"{d['nsp']['BSE-2N']['target_disp_in']:.1f}" for d in po["directions"].values())
        nl_roof = (f"mean {roof_mean[0]:.1f} / {roof_mean[1]:.1f}" if nl else "—")
        rd = ""
        if nl and len(po["directions"]) == 2:
            devs = [(d["nsp"]["BSE-2N"]["target_disp_in"] - roof_mean[i]) / roof_mean[i] * 100 for i, d in enumerate(po["directions"].values())]
            rd = "The coefficient-method δ<sub>t</sub> sits " + " and ".join(f"{abs(v):.0f}% {'above' if v > 0 else 'below'}" for v in devs) + " the record mean (X, Y); the suite adds the record-to-record scatter."
        Q.append(row("MCE<sub>R</sub>-level roof displacement (in)", "—", f"δ<sub>t</sub> {dt_row}", nl_roof, "—", rd))
    if st["drift_X"] or po or nl:
        Q.append(row("Max storey drift", (f"design {max(st['drift_X']):.2f}% / {max(st['drift_Y']):.2f}% (DE, C<sub>d</sub>δ<sub>e</sub>/I<sub>e</sub>)" if st["drift_X"] else "—"),
                     (" / ".join(f"{100*d['acceptance']['BSE-2N']['max_story_drift']:.2f}%" for d in po["directions"].values()) + " at δ<sub>t</sub> BSE-2N" if po else "—"),
                     (f"mean {max(mean_X):.2f}% / {max(mean_Y):.2f}% (peaks {max(max_X):.2f}% / {max(max_Y):.2f}%)" if nl else "—"), "—",
                     (f"Chapter 16 mean limit {pct(nl['limits']['mean_limit'])} for Risk Category {rc.replace('_','/')}" + (f"; ASCE 7 design limit {st['drift_limit_pct']:.2f}%." if st["drift_limit_pct"] else ".") if nl else "")))
    if nl:
        Q.append(row("Deformation-controlled components", "—", (" / ".join(f"CP D/C {fmt(d['acceptance']['BSE-2N']['worst_DC'].get('CP'))}" for d in po["directions"].values()) if po else "—"),
                     f"mean CP D/C {worst_def['DC_CP']:.2f} · valid-range {worst_def['DC_valid']:.2f}" if worst_def else "—", "—", "Both nonlinear seismic methods use the same hinge and brace backbones; NLRHA checks the mean of the record peaks per group (16.4.2.2)."))
        Q.append(row("Force-controlled columns", (f"D/C ≤ {max(x['DC'] for x in st['members'] if 'col' in x['id']):.2f}" if any('col' in x['id'] for x in st['members']) else "—"), "P<sub>G</sub>/P<sub>ye</sub> screened (> 0.6 → force-controlled)",
                     f"D/C {worst_fc['DC']:.2f} ({worst_fc['section']})" if worst_fc else "—", (f"λ<sub>u</sub> {summary['ddm']['lambda_u']:.2f} ({html.escape(summary['ddm']['governing'])})" if dd else "—"), "Chapter 16 Eq. 16.4-1 with γ = 1.3 on the mean column axial demand; the DDM asks the gravity-system question directly."))
    Q.append(row("Component parameters", "AISC 360/341 cited per member", ("verified · " + html.escape(str(prm.get("source", ""))[:90])) if po and po.get("params_verified") else ("UNVERIFIED placeholders" if po else "—"),
                 ("same file" if nl else "—"), (f"φ<sub>s</sub> {gov['phi'].get('status', '')}" if dd and checked else "—"), "Parameters live with the job outputs (pushover/hinge_params_used.json); the bots query the standards through Query file manager."))

    # ---------------- figures
    figs = []
    if po:
        figs.append(f"<figure>{svg_curves(po, st['V_kip'] or po['basis'].get('V_design_kip'))}<figcaption>Capacity curves with the ASCE 41 target displacements and the R-reduced design base shear.</figcaption></figure>")
    if po or nl or st["drift_X"]:
        # direction with the larger NLRHA mean drift (else pushover, else Y)
        dirn = "Y"
        if nl: dirn = "X" if max(mean_X) > max(mean_Y) else "Y"
        elif po: dirn = max(po["directions"], key=lambda k: po["directions"][k]["acceptance"]["BSE-2N"]["max_story_drift"])
        stp = (st["drift_X"] if dirn == "X" else st["drift_Y"]) if st["drift_X"] else None
        pop = [100 * x["drift_ratio"] for x in po["directions"][dirn]["acceptance"]["BSE-2N"]["story_drifts"]] if po and dirn in po["directions"] else None
        figs.append(f"<figure>{svg_drifts(stp, pop, (mean_X if dirn == 'X' else mean_Y) if nl else None, (max_X if dirn == 'X' else max_Y) if nl else None, 100*nl['limits']['mean_limit'] if nl else None, st['drift_limit_pct'], dirn)}"
                    f"<figcaption>Storey drift profiles, direction {dirn}: design drift, pushover at δ<sub>t</sub> BSE-2N, Chapter 16 suite mean and record maximum, with the ASCE 7 design limit and the Chapter 16 mean limit.</figcaption></figure>")
    if dd:
        grav = [r for r in dd["runs"] if r["kind"] == "gravity"]; lat = [r for r in dd["runs"] if r["kind"] != "gravity"]
        sel = []
        if grav: sel.append((min(grav, key=lambda r: r["lambda_u"]), "var(--gold)", "gravity"))
        for kind, col in (("seismic", "var(--hot)"), ("wind", "var(--steel)")):
            c = [r for r in lat if r["kind"] == kind]
            if c: sel.append((min(c, key=lambda r: r["lambda_u"]), col, kind))
        figs.append(f"<figure>{svg_lambda(sel)}<figcaption>λ–Δ curves of the governing gravity, seismic and wind combinations (peak marked). Proportional λ scales gravity with the lateral pattern.</figcaption></figure>")

    # ---------------- detail tables
    det = []
    if nl:
        det.append(f"<h3>Chapter 16 records (period range {nl['ground_motions']['T_lower']:.2f}–{nl['ground_motions']['T_upper']:.2f} s, suite mean / target ≥ {nl['ground_motions']['min_ratio_in_range']:.3f})</h3><div class=\"tbl\"><table><tr><th>Record</th><th class=\"num\">SF</th><th class=\"num\">peak storey drift</th><th class=\"num\">peak roof X / Y (in)</th><th class=\"num\">residual</th><th>16.4.1.1</th></tr>")
        for r in recs:
            s_ = ("acceptable" if not r["unacceptable"] else pill("warn", "unacceptable") + " " + html.escape("; ".join(r["flags"]))) + (' <span class="k">retried at a finer step</span>' if r.get("retry") else "")
            roofs = (f"{r['peak_roof_in'][0]:.1f} / {r['peak_roof_in'][1]:.1f}") if r["peak_roof_in"] else "—"
            det.append(f'<tr><td>{html.escape(r["label"])}</td><td class="num">{r["sf"]:.2f}</td><td class="num">{pct(r["peak_drift"]) if r["converged"] else "—"}</td><td class="num">{roofs}</td><td class="num">{pct(r["residual"]) if r["residual"] is not None else "—"}</td><td>{s_}</td></tr>')
        det.append("</table></div>")
        det.append('<h3>Element checks (16.4.2) — worst groups</h3><div class="tbl"><table><tr><th>Group</th><th class="num">mean peak deformation</th><th class="num">CP</th><th class="num">D/C CP</th><th class="num">D/C b</th></tr>')
        for g in sorted(dg, key=lambda g: -g["DC_CP"])[:8]:
            if g["kind"] == "brace":
                det.append(f'<tr><td>brace · {g["section"]} · z = {g["z_in"]:.0f} in ({g["n"]})</td><td class="num">{g["Qu_comp_in"]:.3f} / {g["Qu_tens_in"]:.3f} in (c / t)</td><td class="num">{g["CP_comp"]:.3f} / {g["CP_tens"]:.3f}</td><td class="num">{g["DC_CP"]:.2f}</td><td class="num">{g["DC_valid"]:.2f}</td></tr>')
            else:
                det.append(f'<tr><td>{g["kind"]} · {g["section"]} · z = {g["z_in"]:.0f} in ({g["n"]})</td><td class="num">{g["Qu_rad"]:.4f} rad</td><td class="num">{g["CP"]:.4f}</td><td class="num">{g["DC_CP"]:.2f}</td><td class="num">{g["DC_valid"]:.2f}</td></tr>')
        det.append("</table></div>")
    if dd:
        det.append('<h3>DDM sweeps</h3><div class="tbl"><table><tr><th>Combination</th><th>kind</th><th class="num">λ<sub>u</sub></th><th class="num">first yield λ</th><th class="num">φ<sub>s</sub> class</th><th class="num">φ<sub>s</sub>λ<sub>u</sub></th><th>mechanism at the peak</th></tr>')
        for r in dd["runs"]:
            chk = r["check"] or (None, "n/a"); ph = r["phi"]
            det.append(f'<tr><td>{html.escape(r["label"])}</td><td>{r["kind"]}</td><td class="num">{r["lambda_u"]:.2f}</td><td class="num">{fmt(r.get("first_yield"))}</td><td class="num">{ph.get("cls")} · {fmt(ph.get("phi_s"))}</td><td class="num">{fmt(chk[0])} {chk[1]}</td><td>{html.escape(r["cls"]["mechanism"][:95])}</td></tr>')
        det.append("</table></div>")

    # ---------------- disclosures (from the tool outputs)
    disc = []
    if po:
        pcr = (po.get("numerics") or {}).get("post_cap_ratio")
        if pcr and pcr > 0.2: disc.append(f"Pushover / NLRHA hinge backbones: post-capping descent spread over {pcr:.2f}a (tail-protocol rung 3, default 0.15a).")
        for k, v in tails.items():
            if v and v != "captured": disc.append(f"Push {k}: descending branch {v} — δ<sub>u</sub> and μ<sub>T</sub> are lower bounds.")
        if not po.get("params_verified"): disc.append("Pushover / NLRHA component parameters are UNVERIFIED placeholders (red banner in both reports).")
    if nl:
        d_ = nl["ch16"].get("damping", {})
        disc.append(f"Chapter 16 integration: {d_.get('integrator', 'newmark').upper()}, dt {d_.get('dt_s', 0.02)} s, ξ {100*d_.get('xi_used', 0.025):.1f}%; cyclic deterioration Λ = 0 (16.3.1 open item).")
        for r in recs:
            if r.get("retry"): disc.append(f"Record {html.escape(r['label'])}: re-run at a finer time step after non-convergence ({'converged' if r['converged'] else 'still non-converged'}).")
    if dd:
        provs = sorted({r["phi"].get("status", "") for r in dd["runs"] if r["phi"].get("phi_s") is not None})
        disc.append("DDM φ<sub>s</sub> classes used: " + ", ".join(f"{r['phi']['cls']} = {fmt(r['phi']['phi_s'])} ({r['phi'].get('status', '')})" for r in {r['phi']['cls']: r for r in dd['runs'] if r['phi'].get('phi_s') is not None}.values()) + ".")

    css = open(os.path.join(_HERE, "four_analyses.css"), encoding="utf-8").read()
    title = title or f"{name} Four Analyses"
    sys_ = basis.get("system") or (st["capacity_design"].get("system") if isinstance(st["capacity_design"], dict) else "") or ""
    hn = None
    if nl and nl["basis"].get("heights_in"): hn = sum(nl["basis"]["heights_in"]) / 12
    elif po and po.get("gravity"): hn = po["gravity"][-1]["z_in"] / 12
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(title)}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap">
<style>{css}</style></head><body>
<div class="wrap">
<header class="tb">
  <div class="main">
    <div class="eyebrow">Steltic_nonlinear · {html.escape(name)} · four solution packages</div>
    <h1>{html.escape(name)}: the design package and its three nonlinear checks, side by side</h1>
    <p class="lede">The Steltic member design (AISC 360/341 LRFD) and the three SNL analyses run on the same package — the Pushover Analyst (ASCE 41-23 NSP with AISC 342-22 component parameters), the Non Linear Dynamic analysis (ASCE 7-22 Chapter 16) and the DDM system-capacity analysis (GMNIA). Every number on this sheet is read from the package files; the readings are rule-based. The engineering narrative that interprets them is the SNL bot's, delivered with this sheet.</p>
  </div>
  <div class="meta">
    <div>Building</div><div>{html.escape(name)}{f' · h<sub>n</sub> = {hn:.0f} ft' if hn else ''}{f' · W = {basis["W_kip"]:,.0f} kip' if basis.get('W_kip') else ''}</div>
    <div>System</div><div>{html.escape(str(sys_))} · R {fmt(basis.get('R'), 1)} · C<sub>d</sub> {fmt(basis.get('Cd'), 1)} · Ω<sub>0</sub> {fmt(basis.get('Om0'), 1)}</div>
    <div>Site / RC</div><div>S<sub>DS</sub> {fmt(basis.get('SDS'))} g · S<sub>D1</sub> {fmt(basis.get('SD1'))} g · Risk Category {rc.replace('_', '/')} · I<sub>e</sub> {fmt(basis.get('Ie'), 2)}</div>
    <div>Packages</div><div>report.html · pushover/ · nlrha/ · ddm_report.html · steltic_viewer_bundle.html</div>
    <div>Date</div><div>{date} · not for construction</div>
  </div>
</header>

<section class="sheet">
  <div class="sheet-head"><h2>Four verdicts on the same steel</h2><span class="no">SHEET 1</span></div>
  <div class="tiles">
    <div class="tile"><div class="who">Steltic</div><div class="what">AISC 360/341-22 LRFD · ASCE 7-22 Ch. 12</div><div class="v">{st_v}</div><div class="vl">{st_vl}</div><div class="foot">{'. '.join(st_foot)}.</div></div>
    <div class="tile po"><div class="who">Pushover Analyst</div><div class="what">ASCE 41-23 NSP · AISC 342-22</div><div class="v">{po_v}</div><div class="vl">{po_vl}</div><div class="foot">{po_foot}</div></div>
    <div class="tile nl"><div class="who">Non Linear Dynamic</div><div class="what">ASCE 7-22 Chapter 16 NLRHA</div><div class="v">{nl_v}</div><div class="vl">{nl_vl}</div><div class="foot">{nl_foot}</div></div>
    <div class="tile ddm"><div class="who">DDM (GMNIA)</div><div class="what">system capacity · φ<sub>s</sub>λ<sub>u</sub> ≥ 1</div><div class="v">{dd_v}</div><div class="vl">{dd_vl}</div><div class="foot">{dd_foot}</div></div>
  </div>
</section>

<section class="sheet">
  <div class="sheet-head"><h2>What each package answers</h2><span class="no">SHEET 2</span></div>
  <div class="tbl"><table>
    <tr><th></th><th>Steltic {pill('st','design')}</th><th>Pushover {pill('po','NSP')}</th><th>NLRHA {pill('nl','Ch. 16')}</th><th>DDM {pill('ddm','GMNIA')}</th></tr>
    <tr><td>Question</td><td>Does every member and connection have LRFD strength for every ASCE 7 combination, and does the R-reduced, C<sub>d</sub>-amplified drift meet Table 12.12-1?</td><td>Which mechanism forms, how strong is the system, and does the deformation at the ASCE 41 target displacement satisfy the BPON levels for the Risk Category ({RC_BPON_NOTE[rc]})?</td><td>Under ≥ 11 MCE<sub>R</sub> pairs, are mean storey drifts within 2 × Table 12.12-1, are the responses acceptable, and are elements within their deformation / force limits?</td><td>By how much can each factored combination be scaled before the imperfect, residual-stressed structure loses stability?</td></tr>
    <tr><td>Demand</td><td>Elastic P-Δ analysis, R-reduced seismic, factored wind; the full §2.3 combination set</td><td>First-mode push; δ<sub>t</sub> = C<sub>0</sub>C<sub>1</sub>C<sub>2</sub>S<sub>a</sub>T<sub>e</sub>²/4π² (ASCE 41-23 Eq. 7-29); BSE-2N = MCE<sub>R</sub></td><td>Recorded pairs, both components, RotD100-scaled to 1.5 × the design spectrum, ±10% orientation balance</td><td>The same combinations applied proportionally with a load factor λ</td></tr>
    <tr><td>Capacity</td><td>AISC 360 φR<sub>n</sub>; AISC 341 capacity design; AISC 358 connections</td><td>Concentrated hinges (AISC 342-22 Ch. C tables) and phenomenological braces; F<sub>ye</sub> = R<sub>y</sub>F<sub>y</sub></td><td>Same hinge/brace model; CP and valid range b per group; force-controlled columns by Eq. 16.4-1, γ = 1.3</td><td>Fibre sections with residual stresses, L/1000 bows, H/500 out-of-plumb; peak of the λ–Δ curve</td></tr>
    <tr><td>Uses R, Ω<sub>0</sub>, C<sub>d</sub>?</td><td>Yes</td><td>No — reports Ω and μ<sub>T</sub></td><td>No — direct MCE<sub>R</sub> demand</td><td>No — φ<sub>s</sub> by target reliability</td></tr>
    <tr><td>Cannot see</td><td>Mechanism, hinge rotations, real overstrength, MCE<sub>R</sub>-level drift</td><td>Higher modes, cyclic demand, duration, record scatter, dynamic instability</td><td>Gravity-only and wind limit states; anything beyond the records; foundations</td><td>Dynamic response and cyclic degradation; it scales the code load pattern, gravity included</td></tr>
    <tr><td>Spec authority</td><td>AISC 360-22, 341-22, 358-22; ASCE 7-22 Ch. 11–12</td><td>ASCE 41-23 Ch. 7 → AISC 342-22 Ch. C (via ASCE 7 §1.3.1.3)</td><td>ASCE 7-22 Ch. 16; AISC 342-22 for component models</td><td>AISC 360-22 App. 1 §1.3; φ<sub>s</sub> from the Sydney system-reliability calibrations</td></tr>
  </table></div>
</section>

<section class="sheet">
  <div class="sheet-head"><h2>The same quantities, four ways</h2><span class="no">SHEET 3</span></div>
  <div class="tbl"><table><tr><th>Quantity</th><th class="num">Steltic</th><th class="num">Pushover</th><th class="num">NLRHA</th><th class="num">DDM</th><th>Reading</th></tr>{''.join(Q)}</table></div>
  {''.join(figs)}
</section>

<section class="sheet">
  <div class="sheet-head"><h2>Suite and sweeps in full</h2><span class="no">SHEET 4</span></div>
  {''.join(det) if det else '<p class="na">No NLRHA or DDM results in this job folder.</p>'}
</section>

<section class="sheet">
  <div class="sheet-head"><h2>Disclosures read from the outputs</h2><span class="no">SHEET 5</span></div>
  <ul>{''.join(f'<li>{d}</li>' for d in disc) or '<li class="na">none</li>'}</ul>
  <p class="src">Files: report.html · viewer_3d.html · pushover/ (pushover_report.html, pushover_package.json, hinge_params_used.json, pushover_viewer_3d.html) · nlrha/ (nlrha_report.html, nlrha_package.json, gm_scaling.json, nlrha_viewer_3d.html) · ddm_report.html, ddm_results.json, ddm_viewer_3d.html · steltic_viewer_bundle.html · snl_summary.json · this sheet.</p>
  <div class="disc">Prototype analyses produced by analysis engines and AI agents (Steltic_nonlinear). Not for construction. Every result requires review and sealing by a licensed professional engineer; the ASCE 7-22 Chapter 16 procedure additionally requires project-specific design criteria (16.1.4) and independent design review (16.5). φ<sub>s</sub> is a literature value with no specification clause.</div>
</section>
</div></body></html>"""
    out = os.path.join(job, out_name)
    open(out, "w", encoding="utf-8").write(doc)
    json.dump(summary, open(os.path.join(job, "snl_summary.json"), "w"), indent=1, default=str)
    return out
