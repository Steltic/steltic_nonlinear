"""brace_fracture.py -- NL-R2-20 (round 4): a physical-theory brace whose cross-section has fractured through is taken
out of the NLRHA model.

Why. The NL-10 brace is a chain of corotational DISPLACEMENT-based fibre segments (Steel02 wrapped in the OpenSees
Fatigue material, Uriz & Mahin 2008). A fibre whose Coffin-Manson damage reaches 1 is failed at commit and from then on
carries 1e-8 of its stress in tension AND compression (no crack closure) -- the material's own statement that the fibre
is gone. When every fibre of one section has failed, the brace is severed at that section: it can carry no axial force.
A displacement-based segment does not enforce that: its axial strain is constant over the segment and its axial force
is the integration-weighted sum over its sections, so with one (or three of four) Lobatto sections fractured through,
the remaining sections of the segment still tie the two halves together (Ex20 Northridge #10: braces 1042/1043/
1066/1067 had two to three of the four sections of one segment at zero fibres for 0.9 s while the segment kept
carrying force through the last one). The intact fibres of those sections then take the whole elongation, fail a few
at a time at successive commits, and each failure is a force-and-stiffness jump: the step crawled below dt/32 and the
record stopped as non-converged with the Y storey drift at 4.7 % (round 3).

What. After every converged step, for each physical-theory brace that has buckled or yielded (end-to-end deformation
past 0.5 x its buckling shortening dc or 0.5 x its yield elongation dT at some committed step -- below that every
fibre is elastic and the Coffin-Manson damage of a few thousand elastic cycles is negligible), each section of its
segments is read (fiberData). A section with every fibre failed (|stress| <= 1e-6 Fye, confirmed with the Fatigue
material's own `failure` flag) fractures that buckling unit through: its fibre segments, its end pin springs and its
own chain nodes are removed from the domain (the gusset end zones stay on the frame; an X-brace half that is still
whole stays), the removed chain nodes' translational mass goes to the unit's two attach nodes (the mass taken off the
floors at build time stays in the model; when both halves of an X diagonal are gone the crossing node's mass goes to
the diagonal's frame ends, and when both diagonals are gone the two crossing nodes are removed), and the analysis is rebuilt on the smaller domain (the caller's `rebuild`:
nlrha.run wipes and re-creates the same analysis objects -- the UmfPack SOE of OpenSees 3.7/3.8 fails its symbolic
analysis when a domainChange shrinks the system ('symbolic analysis returns -8'), and the transient integrators re-read
displacement / velocity / acceleration from the committed nodes, so the response continues unchanged). From then on the
brace carries nothing, exactly what its fractured fibres already said, without a near-zero-stiffness chain for Newton.

Basis. AISC 341-22 Appendix 1, 1.5.4(d) (PDF p. 227, printed 9.1-173): brace models shall address "Fracture due to
low-cycle fatigue degradation and peak ductility effects. If fracture is not included in the brace model, the peak
deformation shall not exceed the values specified in ANSI/AISC 342, Section C3." The physical-theory brace includes
fracture; this makes a fractured section actually sever the member. ASCE 7-22 16.3.1 (strength / stiffness degradation
included in the hysteretic models). The monitor truss (zero stiffness) stays, so the brace's end-to-end deformation is
still recorded for the 16.4.2 acceptance checks.

Records in which no section fractures through never remove anything: the scan only reads element responses, so their
results are bit-identical."""
from __future__ import annotations
import numpy as np
import openseespy.opensees as ops

GATE = 0.5                 # x dc (buckling shortening) / x dT (yield elongation): the brace joins the scanned set
FAILED_STRESS = 1e-6       # x Fye: a failed Fatigue fibre returns 1e-8 x its Steel02 stress


def section_fractured(seg, ip, fye):
    """True when every fibre of section `ip` of fibre element `seg` has failed (Fatigue `failure` flag)."""
    fd = ops.eleResponse(seg, "section", ip, "fiberData")
    if not fd or len(fd) < 5:
        return False
    a = np.asarray(fd, float).reshape(-1, 5)                      # y, z, A, stress, strain
    if np.any(np.abs(a[:, 3]) > FAILED_STRESS * fye):
        return False
    for y, z in a[:, :2]:                                         # confirm with the material's own flag
        f = ops.eleResponse(seg, "section", ip, "fiber", float(y), float(z), "failure")
        if not f or f[0] < 0.5:
            return False
    return True


def _add_mass(node, m):
    try:
        cur = list(ops.nodeMass(node))
    except Exception:                                             # noqa: BLE001
        return False
    if len(cur) < 3:
        return False
    cur[0] += m; cur[1] += m; cur[2] += m
    ops.mass(node, *cur)
    return True


def _move_cross_mass(h):
    """NL-R2-20 (round 4, test): both halves of an X diagonal gone -> its crossing node keeps the chain-end mass of both
    halves with no stiffness of its own; that translational mass goes half to each frame end of the diagonal (the
    crossing node keeps its tiny rotational inertia; its translations stay tied to the partner's crossing node).
    Returns the mass moved."""
    xc = h.get("pt_cross")
    if not xc or xc.get("mass_moved") is not None or xc["node"] not in ops.getNodeTags():
        return 0.0
    cur = list(ops.nodeMass(xc["node"]))
    m = float(cur[0]) if cur else 0.0
    if m > 0.0:
        ops.mass(xc["node"], 0.0, 0.0, 0.0, *cur[3:6])
        for g in xc["frame"]:
            _add_mass(g, 0.5 * m)
    xc["mass_moved"] = m
    return m


