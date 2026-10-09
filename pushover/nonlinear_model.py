"""nonlinear_model.py -- rebuild the Steltic elastic model as a concentrated-plasticity OpenSees model.

Every `elasticBeamColumn` column/beam becomes:  node_i --[zeroLength hinge]-- elastic interior --[zeroLength hinge]-- node_j
with the strong-axis rotational spring a ModIMKPeakOriented material (params from hinge_models) and all other
DOF of the zeroLength rigid. The interior element keeps the ORIGINAL geomTransf (so P-Delta stays on for columns)
and has its hinged-axis inertia scaled by (n+1)/n, the spring K0 = n*6EI/L (Ibarra & Krawinkler, n = 10).
Nodes, fixes, masses and rigid diaphragms are replayed exactly as recorded in model_opensees.py.

Optional panel zones (hinge_params panel_zones.mode): default "rigid" (unchanged). "scissors" inserts a
joint-centerline rotational spring between the column joint node and a coincident FR-beam attachment node
(zeroLength on all 6 DOFs: rigid translations/torsion/unused flexure + PZ spring on active rot DOF(s)).
Do not use equalDOF to an RD slave here — it conflicts with rigidDiaphragm under Transformation.
IMK end hinges stay on member ends; do not also apply a C5.4a PZ ductility modifier when scissors is on
(double-counting). Not a full 8-bar Krawinkler rectangle.
Optional RBS 7-seg (beam_flexure.rbs_segments=7 + rbs_geometry_*): FR beams remeshed into 7
elasticBeamColumn segments with reduced flange props in RBS zones; ModIMK ZL hinges at RBS centres
(offset a+b/2), not at every segment joint. Still ZL architecture by default.

Optional L2 (numerics.element_form=concentrated_plasticity_fbc or
beam_flexure.integration=ConcentratedPlasticity): FR hinged beams/cols become
forceBeamColumn + ConcentratedPlasticity (Uniaxial ModIMK ends + Elastic mid).
Scissors PZ retained; RBS uses elastic stubs + CP midspan at RBS centres when geometry fits.

Default method (HR wave): plasticity="fibre" — forceBeamColumn + Lobatto fibre sections
(see fibre_model.py). IMK / ConcentratedPlasticity remain available via --plasticity imk or
numerics.element_form. Fibre path drops scissors PZ (rigid); it meshes RBS beam ends with the circular
flange cut in the fibres and registers every unreleased beam/column end as a monitored plastic-hinge region
(fibre_model.py, NL-01 / NL-07).

Per-beam geometry (NL-07): beam_params_for() evaluates the supplied beam hinge parameters with the design
package's own RBS cut c (Z_RBS, AISC 358-22 Eq. 5.7-4) and unbraced length Lb per beam section.
Scissors panel zones take HR's per-joint doubler plates (capacity_design.panel_zone.by_joint, NL-28).
The push re-issues analysis objects only when they change (StaticAnalysis, NL-11).
"""
from __future__ import annotations
import math, time
import openseespy.opensees as ops
from . import hinge_models as HM
from . import sections_db as SDB
from . import rbs_remesh as RBS
from . import fbc_concentrated as FBC

G_IN = 386.4
N_STIFF = 10.0
def E_KSI_AL(spec):                       # axial stiffness EA/L of a brace (used as 'K0' so the recorder can subtract elastic deformation)
    return 29000.0 * spec.A / spec.L_in
RIGID_T, RIGID_R = 1.0e9, 1.0e11
HN_BASE = 20_000_000          # hinge node tags: HN_BASE + ele*10 + (1|2)
ZL_BASE = 30_000_000          # zeroLength tags: ZL_BASE + ele*10 + (1|2)
MAT_BASE = 40_000_000
PZ_NODE_BASE = 50_000_000     # scissors beam-side node: PZ_NODE_BASE + joint_node
PZ_ZL_BASE = 60_000_000       # scissors PZ zeroLength: PZ_ZL_BASE + joint_node
SEG_NODE_BASE = 70_000_000     # intermediate mesh nodes: SEG_NODE_BASE + ele*100 + i
SEG_ELE_BASE = 80_000_000      # sub-element tags (i>0): SEG_ELE_BASE + ele*100 + i
# NL-02 / NL-03 / NL-10 element families (tags chosen clear of every base above and of fibre_model's 90-93M)
PT_NODE_BASE = 94_000_000      # physical-theory brace nodes: ele*100 + k (k = 99: X-crossing node)
PT_ELE_BASE = 95_000_000       # physical-theory brace fibre segments ele*100 + 1.., rigid end zones ele*100 + 61..
PT_ZL_BASE = 96_000_000        # physical-theory brace pins: ele*100 + k
PT_PINMAT_BASE = 39_000_000    # physical-theory brace pin springs (R5, NL-R2-20): ele*10 + 2*pin - 1 (translation) / 2*pin (torsion)
PT_SEC_BASE = 97_000_000       # physical-theory brace fibre sections / integrations
PT_TRANSF_BASE = 9_000_000     # physical-theory brace corotational transforms: + ele
PT_MAT0 = 47_000_000           # fibre materials of physical-theory braces
LINK_END = 3                   # link shear spring: node HN_BASE + ele*10 + 3, zeroLength ZL_BASE + ele*10 + 3


def _dir_vec(p1, p2):
    d = [p2[i] - p1[i] for i in range(3)]
    L = math.sqrt(sum(v * v for v in d))
    return [v / L for v in d], L


def member_kind(pkg, e):
    """col/beam/brace from member_schedule.csv, else from geometry (vertical => col)."""
    k = pkg.schedule.get(e["tag"], {}).get("member")
    if k:
        return k
    d, _ = _dir_vec(pkg.model.nodes[e["n1"]], pkg.model.nodes[e["n2"]])
    return "col" if abs(d[2]) > 0.9 else "beam"


def strong_rot_dof(pkg, e, kind):
    """Global rotational DOF (4=RX, 5=RY) whose spring carries the strong-axis moment.
    Beam along X bends about global Y -> 5; along Y -> 4. Column: local z = vecxz-direction of its transf:
    vecxz=(0,1,0) -> strong axis about Y (resists X sway) -> 5; vecxz=(1,0,0) -> 4."""
    if kind == "col":
        ty, vx, vy, vz = pkg.model.transfs[e["transf"]]
        return 5 if abs(vy) > 0.5 else 4
    d, _ = _dir_vec(pkg.model.nodes[e["n1"]], pkg.model.nodes[e["n2"]])
    return 5 if abs(d[0]) > 0.5 else 4


def strong_I_slot(pkg, e, kind):
    """Which elasticBeamColumn inertia argument (Iy or Iz) is the strong-axis one for this member.
    steltic add_column passes (Iy_weak, Ix_strong) -> 'Iz'; add_beam passes (Ix_strong, Iy_weak) -> 'Iy'."""
    return "Iz" if kind == "col" else "Iy"


def beam_params_for(pkg, prm, section, fr=True):
    """hinge_params view for ONE beam section with the design package's own RBS cut and unbraced length (NL-07).

    The component values (Table C5.5 a/b/c expressions, IO/LS/CP) stay exactly as supplied in hinge_params;
    only the member geometry they are evaluated with is taken per beam from the package:
      * RBS cut c (and a, b) -- hinge_params `beam_flexure.rbs_geometry_*[section]` if the engineer gave one,
        else the package's record for this section (package_reader.beam_details), else, for an FR beam of a
        frame the package shows to be RBS, the AISC 358-22 5.7 Step 1 default (package_reader.rbs_default).
        The hinge then uses Z_RBS = Zx - 2 c tf (d - tf) (AISC 358-22 Eq. 5.7-4) through rbs_c_in. A single
        global `rbs_c_in` in hinge_params is only used when the package records no RBS at all.
      * Lb -- the larger of the member's recorded Lb_in and the frame's beam-bracing spacing (D1.2), passed
        as Lb_over_ry = Lb / ry of this section (ry from sections_db); the hinge takes min(span, Lb). Without
        package data the hinge_params Lb rule is kept (and noted).
    Returns (prm_view, info) with info = {rbs: geometry|None, Lb_in, notes: [...]}."""
    from . import package_reader as PR
    from . import sections_db as SDB
    bp0 = prm.get("beam_flexure") or {}
    sec = PR._norm_sec(section)
    cache = getattr(pkg, "_beam_prm_cache", None)
    if cache is None:
        cache = {}
        try:
            pkg._beam_prm_cache = cache
        except Exception:
            pass
    key = (id(prm), sec, bool(fr))
    if key in cache:
        return cache[key]
    det = PR.beam_details(pkg)
    bp = dict(bp0)
    notes = []
    geo = None
    explicit = RBS.rbs_geometry_for_section(section, {k: v for k, v in bp0.items() if str(k).startswith("rbs_geometry")})
    if explicit:
        geo = dict(explicit, source="hinge_params beam_flexure.rbs_geometry_*")
    elif sec in det["rbs"]:
        geo = dict(det["rbs"][sec])
    elif det["rbs_frame"] and fr:
        try:
            geo = PR.rbs_default(SDB.props(section))
            notes.append("%s: RBS frame but no cut recorded for this section -> %s (c = %.3f in)" % (sec, geo["source"], geo["c_in"]))
        except Exception as ex:
            notes.append("%s: RBS frame, no cut recorded and no section properties (%s); full section used" % (sec, ex))
    if det["rbs_frame"] or explicit:
        if (bp0.get("rbs_c_in") or bp0.get("rbs_c_frac_bf")) and geo and not explicit and \
                abs(float(bp0.get("rbs_c_in") or 0) - geo["c_in"]) > 1e-6:
            notes.append("%s: hinge_params global rbs_c_in=%s superseded by the per-beam cut c=%.3f in (%s)"
                         % (sec, bp0.get("rbs_c_in"), geo["c_in"], geo["source"]))
        bp["rbs_c_in"] = float(geo["c_in"]) if (geo and fr) else 0.0
        bp["rbs_c_frac_bf"] = 0.0
        if geo and fr:
            bp["rbs_geometry_pkg"] = {sec: dict(a_in=geo["a_in"], b_in=geo["b_in"], c_in=geo["c_in"])}
        basis = str(bp0.get("basis") or bp0.get("source") or "")
        if geo and fr and bp0.get("mode") == "fr_connection" and basis and not PR._RBS_NAME.search(basis):
            notes.append("%s: the package records an RBS connection but hinge_params beam_flexure is not the RBS row (%s) -- "
                         "check the supplied Table C5.5 row" % (sec, basis[:80]))
    elif geo is None and (bp0.get("rbs_c_in") or bp0.get("rbs_c_frac_bf")):
        geo = None                                      # legacy: global cut from hinge_params, package silent
    # unbraced length
    cands = []
    if det["Lb"].get(sec):
        cands.append((det["Lb"][sec]["Lb_in"], det["Lb"][sec]["source"]))
    if fr and det["Lb_frame"]:
        cands.append((det["Lb_frame"]["Lb_in"], det["Lb_frame"]["source"]))
    Lb_in = None
    if cands:
        Lb_in, src = max(cands)
        try:
            ry = float(SDB.props(section)["ry"])
            bp["Lb_divisor"] = None
            bp["Lb_over_ry"] = Lb_in / ry
            bp["Lb_source"] = src
            if len(cands) > 1 and abs(cands[0][0] - cands[1][0]) > 0.01 * max(cands[0][0], cands[1][0]):
                notes.append("%s: Lb %.1f in (%s) vs %.1f in (%s); larger used" % (sec, cands[0][0], cands[0][1], cands[1][0], cands[1][1]))
        except Exception as ex:
            Lb_in = None
            notes.append("%s: Lb from package not applied (%s)" % (sec, ex))
    elif bp0.get("mode") == "fr_connection":
        notes.append("%s: no unbraced length in the package -> hinge_params rule (%s)"
                     % (sec, ("Lb = span/%s" % bp0["Lb_divisor"]) if bp0.get("Lb_divisor") else ("Lb/ry = %s" % bp0.get("Lb_over_ry"))))
    view = dict(prm)
    view["beam_flexure"] = bp
    # notes only where a hinge exists (an FR beam-to-column end); pinned framing has no hinge to qualify
    out = (view, dict(rbs=(geo if fr else None), Lb_in=Lb_in, notes=(notes if fr else [])))
    cache[key] = out
    return out


