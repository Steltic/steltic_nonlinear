"""package_reader.py -- load a Steltic HR design package (the Download .zip, or the job folder it
mirrors) into one plain-Python structure the pushover pipeline can build from.

What we read (all produced by steltic's pipeline.design_and_report / bundle.make_zip):
  <building>/model_opensees.py        the EXACT elastic OpenSeesPy model (replayed ops.* calls)
  <building>/design/member_schedule.csv   ele_tag -> member kind (col/beam/brace) + AISC section
  <building>/design/calc_package.json     roles, demands, agent capacities, capacity_design block
  <building>/cfg.py                   the agent's cfg (seis params, system, heights, loads) -- optional; scalars are
                                      regexed, `heights` comes from the EXECUTED cfg (NL-R2-06, steltic_ddm.ingest.load_cfg)
  <building>/report.html              fallback source for SDS/SD1/R/Cd/Om0/Ie, W, V, T (regex on text)

Nothing here runs the model: model_opensees.py is PARSED (regex on `ops.<cmd>(...)` lines), never
exec'd, so a package can be inspected safely before anything is analysed.
"""
from __future__ import annotations
import ast, csv, html, io, json, os, re, tempfile, zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ElasticModel:
    ndm: int = 3
    ndf: int = 6
    nodes: dict = field(default_factory=dict)         # tag -> (x, y, z)
    fixes: dict = field(default_factory=dict)         # tag -> [6 flags]
    masses: dict = field(default_factory=dict)        # tag -> [6 values]
    transfs: dict = field(default_factory=dict)       # tag -> (type, vx, vy, vz)
    elements: list = field(default_factory=list)      # dicts: tag,n1,n2,A,E,G,J,Iy,Iz,transf,release
    materials: dict = field(default_factory=dict)     # tag -> raw uniaxialMaterial args (replayed for raw elements)
    diaphragms: list = field(default_factory=list)    # (perpDir, master, [slaves])
    equal_dofs: list = field(default_factory=list)
    other: list = field(default_factory=list)         # anything we did not classify (kept for audit)


@dataclass
class DesignBasis:
    SDS: Optional[float] = None
    SD1: Optional[float] = None
    S1: Optional[float] = None
    R: Optional[float] = None
    Cd: Optional[float] = None
    Om0: Optional[float] = None
    Ie: Optional[float] = None
    system: Optional[str] = None
    W_kip: Optional[float] = None            # effective seismic weight (report)
    V_design_kip: Optional[float] = None     # ELF base shear (report)
    T_design_s: Optional[float] = None       # design period used for Cs (report)
    L_floor_psf: Optional[float] = None
    heights_in: Optional[list] = None
    site_class: Optional[str] = None
    sources: dict = field(default_factory=dict)   # field -> where it came from


@dataclass
class Package:
    root: Path
    name: str
    model: ElasticModel
    schedule: dict                 # ele_tag -> {"member": col/beam/brace, "section": "W14X311", ...}
    calc: dict                     # calc_package.json
    basis: DesignBasis
    files: dict                    # logical name -> path


# --------------------------------------------------------------------------- locating the package
def locate(path: str | os.PathLike) -> Path:
    """Accept a .zip (Steltic Download) or a folder; return the folder that holds model_opensees.py."""
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".zip":
        out = Path(tempfile.mkdtemp(prefix="steltic_pkg_"))
        with zipfile.ZipFile(p) as z:
            z.extractall(out)
        p = out
    if (p / "model_opensees.py").is_file():
        return p                                        # the package root itself -- never a nested copy below it
    # otherwise the shallowest hit (a zip wrapped in one top folder); feedback/<loop>/candidate/ or
    # archive/<stamp>/ copies deeper in the tree must never shadow the package the caller named
    hits = sorted(p.rglob("model_opensees.py"), key=lambda h: (len(h.parts), str(h)))
    if not hits:
        raise FileNotFoundError(f"no model_opensees.py under {p} -- is this a Steltic design package?")
    return hits[0].parent


