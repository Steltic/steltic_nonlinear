/* Nonlinear (SNL) feedback loops — what Chapter 16 measured, three Go buttons, the re-design, the verdict, design of record. */
'use strict';

const qs = new URLSearchParams(location.search);
const S = {
  project: (qs.get('project') || 'Project').replace(/[^A-Za-z0-9_-]/g, '') || 'Project',
  me: null, proj: null, step: 1, kind: null, plan: null, opts: {}, edits: {}, verify: 'full', parallel: null,
  run: null, runState: null, es: null, lastSeq: 0, log: [], hrText: '', hrReason: '', include: {},
};
const $ = (s, r = document) => r.querySelector(s);
const enc = encodeURIComponent;
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'style') n.style.cssText = v;
    else if (k.startsWith('on')) n[k] = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k in n && k !== 'list' && k !== 'form') { try { n[k] = v; } catch (e) { n.setAttribute(k, v); } }
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : document.createTextNode(String(k)));
  return n;
}
function toast(msg, kind = '') { const t = el('div', { class: 'toast ' + kind }, msg); $('#toasts').append(t); setTimeout(() => t.remove(), kind === 'bad' ? 9000 : 4500); }
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  const txt = await r.text(); let d; try { d = JSON.parse(txt); } catch (e) { d = { detail: txt }; }
  if (!r.ok) throw new Error((d && (d.detail || d.error)) || `${r.status} ${r.statusText}`);
  return d;
}
function openModal(title, body, buttons) { const m = $('#modal'); $('.mh', m).textContent = title; $('.mb', m).replaceChildren(...body); $('.mf', m).replaceChildren(...buttons); m.hidden = false; }
function closeModal() { $('#modal').hidden = true; }
const pct = (v, d = 2) => (v === null || v === undefined) ? '—' : `${(100 * v).toFixed(d)}%`;
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : (typeof v === 'number' ? v.toFixed(d) : String(v));
const P = () => `/api/project/${enc(S.project)}`;

/* ---------------------------------------------------------------- load */
async function load() {
  $('#projname').textContent = S.project;
  [S.me, S.proj] = await Promise.all([api('/api/me'), api(P())]);
  if (S.parallel === null) S.parallel = Math.max(1, Math.min(4, Math.floor(S.me.cpu_count / 2)));
  if (S.proj.running) { S.run = S.proj.running; S.step = 3; await loadRun(); attachEvents(); }
  else if (S.step === 1 && S.proj.loops && S.proj.loops.length && !S.kind) { S.run = S.proj.loops[S.proj.loops.length - 1].id; }
  header(); render();
}
async function refresh() { S.proj = await api(P()); header(); }
function header() {
  const n = $('#status'); n.className = 'note'; const bits = [];
  if (!S.me.steltic_url) { bits.push('HR Steel URL not set'); n.classList.add('bad'); }
  else bits.push(S.me.hr_ok ? 'HR Steel connected' : 'HR Steel not reachable');
  if (!S.me.hr_ok) n.classList.add('bad');
  bits.push(S.me.engine_ok ? 'engine ok' : 'engine dir missing (DDM re-check off)');
  n.textContent = bits.join(' · ');
}

/* ---------------------------------------------------------------- steps */
const STEPS = [[1, 'Measured'], [2, 'Loops'], [3, 'Runs']];
function stepState(k) {
  const p = S.proj;
  if (k === 1) return p.status ? (p.status.nlrha ? `Ch. 16 ${p.status.nlrha_verdict}` : 'no NLRHA yet') : 'no package';
  if (k === 2) return p.plans ? `${Object.values(p.plans).filter(x => x.eligible).length} of 3 eligible` : '';
  if (k === 3) return p.running ? 'running…' : (p.loops && p.loops.length ? `${p.loops.length} run${p.loops.length > 1 ? 's' : ''}` : '');
  return '';
}
function render() {
  $('#steps').replaceChildren(...STEPS.map(([k, t]) => el('div', { class: 'step' + (S.step === k ? ' on' : '') + (stepState(k) && S.step !== k ? ' done' : ''),
    onclick: () => { S.step = k; render(); } }, el('span', { class: 'k' }, k), el('span', {}, t), stepState(k) ? el('span', { class: 'st' }, stepState(k)) : null)));
  const main = $('#main'); main.replaceChildren();
  ({ 1: paneMeasured, 2: paneLoops, 3: paneRuns })[S.step](main);
}
function stat(l, v, s) { return el('div', { class: 'stat' }, el('div', { class: 'l' }, l), el('div', { class: 'v' }, v), s ? el('div', { class: 's' }, s) : null); }