def fibre_end_rotation(h):
    """Plastic rotation (rad) of one monitored fibre hinge region (fibre_model, form "fibre_end").

    `plasticDeformation` of a forceBeamColumn is its basic deformation minus the initial-flexibility elastic
    part, i.e. theta_I,p = int (xi - 1) kappa_p dx and theta_J,p = int xi kappa_p dx. Over the region's
    segments the plastic curvature integral is sum (theta_J,p - theta_I,p) ("integral"); a single-segment
    member uses its own end rotation (-theta_I,p at end I, theta_J,p at end J), the concentrated-hinge
    equivalent of the curvature near that end."""
    i, j = h["comp"]
    tot = 0.0
    for et in h["segs"]:
        r = ops.eleResponse(et, "plasticDeformation")
        if not r or len(r) <= j:
            continue
        if h["mode"] == "integral":
            tot += r[j] - r[i]
        elif h["mode"] == "endI":
            tot -= r[i]
        else:
            tot += r[j]
    return tot


def zero_length_spring(tag, dof):
    """(deformation, spring force) of DOF `dof` (1..6) of a zeroLength spring built with all six -dir 1..6 in
    global orientation (every hinge / link / panel-zone spring of this package).

    SIGN: eleResponse(zl, "force") returns the 12 resisting NODAL forces, [node-1 (0:6), node-2 (6:12)]. The
    node-1 entries are MINUS the spring force (a spring k = 100 stretched by +0.05 gives force[0] = -5, force[6] =
    +5). The spring force that goes with eleResponse(zl, "deformation") (= u2 - u1) is therefore force[dof-1+6]
    (= -force[dof-1]). Using force[dof-1] made theta - M/K0 = plastic + 2 x elastic (merge integration fix)."""
    j = int(dof) - 1
    d = ops.eleResponse(tag, "deformation"); f = ops.eleResponse(tag, "force")
    th = d[j] if (d and len(d) > j) else 0.0
    if f and len(f) >= 12:
        M = f[j + 6]
    elif f and len(f) > j:
        M = -f[j]
    else:
        M = 0.0
    return th, M


def hinge_plastic_deformation(tag, h):
    """(plastic deformation, moment|axial force) of any registered hinge `hinges[tag]` at the current state:
    brace total axial deformation (in) and force (hinge_models.brace_axial_force: the truss force, or the first
    fibre segment's basic axial force for a physical-theory brace); fibre end region plastic rotation (rad,
    force None); ConcentratedPlasticity end IP or zeroLength IMK / link spring plastic deformation d - F/K0 and
    the spring force F (zero_length_spring: node-2 force, NOT the node-1 entry)."""
    if h["kind"] == "brace":
        d = ops.eleResponse(tag, "deformation")
        return (d[0] if d else 0.0), HM.brace_axial_force(tag, h)
    if h.get("form") == "fibre_end":
        return fibre_end_rotation(h), None
    if h.get("form") == "fbc_cp":
        ip = h.get("sec_ip", 1); jc = h.get("sec_comp", 0)
        d = ops.eleResponse(h["ele"], "section", ip, "deformation")
        f = ops.eleResponse(h["ele"], "section", ip, "force")
        th = d[jc] if (d and len(d) > jc) else 0.0
        M = f[jc] if (f and len(f) > jc) else 0.0
        return th - M / h["K0"], M
    th, M = zero_length_spring(tag, h["dof"])
    return th - M / h["K0"], M


def column_nodes(pkg):
    """Nodes a column (with a section) frames into -- where a beam end can be a beam-to-column connection."""
    cached = getattr(pkg, "_column_nodes", None)
    if cached is not None:
        return cached
    out = set()
    for e in pkg.model.elements:
        if "etype" not in e and member_kind(pkg, e) == "col":
            out.update((e["n1"], e["n2"]))
    try:
        pkg._column_nodes = out
    except Exception:
        pass
    return out


def fr_column_ends(pkg, e):
    """(end1, end2) booleans: this beam end is not released about the major axis AND frames into a column,
    i.e. a moment (FR) beam-to-column connection -- the only place an RBS cut belongs."""
    relz = _major_release_code(e, strong_I_slot(pkg, e, "beam"))
    cn = column_nodes(pkg)
    return (relz not in (1, 3) and e["n1"] in cn), (relz not in (2, 3) and e["n2"] in cn)


def moment_frame_members(pkg):
    """Number of FR beams (at least one major-axis end not released at a column) -- members whose flexural
    hinges an NSP of this frame must evaluate."""
    n = 0
    for e in pkg.model.elements:
        if "etype" in e or member_kind(pkg, e) != "beam" or not pkg.schedule.get(e["tag"], {}).get("section"):
            continue
        if any(fr_column_ends(pkg, e)):
            n += 1
    return n


def _major_release_code(e, slot):
    rel = e.get("release") or []
    flag = "-releasey" if slot == "Iy" else "-releasez"
    return int(rel[rel.index(flag) + 1]) if flag in rel else 0


def _end_is_released(relz, end):
    return (end == 1 and relz in (1, 3)) or (end == 2 and relz in (2, 3))


def fr_joint_plan(pkg):
    """FR moment-frame joints for scissors PZ: nodes with ≥1 unreleased column end and ≥1 unreleased beam end.

    Returns {joint_node: {dof: {"col_section", "beam_sections": [...], "beam_ends": [(ele, end), ...]}}}.
    Pin-released ends are excluded. One rotational spring per active strong-axis DOF (4 and/or 5).
    """
    from collections import defaultdict
    from . import sections_db as SDB
    cols_at = defaultdict(list)   # joint -> list of (sec, dof)
    beams_at = defaultdict(list)  # joint -> list of (ele, end, sec, dof)
    for e in pkg.model.elements:
        if "etype" in e:
            continue
        kind = member_kind(pkg, e)
        if kind not in ("col", "beam"):
            continue
        sec = pkg.schedule.get(e["tag"], {}).get("section")
        if not sec:
            continue
        slot = strong_I_slot(pkg, e, kind)
        relz = _major_release_code(e, slot)
        dof = strong_rot_dof(pkg, e, kind)
        for end, n in ((1, e["n1"]), (2, e["n2"])):
            if _end_is_released(relz, end):
                continue
            if kind == "col":
                cols_at[n].append((sec, dof))
            else:
                beams_at[n].append((e["tag"], end, sec, dof))
    plan = {}
    for n, beams in beams_at.items():
        if n not in cols_at:
            continue
        best_col = max(cols_at[n], key=lambda sd: SDB.props(sd[0])["d"] * SDB.props(sd[0])["tw"])
        col_sec = best_col[0]
        by_dof = {}
        for ele, end, sec, dof in beams:
            slot = by_dof.setdefault(dof, {"col_section": col_sec, "beam_sections": [], "beam_ends": []})
            slot["beam_sections"].append(sec)
            slot["beam_ends"].append((ele, end))
        plan[n] = by_dof
    return plan


def levels(pkg):
    """Diaphragm levels: [(k, z, master, [slave nodes])] sorted by z (k = 1..NF)."""
    out = []
    for perp, master, slaves in pkg.model.diaphragms:
        z = pkg.model.nodes[master][2]
        out.append((z, master, slaves))
    out.sort()
    return [(k + 1, z, m, s) for k, (z, m, s) in enumerate(out)]


def level_mass(pkg, master, slaves):
    """R2 patch (NL-R2-01): the level's seismic mass is wherever the engine put it -- on the master or on
    the centre-of-mass node cmtag(k) slaved to it (HR-19). Returns dict(m, J (about the CoM), xc, yc,
    xm, ym, node) where `node` is the single mass-carrying node (load point) or the master."""
    xm, ym = pkg.model.nodes[master][0], pkg.model.nodes[master][1]
    pts = []
    for t in [master] + list(slaves):
        mv = pkg.model.masses.get(t)
        if mv and mv[0] > 0:
            pts.append((t, mv))
    M = sum(mv[0] for t, mv in pts)
    if M <= 0:
        return dict(m=0.0, J=0.0, xc=xm, yc=ym, xm=xm, ym=ym, node=master)
    xc = sum(mv[0] * pkg.model.nodes[t][0] for t, mv in pts) / M
    yc = sum(mv[0] * pkg.model.nodes[t][1] for t, mv in pts) / M
    J = sum(mv[5] + mv[0] * ((pkg.model.nodes[t][0] - xc) ** 2 + (pkg.model.nodes[t][1] - yc) ** 2) for t, mv in pts)
    node = pts[0][0] if len(pts) == 1 else master
    return dict(m=M, J=J, xc=xc, yc=yc, xm=xm, ym=ym, node=node)


def com_eigvec(lm, master, mode):
    """(ux, uy, rz) of mode `mode` at the level centre of mass (rigid diaphragm kinematics)."""
    ux = ops.nodeEigenvector(master, mode, 1); uy = ops.nodeEigenvector(master, mode, 2)
    rz = ops.nodeEigenvector(master, mode, 6)
    return ux - rz * (lm["yc"] - lm["ym"]), uy + rz * (lm["xc"] - lm["xm"]), rz


def gravity_loads(pkg, prm, verbose=True):
    """1.1*(QD + 0.25*QL) per level (ASCE 41 7.2.2 form), QD from the recorded seismic mass (D + cladding),
    QL from cfg L_floor psf x level footprint area; spread equally over that level's column nodes.
    Returns {node: Pz_kip (negative = down)}, plus a per-level table for the report."""
    lv = levels(pkg)
    Lpsf = pkg.basis.L_floor_psf if pkg.basis.L_floor_psf is not None else 50.0
    Lroof = 20.0
    table, loads = [], {}
    for k, z, master, slaves in lv:
        m = level_mass(pkg, master, slaves)["m"]          # R2 patch: master + CoM node
        WD = m * G_IN
        xs = [pkg.model.nodes[n][0] for n in slaves]; ys = [pkg.model.nodes[n][1] for n in slaves]
        area_ft2 = (max(xs) - min(xs)) * (max(ys) - min(ys)) / 144.0
        L = (Lroof if k == len(lv) else Lpsf) * area_ft2 / 1000.0
        QG = 1.1 * (WD + 0.25 * L)
        per = QG / len(slaves)
        for n in slaves:
            loads[n] = loads.get(n, 0.0) - per
        table.append(dict(level=k, z_in=z, WD_kip=round(WD, 1), QL25_kip=round(0.25 * L, 1), QG_kip=round(QG, 1),
                          nodes=len(slaves), area_ft2=round(area_ft2)))
    return loads, table


def build_elastic(pkg):
    """Replay the recorded elastic model 1:1 (used to get gravity column axials PG)."""
    m = pkg.model
    ops.wipe(); ops.model("basic", "-ndm", m.ndm, "-ndf", m.ndf)
    for t, xyz in m.nodes.items():
        ops.node(t, *xyz)
    for t, fl in m.fixes.items():
        ops.fix(t, *fl)
    for t, (ty, vx, vy, vz) in m.transfs.items():
        ops.geomTransf(ty, t, vx, vy, vz)
    for t, a in m.materials.items():
        ops.uniaxialMaterial(*a)
    for e in m.elements:
        if "etype" in e:
            ops.element(*e["raw"]); continue
        args = [e["A"], e["E"], e["G"], e["J"], e["Iy"], e["Iz"], e["transf"]] + (e["release"] or [])
        ops.element("elasticBeamColumn", e["tag"], e["n1"], e["n2"], *args)
    for perp, master, slaves in m.diaphragms:
        ops.rigidDiaphragm(perp, master, *slaves)


def _apply_gravity(loads, nsteps=10):
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1)
    for n, pz in loads.items():
        ops.load(n, 0.0, 0.0, pz, 0.0, 0.0, 0.0)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-8, 50); ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0 / nsteps); ops.analysis("Static")
    ok = ops.analyze(nsteps)
    ops.loadConst("-time", 0.0)
    return ok


