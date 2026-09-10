# Steltic Nonlinear — product defaults (rules 1–9)

**Branch:** `fix-gate-b-null-fc` (extends `fibre-mesh-convergence`)  
**Locked:** 2026-09-09 (Michael finalize)

| # | Rule | Product encoding |
|---|------|------------------|
| **1** | **NLRHA ladder:** ModIMK → PZ once → if Gate A &lt;10/11 → fibre + mesh 10%. If ModIMK hits Gate A, **stay ModIMK for Gate B FC** (no fibre switch). Early abort: once **2 records NC**, abandon rest of suite → next method/mesh. | `snl mesh-converge` / `mesh_convergence` NLRHA path; `nlrha run --plasticity imk` (default); `--early-abort-nc 2` |
| **2** | **CFS DDM:** Tier 2 always (`analysis_fidelity=2`); hard-fail &lt;2 unless `--force`. No shell default. | `steltic_ddm.cfs_fidelity.cfs_ddm_fidelity_gate` on portal CFS DDM entry |
| **3** | **HR DDM:** fibre + mesh 10% | `mesh-converge --analyses ddm`; fibre GMNIA + M0–M3 nsub ladder |
| **4** | **NSP (HR):** fibre + mesh 10% | `pushover --plasticity fibre` (default); mesh-converge NSP |
| **5** | **Dual-gate:** A∧B; after A no full 11-suite for FC; FC refine = **governing record(s) only**; null/empty FC never pass | `evaluate_nlrha_dual_gate` / `gate_b_fc` / `select_governing_fc_records`; FC refine `--n <suite> --only-records <i>`; see [FIBRE_MESH_CONVERGENCE.md](FIBRE_MESH_CONVERGENCE.md) |
| **6** | Keep Newton cascade; **no** Broyden reorder | `nlrha/run.py` unchanged cascade |
| **7** | PZ default **rigid**; scissors/PZ = the one optional try in (1); **drop** ConcentratedPlasticity auto-ladder from product default | `panel_zones.mode=rigid`; PZ×1 only in NLRHA ladder; no L1/L2/L3 CP climb |
| **8** | Max ~**4** rungs; JSON scorecards (+ short md summary) | `--max-rungs 4`; `mesh_convergence_scorecard_*.{json,md}` |
| **9** | **HR DDM GMNIA:** rigid end offsets / continuous FR beam–column continuity (Liu lesson) | `GMNIAModel(rigid_end_offset=0.05)` default for non-portal HR; `--no-rigid-end-offset` to disable |

## CLI cheatsheet

```bash
# Product NLRHA method + mesh ladder
python -m snl mesh-converge JOB --analyses nlrha --tol 0.10 --max-rungs 4 --early-abort-nc 2

# NSP / HR DDM fibre mesh
python -m snl mesh-converge JOB --analyses nsp ddm --steltic-engine "$STELTIC_ENGINE_DIR"

# Standalone defaults
python -m pushover run JOB          # fibre
python -m nlrha run JOB             # ModIMK (imk), early-abort-nc=2
python -m steltic_ddm run JOB       # HR: rigid offsets on; CFS portal: Tier-2 gate
```

## Explicitly not product defaults

- ConcentratedPlasticity FBC auto-ladder (L1/L2/L3 PERFORM-like climb)
- CFS shell DDM
- Broyden-first algorithm reorder
- Merge policy: follow Michael’s current authorization

## Gate B null-FC honesty + governing-record refine

See [FIBRE_MESH_CONVERGENCE.md](FIBRE_MESH_CONVERGENCE.md), `/workspace/ddm_exemplars/analysis/REPO_UPDATE_NEXT_FC_NULL_AND_GATES.md`, and `/workspace/ddm_exemplars/analysis/REPO_UPDATE_FC_GOVERNING_RECORD.md`.

- After Gate A locks: refine finer mesh on the **governing FC record(s)** only (max per-record D/C on the suite-worst FC column among accepted motions).
- Not full 11, not arbitrary first `--n 1`.
- Unacceptable refine record → next candidate or `fc_refine_record_unacceptable` (never vacuous-pass).
