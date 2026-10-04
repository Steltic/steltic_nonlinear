# Review fixes — Steltic Nonlinear (steltic_nonlinear)

October 2026. These changes fix the engine, gate, report, contract and hub defects found in the October 2026 review of the Steltic repos (30 test buildings, 6 nonlinear runs and a static review). Every Critical and High item was re-checked by an independent pass against the 2022 editions and hand calculations.

**Out of scope, deferred to a separate RAG/retrieval change set:**
- the grounding server and the search tool's escalation ladder;
- grokbot conversion and indexing;
- collection names and retrieval-only contract text;
- the nonlinear Collect retrieval and validators. Component values for nonlinear (SNL) runs are verified manually by the user with the RAG and supplied in the hinge-params file.

**Effect on existing designs.** Most of these fixes make demands larger, which is the correct direction: the old values were unconservative. Designs produced before these changes should be re-run, and some will now fail gates they used to pass. That is expected.

Item IDs (HR-xx, CFS-xx, NL-xx, HUB-xx) refer to the review's verified bug register.


## Workstream `nl-pushover`

### Status

| ID | Status | Files | What changed | Test | Before → after evidence |
|---|---|---|---|---|---|
| NL-01 | FIXED | pushover/fibre_model.py, nonlinear_model.py, postprocess.py, snl/compare.py (+ cli.py, report_supplement.py, viewer3d.py, nlrha/run.py) | Every beam/column end in the default fibre NSP that isn't pin-released is now a monitored plastic-hinge region (`form="fibre_end"`). θp = ∫κp over the end region, taken from forceBeamColumn `plasticDeformation` (chord rotation minus the initial-flexibility elastic part). The region is the end segment, or the stub plus the reduced segment at an RBS. The supplied beam/column a, b, c and IO/LS/CP come from `beam_hinge` / `column_hinge` exactly as on the IMK path. `acceptance()` returns `status` and `monitored`. It reports "not_evaluated" with worst D/C None when nothing is monitored, or when a frame with moment-frame beams has no beam/column monitor. compare.py: `bpon_ok` is None for NOT EVALUATED and False for target not reached, and 0.00/None never reads as "both pass". The P-695 b-limit can now trigger on fibre runs. | test_fibre_end_rotation_matches_section_curvature_integral, test_fibre_registers_beam_and_column_hinges_and_acceptance_evaluates, test_ex22_example_fibre_monitors_every_fr_end, test_acceptance_moment_frame_without_beam_column_monitors_is_not_evaluated, test_compare_bpon_never_turns_missing_into_pass[4] | **Ex22** (RC IV): "IO 0.00/0.00; LS 0.00/0.00 — both pass", 0 groups → 216 beam + 420 column hinges monitored. IO at BSE-1N 0.40 / 0.42, LS at BSE-2N 0.20 / 0.23, both pass on real numbers. 16–20 RBS beam hinges yielded per group; IO D/C at BSE-2N 0.99 / 1.03. **Ex16** (RC III): 0.00 with 0 groups → 88 beam + 112 column hinges, LS at BSE-1N 0.001 / 0.001, CP at BSE-2N 0.001 / 0.020 (drift-governed IMF, Ω ≈ 12, so it is a genuine pass). |
| NL-09 | FIXED | postprocess.py (+ compare.py, report_supplement.py, cli.py) | `step_at` returns the first step with u ≥ δt, or None, and no longer clamps. If the push never reaches δt, the level is `target_not_reached`: `acceptable=False`, no D/C, no drift, and a note citing ASCE 41-23 7.4.3.3.1. Last-step numbers go only into a labelled `at_last_converged` diagnostic. nsp adds `nsp_ok` (permitted and reached), `reached_target` and `target_status`. The idealisation never uses a point past the curve (Δd ≤ u at Vmax). compare prints "NOT ACCEPTABLE — target displacement not reached". | test_acceptance_at_first_step_reaching_target_and_target_not_reached, test_compare_bpon_never_turns_missing_into_pass[target_not_reached] | Synthetic run: target 3.5 in, push ends at 3.0 in. Before: D/C at 3.0 in presented as δt. After: TARGET NOT REACHED, worst D/C None, verdict False. (Ex1 Y at 2.38 in against 6.47 / 11.81 in now reports this way; Ex1 was not re-run.) |
| NL-11 | FIXED | nonlinear_model.py | `StaticAnalysis` creates the analysis objects once. test and algorithm are re-issued only when their arguments change. An integrator change (halving, speed-up, arc-length) does `wipeAnalysis` and a rebuild, because re-issuing `ops.integrator` is what leaks (measured: test/algorithm re-issue is flat, integrator re-issue costs ~0.19 MB/step at 4.5k DOF). `_try_analyze` and `_tail_recovery` use it, and the push frees its objects at the end. `run["analysis_objects"]` records how many were issued. | test_static_analysis_reuses_objects_memory_flat (4.5k DOF, 600 steps, step change every 50: growth < 12 MB, ≤ 14 integrators) | Same 4503-DOF model, 800 steps: **old pattern 38.0 → 194.6 MB; new 36.5 → 40.2 MB** with 16 integrator changes. Ex16 and Ex22 full pushes issue 1 integrator per direction (was 1 per step). |
| NL-16 | FIXED | postprocess.py (+ report_supplement.py labels) | §7.4.3.2.5: (Vd, Δd) is at min(δt, Δ at Vmax). Ke is the secant at 0.6Vy, Vy is area-balanced up to Δd and capped at Vmax. α2 comes from (Vd, Δd) to the point at 0.6Vy (else the last point, flagged). μmax uses Δd/Δy. λ is tested on S_X1 of BSE-2N. Cm follows Table 7-4 by system (MF / CBF / EBF 0.9, Other and BRBF 1.0; 1.0 if T1 > 1.0 s, using T not Te), with an unrecognised system defaulting to 1.0. C1 uses max(Te, 0.2); C2 uses Te. NSP permitted needs μstrength < μmax (strict). Equations renumbered to ASCE 41-23: 7-28 Te, 7-29 δt, 7-30 C1, 7-31 C2, 7-32 μstrength, 7-33 μmax, 7-34 αe. | test_idealize_recovers_bilinear_and_uses_least_of_target_and_peak, test_nsp_coefficients_asce41_23 | Bilinear test curve recovers Ke, Vy and α1 within 0.5%. On a degrading curve with target 40 in and peak at 25 in, Δd is now 25 in (was 40 in, i.e. u_end). |
| NL-07 | FIXED (engine side) | package_reader.py, nonlinear_model.py (`beam_params_for`), fibre_model.py, rbs_remesh via `rbs_geometry_pkg` | `package_reader.beam_details()` reads RBS from structured records: member `inputs.RBS`, connection records that name the RBS and carry a=/b=/c=, connection types naming the RBS, and capacity_design `rbs_drift_factor` / `connection` / `moment_connection`. It also reads Lb per section (member `Lb_in`) and per frame (beam_bracing `Lb_provided_in` or bracing-spacing text). `beam_params_for()` evaluates the supplied C5.5 values with per-beam c (Z_RBS = Zx − 2c·tf(d − tf), AISC 358-22 Eq. 5.7-4) and per-beam Lb/ry (the larger of member and bracing values; sections_db ry). Precedence for c is: the engineer's `rbs_geometry_*`, then the package per section, then the AISC 358-22 §5.7 Step 1 default (c = 0.25bf, a = 0.625bf, b = 0.75d) for an RBS frame. The cut applies only at FR beam-to-column ends. A stale global `rbs_c_in` is superseded, with a note. The fibre path meshes stub (a) + reduced segment (b) at each RBS end, using UserDefined Lobatto with the circular-cut flange width at each point. The IMK, FBC and 7-segment paths get the same per-beam c. | test_rbs_detection_structured_variants, test_beam_hinge_uses_per_beam_z_rbs_and_package_lb, test_fibre_rbs_mesh_reduces_beam_strength | **Ex2** W36X182 hinge Z 718 → **479.7 in³** (= HR's Z_RBS; My was 1.50× too high). **Ex22** W30X116 Z 217 (global c = 3.25) → 272.7, W33X169 → 440.2; Lb/ry W33X241 61.5 (max over beams) → 55.1. **Ex16** IMF beams Lb = full span → 112 in (beam_bracing); c per beam 2.00 / 1.80 / 1.41 in plus a 0.25bf default for W21X55 / W24X68. Fibre push Ω: Ex16 12.88 / 11.74 → 12.60 / 11.02; Ex22 5.64 / 5.23 → 4.94 / 4.48. Portal test: Vmax with RBS / without = Z_RBS/Zx · L/(L − 2Sh) within 0.08. |
| NL-28 | FIXED | package_reader.py (`pz_doublers`, `doubler_for_joint`), nonlinear_model.py (scissors loop) | Scissors panel zones take HR's `capacity_design.panel_zone.by_joint[].doubler_in` per model joint. The match is on column section, beam-section set, and level (from `level` or the L#/floor # in the label). Ex16-style `conn-…-Wbeam-Wcol` labels are parsed. If several records match, the thinnest doubler is used and noted. With no match it falls back to the global value, noted. `panel_zones.doublers="global"` restores the old behaviour. Each spec's flags record the source. | test_per_joint_doublers_from_hr, test_scissors_panel_zone_uses_joint_doubler | Before, every joint had tp = tw + 0.0 (the global `doubler_t_in` in every collected file). After, the Ex22 scissors build matches 120 joint-planes to the 24 HR records and gets 10 distinct (column, tp) values (e.g. W27X368 tp 1.38 / 2.3175 / 2.5675 / 2.63 in). Ex2: 144 joint-planes, 5 distinct. Default `rigid` mode is unchanged. |
| NL-22 | FIXED | nonlinear_model.py, report_supplement.py (+ skill doc) | The initial tail status is `captured` only when the main push fell to 0.8 Vmax, otherwise `lower_bound`. With `--tail none` it gets an explicit message, and `not_needed` is no longer produced. The report reclassifies legacy `not_needed` from the curve, and "escalation tried: none needed" appears only for captured / component_limit. | test_tail_status_lower_bound_when_escalation_disabled | Forced non-convergence at 15 steps above 0.8 Vmax with tail none: was `not_needed`, printed as "captured"; now `lower_bound`, captured False. |

