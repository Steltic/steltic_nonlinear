"""
ingest.py -- read a finished Steltic (hot-rolled) job folder into a neutral model description.

Sources, in order of authority:
  1. model_opensees.py  -- the flat replay of every ops.* call Steltic issued: nodes, fixities, masses,
                           geomTransf vectors, elasticBeamColumn/Truss elements (with -releasey/-releasez),
                           rigidDiaphragm groups. Parsed line by line; the engine is NOT needed for this.
  2. design/member_schedule.csv -- ele_tag -> section label + member kind (the replay has properties only).
  3. design/calc_package.json   -- section/role groups + the agent's member-based D/C (for comparison).
  4. cfg.py             -- exec'd with the Steltic engine on sys.path to regenerate loads/combos.

Steltic conventions decoded here (engine3d.add_column / add_beam):
  geomTransf 1 (vecxz 1,0,0) -> column strong axis resists Y ; 2 (0,1,0) -> strong axis resists X ;
  3 (0,0,1) -> beams (local z vertical).  Node tag = k*100000 + i*100 + j ; master = k*100000 + 99999.
  '-releasey' code on a BEAM = major-axis (gravity) moment release ; '-releasez' = minor-axis release.
"""
import ast, csv, json, os, re, sys
from dataclasses import dataclass, field


@dataclass
class Member:
    tag: int
    kind: str            # col | beam | brace
    section: str
    n1: int
    n2: int
    transf: int = 0
    relz: int = 0        # 0 none, 1 I, 2 J, 3 both  (MAJOR release code, Steltic 'relz')
    rely: int = 0
    A: float = 0.0
    role: str = ""       # lateral_col | gravity_col | floor | roof | brace
    dirn: str = ""       # X | Y | Z(column) | D(iagonal)


@dataclass
class NeutralModel:
    job_dir: str
    name: str
    nodes: dict = field(default_factory=dict)        # tag -> (x, y, z)
    fixes: dict = field(default_factory=dict)        # tag -> (6 flags)
    masses: dict = field(default_factory=dict)       # tag -> (6 values)
    transf: dict = field(default_factory=dict)       # tag -> (type, vecxz)
    members: list = field(default_factory=list)
    diaphragms: dict = field(default_factory=dict)   # master -> [slaves]
    cfg: dict = None
    calc_package: dict = None
    levels: list = field(default_factory=list)       # z of each level incl. base

    def by_tag(self):
        return {m.tag: m for m in self.members}

    def level_of(self, ntag):
        return ntag // 100000

    def grid_of(self, ntag):
        r = ntag % 100000
        return r // 100, r % 100, ntag // 100000


def decode_tag(t):
    r = t % 100000
    return r // 100, r % 100, t // 100000


_CALL = re.compile(r"^ops\.(\w+)\((.*)\)\s*$")


def _args(s):
    if not s.strip():
        return []
    return list(ast.literal_eval("(" + s + ",)"))


