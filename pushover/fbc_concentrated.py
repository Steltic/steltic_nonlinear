"""L2 ConcentratedPlasticity + forceBeamColumn helpers (Post-8 ladder).

Gated by numerics.element_form == "concentrated_plasticity_fbc" or
beam_flexure.integration == "ConcentratedPlasticity".

End sections = section Uniaxial wrapping ModIMK/IMKPeakOriented (M vs plastic
rotation). Mid = section Elastic (M–φ / P–ε). Integration:
  beamIntegration('ConcentratedPlasticity', itag, secI, secJ, secE)
  element('forceBeamColumn', ele, ni, nj, transf, itag)

Scissors PZ: FR beams still attach via beam_side nodes (caller passes attach).
RBS: when geometry present, 3-piece layout — elastic stubs joint→RBS centre,
forceBeamColumn midspan with CP ends at RBS centres (Z_RBS strength via
beam_hinge). If remesh unfit, single FBC joint-to-joint with Z_RBS disclosed.
"""
from __future__ import annotations
import math
import openseespy.opensees as ops
from . import hinge_models as HM
from . import rbs_remesh as RBS

# Tag spaces (avoid HN/ZL/MAT/PZ/RBS bases in nonlinear_model / rbs_remesh)
SEC_BASE = 90_000_000       # SEC_BASE + ele*10 + (1=I,2=J,3=E)
ITAG_BASE = 91_000_000      # ITAG_BASE + ele
HKEY_BASE = 92_000_000      # synthetic hinge registry keys (no ZL)
SOFT_MAT = 93_000_000       # near-zero Uniaxial for released ends (shared)
STUB_ELE_BASE = 80_000_000  # reuse RBS seg tag space for elastic stubs
RBS_NODE_BASE = 70_000_000  # reuse RBS intermediate node space

N_STIFF_FBC = 100.0         # default Ke = N * 6EI/L; override via numerics.fbc_Ke_n
CP_END_IP_I = 1
CP_END_IP_J = 5             # ConcentratedPlasticity: 2 plastic + 3 elastic IPs


def want_fbc(prm: dict) -> bool:
    num = prm.get("numerics") or {}
    bf = prm.get("beam_flexure") or {}
    return (
        str(num.get("element_form") or "").strip().lower() == "concentrated_plasticity_fbc"
        or str(bf.get("integration") or "").strip() == "ConcentratedPlasticity"
    )


def _strong_code(slot: str) -> str:
    """EBC Iy = bending about local y → My; Iz → Mz."""
    return "My" if slot == "Iy" else "Mz"


_SOFT_READY = False

def _ensure_soft_mat():
    """Tiny rotational stiffness for released ends (Uniaxial M–θ ≈ free)."""
    global _SOFT_READY
    if _SOFT_READY:
        return
    ops.uniaxialMaterial("Elastic", SOFT_MAT, 1.0e-9)
    _SOFT_READY = True


def _elastic_section(tag: int, e: dict):
    # OpenSees 3D Elastic: E, A, Iz, Iy, G, J
    ops.section("Elastic", tag, e["E"], e["A"], e["Iz"], e["Iy"], e["G"], e["J"])


def _end_uniaxial(sec_tag: int, mat_tag: int, code: str):
    ops.section("Uniaxial", sec_tag, mat_tag, code)


def _k0(e: dict, slot: str, L: float, prm: dict | None = None) -> float:
    n = N_STIFF_FBC
    if prm is not None:
        n = float((prm.get("numerics") or {}).get("fbc_Ke_n", n))
    return n * 6.0 * e["E"] * e[slot] / max(L, 1e-9)