# --------------------------------------------------------------------------- model_opensees.py
_CALL = re.compile(r"^\s*ops\.(\w+)\((.*)\)\s*$")


def _args(s: str) -> list:
    """Parse the argument list of an `ops.cmd(...)` line into Python literals."""
    try:
        node = ast.parse(f"f({s})", mode="eval").body
        return [ast.literal_eval(a) for a in node.args]
    except Exception:
        return [s]


def parse_model_script(path: Path) -> ElasticModel:
    m = ElasticModel()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        mo = _CALL.match(line)
        if not mo:
            continue
        cmd, a = mo.group(1), _args(mo.group(2))
        if cmd == "model":
            if "-ndm" in a: m.ndm = int(a[a.index("-ndm") + 1])
            if "-ndf" in a: m.ndf = int(a[a.index("-ndf") + 1])
        elif cmd == "node":
            m.nodes[int(a[0])] = tuple(float(v) for v in a[1:4])
        elif cmd == "fix":
            m.fixes[int(a[0])] = [int(v) for v in a[1:]]
        elif cmd == "mass":
            m.masses[int(a[0])] = [float(v) for v in a[1:]]
        elif cmd == "geomTransf":
            m.transfs[int(a[1])] = (str(a[0]), float(a[2]), float(a[3]), float(a[4]))
        elif cmd == "element" and a[0] == "elasticBeamColumn":
            e = dict(tag=int(a[1]), n1=int(a[2]), n2=int(a[3]), A=float(a[4]), E=float(a[5]),
                     G=float(a[6]), J=float(a[7]), Iy=float(a[8]), Iz=float(a[9]), transf=int(a[10]),
                     release=None)
            rel = [i for i, v in enumerate(a) if isinstance(v, str) and v.startswith("-release")]
            if rel:                                   # native end releases: "-releasey", code, "-releasez", code (code 1=I 2=J 3=both)
                e["release"] = a[rel[0]:]
            m.elements.append(e)
        elif cmd == "element":                        # truss / other -> keep for audit; braces come as trusses in some builds
            m.elements.append(dict(tag=int(a[1]), n1=int(a[2]), n2=int(a[3]), etype=str(a[0]), raw=a))
        elif cmd == "uniaxialMaterial":
            m.materials[int(a[1])] = a
        elif cmd == "rigidDiaphragm":
            m.diaphragms.append((int(a[0]), int(a[1]), [int(v) for v in a[2:]]))
        elif cmd == "equalDOF":
            m.equal_dofs.append(a)
        elif cmd in ("wipe",):
            # R2 patch (NL-R2-01): keep ONLY the last build. The export holds the probe build then the real
            # build; probe-only nodes (e.g. a centre-of-mass node cmtag(k) the real build does not create)
            # and probe masses must not survive. geomTransf / materials are recorded once -> keep them.
            m.nodes.clear(); m.fixes.clear(); m.masses.clear(); m.elements.clear()
            m.diaphragms.clear(); m.equal_dofs.clear()
        else:
            m.other.append((cmd, a))
    # Steltic's export records the engine's PROBE build followed by the real build (nodes/elements/masses/
    # diaphragms appear twice with the same tags; geomTransf only once). Keep the LAST definition of each tag.
    last = {}
    for e in m.elements:
        last[e["tag"]] = e
    m.elements = list(last.values())
    if len(m.diaphragms) > 1:
        # R2 patch (NL-R2-01): one master can carry several rigidDiaphragm calls (engine3d slaves the
        # level centre-of-mass node cmtag(k) with its own call, HR-19) -> MERGE the slave sets per master.
        seen = {}
        for perp, ma, sl in m.diaphragms:
            if ma in seen:
                cur = seen[ma][2]
                cur.extend(s for s in sl if s not in cur)
            else:
                seen[ma] = (perp, ma, list(sl))
        m.diaphragms = list(seen.values())
    return m


