"""
report_ddm.py -- the DDM System Capacity Report (self-contained HTML) + the `ddm_analysis` block.
"""
import datetime, hashlib, html, json, os
from . import phi_s


def _h(s):
    return html.escape(str(s))


def _sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:12]
    except Exception:
        return "n/a"


def curve_svg(hist, lam_u, label, w=460, h=220):
    """lambda vs |control displacement| curve as inline SVG."""
    if not hist:
        return ""
    xs = [abs(d) for _, d in hist]; ys = [l for l, _ in hist]
    xmax = max(xs) * 1.05 or 1.0; ymax = max(ys) * 1.15 or 1.0
    L, R, T, B = 46, 14, 14, 34
    def X(v): return L + v / xmax * (w - L - R)
    def Y(v): return T + (1 - v / ymax) * (h - T - B)
    pts = " ".join("%.1f,%.1f" % (X(x), Y(y)) for x, y in zip(xs, ys))
    yt = "".join('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="g"/><text x="%d" y="%.1f" class="t" text-anchor="end">%.2f</text>'
                 % (L, w - R, Y(v), Y(v), L - 4, Y(v) + 3, v) for v in _ticks(ymax))
    xt = "".join('<text x="%.1f" y="%d" class="t" text-anchor="middle">%.1f</text>' % (X(v), h - B + 14, v) for v in _ticks(xmax))
    one = '<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="one"/>' % (L, w - R, Y(1.0), Y(1.0)) if ymax > 1 else ""
    peak = '<circle cx="%.1f" cy="%.1f" r="4" class="pk"/>' % (X(xs[ys.index(max(ys))]), Y(max(ys)))
    return ('<svg viewBox="0 0 %d %d" class="curve"><title>%s</title>%s%s%s<polyline points="%s" class="c"/>%s'
            '<text x="%d" y="%d" class="t">|control disp| (in)</text><text x="4" y="%d" class="t">λ</text></svg>'
            % (w, h, _h(label), yt, xt, one, pts, peak, w // 2 - 30, h - 4, T + 4))


def _ticks(vmax, n=4):
    import math
    if vmax <= 0:
        return [0]
    raw = vmax / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min([1, 2, 2.5, 5, 10], key=lambda s: abs(s * mag - raw)) * mag
    return [i * step for i in range(int(vmax / step) + 1)]


CSS = """
:root{--bg:#F2F4F6;--ink:#1C232B;--ink2:#4A5563;--rule:#C9D1DA;--acc:#2F5D8A;--gold:#B8860B;--band:#E4EAF0;--ok:#2E7D4F;--bad:#B23A3A;--warn:#8A6D1D}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 "Source Sans 3","Segoe UI",system-ui,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:36px 24px 80px}
h1{font-family:Georgia,"IBM Plex Serif",serif;font-size:32px;line-height:1.15;margin:4px 0 6px}
h2{font-family:Georgia,serif;font-size:22px;margin:40px 0 10px;padding-top:14px;border-top:1px solid var(--rule)}
h3{font-size:17px;margin:22px 0 6px}
.eyebrow{font:12px/1.4 ui-monospace,Menlo,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--acc)}
.meta{font:13px ui-monospace,Menlo,monospace;color:var(--ink2);display:flex;gap:20px;flex-wrap:wrap;margin:8px 0 18px}
.flag{border-left:4px solid var(--gold);background:var(--band);padding:10px 14px;margin:14px 0;font-size:15px}
.verdict{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0}
.tile{background:#fff;border:1px solid var(--rule);padding:12px 14px}
.tile .k{font:11px ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--ink2)}
.tile .v{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}
.tw{overflow-x:auto;background:#fff;border:1px solid var(--rule);margin:8px 0 16px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 9px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{font:11px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;background:var(--band);color:var(--ink2)}
td{font-variant-numeric:tabular-nums}
.PASS{color:var(--ok);font-weight:600}.FAIL{color:var(--bad);font-weight:600}.na{color:var(--ink2)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.curve{width:100%;height:auto;background:#fff;border:1px solid var(--rule)}
.curve .g{stroke:var(--rule);stroke-width:1}.curve .one{stroke:var(--gold);stroke-width:1.5;stroke-dasharray:4 3}
.curve .c{fill:none;stroke:var(--acc);stroke-width:2}.curve .pk{fill:var(--gold)}.curve .t{font:10px ui-monospace,monospace;fill:var(--ink2)}
.cap{font-size:13px;color:var(--ink2);margin:4px 0 0}
code,pre{font-family:ui-monospace,Menlo,monospace;font-size:13px}pre{background:var(--band);padding:10px;overflow-x:auto}
ul{padding-left:1.2em}li{margin:3px 0}
"""


def phi_s_provisional(runs):
    """True when any combination checked used a phi_s class the policy flags provisional / extrapolated."""
    return any(r["phi"].get("phi_s") is not None and (r["phi"].get("provisional") or "provisional" in (r["phi"].get("status") or "")) for r in runs)


def phi_s_status_text(runs, as_html=True):
    """One sentence per phi_s class actually used: value, beta_T row, status and where it comes from (phi_s.py TABLE)."""
    seen = {}
    for r in runs:
        ph = r["phi"]
        if ph.get("phi_s") is None or ph["cls"] in seen:
            continue
        seen[ph["cls"]] = ph
    if not seen:
        return "No phi_s class applies to the combinations analysed (seismic-pattern cases on an R > 3 system carry no pass/fail)."
    bits = []
    for k, ph in seen.items():
        bits.append("%s = %.2f at beta_T %.2f [%s]" % (k, ph["phi_s"], ph["beta_T"], (ph["status"] or "").upper()))
    prov = [k for k, ph in seen.items() if ph.get("provisional") or "provisional" in (ph.get("status") or "")]
    tail = (" Classes %s are PROVISIONAL (scaled or abstract-level, not a calibrated value) -- keep the flag in any deliverable." % ", ".join(prov)) if prov \
        else " Every class used is verified from open documents (SSRC 2013 Table 10; Wan 2024 thesis Tables 6.10-6.14 / Eq. 6.9; Arrayago-Rasmussen-Zhang 2022 for the cited HSS/CFS wind values); see docs/PHI_S_SOURCES.md."
    t = "Classes used: " + "; ".join(bits) + "." + tail
    if as_html:
        t = t.replace("beta_T", "β<sub>T</sub>").replace("phi_s", "φ<sub>s</sub>")
    return t


def build(out_dir, nm, cfg, gate, runs, sensitivity, options, member_table, notes):
    """runs: list of dict(combo=..., summary=..., res=..., cls=..., phi=...)."""
    name = nm.name
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    prov = {f: _sha(os.path.join(nm.job_dir, f)) for f in ("cfg.py", "model_opensees.py", "design/calc_package.json")}
    strength = [r for r in runs if r["phi"]["phi_s"] is not None]
    worst = min(strength, key=lambda r: r["phi"]["phi_s"] * r["res"]["lambda_u"]) if strength else None
    n_pass = sum(1 for r in strength if r["check"][1] == "PASS")
    R = cfg.get("seis", {}).get("R")

    parts = []
    parts.append('<title>%s DDM Capacity</title><style>%s</style><div class="wrap">' % (_h(name), CSS))
    parts.append('<div class="eyebrow">Direct Design Method · system capacity report</div>')
    parts.append('<h1>%s — system capacity by advanced analysis</h1>' % _h(name))
    parts.append('<div class="meta"><span>%s</span><span>steltic_ddm 0.1</span><span>kip · in · ksi</span><span>openseespy GMNIA</span>'
                 '<span>cfg.py %s · model_opensees.py %s · calc_package.json %s</span></div>' % (now, prov["cfg.py"], prov["model_opensees.py"], prov["design/calc_package.json"]))
    statuses = sorted({r["phi"]["status"] for r in runs})
    parts.append('<div class="flag"><b>System resistance factors — status: %s.</b> %s φ<sub>s</sub> is a literature value — it has no '
                 'specification clause.</div>' % (", ".join(statuses), phi_s_status_text(runs)))

    # verdict tiles
    parts.append('<div class="verdict">')
    if worst:
        parts.append('<div class="tile"><div class="k">governing combination</div><div class="v" style="font-size:17px">%s</div></div>' % _h(worst["combo"][0]))
        parts.append('<div class="tile"><div class="k">λ<sub>u</sub> (governing)</div><div class="v">%.2f</div></div>' % worst["res"]["lambda_u"])
        parts.append('<div class="tile"><div class="k">φ<sub>s</sub>·λ<sub>u</sub> (governing)</div><div class="v %s">%.2f</div></div>' % (worst["check"][1], worst["check"][0]))
    parts.append('<div class="tile"><div class="k">DDM checks</div><div class="v">%d / %d pass</div></div>' % (n_pass, len(strength)))
    parts.append('<div class="tile"><div class="k">transfer gate</div><div class="v %s">%s</div></div></div>' % ("PASS" if gate["ok"] else "FAIL", "PASS" if gate["ok"] else "FAIL"))

    # 1 design basis
    parts.append('<h2>1 · Design of record and what was analysed</h2>')
    parts.append('<p>Source package: <code>%s</code>. Steltic design: <b>%s</b>; system <b>%s</b>; R = %s, C<sub>d</sub> = %s, Ω<sub>0</sub> = %s; '
                 'bases <b>%s</b>, joints <b>%s</b>, gravity framing <b>%s</b>; %d storeys, %d × %d bays @ %.0f × %.0f in. The DDM agent analysed the '
                 'building exactly as designed — no member was resized.</p>' % (
                     _h(nm.job_dir), _h(cfg.get("arch", "")), _h(cfg.get("system", "")), R, cfg.get("seis", {}).get("Cd"), cfg.get("seis", {}).get("Om0"),
                     cfg.get("model", {}).get("bases"), cfg.get("model", {}).get("joints"), cfg.get("model", {}).get("gravity"),
                     len(cfg["heights"]), cfg["NX"], cfg["NY"], cfg["SX"], cfg["SY"]))
    secs = sorted({(m.role, m.section) for m in nm.members})
    parts.append('<div class="tw"><table><tr><th>role</th><th>section</th><th>members</th><th>member-based D/C (Steltic)</th><th>governing combo (Steltic)</th></tr>')
    dcmap = {}
    for m in (nm.calc_package or {}).get("members", []):
        dcmap[(m["inputs"].get("role"), m["inputs"].get("section", "").upper())] = (m.get("DC"), m["inputs"].get("governing_combo"))
    for role, sec in secs:
        n = sum(1 for m in nm.members if m.role == role and m.section == sec)
        dc, gc = dcmap.get((role, sec.upper()), (None, None))
        parts.append('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td></tr>' % (role, sec, n, "%.3f" % dc if isinstance(dc, (int, float)) else "—", _h(gc or "—")))
    parts.append('</table></div>')

    # 2 transfer gate
    parts.append('<h2>2 · Transfer gate — the GMNIA rebuild is the Steltic building</h2>')
    parts.append('<p>Elastic-to-elastic comparison before any imperfection, residual stress or plasticity is switched on (tolerance ±%.0f %%). '
                 'Steltic model: %d members; GMNIA model: %d elements.</p>' % (100 * gate["tol"], gate["elements_steltic"], gate["elements_gmnia"]))
    parts.append('<div class="tw"><table><tr><th>quantity</th><th>Steltic</th><th>GMNIA (elastic)</th><th>ratio</th><th></th></tr>')
    for r in gate["rows"]:
        parts.append('<tr><td>%s</td><td>%.4f</td><td>%.4f</td><td>%.3f</td><td class="%s">%s</td></tr>' % (
            _h(r["quantity"]), r["steltic"], r["gmnia"], r["ratio"], "PASS" if r["ok"] else "FAIL", "ok" if r["ok"] else "FAIL"))
    parts.append('</table></div>')
    if gate.get("hint"):
        parts.append('<p class="FAIL">%s</p>' % _h(gate["hint"]))

    # 3 model statement
    o = options
    parts.append('<h2>3 · Nominal GMNIA model</h2><ul>')
    parts.append('<li><b>Elements:</b> %s, 3-D Corotational transformation, %d Gauss–Lobatto points; %d / %d / %d sub-elements per column / beam / brace.</li>'
                 % ("dispBeamColumn" if o.get("fast") else "forceBeamColumn", o.get("nip", 5), *o.get("nsub", (2, 2, 4))))
    parts.append('<li><b>Sections:</b> fibre W-shapes from d, t<sub>w</sub>, b<sub>f</sub>, t<sub>f</sub> (fillets ignored: A and I 1–3 %% low, conservative); rectangular HSS with A500 design thickness 0.93 t; %s.</li>'
                 % _h(", ".join("%s (%d fibres)" % (l, n) for _, l, _, n, _ in o.get("section_log", [])[:12])))
    parts.append('<li><b>Material:</b> Steel01, F<sub>y</sub> = %.0f ksi (nominal), E = 29 000 ksi, kinematic hardening b = %.3f.</li>' % (o.get("Fy", 50.0), o.get("hardening", 0.002)))
    parts.append('<li><b>Residual stresses:</b> %s (W: Galambos–Ketter linear pattern, σ<sub>rc</sub> = 0.3 F<sub>y</sub> at flange tips, self-equilibrating; HSS: provisional membrane pattern).</li>' % _h(o.get("residual")))
    parts.append('<li><b>Imperfections:</b> whole-building out-of-plumb ψ = 1/%d in the direction of the lateral load (gravity cases: %s); member out-of-straightness L/%d half-sine, weak axis for columns, out-of-plane for braces.</li>'
                 % (round(1 / o.get("psi", 1 / 500)), o.get("gravity_dirs", "+X and +Y"), round(1 / o.get("bow", 1 / 1000))))
    parts.append('<li><b>Joints:</b> as designed — Steltic beam releases become true pins (duplicate node + zeroLength, released rotation carries no stiffness); brace ends pinned in bending, torsion retained; braces do not share a node at the X-crossing (K = 1 on the full diagonal, conservative).</li>')
    parts.append('<li><b>Diaphragms / bases:</b> rigid diaphragms and base fixities exactly as recorded in model_opensees.py.</li>')
    parts.append('<li><b>Loading and solution:</b> each factored ASCE 7-22 combination (regenerated with Steltic\'s <code>design_pipeline.combos</code>) applied proportionally and scaled by λ; gravity as two-way tributary line loads on beams (Steltic <code>apply_gravity</code>), lateral forces and accidental torsion at the diaphragm masters; adaptive displacement control (Newton → KrylovNewton, step halving); λ<sub>u</sub> = the FIRST limit point of the λ–Δ curve (a drop of more than 15 % or an 8-step post-peak budget ends the run — post-buckling redistribution beyond that is not credited), or the onset of a plastic plateau (tangent stiffness below 2 % of elastic — strain-hardening creep beyond a mechanism is not credited).</li></ul>')

    # 4 capacity table
    parts.append('<h2>4 · System capacity by combination</h2>')
    parts.append('<div class="tw"><table><tr><th>combination</th><th>kind</th><th>imperf.</th><th>λ first yield</th><th>λ<sub>u</sub></th><th>φ<sub>s</sub> class</th><th>φ<sub>s</sub></th><th>φ<sub>s</sub>·λ<sub>u</sub></th><th>check</th><th>mechanism at peak</th><th>roof drift at peak</th><th>steps / s</th></tr>')
    for r in runs:
        res, ph = r["res"], r["phi"]
        drift = "—"
        sn = res["snapshot"]
        if sn.get("drifts"):
            disp, dr = sn["drifts"]
            drift = "%.2f in (max storey %.2f %%)" % (disp[-1], 100 * max(abs(x) for x in dr))
        parts.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><b>%.3f</b></td><td>%s</td><td>%s</td><td>%s</td><td class="%s">%s</td><td>%s</td><td>%s</td><td>%d / %.0f</td></tr>' % (
            _h(r["combo"][0]), r["summary"]["kind"], r["imp"], ("%.2f" % res["first_yield"]) if res["first_yield"] else "—", res["lambda_u"],
            ph["cls"], ("%.2f" % ph["phi_s"]) if ph["phi_s"] is not None else "—",
            ("%.3f" % r["check"][0]) if r["check"][0] is not None else "—", r["check"][1], r["check"][1],
            _h(r["cls"]["mechanism"]), drift, res["steps"], res["seconds"]))
    parts.append('</table></div>')
    parts.append('<p class="cap">Design check: φ<sub>s</sub>·λ<sub>u</sub> ≥ 1.0 (Zhang, Shayan, Rasmussen &amp; Ellingwood 2016). λ<sub>u</sub> is the load factor on the WHOLE factored combination at the peak of the load–deformation curve of the nominal (imperfect, residual-stressed) structure. '
                 'Seismic-pattern combinations on an R = %s system: %s.</p>' % (R, "treated as ordinary strength combinations (R ≤ 3, no ductile detailing assumed), φ<sub>s</sub> per the hot-rolled class" if (R is None or R <= 3) else "reported as a seismic supplement without a φ<sub>s</sub> pass/fail (outside the calibrations)"))

    # curves
    parts.append('<h3>Load–deformation curves</h3><div class="grid">')
    for r in runs:
        parts.append('<div>%s<div class="cap">%s — λ<sub>u</sub> = %.3f, control %s dof %d</div></div>' % (
            curve_svg(r["res"]["hist"], r["res"]["lambda_u"], r["combo"][0]), _h(r["combo"][0]), r["res"]["lambda_u"], r["res"]["control"][0], r["res"]["control"][1]))
    parts.append('</div>')

    # 5 member utilisation
    parts.append('<h2>5 · Member state at collapse vs. the member-based design</h2>')
    parts.append('<p>For each role × section group: the highest fibre-strain ratio ε<sub>max</sub>/ε<sub>y</sub> reached at the peak of the governing combination '
                 '(≥ 1.0 = yielded; ≥ 3.0 = plastic hinge), the number of members yielded / at hinge level, brace buckling flags, and the D/C the HR Steel App reported. '
                 'A group with a high member-based D/C but low utilisation at collapse is where the member design left reserve on the table; the reverse is where the system relies on it.</p>')
    parts.append('<div class="tw"><table><tr><th>role</th><th>section</th><th>n</th><th>Steltic D/C</th><th>governing DDM combo</th><th>max ε/ε<sub>y</sub> at peak</th><th>yielded</th><th>hinges</th><th>buckled braces</th></tr>')
    for row in member_table:
        parts.append('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%.2f</td><td>%d</td><td>%d</td><td>%s</td></tr>' % (
            row["role"], row["section"], row["n"], ("%.3f" % row["DC"]) if isinstance(row["DC"], (int, float)) else "—", _h(row["combo"]), row["ratio"], row["yielded"], row["hinges"], row.get("buckled", "—")))
    parts.append('</table></div>')

    # 6 sensitivity
    parts.append('<h2>6 · Sensitivity</h2>')
    if sensitivity:
        parts.append('<div class="tw"><table><tr><th>case</th><th>combination</th><th>λ<sub>u</sub></th><th>Δ vs nominal</th><th>mechanism</th></tr>')
        for s in sensitivity:
            parts.append('<tr><td>%s</td><td>%s</td><td>%.3f</td><td>%+.1f %%</td><td>%s</td></tr>' % (_h(s["case"]), _h(s["combo"]), s["lambda_u"], 100 * (s["lambda_u"] / s["ref"] - 1), _h(s["mechanism"])))
        parts.append('</table></div>')
    else:
        parts.append('<p class="na">No sensitivity runs requested (use <code>--sensitivity</code>).</p>')

    # 7 basis
    parts.append('<h2>7 · Basis, provisions and literature</h2><ul>')
    for n in notes:
        parts.append('<li>%s</li>' % n)
    parts.append('</ul>')

    # 8 QA
    parts.append('<h2>8 · QA and convergence</h2><div class="tw"><table><tr><th>combination</th><th>steps</th><th>failed steps</th><th>post-peak λ at 1.25 Δ<sub>peak</sub></th><th>solver log</th></tr>')
    for r in runs:
        res = r["res"]
        parts.append('<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td><code>%s</code></td></tr>' % (
            _h(r["combo"][0]), res["steps"], str(res["fails"]) if res.get("fails") is not None else "—", ("%.3f" % res["lam_at_1p25d"]) if res.get("lam_at_1p25d") else "not reached", _h("; ".join(res.get("log", [])[-3:]) or "clean")))
    parts.append('</table></div>')
    parts.append('<h2>9 · Caveats</h2><ul>'
                 '<li>Beam lateral-torsional buckling and local buckling are not represented by fibre beam elements: beams are taken as braced by the composite deck (Steltic L<sub>b</sub> convention) and compact (design of record).</li>'
                 '<li>Rigid diaphragms suppress in-plane beam axial force; collector/drag forces are not part of this model (as in the design of record).</li>'
                 '<li>Connections are rigid or pinned as designed; connection strength is not modelled — the DDM capacity assumes the connections develop the member forces.</li>'
                 '<li>Composite floor action is ignored (bare-steel lower bound, as in the design of record).</li>'
                 '<li>Cold-formed HSS residual stresses use a provisional membrane pattern; the calibrated pattern of Liu, Rasmussen &amp; Zhang (2018) should replace it.</li>'
                 '<li>%s This report does not replace the member-based design, AISC 341 detailing (n/a for R = 3) or connection design.</li></ul>'
                 % ("φ<sub>s</sub> classes flagged PROVISIONAL are used in this report (see banner)." if phi_s_provisional(runs) else "All φ<sub>s</sub> classes used here are verified from open documents (see banner); φ<sub>s</sub> remains a literature value."))
    parts.append('<p class="cap">Not for construction. Produced by an AI agent; must be independently checked by a licensed professional engineer.</p></div>')

    path = os.path.join(out_dir, "ddm_report.html")
    open(path, "w", encoding="utf-8").write("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>" + "".join(parts) + "</body></html>")
    return path