def build_fbc_member(
    pkg,
    e,
    prm,
    sec,
    spec,
    slot,
    dof,
    p1,
    p2,
    L,
    kind,
    hinge_i,
    hinge_j,
    beam_side,
    mat,
    hinges,
    stats,
    verbose=True,
):
    """Replace one hinged beam/col with forceBeamColumn + ConcentratedPlasticity.

    Returns updated mat counter.
    """
    _ensure_soft_mat()
    code = _strong_code(slot)
    post = prm.get("numerics", {}).get("post_cap_ratio", 0.15)
    K0 = _k0(e, slot, L, prm)
    ele = e["tag"]
    sec_i = SEC_BASE + ele * 10 + 1
    sec_j = SEC_BASE + ele * 10 + 2
    sec_e = SEC_BASE + ele * 10 + 3
    itag = ITAG_BASE + ele

    attach_i = beam_side[e["n1"]] if (kind == "beam" and e["n1"] in beam_side) else e["n1"]
    attach_j = beam_side[e["n2"]] if (kind == "beam" and e["n2"] in beam_side) else e["n2"]

    geo = None
    bp = prm.get("beam_flexure") or {}
    if kind == "beam" and hinge_i and hinge_j and int(bp.get("rbs_segments") or 0) == 7:
        geo = RBS.rbs_geometry_for_section(sec, bp)

    ni, nj = attach_i, attach_j
    rbs_offset = None
    used_rbs3 = False
    elastic_stub_tags = []

    if geo is not None:
        a, b, c = geo["a_in"], geo["b_in"], geo["c_in"]
        offset = a + 0.5 * b
        try:
            RBS.segment_stations(L, a, b)  # validate fit
            if offset > 1.0 and (L - 2.0 * offset) > 1.0:
                xyz_i = RBS.xyz_along(p1, p2, offset, L)
                xyz_j = RBS.xyz_along(p1, p2, L - offset, L)
                ni = RBS_NODE_BASE + ele * 20 + 1
                nj = RBS_NODE_BASE + ele * 20 + 2
                ops.node(ni, *xyz_i)
                ops.node(nj, *xyz_j)
                # elastic stubs (full-section props; plastic capacity still Z_RBS via spec)
                stub_i = STUB_ELE_BASE + ele * 10 + 1
                stub_j = STUB_ELE_BASE + ele * 10 + 7
                ops.element(
                    "elasticBeamColumn",
                    stub_i,
                    attach_i,
                    ni,
                    e["A"],
                    e["E"],
                    e["G"],
                    e["J"],
                    e["Iy"],
                    e["Iz"],
                    e["transf"],
                )
                ops.element(
                    "elasticBeamColumn",
                    stub_j,
                    nj,
                    attach_j,
                    e["A"],
                    e["E"],
                    e["G"],
                    e["J"],
                    e["Iy"],
                    e["Iz"],
                    e["transf"],
                )
                elastic_stub_tags = [stub_i, stub_j]
                rbs_offset = offset
                used_rbs3 = True
                stats["rbs_remesh_beams"] = stats.get("rbs_remesh_beams", 0) + 1
                stats["rbs_fbc3_beams"] = stats.get("rbs_fbc3_beams", 0) + 1
                stats.setdefault(
                    "rbs_formula",
                    "L2 FBC: elastic stubs + CP midspan at RBS centres (a+b/2); full-section stubs; Z_RBS via hinge spec",
                )
        except Exception as ex:
            stats.setdefault("rbs_remesh_errors", []).append(
                dict(ele=ele, section=sec, err=str(ex), form="fbc3")
            )
            if verbose:
                print("[fbc_concentrated] RBS3 fallback ele %s (%s): %s" % (ele, sec, ex))
            ni, nj = attach_i, attach_j
            used_rbs3 = False

    # End materials (separate IMK instances — shared state is unsafe)
    if hinge_i:
        mat += 1
        HM.make_imk_material(mat, spec, K0, post_cap_ratio=post)
        mat_i = mat
    else:
        mat_i = SOFT_MAT
    if hinge_j:
        mat += 1
        HM.make_imk_material(mat, spec, K0, post_cap_ratio=post)
        mat_j = mat
    else:
        mat_j = SOFT_MAT

    _end_uniaxial(sec_i, mat_i, code)
    _end_uniaxial(sec_j, mat_j, code)
    _elastic_section(sec_e, e)
    ops.beamIntegration("ConcentratedPlasticity", itag, sec_i, sec_j, sec_e)
    ops.element("forceBeamColumn", ele, ni, nj, e["transf"], itag, "-iter", 25, 1e-8)

    stats.setdefault("elastic_ele_tags", []).append(ele)
    stats.setdefault("elastic_ele_tags", []).extend(elastic_stub_tags)
    stats["fbc_cp_members"] = stats.get("fbc_cp_members", 0) + 1
    stats["element_form"] = "concentrated_plasticity_fbc"

    for end, on, joint, sec_ip, mtag in (
        (1, hinge_i, e["n1"], CP_END_IP_I, mat_i if hinge_i else None),
        (2, hinge_j, e["n2"], CP_END_IP_J, mat_j if hinge_j else None),
    ):
        if not on:
            continue
        hkey = HKEY_BASE + ele * 10 + end
        hinges[hkey] = dict(
            ele=ele,
            end=end,
            kind=kind,
            section=sec,
            dof=dof,
            K0=K0,
            mat=mtag,
            node=joint,
            z=p1[2] if end == 1 else p2[2],
            spec=spec,
            form="fbc_cp",
            sec_ip=sec_ip,
            sec_comp=0,
            strong_code=code,
            rbs_offset_in=rbs_offset,
            rbs_fbc3=used_rbs3,
        )
    stats[kind] += 1
    return mat