def parse_replay(path):
    """Parse model_opensees.py into dicts. Only the recorded ops.* lines are read."""
    nodes, fixes, masses, transf, elems, diaph = {}, {}, {}, {}, [], {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if '#' in line:
                # keep string literals intact enough for ops replay; comments are export-only
                line = line.split('#', 1)[0].rstrip()
            m = _CALL.match(line)
            if not m:
                continue
            cmd, a = m.group(1), m.group(2)
            try:
                args = _args(a)
            except Exception:
                continue
            if cmd == "wipe":
                # Steltic's export records the probe build AND the real build -> keep the LAST build,
                # but remember geomTransf tags (they are only registered once, in the first build).
                nodes, fixes, masses, elems, diaph = {}, {}, {}, [], {}
            elif cmd == "node":
                nodes[args[0]] = tuple(float(v) for v in args[1:4])
            elif cmd == "fix":
                fixes[args[0]] = tuple(args[1:7])
            elif cmd == "mass":
                masses[args[0]] = tuple(float(v) for v in args[1:7])
            elif cmd == "geomTransf":
                transf[args[1]] = (args[0], tuple(args[2:5]))
            elif cmd == "element":
                elems.append(args)
            elif cmd == "rigidDiaphragm":
                diaph[args[1]] = list(args[2:])
    return nodes, fixes, masses, transf, elems, diaph


def _release_codes(args):
    relz = rely = 0
    if "-releasey" in args:
        relz = int(args[args.index("-releasey") + 1])     # Steltic: relz (MAJOR) -> element -releasey
    if "-releasez" in args:
        rely = int(args[args.index("-releasez") + 1])
    return relz, rely


def load_package(job_dir, steltic_engine_dir=None):
    """Build the NeutralModel for a Steltic job folder."""
    job_dir = os.path.abspath(job_dir)
    name = os.path.basename(job_dir.rstrip("/\\"))
    nm = NeutralModel(job_dir=job_dir, name=name)

    replay = os.path.join(job_dir, "model_opensees.py")
    if not os.path.exists(replay):
        raise FileNotFoundError("model_opensees.py missing in %s -- ask the HR Steel App to re-run "
                                "pipeline.design_and_report(name, cfg) WITH cfg passed (the export is skipped otherwise)" % job_dir)
    nodes, fixes, masses, transf, elems, diaph = parse_replay(replay)
    nm.nodes, nm.fixes, nm.masses, nm.transf, nm.diaphragms = nodes, fixes, masses, transf, diaph

    # section labels from member_schedule.csv
    secmap = {}
    sched = os.path.join(job_dir, "design", "member_schedule.csv")
    if os.path.exists(sched):
        with open(sched, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    secmap[int(r["ele_tag"])] = (r["member"], r["section"])
                except Exception:
                    pass

    for a in elems:
        et = a[0]; tag = a[1]; n1, n2 = a[2], a[3]
        if et == "elasticBeamColumn":
            A, tr = float(a[4]), int(a[10])
            relz, rely = _release_codes(a)
            kind, sec = secmap.get(tag, ("col" if tr in (1, 2) else "beam", "?"))
            i1, j1, k1 = decode_tag(n1); i2, j2, k2 = decode_tag(n2)
            if k1 != k2 and (i1, j1) == (i2, j2):
                dirn = "Z"
            elif j1 == j2:
                dirn = "X"
            else:
                dirn = "Y"
            nm.members.append(Member(tag, kind, sec, n1, n2, tr, relz, rely, A, dirn=dirn))
        elif et in ("Truss", "corotTruss"):
            A = float(a[4])
            kind, sec = secmap.get(tag, ("brace", "?"))
            nm.members.append(Member(tag, "brace", sec, n1, n2, 0, 3, 3, A, dirn="D"))

    # levels
    zs = sorted({round(v[2], 6) for v in nodes.values()})
    nm.levels = zs

    # calc_package + roles
    cp = os.path.join(job_dir, "design", "calc_package.json")
    if os.path.exists(cp):
        nm.calc_package = json.load(open(cp))
    _assign_roles(nm)

    # cfg.py (needs the Steltic engine importable for engine3d/openseespy)
    nm.cfg = load_cfg(job_dir, steltic_engine_dir)
    return nm


def _assign_roles(nm):
    """lateral_col = column at a node that a brace or a RIGID beam frames into; roof = top-level beam."""
    top = max(nm.levels) if nm.levels else None
    lateral_nodes = set()
    for m in nm.members:
        if m.kind == "brace":
            lateral_nodes.add(m.n1); lateral_nodes.add(m.n2)
        if m.kind == "beam" and m.relz == 0:
            lateral_nodes.add(m.n1); lateral_nodes.add(m.n2)
    # a column line is lateral if any node on that (i,j) line is a lateral node
    lat_lines = {decode_tag(t)[:2] for t in lateral_nodes}
    for m in nm.members:
        if m.kind == "col":
            m.role = "lateral_col" if decode_tag(m.n1)[:2] in lat_lines else "gravity_col"
        elif m.kind == "beam":
            if m.n1 not in nm.nodes:
                m.role = "floor"  # PR/slave endpoint missing from node table — do not crash
            else:
                m.role = "roof" if (top is not None and abs(nm.nodes[m.n1][2] - top) < 1e-6) else "floor"
        else:
            m.role = "brace"


def load_cfg(job_dir, steltic_engine_dir=None):
    """exec cfg.py with the Steltic engine importable; returns the cfg dict (with custom_build)."""
    eng_dir = steltic_engine_dir or os.environ.get("STELTIC_ENGINE_DIR")
    if eng_dir and eng_dir not in sys.path:
        sys.path.insert(0, eng_dir)
    try:
        import engine3d  # noqa: F401
    except ImportError:
        raise ImportError("Steltic's steel_engine is not importable -- pass steltic_engine_dir or set STELTIC_ENGINE_DIR")
    src = open(os.path.join(job_dir, "cfg.py")).read()
    ns = {"__name__": "steltic_cfg", "__file__": os.path.join(job_dir, "cfg.py")}
    old = os.getcwd()
    try:
        os.chdir(job_dir)
        exec(compile(src, "cfg.py", "exec"), ns)
    finally:
        os.chdir(old)
    cfg = ns.get("cfg")
    if not isinstance(cfg, dict):
        raise ValueError("cfg.py does not define a top-level `cfg = dict(...)`")
    return cfg


def summary(nm):
    kinds = {}
    for m in nm.members:
        kinds[m.kind] = kinds.get(m.kind, 0) + 1
    secs = sorted({(m.kind, m.section) for m in nm.members})
    return dict(name=nm.name, nodes=len(nm.nodes), members=kinds, levels=len(nm.levels) - 1,
                diaphragms=len(nm.diaphragms), sections=secs,
                bases={t: f for t, f in nm.fixes.items() if t < 100000})
