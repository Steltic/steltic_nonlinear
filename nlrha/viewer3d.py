"""viewer3d.py -- NLRHA viewer (nlrha_viewer_3d.html): the ASCE 7-22 Chapter 16 suite as a time-history player over the
deformed building. One record at a time: scrub or play the response, braces coloured by their instantaneous state, beams
and columns by their peak state over the record, story drifts against the suite mean and the 16.4 limit, and the roof /
ground-motion traces as the timeline. Part of the Steltic viewer bundle (same core, palette and controls as the Pushover
and DDM viewers)."""
from __future__ import annotations
import os
from . import viewer_core as VC

LEFT = """
<h2>Ground motion (scaled pair)</h2>
<select id="recSel"></select>
<h2>Time</h2>
<input type="range" id="step" min="0" max="1" value="0">
<div class="row"><span class="val" id="stepT" style="min-width:110px;text-align:left"></span><span class="val" id="stepU" style="flex:1"></span></div>
<div class="btns"><button id="play" class="acc">▶ play</button><button id="toPX">peak X</button><button id="toPY">peak Y</button><button id="toEnd">end (residual)</button></div>
<div class="row"><span>speed</span><select id="speed"><option value="0.5">0.5×</option><option value="1" selected>1× real time</option><option value="2">2×</option><option value="4">4×</option></select></div>
<h2>Timeline trace</h2>
<select id="chartMode"><option value="roof">Roof displacement (in)</option><option value="drift">Roof drift ratio (%)</option><option value="ag">Ground acceleration (g)</option></select>
<h2>Colour by</h2>
<select id="colorMode"><option value="state">Component state (at this step)</option><option value="type">Member type</option><option value="sec">Section</option></select>
<div class="row"><span style="min-width:90px">Hinge dots</span><select id="hingeMode"><option value="state">colour = state</option><option value="red">red = any plastic hinge</option><option value="off">off</option></select></div>
<label><input type="checkbox" id="fAg"> Ground-acceleration vector (live)</label>
"""
RIGHT = """
<h2 style="margin-top:0">At this instant</h2>
<div id="stepStats"></div>
<h2>Story drift · this record (bar) · suite mean (tick) · 16.4 limit</h2>
<div id="driftBars"></div>
<h2>Component census</h2>
<div id="census"></div>
<h2>Suite · Section 16.4</h2>
<div id="suiteTable"></div>
"""
MODULE_JS = r"""
const N = DATA.nlrha; const RECS = N.records; let ri = 0, step = 0, playing = false, colorMode = 'state', chartMode = 'roof', speed = 1;
const HZ = N.hinges; const hingeByEle = {}; HZ.forEach((h,i) => { (hingeByEle[h.ele] = hingeByEle[h.ele] || []).push(i); });
const BIDX = N.brace_idx;                       // frames.brace[j] belongs to hinge HZ[BIDX[j]]
const HIDX = N.hinge_idx || [];                 // frames.hinge[s][j] belongs to hinge HZ[HIDX[j]]
const eleByTag = {}; meshes.forEach(m => eleByTag[m.userData.tag] = m);
const SECS = [...new Set(M.elements.map(e=>e.sec))]; const SEC_PAL = [0x5b8dd9,0x2ecc71,0xe67e22,0x9b59b6,0xf1c40f,0x1abc9c,0xe74c3c,0x95a5a6,0xd35400,0x3498db,0x27ae60,0xc0392b];
const SEC_COL = {}; SECS.forEach((s,i)=>SEC_COL[s]=SEC_PAL[i%SEC_PAL.length]);
const markers = HZ.map(() => hingeDot(0xd13b3b));           // solid discs at the hinge locations (core helper)
function hingeState(h, v){          // v = plastic rotation (rad) or brace axial deformation (in, +tension)
  if (h.kind === 'brace'){
    if (v < 0){ const d=-v; if (d>h.b) return 'beyond'; if (d>h.CP) return 'cp'; if (d>h.LS) return 'ls'; if (d>h.dc) return 'buckled'; return 'elastic'; }
    else { if (v>h.b_t) return 'beyond'; if (v>h.CP_t) return 'cp'; if (v>h.LS_t) return 'ls'; if (v>h.dT) return 'tension'; return 'elastic'; }
  }
  const t = Math.abs(v); if (t>h.b) return 'beyond'; if (t>h.CP) return 'cp'; if (t>h.LS) return 'ls'; if (t>h.IO) return 'io'; if (t>0.5*h.thy) return 'yield'; return 'elastic';
}
const RANK = {elastic:0, yield:1, tension:1, buckled:1.5, io:2, ls:3, cp:4, beyond:5};
function worseOf(a,b){ return RANK[a]>=RANK[b]?a:b; }
function R(){ return RECS[ri]; }
function frameCount(){ return R().frames.t.length; }
// per-record peak state of every hinge (frames peak, braces worst of compression / tension excursions)
function peakStates(rec){ return HZ.map((h,i)=>{ const p = rec.hinge_peak[i]; if (h.kind==='brace') return worseOf(hingeState(h, p[0]), hingeState(h, -p[1])); return hingeState(h, p); }); }
let PEAK = peakStates(R());
// The state of every hinge AT FRAME s. Braces have always been live; beams and columns used to
// start from PEAK -- the record's worst -- so the dots and the colours were already at their final
// state on frame 0 and never changed as you played. With frames.hinge present nothing is seeded
// from the peak. A suite run before that field existed still falls back to it, and says so.
const LIVE_HINGES = HIDX.length > 0 && !!(R().frames.hinge);
function statesAt(s){
  const F = R().frames;
  const fh = F.hinge && F.hinge[s];
  const st = fh ? HZ.map(()=> 'elastic') : PEAK.slice();
  if (fh) HIDX.forEach((hi,j)=>{ const h = HZ[hi]; const v = fh[j];
    st[hi] = h.kind==='brace' ? hingeState(h, v) : hingeState(h, Math.abs(v)); });
  const fb = F.brace[s]; BIDX.forEach((hi,j)=>{ st[hi] = hingeState(HZ[hi], fb[j]); });
  return st;
}
function floorsAt(s){ return [[0,0,0]].concat(R().frames.story[s]); }
function paint(s){
  const st = statesAt(s); const worst = {};
  st.forEach((k,i)=>{ const e=HZ[i].ele; if(!(e in worst) || RANK[k]>RANK[worst[e]]) worst[e]=k; });
  for (const mesh of meshes){ const e=mesh.userData; let hex;
    if (colorMode==='type') hex = TYPE_COL[e.type]; else if (colorMode==='sec') hex = SEC_COL[e.sec]; else hex = STATE[worst[e.tag]||'elastic'];
    setColor(mesh, hex); }
  const hm = document.getElementById('hingeMode').value; const showH = hm!=='off' && colorMode==='state';
  markers.forEach((m,i)=>{ const h=HZ[i], k=st[i]; const em=eleByTag[h.ele]; if(!em||!em.visible||!showH||k==='elastic'){ m.visible=false; return; }
    setDot(m, hm==='red'?0xd13b3b:STATE[k], memberPos(em.userData, h.end===1?0.08:(h.end===2?0.92:0.5)), h.kind==='brace'?0.85:1); });
  return st;
}
function fmt(v,n){ return (v===null||v===undefined||Number.isNaN(v))?'—':Number(v).toFixed(n===undefined?2:n); }
function pct(v){ return (100*v).toFixed(2)+'%'; }
function update(){
  const rec = R(); const F = rec.frames; const n = F.t.length; step = Math.max(0, Math.min(n-1, step));
  document.getElementById('step').max = n-1; document.getElementById('step').value = step;
  applyDeformation(floorsAt(step)); const st = paint(step);
  const t = F.t[step]; const roof = F.story[step][F.story[step].length-1]; const ag = F.ag[step];
  document.getElementById('stepT').textContent = `t = ${t.toFixed(2)} s${t>rec.t_sig?' · free vibration':''}`;
  document.getElementById('stepU').textContent = `roof ${roof[0].toFixed(2)} / ${roof[1].toFixed(2)} in · ag ${ag[0].toFixed(3)} / ${ag[1].toFixed(3)} g`;
  if (document.getElementById('fAg').checked){ const a = Math.hypot(ag[0], ag[1]); showDirection(a > 0.02*rec.pga ? [ag[0]/a, ag[1]/a, 0] : null, 0x3fc1a0); } else showDirection(null);
  // story drifts now (from the masters -> rigid-body, no torsional edge component; peaks in the tables are the 16.4.1.2 edge values)
  const dX=[], dY=[]; let px=0, py=0; F.story[step].forEach((f,i)=>{ dX.push((f[0]-px)/N.heights[i]); dY.push((f[1]-py)/N.heights[i]); px=f[0]; py=f[1]; });
  const rzr = roof[2];
  document.getElementById('stepStats').innerHTML = rows([['Record', `${rec.label}`], ['Scale factor · components', `${rec.sf.toFixed(2)} · ${rec.x_comp===1?'H1→X, H2→Y':'H2→X, H1→Y'}`], ['Roof displacement X / Y', `${roof[0].toFixed(2)} / ${roof[1].toFixed(2)} in`], ['Roof rotation (torsion)', `${(rzr*1e3).toFixed(3)} mrad`], ['Story drift now, max X / Y', `${pct(Math.max(...dX.map(Math.abs)))} / ${pct(Math.max(...dY.map(Math.abs)))}`], ['Record peak drift X / Y (edges)', `${pct(Math.max(...rec.peak_drift.map(d=>d[0])))} / ${pct(Math.max(...rec.peak_drift.map(d=>d[1])))}`], ['Residual drift (end)', pct(Math.max(...rec.residual))], ['16.4.1.1', rec.ok?'<span class="badge ok">acceptable response</span>':'<span class="badge ng">UNACCEPTABLE</span>'+(rec.flags.length?' · '+rec.flags.join('; '):'')]]);
  const lim = N.limits.mean_limit; const scale = Math.max(0.002, ...rec.peak_drift.flat(), ...N.story.map(s=>Math.max(s.mean_X, s.mean_Y)))*1.35; const limIn = lim <= scale;
  const bar = (v, pk, mean, col) => `<span style="flex:1;margin:0 6px;height:8px;background:#1d222b;border-radius:3px;position:relative;top:4px">
      <span style="position:absolute;left:0;top:0;height:8px;width:${(100*Math.min(pk,scale)/scale).toFixed(1)}%;background:${col};opacity:.35;border-radius:3px"></span>
      <span style="position:absolute;left:0;top:0;height:8px;width:${(100*Math.min(Math.abs(v),scale)/scale).toFixed(1)}%;background:${col};border-radius:3px"></span>
      <span style="position:absolute;left:${(100*Math.min(mean,scale)/scale).toFixed(1)}%;top:-2px;height:12px;width:2px;background:#dde3ea"></span>
      ${limIn?`<span style="position:absolute;left:${(100*lim/scale).toFixed(1)}%;top:-2px;height:12px;width:1px;background:#d13b3b"></span>`:''}</span>`;
  document.getElementById('driftBars').innerHTML = dX.map((d,i)=>{ const S = N.story[i]; return `<div class="stat"><span style="min-width:54px">story ${i+1}</span><span style="color:#3fc1a0;min-width:10px">X</span>${bar(d, rec.peak_drift[i][0], S.mean_X, '#3fc1a0')}<b style="min-width:46px;text-align:right">${pct(rec.peak_drift[i][0])}</b></div>
      <div class="stat" style="margin-top:-2px"><span style="min-width:54px"></span><span style="color:#5b8dd9;min-width:10px">Y</span>${bar(dY[i], rec.peak_drift[i][1], S.mean_Y, '#5b8dd9')}<b style="min-width:46px;text-align:right">${pct(rec.peak_drift[i][1])}</b></div>`; }).join('')
    + `<div style="font-size:10.5px;color:#8b95a3;margin-top:4px">solid = now (masters) · faded = record peak (edges) · white tick = suite mean · ${limIn?'red = ':''}mean limit ${pct(lim)} (2 × Table 12.12-1)${limIn?'':' is off the bar scale (×'+(lim/scale).toFixed(0)+')'}</div>`;
  const nb = k => BIDX.filter(hi=>st[hi]===k).length;
  const pk = PEAK;
  document.getElementById('census').innerHTML = rows([['Braces buckled now (Δ > Δc)', nb('buckled')+nb('ls')+nb('cp')+nb('beyond')], ['Braces yielded in tension now', nb('tension')], ['Braces past CP now', nb('cp')+nb('beyond')],
    ['Braces past CP · record peak', BIDX.filter(hi=>RANK[pk[hi]]>=4).length], ['Beam hinges > IO · record peak', HZ.filter((h,i)=>h.kind==='beam'&&RANK[pk[i]]>=2).length], ['Column hinges > IO · record peak', HZ.filter((h,i)=>h.kind==='col'&&RANK[pk[i]]>=2).length], ['Beyond b (valid range) · record peak', HZ.filter((h,i)=>pk[i]==='beyond').length]]);
  redrawChart();
}
function redrawChart(){
  const rec = R(); const F = rec.frames; const tmax = F.t[F.t.length-1];
  let s1, s2, ylabel, yfmt, title, hl = [];
  if (chartMode==='ag'){ s1 = F.t.map((t,i)=>[t, F.ag[i][0]]); s2 = F.t.map((t,i)=>[t, F.ag[i][1]]); ylabel='g'; yfmt=v=>v.toFixed(2); title=`Scaled ground acceleration — ${rec.label} · SF ${rec.sf.toFixed(2)}`; }
  else if (chartMode==='drift'){ s1 = F.t.map((t,i)=>{ const r=F.story[i][F.story[i].length-1]; return [t, 100*r[0]/N.H]; }); s2 = F.t.map((t,i)=>{ const r=F.story[i][F.story[i].length-1]; return [t, 100*r[1]/N.H]; }); ylabel='% hn'; yfmt=v=>v.toFixed(2); title=`Roof drift ratio — ${rec.label}`; }
  else { s1 = F.t.map((t,i)=>{ const r=F.story[i][F.story[i].length-1]; return [t, r[0]]; }); s2 = F.t.map((t,i)=>{ const r=F.story[i][F.story[i].length-1]; return [t, r[1]]; }); ylabel='in'; yfmt=v=>v.toFixed(1); title=`Roof displacement — ${rec.label} · SF ${rec.sf.toFixed(2)}`;
    if (N.pushover_dt){ for (const [d,v] of Object.entries(N.pushover_dt)) hl.push({y:v, color:'#ffb454', label:`pushover δt BSE-2N ${d} ${v.toFixed(1)} in`, align: d==='X'?'left':'right'}); } }
  const ymax = Math.max(1e-6, ...s1.map(p=>Math.abs(p[1])), ...s2.map(p=>Math.abs(p[1])), ...hl.map(h=>Math.abs(h.y)))*1.15;
  const cur = chartMode==='ag' ? F.ag[step][0] : (chartMode==='drift' ? 100*F.story[step][F.story[step].length-1][0]/N.H : F.story[step][F.story[step].length-1][0]);
  drawChart({xmin:0, xmax:tmax, ymin:-ymax, ymax:ymax, series:[{pts:s1, color:'#3fc1a0', width:1.5}, {pts:s2, color:'#5b8dd9', width:1.5}],
    bands:[{x0:rec.t_start, x1:rec.t_sig, color:'rgba(63,193,160,0.07)', label:'significant duration (Arias 0.1–99.5%)'}, {x0:rec.t_sig, x1:tmax, color:'rgba(255,255,255,0.03)', label:'free vibration'}],
    hlines:[{y:0, color:'#3a4656', label:''}].concat(hl), cursor:[F.t[step], cur], cursorColor:'#3fc1a0', title, xlabel:'t (s)   —  X green · Y blue', ylabel, yfmt, xfmt:v=>v.toFixed(0)});
}
function onChartSeek(x){ const T=R().frames.t; let best=0,bd=1e9; T.forEach((t,i)=>{const d=Math.abs(t-x); if(d<bd){bd=d;best=i;}}); step=best; update(); }
function onDeformed(){} function onVisibility(){ paint(step); }
function onSpace(){ playing=!playing; document.getElementById('play').textContent = playing?'❚❚ pause':'▶ play'; }
let acc=0; function onTick(dt){ if(!playing) return; acc+=dt*speed; const fdt = R().frames.dt; while(acc>=fdt){ acc-=fdt; step++; if(step>=frameCount()){ step=0; } } update(); }
function memberInfo(e){
  const idx = hingeByEle[e.tag]||[]; const st = statesAt(step); const rec = R();
  const lines = [['Length', `${e.L?e.L.toFixed(0):'—'} in`], ['Level', e._lvl]];
  idx.forEach(i=>{ const h=HZ[i]; const p=rec.hinge_peak[i];
    if (h.kind==='brace'){ const j = BIDX.indexOf(i); const v = rec.frames.brace[step][j];
      lines.push(['Δ axial now (+tens)', `${v.toFixed(3)} in`], ['Record peak tens / comp', `${p[0].toFixed(3)} / ${p[1].toFixed(3)} in`], ['Δc / Δy', `${h.dc.toFixed(3)} / ${h.dT.toFixed(3)} in`], ['CP comp / tens', `${h.CP.toFixed(2)} / ${h.CP_t.toFixed(2)} in`], ['State now · record peak', `${STATE_LABEL[st[i]]} · ${STATE_LABEL[PEAK[i]]}`]); }
    else lines.push([`θpl peak, end ${h.end}`, `${p.toExponential(2)} rad`], ['θy · IO · LS · CP', `${h.thy.toExponential(1)} · ${h.IO.toExponential(1)} · ${h.LS.toExponential(1)} · ${h.CP.toExponential(1)}`], ['D/C CP (this record)', (Math.abs(p)/h.CP).toFixed(2)], ['Record peak state', STATE_LABEL[PEAK[i]]]);
  });
  const fc = N.force_controlled[e.tag]; if (fc) lines.push(['16.4.2.1 force-controlled (suite mean)', `Qu ${fc.Qu.toFixed(0)} kip · demand ${fc.demand.toFixed(0)} ≤ φBRn ${fc.phiBRn.toFixed(0)} → D/C ${fc.DC.toFixed(2)} ${fc.DC<=1?'<span class="badge ok">ok</span>':'<span class="badge ng">NG</span>'}`]);
  const row = N.schedule[e.tag]; if (row) lines.push(['Steltic governing combo', row.combo], ['Steltic demand P / Mx', `${row.P} kip / ${row.Mx} k-ft`]);
  return rows(lines);
}
function suite(){
  const v = N.verdict, L = N.limits; const r = [];
  r.push(['Overall', v.overall?'<span class="badge ok">ACCEPTABLE</span>':'<span class="badge ng">NOT ACCEPTABLE</span>'], ['Records · unacceptable (allowed)', `${v.n_records} · ${v.n_unacceptable} (${v.unacceptable_allowed})`],
    ['Mean story drift max ≤ limit', `${pct(v.mean_drift_max||0)} ≤ ${pct(L.mean_limit)} ${v.mean_drift_ok?'<span class="badge ok">ok</span>':'<span class="badge ng">NG</span>'}`],
    ['Deformation-controlled · worst D/C CP', `${fmt(N.worst_DC_CP)} ${v.deformation_ok?'<span class="badge ok">ok</span>':'<span class="badge ng">NG</span>'}`], ['Valid range b · worst D/C', `${fmt(N.worst_DC_b)} ${v.valid_range_ok?'<span class="badge ok">ok</span>':'<span class="badge ng">NG</span>'}`],
    ['Force-controlled columns · worst D/C', `${fmt(N.worst_DC_fc)} ${v.force_controlled_ok?'<span class="badge ok">ok</span>':'<span class="badge ng">NG</span>'}`],
    ['Target · damping', `MCE<sub>R</sub> = 1.5 × design (S<sub>MS</sub> ${N.SMS.toFixed(2)} g) · ξ ${(100*N.xi).toFixed(1)}%`], ['Period range · scaling', `${N.period_range[0].toFixed(2)}–${N.period_range[1].toFixed(2)} s · suite mean / target min ${N.scaling_min.toFixed(3)}`],
    ['T1 X / Y (hinge model)', `${N.T1x.toFixed(3)} / ${N.T1y.toFixed(3)} s`], ['Component parameters', N.params_verified?'verified':'<span class="badge ng">UNVERIFIED placeholders</span>']);
  document.getElementById('suiteTable').innerHTML = rows(r);
}
function init(){
  const sel = document.getElementById('recSel'); RECS.forEach((r,i)=>{ const o=document.createElement('option'); o.value=i; o.textContent=`${i+1}. ${r.label}  ·  SF ${r.sf.toFixed(2)}  ·  drift ${pct(Math.max(...r.peak_drift.flat()))}${r.ok?'':'  ·  UNACCEPTABLE'}`; sel.appendChild(o); });
  sel.onchange = e => { ri=parseInt(e.target.value); step=0; PEAK=peakStates(R()); update(); };
  document.getElementById('step').oninput = e => { step=parseInt(e.target.value); update(); };
  const peakIdx = c => { const F=R().frames; let b=0,bv=-1; F.story.forEach((f,i)=>{ const v=Math.abs(f[f.length-1][c]); if(v>bv){bv=v;b=i;} }); return b; };
  document.getElementById('toPX').onclick = () => { step=peakIdx(0); update(); };
  document.getElementById('toPY').onclick = () => { step=peakIdx(1); update(); };
  document.getElementById('toEnd').onclick = () => { step=frameCount()-1; update(); };
  document.getElementById('play').onclick = onSpace;
  document.getElementById('speed').onchange = e => { speed=parseFloat(e.target.value); };
  document.getElementById('chartMode').onchange = e => { chartMode=e.target.value; redrawChart(); };
  document.getElementById('colorMode').onchange = e => { colorMode=e.target.value; setLegend(); paint(step); };
  document.getElementById('hingeMode').onchange = () => paint(step);
  document.getElementById('fAg').onchange = () => update();
  setLegend(); suite(); update();
}
function setLegend(){ if (colorMode==='state') legend([[STATE.elastic,'elastic'],[STATE.yield,'yielded / brace tension yield'],[STATE.buckled,'brace buckled (Δ > Δc)'],[STATE.io,'> IO'],[STATE.ls,'> LS'],[STATE.cp,'> CP'],[STATE.beyond,'beyond b (valid range)']], 'Component state · '+(LIVE_HINGES?'every hinge at this instant':'braces at this instant, beams/columns at their record peak (suite run before per-frame hinges: re-run to see them form)')+' (parameters: '+(N.params_verified?'verified':'UNVERIFIED placeholders')+')', '<span style="color:#d13b3b;font-size:14px;vertical-align:-1px">●</span> solid dot = plastic hinge formed at that member end (brace: mid-length buckling / tension yield); dot colour follows the state, or red for any hinge (Hinge dots selector)');
  else if (colorMode==='type') legend([[TYPE_COL.column,'column'],[TYPE_COL.beam,'beam'],[TYPE_COL.brace,'brace']], 'Member type'); else legend(SECS.map(s=>[SEC_COL[s],s]), 'Section'); }
"""