# --------------------------------------------------------------------------- schedule / calc package
def read_schedule(path: Path) -> dict:
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[int(row["ele_tag"])] = {"member": row["member"].strip(), "section": row["section"].strip(),
                                            "length_in": float(row.get("length_in") or 0),
                                            "P_comp_kip": float(row.get("P_comp_kip") or 0),
                                            "Mx_kipft": float(row.get("Mx_kipft") or 0),
                                            "governing_combo": row.get("governing_combo", "")}
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------- design basis
_NUM = r"([0-9]+(?:\.[0-9]+)?)"


def _basis_from_cfg(cfg_py: Path, b: DesignBasis):
    """Regex the seis(...) / dict literals out of cfg.py WITHOUT importing it (it imports engine3d)."""
    src = cfg_py.read_text(encoding="utf-8", errors="replace")
    def grab(key, pat):
        mo = re.search(pat, src)
        if mo:
            setattr(b, key, float(mo.group(1)) if key not in ("system",) else mo.group(1))
            b.sources[key] = "cfg.py"
    for key in ("SDS", "SD1", "S1", "R", "Cd", "Om0", "Ie"):
        grab(key, rf"['\"]{key}['\"]\s*:\s*{_NUM}")
        if getattr(b, key) is None:
            grab(key, rf"\b{key}\s*=\s*{_NUM}")
    mo = re.search(r"seis\(\s*" + r"\s*,\s*".join([_NUM] * 4), src)   # seis(SDS,SD1,S1,R,...) positional
    if mo and b.SDS is None:
        b.SDS, b.SD1, b.S1, b.R = (float(mo.group(i)) for i in range(1, 5)); b.sources["SDS"] = "cfg.py seis()"
    mo = re.search(r"['\"]?\bsystem['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", src)
    if mo: b.system = mo.group(1); b.sources["system"] = "cfg.py"
    mo = re.search(r"['\"]?L_floor['\"]?\s*[:=]\s*" + _NUM, src)
    if mo: b.L_floor_psf = float(mo.group(1)); b.sources["L_floor_psf"] = "cfg.py"
    # NL-R2-06: `heights` is NOT regexed here -- the first `heights = [...]` in the text can be a docstring/comment
    # (Ex13: "heights = [14,6,6,10,2,12] ft" -> feet read as inches, h_n 4 ft instead of 50). See _heights().


def _heights(root: Path, model: Optional["ElasticModel"]) -> tuple:
    """NL-R2-06: inter-level heights (in) and where they came from -- the EXECUTED cfg value cfg['heights'] (the
    steltic_ddm loader runs cfg.py with the HR engine importable, as brace_geometry does), never a comment; when
    cfg.py cannot be executed or carries no heights, the elevations of the replayed model's diaphragm masters
    (else of its mass nodes). (None, reason) when neither exists."""
    why = "no cfg.py"
    if (root / "cfg.py").exists():
        try:
            from steltic_ddm.ingest import load_cfg
            h = load_cfg(str(root)).get("heights")
            if isinstance(h, (list, tuple)) and h and all(isinstance(v, (int, float)) and v > 0 for v in h):
                return [float(v) for v in h], "cfg.py (executed cfg['heights'])"
            why = "cfg['heights'] absent or not a list of positive numbers"
        except Exception as ex:                                       # noqa: BLE001 -- engine missing, cfg error, ...
            why = "cfg.py not executable (%s)" % str(ex)[:80]
    if model is not None and model.nodes:
        zs = sorted({round(model.nodes[ma][2], 3) for _p, ma, _s in model.diaphragms if ma in model.nodes})
        src = "diaphragm master elevations"
        if not zs:
            zs = sorted({round(model.nodes[t][2], 3) for t, mv in model.masses.items() if t in model.nodes and mv and mv[0] > 0})
            src = "mass node elevations"
        z0 = min(min(v[2] for v in model.nodes.values()), 0.0)
        zs = [z for z in zs if z > z0 + 1e-6]
        if zs:
            return [b - a for a, b in zip([z0] + zs[:-1], zs)], "model_opensees.py %s (%s)" % (src, why)
    return None, why


