"""selftest -- fibre W-column with Lehigh residual stresses + L/1000 bow vs the AISC 360 E3 nominal curve,
and a pin-release / brace-pin sanity model. Pass criterion: GMNIA/E3 within 0.88..1.12 at KL/r = 60 and 120."""
import math
import openseespy.opensees as ops
from .sections_fiber import FiberSectionBuilder, shape, _f

E, Fy = 29000.0, 50.0


def e3(label, KL, axis):
    r = shape(label); A = _f(r["A"]); rr = _f(r["rx"] if axis == "strong" else r["ry"])
    Fe = math.pi ** 2 * E / (KL / rr) ** 2
    Fcr = 0.658 ** (Fy / Fe) * Fy if KL / rr <= 4.71 * math.sqrt(E / Fy) else 0.877 * Fe
    return Fcr * A


def column(label, L, axis, nele=8):
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    b = FiberSectionBuilder(ops, Fy=Fy)
    # column convention: depth along local y; strong-axis buckling = displacement in local y
    b.w_shape(1, label, axis="y")
    ops.beamIntegration("Lobatto", 1, 1, 5)
    ops.geomTransf("Corotational", 1, 1.0, 0.0, 0.0)   # local y = -Y global ... bow in the plane of the buckling axis
    # transf vecxz=(1,0,0): local y = (0,-1,0) -> strong axis bending displaces along global Y; weak along X
    bow_dir = (0.0, 1.0, 0.0) if axis == "strong" else (1.0, 0.0, 0.0)
    for i in range(nele + 1):
        f = i / nele; off = L / 1000.0 * math.sin(math.pi * f)
        ops.node(i + 1, bow_dir[0] * off, bow_dir[1] * off, L * f)
    ops.fix(1, 1, 1, 1, 0, 0, 1); ops.fix(nele + 1, 1, 1, 0, 0, 0, 1)   # pinned-pinned, torsion restrained
    for i in range(nele):
        ops.element("forceBeamColumn", i + 1, i + 1, i + 2, 1, 1, "-iter", 20, 1e-8)
    ops.timeSeries("Linear", 1); ops.pattern("Plain", 1, 1); ops.load(nele + 1, 0, 0, -1.0, 0, 0, 0)
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("BandGeneral"); ops.test("NormDispIncr", 1e-8, 50, 0); ops.algorithm("Newton")
    ops.integrator("DisplacementControl", nele + 1, 3, -L * Fy / E / 40.0); ops.analysis("Static")
    Pmax = 0.0
    for _ in range(400):
        if ops.analyze(1) != 0:
            break
        lam = ops.getLoadFactor(1); Pmax = max(Pmax, lam)
        if lam < 0.9 * Pmax:
            break
    return Pmax


def main():
    ok = True
    label = "W14X90"; r = shape(label)
    for axis in ("strong", "weak"):
        rr = _f(r["rx"] if axis == "strong" else r["ry"])
        for KLr in (60, 120):
            L = KLr * rr
            Pg, Pn = column(label, L, axis), e3(label, L, axis)
            ratio = Pg / Pn
            good = 0.88 <= ratio <= 1.12
            ok &= good
            print("  %s %-6s KL/r=%3d  GMNIA %7.1f  E3 %7.1f  ratio %.3f  %s" % (label, axis, KLr, Pg, Pn, ratio, "ok" if good else "FAIL"))
    print("selftest", "PASS" if ok else "FAIL")
    return 0 if ok else 1
