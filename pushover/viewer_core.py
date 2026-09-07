"""viewer_core.py -- shared builder for the Steltic analysis-viewer bundle (Pushover / NLRHA / DDM).

Every viewer is a single self-contained HTML file: three.js (vendored, r128, MIT) + the core scene/controls from
viewer_core.html + one analysis module. The three viewers are meant to be opened side by side with Steltic's own
viewer_3d.html, so this file keeps geometry, palette and behaviour identical across them.
"""
from __future__ import annotations
import json, os

_HERE = os.path.dirname(os.path.abspath(__file__))
ACCENT = {"pushover": "#ffb454", "nlrha": "#3fc1a0", "ddm": "#e0b341"}
# where each viewer of the bundle lives relative to the Steltic package root (the hub sits in the root)
BUNDLE_FILES = {"steltic": "viewer_3d.html", "pushover": os.path.join("pushover", "pushover_viewer_3d.html"),
                "nlrha": os.path.join("nlrha", "nlrha_viewer_3d.html"), "ddm": "ddm_viewer_3d.html", "hub": "steltic_viewer_bundle.html"}


def model_dict(nodes, elements, fixed, diaphragms, support_label=""):
    """nodes: {tag:(x,y,z)}; elements: [(tag, kind, sec, n1, n2)] kind in col/beam/brace; fixed: [tags];
    diaphragms: [(perp, master, [slaves])]."""
    levels = sorted({0.0} | {nodes[m][2] for _, m, _ in diaphragms})
    cm, masters, slabs = {}, {}, []
    for perp, master, slaves in diaphragms:
        z = nodes[master][2]; k = levels.index(z)
        cm[k] = [nodes[master][0], nodes[master][1]]; masters[k] = master
        xs = [nodes[n][0] for n in slaves]; ys = [nodes[n][1] for n in slaves]
        slabs.append([min(xs), max(xs), min(ys), max(ys), z])
    kind_map = {"col": "column", "beam": "beam", "brace": "brace"}
    els = [dict(tag=int(t), type=kind_map.get(k, k), sec=str(s), n=[int(a), int(b)]) for t, k, s, a, b in elements]
    return dict(nodes={str(t): [round(v, 2) for v in xyz] for t, xyz in nodes.items()}, elements=els, fixed=[int(t) for t in fixed],
                levels=levels, cm={str(k): v for k, v in cm.items()}, masters={str(k): v for k, v in masters.items()}, slabs=slabs,
                support_label=support_label)


def model_from_package(pkg):
    """From a steltic_pushover Package (parsed model_opensees.py + member_schedule.csv)."""
    els = []
    for e in pkg.model.elements:
        row = pkg.schedule.get(e["tag"], {})
        kind = row.get("member") or ("brace" if "etype" in e else "col")
        els.append((e["tag"], kind, row.get("section", "?"), e["n1"], e["n2"]))
    base_fixed = [t for t, fl in pkg.model.fixes.items() if fl[:3] == [1, 1, 1] and pkg.model.nodes[t][2] <= min(v[2] for v in pkg.model.nodes.values()) + 1e-6]
    lab = "fixed" if any(fl[3] == 1 for t, fl in pkg.model.fixes.items() if t in base_fixed) else "pinned"
    return model_dict(pkg.model.nodes, els, base_fixed, pkg.model.diaphragms, support_label=lab)


def bundle_links(out_dir, root):
    """Relative paths (from the folder a viewer is written to) to every viewer of the bundle + the hub, POSIX separators."""
    if not root:
        return {}
    links = {}
    for k, f in BUNDLE_FILES.items():
        rel = os.path.relpath(os.path.join(os.path.abspath(root), f), os.path.abspath(out_dir))
        links[k] = rel.replace(os.sep, "/")
    return links