/* ---------------------------------------------------------------- 1. measured */
function paneMeasured(main) {
  const p = S.proj;
  const pane = el('div', { class: 'pane' });
  pane.append(el('h2', {}, 'What the nonlinear analyses measured'),
    el('div', { class: 'about' },
      el('p', {}, el('b', {}, 'The idea. '), 'HR Steel sizes the building against the linear code checks: member D/C for every LRFD combination and the C', el('sub', {}, 'd'), '·δ', el('sub', {}, 'e'), '/I', el('sub', {}, 'e'), ' drift of §12.12.1. The three SNL analyses measure what the building actually does — the MCE', el('sub', {}, 'R'), ' drift of a Chapter 16 suite, the mechanism a pushover forms, the load factor at which the imperfect structure loses stability. Where the two pictures disagree, this tab hands a precise re-design instruction back to HR Steel, verifies the result with the same analyses, and — only when you say so — makes it the design of record.'),
      el('p', {}, 'Nothing here is required by code. Each loop is a documented, reversible step: the previous design is archived on both sides, and the brief HR Steel receives is shown before it is sent.')));
  if (!p.exists) { pane.append(el('div', { class: 'card' }, el('p', { class: 'lead' }, p.hint || 'No package in this project.'))); main.append(pane); return; }
  const st = p.status; const d = (p.plans && p.plans.drift && p.plans.drift.numbers) || {};
  pane.append(el('div', { class: 'stats' },
    stat('Building', st.name, `${st.levels} storeys · ${st.groups} member groups · Risk Category ${st.risk_category.replace('_', '/')}`),
    stat('Steel', `${st.tons.total} t`, `${st.tons.lateral} t in the lateral system`),
    stat('Linear drift', d.linear_drift !== undefined ? pct(d.linear_drift) : '—', d.linear_limit ? `vs ${pct(d.linear_limit)} allowable (C_d δ_e / I_e)` : 'report.html Chapter 8'),
    stat('Chapter 16', st.nlrha ? st.nlrha_verdict : 'not run', st.nlrha ? `mean MCE_R drift ${pct(st.nlrha_mean_drift)} vs ${pct(st.nlrha_limit)} (16.4.1.2)` : 'run the Nonlinear analyses first'),
    stat('Margin Ch. 16 never designed for', d.margin !== undefined && d.margin !== null ? pct(d.margin, 0) : '—', d.prize || ''),
    stat('Pushover / DDM', `${st.pushover ? 'pushover ✓' : 'pushover —'} · ${st.ddm ? 'DDM ✓' : 'DDM —'}`, st.finished ? `last run finished ${st.finished}` : '')));
  if (st.relief) pane.append(el('div', { class: 'card tight' }, el('span', { class: 'pill warn' }, '16.1.2 relief in force'), ' ', el('span', { class: 'hint' }, 'this package already carries drift_relief_16_1_2 — it is a relieved design; verify it, do not relax it again.')));
  const links = el('div', { class: 'card tight row' }, el('span', { class: 'hint' }, 'Files:'));
  for (const [lab, f] of [['four analyses', 'four_analyses.html'], ['HR Steel report', 'report.html'], ['NLRHA report', 'nlrha/nlrha_report.html'], ['pushover report', 'pushover/pushover_report.html'], ['DDM report', 'ddm_report.html']])
    links.append(el('a', { href: `${P()}/file/${f}`, target: '_blank' }, lab));
  pane.append(links);
  pane.append(el('div', { class: 'row' }, el('button', { class: 'primary', onclick: () => { S.step = 2; render(); } }, 'Loops →')));
  main.append(pane);
}