def ddm_block(nm, gate, runs, sensitivity, options, member_table):
    return {
        "method": "Direct Design Method (system-based design by advanced analysis) -- steltic_ddm 0.1",
        "basis": ["Zhang, Shayan, Rasmussen & Ellingwood, JCSR 123 (2016) I & II",
                  "AISC 360-22 Appendix 1 (design by advanced analysis) -- cite via Query file manager"]
                 + [phi_s.SOURCES[k] for k in sorted({src for r in runs if r["phi"]["cls"] in phi_s.TABLE for src in phi_s.TABLE[r["phi"]["cls"]]["sources"]})],
        "phi_s_provisional": phi_s_provisional(runs),
        "phi_s_status": phi_s_status_text(runs, as_html=False),
        "phi_s_classes": {r["phi"]["cls"]: dict(phi_s=r["phi"]["phi_s"], beta_T=r["phi"]["beta_T"], status=r["phi"]["status"]) for r in runs},
        "model": {"elements": "%s / Corotational / %d IP fibre" % ("dispBeamColumn" if options.get("fast") else "forceBeamColumn", options.get("nip", 5)),
                  "nsub": list(options.get("nsub", (2, 2, 4))), "material": "Steel01 Fy=%.0f E=29000 b=%.3f" % (options.get("Fy", 50), options.get("hardening", 0.002)),
                  "residual_stress": options.get("residual"), "out_of_plumb": "1/%d" % round(1 / options.get("psi", 1 / 500)),
                  "out_of_straightness": "L/%d" % round(1 / options.get("bow", 1 / 1000)), "diaphragm": "rigid (as designed)", "joints": "as designed",
                  "transfer_gate": {"pass": gate["ok"], "rows": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in gate["rows"]]}},
        "combinations": [
            {"label": r["combo"][0], "kind": r["summary"]["kind"], "imperfection": r["imp"], "scheme": "proportional",
             "lambda_u": round(r["res"]["lambda_u"], 3), "lambda_first_yield": (round(r["res"]["first_yield"], 3) if r["res"]["first_yield"] else None),
             "phi_s": r["phi"]["phi_s"], "phi_class": r["phi"]["cls"], "phi_source": r["phi"]["source"],
             "check": ("phi_s*lambda_u = %.3f %s" % r["check"]) if r["check"][0] is not None else "n/a",
             "mechanism": r["cls"]["mechanism"], "hinges_by_role": r["cls"]["hinges_by_role"], "buckled_braces": len(r["cls"]["buckled_braces"]),
             "roof_disp_at_peak_in": (round(r["res"]["snapshot"]["drifts"][0][-1], 3) if r["res"]["snapshot"].get("drifts") else None),
             "steps": r["res"]["steps"], "seconds": r["res"]["seconds"]} for r in runs],
        "sensitivity": [{k: v for k, v in s.items()} for s in (sensitivity or [])],
        "member_state_at_collapse": member_table,
        "caveats": ["LTB/local buckling of beams outside the fibre model", "collector axial not in rigid-diaphragm model",
                    "connections not modelled", "composite action ignored"] + (["phi_s provisional for some classes used"] if phi_s_provisional(runs) else []),
    }


def write_block(job_dir, block):
    p = os.path.join(job_dir, "design", "calc_package.json")
    if not os.path.exists(p):
        return None
    pkg = json.load(open(p))
    bak = p + ".pre_ddm.bak"
    if not os.path.exists(bak):
        open(bak, "w").write(json.dumps(pkg, indent=1))
    pkg["ddm_analysis"] = block
    open(p, "w").write(json.dumps(pkg, indent=1))
    return p