def _spec_dict(kind, s):
    if kind == "brace":
        return dict(dc=s.dc, dT=s.dT, IO=s.IO, LS=s.LS, CP=s.CP, b=s.b_c, IO_t=s.IO_t, LS_t=s.LS_t, CP_t=s.CP_t, b_t=s.b_t, thy=s.dc)
    return dict(thy=s.theta_y, IO=s.IO, LS=s.LS, CP=s.CP, b=s.b_pl)


def write(outdir, pkg, prm, ch16, gm, results, acc, modal, xi, pushover_pkg=None):
    """Build nlrha_viewer_3d.html from the suite results (called by `run` and by `report` from raw_results.pkl)."""
    model = VC.model_from_package(pkg)
    b = pkg.basis
    good = [r for r in results if r.get("converged") and "frames" in r]
    if not good:
        raise RuntimeError("no converged records with viewer frames (re-run the suite with this version of nlrha)")
    ref = good[0]
    hz = sorted(ref["hinges_meta"])
    HZ = []
    for t in hz:
        m = ref["hinges_meta"][t]
        end = m.get("end", 0 if m["kind"] == "brace" else t % 10)      # zeroLength tag = ZL_BASE + ele*10 + end
        d = dict(tag=t, ele=m["ele"], end=end, kind=m["kind"], sec=m["section"], z=round(m["z"]))
        d.update(_spec_dict(m["kind"], ref["specs"][t]))
        HZ.append(d)
    pos = {t: i for i, t in enumerate(hz)}
    brace_tags = ref["frames"]["brace_tags"]
    brace_idx = [pos[t] for t in brace_tags]
    # frames.hinge[s][j] is hinge HZ[hinge_idx[j]] at frame s. Absent from suites run before the
    # per-frame hinge record existed; the page falls back to the record peak for those, which is
    # what every suite used to get.
    hinge_tags = ref["frames"].get("hinge_tags") or []
    hinge_idx = [pos[t] for t in hinge_tags if t in pos]
    heights = ref["heights"]
    recs = []
    for r in results:
        if not (r.get("converged") and "frames" in r):
            continue
        F = r["frames"]
        hp = []
        for t in hz:
            if ref["hinges_meta"][t]["kind"] == "brace":
                p, n = r["signed_def"][t]; hp.append([round(p, 4), round(-n, 4)])        # [tension peak, compression peak] (both positive)
            else:
                hp.append(round(r["peak_def"][t], 6))
        per = next((p for p in acc["per_record"] if p["record"] == r["record"]), {})
        ag = F["ag"]; pga = max((max(abs(a[0]), abs(a[1])) for a in ag), default=1.0) or 1.0
        recs.append(dict(id=r["record"], label=r["label"], sf=round(r["sf"], 3), x_comp=r["x_comp"], ok=not per.get("unacceptable", False), flags=per.get("flags", []),
                         peak_drift=[[round(v, 5) for v in row] for row in r["peak_story_drift"]], peak_roof=r["peak_roof_in"], residual=[round(v, 5) for v in r["residual_drift"]],
                         t_start=round(r["t_window"][0], 2), t_sig=round(r["t_window"][1], 2), T1x=r["T1x"], T1y=r["T1y"], pga=pga,
                         frames=dict(t=F["t"], dt=(F["t"][1] - F["t"][0]) if len(F["t"]) > 1 else 0.1, story=F["story"], brace=F["brace"],
                                     **({"hinge": F["hinge"]} if F.get("hinge") else {}), ag=ag), hinge_peak=hp))
    v = acc["verdict"]
    fc = {r["ele"]: dict(Qu=r["Qu"], demand=r["demand"], phiBRn=r["phiBRn"], DC=r["DC"]) for r in acc["force_controlled_columns"]}
    dg = acc["deformation_groups"]
    sched = {t: dict(combo=rw.get("governing_combo", ""), P=round(rw.get("P_comp_kip", 0)), Mx=round(rw.get("Mx_kipft", 0))) for t, rw in pkg.schedule.items()}
    for e in model["elements"]:
        n1, n2 = model["nodes"][str(e["n"][0])], model["nodes"][str(e["n"][1])]
        e["L"] = round(sum((a - c) ** 2 for a, c in zip(n1, n2)) ** 0.5, 1)
    pdt = None
    if pushover_pkg:
        try:
            pdt = {d: pushover_pkg["directions"][d]["nsp"]["BSE-2N"]["target_disp_in"] for d in pushover_pkg["directions"]}
        except Exception:
            pdt = None
    extra = dict(nlrha=dict(records=recs, hinges=HZ, brace_idx=brace_idx, hinge_idx=hinge_idx, heights=heights, H=sum(heights), story=acc["story"], limits=acc["limits"], verdict=v,
                            worst_DC_CP=max([g["DC_CP"] for g in dg], default=None), worst_DC_b=max([g["DC_valid"] for g in dg], default=None),
                            worst_DC_fc=max([r["DC"] for r in acc["force_controlled_columns"]], default=None), force_controlled=fc,
                            SMS=1.5 * b.SDS, xi=xi, period_range=[gm.get("T_lower"), gm.get("T_upper")], scaling_min=gm.get("min_ratio_in_range", float("nan")),
                            T1x=modal["T1x"], T1y=modal["T1y"], params_verified=bool(prm.get("verified")), schedule=sched, pushover_dt=pdt))
    n_ok = sum(1 for r in recs if r["ok"])
    meta = dict(title=f"{pkg.name} · NLRHA (ASCE 7-22 Chapter 16)", subtitle=f"{b.system or ''}",
                accline=f"Steltic viewer bundle · Non Linear Dynamic Bot · MCE_R target · {len(recs)} records ({n_ok} acceptable) · {'ACCEPTABLE' if v['overall'] else 'NOT ACCEPTABLE'}",
                caveat=("Deformed shape from the diaphragm masters (rigid floors) every %.2f s; braces coloured by their instantaneous axial deformation, beams and columns by "
                        "their peak plastic rotation over the record. Drift bars: record peaks are the 16.4.1.2 edge values, the live value is the master (rigid-body) drift. "
                        % recs[0]["frames"]["dt"]
                        + ("Component parameters UNVERIFIED (placeholders) — see the red banner in nlrha_report.html. " if not prm.get("verified") else "")
                        + "Not for construction."))
    return VC.render(os.path.join(outdir, "nlrha_viewer_3d.html"), f"{pkg.name} · NLRHA viewer", "nlrha", meta, model, extra, LEFT, RIGHT, MODULE_JS, amp=10,
                     root=str(pkg.root))
