# Fibre mesh-convergence + NLRHA method ladder

**Status:** productised (`fibre-mesh-convergence` + Gate B null-FC honesty + governing-record FC refine on `fix-gate-b-null-fc`)  
**Authoritative:** `/workspace/ddm_exemplars/analysis/REPO_FINALIZE_FIBRE_MESH_v1.md`  
**Next update brief:** `/workspace/ddm_exemplars/analysis/REPO_UPDATE_NEXT_FC_NULL_AND_GATES.md`  
**Governing-record rule:** `/workspace/ddm_exemplars/analysis/REPO_UPDATE_FC_GOVERNING_RECORD.md`  
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
| **B — FC** | Numeric `worst_FC_DC` ≤ 1.0 **and** nonempty `force_controlled_columns`; ≤10% Δ if refining | **Governing FC record(s) only** (motion(s) behind suite-worst FC D/C at locked Gate A mesh). Same suite `--n` + `--only-records <index>`; **not** full 11 and **not** arbitrary first `--n 1`. |

Complete only when **A ∧ B**. Statuses: `continue`, `gate_a_locked_fc_refine`, `gate_a_locked_fc_pending`, `nlrha_complete`, `nlrha_partial_override`, `not_converged_within_cap`.

#### Gate B fail modes

| Reason | When |
|--------|------|
| `fc_exceeds_1` | Numeric `worst_FC_DC` > 1.0 |
| `fc_not_computed` | Empty `force_controlled_columns` and/or no DC on an FC refine / Gate B eval |
| `fc_refine_delta_gt_tol` | Relative Δ on `worst_FC_DC` vs previous FC level > tol (default 10%) |
| `fc_null_forbidden` | Null DC (including null→null refine) — **never** within-tol |
| `fc_refine_record_unacceptable` | Chosen refine record is Ch.16-unacceptable at finer mesh — try next governing candidate or fail (never vacuous-pass) |

`force_controlled_ok=True` is **invalid** when no FC columns / no DC were computed (vacuous `all([])` must not pass). Scorecards surface **suite** FC (`suite_worst_FC_DC` from locked `--n 11`) separately from **probe** FC (`probe_worst_FC_DC` on refine rungs). Probe null must not overwrite a known suite DC.

Michael override that locks a suite with **failed Gate A** stays `nlrha_partial_override` / PARTIAL — never silent `nlrha_complete`.

#### Gate B governing-record refine (Michael 2026-09-10)

After Gate A locks (≥10/11):

1. Identify the **governing FC column** = max suite `force_controlled_columns` D/C.
2. Rank motions by **per-record** D/C on that column (`per_record_fc` / `governing_fc_records`); prefer Ch.16-accepted records.
3. FC mesh refine re-runs **only** that primary suite index (then next candidates if needed) via `nlrha run --n <suite_n> --only-records <i>`.
4. Locked Gate A suite EDPs (drifts / lit compares) stay frozen.
5. Null / empty probe FC still fails (`fc_not_computed`); unacceptable refine record does not vacuous-pass.

See also `/workspace/ddm_exemplars/analysis/REPO_UPDATE_FC_GOVERNING_RECORD.md`.


Early abort (2 NC) still writes the partial suite package (acceptance + FC columns from completed records) before advancing method/mesh. ModIMK Gate A hit ⇒ FC refine stays ModIMK (no fibre).

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