/* ---------------------------------------------------------------- 2. loops */
const EXPLAIN = {
  drift: 'HR Steel sizes moment frames against §12.12.1 with C_d·δ_e/I_e — that coefficient sets the steel tonnage in a drift-governed building. Chapter 16 measures the MCE_R drift directly. §16.1.2 says the §12.12.1 limits need not apply for Risk Category I–III when a Chapter 16 analysis is performed, so the linear target is reset to the measured response, HR Steel lightens the frame, and the NLRHA is re-run to confirm the result is still inside §16.4. (Risk Category IV gets no relief — the numbers only show the size of the prize.)',
  resize: 'Member D/C and system role barely correlate. This joins the DDM member table (strain ratio, yielded count, hinges at collapse), the NLRHA rotation-vs-CP groups and the pushover acceptance against calc_package.json D/C, and emits the change set an engineer wants: groups that carry a code check nothing in the system is asking for, and groups that are the actual bottleneck. HR Steel applies it; the DDM re-verifies with --only on the governing combination — minutes, not the full hour.',
  mechanism: 'The pushover\'s component acceptance says which ends reached θ_y, IO, LS and CP and at which storeys — a direct read on whether AISC 341\'s joint-by-joint SCWB rule produced the global mechanism it is meant to. The AISC 342 Table C5.5 modifier machinery flags joints failing the C5.4a.1.a.1 panel-zone condition. Both map to HR Steel inputs: a higher SCWB ratio at the misbehaving storeys, doublers where the modifier fired. Then re-push and check the hinges moved into the beams and the modifier cleared.',
};
function paneLoops(main) {
  const p = S.proj; const pane = el('div', { class: 'pane' });
  pane.append(el('h2', {}, 'Three loops back to HR Steel'), el('p', { class: 'lead' }, 'Each card reads the analyses in this project folder. Review the change set, then Go: HR Steel re-designs a candidate copy, the Nonlinear module verifies it, and you decide whether it becomes the design of record.'));
  if (!p.plans) { pane.append(el('div', { class: 'card' }, el('p', { class: 'lead' }, p.hint || 'No package.'))); main.append(pane); return; }
  const grid = el('div', { class: 'loops' });
  for (const k of ['drift', 'resize', 'mechanism']) {
    const pl = p.plans[k]; const nums = [];
    if (k === 'drift' && pl.numbers) {
      const n = pl.numbers;
      nums.push(['linear drift', `${pct(n.linear_drift)} / ${pct(n.linear_limit)}`], ['Ch. 16 mean', `${pct(n.nlrha_mean_drift)} / ${pct(n.nlrha_limit)}`], ['new linear target', n.new_cfg_drift_limit ? pct(n.new_cfg_drift_limit) : '—'], ['scale', num(n.scale)]);
    }
    if (k === 'resize' && pl.rows) {
      const up = (pl.change_set || []).filter(c => c.verdict === 'upsize').length, dn = (pl.change_set || []).filter(c => c.verdict === 'downsize').length;
      nums.push(['upsize', up], ['downsize', dn], ['held', (pl.held || []).length], ['drift utilisation', num(pl.drift_utilisation)]);
    }
    if (k === 'mechanism' && pl.storeys) {
      nums.push(['storeys with column hinging', pl.storeys.length], ['panel-zone modifier', pl.panel_zone ? `x${num(pl.panel_zone.modifiers[0].factor)}` : 'none'],
        ['SCWB (design)', pl.scwb_design ? num(pl.scwb_design.ratio) : '—'], ['doubler (design)', pl.panel_zone && pl.panel_zone.design_block ? `${pl.panel_zone.design_block.doubler_in} in` : '—']);
    }
    grid.append(el('div', { class: 'loop' + (S.kind === k ? ' on' : '') },
      el('div', { class: 'row' }, el('h4', {}, `${k === 'drift' ? '1' : k === 'resize' ? '2' : '3'}. ${pl.title}`), el('span', { class: 'sp' }), el('span', { class: 'pill ' + (pl.eligible ? 'ok' : 'dim') }, pl.eligible ? 'eligible' : 'not eligible')),
      el('p', {}, EXPLAIN[k]),
      el('div', { class: 'nums' }, ...nums.flatMap(([l, v]) => [el('span', {}, l), el('b', {}, v)])),
      pl.reasons && pl.reasons.length ? el('div', { class: 'why' }, pl.reasons.join(' · ')) : null,
      el('div', { class: 'foot' }, el('button', { class: pl.eligible ? 'primary' : '', onclick: () => choose(k) }, pl.eligible ? 'Review & Go' : 'Review'))));
  }
  pane.append(grid);
  if (S.kind && S.plan) pane.append(detail());
  main.append(pane);
}
async function choose(kind) {
  S.kind = kind; S.edits = {}; S.opts = {}; S.include = {};
  S.plan = await api(`${P()}/plan`, { method: 'POST', body: { kind } });
  render(); setTimeout(() => { const d = $('#detail'); if (d) d.scrollIntoView({ behavior: 'smooth' }); }, 50);
}
async function replan() {
  S.plan = await api(`${P()}/plan`, { method: 'POST', body: { kind: S.kind, options: S.opts, edits: S.edits } });
  render();
}
function detail() {
  const pl = S.plan; const box = el('div', { class: 'card', id: 'detail' });
  box.append(el('h2', {}, pl.title), el('p', { class: 'lead' }, 'Sources: ' + Object.entries(pl.sources || {}).map(([k, v]) => `${k} — ${v}`).join(' · ')));
  if (pl.kind === 'drift') box.append(detailDrift(pl));
  if (pl.kind === 'resize') box.append(detailResize(pl));
  if (pl.kind === 'mechanism') box.append(detailMechanism(pl));
  box.append(el('h3', {}, 'The brief HR Steel receives'), el('pre', { class: 'brief' }, pl.brief || ''));
  const verify = el('div', { class: 'choices' });
  for (const [v, t, s] of [['full', 'Full verification', pl.kind === 'drift' ? 'the complete Chapter 16 suite on the candidate' : pl.kind === 'resize' ? 'DDM on the governing combination(s) + the Chapter 16 suite' : 'the re-push + the Chapter 16 suite'],
    ['quick', 'Quick verification', pl.kind === 'drift' ? 'the three governing records only (a screen, not a code check)' : pl.kind === 'resize' ? 'DDM on the governing combination(s) only — minutes' : 're-push only'],
    ['none', 'No analysis', 'package checks only: D/C, drift, the change set applied']])
    verify.append(el('div', { class: 'choice' + (S.verify === v ? ' on' : ''), onclick: () => { S.verify = v; render(); } }, el('b', {}, t), el('span', {}, s)));
  box.append(el('h3', {}, 'Verification after HR Steel returns'), verify);
  const go = el('div', { class: 'row' },
    el('label', { class: 'f' }, 'workers ', el('input', { type: 'number', min: 1, max: 16, value: S.parallel, onchange: e => { S.parallel = +e.target.value || 1; } })),
    el('span', { class: 'sp' }),
    el('button', { onclick: () => navigator.clipboard && navigator.clipboard.writeText(pl.brief || '').then(() => toast('brief copied')) }, 'Copy brief'),
    el('button', { class: 'primary', disabled: !pl.eligible || !!S.proj.running, onclick: go_ }, `Go — send to HR Steel as ${S.project}__${pl.kind}`));
  box.append(el('div', { class: 'hint' }, S.proj.hr_job && S.proj.hr_job.resumable === false ? `HR Steel holds no resumable job "${S.project}" — the package in this project folder is sent instead (it must contain conversation.json).` : ''), go);
  return box;
}
function detailDrift(pl) {
  const n = pl.numbers || {}; const w = el('div');
  w.append(el('div', { class: 'stats' },
    stat('Linear drift (DE)', pct(n.linear_drift), `allowable ${pct(n.linear_limit)} · cfg drift_limit ${num(n.cfg_drift_limit, 4)}`),
    stat('Chapter 16 mean drift (MCE_R)', pct(n.nlrha_mean_drift), `limit ${pct(n.nlrha_limit)} · ${n.nlrha_verdict}`),
    stat('Scale', num(n.scale, 3), `= ${num(n.target_fraction)} × limit / mean`),
    stat('New linear target', n.new_cfg_drift_limit ? pct(n.new_cfg_drift_limit) : '—', `cfg['drift_limit'] = ${num(n.new_cfg_drift_limit, 4)} (Table 12.12-1: ${pct(n.table_12_12_1)})`)));
  w.append(el('div', { class: 'row', style: 'margin:10px 0' }, el('label', { class: 'f' }, 'aim the re-run NLRHA mean drift at this fraction of the 16.4.1.2 limit: '),
    el('input', { type: 'number', step: 0.05, min: 0.5, max: 1.0, value: (S.opts.target_fraction ?? n.target_fraction ?? 0.9), onchange: e => { S.opts.target_fraction = +e.target.value; replan(); } }),
    el('span', { class: 'hint' }, 'the expected linear drift scales with the measured one (first order); the re-run NLRHA is the check')));
  w.append(el('label', { class: 'f' }, 'note to the design engineer (appended to the brief)'), el('textarea', { rows: 2, value: S.edits.brief_note || '', onchange: e => { S.edits.brief_note = e.target.value; replan(); } }));
  if (pl.reasons && pl.reasons.length) w.append(el('p', { class: 'note bad', style: 'margin-top:8px' }, pl.reasons.join(' · ')));
  return w;
}
function detailResize(pl) {
  const w = el('div');
  w.append(el('p', { class: 'hint' }, `Linear drift utilisation ${num(pl.drift_utilisation)}${pl.drift_governed ? ' — drift-governed: lateral groups are held from downsizing until the drift loop has run' : ''}. Edit a proposed section or untick a line; Go sends exactly what the table shows.`));
  const t = el('table', { class: 'plan' }); t.append(el('tr', {}, ...['', 'group · why', 'levels', 'n', 'D/C', 'combo', 'DDM ε/ε_y', 'yielded / hinged', 'NLRHA CP', 'push CP', 'verdict', 'proposed'].map(h => el('th', {}, h))));
  const inCS = new Map((pl.change_set || []).map(c => [c.id, c]));
  for (const r of pl.rows) {
    const c = inCS.get(r.id); const included = c ? (S.include[r.id] !== false) : (S.include[r.id] === true);
    const cb = el('input', { type: 'checkbox', checked: included, onchange: e => { S.include[r.id] = e.target.checked; S.edits.change_set = S.edits.change_set || {}; S.edits.change_set[r.id] = e.target.checked ? (S.edits.change_set[r.id] || (c ? c.proposed : r.proposed) || '') : null; replan(); } });
    const secIn = el('input', { class: 'sec', value: (S.edits.change_set && S.edits.change_set[r.id]) || (c ? c.proposed : (r.proposed || '')), placeholder: 'W14X…', onchange: e => { S.edits.change_set = S.edits.change_set || {}; S.edits.change_set[r.id] = e.target.value.trim() || null; S.include[r.id] = !!e.target.value.trim(); replan(); } });
    t.append(el('tr', { class: (c && !included) ? 'excluded' : '' }, el('td', {}, cb), el('td', {}, el('div', { class: 'id' }, r.id), el('div', { class: 'reason' }, r.reason)), el('td', {}, (r.levels || []).join(', ')), el('td', { class: 'num' }, r.n),
      el('td', { class: 'num' }, num(r.DC)), el('td', {}, r.combo || ''), el('td', { class: 'num' }, num(r.ddm_ratio, 1)), el('td', { class: 'num' }, r.ddm_yielded === null ? '—' : `${r.ddm_yielded} / ${r.ddm_hinges}`),
      el('td', { class: 'num' }, num(r.nl_dc_cp)), el('td', { class: 'num' }, num(r.po_dc_cp)),
      el('td', { class: 'verdict' }, el('span', { class: 'pill ' + (r.verdict === 'upsize' ? 'bad' : r.verdict === 'downsize' ? 'ok' : r.verdict === 'hold' ? 'warn' : 'dim') }, r.verdict), r.bottleneck ? el('span', { class: 'hint' }, ' bottleneck') : null),
      el('td', {}, secIn)));
  }
  w.append(el('div', { class: 'tablewrap' }, t));
  w.append(el('p', { class: 'hint', style: 'margin-top:6px' }, `Verification: ${pl.verify ? pl.verify.note : ''}`));
  return w;
}
function detailMechanism(pl) {
  const w = el('div');
  if (pl.storeys.length) {
    const t = el('table'); t.append(el('tr', {}, ...['level', 'z (in)', 'push', 'column ends yielded', 'beam ends yielded', 'reading', 'required SCWB ratio'].map(h => el('th', {}, h))));
    for (const s of pl.storeys) t.append(el('tr', {}, el('td', {}, s.level), el('td', { class: 'num' }, s.z_in), el('td', {}, s.dirs.join('/')), el('td', { class: 'num' }, s.col_yielded), el('td', { class: 'num' }, s.beam_yielded), el('td', { class: 'reason' }, s.reading), el('td', { class: 'num' }, num(s.scwb_target))));
    w.append(el('h3', {}, 'A. storeys where the columns yielded at BSE-2N'), t,
      el('div', { class: 'row', style: 'margin:8px 0' }, el('label', { class: 'f' }, 'required ΣM*pc/ΣM*pb at those storeys '), el('input', { type: 'number', step: 0.1, min: 1.0, max: 3.0, value: S.edits.scwb_target ?? pl.storeys[0].scwb_target, onchange: e => { S.edits.scwb_target = +e.target.value; replan(); } })));
  } else w.append(el('p', { class: 'hint' }, 'No column yielding above the base at BSE-2N in either push direction — the local SCWB rule produced the intended mechanism.'));
  if (pl.panel_zone) {
    const pz = pl.panel_zone; const t = el('table'); t.append(el('tr', {}, ...['joint group (from the modifier note)', 'V_pz / V_ye now', 'action'].map(h => el('th', {}, h))));
    for (const m of pz.modifiers) for (const j of m.joints) t.append(el('tr', {}, el('td', {}, j.joint), el('td', { class: 'num' }, j.ratio ? `${j.ratio[0]}–${j.ratio[1]}` : '—'), el('td', {}, el('span', { class: 'pill ' + (j.action === 'doublers' ? 'bad' : j.action === 'marginal' ? 'warn' : 'dim') }, j.action))));
    w.append(el('h3', {}, 'B. panel zones'), el('p', { class: 'hint' }, pz.reading), t,
      el('p', { class: 'hint', style: 'margin-top:6px' }, `Design block now: ${pz.design_block ? JSON.stringify(pz.design_block) : '—'}. The modifier is cleared for the re-push only when the candidate records every joint group with V_pz/V_ye inside ${pz.window[0]}–${pz.window[1]}.`));
  } else w.append(el('p', { class: 'hint' }, 'No panel-zone modifier in force in pushover/hinge_params_used.json.'));
  return w;
}
async function go_() {
  try {
    const r = await api(`${P()}/loop`, { method: 'POST', body: { kind: S.kind, options: S.opts, edits: S.edits, verify: S.verify, parallel: S.parallel } });
    toast(`loop ${r.id} started — HR Steel candidate ${r.candidate}`, 'ok');
    S.run = r.id; S.log = []; S.hrText = ''; S.hrReason = ''; S.lastSeq = 0; S.step = 3;
    await refresh(); await loadRun(); attachEvents(); render();
  } catch (e) { toast(e.message, 'bad'); }
}

