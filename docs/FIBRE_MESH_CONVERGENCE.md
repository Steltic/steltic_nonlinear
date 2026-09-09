# Fibre mesh-convergence + NLRHA method ladder

**Status:** productised on branch `fibre-mesh-convergence`  
**Authoritative:** `/workspace/ddm_exemplars/analysis/REPO_FINALIZE_FIBRE_MESH_v1.md`  
**Defaults list:** [PRODUCT_DEFAULTS.md](PRODUCT_DEFAULTS.md)  
**Stop band:** **10%** relative on primary metrics

## Product paths

| Analysis | Product path |
|----------|----------------|
| **NSP** | Fibre + mesh M0→… stop at 10% (T1, Vy, Vpeak, δt) |
| **HR DDM** | Fibre GMNIA + mesh 10% (λu / λG / φs·λu); **rigid end offsets** on by default (Liu continuity) |
| **CFS DDM** | Tier 2 always (`analysis_fidelity≥2`); hard-fail otherwise unless `--force`; no shell |
| **NLRHA** | **ModIMK → PZ×1 (scissors) → fibre + mesh 10%**; dual-gate A∧B; early abort on **2 NC** |

### NLRHA method ladder (rule 1)

1. Start **ModIMK** with **rigid** PZ.  
2. Try **PZ scissors once** (still ModIMK hinges) — not a multi-rung PERFORM climb.  
3. If Gate A still &lt;10/11 → **fibre** + mesh 10% iterations.  
4. If Gate A hits on **ModIMK** (or ModIMK+PZ): **stay that plasticity for Gate B FC** — do **not** switch to fibre for FC.  
5. While running 11 records: once **2 are NC**, abandon the rest of that suite and move to the **next method / mesh size**.

ConcentratedPlasticity auto-ladder (L1/L2/L3) is **not** a product default.

### NLRHA dual-gate (rule 5)

| Gate | Criterion | Behaviour |
|------|-----------|-----------|
| **A — suite / 10/11** | ≥10 of 11 Ch.16-accepted | Lock suite EDPs; **stop** further full `--n 11` |
| **B — FC** | FC accepted; ≤10% Δ if refining | FC-only path (`--n 1`); no full suite redo |

Complete only when **A ∧ B**. Statuses: `continue`, `gate_a_locked_fc_refine`, `gate_a_locked_fc_pending`, `nlrha_complete`, `not_converged_within_cap`.

Newton / algo cascade is **unchanged** (no Broyden reorder).

## Mesh rungs (M0–M3, cap 4)

| Level | NSP/NLRHA fibre | DDM |
|-------|-----------------|-----|
| M0 | nseg=2, coarse fibres | nsub 2 2 2, nip 3 |
| M1 | nseg=4, nf 8×4 / 16×2 | nsub 4 4 4, nip 5 |
| M2 | nseg=8 | nsub 8 8 6 |
| M3 | nseg=12, denser | nsub 12 12 8, nip 7 |

## CLI

```bash
python -m snl mesh-converge /path/to/job \
  --analyses nsp nlrha ddm \
  --tol 0.10 --max-rungs 4 --early-abort-nc 2 \
  --steltic-engine "$STELTIC_ENGINE_DIR"

python -m mesh_convergence --package /path/to/job --analyses nlrha --dry-run
```

Each analysis writes `mesh_convergence_scorecard_<analysis>.{json,md}` plus `mesh_convergence_summary.{json,md}`.

## Env knobs

```
SNL_PLASTICITY=imk|fibre
SNL_MEMBER_NSEG=4
SNL_FIBRE_NIP=5
SNL_FIBRE_NF_FLANGE=8,4
SNL_FIBRE_NF_WEB=16,2
```

## Tests

```bash
python -m pytest tests/test_mesh_convergence.py tests/test_product_defaults.py -q
```

Pure-Python — no OpenSees required for stop-rule / ladder / fidelity / early-abort unit tests.
