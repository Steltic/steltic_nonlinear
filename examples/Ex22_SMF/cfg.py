"""Ex22_SMF gold cfg: 6-story hospital SMF, RISK CATEGORY IV (Ie=1.5, drift limit 0.010hsx).
Regular 6x4 bays @ 360 in; heights [192]+[168]*5. Perimeter SMF all faces; penthouse as mass.
SMF bases FIXED; gravity pinned. rho=1.0 DEMONSTRATED per 12.3.4.2 (>=2 moment bays each
side of CM at every story, loss of one connection < 33% strength, no extreme torsion)."""
import openseespy.opensees as ops
import engine3d as eng

NX, NY = 6, 4
SX = SY = 360.0
HEIGHTS = [192.0] + [168.0] * 5
NF = 6
SMF_COL = {1: "W14X730", 2: "W14X730", 3: "W14X605", 4: "W14X605", 5: "W14X500", 6: "W14X500"}
GRAV_COL = {1: "W14X193", 2: "W14X193", 3: "W14X145", 4: "W14X145", 5: "W14X90", 6: "W14X90"}
def SMF_BEAM(k, ydir=False):
    if ydir:
        return "W40X277" if k <= 3 else ("W36X256" if k <= 5 else "W36X232")
    return "W36X232" if k <= 3 else ("W36X194" if k <= 5 else "W33X141")
def GBEAM(k):
    return "W24X76" if k == 6 else "W27X94"

def custom_build(cfg, transf="PDelta"):
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    z = [0.0]
    for h in HEIGHTS: z.append(z[-1] + h)
    present = {k: {(i, j) for i in range(NX + 1) for j in range(NY + 1)} for k in range(NF + 1)}
    XY = lambda i, j: (i * SX, j * SY)
    smf_pos = {(i, j) for i in range(NX + 1) for j in range(NY + 1) if i in (0, NX) or j in (0, NY)}
    for k in range(NF + 1):
        for (i, j) in present[k]:
            x, y = XY(i, j); ops.node(eng.ntag(i, j, k), x, y, z[k])
    for (i, j) in present[0]:
        if (i, j) in smf_pos: ops.fix(eng.ntag(i, j, 0), 1, 1, 1, 1, 1, 1)
        else:                 ops.fix(eng.ntag(i, j, 0), 1, 1, 1, 0, 0, 0)
    cm = {}
    for k in range(1, NF + 1):
        pts = present[k]
        cx = sum(XY(i, j)[0] for i, j in pts) / len(pts)
        cy = sum(XY(i, j)[1] for i, j in pts) / len(pts)
        cm[k] = (cx, cy); ops.node(eng.mtag(k), cx, cy, z[k]); ops.fix(eng.mtag(k), 0, 0, 1, 1, 1, 0)
    et = 1; eles = []
    for i in range(NX + 1):
        for j in range(NY + 1):
            for k in range(NF):
                if (i, j) in smf_pos:
                    sec = SMF_COL[k + 1]
                    sd = "X" if j in (0, NY) else "Y"
                else:
                    sec = GRAV_COL[k + 1]; sd = "X"
                eng.add_column(et, eng.ntag(i, j, k), eng.ntag(i, j, k + 1), sec, sd)
                eles.append((et, "col", sec, eng.ntag(i, j, k), eng.ntag(i, j, k + 1))); et += 1
    for k in range(1, NF + 1):
        for j in range(NY + 1):
            for i in range(NX):
                sec = SMF_BEAM(k) if j in (0, NY) else GBEAM(k)
                rel = None if j in (0, NY) else ("both", "none")
                eng.add_beam(et, eng.ntag(i, j, k), eng.ntag(i + 1, j, k), sec, releases=rel)
                eles.append((et, "beam", sec, eng.ntag(i, j, k), eng.ntag(i + 1, j, k))); et += 1
        for i in range(NX + 1):
            for j in range(NY):
                sec = SMF_BEAM(k, ydir=True) if i in (0, NX) else GBEAM(k)
                rel = None if i in (0, NX) else ("both", "none")
                eng.add_beam(et, eng.ntag(i, j, k), eng.ntag(i, j + 1, k), sec, releases=rel)
                eles.append((et, "beam", sec, eng.ntag(i, j, k), eng.ntag(i, j + 1, k))); et += 1
    info = {"cm": cm, "present": present, "z": z, "NF": NF, "ele": eles}
    for k in range(1, NF + 1):
        sl = [eng.ntag(i, j, k) for (i, j) in present[k]]
        ops.rigidDiaphragm(3, eng.mtag(k), *sl)
        w = eng.floor_w(cfg, k); m = w / eng.g
        Bx, By = NX * SX + SX, NY * SY + SY
        ops.mass(eng.mtag(k), m, m, 0.0, 0.0, 0.0, m * (Bx ** 2 + By ** 2) / 12.0)
    return info

cfg = dict(
    arch="Ex22: 6-story hospital SMF, Risk Category IV (gold)",
    NX=NX, NY=NY, SX=SX, SY=SY, heights=list(HEIGHTS),
    base="mixed", col="W14X605", beam="W27X94",
    system="SMF",
    model={"bases": "mixed", "joints": "mixed", "gravity": "framed"},
    seis=dict(SDS=1.00, SD1=0.60, S1=0.60, R=8.0, Cd=5.5, Om0=3.0,
              Ct=0.028, x=0.8, Cu=1.4, Ie=1.5, TL=8.0),
    rho=1.0,
    analyses=["ELF", "RS"],
    wind=dict(V=120.0, exposure="C", Kd=0.85, Kzt=1.0, G=0.85, Cpnet=1.3),
    D_floor=95.0, D_roof=95.0, clad=20.0, L_floor=90.0, Lr=20.0,
    extra_mass_floors={6: 10.0},
    drift_limit=0.010, floor_system="one-way", torsion_check=True,
    custom_build=custom_build,
)