Tests: `PYTHONPATH=$PWD STELTIC_ENGINE_DIR=… python -m pytest tests -q` gives **194 passed** (baseline 176 plus 18 new in `tests/test_nl_pushover_fixes.py`).


### Behaviour changes users will notice

- The fibre NSP BPON now carries real beam and column D/C, so moment frames can FAIL where they used to "pass" with 0.00. Packages written before this change with empty groups render as NOT EVALUATED, not as a pass. `snl_summary.pushover.bpon_ok` can be None (not evaluated); `review.py` still prints "NOT satisfied" for None, so it stays conservative.
- Pushes that never reach δt are NOT ACCEPTABLE, with drift and D/C at δt blank.
- RBS beams are weaker in every engine (Z_RBS per beam, fibre cut), which lowers Ω (Ex22 −12%, Ex16 −2 to −6%) and lengthens T1 slightly. Lb now comes from the package (usually shorter than the span), which raises `a` for the C5.5 rows.
- The P-695 `component_limit` can now trigger on fibre runs, because beam and column b is monitored (Ex22: δu at u = 49.5 / 56.4 in, μT 5.7 / 6.8; Ex16 X at 26.5 in).
- The fibre pushover is about 20% slower (Ex16 100 s → 120 s under the same load) because RBS beams get 6 segments and 2 extra eleResponse calls per member end per step. Memory no longer grows with the step count.
- Cm is 1.0 for systems not in Table 7-4 (BRBF / unrecognised) instead of 0.9.

### Follow-ups