def _basis_from_report(report_html: Path, b: DesignBasis):
    t = report_html.read_text(encoding="utf-8", errors="replace")
    txt = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
    def grab(key, pat, conv=float):
        if getattr(b, key) is not None:
            return
        mo = re.search(pat, txt)
        if mo:
            setattr(b, key, conv(mo.group(1))); b.sources[key] = "report.html"
    grab("SDS", r"S\s*DS\s*=\s*" + _NUM)
    grab("SD1", r"S\s*D1\s*=\s*" + _NUM)
    grab("S1", r"S\s*1\s*=\s*" + _NUM)
    grab("R", r"\bR\s*=\s*" + _NUM)
    grab("Ie", r"I\s*e\s*=\s*" + _NUM)
    mo = re.search(r"R\s*/\s*Cd\s*/\s*Ω0\s*/\s*Ie\s*" + r"\s*/\s*".join([_NUM] * 4), txt)
    if mo:
        for k, i in (("R", 1), ("Cd", 2), ("Om0", 3), ("Ie", 4)):
            if getattr(b, k) is None:
                setattr(b, k, float(mo.group(i))); b.sources[k] = "report.html"
    grab("W_kip", r"Seismic weight W\s*=\s*" + _NUM)
    grab("V_design_kip", r"Base shear ΣF\s*=\s*" + _NUM)
    grab("T_design_s", r"design period T\s*=\s*min\([^)]*\)\s*=\s*" + _NUM)


def read_basis(root: Path, calc: dict, model: Optional[ElasticModel] = None) -> DesignBasis:
    b = DesignBasis()
    if (root / "cfg.py").exists():
        _basis_from_cfg(root / "cfg.py", b)
    b.heights_in, src = _heights(root, model)                         # NL-R2-06
    if b.heights_in:
        b.sources["heights_in"] = src
    if (root / "report.html").exists():
        _basis_from_report(root / "report.html", b)
    dr = root / "design" / "design_report.md"
    if b.system is None and dr.exists():
        mo = re.search(r";\s*system\s+([A-Za-z0-9 +/-]+?)\s*;", dr.read_text(errors="replace"))
        if mo:
            b.system = mo.group(1).strip(); b.sources["system"] = "design_report.md (engine label)"
    cd = calc.get("capacity_design") or {}
    if b.system is None and cd.get("system"):
        b.system = cd["system"]; b.sources["system"] = "calc_package.capacity_design"
    return b


# --------------------------------------------------------------------------- design details the engines need per member
# (NL-07 RBS geometry and beam bracing, NL-28 per-joint panel-zone doublers). Read from the STRUCTURED records
# HR writes into calc_package.json -- member inputs, connection records carrying a cut geometry, and the
# capacity_design block -- never from one free-text word. Everything returned carries where it came from.
_W_SHAPE = re.compile(r"\bW\d+(?:\.\d+)?X\d+(?:\.\d+)?\b", re.I)
_NUMV = r"([0-9]+(?:\.[0-9]+)?)"
_GEO_RE = {k: re.compile(r"(?<![A-Za-z_'])%s\s*=\s*%s\s*(?:in\b|\"|$|[,;)\s])" % (k, _NUMV)) for k in ("a", "b", "c")}
# AISC 358-22 Chapter 5 names the connection "reduced beam section (RBS)"; HR spells it several ways
# ("RBS", "Reduced Beam Section", "AISC 358 Ch.5"). A connection record counts as an RBS only when it ALSO
# carries the cut geometry a / b / c (AISC 358-22 Fig. 5.1) -- the name alone is never enough for a number.
_RBS_NAME = re.compile(r"\bRBS\b|reduced[\s_-]*beam[\s_-]*section|358(?:-\d+)?\s*(?:ch(?:apter|\.)?\s*5\b)", re.I)


def _norm_sec(s) -> str:
    return str(s or "").strip().upper().replace(" ", "")


def _shapes_in(text) -> list:
    return [_norm_sec(m.group(0)) for m in _W_SHAPE.finditer(str(text or ""))]


def _geo_from_text(text) -> Optional[dict]:
    t = str(text or "")
    out = {}
    for k, rx in _GEO_RE.items():
        mo = rx.search(t)
        if not mo:
            return None
        out[k + "_in"] = float(mo.group(1))
    return out if out["c_in"] > 0 else None