def column_gravity_axials(pkg, loads):
    """PG (compression positive, kip) for every column element under the pushover gravity load."""
    build_elastic(pkg)
    ok = _apply_gravity(loads)
    if ok != 0:
        raise RuntimeError("elastic gravity analysis failed (%d)" % ok)
    PG = {}
    for e in pkg.model.elements:
        if "etype" in e or member_kind(pkg, e) != "col":
            continue
        f = ops.eleResponse(e["tag"], "localForce")
        PG[e["tag"]] = max(0.0, f[0]) if f else 0.0     # local N at end i: +ve = compression in OpenSees local force
    return PG



def _build_rbs7_beam(pkg, e, prm, sec, spec, slot, dof, p1, p2, L, geo, beam_side, mat, hinges, stats):
    """Remesh one FR SMF beam into 7 elastic segments + 2 ModIMK ZL at RBS centres.

    Returns updated mat tag counter. Populates hinges/stats like the single-span path.
    """
    a, b, c = geo["a_in"], geo["b_in"], geo["c_in"]
    RBS.segment_stations(L, a, b)  # validate fit
    inode, seg_ele = RBS.remesh_tags(e["tag"])
    I_strong = e[slot]
    I_weak = e["Iz"] if slot == "Iy" else e["Iy"]
    red = RBS.reduced_props(sec, c, e["A"], I_strong, I_weak, e["J"])

    attach_i = beam_side[e["n1"]] if e["n1"] in beam_side else e["n1"]
    attach_j = beam_side[e["n2"]] if e["n2"] in beam_side else e["n2"]

    # Coincident hinge-split nodes at RBS centres (offset a+b/2 from each joint)
    xyz_i = RBS.xyz_along(p1, p2, a + 0.5 * b, L)
    xyz_j = RBS.xyz_along(p1, p2, L - (a + 0.5 * b), L)
    n_out_i = inode(1)
    n_in_i = HN_BASE + e["tag"] * 10 + 1
    n_in_j = HN_BASE + e["tag"] * 10 + 2
    n_out_j = inode(2)
    for nt, xyz in ((n_out_i, xyz_i), (n_in_i, xyz_i), (n_in_j, xyz_j), (n_out_j, xyz_j)):
        ops.node(nt, *xyz)

    # Continuum nodes at s = a, a+b, L-(a+b), L-a
    s_cont = (a, a + b, L - (a + b), L - a)
    n_cont = []
    for k, s in enumerate(s_cont):
        nt = inode(3 + k)
        ops.node(nt, *RBS.xyz_along(p1, p2, s, L))
        n_cont.append(nt)
    n1, n3, n4, n6 = n_cont  # names match station sketch

    # seg endpoints: (idx, ni, nj, kind)
    ends = [
        (1, attach_i, n1, "full"),
        (2, n1, n_out_i, "rbs"),
        (3, n_in_i, n3, "rbs"),
        (4, n3, n4, "full"),
        (5, n4, n_in_j, "rbs"),
        (6, n_out_j, n6, "rbs"),
        (7, n6, attach_j, "full"),
    ]
    I_mid = I_strong * (N_STIFF + 1.0) / N_STIFF
    seg_tags = []
    for idx, ni, nj, kind in ends:
        A, J = e["A"], e["J"]
        Iy, Iz = e["Iy"], e["Iz"]
        if kind == "rbs":
            A, J = red["A"], red["J"]
            if slot == "Iy":
                Iy, Iz = red["I_strong"], red["I_weak"]
            else:
                Iz, Iy = red["I_strong"], red["I_weak"]
        elif idx == 4:
            if slot == "Iy":
                Iy = I_mid
            else:
                Iz = I_mid
        et = seg_ele(idx)
        ops.element("elasticBeamColumn", et, ni, nj, A, e["E"], e["G"], J, Iy, Iz, e["transf"])
        seg_tags.append(et)
        stats.setdefault("elastic_ele_tags", []).append(et)

    K0 = N_STIFF * 6.0 * e["E"] * I_strong / L
    post = prm.get("numerics", {}).get("post_cap_ratio", 0.15)
    other_rot = 4 if dof == 5 else 5
    for end, n_a, n_b, joint in (
        (1, n_out_i, n_in_i, e["n1"]),
        (2, n_out_j, n_in_j, e["n2"]),
    ):
        mat += 1
        HM.make_imk_material(mat, spec, K0, post_cap_ratio=post)
        mats = {1: 1, 2: 1, 3: 1, other_rot: 2, 6: 2, dof: mat}
        zl = ZL_BASE + e["tag"] * 10 + end
        ops.element("zeroLength", zl, n_a, n_b, "-mat", *[mats[k] for k in (1, 2, 3, 4, 5, 6)],
                    "-dir", 1, 2, 3, 4, 5, 6)
        hinges[zl] = dict(ele=e["tag"], end=end, kind="beam", section=sec, dof=dof, K0=K0, mat=mat,
                          node=joint, z=p1[2] if end == 1 else p2[2], spec=spec,
                          rbs_offset_in=a + 0.5 * b)
    stats["beam"] += 1
    stats["rbs_remesh_beams"] = stats.get("rbs_remesh_beams", 0) + 1
    stats.setdefault("rbs_formula", red.get("formula"))
    stats.setdefault("rbs_seg_tags_sample", seg_tags[:3] + [seg_tags[-1]])
    return mat


# --------------------------------------------------------------------------- NL-02 / NL-03 / NL-10 element builders
def package_cfg(pkg):
    """The executed cfg of the package (steltic_ddm.ingest.load_cfg, HR engine importable), cached on the package;
    (None, reason) when cfg.py is absent or cannot be executed."""
    c = getattr(pkg, "_cfg_exec", None)
    if c is None:
        try:
            from steltic_ddm.ingest import load_cfg
            c = (load_cfg(str(pkg.root)), "cfg.py")
        except Exception as ex:                                 # noqa: BLE001
            c = (None, "cfg.py not executable (%s)" % str(ex)[:80])
        try:
            pkg._cfg_exec = c
        except Exception:                                       # noqa: BLE001
            pass
    return c


def element_context(pkg, prm):
    """Per-build data for the special elements: BRB package data (NL-02; NL-R2-10: also the HR engine's cfg['brb'] and
    capacity_design.BRB_adjusted_strengths) and the EBF link census (NL-03; NL-R2-13: links only in a declared EBF)."""
    notes = []
    links = HM.find_links_pkg(pkg, prm, member_kind, notes=notes)
    cfg = package_cfg(pkg)[0] if any(SDB.is_brb(str(v.get("section") or "")) for v in (pkg.schedule or {}).values()) else None
    return dict(brb=HM.brb_package_data(pkg.calc, cfg), links=links, link_notes=notes, pt_secs={}, pt_builder=None, pt_count=0)


def _note(stats, msg):
    w = stats.setdefault("model_warnings", [])
    if msg not in w:
        w.append(msg)


def _brace_hinge(e, sec, p1, p2, spec, mat, K0, **extra):
    return dict(ele=e["tag"], end=0, kind="brace", section=sec, dof=0, K0=K0, mat=mat, node=e["n1"], z=max(p1[2], p2[2]),
                spec=spec, **extra)


def build_brace(pkg, e, sec, prm, mat, hinges, stats, ctx):
    """Nonlinear brace for element e (a raw Truss or an elasticBeamColumn tagged 'brace'). Returns the material counter.

    BRB label (NL-02)          -> corotTruss with the C3.3 BRB material (hinge_models.brb_spec / make_brb_material);
                                  stiffness area = the HR truss area (KF*Asc), yielding area = Asc.
    buckling brace, NSP        -> corotTruss + Hysteretic on the Table C3.4 (or placeholder) backbone (unchanged).
    buckling brace, NLRHA with brace_axial.nlrha_element = physical_theory (NL-10) -> cambered corotational fibre brace,
                                  Steel02 + Fatigue (rectangular HSS only; other shapes keep the truss and are flagged)."""
    m = pkg.model
    p1, p2 = m.nodes[e["n1"]], m.nodes[e["n2"]]; _, L = _dir_vec(p1, p2)
    if SDB.is_brb(sec):
        A_model = float(e["raw"][4]) if "etype" in e else float(e["A"])
        spec = HM.brb_spec(sec, L, prm, A_model=A_model, pkg_data=ctx["brb"])
        mat += 1; name = HM.make_brb_material(mat, spec, prm)
        ops.element("corotTruss", e["tag"], e["n1"], e["n2"], spec.A_model, mat)
        hinges[e["tag"]] = _brace_hinge(e, sec, p1, p2, spec, mat, spec.K_axial, brb=True, material=name)
        stats["brb"] = stats.get("brb", 0) + 1; stats["brace_nonlinear"] += 1
        for f in spec.flags:
            if f.startswith("WARNING") or "FALLBACK" in f or "TEMPLATE" in f:
                _note(stats, "BRB %s: %s" % (sec, f))
        return mat
    Lc, lc_src = brace_Lc(pkg, ctx, prm, e, L)                   # NL-R2-19: the design's buckling length
    spec = HM.brace_spec(sec, L, prm, Lc_in=Lc)
    _note(stats, "buckling braces: buckling length from %s" % lc_src.split(" x ")[0] if " x " in lc_src else "buckling braces: " + lc_src)
    if HM.brace_element_form(prm) == "physical_theory":
        if str(sec).upper().startswith("HSS") and HM._hss_outside_and_tdes(sec)[0]:
            mat = _build_physical_theory_brace(pkg, e, sec, prm, spec, L, p1, p2, mat, hinges, stats, ctx)
            stats["brace_nonlinear"] += 1
            return mat
        _note(stats, "physical-theory brace needs a rectangular HSS; %s kept as Hysteretic truss (no cyclic degradation)" % sec)
    mat += 1; HM.make_brace_material(mat, spec, prm)
    ops.element("corotTruss", e["tag"], e["n1"], e["n2"], spec.A, mat)
    hinges[e["tag"]] = _brace_hinge(e, sec, p1, p2, spec, mat, E_KSI_AL(spec))
    stats["brace_nonlinear"] += 1
    return mat


def brace_geometry(pkg, ctx):
    """NL-R2-19: the buckling length of every buckling brace, and X-brace crossings, from the HR design.

    * Lc (in): cfg['brace_length'] (number, or {section: in}) or cfg['brace_length_factor'] x work-point length --
      the end-to-end length the HR design used for the expected compression (AISC 341 F2.3); otherwise
      brace_axial.K_effective x work-point length (flagged).
    * crossings: two brace diagonals whose work lines intersect inside both spans (not at a shared node) are an X
      connected at the crossing (the HR contract's "crossing X-brace diagonals" case).
    Cached in ctx['brace_geom']."""
    if ctx.get("brace_geom") is not None:
        return ctx["brace_geom"]
    cfg, src_cfg = package_cfg(pkg)
    if not hasattr(pkg.model, "elements"):                      # minimal test packages: no element list
        ctx["brace_geom"] = dict(lc={}, cross={}, cfg_source=src_cfg, factor=None)
        return ctx["brace_geom"]
    fac = None; bl = None
    if isinstance(cfg, dict):
        fac = cfg.get("brace_length_factor")
        bl = cfg.get("brace_length")
    braces = []
    for e in pkg.model.elements:
        if member_kind(pkg, e) != "brace":
            continue
        sec = str(pkg.schedule.get(e["tag"], {}).get("section") or "")
        if SDB.is_brb(sec):
            continue
        braces.append((e["tag"], e["n1"], e["n2"], sec.upper().replace(" ", "")))
    nodes = pkg.model.nodes
    lc, cross = {}, {}
    for tag, n1, n2, sec in braces:
        L = math.dist(nodes[n1], nodes[n2])
        if isinstance(bl, (int, float)) and bl > 0:
            lc[tag] = (float(bl), "cfg brace_length")
        elif isinstance(bl, dict) and bl:
            v = {str(k).upper().replace(" ", ""): x for k, x in bl.items()}.get(sec)
            if isinstance(v, (int, float)) and v > 0:
                lc[tag] = (float(v), "cfg brace_length[%s]" % sec)
        if tag not in lc and isinstance(fac, (int, float)) and fac > 0:
            lc[tag] = (float(fac) * L, "cfg brace_length_factor %.3g x work-point length" % fac)
    # crossings: closest points of two segments, both strictly inside, distance < 1 in
    for i in range(len(braces)):
        ti, a1, a2, _ = braces[i]
        P, Q = nodes[a1], nodes[a2]
        for j in range(i + 1, len(braces)):
            tj, b1, b2, _ = braces[j]
            if len({a1, a2, b1, b2}) < 4:
                continue
            R, S = nodes[b1], nodes[b2]
            u = [Q[k] - P[k] for k in range(3)]; v = [S[k] - R[k] for k in range(3)]; w0 = [P[k] - R[k] for k in range(3)]
            a = sum(x * x for x in u); b = sum(u[k] * v[k] for k in range(3)); c = sum(x * x for x in v)
            d = sum(u[k] * w0[k] for k in range(3)); e_ = sum(v[k] * w0[k] for k in range(3))
            den = a * c - b * b
            if den < 1e-9 * a * c:
                continue                                         # parallel
            s = (b * e_ - c * d) / den; t = (a * e_ - b * d) / den
            if not (0.05 < s < 0.95 and 0.05 < t < 0.95):
                continue
            X1 = [P[k] + s * u[k] for k in range(3)]; X2 = [R[k] + t * v[k] for k in range(3)]
            if math.dist(X1, X2) > 1.0:
                continue
            X = [(X1[k] + X2[k]) / 2 for k in range(3)]
            cross[ti] = dict(partner=tj, s=s, X=X); cross[tj] = dict(partner=ti, s=t, X=X)
    ctx["brace_geom"] = dict(lc=lc, cross=cross, cfg_source=src_cfg, factor=fac)
    return ctx["brace_geom"]