/* ---------------------------------------------------------------- 3. runs */
async function loadRun() { if (!S.run) return; try { S.runState = await api(`${P()}/loop/${enc(S.run)}`); } catch (e) { S.runState = null; } }
function attachEvents() {
  if (S.es) S.es.close();
  S.es = new EventSource(`${P()}/events?since=${S.lastSeq}`);
  S.es.onmessage = async ev => {
    const e = JSON.parse(ev.data); if (e.seq) S.lastSeq = e.seq;
    if (e.id && e.id !== S.run) return;
    if (e.type === 'log') { S.log.push(e.text); if (S.log.length > 600) S.log.shift(); appendLog(e.text); }
    else if (e.type === 'hr_text') { S.hrText += e.text; const h = $('#hrtext'); if (h) { h.textContent = S.hrText.slice(-6000); h.scrollTop = h.scrollHeight; } }
    else if (e.type === 'hr_reason') { S.hrReason += e.text; const h = $('#hrreason'); if (h) { h.textContent = S.hrReason.slice(-6000); h.scrollTop = h.scrollHeight; const w = $('#hrreasonwrap'); if (w) w.hidden = false; } }
    else if (e.type === 'step' || e.type === 'status' || e.type === 'promoted') { await loadRun(); await refresh(); if (S.step === 3) render(); }
    else if (e.type === 'idle') { S.es.close(); S.es = null; await loadRun(); await refresh(); if (S.step === 3) render(); }
  };
  S.es.onerror = () => { };
}
function appendLog(text) { const box = $('#log'); if (!box) return; box.append(el('div', { class: 'l' + (text.startsWith('!!') || text.startsWith('NG') ? ' err' : text.startsWith('ok') ? ' ok' : text.startsWith('$') ? ' tool' : '') }, text)); box.scrollTop = box.scrollHeight; }
function paneRuns(main) {
  const p = S.proj; const pane = el('div', { class: 'pane' });
  pane.append(el('h2', {}, 'Runs'), el('p', { class: 'lead' }, 'Each run keeps its plan, brief, HR Steel transcript, candidate package and verification under feedback/<id>/ in the project folder.'));
  const runs = el('div', { class: 'runs' });
  for (const r of (p.loops || []).slice().reverse()) runs.append(el('div', { class: 'run' + (S.run === r.id ? ' on' : ''), onclick: async () => { S.run = r.id; S.log = []; S.hrText = ''; S.hrReason = ''; await loadRun(); render(); } },
    el('span', { class: 'pill ' + pillOf(r.status) }, r.status), el('span', { class: 't' }, `${r.id} · ${r.title}`), el('span', { class: 'hint' }, r.verify), r.passed === true ? el('span', { class: 'pill ok' }, 'passed') : r.passed === false ? el('span', { class: 'pill bad' }, 'not passed') : null, r.promoted_at ? el('span', { class: 'pill ok' }, 'design of record') : null));
  pane.append(runs.childElementCount ? runs : el('p', { class: 'hint' }, 'No runs yet.'));
  if (S.run && S.runState) pane.append(runDetail(S.runState));
  main.append(pane);
  if (S.runState) { const box = $('#log'); if (box) { for (const l of (S.runState.log || []).slice(-300)) appendLog(l.text); } const h = $('#hrtext'); if (h && !S.hrText) h.textContent = '(HR Steel narrative streams here; the full transcript is in hr_transcript.txt)'; }
}
function pillOf(st) { return { verified: 'ok', promoted: 'ok', failed: 'bad', hr_failed: 'bad', hr_paused: 'warn', stopped: 'warn', hr_running: 'run', verifying: 'run', planned: 'run' }[st] || 'dim'; }
function runDetail(st) {
  const box = el('div', { class: 'card' });
  const live = !!(S.proj.running && S.proj.running === st.id);
  box.append(el('div', { class: 'row' }, el('h2', {}, `${st.title} · ${st.id}`), el('span', { class: 'pill ' + pillOf(st.status) }, st.status), el('span', { class: 'sp' }),
    live ? el('button', { class: 'danger', onclick: async () => { await api(`${P()}/loop/${enc(st.id)}/stop`, { method: 'POST' }); toast('stop requested'); } }, 'Stop') : null,
    (!live && (st.status === 'verified' || st.status === 'failed') && !st.promoted_at) ? el('button', { class: st.passed ? 'primary' : 'danger', onclick: () => promote(st) }, st.passed ? 'Make this the design of record' : 'Promote anyway…') : null));
  box.append(el('div', { class: 'timeline' }, ...(st.steps || []).map(s => el('div', { class: 'tstep ' + s.status }, el('span', { class: 'd' }), el('span', {}, s.name), s.note ? el('span', { class: 'n' }, s.note) : null))));
  if (st.error) box.append(el('p', { class: 'note bad' }, st.error));
  if (st.promoted_at) box.append(el('p', { class: 'note ok' }, `Design of record since ${st.promoted_at}` + (st.archived ? ` — previous design archived: HR Steel ${st.archived.hr || '(not reachable)'}, hub ${st.archived.hub}` : '')));
  const c = st.comparison;
  if (c) {
    box.append(el('h3', {}, 'Verdict'), el('div', { class: 'crit' }, ...c.criteria.map(x => el('span', { class: 'pill ' + (x.ok ? 'ok' : 'bad') }, x.text))));
    const g = el('div', { class: 'cmp' });
    g.append(cmpCard('Steel', `${c.tons.base.total} → ${c.tons.candidate.total} t`, `${c.tons.delta > 0 ? '+' : ''}${c.tons.delta} t (${c.tons.delta_pct > 0 ? '+' : ''}${c.tons.delta_pct}%) · lateral ${c.tons.base.lateral} → ${c.tons.candidate.lateral} t`));
    g.append(cmpCard('Governing D/C', `${num(c.dc.base)} → ${num(c.dc.candidate)}`, `${c.dc.base_id} → ${c.dc.candidate_id}`));
    g.append(cmpCard('Linear drift', `${pct(c.drift.base)} → ${pct(c.drift.candidate)}`, `allowable ${pct(c.drift.base_limit)} → ${pct(c.drift.candidate_limit)}`));
    if (c.nlrha) { const b = c.nlrha.base, k = c.nlrha.candidate; g.append(cmpCard('Chapter 16', `${b ? pct(b.mean_drift_max) : '—'} → ${pct(k.mean_drift_max)}`, `${k.verdict} · limit ${pct(k.mean_limit)} · ${k.n_unacceptable} unacceptable of ${k.n_records}`)); }
    if (c.ddm) for (const r of c.ddm) g.append(cmpCard(`DDM ${r.label}`, `${r.base_lambda_u ? num(r.base_lambda_u) : '—'} → ${num(r.lambda_u)}`, `${r.check}${r.phi_lambda ? ` · φλ ${num(r.phi_lambda)}` : ''} · ${r.mechanism}`));
    if (c.pushover) { const lv = Object.keys(c.pushover.candidate); g.append(cmpCard('Pushover column yielding (BSE-2N)', lv.map(l => `L${l}: ${(c.pushover.base[l] || {}).col_yielded ?? '—'}→${c.pushover.candidate[l].col_yielded}`).join(' '), (c.pushover.modifier || []).length ? `modifiers still applied: ${c.pushover.modifier.map(m => 'x' + m.factor).join(', ')}` : 'no modifier applied in the re-push')); }
    box.append(g);
    box.append(el('h3', {}, 'Package checks'), el('div', { class: 'crit' }, ...c.checks.items.map(x => el('span', { class: 'pill ' + (x.ok ? 'ok' : 'bad') }, x.text))));
    const t = el('table'); t.append(el('tr', {}, ...['role', 'level', 'before', 'after'].map(h => el('th', {}, h))));
    for (const r of c.groups) t.append(el('tr', { class: r.changed ? 'changed' : '' }, el('td', {}, r.role), el('td', { class: 'num' }, r.level), el('td', {}, r.base.join(', ')), el('td', {}, r.candidate.join(', '))));
    box.append(el('details', {}, el('summary', {}, `Sections by role and level (${c.groups.filter(r => r.changed).length} changed)`), el('div', { class: 'tablewrap', style: 'max-height:360px' }, t)));
    const links = el('div', { class: 'row', style: 'margin-top:8px' }, el('span', { class: 'hint' }, 'Candidate:'));
    for (const [lab, f] of [['HR Steel report', 'candidate/report.html'], ['four analyses', 'candidate/four_analyses.html'], ['NLRHA report', 'candidate/nlrha/nlrha_report.html'], ['pushover report', 'candidate/pushover/pushover_report.html'], ['DDM report', 'candidate/ddm_report.html'], ['brief', 'brief.txt'], ['HR transcript', 'hr_transcript.txt'], ['plan', 'plan.json'], ['candidate.zip', 'candidate.zip']])
      links.append(el('a', { href: `${P()}/loop/${enc(st.id)}/file/${f}`, target: '_blank' }, lab));
    box.append(links);
  }
  if (st.modifier_decision) box.append(el('p', { class: 'hint', style: 'margin-top:8px' }, `Panel-zone modifier: ${st.modifier_decision}`));
  // the re-design as the hub shows a design run: the model's text, and its reasoning in a box of its own
  box.append(el('h3', {}, 'Model output (HR Steel)'), el('div', { class: 'hrtext', id: 'hrtext' }, S.hrText.slice(-6000)));
  box.append(el('div', { id: 'hrreasonwrap', hidden: !S.hrReason }, el('h3', {}, 'Model reasoning'), el('div', { class: 'hrtext reason', id: 'hrreason' }, S.hrReason.slice(-6000))));
  box.append(el('h3', {}, 'Log'), el('div', { class: 'log', id: 'log' }));
  return box;
}
function cmpCard(l, v, s) { return el('div', { class: 'c' }, el('div', { class: 'l' }, l), el('div', { class: 'v' }, v), el('div', { class: 's' }, s)); }
function promote(st) {
  const body = [el('p', { class: 'kv' }, `HR Steel job "${S.project}" will hold the candidate design; the current design is archived beside it as ${S.project}__<timestamp>. In this project folder the current package and analyses move to archive/<timestamp>/ and the candidate with its verification outputs takes their place.`)];
  if (!st.passed) body.push(el('p', { class: 'note bad' }, 'This candidate did NOT pass its verification criteria. Promote only if you have a reason the criteria do not capture; the reason is not recorded automatically — write it in the HR Steel report.'));
  openModal('Make this the design of record?', body, [el('button', { onclick: closeModal }, 'Cancel'), el('button', { class: st.passed ? 'primary' : 'danger', onclick: async () => {
    closeModal();
    try { const r = await api(`${P()}/loop/${enc(st.id)}/promote`, { method: 'POST' }); for (const l of r.lines) toast(l, 'ok'); await refresh(); await loadRun(); render(); }
    catch (e) { toast(e.message, 'bad'); }
  } }, 'Promote')]);
}

load().catch(e => toast(e.message, 'bad'));