def _length_in(text) -> Optional[float]:
    """First 'Lb <n> ft|in' / '<n> in spacing' / 'at <n> ft' bracing length in a capacity-design string (inches)."""
    t = str(text or "")
    for pat in (r"\bLb\w*\s*(?:=|of|:)?\s*" + _NUMV + r"\s*(ft|in)\b",
                r"\bat\s+" + _NUMV + r"\s*(ft|in)\.?\s+(?:spacing|o\.?c\.?|centres|centers)",
                r"\bspacing\s+(?:of\s+)?" + _NUMV + r"\s*(ft|in)\b"):
        mo = re.search(pat, t, re.I)
        if mo:
            v = float(mo.group(1))
            return v * 12.0 if mo.group(2).lower() == "ft" else v
    return None


def beam_details(p: "Package") -> dict:
    """Per-beam-section design details for the hinge models (cached on the package).

    Returns {"rbs": {SECTION: {a_in, b_in, c_in, source}}, "rbs_frame": bool, "rbs_evidence": [...],
             "Lb": {SECTION: {Lb_in, source}}, "Lb_frame": {Lb_in, source} | None, "notes": [...]}

    RBS (AISC 358-22 Ch. 5): a beam section is RBS when
      * a member record carries an `RBS` geometry dict (inputs.RBS with a_in/b_in/c_in), or
      * a connection record names the RBS connection AND carries its cut geometry a=, b=, c= (Fig. 5.1);
        the beam section comes from the record's `section` field, else the first W shape in its text.
    The frame is RBS (`rbs_frame`) when any of those exist, when a connection record's type names the RBS
    connection without recording its cut, or capacity_design carries RBS-specific entries
    (`rbs_drift_factor`, an RBS `connection` / `moment_connection` record). Where the frame is RBS but a
    section has no recorded geometry, the engines use the AISC 358-22 5.7 Step 1 default (see rbs_default).
    Where two records give different cuts for one section the smaller c is kept (larger Z_RBS: the beam
    is not under-strength against the columns) and the disagreement is noted.

    Lb (unbraced length of the beam for the AISC 342 Table C5.5 a-expression): per section from the member
    inputs (`Lb_in` > 0) and frame-wide from capacity_design beam_bracing / beam_ductility (D1.2 bracing
    spacing). Where both exist the LARGER governs (a decreases with Lb/ry -> conservative), noted.
    """
    cached = getattr(p, "_beam_details", None)
    if cached is not None:
        return cached
    calc = p.calc or {}
    cd = calc.get("capacity_design") or {}
    rbs, notes, evidence = {}, [], []

    def put(sec, geo, src):
        sec = _norm_sec(sec)
        if not sec or not geo:
            return
        old = rbs.get(sec)
        if old and abs(old["c_in"] - geo["c_in"]) > 1e-6:
            notes.append("RBS %s: records disagree on c (%.3f in from %s, %.3f in from %s); smaller c kept"
                         % (sec, old["c_in"], old["source"], geo["c_in"], src))
            if geo["c_in"] >= old["c_in"]:
                return
        elif old:
            return
        rbs[sec] = dict(a_in=float(geo["a_in"]), b_in=float(geo["b_in"]), c_in=float(geo["c_in"]), source=src)

    for m in calc.get("members") or []:
        inp = (m or {}).get("inputs") or {}
        g = inp.get("RBS") or inp.get("rbs")
        if isinstance(g, dict) and all(g.get(k) is not None for k in ("a_in", "b_in", "c_in")):
            put(inp.get("section"), g, "calc_package members[%s].inputs.RBS" % m.get("id"))
            evidence.append("member %s inputs.RBS" % m.get("id"))
    for cn in calc.get("connections") or []:
        if not isinstance(cn, dict):
            continue
        text = " ".join(str(cn.get(k) or "") for k in ("type", "components", "notes", "note", "id"))
        if not _RBS_NAME.search(str(cn.get("type") or "") + " " + str(cn.get("id") or "") + " " + str(cn.get("components") or "")):
            continue
        geo = _geo_from_text(cn.get("components")) or _geo_from_text(text)
        if not geo:                                   # the connection type names the RBS but records no cut:
            evidence.append("connection %s type '%s' (no cut geometry recorded)" % (cn.get("id"), cn.get("type")))
            continue
        secs = _shapes_in(cn.get("section")) or _shapes_in(cn.get("components")) or _shapes_in(cn.get("id"))
        if secs:
            put(secs[0], geo, "calc_package connections[%s]" % cn.get("id"))
            evidence.append("connection %s (%s)" % (cn.get("id"), cn.get("type")))
    for key in ("connection", "moment_connection"):
        v = cd.get(key)
        if v and _RBS_NAME.search(json.dumps(v) if not isinstance(v, str) else v):
            evidence.append("capacity_design.%s" % key)
    if isinstance(cd.get("rbs_drift_factor"), dict):
        evidence.append("capacity_design.rbs_drift_factor")
    rbs_frame = bool(rbs) or bool(evidence)

    # unbraced length
    Lb = {}
    for m in calc.get("members") or []:
        inp = (m or {}).get("inputs") or {}
        sec = _norm_sec(inp.get("section"))
        try:
            v = float(inp.get("Lb_in") or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if sec and v > 0 and (sec not in Lb or v > Lb[sec]["Lb_in"]):
            Lb[sec] = dict(Lb_in=v, source="calc_package members[%s].inputs.Lb_in" % m.get("id"))
    Lb_frame = None
    bb = cd.get("beam_bracing")
    if isinstance(bb, dict) and bb.get("Lb_provided_in"):
        Lb_frame = dict(Lb_in=float(bb["Lb_provided_in"]), source="capacity_design.beam_bracing.Lb_provided_in")
    else:
        for key, val in (("beam_bracing", bb), ("beam_ductility", cd.get("beam_ductility"))):
            txt = val if isinstance(val, str) else (json.dumps(val) if val else "")
            if isinstance(val, dict):
                txt = " ".join(str(x) for x in val.values())
            v = _length_in(txt)
            if v:
                Lb_frame = dict(Lb_in=v, source="capacity_design.%s" % key)
                break
    out = dict(rbs=rbs, rbs_frame=rbs_frame, rbs_evidence=evidence, Lb=Lb, Lb_frame=Lb_frame, notes=notes)
    try:
        p._beam_details = out
    except Exception:
        pass
    return out


def rbs_default(props: dict) -> dict:
    """AISC 358-22 5.7 Step 1 mid-range RBS geometry for a beam with no recorded cut:
    a = 0.625 bf (0.5..0.75 bf, Eq. 5.7-1), b = 0.75 d (0.65..0.85 d, Eq. 5.7-2), c = 0.25 bf (0.1..0.25 bf, Eq. 5.7-3).
    c is taken at the upper limit: the smallest Z_RBS = Zx - 2 c tf (d - tf) (Eq. 5.7-4), so the beam yields at
    the lowest moment the connection permits and its plastic-rotation demand is not under-estimated."""
    return dict(a_in=0.625 * props["bf"], b_in=0.75 * props["d"], c_in=0.25 * props["bf"],
                source="AISC 358-22 5.7 Step 1 default (no cut recorded for this section)")


def pz_doublers(p: "Package") -> list:
    """HR's per-joint panel-zone doublers (capacity_design.panel_zone.by_joint[]) normalised to
    [{level, column, beams:[...], doubler_in, label}] (level None where the record does not say)."""
    pz = ((p.calc or {}).get("capacity_design") or {}).get("panel_zone") or {}
    out = []
    for j in pz.get("by_joint") or []:
        if not isinstance(j, dict):
            continue
        t = j.get("doubler_in", j.get("doubler_t_in"))
        try:
            t = float(t or 0.0)
        except (TypeError, ValueError):
            continue
        label = str(j.get("joint") or "")
        lvl = j.get("level")
        if lvl is None:
            mo = re.search(r"\b(?:L|level|floor|storey|story)\s*-?\s*(\d+)\b", label, re.I)
            lvl = int(mo.group(1)) if mo else None
        col = _norm_sec(j.get("column")) or None
        beams_txt = str(j.get("beams") or "")
        beams = []
        for mo in re.finditer(r"(?:(\d+)\s*[x×]\s*)?(W\d+(?:\.\d+)?X\d+(?:\.\d+)?)", beams_txt, re.I):
            beams += [_norm_sec(mo.group(2))] * int(mo.group(1) or 1)
        if col is None or not beams:                 # e.g. 'conn-IMF-RBS-W27X94-W14X176' -> beam then column
            shp = _shapes_in(label)
            if len(shp) >= 2:
                beams = beams or [shp[0]]
                col = col or shp[-1]
        out.append(dict(level=int(lvl) if lvl is not None else None, column=col, beams=beams, doubler_in=t, label=label))
    return out


def doubler_for_joint(records: list, level: int, column: str, beams: list):
    """Pick HR's doubler for one model joint: same column section, same beam section set, same level where
    the record names one. Returns (doubler_in, note) or (None, reason). Several matching records with
    different doublers (HR groups joints by position, which the model does not carry) -> the THINNEST is
    used (the more flexible / weaker panel, so panel-zone demand is not under-estimated) and noted."""
    col = _norm_sec(column)
    bset = sorted(set(_norm_sec(b) for b in beams if b))
    cand = []
    for r in records:
        if r["column"] and r["column"] != col:
            continue
        if r["beams"] and sorted(set(r["beams"])) != bset:
            continue
        if r["level"] is not None and level is not None and r["level"] != level:
            continue
        cand.append(r)
    if not cand:
        return None, "no HR panel_zone.by_joint record for %s with %s at level %s" % (col, "+".join(bset), level)
    exact = [r for r in cand if r["level"] is not None and len(r["beams"]) == len(beams)] or cand
    ts = sorted(set(r["doubler_in"] for r in exact))
    if len(ts) == 1:
        return ts[0], "HR panel_zone.by_joint '%s'" % exact[0]["label"]
    return ts[0], "HR panel_zone.by_joint: %d records match (%s in); thinnest used" % (len(exact), ", ".join("%.4g" % t for t in ts))


# --------------------------------------------------------------------------- entry point
def load(path: str | os.PathLike) -> Package:
    root = locate(path)
    files = {"model": root / "model_opensees.py",
             "schedule": root / "design" / "member_schedule.csv",
             "calc": root / "design" / "calc_package.json",
             "cfg": root / "cfg.py", "report": root / "report.html",
             "design_report": root / "design" / "design_report.md"}
    model = parse_model_script(files["model"])
    schedule = read_schedule(files["schedule"]) if files["schedule"].exists() else {}
    calc = json.load(open(files["calc"])) if files["calc"].exists() else {}
    basis = read_basis(root, calc, model)
    name = calc.get("building") or root.name
    return Package(root=root, name=name, model=model, schedule=schedule, calc=calc, basis=basis,
                   files={k: str(v) for k, v in files.items() if v.exists()})


def summary(p: Package) -> str:
    m = p.model
    kinds = {}
    for e in m.elements:
        k = p.schedule.get(e["tag"], {}).get("member", e.get("etype", "?"))
        kinds[k] = kinds.get(k, 0) + 1
    b = p.basis
    return ("package %s @ %s\n  nodes %d, elements %d %s, diaphragms %d, fixed nodes %d, mass nodes %d\n"
            "  basis: SDS=%s SD1=%s R=%s Cd=%s Om0=%s Ie=%s system=%s W=%s kip V=%s kip T=%s s\n  sources: %s"
            % (p.name, p.root, len(m.nodes), len(m.elements), kinds, len(m.diaphragms), len(m.fixes),
               len(m.masses), b.SDS, b.SD1, b.R, b.Cd, b.Om0, b.Ie, b.system, b.W_kip, b.V_design_kip,
               b.T_design_s, b.sources))


if __name__ == "__main__":
    import sys
    print(summary(load(sys.argv[1])))