def brace_Lc(pkg, ctx, prm, e, L):
    """(Lc, source) for buckling brace e (see brace_geometry); falls back to K_effective x L, and for an X crossing
    without a design length to K x the longer half."""
    g = brace_geometry(pkg, ctx)
    if e["tag"] in g["lc"]:
        return g["lc"][e["tag"]]
    K = float((prm.get("brace_axial") or {}).get("K_effective", 1.0))
    if e["tag"] in g["cross"]:
        s = g["cross"][e["tag"]]["s"]
        return K * max(s, 1 - s) * L, "K_effective x longer half of the X (no design brace length in cfg)"
    return K * L, "K_effective x work-point length (no design brace length in cfg -- check)"


def _pt_unit(tag, c, gA, pA, gB, pB, eA, eB, sign, w, cam, nseg, st, tr, tr_rigid, etype, mL, pinA, pinB, ax, yv):
    """One buckling unit of a physical-theory brace between attach nodes gA (at pA) and gB (at pB):
    gA -[rigid end zone eA]- a -[pin if pinA]- chain (nseg cambered segments over the clear length) -[pin if pinB]- b
    -[rigid end zone eB]- gB. c = per-brace tag counters {n, e, z, s}. Returns (segments, chain nodes)."""
    Lu = math.dist(pA, pB); d = [(pB[q] - pA[q]) / Lu for q in range(3)]
    Lcl = Lu - eA - eB
    def pt(sx):
        return [pA[q] + d[q] * sx for q in range(3)]
    def new_node(xyz):
        c["n"] += 1; t = PT_NODE_BASE + tag * 100 + c["n"]; ops.node(t, *xyz); return t
    def rigid(n_from, n_to):
        c["e"] += 1
        ops.element("elasticBeamColumn", PT_ELE_BASE + tag * 100 + 60 + c["e"], n_from, n_to,
                    c["A"], 29000.0, 11200.0, 1.0e5, 1.0e5, 1.0e5, tr_rigid)   # gusset zone: brace EA (axial
                                                                              # stiffness as in the HR model), rigid in bending
    def pin(n_frame, n_chain):
        # NL-R2-20 (R5): pin springs scaled to the brace -- pin_factor x E A / L_clear on the translations, 100 x that on
        # torsion -- instead of the global RIGID_T / RIGID_R (1e9 / 1e11). Next to a buckled chain whose lateral stiffness
        # is ~0, the 1e9 penalty made the effective tangent numerically singular (Ex24 Gilroy #3: one Newton
        # iteration -> 1e26). At 1000 x EA/L the added axial flexibility is 0.1 % of the brace's.
        c["z"] += 1
        kt = c.get("pin_factor", 1000.0) * 29000.0 * c["A"] / max(Lcl, 1.0)
        mt, mr = PT_PINMAT_BASE + tag * 10 + 2 * c["z"] - 1, PT_PINMAT_BASE + tag * 10 + 2 * c["z"]
        ops.uniaxialMaterial("Elastic", mt, kt); ops.uniaxialMaterial("Elastic", mr, 100.0 * kt)
        c.setdefault("pin_k", []).append(kt)
        ops.element("zeroLength", PT_ZL_BASE + tag * 100 + c["z"], n_frame, n_chain, "-mat", mt, mt, mt, mr, "-dir", 1, 2, 3, 4,
                    "-orient", *ax, *yv)                        # translations + torsion; bending released
    startA = gA
    if eA > 1e-6:
        startA = new_node(pt(eA)); rigid(gA, startA)
    a = new_node(pt(eA)) if pinA else startA
    if pinA:
        pin(startA, a)
    endB = gB
    if eB > 1e-6:
        endB = new_node(pt(Lu - eB)); rigid(endB, gB)
    b = new_node(pt(Lu - eB)) if pinB else endB
    if pinB:
        pin(endB, b)
    chain = [a]
    for i in range(1, nseg):
        f = i / nseg
        off = sign * cam * Lcl * math.sin(math.pi * f)
        base = pt(eA + f * Lcl)
        chain.append(new_node([base[q] + w[q] * off for q in range(3)]))
    chain.append(b)
    segs = []
    for i in range(nseg):
        c["s"] += 1
        et = PT_ELE_BASE + tag * 100 + c["s"]
        if etype == "dispBeamColumn":
            ops.element("dispBeamColumn", et, chain[i], chain[i + 1], tr, st)
        else:
            ops.element("forceBeamColumn", et, chain[i], chain[i + 1], tr, st, "-iter", 50, 1e-6)
        segs.append(et)
    seg = Lcl / nseg                                              # own mass: clear length on the chain, end zones on its ends
    for k_, nd in enumerate(chain):
        mt = mL * seg * (0.5 if k_ in (0, len(chain) - 1) else 1.0)
        if k_ == 0:
            mt += mL * eA
        if k_ == len(chain) - 1:
            mt += mL * eB
        ri = mL * seg ** 3 / 12.0
        cur = list(ops.nodeMass(nd)) if nd in ops.getNodeTags() else [0.0] * 6
        ops.mass(nd, *[cur[q] + v for q, v in enumerate((mt, mt, mt, ri, ri, ri))])
    return segs, chain


def _build_physical_theory_brace(pkg, e, sec, prm, spec, L, p1, p2, mat, hinges, stats, ctx):
    """NL-10 physical-theory brace for the NLRHA (Uriz & Mahin 2008), with the R2 corrections:

    * NL-R2-09: the brace's own steel mass on its nodes (balanced off the floors in _pt_mass_balance) -- without it the
      buckling snap had no inertia and Newton diverged at the first large excursion;
    * NL-R2-19: the clear buckling length of the HR design (brace_Lc): stiff gusset end zones take the rest of the
      work-point length; an X connected at the crossing is modelled as two continuous diagonals sharing translations
      at the crossing, each half pinned at its gusset and cambered in opposite directions (full-sine shape);
    * element: dispBeamColumn by default (no element-level iteration; brace_axial.physical_theory.element overrides).
    Fibre section = rectangular HSS (0.93 t_nom) of Steel02 (Fye, b, R0) wrapped in Fatigue (eps0, m). The registered
    tag e['tag'] is a zero-stiffness corotTruss n1-n2 that measures the end-to-end deformation for the monitors; the
    force comes from the first fibre segment (hinge_models.brace_axial_force via hinges[tag]['force_ele'])."""
    from steltic_ddm.sections_fiber import FiberSectionBuilder
    tag = e["tag"]
    fp = HM.brace_fatigue_params(sec, spec.KL_r, spec.Fye_ksi, prm)
    if ctx.get("pt_builder") is None:
        ctx["pt_builder"] = FiberSectionBuilder(ops, Fy=spec.Fye_ksi, hardening=fp["b"], residual="none", elastic=False, mat_tag0=PT_MAT0)
    bld = ctx["pt_builder"]
    key = (str(sec).upper(), round(spec.Fye_ksi, 3), round(fp["eps0"], 5), fp["m"])
    if key not in ctx["pt_secs"]:
        st = PT_SEC_BASE + len(ctx["pt_secs"]) + 1
        bld.hss_rect(st, str(sec).upper(), n_per_side=8, n_thick=2, residual="none",
                     material=dict(kind="Steel02", Fy=spec.Fye_ksi, b=fp["b"], R0=fp["R0"], fatigue=(fp["eps0"], fp["m"])))
        ops.beamIntegration("Lobatto", st, st, max(3, fp["nip"]))
        ctx["pt_secs"][key] = st
    st = ctx["pt_secs"][key]
    ax = [(p2[i] - p1[i]) / L for i in range(3)]
    w = [ax[1], -ax[0], 0.0]                                   # horizontal, perpendicular to the brace: out of the frame plane
    nw = math.sqrt(w[0] ** 2 + w[1] ** 2)
    w = [1.0, 0.0, 0.0] if nw < 1e-9 else [v / nw for v in w]
    tr = PT_TRANSF_BASE + tag
    ops.geomTransf("Corotational", tr, *w)
    tr_rigid = PT_TRANSF_BASE + 500_000 + tag
    ops.geomTransf("Linear", tr_rigid, *w)
    ref = (0.0, 0.0, 1.0) if abs(ax[2]) < 0.9 else (1.0, 0.0, 0.0)
    yv = (ref[1] * ax[2] - ref[2] * ax[1], ref[2] * ax[0] - ref[0] * ax[2], ref[0] * ax[1] - ref[1] * ax[0])
    nseg = max(2, min(fp["nseg"], 8))
    etype = fp.get("element", "dispBeamColumn")
    A_in2 = SDB.props(sec)["A"]
    mL = A_in2 * STEEL_DENSITY_KIP_IN3 / G_IN                     # kip-s2/in per inch of brace
    Lc, lc_src = brace_Lc(pkg, ctx, prm, e, L)
    g = brace_geometry(pkg, ctx)
    cr = g["cross"].get(tag)
    ptp = (prm.get("brace_axial") or {}).get("physical_theory") or {}
    c = dict(n=0, e=0, z=0, s=0, A=A_in2, pin_factor=float(ptp.get("pin_stiffness_factor", 1000.0)))
    if cr is None:
        Lcl = min(Lc, L); ez = 0.5 * (L - Lcl)
        segs, chain = _pt_unit(tag, c, e["n1"], p1, e["n2"], p2, ez, ez, 1.0, w, fp["camber"], nseg, st, tr,
                               tr_rigid, etype, mL, True, True, ax, yv)
        geom = "single: clear length %.0f in of %.0f in (%s), end zones %.0f in" % (Lcl, L, lc_src, ez)
    else:
        # X connected at the crossing: this diagonal's own crossing node, translations tied to the partner's
        X = cr["X"]
        xn = PT_NODE_BASE + tag * 100 + 99
        ops.node(xn, *X)
        ctx.setdefault("pt_cross_nodes", {})[tag] = xn
        pn = ctx["pt_cross_nodes"].get(cr["partner"])
        if pn is not None:
            ops.equalDOF(pn, xn, 1, 2, 3)
        s = cr["s"]; L1 = s * L; L2 = (1 - s) * L
        e1 = max(0.0, L1 - min(Lc, L1)); e2 = max(0.0, L2 - min(Lc, L2))
        segs1, ch1 = _pt_unit(tag, c, e["n1"], p1, xn, X, e1, 0.0, 1.0, w, fp["camber"], nseg, st, tr,
                              tr_rigid, etype, mL, True, False, ax, yv)
        segs2, ch2 = _pt_unit(tag, c, xn, X, e["n2"], p2, 0.0, e2, -1.0, w, fp["camber"], nseg, st, tr,
                              tr_rigid, etype, mL, False, True, ax, yv)
        segs, chain = segs1 + segs2, ch1 + ch2[1:]
        geom = "X crossing at s=%.2f with brace %d: halves %.0f / %.0f in, clear %.0f / %.0f in (%s)" % (
            s, cr["partner"], L1, L2, L1 - e1, L2 - e2, lc_src)
    ctx.setdefault("pt_mass", []).append((e["n1"], e["n2"], mL * L))
    mat += 1
    ops.uniaxialMaterial("Elastic", mat, 1.0e-6)
    ops.element("corotTruss", tag, e["n1"], e["n2"], 1.0, mat)  # deformation monitor (no stiffness)
    hinges[tag] = _brace_hinge(e, sec, p1, p2, spec, mat, E_KSI_AL(spec), form="physical_theory", force_ele=segs[0],
                               segments=segs, fatigue=dict(eps0=fp["eps0"], m=fp["m"]), camber=fp["camber"], geometry=geom, Lc=Lc)
    stats["brace_physical_theory"] = stats.get("brace_physical_theory", 0) + 1
    stats.setdefault("pt_ele_tags", []).extend(segs)
    stats.setdefault("pt_geometry", []).append(geom + "; pin springs %s kip/in" % "/".join("%.3g" % k for k in c.get("pin_k", [])))
    for f in fp["flags"]:
        _note(stats, "physical-theory brace %s: %s" % (sec, f))
    return mat