def _drop_crossing(h, hinges):
    """Both diagonals of a connected X fractured through: the two crossing nodes have no stiffness and (after
    _move_cross_mass) no translational mass -- a singular pair. Remove their equalDOF and both nodes."""
    xc = h.get("pt_cross")
    p = (hinges or {}).get(xc["partner"]) if xc else None
    pc = (p or {}).get("pt_cross")
    if not (pc and p.get("fractured") and h.get("fractured")) or xc.get("dropped") or pc.get("dropped"):
        return False
    nodes = set(ops.getNodeTags())
    con = xc if xc.get("constrained") else pc                     # equalDOF(retained, constrained): the second-built half
    if con["node"] in nodes:
        ops.remove("mp", int(con["node"]))
    for c in (xc, pc):
        if c["node"] in nodes:
            ops.remove("node", int(c["node"]))
        c["dropped"] = True
    return True


def remove_unit(h, k, hinges=None):
    """Remove buckling unit k of physical-theory brace hinge h (segments, pin springs, own chain nodes); its chain
    nodes' translational mass goes half to each attach node. When the last unit of an X diagonal goes, its crossing
    node's translational mass goes to the diagonal's frame ends too, and when the partner diagonal (`hinges`) is
    fractured as well both crossing nodes are removed. Returns the removed translational mass (kip-s2/in)."""
    u = h["pt_units"][k]
    for t in list(u["segs"]) + list(u["pins"]):
        ops.remove("element", int(t))
    m = 0.0
    for n in u["nodes"]:
        try:
            m += float(ops.nodeMass(n)[0])
        except Exception:                                         # noqa: BLE001
            pass
        ops.remove("node", int(n))
    for g in u["ends"]:
        _add_mass(g, 0.5 * m)
    u["removed"] = True
    gone = set(u["segs"])
    if h.get("force_ele") in gone:                                # brace force from a segment that still exists
        left = [s for v in h["pt_units"] if not v.get("removed") for s in v["segs"]]
        h["force_ele"] = left[0] if left else None
    if all(v.get("removed") for v in h["pt_units"]):
        h["fractured"] = True
        _move_cross_mass(h)                                       # recorded in h["pt_cross"]["mass_moved"]
        _drop_crossing(h, hinges)
    return m


class FractureMonitor:
    """Per-record fracture bookkeeping (nlrha.run.run_record). check(t) after every converged step: returns the
    fracture events of that step (and has already removed the units and re-formed the domain)."""

    def __init__(self, hinges, rebuild=None):
        self.hinges = hinges
        self.rebuild = rebuild or ops.domainChange
        self.braces = {t: h for t, h in hinges.items() if h.get("form") == "physical_theory" and h.get("pt_units")}
        self.watch = set()
        self.events = []

    def check(self, t):
        if not self.braces:
            return []
        for tag, h in self.braces.items():
            if tag in self.watch:
                continue
            d = ops.eleResponse(tag, "deformation")
            s = h["spec"]
            if d and (d[0] <= -GATE * s.dc or d[0] >= GATE * s.dT):
                self.watch.add(tag)
        new = []
        for tag in sorted(self.watch):
            h = self.braces[tag]
            fye = h["spec"].Fye_ksi
            for k, u in enumerate(h["pt_units"]):
                if u.get("removed"):
                    continue
                hit = next(((j, seg, ip) for j, seg in enumerate(u["segs"]) for ip in range(1, int(h.get("nip", 4)) + 1)
                            if section_fractured(seg, ip, fye)), None)
                if hit is None:
                    continue
                d = ops.eleResponse(tag, "deformation")
                m = remove_unit(h, k, self.hinges)
                ev = dict(brace=tag, section=h["section"], z=h["z"], unit=k, n_units=len(h["pt_units"]), segment=hit[0] + 1,
                          n_segments=len(u["segs"]), ip=hit[2], t=t, deformation_in=(d[0] if d else None), mass_moved=m,
                          whole_brace=bool(h.get("fractured")))
                self.events.append(ev); new.append(ev)
        if new:
            self.rebuild()
        return new

    def summary(self):
        """'' or '; N brace(s) fractured through (removed from the model at t = ..): tag (section, z), ...'"""
        if not self.events:
            return ""
        whole = sorted({e["brace"] for e in self.events if e["whole_brace"]})
        part = sorted({e["brace"] for e in self.events if not e["whole_brace"]} - set(whole))
        first = min(e["t"] for e in self.events)
        lab = ", ".join("%d (%s, z %.0f in, t %.2f s)" % (e["brace"], e["section"], e["z"], e["t"]) for e in self.events)
        txt = "; %d brace(s) fractured through (low-cycle fatigue, every fibre of a section failed) and removed from the model from t = %.2f s: %s" % (
            len(whole) + len(part), first, lab)
        if part:
            txt += " [X-brace half only: %s]" % ", ".join(str(b) for b in part)
        return txt
