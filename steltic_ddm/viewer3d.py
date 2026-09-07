"""viewer3d.py -- DDM viewer (ddm_viewer_3d.html): the GMNIA system-capacity sweeps, one load combination at a time.
The lambda-Delta curve is the chart (design level lambda = 1, first yield, phi_s * lambda_u, lambda_u), the building is
drawn in its shape at lambda_u (rigid-diaphragm masters), members coloured by the fibre strain ratio eps/eps_y at the
peak (elastic / yielded / plastic hinge) and braces flagged when buckled. Part of the Steltic viewer bundle (same core,
palette and controls as the Pushover and NLRHA viewers)."""
from __future__ import annotations
import csv, os
from . import viewer_core as VC

LEFT = """
<h2>Load combination (governing imperfection case)</h2>
<select id="comboSel"></select>
<label><input type="checkbox" id="fOverlay" checked> Overlay the other combinations of this kind</label>
<h2>Point on the λ–Δ curve</h2>
<input type="range" id="step" min="0" max="1" value="0">
<div class="row"><span class="val" id="stepL" style="min-width:110px;text-align:left"></span><span class="val" id="stepD" style="flex:1"></span></div>
<div class="btns"><button id="toDesign">λ = 1.0</button><button id="toFY">first yield</button><button id="toPeak" class="acc">λ<sub>u</sub></button><button id="play">▶</button></div>
<h2>Colour by</h2>
<select id="colorMode"><option value="state">Fibre state at the shown λ (ε / εy)</option><option value="role">Member role</option><option value="type">Member type</option><option value="sec">Section</option></select>
<div class="row"><span style="min-width:90px">Hinge dots</span><select id="hingeMode"><option value="state">colour = state</option><option value="red">red = any yielded section</option><option value="off">off</option></select></div>
"""
RIGHT = """
<h2 style="margin-top:0">System capacity</h2>
<div id="capTable"></div>
<h2>Story drift at λ<sub>u</sub></h2>
<div id="driftBars"></div>
<h2>Member census at the shown λ</h2>
<div id="census"></div>
<h2>Transfer gate (Steltic ↔ GMNIA, elastic)</h2>
<div id="gateTable"></div>
"""
MODULE_JS = r"""
const D = DATA.ddm; const RUNS = D.runs; let ci = D.default_index||0, step = 0, colorMode = 'state';
const eleByTag = {}; meshes.forEach(m => eleByTag[m.userData.tag] = m);
const SECS = [...new Set(M.elements.map(e=>e.sec))]; const SEC_PAL = [0x5b8dd9,0x2ecc71,0xe67e22,0x9b59b6,0xf1c40f,0x1abc9c,0xe74c3c,0x95a5a6,0xd35400,0x3498db,0x27ae60,0xc0392b];
const SEC_COL = {}; SECS.forEach((s,i)=>SEC_COL[s]=SEC_PAL[i%SEC_PAL.length]);
const ROLE_COL = {lateral_col:0x5b8dd9, gravity_col:0x3d5f8f, floor:0x9aa7b8, roof:0xc9d2dd, brace:0xe67e22};
const FIB = {elastic:STATE.elastic, partial:0xb8a25a, yield:STATE.yield, hinge:STATE.cp, buckled:STATE.buckled};   // eps/eps_y bands
const FIB_LABEL = {elastic:'elastic (ε < 0.5 εy)', partial:'0.5 εy ≤ ε < εy', yield:'yielded (εy ≤ ε < 3 εy)', hinge:'plastic hinge (ε ≥ 3 εy)', buckled:'brace buckled (N < 0, offset > L/200)'};
const dots = {}; M.elements.forEach(e => dots[e.tag] = hingeDot(0xd13b3b));       // one hinge dot per member (core sprite helper)
function C(){ return RUNS[ci]; }
function F(){ return C().frames[step]; }
function fibState(tag, f){ const m = f.mem[tag]; const r = m?m[0]:0; if (f.buckled.indexOf(Number(tag))>=0) return 'buckled'; if (r>=3) return 'hinge'; if (r>=1) return 'yield'; if (r>=0.5) return 'partial'; return 'elastic'; }
function floorsOf(f){ const fl=[[0,0,0]]; for (let k=1;k<M.levels.length;k++){ const mt = M.masters[String(k)]; const d = f.disp[String(mt)]; fl.push(d?[d[0],d[1],d[5]]:[0,0,0]); } return fl; }
function paint(){
  const f = F(); const hm = document.getElementById('hingeMode').value; const showH = hm!=='off' && colorMode==='state';
  for (const mesh of meshes){ const e=mesh.userData; let hex; const st = fibState(e.tag, f);
    if (colorMode==='type') hex = TYPE_COL[e.type]; else if (colorMode==='sec') hex = SEC_COL[e.sec]; else if (colorMode==='role') hex = ROLE_COL[D.roles[e.tag]]||0x9aa7b8; else hex = FIB[st];
    setColor(mesh, hex);
    const dot = dots[e.tag]; const m = f.mem[e.tag];
    if (!showH || !mesh.visible || (st!=='yield' && st!=='hinge' && st!=='buckled')){ dot.visible=false; continue; }
    const frac = st==='buckled' ? 0.5 : (m?m[1]:0.5);
    setDot(dot, hm==='red'?0xd13b3b:FIB[st], memberPos(e, frac), e.type==='brace'?0.85:1);
  }
}
function fmt(v,n){ return (v===null||v===undefined||Number.isNaN(v))?'—':Number(v).toFixed(n===undefined?2:n); }
function pct(v){ return (100*v).toFixed(2)+'%'; }
function update(){
  const run = C(); const FR = run.frames; step = Math.max(0, Math.min(FR.length-1, step));
  document.getElementById('step').max = FR.length-1; document.getElementById('step').value = step;
  const f = FR[step];
  document.getElementById('stepL').textContent = `λ = ${f.lam.toFixed(3)}`;
  document.getElementById('stepD').textContent = `${run.control_label} = ${f.d.toFixed(2)} in${step===run.peak_frame?' · λu':''}`;
  applyDeformation(floorsOf(f)); paint();
  const cnt = (pred) => M.elements.filter(e=>pred(e, f.mem[e.tag]?f.mem[e.tag][0]:0)).length;
  const worst = Math.max(0, ...Object.values(f.mem).map(m=>m[0]));
  document.getElementById('census').innerHTML = rows([['λ shown', `<b>${f.lam.toFixed(3)}</b> (step ${f.step}${step===run.peak_frame?' · λu':''})`], ['Members yielded (ε ≥ εy)', cnt((e,r)=>r>=1)], ['… of which plastic hinges (ε ≥ 3 εy)', cnt((e,r)=>r>=3)], ['Columns yielded', cnt((e,r)=>e.type==='column'&&r>=1)], ['Beams yielded', cnt((e,r)=>e.type==='beam'&&r>=1)], ['Braces yielded', cnt((e,r)=>e.type==='brace'&&r>=1)], ['Braces buckled', f.buckled.length], ['Worst ε / εy', fmt(worst,2)]])
    + (step===run.peak_frame ? '<table>' + Object.values(run.state).sort((a,b)=>b.ratio-a.ratio).map(s=>`<tr><td>${s.role} · ${s.section}</td><td>${s.n} · ε/εy ${s.ratio.toFixed(2)} · ${s.yielded} yld · ${s.hinges} hinge${s.buckled?' · '+s.buckled+' buckled':''}</td></tr>`).join('') + '</table>' : '<div style="font-size:10.5px;color:#8b95a3;margin-top:4px">group table shown at λu</div>');
  redrawChart();
}
function show(){
  const run = C();
  const lat = run.lateral; showDirection(lat && lat[0] ? (lat[0]==='X'?[lat[1],0,0]:[0,lat[1],0]) : null, 0xe0b341);
  const chk = run.check_ok===null ? '<span class="badge">n/a</span>' : (run.check_ok ? '<span class="badge ok">PASS</span>' : '<span class="badge ng">FAIL</span>');
  document.getElementById('capTable').innerHTML = rows([['Combination', `${run.label} <span style="color:#8b95a3">(${run.kind})</span>`], ['Imperfection case', run.imp], ['λu (system capacity)', `<b>${run.lambda_u.toFixed(3)}</b>`], ['First yield λ', fmt(run.first_yield,3)], ['λ at 1.25 Δu (post-peak)', fmt(run.lam_125,3)],
    ['φs · class', `${run.phi_s===null?'n/a':run.phi_s.toFixed(2)} · ${run.phi_cls}${run.beta_T?' · βT '+Number(run.beta_T).toFixed(2):''}${run.provisional?' <span class="badge ng">PROVISIONAL</span>':(run.phi_status?' <span class="badge ok">'+run.phi_status+'</span>':'')}`], ['φs · λu ≥ 1.0', `${fmt(run.phi_lam,3)} ${chk}`], ['Classification', run.cls], ['Mechanism', run.mechanism],
    ['Control DOF', run.control_label], ['Sweep', `${run.steps} steps · ${run.seconds.toFixed(0)} s${run.plateau?' · plateau':''}${run.frames.length>1?' · '+run.frames.length+' frames':' · peak snapshot only'}`]]);
  const dr = run.snapshot.drifts;
  if (dr && dr[1]){ const drifts = dr[1]; const dmax = Math.max(0.002, ...drifts.map(Math.abs));
    document.getElementById('driftBars').innerHTML = drifts.map((v,i)=>`<div class="stat"><span>story ${i+1}</span><span style="flex:1;margin:0 8px;height:7px;background:#1d222b;border-radius:3px;position:relative;top:5px"><span style="display:block;height:7px;width:${(100*Math.abs(v)/dmax).toFixed(0)}%;background:var(--acc);border-radius:3px"></span></span><b>${pct(Math.abs(v))}</b></div>`).join('') + `<div style="font-size:10.5px;color:#8b95a3;margin-top:4px">lateral ${run.lateral[0]}${run.lateral[1]>0?'+':'−'} · masters at λu (imperfections included)</div>`; }
  else document.getElementById('driftBars').innerHTML = '<div style="color:#8b95a3;font-size:11.5px">gravity combination — control is the largest vertical beam deflection (rigid-diaphragm shape shows sway only)</div>';
  document.getElementById('gateTable').innerHTML = rows(D.gate.rows.map(g=>[g.quantity, `${fmt(g.steltic,3)} → ${fmt(g.gmnia,3)} · ${fmt(g.ratio,3)} ${g.ok?'<span class="badge ok">ok</span>':'<span class="badge ng">'+(100*D.gate.tol).toFixed(0)+'%</span>'}`]));
  step = run.peak_frame; update();
}
function redrawChart(){
  const run = C(); const H = run.hist; const ser = [];
  const absH = h => h.map(p=>[Math.abs(p[1]), p[0]]);                       // chart x = |control displacement|, y = lambda
  if (document.getElementById('fOverlay').checked) RUNS.forEach((r,i)=>{ if (i!==ci && r.kind===run.kind) ser.push({pts:absH(r.hist), color:'#3a4656', width:1}); });
  ser.push({pts:absH(H), color:'#e0b341', width:2.2});
  const xmax = Math.max(...RUNS.filter(r=>r.kind===run.kind).map(r=>Math.max(...r.hist.map(p=>Math.abs(p[1])))))*1.05;
  const ymax = Math.max(1.2, ...RUNS.filter(r=>r.kind===run.kind).map(r=>r.lambda_u))*1.12;
  const hl = [{y:1.0, color:'#8b95a3', label:'λ = 1.0 (ASCE 7 factored loads)', align:'left'}, {y:run.lambda_u, color:'#e0b341', label:`λu ${run.lambda_u.toFixed(3)}`}];
  if (run.phi_lam!==null) hl.push({y:run.phi_lam, color: run.check_ok?'#3fc1a0':'#d13b3b', label:`φs λu ${run.phi_lam.toFixed(3)}`, dy: Math.abs(run.phi_lam-run.lambda_u)<0.08*ymax?12:0});
  if (run.first_yield!==null) hl.push({y:run.first_yield, color:'#f2c14e', label:`first yield λ ${run.first_yield.toFixed(2)}`, align:'left', dy: Math.abs(run.first_yield-1.0)<0.08*ymax?12:0});
  const f = F();
  drawChart({xmin:0, xmax, ymin:0, ymax, series:ser, hlines:hl, vlines:[{x:Math.abs(run.d_at_max), color:'#e0b341', label:`Δu ${Math.abs(run.d_at_max).toFixed(2)} in`, dy:12}], cursor:[Math.abs(f.d), f.lam], cursorColor:'#e0b341',
    title:`λ–Δ · ${run.label} · ${run.kind}`, xlabel:`${run.control_label} (in)`, ylabel:'λ', yfmt:v=>v.toFixed(2), xfmt:v=>v.toFixed(1)});
}
function onChartSeek(x){ const FR=C().frames; let best=0,bd=1e9; FR.forEach((f,i)=>{const dd=Math.abs(Math.abs(f.d)-x); if(dd<bd){bd=dd;best=i;}}); step=best; update(); }
function onDeformed(){} function onVisibility(){ paint(); }
let playing=false, acc=0; function onSpace(){ playing=!playing; }
function onTick(dt){ if(!playing) return; acc+=dt; if(acc>0.12){ acc=0; step++; if(step>=C().frames.length){ step=0; playing=false; } update(); } }
function memberInfo(e){
  const run = C(); const f = F(); const m = f.mem[e.tag]; const r = m?m[0]:0; const b = run.snapshot.braces[e.tag]; const st = fibState(e.tag, f);
  const lines = [['Length', `${e.L?e.L.toFixed(0):'—'} in`], ['Level', e._lvl], ['Role', D.roles[e.tag]||'—'], [`ε / εy at λ = ${f.lam.toFixed(3)}`, `${r.toFixed(2)} · ${FIB_LABEL[st]}`]];
  if (m && r>=1) lines.push(['Worst section along member', `${(100*m[1]).toFixed(0)} % from end 1`]);
  const rp = run.snapshot.member_ratio[e.tag]; if (rp!==undefined) lines.push(['ε / εy at λu', rp.toFixed(2)]);
  if (b) lines.push(['Brace N at λu (+tens)', `${b.N.toFixed(1)} kip`], ['Mid-length offset at λu', `${b.offset.toFixed(3)} in (L/${b.offset>0?(e.L/b.offset).toFixed(0):'∞'})`]);
  const row = D.schedule[e.tag]; if (row) lines.push(['Steltic governing combo', row.combo], ['Steltic demand P / Mx', `${row.P} kip / ${row.Mx} k-ft`]);
  return rows(lines);
}
function init(){
  const sel = document.getElementById('comboSel'); RUNS.forEach((r,i)=>{ const o=document.createElement('option'); o.value=i; o.textContent=`${r.label}  ·  ${r.kind}  ·  λu ${r.lambda_u.toFixed(2)}  ·  φsλu ${r.phi_lam===null?'n/a':r.phi_lam.toFixed(2)} ${r.check_ok===null?'':(r.check_ok?'✓':'✗')}`; sel.appendChild(o); });
  sel.value = ci; sel.onchange = e => { ci=parseInt(e.target.value); show(); };
  document.getElementById('step').oninput = e => { step=parseInt(e.target.value); update(); };
  const firstAtOrAbove = lam => { const FR=C().frames; for (let i=0;i<FR.length;i++){ if (FR[i].lam>=lam) return i; } return C().peak_frame; };
  document.getElementById('toPeak').onclick = () => { step=C().peak_frame; update(); };
  document.getElementById('toDesign').onclick = () => { step=firstAtOrAbove(1.0); update(); };
  document.getElementById('toFY').onclick = () => { const fy=C().first_yield; if (fy===null) return; step=firstAtOrAbove(fy); update(); };
  document.getElementById('play').onclick = onSpace;
  document.getElementById('fOverlay').onchange = redrawChart;
  document.getElementById('colorMode').onchange = e => { colorMode=e.target.value; setLegend(); paint(); };
  document.getElementById('hingeMode').onchange = () => paint();
  setLegend(); show();
}
function setLegend(){ if (colorMode==='state') legend([[FIB.elastic,FIB_LABEL.elastic],[FIB.partial,FIB_LABEL.partial],[FIB.yield,FIB_LABEL.yield],[FIB.hinge,FIB_LABEL.hinge],[FIB.buckled,FIB_LABEL.buckled]], 'Fibre state at the shown λ · GMNIA (residual stresses, imperfections, P-Δ/P-δ)', '<span style="color:#d13b3b;font-size:14px;vertical-align:-1px">●</span> solid dot = section at or beyond εy, placed at the worst integration segment of the member (yellow yielded · red plastic hinge ε ≥ 3 εy · purple buckled brace); red for all with the Hinge dots selector');
  else if (colorMode==='role') legend(Object.entries(ROLE_COL).map(([k,v])=>[v,k.replace('_',' ')]), 'Member role');
  else if (colorMode==='type') legend([[TYPE_COL.column,'column'],[TYPE_COL.beam,'beam'],[TYPE_COL.brace,'brace']], 'Member type'); else legend(SECS.map(s=>[SEC_COL[s],s]), 'Section'); }
"""