def build_link_imk(pkg, e, sec, prm, mat, hinges, stats, ctx, beam_side=None):
    """NL-03 EBF link, concentrated-plasticity form:
        n1 -[zeroLength: shear spring on global Z (link shear), rigid elsewhere]- s -[IMK flexural spring]- ni
           == elastic interior (I x (n+1)/n) == nj -[IMK flexural spring]- n2
    Shear spring: hinge_models.link_specs / make_link_shear_material (Table C2.4 backbone, Vp = 0.6 Fye Alw), registered
    as kind 'link' (deformation = transverse displacement; limits = gamma x e). Flexural springs: Table C2.2 values,
    registered as kind 'beam' with section '<sec> link'."""
    m = pkg.model
    tag = e["tag"]; p1, p2 = m.nodes[e["n1"]], m.nodes[e["n2"]]; _, L = _dir_vec(p1, p2)
    shear, flex, info = HM.link_specs(sec, L, prm)
    sn = HN_BASE + tag * 10 + LINK_END
    ops.node(sn, *p1)
    mat += 1; HM.make_link_shear_material(mat, shear, prm)
    mats = {1: 1, 2: 1, 3: mat, 4: 2, 5: 2, 6: 2}
    zl = ZL_BASE + tag * 10 + LINK_END
    n1_attach = beam_side.get(e["n1"], e["n1"]) if beam_side else e["n1"]
    ops.element("zeroLength", zl, n1_attach, sn, "-mat", *[mats[k] for k in (1, 2, 3, 4, 5, 6)], "-dir", 1, 2, 3, 4, 5, 6)
    hinges[zl] = dict(ele=tag, end=0, kind="link", section=sec, dof=3, K0=shear.Ks, mat=mat, node=e["n1"], z=p1[2], spec=shear, link=info)
    slot = strong_I_slot(pkg, e, "beam"); dof = strong_rot_dof(pkg, e, "beam")
    I = e[slot]
    ni, nj = HN_BASE + tag * 10 + 1, HN_BASE + tag * 10 + 2
    ops.node(ni, *p1); ops.node(nj, *p2)
    args = dict(A=e["A"], E=e["E"], G=e["G"], J=e["J"], Iy=e["Iy"], Iz=e["Iz"])
    args[slot] = I * (N_STIFF + 1.0) / N_STIFF
    ops.element("elasticBeamColumn", tag, ni, nj, args["A"], args["E"], args["G"], args["J"], args["Iy"], args["Iz"], e["transf"])
    stats.setdefault("elastic_ele_tags", []).append(tag)
    K0 = N_STIFF * 6.0 * e["E"] * I / L
    post = prm.get("numerics", {}).get("post_cap_ratio", 0.15)
    other_rot = 4 if dof == 5 else 5
    n2_attach = beam_side.get(e["n2"], e["n2"]) if beam_side else e["n2"]
    for end, attach, nn, z in ((1, sn, ni, p1[2]), (2, n2_attach, nj, p2[2])):
        mat += 1
        HM.make_imk_material(mat, flex, K0, post_cap_ratio=post)
        mz = {1: 1, 2: 1, 3: 1, other_rot: 2, 6: 2, dof: mat}
        zt = ZL_BASE + tag * 10 + end
        ops.element("zeroLength", zt, attach, nn, "-mat", *[mz[k] for k in (1, 2, 3, 4, 5, 6)], "-dir", 1, 2, 3, 4, 5, 6)
        hinges[zt] = dict(ele=tag, end=end, kind="beam", section="%s link" % sec, dof=dof, K0=K0, mat=mat,
                          node=e["n1"] if end == 1 else e["n2"], z=z, spec=flex, link=info)
    stats["links"] = stats.get("links", 0) + 1
    return mat


def link_shear_spring(e, p1, p2, sec, prm, mat, hinges, stats, tiny=None):
    """NL-03 shear spring only (fibre path): n1 -[zeroLength shear on global Z]- s. Returns (s, mat); the caller runs
    the fibre chain from s to n2 (flexural yielding is distributed in the fibres)."""
    tag = e["tag"]
    L = math.dist(p1, p2)
    shear, flex, info = HM.link_specs(sec, L, prm)
    sn = HN_BASE + tag * 10 + LINK_END
    ops.node(sn, *p1)
    if tiny:
        ops.mass(sn, *([tiny] * 6))
    mat += 1; HM.make_link_shear_material(mat, shear, prm)
    mats = {1: 1, 2: 1, 3: mat, 4: 2, 5: 2, 6: 2}
    zl = ZL_BASE + tag * 10 + LINK_END
    ops.element("zeroLength", zl, e["n1"], sn, "-mat", *[mats[k] for k in (1, 2, 3, 4, 5, 6)], "-dir", 1, 2, 3, 4, 5, 6)
    hinges[zl] = dict(ele=tag, end=0, kind="link", section=sec, dof=3, K0=shear.Ks, mat=mat, node=e["n1"], z=p1[2], spec=shear, link=info)
    stats["links"] = stats.get("links", 0) + 1
    return sn, mat


STEEL_DENSITY_KIP_IN3 = 490.0 / 1000.0 / 1728.0                # 490 pcf


def _pt_mass_balance(pkg, ctx, stats):
    """NL-R2-09: remove the physical-theory braces' own mass from the floor masses (half of each brace to the level of
    each end node that sits on a diaphragm; base ends go to the ground), so the total seismic mass is unchanged.
    Taken from the level's mass-carrying nodes in proportion to their translational mass."""
    items = ctx.get("pt_mass") or []
    if not items:
        return
    lv = levels(pkg)
    zlev = [(z, master, slaves) for k, z, master, slaves in lv]
    take = {}
    for n1, n2, mb in items:
        for n in (n1, n2):
            z = pkg.model.nodes[n][2]
            hit = next(((master, slaves) for zz, master, slaves in zlev if abs(zz - z) < 1.0), None)
            if hit is None:
                continue
            take[hit[0]] = take.get(hit[0], 0.0) + 0.5 * mb
    moved = 0.0
    for master, dm in take.items():
        slaves = next(s for zz, m, s in zlev if m == master)
        carriers = [(t, pkg.model.masses[t][0]) for t in [master] + list(slaves) if pkg.model.masses.get(t) and pkg.model.masses[t][0] > 0]
        tot = sum(m for t, m in carriers)
        if tot <= 0:
            continue
        for t, m in carriers:
            cur = list(ops.nodeMass(t))
            d = min(dm * m / tot, 0.5 * cur[0])
            cur[0] -= d; cur[1] -= d
            ops.mass(t, *cur)
        moved += dm
    stats["pt_brace_mass_kip_s2_in"] = round(moved, 6)
    _note(stats, "physical-theory braces: own mass %.4f kip-s2/in (%.1f kip) placed on the brace nodes and taken off the floor masses"
          % (moved, moved * G_IN))


def finish_stats(stats, ctx, prm, plasticity):
    """Census + degradation disclosure common to both builders (NL-02 / NL-03 / NL-10)."""
    links = ctx.get("links") or {}
    stats["link_census"] = HM.link_census_summary(links)
    for n in ctx.get("link_notes") or []:                       # NL-R2-13: brace-point beam segments kept as beams
        _note(stats, n)
    for v in links.values():
        if v.get("skipped"):
            _note(stats, "link %s (%s): %s" % (v["tag"], v["section"], v["skipped"]))
    if stats["link_census"]["n"]:
        _note(stats, "EBF links (%d): link axial force (AISC 342-22 E2.4c, PUF/Pye > 0.6 -> elastic) is NOT checked" % stats["link_census"]["n"])
    deg = dict(plasticity=plasticity)
    lam_mode = str((HM.param_group(prm, "cyclic_deterioration")[0]).get("mode", "none")).lower()
    if plasticity == "fibre":
        deg["members"] = ("NONE: fibre forceBeamColumn sections are Steel01 with 1% hardening -- no local buckling, no in-cycle or "
                          "cyclic strength degradation, no fracture; the capacity curve has no member-level strength loss, so Vmax, "
                          "Omega and mu_T are upper/lower bounds respectively")
        _note(stats, "FIBRE NSP: member strength degradation is NOT modelled (no local buckling / fracture in the fibres); "
                     "use --plasticity imk for the AISC 342 backbones with post-capping strength loss")
    else:
        deg["members"] = ("IMK hinges: in-cycle post-capping strength loss to c*My per the AISC 342 backbone; cyclic (energy-based) "
                          "deterioration %s" % ("ON (cyclic_deterioration.mode=%s)" % lam_mode if lam_mode not in ("none", "off") else "OFF"))
    nb = stats.get("brace_nonlinear", 0) - stats.get("brb", 0)
    if nb > 0:
        deg["braces"] = ("physical-theory fibre braces with Steel02 + Fatigue: buckling, cyclic degradation and low-cycle-fatigue "
                         "fracture (%d braces)" % stats.get("brace_physical_theory", 0)) if stats.get("brace_physical_theory") else \
            "Hysteretic truss on the Table C3.4 backbone: in-cycle post-buckling loss only, NO cyclic degradation (damage = 0)"
    if stats.get("brb"):
        deg["brb"] = ("BRB (%d): no cyclic degradation modelled -- AISC 342-22 Commentary E3: BRBs 'are expected to withstand significant "
                      "inelastic deformations without strength or stiffness degradation'; strength is lost beyond b (Table C3.3)" % stats["brb"])
    if stats.get("links"):
        deg["links"] = ("EBF link shear springs (%d): Table C2.4 backbone with post-capping loss to c*Vp and loss beyond b; cyclic damage "
                        "per ebf_link.shear.damage1/2" % stats["links"])
    stats["degradation"] = deg
    return stats


