"""viewer3d.py -- Pushover viewer (pushover_viewer_3d.html): the capacity curve as a scrubber over the deformed
building, hinges and braces coloured by ASCE 41 state (elastic / yielded / >IO / >LS / >CP / beyond b), the ASCE 41
target displacements and the design base shears as reference lines. Part of the Steltic viewer bundle."""
from __future__ import annotations
import os
from . import viewer_core as VC

LEFT = """
<h2>Push direction</h2>
<select id="dirSel"></select>
<h2>Position on the capacity curve</h2>
<input type="range" id="step" min="0" max="1" value="0">
<div class="row"><span class="val" id="stepU" style="min-width:110px;text-align:left"></span><span class="val" id="stepV" style="flex:1"></span></div>
<div class="btns"><button id="toT1" class="acc">δt BSE-1N</button><button id="toT2" class="acc">δt BSE-2N</button><button id="toVmax">V<sub>max</sub></button></div>
<div class="btns"><button id="play">▶ play</button><button id="toEnd">end</button></div>
<h2>Colour by</h2>
<select id="colorMode"><option value="state">Component state (ASCE 41)</option><option value="type">Member type</option><option value="sec">Section</option></select>
<div class="row"><span style="min-width:90px">Hinge dots</span><select id="hingeMode"><option value="state">colour = state</option><option value="red">red = any plastic hinge</option><option value="off">off</option></select></div>
"""
RIGHT = """
<h2 style="margin-top:0">At this step</h2>
<div id="stepStats"></div>
<h2>Story drift</h2>
<div id="driftBars"></div>
<h2>Component census</h2>
<div id="census"></div>
<h2>NSP summary</h2>
<div id="nspTable"></div>
"""
MODULE_JS = r"""
const P = DATA.pushover; const DIRS = Object.keys(P.dirs); let dir = DIRS[0], step = 0, playing = false, colorMode = 'state';
const HZ = P.hinges; const hingeByEle = {}; HZ.forEach((h,i) => { (hingeByEle[h.ele] = hingeByEle[h.ele] || []).push(i); });
const eleByTag = {}; meshes.forEach(m => eleByTag[m.userData.tag] = m);
const SECS = [...new Set(M.elements.map(e=>e.sec))]; const SEC_PAL = [0x5b8dd9,0x2ecc71,0xe67e22,0x9b59b6,0xf1c40f,0x1abc9c,0xe74c3c,0x95a5a6,0xd35400,0x3498db,0x27ae60,0xc0392b];
const SEC_COL = {}; SECS.forEach((s,i)=>SEC_COL[s]=SEC_PAL[i%SEC_PAL.length]);
// hinge markers
const markers = HZ.map(() => hingeDot(0xd13b3b));           // solid discs at the hinge locations (core helper)
function hingeState(h, v){          // v = plastic rotation (rad) or brace axial deformation (in, +tension)
  if (h.kind === 'brace'){
    if (v < 0){ const d=-v; if (d>h.b) return 'beyond'; if (d>h.CP) return 'cp'; if (d>h.LS) return 'ls'; if (d>h.dc) return 'buckled'; return 'elastic'; }   // IO (0.5 Δc) precedes buckling: reported in the census, drawn as 'buckled'
    else { if (v>h.b_t) return 'beyond'; if (v>h.CP_t) return 'cp'; if (v>h.LS_t) return 'ls'; if (v>h.dT) return 'tension'; return 'elastic'; }
  }
  const t = Math.abs(v); if (t>h.b) return 'beyond'; if (t>h.CP) return 'cp'; if (t>h.LS) return 'ls'; if (t>h.IO) return 'io'; if (t>0.5*h.thy) return 'yield'; return 'elastic';
}
const RANK = {elastic:0, yield:1, tension:1, buckled:1.5, io:2, ls:3, cp:4, beyond:5};
function stateAt(s){ const pl = P.dirs[dir].hinge_pl[s]; return HZ.map((h,i)=>hingeState(h, pl[i])); }
function floorsAt(s){ const su = P.dirs[dir].story_u[s]; const f=[[0,0,0]]; su.forEach(u => f.push(dir==='X'?[u,0,0]:[0,u,0])); return f; }
function paint(s){
  const st = stateAt(s); const worst = {};
  st.forEach((k,i)=>{ const e=HZ[i].ele; if(!(e in worst) || RANK[k]>RANK[worst[e]]) worst[e]=k; });
  for (const mesh of meshes){ const e=mesh.userData; let hex;
    if (colorMode==='type') hex = TYPE_COL[e.type]; else if (colorMode==='sec') hex = SEC_COL[e.sec]; else hex = STATE[worst[e.tag]||'elastic'];
    setColor(mesh, hex); }
  const hm = document.getElementById('hingeMode').value; const showH = hm!=='off' && colorMode==='state';
  markers.forEach((m,i)=>{ const h=HZ[i], k=st[i]; const em=eleByTag[h.ele]; if(!em||!em.visible||!showH||k==='elastic'){ m.visible=false; return; }
    setDot(m, hm==='red'?0xd13b3b:STATE[k], memberPos(em.userData, h.end===1?0.08:(h.end===2?0.92:0.5)), h.kind==='brace'?0.85:1); });
  return {st, worst};
}
function fmt(v,n){ return (v===null||v===undefined)?'—':Number(v).toFixed(n===undefined?2:n); }
function update(){
  const D = P.dirs[dir]; const n = D.u.length; step = Math.max(0, Math.min(n-1, step));
  document.getElementById('step').max = n-1; document.getElementById('step').value = step;
  applyDeformation(floorsAt(step)); const {st} = paint(step);
  const u = D.u[step], V = D.V[step]; const pl_now = D.hinge_pl[step];
  document.getElementById('stepU').textContent = `roof ${u.toFixed(2)} in · ${(100*u/P.H).toFixed(2)}% H`;
  document.getElementById('stepV').textContent = `V = ${V.toFixed(0)} kip · V/W ${(V/P.W).toFixed(3)}`;
  // right panel
  const drifts = []; let prev = 0; D.story_u[step].forEach((uk,i)=>{ drifts.push((uk-prev)/P.heights[i]); prev=uk; });
  const cnt = {}; st.forEach((k,i)=>{ const key = HZ[i].kind+':'+k; cnt[key]=(cnt[key]||0)+1; });
  const nb = k => HZ.filter((h,i)=>h.kind==='brace'&&st[i]===k).length, nc = k => HZ.filter((h,i)=>h.kind==='col'&&RANK[st[i]]>=RANK[k]).length, nbm = k => HZ.filter((h,i)=>h.kind==='beam'&&RANK[st[i]]>=RANK[k]).length;
  document.getElementById('stepStats').innerHTML = rows([['Roof displacement', `${u.toFixed(2)} in`], ['Base shear', `${V.toFixed(0)} kip`], ['V / design V (R-reduced)', P.V_design?(V/P.V_design).toFixed(2):'—'], ['Max story drift', `${(100*Math.max(...drifts)).toFixed(2)}%`], ['Worst component', (()=>{let w='elastic';st.forEach(k=>{if(RANK[k]>RANK[w])w=k;});return STATE_LABEL[w];})()]]);
  const dmax = Math.max(0.005, ...drifts);
  document.getElementById('driftBars').innerHTML = drifts.map((d,i)=>`<div class="stat"><span>story ${i+1}</span><span style="flex:1;margin:0 8px;height:7px;background:#1d222b;border-radius:3px;position:relative;top:5px"><span style="display:block;height:7px;width:${(100*d/dmax).toFixed(0)}%;background:var(--acc);border-radius:3px"></span></span><b>${(100*d).toFixed(2)}%</b></div>`).join('');
  const nbIO = HZ.filter((h,i)=>h.kind==='brace' && pl_now[i]<0 && -pl_now[i]>h.IO).length;
  document.getElementById('census').innerHTML = rows([['Braces buckled (Δ > Δc)', nb('buckled')+nb('ls')+nb('cp')+nb('beyond')], ['Braces past IO (0.5 Δc)', nbIO], ['Braces yielded in tension', nb('tension')], ['Beam hinges > IO', nbm('io')], ['Column hinges > IO', nc('io')], ['Any component > CP', HZ.filter((h,i)=>RANK[st[i]]>=4).length], ['Beyond b (valid range)', HZ.filter((h,i)=>st[i]==='beyond').length]]);
  redrawChart();
}
function redrawChart(){
  const D = P.dirs[dir]; const pts = D.u.map((x,i)=>[x, D.V[i]]); const umax = Math.max(...D.u)*1.05, vmax = Math.max(...D.V, P.V_design||0)*1.12;
  const vl = []; for (const [lvl,n] of Object.entries(D.nsp)) vl.push({x:n.target_disp_in, color: lvl==='BSE-1N'?'#3fc1a0':'#e8742c', label:`δt ${lvl} ${n.target_disp_in.toFixed(1)} in`, dy: lvl==='BSE-1N'?0:12});
  const hl = []; if (P.V_design) hl.push({y:P.V_design, color:'#8b95a3', label:`design V ${P.V_design.toFixed(0)} kip (R = ${P.R})`}); if (P.V_wind) hl.push({y:P.V_wind[dir], color:'#5b8dd9', label:`wind ${P.V_wind[dir].toFixed(0)} kip`});
  const ser = [{pts, color:'#ffb454', width:2}]; const n2 = D.nsp['BSE-2N']; if (n2) ser.push({pts:[[0,0],[n2.uy,n2.Vy],[n2.target_disp_in, n2.Vy+n2.alpha1*n2.Ke*(n2.target_disp_in-n2.uy)]], color:'#9fb4cc', width:1.2, dash:[5,4]});
  drawChart({xmin:0, xmax:umax, ymin:0, ymax:vmax, series:ser, vlines:vl, hlines:hl, cursor:[D.u[step], D.V[step]], cursorColor:'#ffb454', title:`Capacity curve — push ${dir} (first-mode pattern, P-Δ)`, xlabel:'roof displacement (in)', ylabel:'base shear (kip)', yfmt:v=>v.toFixed(0), xfmt:v=>v.toFixed(0)});
}
function onChartSeek(x){ const D=P.dirs[dir]; let best=0,bd=1e9; D.u.forEach((u,i)=>{const d=Math.abs(u-x); if(d<bd){bd=d;best=i;}}); step=best; update(); }
function seekU(x){ onChartSeek(x); }
function onDeformed(){} function onVisibility(){ paint(step); }
function onSpace(){ playing=!playing; document.getElementById('play').textContent = playing?'❚❚ pause':'▶ play'; }
let acc=0; function onTick(dt){ if(!playing) return; acc+=dt; if(acc>0.08){ acc=0; step++; if(step>=P.dirs[dir].u.length){ step=0; } update(); } }
function memberInfo(e){
  const idx = hingeByEle[e.tag]||[]; const pl = P.dirs[dir].hinge_pl[step]; const st = stateAt(step);
  const lines = [['Length', `${e.L?e.L.toFixed(0):'—'} in`], ['Level', e._lvl]];
  idx.forEach(i=>{ const h=HZ[i]; const v=pl[i];
    if (h.kind==='brace') lines.push([`Δ axial (+tens)`, `${v.toFixed(3)} in`], ['Δc / Δy', `${h.dc.toFixed(3)} / ${h.dT.toFixed(3)} in`], ['CP comp / tens', `${h.CP.toFixed(2)} / ${h.CP_t.toFixed(2)} in`], ['State', STATE_LABEL[st[i]]]);
    else lines.push([`θpl end ${h.end}`, `${v.toExponential(2)} rad`], ['θy · IO · LS · CP', `${h.thy.toExponential(1)} · ${h.IO.toExponential(1)} · ${h.LS.toExponential(1)} · ${h.CP.toExponential(1)}`], ['D/C CP', (Math.abs(v)/h.CP).toFixed(2)], ['State', STATE_LABEL[st[i]]]);
  });
  const row = P.schedule[e.tag]; if (row) lines.push(['Steltic governing combo', row.combo], ['Steltic demand P / Mx', `${row.P} kip / ${row.Mx} k-ft`]);
  return rows(lines);
}
function init(){
  const sel = document.getElementById('dirSel'); DIRS.forEach(d=>{ const o=document.createElement('option'); o.value=d; o.textContent=`Push ${d}  ·  T1 ${P.dirs[d].T1.toFixed(3)} s  ·  Vmax ${P.dirs[d].Vmax.toFixed(0)} kip  ·  Ω ${P.dirs[d].Omega?P.dirs[d].Omega.toFixed(1):'—'}`; sel.appendChild(o); });
  sel.onchange = e => { dir=e.target.value; step=0; showDirection(dir==='X'?[1,0,0]:[0,1,0], 0xffb454); nsp(); update(); };
  document.getElementById('step').oninput = e => { step=parseInt(e.target.value); update(); };
  document.getElementById('toT1').onclick = () => seekU(P.dirs[dir].nsp['BSE-1N'].target_disp_in);
  document.getElementById('toT2').onclick = () => seekU(P.dirs[dir].nsp['BSE-2N'].target_disp_in);
  document.getElementById('toVmax').onclick = () => { const D=P.dirs[dir]; step=D.V.indexOf(Math.max(...D.V)); update(); };
  document.getElementById('toEnd').onclick = () => { step=P.dirs[dir].u.length-1; update(); };
  document.getElementById('play').onclick = onSpace;
  document.getElementById('colorMode').onchange = e => { colorMode=e.target.value; setLegend(); paint(step); };
  document.getElementById('hingeMode').onchange = () => paint(step);
  showDirection([1,0,0], 0xffb454); setLegend(); nsp(); update();
}
function nsp(){ const D=P.dirs[dir]; const r=[]; for (const [lvl,n] of Object.entries(D.nsp)) r.push([`${lvl}: Te · Sa · δt`, `${n.Te.toFixed(2)} s · ${n.Sa.toFixed(3)} g · ${n.target_disp_in.toFixed(1)} in`], [`${lvl}: μstrength ≤ μmax`, `${n.mu_strength.toFixed(2)} ≤ ${isFinite(n.mu_max)?n.mu_max.toFixed(1):'∞'} ${n.nsp_permitted?'<span class="badge ok">NSP ok</span>':'<span class="badge ng">NDP</span>'}`]);
  r.push(['Vmax · Ω = Vmax/V', `${D.Vmax.toFixed(0)} kip · ${D.Omega?D.Omega.toFixed(1):'—'}`], ['μT (P-695)', `${D.mu_T.toFixed(2)} (${D.tail})`]); if (D.acc) for (const [lvl,a] of Object.entries(D.acc)) r.push([`Worst D/C at ${lvl} (IO/LS/CP)`, `${a.IO.toFixed(2)} / ${a.LS.toFixed(2)} / ${a.CP.toFixed(2)}`]);
  document.getElementById('nspTable').innerHTML = rows(r); }
function setLegend(){ if (colorMode==='state') legend([[STATE.elastic,'elastic'],[STATE.yield,'yielded / brace tension yield'],[STATE.buckled,'brace buckled (Δ > Δc)'],[STATE.io,'> IO'],[STATE.ls,'> LS'],[STATE.cp,'> CP'],[STATE.beyond,'beyond b (valid range)']], 'Component state · ASCE 41 (parameters: '+(P.params_verified?'verified':'UNVERIFIED placeholders')+')', '<span style="color:#d13b3b;font-size:14px;vertical-align:-1px">●</span> solid dot = plastic hinge formed at that member end (brace: mid-length buckling / tension yield); dot colour follows the state, or red for any hinge (Hinge dots selector)');
  else if (colorMode==='type') legend([[TYPE_COL.column,'column'],[TYPE_COL.beam,'beam'],[TYPE_COL.brace,'brace']], 'Member type'); else legend(SECS.map(s=>[SEC_COL[s],s]), 'Section'); }
"""