- NL-13 interaction: the pushover gravity puts point loads on column-less diaphragm nodes. On Ex16 the gym long-span W24X68 beam-to-beam junction (FR end, no column) carries 51 kip at midspan and is close to yield under gravity. Monitoring makes this visible; with an RBS cut wrongly applied there it was 2.15 D/C, which led to the beam-to-column-only rule.
- The acceptance step is the first recorded step ≥ δt (dU = H/1500 ≈ 0.7 in on Ex22), so D/C is read up to one step past δt (conservative). Interpolating at δt is a possible refinement.
- αPΔ is still the first-storey stability-coefficient approximation (flagged in the report).
- `nlrha/run.py` fallback cascade re-issues algorithm/test only (measured flat), so it was left to nl-nlrha.
- `pushover/hinge_params.json` nsp note still says "Confirm Eq. 7-28..7-32 numbering"; that is a data file outside my set.
- NL-15 (RC III levels and the report's LS/CP hard-coding) is untouched; compare's RC table is used as is.

## Workstream `nl-elements`

### Status

1. **Hinge-monitor sign bug (not mine to fix, affects my link acceptance):** `ops.eleResponse(zeroLength, "force")[0:6]` is the node-1 force = −(spring force). The monitors (`pushover()` snapshot / nl-pushover's new `hinge_plastic_deformation`, and `nlrha/run.py`) compute `d − f/K0` = plastic + 2 × elastic spring deformation. For IMK springs the excess is ~0.2 θy (conservative); for the link shear spring it is 2Δy ≈ 0.19 in, i.e. a link reads IO D/C > 1 at first yield (IO = 0.005 rad × e = 0.18 in). Fix in the monitor: use `M = -f[j]` (or `f[j+6]`). My link hinge registers the physically correct K0 = Ks.
2. nl-pushover's `hinge_plastic_deformation` brace branch should read force with `HM.brace_axial_force(tag, h)` (physical-theory braces only appear in the NLRHA, so the NSP is unaffected today).
3. New hinge kinds: `link` (EBF shear spring, dof 3, inches) and link flexural springs as `beam` with section "<sec> link"; BRBs are kind `brace` with `h["brb"] = True`. `postprocess.acceptance` census puts unknown kinds into the column bucket — a `link` bucket would be cleaner (nl-pushover).
4. nl-nlrha: `results[i]["stats"]["degradation_16_3_1"]["demonstrated"]` is available if the Chapter 16 verdict should carry "16.3.1 not demonstrated".

### Behaviour changes users will notice

- BRBF packages now run in all three engines (previously crashed). Without verified `brb_axial` values the reports show TEMPLATE / FALLBACK banners per BRB label.
- EBF pushover/NLRHA: base shear drops sharply (Ex8 −37 % at ≤ 1.2 % drift), period lengthens (link shear flexibility per Eq. C-E2-1), new `link` acceptance groups, links can fail CP at ~1.5 % storey drift. DDM λu of EBFs can drop (link shear now limits capacity).
- NLRHA: IMK hinges now deteriorate cyclically (template Λ); CBF braces are physical-theory fibre braces with fatigue fracture (slower, more DOFs; set `brace_axial.nlrha_element = "truss"` to revert, and the 16.3.1 block then says braces are NOT DEMONSTRATED). Pushover (NSP) results are unchanged by Λ (monotonic).
- pushover_report.html gets a red "MODEL LIMITATIONS / FALLBACKS" banner (fibre NSP: no strength degradation; template groups; unchecked link axial interaction).

### Follow-ups

- Monitor sign fix (item 1 above) — must land with nl-pushover / nl-nlrha before link IO D/Cs are meaningful.
- AISC 342-22 E2.4c link axial rule (PUF/Pye > 0.6 → elastic, zero permissible deformation) not implemented — flagged in model_warnings.
- Table C2.4 footnotes are not legible in the converted corpus; the shear-flexure interpolation of the C2.4 acceptance values is the mirror of the C2.2 footnote — the user should confirm (`ebf_link.interpolation`).
- Fibre NSP strength degradation (local buckling / fracture in fibres) not modelled; only disclosed.
- Physical-theory brace: ε0 (Hsiao et al. 2012) is mesh-dependent; default nseg 4 × 4 Lobatto points; `physical_theory.element = dispBeamColumn` available. Ex1 (HR-1 apex nodes) does not converge with either brace model.
- DDM `solver.classify` still labels a BRB-yielding peak "instability" (its catch-all branch); a "BRB yielding — ductile" class would be more accurate (affects φs class only for non-seismic combos).
- Collect (snl/collect.py) has no groups for `brb_axial` / `ebf_link` / `cyclic_deterioration` — the user supplies them manually in the params file (as specified for this task).

## Workstream `nl-collect`

### Status

Scope note: per the user, component values are verified manually and supplied via the hinge-params file; Collect
retrieval / transcription validators (NL-05, the NL-04 retrieval PLAN, NL-24) are out of scope and untouched
(`check_field`, `PLAN`, `prefetch`, rag.py unchanged). Collect's FIELDS / row_for / validate / assemble were changed
only to match the new schema.

| ID | Status | Files | What changed | Test | Before → after evidence |
|---|---|---|---|---|---|
| NL-04 | FIXED (schema/engine side; Collect PLAN deferred) | pushover/hinge_params.json, hinge_models.py, params_schema.py, snl/collect.py | Template brace group is `mode: table_C3_4` (AISC 342-22 Table C3.4: n_expr, f, IO "1.5 Δc", LS "0.7 n Δc", CP "n Δc", note [d] tension_only); engine reads printed or field form; template/_README rewritten to ASCE 41-23 / AISC 342-22 (41-17 forms retired; legacy brace form still read but always UNVERIFIED). | test_template_brace_is_table_C3_4_and_printed_cells_are_used, test_legacy_brace_form_is_never_verified | table_C3_4 unreachable from any template/Collect file → template HSS8X8X1/2 brace: IO = 1.5Δc = 0.508 in, LS = 0.7nΔc, CP = nΔc (n from user; template n flagged as damaged-cell reading) |
| NL-06 | FIXED | params_schema.py, hinge_models.py, hinge_params.json, collect.py | C2.2 cells accepted as printed ("0.25 a", "a", "b", "9 θy") or as IO_frac_of_a / LS_frac_of_a / CP_frac_of_b fields; template carries both C2.2 lines. | test_printed_cells_are_read, test_table_C2_2_line_1_io_is_a_quarter_of_a | W36X232 L=360: IO = 1.0 θy = 0.0071 (old template) → 0.25a = 2.25 θy = 0.0160 rad |
| NL-14 | FIXED | params_schema.py, hinge_models.py, collect.py | Row chosen per section by flange/web λ vs AISC 341-22 Table D1.1b (0.30/0.38; web case 11 for MF, 14 otherwise; Fye, Ca=PG/Pye), line 3 interpolation per element, lowest value; no "Moderately ductile" row; Collect asks lines 1 and 2 (line 2 optional). Missing line 2 → flat reduction, flagged and UNVERIFIED. | test_row_selection_interpolates..., test_no_moderately_ductile_row_and_imf_columns_build, test_missing_line_2_is_a_loud_fallback..., test_web_limits_follow_the_frame_type | Ex16 IMF: Collect asked "row '1. Moderately ductile'" (never collected) → "lines '1. Highly ductile' and '2. Non-moderately ductile'"; W14X109 (bf/2tf 8.49, λhd 6.89, λmd 8.73) interpolated (t = 0.87) |
| NL-08 | FIXED | snl/collect.py (`frame_type`) | Frame type from cfg system + HR package structure (braces, links, BRB sections, AISC 358 / named moment connections not marked pinned/released, SCWB/panel_zone checks); dual detected; BRBF brace row says Table C3.3 not modelled. | test_frame_type_is_not_read_from_the_word_moment | Ex4 BRBF: moment_frame True / beam `fr_connection` → False / `member` (kind brbf); Ex16 IMF and Ex2 SMF stay moment |
| NL-15 | FIXED | pushover/performance.py (new), pushover/cli.py, report_supplement.py, snl/cli.py, snl/compare.py | ASCE 41-23 Table 2-5 (verified in corpus): RC I/II LS/CP, RC III Damage Control / Limited Safety, RC IV IO/LS. DC = min(½(IO+LS) [Table 2-1], a-point [§7.5.3.2.2]); LtdS = ½(LS+CP). `--risk-category` passed to pushover; report, package (`risk_category`, `bpon_levels`, DC/LtdS per group) and four-analyses sheet use one mapping. | test_bpon_levels_by_risk_category, test_damage_control_and_limited_safety_limits, test_four_analyses_sheet_uses_the_same_mapping | Ex16 (RC III) imk push: "LS at BSE-1N / CP at BSE-2N" → "performance level checked: DC (Damage Control)", worst DC 0.05 / LtdS 0.03; Ex22 (RC IV) report now IO/LS like the sheet |
| NL-19 | FIXED | params_schema.py, hinge_models.py, hinge_params.json, collect.py, report_supplement.py | Per-field provenance: template `unverified` lists, un-quoted Collect fields, MOCK/placeholder sources → UNVERIFIED; a builder using them drops the effective `verified` and the banner names them. Column P-M reduction = AISC 342-22 Eqs. C3-5/C3-6 (verified in corpus; κ=1, conservative); a legacy 1.18(1-P/Py) is replaced and flagged. | test_template_values_never_pass_as_verified, test_collect_written_group_flags_unquoted_fields..., test_superseded_column_PM_reduction... | Mpce/(Z Fye): P/Pye 0.10 1.000→0.950, 0.15 1.000→0.925 (Ex16 columns), 0.30 0.826→0.788. Ex22/Ex2 "verified" collected files → effective UNVERIFIED naming Mc_over_My, force_controlled_above_P_over_Pye, Mpce, a_min, Fy_ksi, noncompact_reduction |
| NL-21 | FIXED | snl/review.py | `results_verdict()` from the files (DDM n_pass<n_checked, gate, NLRHA verdict, BPON evaluated?, NSP permitted, unverified params); verdict box heads every review (MOCK and model); model text claiming "nothing required / lighter design" against a FAIL/INCOMPLETE is flagged; system prompt rule added. | test_review_verdict_is_derived_from_the_results | Ex22 MOCK: "Nothing is required … candidates for a lighter design" → "Results-derived verdict: FAIL — DDM 1 of 11 below 1.0 (0.98); BPON (IO/LS) NOT EVALUATED"; Ex2 → INCOMPLETE |
| NL-23 | FIXED | snl/llm.py, snl/review.py | connection() records mock_requested / mock_reason; unconfigured model → warning event, stderr, banner in review.md and review.html meta. Force-controlled checks cite §16.4.2.1 (corpus line 13602; 16.4.2.2 is deformation-controlled); a passage grounds a citation only if it is that clause. | test_review_says_loudly_that_no_model_is_configured, test_force_controlled_checks_cite_16_4_2_1 | silent MOCK → "NO MODEL CONFIGURED (STELTIC_LLM_MODEL is empty) -- OFFLINE MOCK review" |
| NL-26 | FIXED | report_supplement.py, nlrha/design_criteria.py | Open-items list states the real parameter status; banner no longer says "41-17 from memory" for every file; 16.1.4 draft: C5.5 modifiers "NOT EVALUATED" instead of "none", capacity-design records as text (no JSON dumps), parameter status names unverified values. | (manual: Ex22/Ex16 criteria rebuilt; Ex16 push report) | Ex22 16.1.4: "Adjustments … none" → "NOT EVALUATED -- … [engineer to evaluate]"; "verified: True" → "UNVERIFIED -- values not supplied by the user: …" |
| NL-27 | FIXED | steltic_ddm/report_ddm.py | Flag under the verdict tiles: λu ≈ 1/(φc·D/C) ⇒ DDM passes only if D/C ≤ φs/φc (φc = φb = 0.90, AISC 360-22 E1/F1), per φs class, with the design's highest member D/C; `member_equivalent_DC_limit` in the ddm_analysis block. | test_ddm_sheet_states_the_equivalent_member_limit | Ex22 rebuilt sheet: HR-G φs 0.70 (βT 3.5) → D/C ≤ 0.78; HR-W 0.80 → 0.89; highest member D/C 0.991 "a DDM FAIL here is expected" |

Test result: `PYTHONPATH=$PWD pytest tests -q` → 196 passed (176 existing + 20 new in tests/test_params_schema.py; test_collect.py updated to the new column field names).

### Behaviour changes users will notice

- Runs on the repository template, on Collect output and on MOCK values now show the red UNVERIFIED banner listing the fields; Ex22's/Ex2's "verified" collected files become effectively UNVERIFIED (template Mc_over_My, force_controlled threshold, Mpce, Fy, noncompact_reduction were never collected).
- Column expected moment with axial load drops (C3-5/C3-6): −5 % at P/Pye 0.1, −7.5 % at 0.15, −4.7 % at 0.3.
- Compactness limits are AISC 341-22 (flange 0.30/0.38, web case 11/14) instead of the 341-16 numbers: more sections fall below highly ductile; with line 2 of C3.6 absent from the file they get the flagged flat ×0.5.
- Template beam (member) values are now the Table C2.2 corpus reading (a 9θy, b 11θy, IO 0.25a…) and the template brace is Table C3.4 — template-run numbers change (still UNVERIFIED).
- RC III BPON checks Damage Control / Limited Safety (stricter than LS/CP); RC IV report checks IO/LS.
- `snl review` without a model prints a warning and stamps review.md/html "NO MODEL CONFIGURED"; every review starts with the results-derived verdict box.
- Collect: brace group now transcribes Table C3.4 fields while PLAN still fetches C3.6 (retrieval deferred), so a braced frame's brace group still cannot be collected (it could not before either); BRBF says Table C3.3 is not modelled.

### Follow-ups

- Collect PLAN for brace_axial (C3.4 + AISC 341 A3.2) and C2.2/C3.6 line-2 transcription quality — deferred with the RAG work (NL-04 retrieval half, NL-05, NL-24).
- Template values the corpus cannot give cleanly: Table C3.4 n expressions (damaged cells, decode UNCONFIRMED) and Table C3.6 line 2 a/b (left null) — the user must supply them.
- Table C3.4 notes [c] (built-up connectors) and [e] (np, Sec. C7) not applied; W-shape braces in C3.4 mode use λ/λhd = 1.0 (flagged).
- DC per §7.5.3.2.2 uses the a point only (the "e point" of other tables not implemented); κ taken as 1.0 in C3-5/C3-6 (the conservative reading).
- nlrha/report.py banner (nl-nlrha) still says "placeholders" generically; it does follow the effective `verified`.
- compare.py `bpon_ok` with zero groups still prints "both pass" (NL-01, nl-pushover); the review now reports it as NOT EVALUATED.

## Workstream `nl-nlrha`

### Status


| ID | Status | Files | What changed | Test | Before → after evidence |
|---|---|---|---|---|---|
| NL-12 | FIXED | `nlrha/drift.py` (new), `nlrha/run.py`, `nlrha/cli.py` (`report`) | 16.4.1.2 drift at vertically aligned points: every plan point of the level above paired with the same (x,y) on the nearest level below (or the base); double-height spaces use their own height; max over points (convex-hull vertices per group — exact for rigid diaphragms, asserted). Residual drift the same way. `nlrha report` recomputes old results from the master frames. Per-record `drift_points` / `peak_drift_at` stored. | `test_ex16_aligned_point_drift_reproduces_review_recomputation`, `test_ex16_double_height_gym_is_monitored`, `test_story_drifts_picks_aligned_points_on_setback` | Ex16 mean drift (11 records) storey 1/2/3 X/Y: reported 1.44/1.65, 0.82/0.87, **0.94/1.76 %** → consecutive-level aligned points 1.44/1.64, 0.66/0.75, **0.40/0.45 %** (= drift16.py exactly); with the gym's base→L2 double-height points storey 2 X = 1.05 %. Max mean 1.76 % (storey 3) → 1.64 % (storey 1). |
| NL-13 | FIXED | `nlrha/gravity.py` (new), `nlrha/model.py` (`ch16_gravity` only) | Framed floor plate rebuilt from the package geometry (bays on column lines, engine-style); roof vs floor per bay (set-back / lower roofs = roof); L from cfg `L_by_level`/`L_floor`, roofs at cfg `Lr`; each bay lumped to its column corners (beam-only and off-grid nodes unloaded); 16.3.2 exception checks BOTH Σ0.5L ≤ 25 % ΣD and L0 < 100 psf over ≥ 75 % of the area; `with_live=False` gives 1.0 D. | `test_gravity_framed_area_roofs_and_tributaries`, `test_gravity_no_live_required_when_heavy_live`, `test_ex16_gravity_before_after` | Ex16 areas L1/L2/L3 7840/**21952**→**10976**/4704 ft²; Σ0.5L/ΣD 0.30 ("no-live REQUIRED") → 0.13 (not required). Ex8 L1–L5 31,500 → 23,400 ft². |
| NL-17 | FIXED | `nlrha/run.py`, `nlrha/acceptance.py`, `nlrha/cli.py` | Record status `completed / nonconvergence / incomplete`. Converged fallback micro-steps no longer abort: they shrink dt. Only an explicit budget (`--step-budget` 40× nominal steps, `--record-budget-s` 1800 s) can cut a record → "incomplete (time-out)", never NC; one retry at dt/2. Old "crawl abort" results read as incomplete. Early abort OFF by default; when on, it fires only once genuine NC count > 16.4.1.1 allowance for the RC; records not run + basis listed in console, report, package (`records_not_run`, `meta.early_abort`). Verdict status ACCEPTABLE / NOT ACCEPTABLE / INCOMPLETE (incomplete, not-run, partial selection, < 11 motions). | `test_crawl_abort_is_incomplete_not_nonconvergence`, `test_early_abort_lists_records_not_run_and_basis`, `test_run_suite_early_abort_is_decision_based` | Ex16 1-record run with `--step-budget 0.3`: "INC … all 642 committed steps converged — NOT a 16.4.1.1(1) non-convergence", verdict INCOMPLETE (was: NC → NOT ACCEPTABLE). Ex1-type 2 crawl aborts + 9 skipped → now INCOMPLETE with the 9 records listed. |
| NL-18 | FIXED | `nlrha/acceptance.py`, `nlrha/run.py`, `nlrha/cli.py` | 16.3.2 no-live (1.0 D) suite run automatically when the exception does not apply (`--no-live-case auto/run/skip`); verdict = both cases; required-but-not-run → INCOMPLETE. 16.4.2.1 column check: both equations (16.4-1 compression, 16.4-2 counteracting/tension), AISC 360 H1-1 (= AISC 342 C3-9/C3-12/C3-13) with concurrent (P, Mmaj, Mmin) per record, mean (or 120 % median) over records; flexure classified per AISC 342 C3.4 (deformation-controlled → analysed M vs M_CE, m = 1; force-controlled → transformed M vs φMn F2/F3/F6); C3-10; Fy/Ry from the component parameters (not hard-coded 50). Qns from the model's own gravity state. Elastic-modelled axes whose M > M_CE are flagged (16.3.1). | `test_column_strengths_hand_calc`, `test_fc_check_combines_axial_and_flexure_and_counteracting_case`, `test_flexure_makes_fc_check_stricter_than_axial_only`, `test_no_live_case_required_but_not_run_withholds_verdict` | Ex16, 2 records (Superstition Hills, Northridge): worst axial-only D/C 0.47 → combined **1.90** (W14X145 gym base column: weak-axis M 10,771 k-in > M_CE 7,315 k-in, modelled elastic → flagged); W14X176 corner 1.14. Old axial-only suite (11 rec) D/C 0.26. |
| NL-20 | FIXED | `nlrha/acceptance.py`, `nlrha/report.py`, `nlrha/cli.py`, `nlrha/viewer3d.py`, `nlrha/design_criteria.py`, `snl/compare.py` | NaN/absent statistics carried as None and printed "not computed" (verdict rows, per-record table, drift figure, four-analyses tile, viewer, criteria doc); incomplete records show "≥ x % (partial)". | `test_nan_rendered_as_not_computed`, `test_early_abort_lists_records_not_run_and_basis` | Synthetic all-NC Ex16 package: four_analyses "mean drift not computed (no acceptable record)", "Record peak drifts not computed" (was "nan% … 0.00%"); 0 "nan%" in reports. |
| NL-25 | PARTIAL | `nlrha/ground_motions.py`, `nlrha/run.py`, `nlrha/report.py`, `nlrha/cli.py` | Scale factors bounded 0.25–4 by default (`--sf-bounds none` to disable; top-up and post-multiplier exceedances reported in `sf_note`). HHT-α algorithmic damping quantified from the amplification matrix at T1 and 0.2T1 and shown next to the 16.3.5 viscous damping (warning if the sum > 2.5 %). Not done: spectral-shape-only ranking without `nlrha hazard` (already disclosed as an open item), DDM transfer gate (not NLRHA). | `test_hht_algorithmic_damping`, `test_scale_factor_bounds_default` | HHT α 0.9 at dt/T 0.1: 0.211 % (OpenSees free vibration 0.205 %); Ex16 dt 0.01: 0.0005 % at T1, 0.063 % at 0.2T1. |

### Behaviour changes users will notice

- Ex16-type buildings: the column force-controlled check now includes flexure and can flip ACCEPTABLE → NOT ACCEPTABLE (Ex16: gym columns' weak-axis moments exceed M_CE in an elastic minor axis; biaxial corner columns). Flags say which axis is unmodelled yielding.
- Verdict can be **INCOMPLETE** (overall False): < 11 motions, records incomplete/not run, `--records/--only-records` subsets, required no-live case not run, results from older builds (no column moments). `verdict.status`, `verdict.not_evaluated`, `records_not_run` added to `nlrha_package.json`; `overall` stays boolean.
- `nlrha run` / `snl run` run the full suite (early abort off); stalling records shrink dt instead of "crawl abort" → longer runs (budget 1800 s/record).
- Gravity on stepped/L/Z plans drops to the framed area; lower roofs get Lr (cfg) not L; beam-only nodes no longer loaded; the no-live 1.0 D suite runs automatically when the 16.3.2 exception fails (doubles NLRHA time for those buildings).
- Drift values change on irregular plans; double-height spaces are now checked.
- Ground-motion suites may change where shape-fit factors were outside 0.25–4.
- `nlrha report` on old `raw_results.pkl`: drifts recomputed at aligned points from master frames; verdict INCOMPLETE (no column moments recorded) with a note that gravity was the old bounding-box one.

### Follow-ups

- `mesh_convergence/driver.py` / `stop_rule.should_early_abort` count `not converged` as NC — should use `status == "nonconvergence"` (results now carry `status`).
- `snl/loop.py:533-534`, `snl/feedback.py`, `snl/review.py` use `mean_drift_max or 0`; render None as "not computed" (NL-20 residue outside my files).
- IMK columns have no minor-axis hinge (nl-elements): weak-axis yielding is not modelled; the FC check now flags it.
- Girder gravity moments are not in the nodal-gravity idealisation; level dead load is the level-average seismic weight (mixed occupancies, e.g. Ex16's gym roof vs classroom floor).
- FC check uses Lb = column length, Cb = 1, K = 1; non-I shapes use Mp (flagged); expected-strength LTB per AISC 342 simplified via F2 with Fye.
- Full 11-record Ex16 / Ex1 / Ex8 re-runs not done here (CPU budget); verified on 1–2-record runs and on the saved Ex16 results.

## After integration: remaining follow-ups and PARTIAL items

- **Partial or deferred items:**
  - NL-10 is PARTIAL: the fibre NSP has no strength degradation (stated only), and Λ / ε0 are literature template values.
  - NL-03: the AISC 342-22 E2.4c link axial rule is not implemented (flagged).
  - NL-25 is PARTIAL (see nl-nlrha notes).
  - NL-04 / NL-05 / NL-24: the Collect retrieval half is deferred by the user.
- **New NOT ACCEPTABLE verdicts.** Both re-run buildings now fail the NLRHA on the 16.4.2.1 force-controlled column check with flexure (nl-nlrha's NL-18). Ex16's failure is on a minor axis that is modelled elastic (IMK columns have no minor-axis hinge; flagged). Ex4 W14X193 fails at 1.14. The engineer should review both. This is a design/model finding, not a merge defect.
- **Old packages keep their old values.** `snl report` re-renders the verdict stored in old packages. For example, Ex22's old NLRHA ACCEPTABLE and `params_verified: true` come from the pre-NL-19 package. Re-run to get the new provenance and checks.
- **RC IV fibre run not repeated after the merge.** Ex22 was not re-run end-to-end here. nl-pushover ran its pushover on the branch: IO 0.40 / 0.42, LS 0.20 / 0.23.
- **Not re-verified after the sign fix.** EBF (Ex8) link acceptance was not re-run after the sign fix. It should now read γp correctly; nl-elements' Ex8 CP D/C 0.94 predates the fix.
- **Carried over from the branch notes:**
  - `mesh_convergence` / `stop_rule` should use `status == "nonconvergence"`.
  - `snl/loop.py` / `feedback.py` / `review.py` use `mean_drift_max or 0`.
  - DDM `solver.classify` labels BRB yielding "instability".
  - The pushover gravity is still not framed-area (`nlrha/gravity.py` could be reused).
  - The acceptance step is the first step ≥ δt (no interpolation).
  - αPΔ uses the first-storey approximation.
- The pushover report repeats the per-label BRB template warning once per BRB label (cosmetic).

## Independent verification

#### NL-01 — fibre NSP beam/column monitors: CORRECTED here (aggregation); the monitor itself is correct

**Plastic rotation definition.** θp is the sum of (θJ,p − θI,p) over the forceBeamColumn `plasticDeformation` of the end region.
- This equals ∫κp dx over that region, because θI = ∫(ξ−1)κ and θJ = ∫ξκ.
- Lp is the end segment (L/nseg), or the stub plus the reduced segment at an RBS end.
- The basic-system components are (3, 4) for beams (strong axis about local y) and (1, 2) for columns.

**Independent check (`nl01_portal.py`).** One-bay SMF portal, fibre path, nseg 4, pushed from 3 % to 4.5 % drift:

| Quantity | Value |
|---|---|
| Drift increment | 0.01533 |
| Column-base θp increment | 0.01546 / 0.01548 |
| Beam-end θp increment | 0.01546 / 0.01413 |
| Opposite column top | 0.00226 |

These match the beam-sway mechanism (dθp = dΔ/h). This confirms both the definition and the axis components.

The existing curvature-integral test is independent (section κ − M/EI), so it is not a tautology.

**zeroLength sign fix.** OpenSees `force[0:6]` is the node-1 force, which is minus the spring force. `zero_length_spring` uses `force[dof+5]`. The merge tests use real OpenSees springs:
- elastic k = 100 under load 5 gives plastic 0;
- EPP spring Fy = 3 pushed to 0.10 gives plastic 0.07.

They are not tautological. The NLRHA uses the same reader. The `fbc_cp` branch reads section forces, so it has no sign issue.

**DEFECT found and fixed (8be1a4a).** This affects both the NSP and the NLRHA.
- `postprocess._census_drifts` reported a (kind, section, level) group's D/C as that of the member with the **largest deformation**.
- `nlrha.acceptance.evaluate` (16.4.2.2 groups) divided the largest mean deformation by the **first member's** CP and b.
- Members of one group have different limits:
  - columns: Table C3.6 a and b vary with P_G/P_ye, through (1 − PG/Pye)^2.4 and ^3.4;
  - beams: per-beam span, Lb and RBS cut (NL-07).
- So a more heavily loaded column with a smaller rotation could be missed.
- Hand case: CP D/C was reported as 0.33 (NSP) and 0.50 (NLRHA); the true value is 1.25.
- Now every member is checked against its own limits. The group carries the governing member's demand and limits, and `theta_pl_max` / `Qu_rad_max` keep the largest deformation.
- I confirmed that both tests fail without the fix and pass with it.
- In Ex22 (template parameters) the column CPs within each group are uniform, because b hits the 0.07 cap, so Ex22 numbers do not change. Groups of lighter or heavily loaded columns do change.
- The DC / LtdS path (`performance.augment`) was already per member.

#### NL-02 — BRB: CORRECT

The implementation matches the AISC 342-22 corpus:
- C3.3a.1: P_CE = T_CE = core area × Fye, with Fye = Ry·Fysc. Point C is ωQ_CE in tension and βωQ_CE in compression.
- Eq. C3-3: Δy = P_CE·Lcore/(E·Acore) + 2·P_CE·Lconn/(E·Aconn).
- Without core/connection lengths, the code uses the HR series stiffness KF·E·Asc/L. The truss area KF·Asc (33.75) is used only for stiffness; Asc (22.5) is the yielding area.

Table C3.3 BRB row: a = b = 13.3Δy, c = 1.0, and IO/LS/CP = 3 / 10 / 13.3 Δy (plastic). These are stored as total deformations, (1 + n)Δy, because the truss monitor records total deformation. That is consistent and slightly conservative, since the true plastic part is total − F/K < total − Δy.

Ex4 hand calculation:
- Pysc = 38 × 22.5 = 855 kip.
- ωRy = 1407.6 / 855 = 1.646, so Ry = 1.21 with ω = 1.36.
- Q_CE = 1035 kip and ωQ_CE = 1407.6 kip, which equals the HR adjusted T.
- Δy = 1035 × 394.4 / (29000 × 33.75) = 0.417 in.

The ω / β fallback of 1.3 / 1.1 is flagged as the C3.3a.1 linear-analysis value. The single-element Steel4 and Hysteretic tests check yield, ω, βω and strength loss beyond b on a real OpenSees element.

#### NL-03 — EBF links: CORRECT (with the flagged gaps)

Link strength and classification:
- Vp = 0.6·Fye·Alw, with Alw = (d − 2tf)·tw and Fye = Ry·Fy. This follows AISC 342 C2.3a.2, which points to Vpe of Seismic F3.
- ρ = e / (M_CE / V_CE). The link is shear-controlled for ρ ≤ 1.6 and flexure-controlled for ρ ≥ 2.6 (C3.1 wording).

Link stiffness:
- Ks = G·d·tw / e.
- The series stiffness 1/(12EI/e³) + e/(G·As) equals 12EI / (e³(1 + η)), which is Commentary Eq. C-E2-1.
- The spring is a zeroLength on global Z between coincident nodes, so its deformation is the shear deformation only. The rigid-body link rotation is excluded, so γp = Δp / e.
- The Table C2.4 limits are 0.005 / 0.14 / 0.16 rad × e, and αh = 6 %.

W14X74, e = 36 in, hand calculation:

| Quantity | Value |
|---|---|
| Alw | 12.63 × 0.45 = 5.684 in² |
| Vp | 187.6 kip |
| Mp | 6930 kip-in |
| 1.6 Mp/Vp | 59.1 in |
| ρ | 0.975 → shear-controlled |
| Ks | 1988 kip/in |
| Ke | 1489 kip/in |
| Δy (spring) | 0.094 in |
| CP | 5.76 in |

These match `test_link_specs_ex8_W14X74`. `test_single_link_push_vs_Vp` checks the elastic stiffness against Eq. C-E2-1 on a real element.

Flagged gaps:
- E2.4c (PUF/Pye > 0.6) is not implemented.
- The C2.4 footnotes are illegible in the corpus, so the shear-flexure interpolation mirrors the C2.2 footnote.

CONCERN (conservative): the fibre end regions of a shear link get flexural limits of 1e-5 rad. Any numerical flexural yielding in the link fibres therefore gives a very large D/C.

#### NL-07 — RBS: CORRECT

- Z_RBS = Zx − 2c·tf·(d − tf) (AISC 358-22 Eq. 5.7-4, as printed in the corpus). W36X182 with c = 2.875 gives 479.7 in³.
- The circular-cut depth uses R = (4c² + b²) / 8c.
- The AISC 358 default geometry is c = 0.25bf, a = 0.625bf, b = 0.75d. It is flagged.
- Lb is the larger of the member and bracing values, which is conservative.

CONCERN (modelling, pre-existing approach): beams run between centreline nodes with no rigid offsets. The cut is therefore placed `a` from the node, not from the column face. For the RBS fibre beam this under-estimates beam strength by about 5 % for W14 columns with L = 30 ft. The IMK path puts Z_RBS at the node, which under-estimates it more. Lower beam strength means lower column / Ω demands.

#### NL-09 — acceptance at δt: CORRECT

- `step_at` returns the first step with u ≥ δt, or None. It no longer clamps.
- If the target is not reached, the result is TARGET NOT REACHED: acceptable False, D/C None, and the last-converged values appear only as a labelled diagnostic.
- `nsp_ok` requires the target to be reached. This matches the 7.4.3.3.1 corpus text.

#### NL-11 — memory leak: CORRECT

- Test and algorithm objects are re-issued only when they change.
- An integrator change does `wipeAnalysis` and a rebuild, which keeps the domain state.
- The memory test measures RSS on a 4.5k-DOF model.

#### NL-12 — aligned drift: CORRECT

- Each top-level slave is paired with the same (x, y) on the nearest lower level that has it, or with the base. Double-height pairs use their own height.
- The convex-hull reduction is exact for rigid diaphragms, because the difference of affine fields is affine.
- Drift is evaluated every analysis step.
- The set-back test reproduces the hand drifts 1.2/120 and 0.8/120.

#### NL-14 — C2.2 / C3.6 row selection: CORRECT

- Rows are chosen per section from AISC 341-22 Table D1.1b, as read in the corpus:
  - flanges: 0.30 / 0.38 √(E/Fye);
  - webs, moment frames (case 11): 2.5 / 5.4 (1 − Ca)^2.3;
  - webs, other members: 2.45(1 − 1.04Ca) and 3.76(1 − 3.05Ca); above Ca = 0.113, 2.26(1 − 0.38Ca) and 2.61(1 − 0.49Ca), with a floor of 1.56.
- Line 3 interpolates per element and takes the lowest value. There is no "moderately ductile" row.
- W14X109 hand check: λhd = 6.89, λmd = 8.73, bf/2tf = 8.49, so t = 0.87.

#### NL-15 — BPON levels: CORRECT

The mapping matches Table 2-5:
- RC I/II: LS / CP;
- RC III: DC (2-B) / LtdS (4-D);
- RC IV: IO (1-A) / LS (3-D).

The intermediate levels:
- DC = min(½(IO + LS) from Table 2-1, the a point from 7.5.3.2.2). Taking the lower is conservative.
- LtdS = ½(LS + CP).
- DC / LtdS are None when the level is not evaluated or the target is not reached.

#### NL-16 — NSP idealisation and coefficients: CORRECT

The implementation matches 7.4.3.2.5:
- (Vd, Δd) is on the curve at min(δt, Δ at Vmax);
- Ke is the secant at 0.6Vy;
- the areas balance up to Δd;
- Vy ≤ Vmax.

The equations match ASCE 41-23:
- Te from Eq. 7-28;
- C1 from Eq. 7-30, evaluated at 0.2 s below 0.2 s and 1.0 above 1.0 s;
- C2 from Eq. 7-31, 1.0 above 0.7 s;
- μstrength from Eq. 7-32 with Cm;
- μmax from Eq. 7-33 with h = 1 + 0.15 ln Te;
- αe from Eq. 7-34, with λ taken on SX1 of BSE-2N;
- Cm from Table 7-4, with T > 1 s giving 1.0.

Treating BRBF as "Other" (1.0) is conservative.

Independent hand calculation (`nl16_hand.py`): bilinear curve with Ke = 200 and Vy = 500, T = 0.5 s, SDS = 1.0, SD1 = 0.45, W = 1000, two storeys, φ = (0.5, 1).
- Hand: Sa = 0.9, μ = 1.8, C0 = 1.2, C1 = 1.0533, C2 = 1.0032, δt = 2.7925 in.
- Code: 2.7924 in.

#### NL-17 — crawl abort: CORRECT

- Converged fallback micro-steps shrink dt instead of aborting.
- Only an explicit budget ends a record, and then as "incomplete", never as NC.
- Legacy "crawl abort" results read as incomplete.

#### NL-18 — Chapter 16 element checks: CORRECT, with the group aggregation now fixed (8be1a4a)

The force-controlled column check follows 16.4.2.1 as printed in the corpus:
- (1.2 + 0.12SMS)D + 0.5L + 1.3Ie(Qu − Qns), and the counteracting case (0.9 − 0.12SMS)D;
- D is split from Qns by ΣD / (ΣD + Σ0.5L).

Combination with flexure:
- AISC 360 H1-1 is identical to AISC 342 C3-9 with C3-12 / C3-13.
- Deformation-controlled flexure uses m = 1 and M_CE, as AISC 342 C3.4b ("shall also satisfy Equations C3-9, C3-10, and C3-11 … m taken as unity") requires.
- C3-10 is checked.
- Hand check of a test case: Pr = 650.8, DC_axial 0.6508, H1-1a, tension 339.8.

CONCERN (method): the suite D/C is the 16.4 mean of the per-record concurrent interaction, not the interaction of the mean demands. The non-concurrent envelope is reported alongside. This is defensible, but the engineer should know it.

#### NL-19 — column P-M reduction, AISC 342-22 Eqs. C3-5 / C3-6: CORRECT

- The expression is min(1 − P/2Pye, 9/8(1 − P/Pye)).
- The two lines cross at P/Pye = 0.2. For κ < 1, C3-6 would exceed C3-5 on [0.2κ, 0.2), so using the minimum (κ = 1) is the conservative reading.
- Values: 0.95 at P/Pye 0.10 and 0.7875 at 0.30.
- A legacy 1.18(1 − P/Py) entry is replaced and flagged.

### Open concerns

1. **NLRHA hinge peak sampling.** `nlrha/run.py` updates `peak_def` / `signed_def` only at viewer frames, every `rec_every`·dt_max = 0.1 s, not every step. Hinge, link and BRB peaks can be under-estimated slightly. Drift and column forces are sampled every step. This is pre-existing. Suggested fix: update the peaks every step and keep the frames for the viewer.
2. **Buckling-brace limits.** Table C3.4 IO / LS / CP are printed as plastic deformation, but they are compared with the total brace deformation. This is conservative by about Δc (an NL-04 area, not re-verified here).
3. **Pushover report table.** The group row now shows the governing member's IO/LS/CP next to `theta_pl_max` (the largest deformation). θmax / CP can therefore differ from the printed D/C on mixed groups. `theta_at_governing` is in the package.
