"""Default HR fibre mesh rungs (NSP/NLRHA nseg + fibre densify; DDM nsub/nip).

Verified against live knobs:
  NSP/NLRHA: member_nseg, SNL_FIBRE_NIP, SNL_FIBRE_NF_FLANGE, SNL_FIBRE_NF_WEB
  DDM: --nsub COL BEAM BRACE, --nip
"""
from __future__ import annotations

# M0..M3: stop early when within tol; cap typically 4.
DEFAULT_RUNGS = (
    dict(level="M0", intent="coarse",
         nseg=2, nip=5, nf_flange=(4, 2), nf_web=(8, 1),
         nsub=(2, 2, 2), ddm_nip=3),
    dict(level="M1", intent="working default (MC4 fibre suite)",
         nseg=4, nip=5, nf_flange=(8, 4), nf_web=(16, 2),
         nsub=(4, 4, 4), ddm_nip=5),
    dict(level="M2", intent="refine",
         nseg=8, nip=5, nf_flange=(8, 4), nf_web=(16, 2),
         nsub=(8, 8, 6), ddm_nip=5),
    dict(level="M3", intent="fine",
         nseg=12, nip=7, nf_flange=(12, 6), nf_web=(24, 4),
         nsub=(12, 12, 8), ddm_nip=7),
)


def rung_knobs(rung: dict, analysis: str) -> dict:
    """CLI/env knobs for one rung + analysis."""
    a = analysis.lower()
    if a in ("nsp", "pushover", "nlrha"):
        return dict(
            plasticity="fibre",
            member_nseg=int(rung["nseg"]),
            fibre_nip=int(rung["nip"]),
            fibre_nf_flange=tuple(rung["nf_flange"]),
            fibre_nf_web=tuple(rung["nf_web"]),
            env={
                "SNL_PLASTICITY": "fibre",
                "SNL_MEMBER_NSEG": str(rung["nseg"]),
                "SNL_FIBRE_NIP": str(rung["nip"]),
                "SNL_FIBRE_NF_FLANGE": "%d,%d" % tuple(rung["nf_flange"]),
                "SNL_FIBRE_NF_WEB": "%d,%d" % tuple(rung["nf_web"]),
            },
        )
    if a == "ddm":
        return dict(
            nsub=tuple(rung["nsub"]),
            nip=int(rung.get("ddm_nip", rung["nip"])),
            env={},
        )
    raise ValueError("unknown analysis %r" % analysis)