def build_nonlinear(pkg, prm, PG, verbose=True, member_nseg=None, plasticity=None,
                    fibre_nip=5, fibre_nf_flange=(8, 4), fibre_nf_web=(16, 2), fibre_residual="none"):
    """Build the nonlinear model. Returns (hinges, stats).

    plasticity: "fibre" (default; distributed forceBeamColumn) or "imk" (concentrated ModIMK /
    optional L2 ConcentratedPlasticity FBC / RBS remesh). Override via arg, numerics.plasticity,
    or SNL_PLASTICITY.
    member_nseg: fibre/IMK member subdivisions (default 4 for fibre, 1 for imk). SNL_MEMBER_NSEG.
    """
    import os
    why = HM.unsupported_system(pkg.basis)                       # NL-R2-13: refuse, never model an STMF as an EBF
    if why:
        raise HM.UnsupportedSystem("NOT EVALUATED -- " + why)
    prm.setdefault("_system", getattr(pkg.basis, "system", None) or "")   # web case of AISC 341-22 Table D1.1b (params_schema)
    num = prm.get("numerics") or {}
    if plasticity is None:
        # CLI/env overrides hinge_params numerics (product ladder may force imk).
        plasticity = os.environ.get("SNL_PLASTICITY") or num.get("plasticity") or "fibre"
    plasticity = str(plasticity).lower()
    if plasticity in ("fiber", "distributed"):
        plasticity = "fibre"
    if member_nseg is None:
        member_nseg = num.get("member_nseg") or os.environ.get("SNL_MEMBER_NSEG")
        if member_nseg is None:
            member_nseg = 4 if plasticity == "fibre" else 1
    member_nseg = int(member_nseg)
    os.environ.setdefault("SNL_PLASTICITY", plasticity)
    os.environ.setdefault("SNL_MEMBER_NSEG", str(member_nseg))
    if plasticity == "fibre":
        from . import fibre_model as FM
        def _pair(env_key, default):
            raw = os.environ.get(env_key)
            if not raw:
                return default
            parts = [int(x.strip()) for x in raw.replace("x", ",").split(",") if x.strip()]
            if len(parts) != 2:
                raise ValueError("%s expects two ints, got %r" % (env_key, raw))
            return tuple(parts)
        nip = int(os.environ.get("SNL_FIBRE_NIP", fibre_nip) or fibre_nip)
        nf_flange = _pair("SNL_FIBRE_NF_FLANGE", fibre_nf_flange)
        nf_web = _pair("SNL_FIBRE_NF_WEB", fibre_nf_web)
        residual = str(os.environ.get("SNL_FIBRE_RESIDUAL", fibre_residual) or fibre_residual)
        # allow numerics overrides
        if num.get("fibre_nip") is not None:
            nip = int(num["fibre_nip"])
        if num.get("fibre_nf_flange"):
            nf_flange = tuple(num["fibre_nf_flange"])
        if num.get("fibre_nf_web"):
            nf_web = tuple(num["fibre_nf_web"])
        if num.get("fibre_residual"):
            residual = str(num["fibre_residual"])
        return FM.build_fibre(pkg, prm, PG, verbose=verbose, nseg=max(1, member_nseg),
                              nip=nip, nf_flange=nf_flange, nf_web=nf_web, residual=residual)
    m = pkg.model
    ops.wipe(); ops.model("basic", "-ndm", m.ndm, "-ndf", m.ndf)
    for t, xyz in m.nodes.items():
        ops.node(t, *xyz)
    for t, fl in m.fixes.items():
        ops.fix(t, *fl)
    tiny = 1e-8 * min((v[0] for v in m.masses.values() if v[0] > 0), default=1.0)   # R2 patch (NL-R2-01b): masters carry explicit 0.0 masses when the CoM node holds the mass
    for t in m.nodes:
        ops.mass(t, *([tiny] * 6))
    for t, mv in m.masses.items():
        ops.mass(t, *[mv[i] + tiny for i in range(6)])
    for t, (ty, vx, vy, vz) in m.transfs.items():
        ops.geomTransf(ty, t, vx, vy, vz)
    for t, a in m.materials.items():                        # recorded materials (e.g. the elastic brace material)
        if t not in (1, 2):
            ops.uniaxialMaterial(*a)
    ops.uniaxialMaterial("Elastic", 1, RIGID_T)
    ops.uniaxialMaterial("Elastic", 2, RIGID_R)
    FBC._SOFT_READY = False  # recreate soft Uniaxial after wipe (L2 FBC)
    hinges = {}          # zl_tag -> dict(ele, end, kind, section, dof, K0, spec) ; braces: tag -> dict(kind='brace', ...)
    mat = MAT_BASE
    stats = dict(col=0, beam=0, brace=0, brace_nonlinear=0, force_controlled=0, released_ends=0,
                 panel_zones=0, panel_zone_mode="rigid", rbs_remesh_beams=0, elastic_ele_tags=[],
                 plasticity="imk", member_nseg=member_nseg)
    pz_mode = HM.panel_zone_mode(prm)
    stats["panel_zone_mode"] = pz_mode
    beam_side = {}       # joint_node -> scissors FR-beam attachment node
    pz_plan = {}
    if pz_mode == "scissors":
        pz_plan = fr_joint_plan(pkg)
        for nj in pz_plan:
            bn = PZ_NODE_BASE + nj
            beam_side[nj] = bn
            ops.node(bn, *m.nodes[nj])
            ops.mass(bn, *([tiny] * 6))
    ctx = element_context(pkg, prm)
    for e in m.elements:
        if "etype" in e:                                    # raw (non-elasticBeamColumn) element
            kind = member_kind(pkg, e); sec = pkg.schedule.get(e["tag"], {}).get("section")
            if e["etype"] in ("Truss", "truss", "corotTruss") and kind == "brace" and sec and str(sec).upper() != "GHOST":
                mat = build_brace(pkg, e, sec, prm, mat, hinges, stats, ctx)
            else:
                ops.element(*e["raw"])
            stats["brace"] += 1; continue
        kind = member_kind(pkg, e)
        sec = pkg.schedule.get(e["tag"], {}).get("section")
        p1, p2 = m.nodes[e["n1"]], m.nodes[e["n2"]]
        d, L = _dir_vec(p1, p2)
        if kind == "brace" and sec:                          # elasticBeamColumn brace (steltic default builder) -> pin-ended nonlinear truss
            mat = build_brace(pkg, e, sec, prm, mat, hinges, stats, ctx)
            stats["brace"] += 1
            continue
        lk = ctx["links"].get(e["tag"])
        if lk and not lk.get("skipped") and kind == "beam" and sec:                 # NL-03 EBF link
            mat = build_link_imk(pkg, e, sec, prm, mat, hinges, stats, ctx, beam_side=beam_side)
            continue
        if sec is None or kind not in ("col", "beam"):
            args = [e["A"], e["E"], e["G"], e["J"], e["Iy"], e["Iz"], e["transf"]] + (e["release"] or [])
            ops.element("elasticBeamColumn", e["tag"], e["n1"], e["n2"], *args); stats["brace"] += 1
            continue
        slot = strong_I_slot(pkg, e, kind); dof = strong_rot_dof(pkg, e, kind)
        I = e[slot]
        rel = e["release"] or []
        # major-axis release code: steltic beams carry the strong axis in the element's Iy slot (-releasey),
        # columns in Iz (-releasez). code 0 none / 1 I-end / 2 J-end / 3 both
        flag = "-releasey" if slot == "Iy" else "-releasez"
        relz = int(rel[rel.index(flag) + 1]) if flag in rel else 0
        hinge_i = relz not in (1, 3); hinge_j = relz not in (2, 3)
        prm_m = prm
        if kind == "col":
            spec = HM.column_hinge(sec, L, PG.get(e["tag"], 0.0), prm)
        else:                                               # per-beam RBS cut and Lb from the package (NL-07)
            prm_m, binfo = beam_params_for(pkg, prm, sec, fr=any(fr_column_ends(pkg, e)))
            spec = HM.beam_hinge(sec, L, prm_m)
            for note in binfo.get("notes", ()):
                if note not in stats.setdefault("beam_detail_notes", []):
                    stats["beam_detail_notes"].append(note)
        if spec.force_controlled:
            hinge_i = hinge_j = False; stats["force_controlled"] += 1
        stats["released_ends"] += (0 if hinge_i else 1) + (0 if hinge_j else 1)
        # L2: ConcentratedPlasticity + forceBeamColumn (gated). Skips ZL / RBS7-ZL path.
        if FBC.want_fbc(prm) and (hinge_i or hinge_j) and not spec.force_controlled:
            mat = FBC.build_fbc_member(
                pkg, e, prm_m, sec, spec, slot, dof, p1, p2, L, kind,
                hinge_i, hinge_j, beam_side, mat, hinges, stats, verbose=verbose,
            )
            continue
        geo = RBS.want_rbs_remesh(kind, sec, hinge_i, hinge_j, prm_m)
        if geo is not None:
            try:
                mat = _build_rbs7_beam(pkg, e, prm_m, sec, spec, slot, dof, p1, p2, L, geo, beam_side, mat, hinges, stats)
                continue
            except Exception as ex:
                # Fall through to single-span path if remesh cannot fit / fails
                stats.setdefault("rbs_remesh_errors", []).append(dict(ele=e["tag"], section=sec, err=str(ex)))
                if verbose:
                    print("[nonlinear_model] RBS 7-seg fallback ele %s (%s): %s" % (e["tag"], sec, ex))
        # interior elastic element between hinge nodes (or original nodes where no hinge)
        ni, nj = e["n1"], e["n2"]
        if hinge_i:
            ni = HN_BASE + e["tag"] * 10 + 1; ops.node(ni, *p1)
        if hinge_j:
            nj = HN_BASE + e["tag"] * 10 + 2; ops.node(nj, *p2)
        args = dict(A=e["A"], E=e["E"], G=e["G"], J=e["J"], Iy=e["Iy"], Iz=e["Iz"])
        if hinge_i or hinge_j:
            args[slot] = I * (N_STIFF + 1.0) / N_STIFF
        ops.element("elasticBeamColumn", e["tag"], ni, nj, args["A"], args["E"], args["G"], args["J"],
                    args["Iy"], args["Iz"], e["transf"], *rel)
        stats.setdefault("elastic_ele_tags", []).append(e["tag"])
        K0 = N_STIFF * 6.0 * e["E"] * I / L
        for end, on, no, nn in ((1, hinge_i, e["n1"], ni), (2, hinge_j, e["n2"], nj)):
            if not on:
                continue
            mat += 1
            HM.make_imk_material(mat, spec, K0, post_cap_ratio=prm.get("numerics", {}).get("post_cap_ratio", 0.15))
            other_rot = 4 if dof == 5 else 5
            mats = {1: 1, 2: 1, 3: 1, other_rot: 2, 6: 2, dof: mat}
            zl = ZL_BASE + e["tag"] * 10 + end
            # Scissors: FR beam ends attach to beam-side node; columns stay on the joint (column continuity).
            attach = beam_side[no] if (kind == "beam" and no in beam_side) else no
            ops.element("zeroLength", zl, attach, nn, "-mat", *[mats[k] for k in (1, 2, 3, 4, 5, 6)], "-dir", 1, 2, 3, 4, 5, 6)
            hinges[zl] = dict(ele=e["tag"], end=end, kind=kind, section=sec, dof=dof, K0=K0, mat=mat,
                              node=no, z=p1[2] if end == 1 else p2[2], spec=spec)
        stats[kind] += 1
    # Scissors panel-zone springs at FR joints (after member hinges, before diaphragms).
    # Use a single 6-DOF zeroLength (rigid on non-PZ DOFs + spring on active rot) — NOT equalDOF.
    # equalDOF(nj, bn, ...) with nj already a rigidDiaphragm slave breaks Transformation modes/push
    # (short spurious T1, collapsed base shear). Beam-side nodes stay out of RD slave lists; they
    # inherit in-plane motion through the rigid translational DOFs of this zeroLength.
    pz_registry = {}
    if pz_mode == "scissors" and pz_plan:
        # Per-joint doubler plates from HR (capacity_design.panel_zone.by_joint[].doubler_in, NL-28) instead of
        # one global panel_zones.doubler_t_in; `panel_zones.doublers = "global"` keeps the old behaviour.
        from . import package_reader as PR
        pz_blk = prm.get("panel_zones") or {}
        pz_records = PR.pz_doublers(pkg) if str(pz_blk.get("doublers", "per_joint")).lower() != "global" else []
        z_level = {round(z, 3): k for k, z, _m, _s in levels(pkg)}
        stats["pz_doublers"] = dict(per_joint=0, global_fallback=0, records=len(pz_records))
        for nj, by_dof in pz_plan.items():
            bn = beam_side[nj]
            active = sorted(by_dof)
            # mats 1=RIGID_T, 2=RIGID_R already defined above
            zl_mats = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2}
            mat_tags, dirs, specs = [], [], []
            for dof in active:
                info = by_dof[dof]
                prm_pz, dbl_note = prm, "hinge_params panel_zones.doubler_t_in (global)"
                if pz_records:
                    lvl = z_level.get(round(m.nodes[nj][2], 3))
                    t_dbl, why = PR.doubler_for_joint(pz_records, lvl, info["col_section"], info["beam_sections"])
                    if t_dbl is not None:
                        prm_pz = dict(prm, panel_zones=dict(pz_blk, doubler_t_in=t_dbl))
                        dbl_note = why; stats["pz_doublers"]["per_joint"] += 1
                    else:
                        dbl_note = why + " -> global panel_zones.doubler_t_in"; stats["pz_doublers"]["global_fallback"] += 1
                pz_spec = HM.panel_zone_spec(nj, dof, info["col_section"], info["beam_sections"], prm_pz)
                pz_spec.flags = tuple(pz_spec.flags) + ("doubler: " + dbl_note,)
                mat += 1
                HM.make_panel_zone_material(mat, pz_spec)
                zl_mats[dof] = mat
                mat_tags.append(mat); dirs.append(dof); specs.append(pz_spec)
            zl = PZ_ZL_BASE + nj
            ops.element("zeroLength", zl, nj, bn,
                        "-mat", *[zl_mats[k] for k in (1, 2, 3, 4, 5, 6)],
                        "-dir", 1, 2, 3, 4, 5, 6)
            pz_registry[zl] = dict(joint=nj, beam_node=bn, dofs=dirs, mats=mat_tags,
                                   specs=[s.as_dict() for s in specs], kind="panel_zone")
            stats["panel_zones"] += 1
    stats["panel_zone_registry"] = pz_registry
    for perp, master, slaves in m.diaphragms:
        ops.rigidDiaphragm(perp, master, *slaves)
    _pt_mass_balance(pkg, ctx, stats)
    finish_stats(stats, ctx, prm, "imk")
    if verbose:
        print("[nonlinear_model] hinges: %d  (cols %d, beams %d, brace elements %d of which nonlinear %d [BRB %d, physical-theory %d], "
              "EBF links %d, force-controlled cols %d, released ends %d, panel_zones %s=%d, rbs7=%d, fbc_cp=%d, elastic_eles=%d)"
              % (len(hinges), stats["col"], stats["beam"], stats["brace"], stats["brace_nonlinear"], stats.get("brb", 0),
                 stats.get("brace_physical_theory", 0), stats.get("links", 0), stats["force_controlled"], stats["released_ends"],
                 stats["panel_zone_mode"], stats["panel_zones"], stats.get("rbs_remesh_beams", 0),
                 stats.get("fbc_cp_members", 0), len(stats.get("elastic_ele_tags") or [])))
        for w in stats.get("model_warnings") or []:
            print("  !! " + w)
    return hinges, stats