def write(outdir, pkg, prm, runs, results, hinge_stats):
    """Build pushover_viewer_3d.html from the in-memory run records (called by the CLI after the report)."""
    model = VC.model_from_package(pkg)
    b = pkg.basis
    # hinge registry (same order as run['hinge_tags'])
    first = next(iter(runs.values())); hinges = results[next(iter(results))]["hinges"]
    HZ = []
    for t in first["hinge_tags"]:
        h = hinges[t]; s = h["spec"]
        d = dict(tag=t, ele=h["ele"], end=h["end"], kind=h["kind"], sec=h["section"], z=round(h["z"]))
        if h["kind"] == "brace":
            d.update(dc=s.dc, dT=s.dT, IO=s.IO, LS=s.LS, CP=s.CP, b=s.b_c, IO_t=s.IO_t, LS_t=s.LS_t, CP_t=s.CP_t, b_t=s.b_t, thy=s.dc)
        else:
            d.update(thy=s.theta_y, IO=s.IO, LS=s.LS, CP=s.CP, b=s.b_pl)
        HZ.append(d)
    dirs = {}
    for dname, run in runs.items():
        R = results[dname]
        dirs[dname] = dict(T1=run["pattern"]["T1"], u=[round(x, 3) for x in run["rec"]["u"]], V=[round(x, 1) for x in run["rec"]["V"]],
                           story_u=[[round(x, 3) for x in s] for s in run["rec"]["story_u"]],
                           hinge_pl=[[round(x, 5) for x in s] for s in run["rec"]["hinge_pl"]],
                           nsp={k: {kk: (None if (isinstance(vv, float) and vv != vv) else vv) for kk, vv in n.items() if kk in ("Te", "Sa", "target_disp_in", "mu_strength", "mu_max", "nsp_permitted", "Vy", "uy", "Ke", "alpha1")} for k, n in R["nsp"].items()},
                           Vmax=R["p695"]["Vmax_kip"], Omega=R["p695"]["Omega"], mu_T=R["p695"]["mu_T"], tail=run.get("tail", {}).get("status", ""),
                           acc={k: a["worst_DC"] for k, a in R["acc"].items()})
    heights = first["heights"]
    sched = {t: dict(combo=r.get("governing_combo", ""), P=round(r.get("P_comp_kip", 0)), Mx=round(r.get("Mx_kipft", 0))) for t, r in pkg.schedule.items()}
    for e in model["elements"]:
        n1, n2 = model["nodes"][str(e["n"][0])], model["nodes"][str(e["n"][1])]
        e["L"] = round(sum((a - c) ** 2 for a, c in zip(n1, n2)) ** 0.5, 1)
    wind = None
    try:
        import re
        cd = (pkg.calc.get("capacity_design") or {}).get("wind_vs_seismic", {}).get("computed", "")
        m = re.search(r"X\s*=\s*([0-9.]+)\s*kip,\s*Y\s*=\s*([0-9.]+)", cd)
        if m: wind = {"X": float(m.group(1)), "Y": float(m.group(2))}
    except Exception:
        pass
    extra = dict(pushover=dict(dirs=dirs, hinges=HZ, H=sum(heights), heights=heights, W=b.W_kip or 1.0, V_design=b.V_design_kip, V_wind=wind,
                               R=b.R, params_verified=bool(prm.get("verified")), schedule=sched))
    meta = dict(title=f"{pkg.name} · Pushover (ASCE 41 NSP)", subtitle=f"{b.system or ''}", accline=f"Steltic viewer bundle · Pushover Analyst · SDS {b.SDS} g · R {b.R}",
                caveat=("Deformed shape from the diaphragm masters (rigid floors); hinge states from the recorded plastic rotations / brace deformations at each step. "
                        + ("Component parameters UNVERIFIED (placeholders) — see the red banner in pushover_report.html. " if not prm.get("verified") else "")
                        + "Not for construction."))
    return VC.render(os.path.join(outdir, "pushover_viewer_3d.html"), f"{pkg.name} · Pushover viewer", "pushover", meta, model, extra, LEFT, RIGHT, MODULE_JS, amp=10,
                     root=str(pkg.root))
