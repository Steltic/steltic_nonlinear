"""package_reader.py -- load a Steltic HR design package (the Download .zip, or the job folder it
mirrors) into one plain-Python structure the pushover pipeline can build from.

What we read (all produced by steltic's pipeline.design_and_report / bundle.make_zip):
  <building>/model_opensees.py        the EXACT elastic OpenSeesPy model (replayed ops.* calls)
  <building>/design/member_schedule.csv   ele_tag -> member kind (col/beam/brace) + AISC section
  <building>/design/calc_package.json     roles, demands, agent capacities, capacity_design block
  <building>/cfg.py                   the agent's cfg (seis params, system, heights, loads) -- optional
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
            pass
        else:
            m.other.append((cmd, a))
    # Steltic's export records the engine's PROBE build followed by the real build (nodes/elements/masses/
    # diaphragms appear twice with the same tags; geomTransf only once). Keep the LAST definition of each tag.
    last = {}
    for e in m.elements:
        last[e["tag"]] = e
    m.elements = list(last.values())
    if len(m.diaphragms) > 1:
        seen = {}
        for d in m.diaphragms:
            seen[d[1]] = d
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
    mo = re.search(r"heights\s*[:=]\s*(\[[^\]]*\])", src)
    if mo:
        try:
            b.heights_in = [float(v) for v in ast.literal_eval(mo.group(1))]; b.sources["heights_in"] = "cfg.py"
        except Exception:
            pass


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


def read_basis(root: Path, calc: dict) -> DesignBasis:
    b = DesignBasis()
    if (root / "cfg.py").exists():
        _basis_from_cfg(root / "cfg.py", b)
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
    basis = read_basis(root, calc)
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