def modal_pattern(pkg, direction, nmodes=12, mass_target=0.90, max_modes=96):
    """First translational mode in `direction` ('X'|'Y') from the current (nonlinear, initial-stiffness) model:
    returns (T1, {level_k: F_k normalised to sum 1}, {level_k: phi_k}) using the diaphragm masters.

    NL-R2-12: also returns `modes` -- every computed mode's period, participation factor Gamma_n = L_n / M_n in
    `direction` and level ordinates at the level centres of mass -- for the ASCE 41-23 7.3.2.1 higher-mode test. L_n,
    M_n and the effective mass come from the FULL lumped mass matrix of the model (as nlrha.model.modal, NL-R2-09):
    the eigenvectors are orthogonal with respect to it, and a level-mass-only M_n gives the member-local modes of the
    seeded node masses a spurious, unit participation (seen on a 1-storey portal once more than 3 modes are asked).
    The mode count doubles from `nmodes` (capped by the mass DOFs) until the cumulative effective mass in `direction`
    reaches `mass_target` (90 %) or `max_modes`."""
    dof = 1 if direction.upper() == "X" else 2
    lv = levels(pkg)
    LM = {k: level_mass(pkg, master, s) for k, z, master, s in lv}          # R2 patch (NL-R2-01)
    md = []
    for n in ops.getNodeTags():
        try:
            mv = list(ops.nodeMass(n))
        except Exception:                                                   # noqa: BLE001
            continue
        if any(v > 0.0 for v in mv):
            md.append((n, mv))
    n_mass_dof = sum(1 for n, mv in md for v in mv[:6] if v > 0.0)
    Mtot = sum(mv[dof - 1] for n, mv in md) or sum(LM[k]["m"] for k in LM)
    n_try, modes, eig_err = max(1, min(nmodes, n_mass_dof - 1 if n_mass_dof > 1 else 1)), [], None
    while True:
        ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
        try:
            w2 = ops.eigen("-genBandArpack", n_try)
        except Exception as ex:                                 # more modes than the solver can give: keep the last set
            eig_err = str(ex)[:120]
            if modes:
                break
            raise
        if not w2:
            if modes:
                break
            raise RuntimeError("eigen analysis returned no modes")
        modes = []
        for i, w in enumerate(w2):
            T = 2 * math.pi / math.sqrt(max(w, 1e-12))
            cv = {k: com_eigvec(LM[k], master, i + 1) for k, z, master, s in lv}
            phi = {k: cv[k][dof - 1] for k in cv}
            Mn = Ln = Lp = 0.0
            for n, mv in md:                                    # full mass matrix (all mass-carrying nodes, 6 DOFs)
                ev = ops.nodeEigenvector(n, i + 1)
                Mn += sum(mv[d] * ev[d] * ev[d] for d in range(min(6, len(ev), len(mv))))
                Ln += mv[dof - 1] * ev[dof - 1]; Lp += mv[2 - dof] * ev[2 - dof]
            meff = Ln ** 2 / Mn if Mn > 0 else 0
            modes.append(dict(mode=i + 1, T=T, gamma=(Ln / Mn if Mn > 0 else 0.0), meff=meff, Lp=Lp, Ln=Ln,
                              meff_frac=(meff / Mtot if Mtot > 0 else 0.0), phi=phi))
        cum = sum(m["meff_frac"] for m in modes)
        if cum >= mass_target or n_try >= max_modes or n_try >= n_mass_dof - 1:
            break
        n_try = min(2 * n_try, max_modes, max(1, n_mass_dof - 1))
    best = None
    for m in modes:
        if best is None or (m["meff"] > best[0] and abs(m["Ln"]) > abs(m["Lp"])):
            best = (m["meff"], m["T"], m["phi"], m["mode"])
    meff, T, phi, mode = best
    sgn = 1.0 if phi[max(phi)] >= 0 else -1.0
    phi = {k: sgn * v / abs(phi[max(phi)]) for k, v in phi.items()}         # roof ordinate = +1
    F = {k: LM[k]["m"] * phi[k] for k in LM}
    s = sum(F.values()); F = {k: v / s for k, v in F.items()}
    return dict(T1=T, mode=mode, meff_frac=meff / Mtot, phi=phi, F=F,
                masses={k: LM[k]["m"] for k in LM}, load_node={k: LM[k]["node"] for k in LM},
                modes=[dict(mode=m["mode"], T=m["T"], gamma=m["gamma"], meff_frac=m["meff_frac"], phi=m["phi"]) for m in modes],
                modes_cum_frac=sum(m["meff_frac"] for m in modes), modes_eigen_note=eig_err)


class StaticAnalysis:
    """Owns the OpenSees static-analysis objects of one push and re-issues them ONLY when they change (NL-11).

    Re-issuing `ops.integrator` replaces the integrator without freeing the old one's ndof-sized work vectors
    (measured: 4.5k DOF, 800 steps, 35 -> 192 MB; re-issuing test/algorithm alone is flat). The old loop re-issued
    test + algorithm + integrator on EVERY step, which OOM-killed the Ex8 pushover after 51 min. Here:
      * test / algorithm are issued only when their arguments differ from the current ones;
      * an integrator change (step halving, speed-up, arc-length) wipes the analysis (`ops.wipeAnalysis`, which
        frees every analysis object) and rebuilds constraints/numberer/system/test/algorithm/integrator/analysis,
        so memory stays flat however often the step changes. The domain state (displacements, load factor,
        committed material state) is untouched by wipeAnalysis.
    `issued` counts the calls actually made (the regression test checks it stays bounded)."""

    def __init__(self, constraints=("Transformation",), numberer=("RCM",), system=("UmfPack",)):
        self.base = (tuple(constraints), tuple(numberer), tuple(system))
        self.cur = dict(test=None, algorithm=None, integrator=None)
        self.built = False
        self.issued = dict(test=0, algorithm=0, integrator=0, rebuild=0)

    def set(self, test, algorithm, integrator):
        test, algorithm, integrator = tuple(test), tuple(algorithm), tuple(integrator)
        if self.built and integrator != self.cur["integrator"]:
            ops.wipeAnalysis()
            self.built = False
        if not self.built:
            ops.constraints(*self.base[0]); ops.numberer(*self.base[1]); ops.system(*self.base[2])
            ops.test(*test); ops.algorithm(*algorithm); ops.integrator(*integrator); ops.analysis("Static")
            self.cur = dict(test=test, algorithm=algorithm, integrator=integrator)
            self.built = True
            self.issued["rebuild"] += 1; self.issued["integrator"] += 1
            self.issued["test"] += 1; self.issued["algorithm"] += 1
            return
        if test != self.cur["test"]:
            ops.test(*test); self.cur["test"] = test; self.issued["test"] += 1
        if algorithm != self.cur["algorithm"]:
            ops.algorithm(*algorithm); self.cur["algorithm"] = algorithm; self.issued["algorithm"] += 1

    def wipe(self):
        ops.wipeAnalysis(); self.built = False


ALGOS = (("Newton",), ("ModifiedNewton", "-initial"), ("KrylovNewton",), ("NewtonLineSearch",))


def _try_analyze(an, dU, ctrl, dof, algos=ALGOS):
    """One displacement-controlled step of size dU, trying the algorithms in order (objects reused, NL-11)."""
    integ = ("DisplacementControl", ctrl, dof, dU)
    for i, alg in enumerate(algos):
        an.set(("NormDispIncr", 1e-5, 100 if i == 0 else 40, 0), alg, integ)
        if ops.analyze(1) == 0:
            return True
    return False


