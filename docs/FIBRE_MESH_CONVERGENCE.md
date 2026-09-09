# Fibre mesh-convergence (HR first)

**Status:** productised on branch `fibre-mesh-convergence`  
**Authoritative strategy:** `/workspace/ddm_exemplars/analysis/FIBRE_MESH_CONVERGENCE_STRATEGY.md`  
**Stop band:** **10%** relative on primary metrics (Michael, 2026-09-09)

## Method

NSP (pushover), NLRHA, and DDM all use **fibre** elements by default:

| Analysis | Default | Mesh knobs |
|----------|---------|------------|
| NSP / NLRHA | `--plasticity fibre --member-nseg 4` | `nseg`, `SNL_FIBRE_NIP`, `SNL_FIBRE_NF_FLANGE`, `SNL_FIBRE_NF_WEB` |
| DDM | already fibre GMNIA | `--nsub COL BEAM BRACE`, `--nip` |

Concentrated ModIMK remains available: `--plasticity imk` (and L2 ConcentratedPlasticity FBC via hinge_params). Fibre path **drops scissors PZ** (rigid) and skips RBS remesh — disclose in reports.

Newton / algo cascade for NLRHA is **unchanged** (do not reorder Broyden).

## Mesh ladder

| Level | Intent | NSP/NLRHA | DDM |
|-------|--------|-----------|-----|
| M0 | coarse | nseg=2, nf 4×2 / 8×1 | nsub 2 2 2, nip 3 |
| M1 | working default (MC4 fibre suite) | nseg=4, nip=5, nf 8×4 / 16×2 | nsub 4 4 4, nip 5 |
| M2 | refine | nseg=8, same fibres | nsub 8 8 6 |
| M3 | fine | nseg=12, denser fibres | nsub 12 12 8, nip 7 |

Stop at the first level where **all** primary metrics vs the previous level are within **10%** relative (and for NLRHA, ACCEPTABLE / `n_unacceptable` unchanged). Cap 4 rungs → status `not_converged_within_cap` if still moving.

### Primary metrics

- **NSP:** T1, Vy, Vpeak, δt  
- **NLRHA:** Ch.16 mean drift max, roof mean X/Y, worst FC D/C; plus verdict stability  
- **DDM:** λu (λG if gravity-first), φs·λu  

## CLI

```bash
# Orchestrator entry (preferred)
python -m snl mesh-converge /path/to/job \
  --analyses nsp nlrha ddm \
  --out /path/to/job/mesh_convergence \
  --tol 0.10 --max-rungs 4 \
  --steltic-engine "$STELTIC_ENGINE_DIR"

# Module entry
python -m mesh_convergence --package /path/to/job --analyses nsp --dry-run

# Single-analysis fibre (defaults)
python -m pushover run JOB --plasticity fibre --member-nseg 4
python -m nlrha run JOB --plasticity fibre --member-nseg 4
python -m steltic_ddm run JOB --nsub 4 4 4 --nip 5
```

Dry-run prints the rung plan and exercises the stop-rule / scorecard writers **without** OpenSees.

Each analysis writes `mesh_convergence_scorecard_<analysis>.json` under `--out`, plus `mesh_convergence_summary.json`.

## Env knobs (spawn-worker safe)

```
SNL_PLASTICITY=fibre
SNL_MEMBER_NSEG=4
SNL_FIBRE_NIP=5
SNL_FIBRE_NF_FLANGE=8,4
SNL_FIBRE_NF_WEB=16,2
SNL_FIBRE_RESIDUAL=none
```

## Tests

```bash
python -m pytest tests/test_mesh_convergence.py -q
```

Pure-Python stop-rule / rung comparison — no OpenSees required.

## Still needs OpenSees smoke (MC4)

After this branch lands locally:

1. Dry-run scorecard path (done in CI/unit).  
2. MC4 NSP fibre M0→… smoke on smallest machine budget.  
3. MC4 NLRHA fibre ladder (or reuse prior L3 nseg=4 as M1 hint — still run M0 for the formal ladder).  
4. Orbison / MC8 DDM nsub ladder when packs are ready.

Do **not** resume MC8 L2 ConcentratedPlasticity.