def render(out_path, title, analysis, meta, model, extra, left_panel_html, right_panel_html, module_js, amp=10,
           template=None, three_js=None, root=None, hub=True):
    """Write one viewer. `root` = Steltic package root: enables the module selector in the header (links to the sibling
    viewers, availability probed when the page opens) and, with hub=True, writes/refreshes <root>/steltic_viewer_bundle.html."""
    tpl = open(template or os.path.join(_HERE, "viewer_core.html"), encoding="utf-8").read()
    three = open(three_js or os.path.join(_HERE, "vendor", "three.min.js"), encoding="utf-8").read()
    meta = dict(meta, analysis=analysis, bundle=bundle_links(os.path.dirname(os.path.abspath(out_path)), root))
    data = dict(meta=meta, model=model, **extra)
    html = (tpl.replace("__TITLE__", title).replace("__ACCENT__", ACCENT.get(analysis, "#ffffff")).replace("__AMP__", str(amp))
            .replace("__LEFT_PANEL__", left_panel_html).replace("__RIGHT_PANEL__", right_panel_html)
            .replace("__THREE__", three).replace("__MODULE_JS__", module_js)
            .replace("__DATA__", json.dumps(data, separators=(",", ":"), default=_default)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    if root and hub:
        try:
            write_hub(root, name=meta.get("title", "").split(" · ")[0] or os.path.basename(os.path.abspath(root)))
        except Exception:
            pass
    return out_path


HUB_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>__NAME__ · Steltic viewer bundle</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { height:100%; background:#14171c; color:#dde3ea; font-family:'Segoe UI',system-ui,sans-serif; overflow:hidden; }
  #bar { position:fixed; top:0; left:0; right:0; height:36px; display:flex; align-items:center; gap:6px; padding:0 12px; background:#101318; border-bottom:1px solid #2c333e; z-index:10; }
  .brand { font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:#8b95a3; margin-right:10px; white-space:nowrap; }
  .brand b { color:#fff; letter-spacing:0; text-transform:none; font-size:12.5px; margin-left:6px; }
  .chip { font-size:11px; letter-spacing:.06em; text-transform:uppercase; padding:4px 12px; border:1px solid #2c333e; border-radius:5px; color:#5d6774; user-select:none; white-space:nowrap; }
  .chip.ok { color:#dde3ea; cursor:pointer; border-color:#3a4656; } .chip.ok:hover { background:#26405e; }
  .chip.on { background:var(--c); color:#14171c; border-color:var(--c); font-weight:700; }
  .chip.na { opacity:.45; text-decoration:line-through; cursor:not-allowed; }
  .chip.probing { opacity:.6; }
  .sp { flex:1; }
  .note { font-size:10.5px; color:#5d6774; white-space:nowrap; }
  a.open { font-size:11px; color:#9fb4cc; text-decoration:none; margin-left:10px; white-space:nowrap; } a.open:hover { color:#fff; }
  iframe { position:fixed; top:36px; left:0; width:100%; height:calc(100% - 36px); border:0; background:#14171c; display:none; }
  #empty { position:fixed; inset:36px 0 0 0; display:flex; align-items:center; justify-content:center; color:#8b95a3; font-size:13px; text-align:center; line-height:1.6; }
</style></head>
<body>
<div id="bar"><span class="brand">Steltic viewer bundle <b>__NAME__</b></span><span id="chips" style="display:flex;gap:6px"></span><span class="sp"></span><span class="note" id="note">looking for viewers in this folder…</span><a class="open" id="open" target="_blank" style="display:none">open standalone ↗</a></div>
<div id="empty">No viewer found next to this file.<br><span style="font-size:11.5px">Expected: viewer_3d.html · pushover/pushover_viewer_3d.html · nlrha/nlrha_viewer_3d.html · ddm_viewer_3d.html</span></div>
<script>
const MODS = [['steltic','Steltic','#cfe3ff','viewer_3d.html'],['pushover','Pushover','#ffb454','pushover/pushover_viewer_3d.html'],['nlrha','NLRHA','#3fc1a0','nlrha/nlrha_viewer_3d.html'],['ddm','DDM','#e0b341','ddm_viewer_3d.html']];
const avail = {}, frames = {}; let current = null, pending = MODS.length;
const chips = document.getElementById('chips');
MODS.forEach(([k,label,col,rel]) => { const c = document.createElement('span'); c.className='chip probing'; c.dataset.mod=k; c.style.setProperty('--c', col); c.textContent=label; c.title='looking for '+rel; chips.appendChild(c); });
function probe(rel, cb){ const s=document.createElement('script'); s.src=rel; s.onload=()=>{ s.remove(); cb(true); }; s.onerror=()=>{ s.remove(); cb(false); }; document.head.appendChild(s); }
window.addEventListener('error', e => { if (e.filename && /\.html$/i.test(e.filename)) { e.preventDefault(); } }, true);   // the probes are html files loaded as scripts: swallow their syntax errors
function select(k){
  if (!avail[k]) return; current = k;
  if (!frames[k]){ const f=document.createElement('iframe'); f.src=MODS.find(x=>x[0]===k)[3]; f.title=k; document.body.appendChild(f); frames[k]=f; }
  MODS.forEach(([m]) => { const c = chips.querySelector('[data-mod="'+m+'"]'); c.classList.toggle('on', m===k); if (frames[m]) frames[m].style.display = m===k?'block':'none'; });
  document.getElementById('empty').style.display='none';
  const a=document.getElementById('open'); a.href=MODS.find(x=>x[0]===k)[3]; a.style.display='inline';
  try { history.replaceState(null, '', '#'+k); } catch(e){}
}
MODS.forEach(([k,label,col,rel]) => probe(rel, ok => {
  avail[k]=ok; const c = chips.querySelector('[data-mod="'+k+'"]'); c.classList.remove('probing');
  if (ok){ c.classList.add('ok'); c.title='open '+rel; c.onclick=()=>select(k); } else { c.classList.add('na'); c.title=rel+' — not in this folder'; }
  if (--pending===0){
    const n = MODS.filter(([m])=>avail[m]).length; document.getElementById('note').textContent = n ? (n+' of 4 viewers in this folder · greyed = not present · the module strip inside each viewer switches too') : 'no viewers found next to this file';
    const want = (location.hash||'').slice(1); if (avail[want]) select(want); else { const first = MODS.find(([m])=>avail[m]); if (first) select(first[0]); }
  }
}));
window.addEventListener('message', e => { const k = e.data && e.data.stelticBundle; if (k && avail[k]) select(k); });
window.addEventListener('keydown', e => { const i = parseInt(e.key)-1; if (i>=0 && i<MODS.length && !e.ctrlKey && !e.metaKey) select(MODS[i][0]); });
</script>
</body></html>
"""


def write_hub(root, name=None):
    """<root>/steltic_viewer_bundle.html: one page that flips between the four viewers (iframes); modules missing from the
    folder are greyed out. Identical content whichever tool writes it."""
    name = name or os.path.basename(os.path.abspath(root))
    out = os.path.join(root, BUNDLE_FILES["hub"])
    with open(out, "w", encoding="utf-8") as f:
        f.write(HUB_HTML.replace("__NAME__", name))
    return out


def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.ndarray): return o.tolist()
        if isinstance(o, (np.floating, np.integer)): return o.item()
    except Exception:
        pass
    if hasattr(o, "as_dict"): return o.as_dict()
    return str(o)
