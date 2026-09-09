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
numerics.element_form. Fibre path drops scissors PZ (rigid) and skips RBS remesh.
"""
from __future__ import annotations
import math
import openseespy.opensees as ops
from . import hinge_models as HM
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


def gravity_loads(pkg, prm, verbose=True):
    """1.1*(QD + 0.25*QL) per level (ASCE 41 7.2.2 form), QD from the recorded seismic mass (D + cladding),
    QL from cfg L_floor psf x level footprint area; spread equally over that level's column nodes.
    Returns {node: Pz_kip (negative = down)}, plus a per-level table for the report."""
    lv = levels(pkg)
    Lpsf = pkg.basis.L_floor_psf if pkg.basis.L_floor_psf is not None else 50.0
    Lroof = 20.0
    table, loads = [], {}
    for k, z, master, slaves in lv:
        m = pkg.model.masses.get(master, [0] * 6)[0]
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


def build_nonlinear(pkg, prm, PG, verbose=True, member_nseg=None, plasticity=None,
                    fibre_nip=5, fibre_nf_flange=(8, 4), fibre_nf_web=(16, 2), fibre_residual="none"):
    """Build the nonlinear model. Returns (hinges, stats).

    plasticity: "fibre" (default; distributed forceBeamColumn) or "imk" (concentrated ModIMK /
    optional L2 ConcentratedPlasticity FBC / RBS remesh). Override via arg, numerics.plasticity,
    or SNL_PLASTICITY.
    member_nseg: fibre/IMK member subdivisions (default 4 for fibre, 1 for imk). SNL_MEMBER_NSEG.
    """
    import os
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
    tiny = 1e-8 * min(v[0] for v in m.masses.values())
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
    for e in m.elements:
        if "etype" in e:                                    # raw (non-elasticBeamColumn) element
            kind = member_kind(pkg, e); sec = pkg.schedule.get(e["tag"], {}).get("section")
            if e["etype"] in ("Truss", "truss", "corotTruss") and kind == "brace" and sec and str(sec).upper() != "GHOST":
                p1, p2 = m.nodes[e["n1"]], m.nodes[e["n2"]]; _, L = _dir_vec(p1, p2)
                spec = HM.brace_spec(sec, L, prm)
                mat += 1; HM.make_brace_material(mat, spec, prm)
                ops.element("corotTruss", e["tag"], e["n1"], e["n2"], spec.A, mat)
                hinges[e["tag"]] = dict(ele=e["tag"], end=0, kind="brace", section=sec, dof=0, K0=E_KSI_AL(spec), mat=mat,
                                        node=e["n1"], z=max(p1[2], p2[2]), spec=spec)
                stats["brace_nonlinear"] += 1
            else:
                ops.element(*e["raw"])
            stats["brace"] += 1; continue
        kind = member_kind(pkg, e)
        sec = pkg.schedule.get(e["tag"], {}).get("section")
        p1, p2 = m.nodes[e["n1"]], m.nodes[e["n2"]]
        d, L = _dir_vec(p1, p2)
        if kind == "brace" and sec:                          # elasticBeamColumn brace (steltic default builder) -> pin-ended nonlinear truss
            spec = HM.brace_spec(sec, L, prm)
            mat += 1; HM.make_brace_material(mat, spec, prm)
            ops.element("corotTruss", e["tag"], e["n1"], e["n2"], spec.A, mat)
            hinges[e["tag"]] = dict(ele=e["tag"], end=0, kind="brace", section=sec, dof=0, K0=E_KSI_AL(spec), mat=mat,
                                    node=e["n1"], z=max(p1[2], p2[2]), spec=spec)
            stats["brace"] += 1; stats["brace_nonlinear"] += 1
            continue
        if sec is None or kind not in ("col", "beam"):
            args = [e["A"], e["E"], e["G"], e["J"], e["Iy"], e["Iz"], e["transf"]] + (e["release"] or [])
            ops.element("elasticBeamColumn", e["tag"], e["n1"], e["n2"], *args); stats["brace"] += 1
            continue
        spec = HM.column_hinge(sec, L, PG.get(e["tag"], 0.0), prm) if kind == "col" else HM.beam_hinge(sec, L, prm)
        slot = strong_I_slot(pkg, e, kind); dof = strong_rot_dof(pkg, e, kind)
        I = e[slot]
        rel = e["release"] or []
        # major-axis release code: steltic beams carry the strong axis in the element's Iy slot (-releasey),
        # columns in Iz (-releasez). code 0 none / 1 I-end / 2 J-end / 3 both
        flag = "-releasey" if slot == "Iy" else "-releasez"
        relz = int(rel[rel.index(flag) + 1]) if flag in rel else 0
        hinge_i = relz not in (1, 3); hinge_j = relz not in (2, 3)
        if spec.force_controlled:
            hinge_i = hinge_j = False; stats["force_controlled"] += 1
        stats["released_ends"] += (0 if hinge_i else 1) + (0 if hinge_j else 1)
        # L2: ConcentratedPlasticity + forceBeamColumn (gated). Skips ZL / RBS7-ZL path.
        if FBC.want_fbc(prm) and (hinge_i or hinge_j) and not spec.force_controlled:
            mat = FBC.build_fbc_member(
                pkg, e, prm, sec, spec, slot, dof, p1, p2, L, kind,
                hinge_i, hinge_j, beam_side, mat, hinges, stats, verbose=verbose,
            )
            continue
        geo = RBS.want_rbs_remesh(kind, sec, hinge_i, hinge_j, prm)
        if geo is not None:
            try:
                mat = _build_rbs7_beam(pkg, e, prm, sec, spec, slot, dof, p1, p2, L, geo, beam_side, mat, hinges, stats)
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
        for nj, by_dof in pz_plan.items():
            bn = beam_side[nj]
            active = sorted(by_dof)
            # mats 1=RIGID_T, 2=RIGID_R already defined above
            zl_mats = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2}
            mat_tags, dirs, specs = [], [], []
            for dof in active:
                info = by_dof[dof]
                pz_spec = HM.panel_zone_spec(nj, dof, info["col_section"], info["beam_sections"], prm)
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
    if verbose:
        print("[nonlinear_model] hinges: %d  (cols %d, beams %d, brace elements %d of which nonlinear %d, force-controlled cols %d, released ends %d, panel_zones %s=%d, rbs7=%d, fbc_cp=%d, elastic_eles=%d)"
              % (len(hinges), stats["col"], stats["beam"], stats["brace"], stats["brace_nonlinear"], stats["force_controlled"], stats["released_ends"],
                 stats["panel_zone_mode"], stats["panel_zones"], stats.get("rbs_remesh_beams", 0),
                 stats.get("fbc_cp_members", 0), len(stats.get("elastic_ele_tags") or [])))
    return hinges, stats


def modal_pattern(pkg, direction, nmodes=6):
    """First translational mode in `direction` ('X'|'Y') from the current (nonlinear, initial-stiffness) model:
    returns (T1, {level_k: F_k normalised to sum 1}, {level_k: phi_k}) using the diaphragm masters."""
    dof = 1 if direction.upper() == "X" else 2
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    w2 = ops.eigen("-genBandArpack", nmodes)
    lv = levels(pkg)
    best = None
    for i, w in enumerate(w2):
        T = 2 * math.pi / math.sqrt(max(w, 1e-12))
        phi = {k: ops.nodeEigenvector(master, i + 1, dof) for k, z, master, s in lv}
        perp = {k: ops.nodeEigenvector(master, i + 1, 3 - dof) for k, z, master, s in lv}
        rot = {k: ops.nodeEigenvector(master, i + 1, 6) for k, z, master, s in lv}
        mk = {k: pkg.model.masses[master][0] for k, z, master, s in lv}; Jk = {k: pkg.model.masses[master][5] for k, z, master, s in lv}
        Ln = sum(mk[k] * phi[k] for k in phi)
        Mn = sum(mk[k] * (phi[k] ** 2 + perp[k] ** 2) + Jk[k] * rot[k] ** 2 for k in phi)     # full generalised mass
        Lp = sum(mk[k] * perp[k] for k in perp)
        meff = Ln ** 2 / Mn if Mn > 0 else 0
        if best is None or (meff > best[0] and abs(Ln) > abs(Lp)):
            best = (meff, T, phi, i + 1)
    meff, T, phi, mode = best
    sgn = 1.0 if phi[max(phi)] >= 0 else -1.0
    phi = {k: sgn * v / abs(phi[max(phi)]) for k, v in phi.items()}         # roof ordinate = +1
    F = {k: pkg.model.masses[m][0] * phi[k] for k, z, m, s in lv}
    s = sum(F.values()); F = {k: v / s for k, v in F.items()}
    Mtot = sum(pkg.model.masses[m][0] for k, z, m, s in lv)
    return dict(T1=T, mode=mode, meff_frac=meff / Mtot, phi=phi, F=F,
                masses={k: pkg.model.masses[m][0] for k, z, m, s in lv})


def _try_analyze(dU, ctrl, dof, algos=(("Newton",), ("ModifiedNewton", "-initial"), ("KrylovNewton",), ("NewtonLineSearch",))):
    for i, alg in enumerate(algos):
        ops.test("NormDispIncr", 1e-5, 100 if i == 0 else 40, 0)
        ops.algorithm(*alg)
        ops.integrator("DisplacementControl", ctrl, dof, dU)
        if ops.analyze(1) == 0:
            return True
    return False


def _tail_recovery(rec, snapshot, roof, dof, dU0, umax, Vmax, strategies, verbose):
    """Descending-branch escalation ladder. Called only when the main push stopped on non-convergence
    BEFORE the curve fell to 0.8*Vmax. Each rung restarts from the last converged state:
      fine_step  -- displacement control with dU0/100 and a relaxed tolerance (1e-4, 200 iters)
      arclength  -- cylindrical arc-length integrator (handles the snap-through of the steep drop)
    Returns a dict the bot can act on: needed / tried / captured / status / message."""
    tail = dict(needed=True, tried=[], captured=False, status="lower_bound",
                message="curve did not lose 20% of Vmax; delta_u and mu_T are lower bounds")
    def reached():
        return rec["V"][-1] <= 0.8 * Vmax or rec["u"][-1] >= umax
    for strat in strategies:
        if reached():
            break
        start_u, steps, fails = rec["u"][-1], 0, 0
        if strat == "fine_step":
            dU = dU0 / 100.0
            while not reached() and fails < 6 and steps < 4000:
                ops.test("NormDispIncr", 1e-4, 200, 0); ops.algorithm("KrylovNewton")
                ops.integrator("DisplacementControl", roof, dof, dU)
                if ops.analyze(1) != 0:
                    ops.algorithm("ModifiedNewton", "-initial")
                    if ops.analyze(1) != 0:
                        fails += 1; dU /= 2.0; continue
                fails = 0; steps += 1; snapshot()
        elif strat == "arclength":
            s_arc = dU0 / 20.0; back = 0
            while not reached() and fails < 6 and steps < 4000 and back < 20:
                ops.test("NormDispIncr", 1e-4, 200, 0); ops.algorithm("KrylovNewton")
                ops.integrator("ArcLength", s_arc, 0.0)
                if ops.analyze(1) != 0:
                    ops.algorithm("ModifiedNewton", "-initial")
                    if ops.analyze(1) != 0:
                        fails += 1; s_arc /= 2.0; continue
                fails = 0; steps += 1
                u_prev = rec["u"][-1]; snapshot()
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
             tail_strategies=("fine_step", "arclength")):
    """Gravity (load control) then displacement-controlled push at the roof master in `direction`
    with the first-mode force pattern. Records the capacity curve, story displacements and every
    hinge's plastic rotation at each step. Stops at max_roof_drift*H, at 20% strength loss past the
    peak, or when the solver gives up after step-halving -- in which case the descending-branch
    escalation ladder (`tail_strategies`, in order) is tried before giving up. Pass () to disable."""
    ok = _apply_gravity(loads)
    if ok != 0:
        raise RuntimeError("gravity stage failed in nonlinear model (%d)" % ok)
    pat = modal_pattern(pkg, direction)
    lv = levels(pkg); dof = 1 if direction.upper() == "X" else 2
    roof = lv[-1][2]; H = lv[-1][1]
    dU0 = dU0 or H / 1500.0
    ops.timeSeries("Linear", 2); ops.pattern("Plain", 2, 2)
    for k, z, master, s in lv:
        f = [0.0] * 6; f[dof - 1] = pat["F"][k]
        ops.load(master, *f)
    ops.constraints("Transformation"); ops.numberer("RCM"); ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-5, 100, 0); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", roof, dof, dU0); ops.analysis("Static")
    fixed = [t for t, fl in pkg.model.fixes.items() if fl[dof - 1] == 1]
    cols = [e["tag"] for e in pkg.model.elements if "etype" not in e and member_kind(pkg, e) == "col"]
    rec = dict(u=[], V=[], story_u=[], hinge_pl=[], hinge_M=[], col_N=[])
    hz = sorted(hinges)
    K0 = [hinges[t]["K0"] for t in hz]
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
        for i, t in enumerate(hz):
            if hinges[t]["kind"] == "brace":
                d = ops.eleResponse(t, "deformation"); f = ops.eleResponse(t, "axialForce")
                pl.append(d[0] if d else 0.0); MM.append(f[0] if f else 0.0)     # TOTAL axial deformation (in); limits are total-deformation multiples
                continue
            h = hinges[t]
            if h.get("form") == "fbc_cp":
                ip = h.get("sec_ip", 1); jc = h.get("sec_comp", 0)
                d = ops.eleResponse(h["ele"], "section", ip, "deformation")
                f = ops.eleResponse(h["ele"], "section", ip, "force")
                th = d[jc] if (d and len(d) > jc) else 0.0
                M = f[jc] if (f and len(f) > jc) else 0.0
                pl.append(th - M / K0[i]); MM.append(M)
                continue
            d = ops.eleResponse(t, "deformation"); f = ops.eleResponse(t, "force")
            j = h["dof"] - 1
            th = d[j] if len(d) >= 6 else 0.0; M = f[j] if len(f) >= 6 else 0.0
            pl.append(th - M / K0[i]); MM.append(M)
        rec["hinge_pl"].append(pl); rec["hinge_M"].append(MM)
        rec["b_ratio"].append(max([abs(pl[i]) / B[i] for i in range(len(pl)) if grav[i]] or [0.0]))
        rec["a_ratio"].append(max([abs(pl[i]) / A[i] for i in range(len(pl)) if grav[i]] or [0.0]))
        rec["brace_b_ratio"].append(max([abs(pl[i]) / B[i] for i in range(len(pl)) if not grav[i]] or [0.0]))
        rec["col_N"].append([ops.eleResponse(c, "localForce")[0] for c in cols])
    snapshot()
    dU, umax, Vmax, halvings, step = dU0, max_roof_drift * H, 0.0, 0, 0
    stop_reason = "reached max roof drift %.1f%% of H" % (100 * max_roof_drift)
    while rec["u"][-1] < umax:
        if not _try_analyze(dU, roof, dof):
            halvings += 1; dU /= 2.0
            if halvings > 8:
                stop_reason = "solver non-convergence at roof u=%.2f in (after %d step halvings)" % (rec["u"][-1], halvings)
                break
            continue
        step += 1
        snapshot()
        Vmax = max(Vmax, rec["V"][-1])
        if rec["V"][-1] < 0.2 * Vmax and rec["u"][-1] > 0.3 * umax:
            stop_reason = "strength dropped below 20%% of Vmax at u=%.2f in" % rec["u"][-1]; break
        if halvings and step % 20 == 0 and dU < dU0:
            dU *= 2.0                                        # try to speed back up
    if verbose:
        print("[pushover %s] T1=%.3fs (mode %d, %.0f%% mass) steps=%d Vmax=%.0f kip u_end=%.2f in  -- %s"
              % (direction, pat["T1"], pat["mode"], 100 * pat["meff_frac"], step, Vmax, rec["u"][-1], stop_reason))
    tail = dict(needed=False, tried=[], captured=rec["V"][-1] <= 0.8 * Vmax, status="captured" if rec["V"][-1] <= 0.8 * Vmax else "not_needed",
                message="")
    if rec["b_ratio"] and rec["b_ratio"][-1] >= 0.95 and rec["V"][-1] > 0.8 * Vmax:
        # hinges have reached rotation b (loss of gravity-load capacity): a NON-SIMULATED collapse point.
        # FEMA P-695 takes delta_u at the earlier of 20% strength loss and such a point, so this is a valid end.
        i_b = next(i for i, r in enumerate(rec["b_ratio"]) if r >= 0.95)
        tail = dict(needed=False, tried=[], captured=True, status="component_limit", u_component_limit=rec["u"][i_b],
                    message="hinge plastic rotation reached b (loss of gravity capacity) at roof u=%.2f in before 20%% strength loss; "
                            "delta_u taken there (P-695 non-simulated collapse rule) -- not a solver problem" % rec["u"][i_b])
        stop_reason += "; component rotation limit b reached (max theta_pl/b = %.2f)" % rec["b_ratio"][-1]
    elif stop_reason.startswith("solver") and rec["V"][-1] > 0.8 * Vmax and tail_strategies:
        tail = _tail_recovery(rec, snapshot, roof, dof, dU0, umax, Vmax, tail_strategies, verbose)
        Vmax = max(Vmax, max(rec["V"]))
        if tail["captured"]:
            stop_reason += "; descending branch recovered by %s" % "+".join(t["strategy"] for t in tail["tried"])
    elif stop_reason.startswith("reached max") and rec["V"][-1] > 0.8 * Vmax:
        tail = dict(needed=True, tried=[], captured=False, status="max_drift",
                    message="reached the max roof drift before losing 20%; raise --max-drift to capture delta_u")
    return dict(direction=direction, H=H, col_tags=cols, tail=tail,
                gravity_table_QG=[r["QG_kip"] for r in (gravity_table or [])], heights=[lv[0][1]] + [lv[i][1] - lv[i - 1][1] for i in range(1, len(lv))],
                pattern=pat, rec=rec, hinge_tags=hz, stop_reason=stop_reason, Vmax=Vmax, roof_node=roof)