class _Progress:
    """NL-R2-14 (user decision: no time limit on the pushover): a progress line -- step, roof drift, V/Vmax, elapsed --
    at most every `every_s` seconds of wall clock (0 = every step), so a long push is visibly alive."""

    def __init__(self, direction, H, every_s=60.0, verbose=True):
        import time as _t
        self._time = _t.time
        self.d, self.H, self.every, self.verbose = direction, H, float(every_s), verbose
        self.t0 = self.last = self._time()
        self.lines = 0

    def elapsed(self):
        e = int(self._time() - self.t0)
        return "%dh%02dm%02ds" % (e // 3600, e % 3600 // 60, e % 60) if e >= 3600 else "%dm%02ds" % (e // 60, e % 60)

    def __call__(self, phase, step, u, V, Vmax, extra="", force=False):
        now = self._time()
        if not self.verbose or (not force and now - self.last < self.every):
            return
        self.last = now; self.lines += 1
        print("[pushover %s %s] step %d  roof u %.2f in (drift %.2f%% H)  V/Vmax %s  elapsed %s%s"
              % (self.d, phase, step, u, 100.0 * u / self.H if self.H else 0.0,
                 ("%.3f" % (V / Vmax)) if Vmax > 0 else "-", self.elapsed(), ("  " + extra) if extra else ""), flush=True)


def _tail_recovery(an, rec, snapshot, roof, dof, dU0, umax, Vmax, strategies, verbose, progress=None):
    """Descending-branch escalation ladder. Called only when the main push stopped on non-convergence
    BEFORE the curve fell to 0.8*Vmax. Each rung restarts from the last converged state:
      fine_step  -- displacement control with dU0/100 and a relaxed tolerance (1e-4, 200 iters)
      arclength  -- cylindrical arc-length integrator (handles the snap-through of the steep drop)
    Returns a dict the bot can act on: needed / tried / captured / status / message."""
    tail = dict(needed=True, tried=[], captured=False, status="lower_bound",
                message="curve did not lose 20% of Vmax; delta_u and mu_T are lower bounds")
    def reached():
        return rec["V"][-1] <= 0.8 * Vmax or rec["u"][-1] >= umax
    tst = ("NormDispIncr", 1e-4, 200, 0)
    for strat in strategies:
        if reached():
            break
        start_u, steps, fails = rec["u"][-1], 0, 0
        if strat == "fine_step":
            dU = dU0 / 100.0
            while not reached() and fails < 6 and steps < 4000:
                integ = ("DisplacementControl", roof, dof, dU)
                an.set(tst, ("KrylovNewton",), integ)
                if ops.analyze(1) != 0:
                    an.set(tst, ("ModifiedNewton", "-initial"), integ)
                    if ops.analyze(1) != 0:
                        fails += 1; dU /= 2.0; continue
                fails = 0; steps += 1; snapshot()
                if progress:
                    progress("tail fine_step", steps, rec["u"][-1], rec["V"][-1], Vmax)
        elif strat == "arclength":
            s_arc = dU0 / 20.0; back = 0
            while not reached() and fails < 6 and steps < 4000 and back < 20:
                integ = ("ArcLength", s_arc, 0.0)
                an.set(tst, ("KrylovNewton",), integ)
                if ops.analyze(1) != 0:
                    an.set(tst, ("ModifiedNewton", "-initial"), integ)
                    if ops.analyze(1) != 0:
                        fails += 1; s_arc /= 2.0; continue
                fails = 0; steps += 1
                u_prev = rec["u"][-1]; snapshot()
                if progress:
                    progress("tail arclength", steps, rec["u"][-1], rec["V"][-1], Vmax)
                back = back + 1 if rec["u"][-1] < u_prev else 0      # arc-length may walk backwards: give up if it keeps doing so
        else:
            continue
        tail["tried"].append(dict(strategy=strat, steps=steps, u_from=round(start_u, 2), u_to=round(rec["u"][-1], 2),
                                  V_end=round(rec["V"][-1]), V_end_over_Vmax=round(rec["V"][-1] / Vmax, 3)))
        if verbose:
            print("[tail %s] %d steps, u %.2f -> %.2f in, V/Vmax %.3f" % (strat, steps, start_u, rec["u"][-1], rec["V"][-1] / Vmax))
    if rec["V"][-1] <= 0.8 * Vmax:
        tail.update(captured=True, status="captured", message="descending branch captured to 0.8*Vmax (delta_u valid)")
    elif rec["u"][-1] >= umax:
        tail.update(status="max_drift", message="reached the max roof drift before losing 20%; raise --max-drift to capture delta_u")
    return tail


def pushover(pkg, hinges, direction, loads, prm, max_roof_drift=0.08, dU0=None, verbose=True, gravity_table=None,
             tail_strategies=("fine_step", "arclength"), progress_s=None):
    """Gravity (load control) then displacement-controlled push at the roof master in `direction`
    with the first-mode force pattern. Records the capacity curve, story displacements and every
    hinge's plastic rotation at each step. Stops at max_roof_drift*H, at 20% strength loss past the
    peak, or when the solver gives up after step-halving -- in which case the descending-branch
    escalation ladder (`tail_strategies`, in order) is tried before giving up. Pass () to disable.

    The analysis objects are created once and changed only when the step or algorithm changes
    (StaticAnalysis, NL-11). `run["n_moment_frame_members"]` and `run["monitored"]` let the acceptance
    check refuse to call a frame acceptable when none of its moment-frame members was evaluated (NL-01).

    NL-R2-14: no time limit (user decision); a progress line (step, roof drift, V/Vmax, elapsed) is printed at most
    every `progress_s` seconds (default numerics.progress_interval_s, else 60 s; 0 = every step) when verbose."""
    t_entry = time.time()
    ok = _apply_gravity(loads)
    if ok != 0:
        raise RuntimeError("gravity stage failed in nonlinear model (%d)" % ok)
    ops.wipeAnalysis()                                      # free the gravity analysis before the eigen solve
    pat = modal_pattern(pkg, direction)
    lv = levels(pkg); dof = 1 if direction.upper() == "X" else 2
    roof = lv[-1][2]; H = lv[-1][1]
    dU0 = dU0 or H / 1500.0
    ops.timeSeries("Linear", 2); ops.pattern("Plain", 2, 2)
    for k, z, master, s in lv:
        f = [0.0] * 6; f[dof - 1] = pat["F"][k]
        ops.load(pat.get("load_node", {}).get(k, master), *f)       # R2 patch: at the level CoM (ASCE 41 7.4.3.2.3)
    ops.wipeAnalysis()                                      # drop the gravity / eigen analysis objects
    an = StaticAnalysis()
    an.set(("NormDispIncr", 1e-5, 100, 0), ("Newton",), ("DisplacementControl", roof, dof, dU0))
    fixed = [t for t, fl in pkg.model.fixes.items() if fl[dof - 1] == 1]
    cols = [e["tag"] for e in pkg.model.elements if "etype" not in e and member_kind(pkg, e) == "col"]
    rec = dict(u=[], V=[], story_u=[], hinge_pl=[], hinge_M=[], col_N=[])
    hz = sorted(hinges)
    B = [max(hinges[t]["spec"].b_pl, 1e-9) for t in hz]
    A = [max(hinges[t]["spec"].a_pl, 1e-9) for t in hz]
    grav = [hinges[t]["kind"] != "brace" for t in hz]      # only gravity-carrying members define the b (collapse) limit
    rec["b_ratio"] = []; rec["a_ratio"] = []; rec["brace_b_ratio"] = []
    def snapshot():
        ops.reactions()
        V = -sum(ops.nodeReaction(t, dof) for t in fixed)
        rec["u"].append(ops.nodeDisp(roof, dof)); rec["V"].append(V)
        rec["story_u"].append([ops.nodeDisp(m, dof) for k, z, m, s in lv])
        pl, MM = [], []
        for t in hz:
            v, M = hinge_plastic_deformation(t, hinges[t])
            pl.append(v); MM.append(M)
        rec["hinge_pl"].append(pl); rec["hinge_M"].append(MM)
        rec["b_ratio"].append(max([abs(pl[i]) / B[i] for i in range(len(pl)) if grav[i]] or [0.0]))
        rec["a_ratio"].append(max([abs(pl[i]) / A[i] for i in range(len(pl)) if grav[i]] or [0.0]))
        rec["brace_b_ratio"].append(max([abs(pl[i]) / B[i] for i in range(len(pl)) if not grav[i]] or [0.0]))
        rec["col_N"].append([ops.eleResponse(c, "localForce")[0] for c in cols])
    snapshot()
    dU, umax, Vmax, halvings, step = dU0, max_roof_drift * H, 0.0, 0, 0
    if progress_s is None:
        progress_s = float((prm.get("numerics") or {}).get("progress_interval_s", 60.0))
    progress = _Progress(direction, H, progress_s, verbose)
    progress.t0 = t_entry                                       # elapsed counts gravity + eigen too
    stop_reason = "reached max roof drift %.1f%% of H" % (100 * max_roof_drift)
    while rec["u"][-1] < umax * (1.0 - 1e-6):                  # relative tolerance: a step landing on the cap within round-off stops there
        if not _try_analyze(an, dU, roof, dof):
            halvings += 1; dU /= 2.0
            progress("push", step, rec["u"][-1], rec["V"][-1], Vmax, "not converged: step halved (%d), dU %.3g in" % (halvings, dU))
            if halvings > 8:
                stop_reason = "solver non-convergence at roof u=%.2f in (after %d step halvings)" % (rec["u"][-1], halvings)
                break
            continue
        step += 1
        snapshot()
        Vmax = max(Vmax, rec["V"][-1])
        progress("push", step, rec["u"][-1], rec["V"][-1], Vmax)
        if rec["V"][-1] < 0.2 * Vmax and rec["u"][-1] > 0.3 * umax:
            stop_reason = "strength dropped below 20%% of Vmax at u=%.2f in" % rec["u"][-1]; break
        if halvings and step % 20 == 0 and dU < dU0:
            dU *= 2.0                                        # try to speed back up
    if verbose:
        print("[pushover %s] T1=%.3fs (mode %d, %.0f%% mass) steps=%d Vmax=%.0f kip u_end=%.2f in  -- %s  [analysis objects: %s] elapsed %s"
              % (direction, pat["T1"], pat["mode"], 100 * pat["meff_frac"], step, Vmax, rec["u"][-1], stop_reason, an.issued,
                 progress.elapsed()), flush=True)
    captured_main = rec["V"][-1] <= 0.8 * Vmax
    # NL-22: "captured" only when the main push itself fell to 0.8 Vmax; a stop above 0.8 Vmax is never "captured"
    tail = dict(needed=not captured_main, tried=[], captured=captured_main,
                status="captured" if captured_main else "lower_bound",
                message="the main push fell to 0.8*Vmax (delta_u valid)" if captured_main else
                        "curve did not lose 20% of Vmax; delta_u and mu_T are lower bounds")
    if rec["b_ratio"] and rec["b_ratio"][-1] >= 0.95 and rec["V"][-1] > 0.8 * Vmax:
        # hinges have reached rotation b (loss of gravity-load capacity): a NON-SIMULATED collapse point.
        # FEMA P-695 takes delta_u at the earlier of 20% strength loss and such a point, so this is a valid end.
        i_b = next(i for i, r in enumerate(rec["b_ratio"]) if r >= 0.95)
        tail = dict(needed=False, tried=[], captured=True, status="component_limit", u_component_limit=rec["u"][i_b],
                    message="hinge plastic rotation reached b (loss of gravity capacity) at roof u=%.2f in before 20%% strength loss; "
                            "delta_u taken there (P-695 non-simulated collapse rule) -- not a solver problem" % rec["u"][i_b])
        stop_reason += "; component rotation limit b reached (max theta_pl/b = %.2f)" % rec["b_ratio"][-1]
    elif stop_reason.startswith("solver") and not captured_main:
        if tail_strategies:
            tail = _tail_recovery(an, rec, snapshot, roof, dof, dU0, umax, Vmax, tail_strategies, verbose, progress)
            Vmax = max(Vmax, max(rec["V"]))
            if tail["captured"]:
                stop_reason += "; descending branch recovered by %s" % "+".join(t["strategy"] for t in tail["tried"])
        else:
            tail["message"] = ("solver stopped above 0.8*Vmax and the descending-branch escalation is disabled (--tail none); "
                               "delta_u and mu_T are lower bounds")
    elif stop_reason.startswith("reached max") and not captured_main:
        tail = dict(needed=True, tried=[], captured=False, status="max_drift",
                    message="reached the max roof drift before losing 20%; raise --max-drift to capture delta_u")
    kinds = {}
    for t in hz:
        kinds[hinges[t]["kind"]] = kinds.get(hinges[t]["kind"], 0) + 1
    ops.wipeAnalysis()
    if verbose:
        print("[pushover %s] done: %d steps, roof u %.2f in, elapsed %s" % (direction, len(rec["u"]) - 1, rec["u"][-1], progress.elapsed()), flush=True)
    return dict(direction=direction, H=H, col_tags=cols, tail=tail, elapsed_s=round(time.time() - progress.t0, 1),
                gravity_table_QG=[r["QG_kip"] for r in (gravity_table or [])], heights=[lv[0][1]] + [lv[i][1] - lv[i - 1][1] for i in range(1, len(lv))],
                pattern=pat, rec=rec, hinge_tags=hz, stop_reason=stop_reason, Vmax=Vmax, roof_node=roof,
                n_moment_frame_members=moment_frame_members(pkg), monitored=kinds, analysis_objects=dict(an.issued))
