# Feedback loops — three ways the nonlinear analyses hand a re-design back to HR Steel

HR Steel sizes a building against the linear code checks: member D/C for every LRFD combination and the
C<sub>d</sub>·δ<sub>e</sub>/I<sub>e</sub> story drift of ASCE 7-22 §12.12.1. The three SNL analyses measure what the
building actually does — the MCE<sub>R</sub> drift of a Chapter 16 suite, the mechanism a pushover forms, the load
factor at which the imperfect structure loses stability. Where the two pictures disagree, the **Feedback** tab of the
Nonlinear module (in the Steltic hub) or `python -m snl feedback` hands a precise, machine-derived re-design
instruction back to HR Steel, verifies the result with the same analyses, and — only when the user says so — makes
the candidate the design of record. Nothing here is required by code; each loop is a documented, reversible step.

Code: `snl/feedback.py` (the change-set builders, pure functions), `snl/hr_client.py` (HR Steel's API, stdlib),
`snl/loop.py` (the runner and the promotion), `snl/loop_server.py` + `snl/loop_ui/` (the hub tab).

## 1. Design drift to the measured response instead of C<sub>d</sub>

*Ex22 is drift-governed at 0.91 % against a 1.00 % limit — that coefficient is setting the steel tonnage. Chapter 16
then measures the MCE<sub>R</sub> mean drift at 1.46 % against a 2.00 % limit: 27 % of margin the linear design never saw.*

ASCE 7-22 §16.1.2: where a Chapter 16 analysis is performed, the §12.12.1 drift limits need not apply for Risk
Category I–III. The loop reads the linear drift and its allowable from `report.html` (Chapter 8), the Chapter 16 mean
drift and its §16.4.1.2 limit from `nlrha/nlrha_package.json`, and sets

    scale            = target_fraction × (Ch.16 limit / Ch.16 mean)        (target_fraction 0.90 by default)
    new linear target = measured linear drift × scale                       (first order: the two drifts scale together)
    cfg['drift_limit'] = new target × (drift_limit / reported allowable)   (undoes the ρ division of §12.12.1.1 where it applies)

and writes the block HR Steel must copy verbatim into `cfg`:

    drift_relief_16_1_2 = {"clause": "ASCE 7-22 16.1.2", "nlrha_job": ..., "nlrha_run": ..., "nlrha_mean_drift": 0.01463,
                           "nlrha_limit": 0.03, "nlrha_verdict": "ACCEPTABLE", "nlrha_records": 11, "table_12_12_1": 0.015,
                           "linear_target": 0.0168, "previous_drift_limit": 0.010, "risk_category": "III", "note": ...}

HR Steel's `preflight.py` / `consistency.py` accept the relaxed `drift_limit` only when this block is present, complete,
the Risk Category is I–III and the target does not exceed the Chapter 16 limit; for Risk Category IV the block is an
ERROR (the tab still shows the numbers — the size of the prize — but the loop is not eligible). Verification = the full
Chapter 16 suite on the candidate (or the three governing records in *quick* mode); pass = verdict ACCEPTABLE with the
mean drift at or below its limit. The relieved design is provisional until that passes, and says so in its report.

## 2. Resize by system role rather than member D/C

*Steltic's critical member is a floor beam at D/C 0.90; at system collapse the most strained group is the roof W24X76
at 9.1× yield strain with all 38 ends hinged — and it sits at D/C 0.73. The lateral columns are W14X730 at D/C 0.18,
sized by drift and strong-column-weak-beam, not strength.*

The loop joins, per member group (`<role>-<section>` of `calc_package.json`): the design D/C and governing combination;
the DDM `member_table` (max ε/ε<sub>y</sub> at the peak of the governing combination, yielded and hinged counts, λ<sub>u</sub>
of that combination); the NLRHA `deformation_groups` (mean peak rotation vs CP) and `force_controlled_columns`; the
pushover BSE-2N acceptance (n_yielded, D/C CP). Rules (thresholds in `feedback.DEFAULTS`, editable in the tab):

| verdict | rule |
|---|---|
| **upsize** (bottleneck) | ≥ 50 % of the group hinged at collapse, or NLRHA mean rotation ≥ 0.8 CP, or force-controlled D/C ≥ 0.95 |
| **downsize** | D/C < 0.55 and no yielding in any nonlinear analysis — the code check is the only thing asking for this size |
| **hold** | the same, but a lateral group in a drift-governed building (utilisation ≥ 0.9) — run the drift loop first |
| **keep** (second look) | heavily yielded (≥ 3× ε<sub>y</sub>) but not the mechanism; or within the 0.55–0.95 band |

Proposed sections step one size within the same nominal-depth family (`pushover/aisc_shapes.csv`; the 277/278 twins are
not a step). The user edits or unticks any line before Go. HR Steel applies the change set exactly and stops at the first
line that breaks a check. Verification = `steltic_ddm run --only <governing combination(s)>` — minutes, not the full
sweep — plus the Chapter 16 suite in *full* mode; pass = every re-run combination at λ<sub>u</sub> ≥ 1 with its φ<sub>s</sub>
check PASS (or n/a for seismic cases), all candidate D/C ≤ 1.0, drift within its allowable.

## 3. Mechanism shaping through SCWB and panel zones

The pushover's component acceptance says which ends reached θ<sub>y</sub>, IO, LS and CP and at which storeys. The loop
reads the BSE-2N census (`col_yielded` / `beam_yielded` per elevation above the base) and names the storeys where
columns yielded — a storey mechanism forming where the joint-by-joint AISC 341 E3.4a rule did not produce the global
one — and asks HR Steel for ΣM*<sub>pc</sub>/ΣM*<sub>pb</sub> ≥ 1.5 there (editable), recorded per storey in
`capacity_design.SCWB.by_story`. From `pushover/hinge_params_used.json` it reads the AISC 342 Table C5.5 modifier
machinery: a `modifiers[]` entry with factor < 1 whose reason names the C5.4a.1.a.1(b) panel-zone condition, with the
joint groups and their V<sub>pz</sub>/V<sub>ye</sub> ranges parsed from the note (Ex22: corner joints 0.3–0.5 over-strong,
interior Y-face 1.1–1.4 → doublers, interior X-face 0.88–0.93 → marginal). HR Steel adds doublers (or a heavier column)
where the ratio is above the 0.6–0.9 window, is told where it is below it (doublers would make that worse), and
records `capacity_design.panel_zone.by_joint[] = {joint, column, beams, Ru_kip, phiRn_kip, doubler_in, V_pz_over_V_ye}`.
Verification = the re-push with `hinge_params_cleared.json`: the modifier is dropped only when every recorded joint
group sits inside the window (the decision is logged); pass = no column yielding above the base at the named storeys
and the modifier cleared; *full* mode adds the Chapter 16 suite.

## What every loop does

1. **plan** — read the job folder, build the change set and the brief (`feedback/<id>/plan.json`, `brief.txt`).
2. **copy** — download the design of record from HR Steel (`/api/download/<project>`; the project folder's package if
   HR Steel holds no such job) and restore it as the candidate job `<project>__<kind>` (`/api/restore/...?archive=1`).
3. **hr** — `/api/run {building: candidate, brief, resume: true}`: the design agent continues the SAME conversation
   with the brief (it starts with `=== SNL FEEDBACK LOOP: <kind> ===`; the agent contract in HR Steel's
   `AGENT_START.md` says what each kind requires). The narrative streams to the tab; Stop cancels on both sides.
4. **fetch** — the candidate package (`candidate.zip`, unpacked to `candidate/`).
5. **check** — package-level checks before any analysis: D/C ≤ 1.0, drift within its allowable, the change set applied
   (by element tag), the relief block, `by_joint` / `by_story` recorded.
6. **verify** — the analyses above, as subprocesses in the module's environment.
7. **compare** — steel tonnage (from `member_schedule.csv` lengths × AISC weights), governing D/C, drift, sections by
   role and level before/after, the NLRHA / DDM / pushover readings, the pass/fail criteria.
8. **promote** (a button, never automatic) — `design/design_of_record.json` is written into the candidate, HR Steel's
   job `<project>` is replaced by it (the previous design archived as `<project>__<timestamp>`), the hub project's
   package and analyses move to `archive/<timestamp>/` and the candidate with its verification outputs takes their
   place; the four-analyses sheet is rebuilt. HR Steel's report shows the marker in its Chapter 1 basis table.

## Command line

    python -m snl feedback <job>                                  # status + all three plans and briefs
    python -m snl feedback <job> --loop resize --option dc_low=0.5
    python -m snl feedback <job> --loop mechanism --run --steltic-url http://127.0.0.1:8411 --verify quick
    python -m snl feedback <job> --promote mechanism-20260914-130347 --steltic-url ...

The hub starts `snl.loop_server:app` as the module's server (`SNL_JOBS`, `STELTIC_URL`, `STELTIC_ENGINE_DIR`; the
`[hub]` extra installs fastapi/uvicorn) and embeds it as the **Feedback** tab.
