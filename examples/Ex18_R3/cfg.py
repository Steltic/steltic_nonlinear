"""Ex18_R3 gold cfg: 8-story R=3 conventional steel (AISC 360 only), perimeter X-braced bays,
regular rectangle, low seismic / wind-governed. 5x4 bays @ 360/300 in; heights [180]+[156]*7.
X-braced bays modeled with both diagonals. All bases pinned, all connections pinned."""
import openseespy.opensees as ops
import engine3d as eng

NX, NY = 5, 4
SX, SY = 360.0, 300.0
HEIGHTS = [180.0] + [156.0] * 7
NF = 8
FRAMES = [("X", 0, 1, 8), ("X", 4, 3, 8), ("Y", 0, 1, 8), ("Y", 5, 2, 8)]
def BR(k):
    return "HSS8X8X1/2" if k <= 4 else "HSS6X6X3/8"
COL_BR = {1: "W14X159", 2: "W14X159", 3: "W14X120", 4: "W14X120", 5: "W14X82", 6: "W14X82",
          7: "W14X61", 8: "W14X61"}
COL_GR = {1: "W14X109", 2: "W14X109", 3: "W14X99", 4: "W14X99", 5: "W14X82", 6: "W14X82",
          7: "W14X61", 8: "W14X61"}
def GBEAM(k):
    return "W24X62" if k == 8 else "W24X84"

def custom_build(cfg, transf="PDelta"):
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    z = [0.0]
    for h in HEIGHTS: z.append(z[-1] + h)
    present = {k: {(i, j) for i in range(NX + 1) for j in range(NY + 1)} for k in range(NF + 1)}
    XY = lambda i, j: (i * SX, j * SY)
    br_pos = {}
    for dirn, fx, b0, top in FRAMES:
        a = (fx, b0) if dirn == "Y" else (b0, fx)
        b = (fx, b0 + 1) if dirn == "Y" else (b0 + 1, fx)
        for p in (a, b):
            br_pos.setdefault(p, []).append(dirn)
    for k in range(NF + 1):
        for (i, j) in present[k]:
            x, y = XY(i, j); ops.node(eng.ntag(i, j, k), x, y, z[k])
    for (i, j) in present[0]:
        ops.fix(eng.ntag(i, j, 0), 1, 1, 1, 0, 0, 0)
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
                if (i, j) in br_pos:
                    sec = COL_BR[k + 1]; sd = br_pos[(i, j)][0]
                else:
                    sec = COL_GR[k + 1]; sd = "Y" if i in (0, NX) else "X"
                eng.add_column(et, eng.ntag(i, j, k), eng.ntag(i, j, k + 1), sec, sd)
                eles.append((et, "col", sec, eng.ntag(i, j, k), eng.ntag(i, j, k + 1))); et += 1
    ops.uniaxialMaterial("Elastic", 1, eng.E)
    br_bays = {}
    for dirn, fx, b0, top in FRAMES:
        br_bays[(dirn, fx, b0)] = top
        for k in range(1, top + 1):
            lo = (fx, b0) if dirn == "Y" else (b0, fx)
            hi = (fx, b0 + 1) if dirn == "Y" else (b0 + 1, fx)
            for (p, q) in ((lo, hi), (hi, lo)):
                ops.element("Truss", et, eng.ntag(p[0], p[1], k - 1), eng.ntag(q[0], q[1], k), eng.HSS[BR(k)], 1)
                eles.append((et, "brace", BR(k), eng.ntag(p[0], p[1], k - 1), eng.ntag(q[0], q[1], k))); et += 1
    for k in range(1, NF + 1):
        for j in range(NY + 1):
            for i in range(NX):
                eng.add_beam(et, eng.ntag(i, j, k), eng.ntag(i + 1, j, k), GBEAM(k), releases=("both", "none"))
                eles.append((et, "beam", GBEAM(k), eng.ntag(i, j, k), eng.ntag(i + 1, j, k))); et += 1
        for i in range(NX + 1):
            for j in range(NY):
                eng.add_beam(et, eng.ntag(i, j, k), eng.ntag(i, j + 1, k), GBEAM(k), releases=("both", "none"))
                eles.append((et, "beam", GBEAM(k), eng.ntag(i, j, k), eng.ntag(i, j + 1, k))); et += 1
    info = {"cm": cm, "present": present, "z": z, "NF": NF, "ele": eles}
    for k in range(1, NF + 1):
        sl = [eng.ntag(i, j, k) for (i, j) in present[k]]
        ops.rigidDiaphragm(3, eng.mtag(k), *sl)
        w = eng.floor_w(cfg, k); m = w / eng.g
        Bx, By = NX * SX + SX, NY * SY + SY
        ops.mass(eng.mtag(k), m, m, 0.0, 0.0, 0.0, m * (Bx ** 2 + By ** 2) / 12.0)
    return info

cfg = dict(
    arch="Ex18: 8-story R=3 conventional steel, wind-governed (gold)",
    NX=NX, NY=NY, SX=SX, SY=SY, heights=list(HEIGHTS),
    base="pinned", col="W14X132", beam="W24X84",
    system="R=3 steel not specifically detailed (OCBF-type X-braced), AISC 360 only",
    model={"bases": "pinned", "joints": "pinned", "gravity": "framed"},
    seis=dict(SDS=0.20, SD1=0.09, S1=0.09, R=3.0, Cd=3.0, Om0=3.0,
              Ct=0.02, x=0.75, Cu=1.7, Ie=1.0, TL=8.0),
    rho=1.0,
    analyses=["ELF"],
    governing="wind",
    wind=dict(V=120.0, exposure="C", Kd=0.85, Kzt=1.0, G=0.85, Cpnet=1.3),
    D_floor=75.0, D_roof=75.0, clad=15.0, L_floor=65.0, Lr=20.0,
    drift_limit=0.020, floor_system="one-way",
    custom_build=custom_build,
)