def model_from_nm(nm):
    base_z = min(v[2] for v in nm.nodes.values())
    els = [(m.tag, m.kind, m.section, m.n1, m.n2) for m in nm.members]
    fixed = [t for t, fl in nm.fixes.items() if tuple(fl[:3]) == (1, 1, 1) and nm.nodes[t][2] <= base_z + 1e-6]
    lab = "fixed" if any(nm.fixes[t][3] == 1 for t in fixed) else "pinned"
    diaph = [(3, master, slaves) for master, slaves in nm.diaphragms.items()]
    return VC.model_dict(nm.nodes, els, fixed, diaph, support_label=lab)


def _schedule(job_dir):
    p = os.path.join(job_dir, "design", "member_schedule.csv"); out = {}
    if os.path.exists(p):
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    out[int(r["ele_tag"])] = dict(combo=r.get("governing_combo", ""), P=round(float(r.get("P_comp_kip") or 0)), Mx=round(float(r.get("Mx_kipft") or 0)))
                except Exception:
                    pass
    return out


def write(out_dir, nm, gate, runs):
    """Build ddm_viewer_3d.html. `runs` are the per-combination records assembled by cli.run (combo, summary, res, cls, phi, check, imp, state)."""
    model = model_from_nm(nm)
    for e in model["elements"]:
        n1, n2 = model["nodes"][str(e["n"][0])], model["nodes"][str(e["n"][1])]
        e["L"] = round(sum((a - c) ** 2 for a, c in zip(n1, n2)) ** 0.5, 1)
    R = []
    for r in runs:
        res = r["res"]; snap = res.get("snapshot") or {}
        if not snap.get("disp"):
            continue                                                   # results from a version of cli.py without viewer snapshots
        hist = [[round(a, 4), round(b, 4)] for a, b in res["hist"]]
        peak_index = max(range(len(hist)), key=lambda i: hist[i][0]) if hist else 0
        ctrl = res.get("control") or (None, None); lat = res.get("lateral") or (None, 0)
        if lat and lat[0]:
            ctrl_label = "roof displacement %s" % lat[0]
        else:
            ctrl_label = "beam deflection (node %s, dof %s)" % (ctrl[0], ctrl[1])
        frames = []
        for f in res.get("frames") or []:
            frames.append(dict(step=f["step"], lam=f["lam"], d=f["d"], disp={str(k): v for k, v in f["disp"].items()},
                               mem={str(k): list(v) for k, v in f["mem"].items()}, buckled=[int(t) for t in f["buckled"]]))
        if not frames:                                                  # results from a version without per-step frames
            frames = [dict(step=snap.get("step", 0), lam=res["lambda_u"], d=res.get("d_at_max", 0.0), disp={str(k): v for k, v in snap["disp"].items()},
                           mem={str(k): [v, 0.5] for k, v in (snap.get("member_ratio") or {}).items() if v >= 0.5},
                           buckled=[int(t) for t, b in (snap.get("braces") or {}).items() if b.get("buckled")])]
        peak_frame = max(range(len(frames)), key=lambda i: frames[i]["lam"])
        phi = r["phi"]; chk = r["check"]
        R.append(dict(label=r["combo"][0], kind=r["summary"]["kind"], imp=r["imp"], lambda_u=res["lambda_u"], first_yield=res.get("first_yield"), lam_125=res.get("lam_at_1p25d"),
                      d_at_max=res.get("d_at_max", 0.0), hist=hist, peak_index=peak_index, frames=frames, peak_frame=peak_frame, control_label=ctrl_label, lateral=list(lat) if lat else None,
                      phi_s=phi.get("phi_s"), phi_cls=phi.get("cls"), provisional=bool(phi.get("provisional")), phi_status=phi.get("status"), beta_T=phi.get("beta_T"), phi_lam=chk[0] if chk else None, check_ok=(None if not chk or chk[1] == "n/a" else chk[1] == "PASS"),
                      cls=r["cls"]["cls"], mechanism=r["cls"]["mechanism"], steps=res["steps"], seconds=res["seconds"], plateau=bool(res.get("plateau")),
                      snapshot=dict(drifts=snap.get("drifts"), disp=snap["disp"], member_ratio=snap.get("member_ratio", {}), braces=snap.get("braces", {})), state=r["state"]))
    if not R:
        raise RuntimeError("no runs with viewer snapshots (re-run with this version of steltic_ddm)")
    # default: the governing (lowest phi_s*lambda_u, else lowest lambda_u) combination
    def key(x):
        return (x["phi_lam"] if x["phi_lam"] is not None else 9.0, x["lambda_u"])
    default_index = min(range(len(R)), key=lambda i: key(R[i]))
    roles = {m.tag: m.role for m in nm.members}
    gate_small = dict(ok=gate.get("ok"), tol=gate.get("tol"), rows=[dict(quantity=g["quantity"], steltic=g["steltic"], gmnia=g["gmnia"], ratio=g["ratio"], ok=g["ok"]) for g in gate.get("rows", [])])
    extra = dict(ddm=dict(runs=R, default_index=default_index, roles=roles, gate=gate_small, schedule=_schedule(nm.job_dir)))
    worst = R[default_index]
    seis = (nm.cfg or {}).get("seis", {})
    meta = dict(title=f"{nm.name} · DDM (GMNIA system capacity)", subtitle=seis.get("system", "") if isinstance(seis, dict) else "",
                accline=f"Steltic viewer bundle · DDM Steel App · {len(R)} combinations · governing {worst['label']} λu {worst['lambda_u']:.3f}" + (f" · φsλu {worst['phi_lam']:.3f} {'PASS' if worst['check_ok'] else 'FAIL'}" if worst["phi_lam"] is not None else ""),
                caveat=("Building drawn at the λ selected on the curve from the diaphragm masters (rigid floors, imperfections and P-Δ included; beam sag of gravity cases is not "
                        "in the rigid-body shape). Member colours and hinge dots are the fibre strain ratio ε/εy recorded every second load step (and at λu). φs status per class as reported by phi_s.py — see the banner in ddm_report.html. Not for construction."))
    return VC.render(os.path.join(out_dir, "ddm_viewer_3d.html"), f"{nm.name} · DDM viewer", "ddm", meta, model, extra, LEFT, RIGHT, MODULE_JS, amp=10,
                     root=nm.job_dir)
