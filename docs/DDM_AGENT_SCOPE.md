# Steltic DDM Agent — Scoping Document

**Direct Design Method (system-based design by advanced analysis) for Steltic-designed steel buildings, in OpenSees, delivered as a fourth bot in the Steltic Grok Bot harness.**

Prepared 3 September 2026 · Status: scoping draft for review · Author: Claude (for Mike / Steltic)

---

## 0. Summary

Steltic's HR Steel App and CFS Steel App produce a *member-based* design to AISC 360/341 or AISI S100/S240/S400: a linear-elastic OpenSees model gives demand envelopes, and the LLM agent derives every capacity by hand from the RAG. The proposed **DDM Agent** takes that finished design package and answers a different question: *what is the ultimate load-carrying capacity of the building as a system?* It rebuilds the same building as a geometrically and materially nonlinear model with imperfections (GMNIA), scales each governing factored load combination by a load factor λ until collapse, and reports the ultimate load factor λ_u together with the collapse mechanism. The DDM check of Rasmussen and co-workers is then simply **φ_s · λ_u ≥ 1.0**, where φ_s is a *system* resistance factor calibrated by system reliability (0.85–0.90 for hot-rolled frames at the ASCE 7 target reliability, per Zhang, Shayan, Rasmussen & Ellingwood 2016).

The scope is deliberately staged: **hot-rolled first**, because Steltic's hot-rolled package already contains a complete, section-resolved 3D OpenSees model (`model_opensees.py` + `cfg.py`), and because the Rasmussen DDM literature for hot-rolled and compact-HSS frames is mature. **Cold-formed second**, portal frames before stud-wall buildings, because the CFS Steltic engine is a pure-Python shear-spring solver with no member-level 3D model, and because DDM calibration for CFS stud-wall buildings does not yet exist in the literature.

A proof-of-concept spike (`ddm_gmnia_spike.py`, attached) built in openseespy 3.7.1 during scoping confirms the modelling recipe: a fibre W14X90 column with the Galambos–Ketter residual-stress pattern and an L/1000 bow reproduces the AISC E3 nominal column curve within −8 %/+8 % from KL/r = 40 to 160, and a two-storey fibre frame with H/500 out-of-plumb runs to its collapse load factor under displacement control without solver intervention.

Recommended first test buildings: hot-rolled **Ex18** (R = 3, wind-governed, 8 storeys), **Ex1** (SCBF 5), **Ex4** (BRBF 3), **Ex2** (SMF 9), **Ex31** (R = 3 gable warehouse) and **Ex35** (3-tier braced platform); cold-formed **CFS_Ex16** (single-span portal), **CFS_Ex17** (two-span portal), **CFS_Ex28**, **CFS_Ex30**, then the strap-braced wall buildings **CFS_Ex5 / Ex2 / Ex8** as the first stud-wall targets. Section 9 gives the full triage.

---

## 1. What the Direct Design Method is (the Rasmussen et al. body of work)

### 1.1 The idea

Conventional design (AISC 360 Ch. C–H, AISI S100 Ch. C–H) is *member-based*: an elastic or second-order elastic analysis produces member forces, and each member is then checked against a code capacity that implicitly contains buckling, imperfections and residual stresses through column curves and interaction equations. The **Direct Design Method (DDM)** — the term coined by the University of Sydney Structures Group for design-by-advanced-analysis provisions — replaces the member checks with a single system check. A geometric and material nonlinear analysis with imperfections (GMNIA) of the *nominal* structure gives the ultimate load factor λ_u on a factored load combination; the design check is

> **φ_s · R_n ≥ Σ γ_i Q_n,i**, i.e. in load-factor form **φ_s · λ_u ≥ 1.0**

where R_n = λ_u · Σγ_iQ_n,i is the nominal *system* resistance and φ_s is a *system* resistance factor. φ_s is calibrated so that the whole structure — not each member — achieves a target reliability index β_T (3.0 in the US LRFD framework, 2.5 in AS/NZS 1170.0, 3.8 for Eurocode CC2), using Monte Carlo / Latin-Hypercube simulation of frames with random yield stress, modulus, section dimensions, imperfections and residual stresses, and FORM at system level.

Because the analysis captures member interaction, load redistribution after first yield, and the actual sway/buckling behaviour, the DDM (a) removes the effective-length and interaction-equation approximations, (b) gives uniform reliability across typologies, and (c) typically produces lighter, more redundant structures — the stainless-steel case studies report lighter frames than Eurocode/ASCE member design (Arrayago, Rasmussen, Zhang & Real 2022).

### 1.2 The modelling protocol the calibrations assume

The φ_s values are only valid if the nominal GMNIA is done the way the calibration studies did it. From the Sydney papers and the open-access Arrayago–Rasmussen–Zhang preprint:

| Ingredient | Hot-rolled I-sections (Zhang et al. 2016; Shayan et al. 2014a,b) | Cold-formed HSS / portal frames (Liu et al. 2018; Sena Cardoso et al. 2019) |
|---|---|---|
| Analysis | Second-order inelastic ("advanced") analysis to collapse; arc-length / Riks or displacement control | Same; shell-element models (ABAQUS S4R) for portal frames; beam models for compact HSS space frames |
| Material | Nonlinear stress–strain with yield plateau and hardening, nominal Fy; elastic–perfectly-plastic acceptable for I-sections | Measured/enhanced corner strength for cold-formed; two-stage stress–strain |
| Residual stress | Probabilistic model (Shayan, Rasmussen & Zhang 2014b); ECCS / Galambos–Ketter (Lehigh) pattern as the nominal, σ_rc ≈ 0.3 Fy at flange tips | Membrane + bending pattern for cold-formed HSS (Liu, Rasmussen & Zhang 2018): e.g. 0.37 Fy corners / 0.63 Fy flats in the stainless work |
| Frame out-of-plumb | H/500 (AS 4100 / AISC 360 erection tolerance) | H/500 (US/AU), 1/200 (EC) |
| Member out-of-straightness | L/1000, half-sine | L/1000, half-sine; plus local plate imperfections for thin-walled sections |
| Imperfection direction | The *most critical* combination of out-of-plumb and out-of-straightness directions is found and adopted for the nominal model (Shayan et al. 2014a; Arrayago & Rasmussen 2022) | Same |
| Joints | Rigid/pinned as designed; semi-rigid via connection models (Wan thesis) | Bolted moment connections with measured stiffness |
| Ultimate load | Peak of the load–deformation curve of the whole frame | Same |

### 1.3 System resistance factors reported so far

| Study | Structure class | φ_s (US framework, β_T ≈ 3.0) | Notes |
|---|---|---|---|
| Zhang, Shayan, Rasmussen & Ellingwood 2016 (JCSR 123, Parts I & II) | Planar hot-rolled steel frames, gravity | **≈ 0.85–0.90** | Higher value for redundant frames failing by a ductile plastic mechanism; lower for frames whose failure involves member instability / low redundancy |
| Zhang, Liu, Ellingwood & Rasmussen 2018 (ASCE JSE 144(3)) | Planar gravity frames designed by the **AISC 360 Appendix 1 inelastic method** | evaluates β achieved by App. 1 | The direct US-code hook for the hot-rolled DDM agent |
| Liu, Rasmussen & Zhang 2016 (Structures 8); Liu, Zhang, Rasmussen & Yan 2019 (JCSR 157) | 3D steel frames, gravity; gravity + wind | system limit-state criterion for wind | 3D and wind extensions |
| Liu, Zhang & Rasmussen 2018 (Eng. Struct. 166) | Space frames, cold-formed compact HSS | **≈ 0.80–0.90** | Lower values for non-ductile sway failure modes |
| Sena Cardoso, Zhang, Rasmussen & Yan 2019 (Eng. Struct. 182) | Cold-formed steel portal frames | **≈ 0.85–0.95** (β_T 2.5–3.0) | Shell-element GMNIA; the direct analogue of Steltic CFS_Ex16/17/27/28/30 |
| Sena Cardoso, Rasmussen & Zhang 2019 (TWS 141, Parts I & II) | Steel storage racks | calibrated; adopted in AS 4084 | Racks are out of Steltic CFS scope, but the protocol transfers |
| Arrayago, Rasmussen & Zhang 2022–23 | Stainless steel frames (gravity, gravity+wind, SLS) | 0.95 (US), 0.90 (AU), γ_M,s = 1.15 (EC) | Shows the framework is material-agnostic |
| Wang, Zhang, Rasmussen et al. 2020 (Eng. Struct.) | Support scaffolds | adopted in AS 3610.2 | |

The published DDM provisions now sit in AS/NZS 4600 (cold-formed), AS 4084 (racks) and AS 3610.2 (scaffolds). In the US framework the nearest code vehicle is **AISC 360-22 Appendix 1, §1.3 Design by Inelastic Analysis** (ductility limits on Fy, compactness and unbraced length; imperfections modelled directly or by notional loads; residual-stress/partial-yield effects modelled directly or by stiffness reduction) — the DDM agent should retrieve and cite the exact §1.3 text through the Query file manager rather than rely on this summary. There is **no** AISI equivalent for stud-wall buildings; AISI S100 Chapter C and Appendix 2 (DSM) provide the member-level ingredients only.

> Paywall note. The φ_s ranges above are taken from the Sydney Structures Group project page, the open-access Arrayago–Rasmussen–Zhang preprint (which summarises the earlier calibrations), and the paper abstracts. Before the bot ships, the exact φ_s tables from Zhang et al. 2016 Part II, Liu et al. 2018 and Sena Cardoso et al. 2019 should be transcribed from the full papers into `ddm_engine/phi_s.py` with their conditions (redundancy class, ductility class, live-to-dead ratio, β_T).

### 1.4 Reading list (in the order the build team should read them)

1. Zhang H., Shayan S., Rasmussen K.J.R., Ellingwood B.R. — *System-based design of planar steel frames, I: Reliability framework*, JCSR 123 (2016) 135–143.
2. Zhang H., Shayan S., Rasmussen K.J.R., Ellingwood B.R. — *System-based design of planar steel frames, II: Reliability results and design recommendations*, JCSR 123 (2016) 154–161.
3. Shayan S., Rasmussen K.J.R., Zhang H. — *On the modelling of initial geometric imperfections of steel frames in advanced analysis*, JCSR 98 (2014) 167–177.
4. Shayan S., Rasmussen K.J.R., Zhang H. — *Probabilistic modelling of residual stress in advanced analysis of steel structures*, JCSR 101 (2014) 407–414.
5. Zhang H., Liu H., Ellingwood B.R., Rasmussen K.J.R. — *System reliabilities of planar gravity steel frames designed by the inelastic method in AISC 360-10*, ASCE J. Struct. Eng. 144(3) (2018) 04018011.
6. Liu W., Rasmussen K.J.R., Zhang H. — *Systems reliability for 3D steel frames subject to gravity loads*, Structures 8 (2016) 170–182.
7. Liu W., Zhang H., Rasmussen K.J.R., Yan S. — *System-based limit state design criterion for 3D steel frames under wind loads*, JCSR 157 (2019) 440–449.
8. Liu W., Rasmussen K.J.R., Zhang H. — *Modelling and probabilistic study of the residual stress of cold-formed hollow steel sections*, Eng. Struct. 150 (2018) 986–995.
9. Liu W., Zhang H., Rasmussen K.J.R. — *System reliability-based Direct Design Method for space frames with cold-formed steel hollow sections*, Eng. Struct. 166 (2018) 79–92.
10. Sena Cardoso F., Zhang H., Rasmussen K.J.R., Yan S. — *Reliability calibrations for the design of cold-formed steel portal frames by advanced analysis*, Eng. Struct. 182 (2019) 164–171.
11. Sena Cardoso F., Rasmussen K.J.R., Zhang H. — *System reliability-based criteria for the design of steel storage rack frames by advanced analysis, Parts I & II*, Thin-Walled Structures 141 (2019) 713–724, 725–739.
12. Sena Cardoso F., Rasmussen K.J.R. — *Finite element (FE) modelling of storage rack frames*, JCSR 126 (2016) 1–14.
13. Arrayago I., Rasmussen K.J.R. — *Influence of the imperfection direction on the ultimate response of steel frames in advanced analysis*, JCSR 190 (2022) 107137.
14. Arrayago I., Zhang H., Rasmussen K.J.R. — *Simplified expressions for reliability assessments in code calibration*, Eng. Struct. 256 (2022) 114013.
15. Arrayago I., Rasmussen K.J.R., Zhang H., Real E. — *Direct design of stainless steel frames: recommendations and case studies*, ce/papers 5(4) (2022) 506–514; and the companion gravity / gravity-wind / SLS reliability papers (Structural Safety 2022; JCSR 2022).
16. Wang C., Zhang H., Rasmussen K.J.R., Reynolds J., Yan S. — *System reliability-based limit state design of support scaffolding systems*, Eng. Struct. (2020) 110677.
17. Shayan S., Rasmussen K.J.R. — *A model for warping transmission through joints of steel frames*, TWS 82 (2014) 1–12 (relevant to the CFS single-channel portal, CFS_Ex27).
18. Wan W. — *System reliability of semi-rigid steel frames designed with Direct Design*, PhD thesis, University of Sydney (open access) — the most recent consolidated statement of the protocol and of PR-joint treatment.

For the CFS stud-wall phase, the modelling (not the DDM calibration) literature is the JHU CFS-NEES body of work: Leng, Schafer & Buonopane (OpenSees whole-building CFS models), Buonopane et al. (fastener-based shear-wall models), Peterman & Schafer (wall tests), and the 2021 JSE high-fidelity WSP shear-wall models.

---

## 2. What Steltic hands the DDM agent today

### 2.1 Hot-rolled package (`github.com/Steltic/steltic`)

The download zip (`steltic/bundle.py`) and the job folder `jobs/<name>/` contain everything the DDM agent needs, and nothing has to change in the HR app to enable Phase 1:

| Artefact | What it is | DDM use |
|---|---|---|
| `cfg.py` | The agent-written design model: `cfg = dict(...)` + `custom_build(cfg, transf)`; kip-inch; `system`, `seis`, `wind`, loads (psf), `model = {bases, joints, gravity}`, `releases`, `present` footprint | Re-exec with `engine3d.build()` to regenerate the elastic model, footprint, masses; `design_pipeline.combos(cfg)` regenerates the exact ASCE 7-22 §2.3 combination set (19–35 combos incl. ρ, Ω₀, 100/30, ±5 % torsion, wind) |
| `model_opensees.py` | Flat replay of every `ops.*` call: nodes (`ntag = k·1e5 + i·100 + j`), fixities, masses, 3 `geomTransf` tags (1/2 = column strong-axis Y/X with `PDelta`, 3 = beam `Linear`), `elasticBeamColumn` with A, E, G, J, Iy, Iz + native `-releasey/-releasez`, `Truss` braces with `Elastic` material, `rigidDiaphragm(3, master, …)` | **Primary ingestion point**: parse line-by-line into a neutral model JSON (topology, section labels, releases, bases, diaphragm sets) without importing the engine |
| `model_static.py` | Same, with beams subdivided (nseg = 6) and `eleLoad -beamUniform` gravity | Source of the distributed gravity loads and two-way tributary line loads |
| `design/calc_package.json` | `members[]` (one per role×section group: `lateral_col/gravity_col/floor/roof/brace`, full AISC props, demand envelope, governing combo, agent-filled limit state / capacity / D/C), `connections[]`, `capacity_design` (SCWB, expected brace strengths), `framework_screen` | Section-to-role map; member-based D/C to compare against DDM utilisation; the natural home for a new top-level **`ddm_analysis`** block, which `report.py` renders automatically through `_extra_blocks_section` |
| `design/member_schedule.csv` | Per-element tag, section, length, P/M/V envelope | Element-tag ↔ section join |
| `report.html`, `viewer_3d.html` | Deliverables | The DDM report is a sibling, `ddm_report.html`, plus figures inserted into the main report on re-render |

Analysis fidelity of the source model (verified by reading the engine): linear-elastic `elasticBeamColumn` throughout; braces are linear `Truss` elements (gross A, no buckling); P-Δ via `geomTransf PDelta` on columns only (beams `Linear`, no Corotational anywhere); **no** Direct Analysis Method stiffness reduction, **no** notional loads, **no** fibre/plastic-hinge/imperfection/pushover capability; rigid diaphragms with all mass on master nodes; bases fixed or pinned; joints rigid or native-released. The section database `aisc_shapes.csv` (2 299 shapes: W, HSS, WT, L, 2L, C, MC, S, HP, M, MT, ST, PIPE) carries A, Ix, Iy, J, Zx, Zy, Sx, Sy, rx, ry, d, tw, bf, tf, Cw, rts, ho — enough to build fibre sections (fillets/k-area not available; the spike ignores them, which biases A and I 1–3 % low).

### 2.2 Cold-formed package (`github.com/Steltic/steltic_cfs`)

Materially different, and this drives the phasing:

* **Wall-path buildings** (shear walls, strap-braced walls, SBMF, gypsum): the "model" is a per-line stack of 1-DOF secant shear springs, `K = ΣV_seg/δ_seg` from the four-term S400 deflection (bending of chords, panel shear G', fastener slip, anchorage). The OpenSees emitter is a 1-D `zeroLength` chain used only as an equivalence check. The building's mechanical identity is `wall_props = {chord_area_in2, Gp_kip_in, en_in, k_anchor_kip_in}` plus segment lengths per storey. There are **no members, no straps as elements, no diaphragm elements, no eigen analysis**. Flexible-diaphragm tributary distribution with a ±5 % width shift replaces torsion. Seeded package arrays: `wall_lines`, `holddowns`, `studs` (one typical bearing stud), `collectors`; the agent authors everything else (tracks, joists, headers, chord studs, straps, connections) as free-form JSON blocks. The package file is `design/calc_package_cfs.json` (not `calc_package.json`), and there are no CSVs.
* **Portal-path buildings** (portal frames, canopies, purlin/girt components): a planar direct-stiffness `Frame2D` with A and Ix from `cfs_sections` (built-up back-to-back or single channel), nodes at purlin/girt stations, 0.8E + 0.002 ΣR notional load + a string-type geometric stiffness re-solve for P-Δ, and a decoupled effective-stiffness iteration (EA from Ae(f), EI from I_eff(f) — S100 Appendix 1 EWM, **no DSM**). A `Frame2D`-equivalent OpenSees emitter exists (`ndm 2`, `Linear` transf, elastic). Package: two member slots (`frame-col`, `frame-raf`), knee/apex connection slots, base anchorage, purlin/girt/strap schedule slots, a torsion companion table for single channels.
* Racks (CFS_Ex20–24) were removed from scope on 2026-07-30; the briefs are tombstones.
* The section library `cfs_shapes.csv` (408 SFIA S/T/F/U designators) carries A, Ix, Iy, J, Cw, xo, ro, β, Lu and tabulated effective properties; `cfs_sections.gross_props()` computes gross and sectorial properties from the thin-wall midline, and `eff_width`/`effective_area`/`effective_Ix` implement EWM. This is enough to build DSM signature-curve inputs and effective-fibre laws, but nothing nonlinear exists.

The CFS explainer already names the missing tier the DDM agent would fill: *Tier 2 — "nonlinear section response: the member's stiffness degrades progressively as local buckling develops … run incrementally rather than in one elastic step … an independent system-level check on a design near its limits"* — described in `frontend/analysis_fidelity_explainer.md` and `cfs_systems.TIERS[2]`, but unimplemented.

### 2.3 The Grok Bot harness (`github.com/Steltic/steltic_grokbot`)

Three bots: **Query file manager** (owns the licensed PDFs → Docling conversion → verbatim-excerpt retrieval; `scripts/`, `engineering_rag_phase2/` OpenSees + worked-example chunks), **HR Steel App** and **CFS Steel App** (each bootstrapped with two prompts: load `Skill_querying_PACKAGED.md`, then clone the app repo and follow `contract/AGENT_START.md`). Design bots send one JSON retrieval plan per wave (`plan_id, source_job, queries[{qid, doc, type, query, purpose, want_commentary, context_neighbors}]`), never invent citations, and may only take design values from `corpus=specification, part=standard`. Canonical doc stems: `AISC_360_22`, `AISC_341_22`, `AISC_358_22`, `ASCE7`, `AISI_S100`, `AISI_S240`, `AISI_S400_20`, plus non-authoritative `examples` / `opensees` / `opensees_documentation`. The DDM bot slots into this pattern as a fourth bot with its own packaged skill (Section 7).

---

## 3. Scope statement for the DDM Agent

### 3.1 Inputs

1. **The design brief** (the same `.txt` the HR/CFS bot received — e.g. `test_buildings/Ex1_SCBF_5levels.txt`) for building identity, system, load basis and any explicit DDM instructions.
2. **The Steltic output package** for that building (the download zip): `cfg.py`, `model_opensees.py`, `model_static.py`, `design/calc_package.json` (`calc_package_cfs.json` for CFS), `report.html`. The package is the *design of record* — the DDM agent must not redesign; it analyses what was designed.
3. **DDM run options** (defaults in brackets): target reliability [β_T = 3.0, US framework]; φ_s policy [table lookup, Section 5.5]; imperfection policy [H/500 + L/1000, worst direction search]; residual-stress model [Lehigh for W, membrane+bending for HSS]; load application [proportional λ on the full factored combination]; which combinations [all gravity combos + governing ±X/±Y lateral combos, wind and seismic-pattern].

### 3.2 Outputs

**A. "DDM System Capacity Report"** (`ddm_report.html`, self-contained, MathJax like the Steltic report):

1. Design basis and provenance: which Steltic job, hash of `cfg.py`/`model_opensees.py`, sections as designed.
2. Nominal GMNIA model description: element types, fibre discretisation, material laws, residual stresses, imperfection magnitudes and the direction search, boundary/diaphragm idealisation, solver.
3. **System capacity table**: for each analysed combination — λ_u, φ_s used and why, φ_s·λ_u ≥ 1.0 pass/fail, roof/storey drift at peak, collapse mechanism class (plastic mechanism / member instability / sway instability / brace buckling), first-yield λ, plastic-hinge / yielded-fibre map at peak.
4. **Member utilisation at collapse** vs the member-based D/C in `calc_package.json` — which members govern the system and how much reserve the member design left on the table.
5. Sensitivity: λ_u for the four imperfection directions; λ_u without residual stresses; λ_u with rigid vs. pinned gravity connections when the brief left them as an assumption.
6. Seismic supplement (R > 3 systems only, Section 6.3): pushover overstrength Ω = V_u/V_design vs Table 12.2-1 Ω₀; λ_u on the Ω₀-level combinations; drift at collapse vs Cd-amplified design drift.
7. Load–deformation figures (λ vs roof drift, λ vs vertical deflection), deformed shape at peak, hinge map.
8. QA block: convergence log, peak detection, energy/equilibrium checks, comparison of the GMNIA model's elastic stiffness to the Steltic elastic model (periods must match within ~5 % before imperfections are applied — this is the model-transfer gate).
9. Grounding: every provision cited came back verbatim from the Query file manager (AISC 360-22 Appendix 1 §1.3, §C2.2, Table B4.1; AISI S100 Ch. C, App. 2), plus the literature basis for φ_s.

**B. `ddm_analysis` block written into `design/calc_package.json`** (schema in Section 5.7) so that re-rendering `report.build_report(name)` shows a "System capacity by Direct Design Method" section in the main Steltic report.

**C. The GMNIA model as a deliverable**: `model_gmnia.py` (standalone openseespy replay, same style as `model_opensees.py`) and `ddm_results.json`.

### 3.3 Non-goals (stated so the bot never pretends)

* It does not replace AISC 341 / AISI S400 ductile-detailing, SCWB, protected-zone or connection-qualification checks. The DDM capacity is a *strength* statement under static (gravity, wind, ELF-pattern) loading.
* It does not perform nonlinear response-history analysis (ASCE 7-22 Ch. 16) — Ex29 stays scoped as before.
* It does not model connections beyond rigid / pinned (PR joints are bounded, as in the HR contract), nor composite floor action (bare steel = lower bound, as in the HR contract).
* It does not calibrate new φ_s values; where no calibration exists (stud-wall CFS buildings, seismic-detailed systems) it reports λ_u with a declared, conservative φ_s and an explicit "uncalibrated" label.

---

## 4. Analysis approach in OpenSees — hot-rolled (Phase 1)

### 4.1 Model transfer

`ddm_engine/ingest.py` parses `model_opensees.py` (or re-executes `cfg.py` through `engine3d.build` when the repo is importable) into a neutral model:

```
nodes{tag:(x,y,z)}, bases{tag:'fixed'|'pinned'}, diaphragms{level:[slave tags], master},
members[{tag, kind:col|beam|brace, section:'W14X90', n1, n2, strong_dir, relz, rely}],
masses, footprint(present), levels z[]
```

Node tags decode to `(i, j, k)`; `calc_package.members[].inputs.role` maps each element to `lateral_col / gravity_col / floor / roof / brace`; `design_pipeline.combos(cfg)` regenerates the factored combinations; `model_static.py` supplies beam UDLs (dead, live, roof, cladding line loads) so gravity is applied as distributed loads, not lumped.

**Transfer gate:** before imperfections, residual stresses or plasticity are switched on, the rebuilt model must reproduce the Steltic elastic model's first three periods and ELF drifts within 5 %. This guards against orientation, release and unit errors — exactly the silent bugs the HR contract warns about.

### 4.2 Element and section modelling

| Steltic element | DDM element |
|---|---|
| `elasticBeamColumn` column | `forceBeamColumn`, 3-D `Corotational` transform, ≥ 4 elements per storey (so the L/1000 bow is representable), 5 Gauss–Lobatto points, **fibre section** built from d, tw, bf, tf; 8 × 2 flange fibres and 12 × 1 web fibres per the spike (~44 fibres); `Steel01`/`Steel02` with Fy nominal (50 ksi A992), E = 29 000 ksi, 0.1 % kinematic hardening; torsional stiffness GJ added as `-GJ` |
| `elasticBeamColumn` beam | Same, ≥ 4 elements per span; gravity `eleLoad -beamUniform` per `model_static.py`; lateral-torsional buckling is *not* captured by a fibre beam — the DDM reports LTB reserve by an explicit Cb/Lb check against the yielded moment, or the beam is declared braced by the deck (the HR contract's `Lb_in` convention) |
| Native `-releasey/-releasez` | Force-based elements have no native releases: split the end node and `equalDOF` translations (and the non-released rotation), i.e. a true pin. Rigid joints stay continuous |
| `Truss` brace (SCBF/OCBF/EBF) | Fibre `forceBeamColumn` with ≥ 6 sub-elements and an L/1000 (SCBF: L/500 optional sensitivity) out-of-plane bow so **brace buckling is captured** — this is what turns the DDM from a moment-frame tool into something meaningful for braced buildings; gusset rotational restraint idealised as pinned in-plane, with an out-of-plane end restraint option |
| BRB (BRBF) | `corotTruss` with a `Steel02` core (yielding, no buckling) — the cleanest system to run first |
| EBF link | Fibre beam with shear-yielding fibre section or an aggregated `Hysteretic` shear spring at the link ends (Phase 1b) |
| `rigidDiaphragm` | Retained (rigid, as designed). Caveat logged: rigid diaphragm suppresses in-plane beam axial, so collector forces are still hand-added per the HR contract; option to replace with a stiff truss/quad diaphragm at Phase 1c |
| Bases | Fixed / pinned as declared in `cfg['model']['bases']` |

### 4.3 Imperfections and residual stresses

* **Storey out-of-plumb** ψ = H/500 (AISC 360-22 §C2.2a tolerance; AS 4100 same), applied as a rigid-body lean of each storey's nodes, in ±X and ±Y; for each lateral combination the direction *with* the lateral load governs; for pure-gravity combinations all four directions are run and the minimum λ_u is kept (Shayan et al. 2014a; Arrayago & Rasmussen 2022).
* **Member out-of-straightness** L/1000 half-sine, in the weak-axis direction for columns and braces (with the strong-axis direction run as a sensitivity), superimposed on the out-of-plumb.
* **Residual stresses**: Lehigh / Galambos–Ketter self-equilibrating pattern for W-shapes (σ_rc = 0.3 Fy at flange tips, linear to +σ_rt at the web junction, web uniform +σ_rt) as `InitStressMaterial` wrappers — implemented and validated in the spike; ECCS pattern selectable; Key–Hancock / Liu-Rasmussen-Zhang membrane+bending pattern for cold-formed HSS braces.
* **Notional-load alternative**: an option to replace out-of-plumb by N_i = 0.002 Y_i (AISC §C2.2b) for cross-checking the App. 1 route; the agent reports both when asked.

### 4.4 Load application and the collapse search

Default is **proportional loading**: every load in factored combination *C* is multiplied by λ; λ is driven by displacement control on the dominant DOF (roof drift for lateral combos, mid-span deflection or vertical shortening for gravity combos) with automatic step halving on non-convergence, `Newton → ModifiedNewton(-initial) → KrylovNewton` fallback, and arc-length (`ArcLength`) as the last resort for snap-through. λ_u is the peak; the run continues to 0.9 λ_u to characterise post-peak ductility (used to classify ductile vs non-ductile for φ_s).

For lateral combinations a second scheme, **gravity-then-lateral** (gravity at its factored level held constant, lateral scaled by λ), is offered because it maps onto the pushover/overstrength quantities engineers expect for seismic systems. The Rasmussen wind calibrations (Liu et al. 2019) used proportional loading; the report states which scheme produced which number.

### 4.5 What the spike showed (evidence)

`ddm_gmnia_spike.py` (openseespy 3.7.1, kip-inch), W14X90, Lehigh residual stresses, L/1000 bow, Corotational, 8 elements, 5 IPs, displacement control:

| KL/r | GMNIA / E3 nominal (strong axis) | GMNIA / E3 nominal (weak axis) |
|---|---|---|
| 40 | 1.014 | 0.971 |
| 80 | 1.040 | 0.923 |
| 120 | 1.032 | 0.957 |
| 160 | 1.080 | 1.026 |

This is the expected picture — the single AISC column curve sits between the strong- and weak-axis GMNIA results (SSRC curves 1/2 vs 2/3), and the −8 % weak-axis dip at KL/r = 80 is the classic residual-stress-dominated region. The two-bay two-storey fibre frame (W14X90 / W24X76, H/500) reached its peak in ~220 displacement steps with no manual intervention; H/500 reduced λ_u by ~1 % relative to the plumb frame for that (moment-frame, gravity-dominated) case, as the literature would predict. Runtime: seconds per column, ~10 s per planar frame; a 3-D 10-storey building with ~1 500 fibre elements is estimated at 2–10 min per combination on a laptop core, so the combination set must be pruned (Section 5.4).

---

## 5. Engine design — `ddm_engine/` (a sibling to `steel_engine/`)

```
ddm_engine/
  ingest.py          model_opensees.py / cfg.py -> neutral model JSON; role map from calc_package
  sections_fiber.py  W / HSS / channel fibre builders from aisc_shapes.csv & cfs_shapes.csv;
                     residual-stress patterns (Lehigh, ECCS, CF-HSS membrane+bending)
  imperfections.py   storey out-of-plumb, member bow, direction search, eigen-mode option
  model_gmnia.py     rebuild in openseespy (Corotational forceBeamColumn, pins, diaphragms,
                     BRB/brace/link idealisations); export model_gmnia.py replay
  loads.py           factored combos via design_pipeline.combos(cfg); UDLs from model_static;
                     proportional vs gravity-then-lateral schemes; combination pruning
  solver.py          adaptive displacement control / arc-length; peak + post-peak; mechanism
                     classification (yielded-fibre fraction per IP, brace buckling detection)
  phi_s.py           system resistance factor table (source, class, beta_T) + policy
  transfer_gate.py   elastic equivalence check vs Steltic model (periods, drifts)
  report_ddm.py      ddm_report.html + ddm_analysis block + figures
  cli.py             `ddm run <job_folder> [--combos ...] [--phi-policy ...]`
tests/               column-curve regression (the spike), planar benchmark frames from the
                     Zhang et al. 2016 set, Vogel frames, brace buckling vs E3, BRB truss
```

### 5.1 Section builders (`sections_fiber.py`)

W-shapes from d/tw/bf/tf (fillets ignored; optional k-area correction from the AISC k_des when the CSV is extended); HSS rectangular from b/h/t with 4 corner regions; PIPE; back-to-back channels and single channels for CFS portal frames (Phase 2). Each builder returns the material tag map so residual stresses can be toggled. Unit tests reproduce A, Ix, Iy against the CSV to within the fillet bias and assert self-equilibrium of the residual pattern (ΣσA = 0, ΣσAy = 0).

### 5.2 Imperfection engine (`imperfections.py`)

Returns a list of imperfection *cases* (±X, ±Y out-of-plumb × bow direction). Optional: scale the first elastic buckling mode (`eigen` on the geometric stiffness via a linear buckling helper) to H/500 — useful for irregular buildings where the critical sway direction is not obvious.

### 5.3 Solver (`solver.py`)

Wraps the openseespy loop: step control, convergence fallbacks, peak detection, post-peak, and per-step recording of λ, control displacement, storey drifts, yielded-fibre fraction per integration point, brace axial vs. its Euler load (buckling flag). Mechanism classification rules: *ductile plastic mechanism* if ≥ N hinges form before peak and post-peak slope is shallow; *instability* if peak occurs with < N hinges or a brace/column buckles first. This classification feeds φ_s.

### 5.4 Combination pruning (`loads.py`)

Running all 19–35 Steltic combinations through GMNIA is unnecessary. Default set: 1.4D; 1.2D+1.6L+0.5Lr; the two ±X and two ±Y seismic-pattern strength combos with the governing accidental-torsion sign (from the elastic results); the ±X/±Y wind combos when wind exists; the 0.9D±E / 0.9D±W uplift combos for braced systems. Ω₀ combos are run on request (seismic supplement). Typically 8–12 GMNIA runs per building, times the imperfection direction search (which is only repeated where direction is ambiguous).

### 5.5 System resistance factor policy (`phi_s.py`)

| Class | Condition (from the run) | Default φ_s (β_T = 3.0) | Source |
|---|---|---|---|
| HR-A | Hot-rolled frame, ductile plastic mechanism, redundant (≥ 3 hinges before peak, shallow post-peak) | 0.90 | Zhang et al. 2016 II |
| HR-B | Hot-rolled frame, failure by member/sway instability or low redundancy | 0.85 | Zhang et al. 2016 II |
| HSS-A/B | Cold-formed HSS braces govern (compact) | 0.90 / 0.80 | Liu et al. 2018 |
| CFS-P | Cold-formed portal frame, GMNIA with local/distortional effects represented | 0.85 (0.90 with shell-grade model) | Sena Cardoso et al. 2019 |
| CFS-W | Stud-wall building | 0.80 **uncalibrated — reported, not relied on** | none (research gap) |
| SEIS | Any R > 3 system on a seismic-pattern combo | report λ_u and Ω only; no φ_s pass/fail | Section 6.3 |

The bot must state the class, the value and the citation for each combination, and must not silently average.

### 5.6 Transfer gate (`transfer_gate.py`)

Elastic GMNIA model (no imperfections, elastic fibres) vs Steltic `model_opensees.py`: T1–T3 within 5 %, ELF roof drift within 5 %, base shear equilibrium within 0.5 %. Failure blocks the run and prints the likely cause (orientation, release, diaphragm membership).

### 5.7 `ddm_analysis` block schema (added to `calc_package.json`)

```jsonc
"ddm_analysis": {
  "method": "Direct Design Method (system-based design by advanced analysis)",
  "basis": ["Zhang, Shayan, Rasmussen & Ellingwood, JCSR 123 (2016) I & II", "AISC 360-22 App. 1 §1.3 (cited via Query file manager)"],
  "model": {"elements": "forceBeamColumn/Corotational, 5 IP, fibre", "material": "Steel01 Fy=50 E=29000 b=0.001",
            "residual_stress": "Lehigh 0.3Fy", "out_of_plumb": "H/500 worst direction", "out_of_straightness": "L/1000",
            "diaphragm": "rigid (as designed)", "joints": "as designed: rigid/pinned", "transfer_gate": {"T1_ratio": 1.02, "pass": true}},
  "combinations": [
    {"label": "1.2D+1.6L+0.5Lr", "scheme": "proportional", "lambda_u": 1.62, "lambda_first_yield": 1.21,
     "phi_s": 0.90, "phi_class": "HR-A", "check": "phi_s*lambda_u = 1.46 >= 1.0 PASS",
     "mechanism": "beam plastic mechanism, floors 2-4", "drift_at_peak": {"roof_in": 3.1},
     "governing_members": ["floor-W24X76 (utilisation 1.00 at peak)", "lateral_col-W14X311 (0.78)"]},
    {"label": "(1.2+0.2SDS)D+rhoE(+X)+0.5L", "scheme": "gravity-then-lateral", "lambda_u": 2.9,
     "phi_s": null, "phi_class": "SEIS", "overstrength_Omega": 2.9, "Omega0_table": 2.0,
     "note": "seismic supplement: system overstrength; no phi_s pass/fail"}
  ],
  "sensitivity": {"imperfection_directions": {"+X": 1.62, "-X": 1.63, "+Y": 1.70, "-Y": 1.69},
                  "no_residual_stress": 1.66, "pinned_gravity_joints": 1.58},
  "member_reserve_vs_member_design": [{"id": "lateral_col-W14X311", "DC_member_based": 0.91, "utilisation_at_collapse": 0.78}],
  "caveats": ["LTB of beams checked outside the fibre model", "collector axial not in rigid-diaphragm model", "..."]
}
```

---

## 6. Seismic systems: what the DDM can and cannot say

Most Steltic HR briefs are SDC D seismic systems (SMF, SCBF, BRBF, EBF, dual). The Rasmussen DDM is calibrated for **gravity and gravity + wind**. For seismic buildings the agent therefore reports in three tiers:

1. **Code-recognisable DDM result** — gravity combinations and wind combinations: φ_s·λ_u ≥ 1.0 with a calibrated φ_s. For R = 3 / wind-governed buildings (Ex18, Ex3, Ex21, Ex26, Ex31) this *is* the whole story and the DDM is fully applicable.
2. **Seismic-pattern system capacity** — the ELF (or first-mode) lateral pattern scaled to collapse over factored gravity: λ_u, pushover overstrength Ω = V_u/V_design, drift at peak vs Cd-amplified design drift, mechanism (does the SCWB-designed SMF actually form beam hinges first? do the SCBF braces buckle at the expected storey?). Reported as *supplementary*, with no φ_s pass/fail, and cross-referenced to the `capacity_design` block the HR agent wrote.
3. **Ω₀-level check** — λ_u on the Ω₀ combinations should exceed 1.0 by a comfortable margin if the capacity-designed columns and collectors are adequate; the DDM run gives that directly.

The bot must say, in the report, that AISC 341 detailing, protected zones, SCWB and connection prequalification remain governed by the member-based design.

---

## 7. The bot for the Steltic Grok Bot harness

### 7.1 A fourth bot: **DDM Steel App**

Follows the existing two-prompt install pattern exactly.

**Prompt 1.** Load `skills/Skill_querying_PACKAGED.md` (unchanged — the DDM bot retrieves provisions the same way) **and** the new `skills/Skill_ddm_PACKAGED.md`.

**Prompt 2.** *(paste into the bot)*

```
Clone https://github.com/Steltic/steltic and https://github.com/Steltic/steltic_ddm onto your computer.
Install both per their READMEs (Python 3.12, pip install -e . — openseespy needs native libs, install
those too). Verify by running `ddm selftest` (column-curve regression + planar benchmark frame) and
confirm all checks pass. Then read steltic_ddm/contract/DDM_START.md: for future tasks you will be
handed a Steltic design package (zip) and its brief; you will follow that contract yourself — run the
transfer gate, build the GMNIA model, run the load-factor sweeps, classify the mechanism, apply the
system resistance factor policy, write the ddm_analysis block and deliver ddm_report.html. Let me
know after the selftest, then run the first example package end to end that way.
```

### 7.2 `Skill_ddm_PACKAGED.md` — contents (outline of the skill to be written)

* Role: system-capacity analyst, *not* a designer. Never resizes members; if φ_s·λ_u < 1 it reports the shortfall and the governing mechanism and hands back to the HR/CFS bot.
* Hand-off contract: input = brief + zip; required files list; refuse if `cfg.py`/`model_opensees.py` are missing (ask the HR bot to re-run `pipeline.design_and_report(name, cfg)` with `cfg` passed — the export is silently skipped otherwise).
* The workflow gates, in order: transfer gate → nominal model statement (elements, materials, residual stresses, imperfections, joints, diaphragm) → combination set → sweeps → mechanism classification → φ_s policy → report → `ddm_analysis` block → re-render Steltic report.
* Retrieval plan template (first wave) for the Query file manager: `AISC_360_22` exact_section `1.3` (+ neighbors), `1.3.2`, `1.3.3`, `C2.2a`, `C2.2b`, exact_table `B4.1b`; `AISC_341_22` exact_section `A3.2` (Ry/Rt, for the seismic supplement); `AISI_S100` exact_section `C1`, `C1.1`, App. 2 index; `opensees` command queries `forceBeamColumn`, `Corotational`, `InitStressMaterial`, `DisplacementControl`, `ArcLength`. Design values only from `corpus=specification, part=standard`. The φ_s values are *literature*, not spec — cite the paper, never a spec id.
* Id traps to add: AISC 360-22 App. 1 has §1.2 (elastic) and §1.3 (inelastic) — do not cite §1.3 provisions under §1.2 ids; App. 1 §1.3.3 sub-clauses are lettered (a/b/c) — retrieve, don't guess; AISI S100 **C1.1** is Direct Analysis Method (stability), not a DDM clause; AS/NZS 4600 / AS 4084 DDM provisions are *not* in the corpus — honest absence.
* Completion gate: transfer gate passed; every combination row has λ_u, φ_s class + citation, mechanism, governing members; sensitivity table present; seismic supplement labelled; `ddm_analysis` written and the Steltic report re-rendered; the closing line lists caveats verbatim.

### 7.3 Test harness for the bot (mirrors `test_buildings/`)

`steltic_ddm/test_packages/` will hold one *finished* HR package per selected test building (produced once by the HR Steel App in MOCK or real mode and frozen), so the DDM bot can be exercised without re-running the design. Each package gets an assessor key (`DDM_ASSESSMENT_RUBRIC.md`): expected mechanism class, expected λ_u band (from a reference run), expected φ_s class, and the traps (e.g. a brace that must buckle before the beam hinges; a rigid-joint assumption that flips the answer).

---

## 8. Cold-formed steel (Phase 2)

### 8.1 Portal frames first (CFS_Ex16, 17, 26, 27, 28, 30)

Direct analogue of Sena Cardoso et al. 2019. Approach in OpenSees, ascending fidelity:

* **2a — Beam-element GMNIA with effective-fibre laws.** Rebuild `cfs_frame` geometry (columns, rafters, purlin/girt stations, pinned/fixed bases, knee/apex as rigid or with the bolt-group rotational stiffness) as `forceBeamColumn`/Corotational with a fibre section of the built-up channels, where each plate fibre carries an *effective* stress–strain law that softens per the DSM local (and distortional) strength (`Pcrl`, `Pcrd` from a CUFSM-style finite-strip signature curve built from `cfs_sections.gross_props` midline geometry — the "effective fibre" concept the CFS explainer's Tier 2 already describes). Imperfections H/500 + L/1000. This is implementable inside the existing stack and gives λ_u; φ_s = 0.85 declared with the caveat that the calibration used shell models.
* **2b — Shell-element GMNIA** for the governing frame (ShellMITC4/ShellNLDKGQ with J2 plasticity, local + distortional + global imperfections from buckling modes) — matches the calibration fidelity and unlocks the 0.90–0.95 φ_s band, at the cost of runtime and model-building complexity. Do only after 2a is proven.
* Single-channel frames (CFS_Ex27) additionally need warping/torsion: OpenSees has no warping beam element in the standard build, so 2b (shell) is the honest route; 2a can only report the flexural λ_u plus the existing analytic torsion companion.

### 8.2 Stud-wall buildings (CFS_Ex1–15)

No member-level model exists in Steltic and no DDM φ_s calibration exists in the literature, so this is a research-grade "DDM-lite":

* Build a 3-D OpenSees model from `cfg.py`'s `WallLine` objects and the agent's filled `calc_package_cfs.json` (sheathing, fastener schedule, chord studs, hold-downs): each wall segment → a `twoNodeLink`/`zeroLength` shear spring with a `Pinching4` backbone calibrated to the S400 table shear (v_n) and the S400 four-term stiffness (this is the CFS-NEES/Leng-Schafer whole-building modelling approach); chord studs → fibre `forceBeamColumn` lipped-channel sections with effective-fibre laws; hold-downs → tension-only springs with the device stiffness `k_anchor`; straps → tension-only `corotTruss` with `Steel02` (strap-braced walls are the most frame-like and go first); diaphragms → flexible (truss/quad with G' from the deck) or rigid per `cfg['diaphragm']`.
* Run the same proportional / gravity-then-lateral sweeps → λ_u, mechanism (shear-wall backbone peak vs chord-stud buckling vs hold-down yield), drift at peak.
* Report with φ_s = 0.80 *declared uncalibrated*; the value of the exercise is the system margin and the failure-mode ranking, not a code pass/fail. This also gives Steltic the "Tier 2" the explainer promises.

### 8.3 What must be added to the CFS engine to make 8.2 possible

The `cfs_viewer3d._wall_data` emitter already produces a schematic 3-D node/element layout (chord studs, top track, two diagonals per segment); it is the natural seed for the GMNIA topology. `cfs_sections.gross_props` gives the sectorial properties needed for the finite-strip signature curve; `cfs_shapes.csv` already carries `xo, ro, beta, Lu` unused. A finite-strip module (~600 lines, pure numpy, CUFSM-equivalent) is the one genuinely new piece of numerical machinery.

---

## 9. Test-building triage

### 9.1 Hot-rolled (`steltic/test_buildings/`)

| Tier | Building | Why |
|---|---|---|
| **1 — first runs** | **Ex18** R = 3 not-detailed, 8 levels, SDC B, wind expected to govern, all pinned | The cleanest DDM application: gravity + wind, no AISC 341, exactly the Rasmussen calibration scope; braced frames + gravity framing |
| 1 | **Ex4** BRBF 3 levels | Smallest ductile system; BRBs as yielding trusses, no buckling; one beam/column size per storey; fast turnaround for debugging the pipeline |
| 1 | **Ex1** SCBF 5 levels, chevron core, pinned bases and framing | First brace-buckling case: chevron unbalanced force after brace buckling is a well-known mechanism to verify against the `capacity_design` block |
| 1 | **Ex2** SMF 9 levels, rigid bases and joints, square plan, ELF | The archetypal Zhang et al. moment frame; strong-column/weak-beam mechanism check; validates hinge mapping |
| 1 | **Ex31** gable warehouse, R = 3, unbalanced snow, ponding | A hot-rolled portal frame — bridges to the CFS portal work and to the Sena Cardoso protocol; wind/snow-governed |
| 1 | **Ex35** Ch. 15 3-tier braced equipment platform, no cladding | Open braced frame with heavy point masses — closest to the Rasmussen scaffold/rack calibrations; gravity + ELF, small model |
| **2 — second wave** | Ex6 SCBF 6; Ex7a SMF 7 (RBS → reduced fibre section at hinge zones); Ex7b SCBF two-storey X + penthouse; Ex5 SMF 10; Ex30 composite SCBF 6 (bare-steel lower bound); Ex3 braced+moment R = 3 NYC (wind); Ex21 1-storey big-box OCBF (wind; flexible diaphragm needs the truss-diaphragm option); Ex22 SMF hospital RC IV | Regular plans, single system, MRSA/ELF — exercise RBS, two-storey X, composite caveat, flexible diaphragm |
| 3 — irregular / large | Ex24 mixed systems; Ex26 slender wind-governed dual 18 levels (a *strong* DDM candidate on merit — wind governs — but large); Ex10, 15, 19, 20, 23, 25, 34 podium/setback/transfer/soft-storey; Ex8, 9, 11, 12, 14, 16, 17 re-entrant / T / U / L / Z / cruciform | Valid but heavier; run after the engine is trusted |
| Avoid initially | Ex9 SPSW, Ex27 SpeedCore, Ex28 STMF, Ex29 NLRHA tall, Ex32 existing A36 addition, Ex33 crane fatigue, Ex13 split-level | Systems Steltic itself only idealises elastically (plate walls, composite walls, trusses) or where the brief's point is something the DDM does not address |

### 9.2 Cold-formed (`steltic_cfs/test_buildings/`)

| Tier | Building | Why |
|---|---|---|
| **1 — portal frames** | **CFS_Ex16** single-span back-to-back portal, pinned bases, wind-governed | The Sena Cardoso et al. 2019 archetype; 2-D; fastest path to a real λ_u |
| 1 | **CFS_Ex17** two-span portal with valley column, snow-governed, pattern snow | Gravity-governed portal → the calibrated gravity case; interior column instability |
| 1 | **CFS_Ex30** cold-storage portal, Ct = 1.2 snow, discrete bracing, no diaphragm credit | Gravity-governed, long span, interior column allowed |
| 1 | **CFS_Ex28** partially enclosed ag shed | Wind with GCpi ±0.55; sway-instability mechanism likely — good φ_s-class discriminator |
| 2 | CFS_Ex26 open canopy (fixed bases, uplift reversal); CFS_Ex27 single-channel portal (needs warping/shell — Phase 2b) | |
| **2 — stud-wall, strap-braced first** | **CFS_Ex5** strap-braced 6 levels (SDC D); **CFS_Ex2** strap-braced 6 levels (SDC C, wind check); **CFS_Ex8** strap-braced T-plan 4 levels (wind vs seismic by direction) | Straps are tension-only trusses with a real yield capacity-design chain (S400 E3) — the most tractable wall system and the one where a system mechanism (strap yield → chord stud → hold-down) is meaningful |
| 2 | **CFS_Ex1** 4-level rectangular WSP/steel-sheet; **CFS_Ex4** steel-sheet over 1-storey podium; **CFS_Ex3** 8-level steel-sheet over podium | Regular bars; sheathed-wall `Pinching4` backbones; podium two-stage handled by modelling the CFS block on the podium top |
| 2 | **CFS_Ex29** free-standing strap-braced mezzanine | A small braced frame inside a warehouse — near-identical to the rack/scaffold DDM cases |
| 3 / avoid initially | CFS_Ex6, 7, 9, 12, 14, 15 (L/Z/U/cruciform/split/stepped plans on the bounding-box idealisation); CFS_Ex13 Type II perforated (no engine support even for the elastic design); CFS_Ex10 SBMF + strap mixed; CFS_Ex11 gypsum/wind-Exposure D; CFS_Ex25 purlin/girt component (no frame); CFS_Ex18/19 (mixed portal + mezzanine / monorail); CFS_Ex20–24 racks (removed from scope) | |

---

## 10. Roadmap and effort

| Phase | Milestone | Acceptance | Est. effort |
|---|---|---|---|
| 0 | Spike (done) | Column curve within ±10 % of E3; frame sweep to peak | — |
| 1a | `ddm_engine` core: ingest, W-fibre builder, imperfections, solver, transfer gate, CLI | Ex4 and Ex18 packages run end to end; benchmark planar frames from Zhang et al. 2016 reproduced within 5 % | 3–4 weeks |
| 1b | Braces, BRBs, RBS, pins, report, `ddm_analysis` block, Steltic report re-render | Ex1, Ex2, Ex31, Ex35 pass the rubric; brace buckling verified against E3 | 3 weeks |
| 1c | Bot packaging: `Skill_ddm_PACKAGED.md`, `DDM_START.md`, `test_packages/`, rubric, φ_s table transcribed from the full papers | DDM Steel App runs Ex4/Ex18 packages from a cold install using only the harness | 2 weeks |
| 1d | Flexible-diaphragm option, EBF links, sensitivity automation, runtime tuning | Tier-2 HR set | 2–3 weeks |
| 2a | CFS portal frames with effective-fibre laws + finite-strip module | CFS_Ex16/17/28/30 | 4–5 weeks |
| 2b | CFS stud-wall DDM-lite (strap-braced → sheathed → podium) | CFS_Ex5/2/8, then Ex1/4/3 | 5–6 weeks |
| 2c | Shell-element portal GMNIA (optional, unlocks single-channel Ex27) | | 3–4 weeks |

## 11. Risks and open questions

1. **φ_s provenance.** The exact calibrated values and their conditions must come from the full papers (paywalled); until transcribed, the bot labels φ_s "provisional (abstract-level)". Nothing in the harness corpus can supply them, and the skill must forbid inventing a spec citation for them.
2. **Seismic systems are outside the calibration.** Handled by the three-tier reporting in Section 6; the bot must never present a seismic-pattern λ_u as a code pass.
3. **Rigid diaphragm** suppresses beam axial (collectors) and constrains the mechanism; the flexible/truss-diaphragm option is scheduled in 1d.
4. **Beam LTB and local buckling** are not in a fibre beam. Compactness is enforced by the HR design (App. 1 §1.3.2 ductility limits are the right gate to cite); LTB is checked outside the model with Lb from `calc_package`.
5. **Connections** rigid/pinned only; RBS by reduced fibre section; PR joints bounded (as the HR contract already does).
6. **Fillets / k-area** absent from `aisc_shapes.csv` → 1–3 % low A/I; extend the CSV or apply a per-shape correction.
7. **Runtime** for 15–20-storey 3-D buildings: prune combinations, run imperfection sensitivity on the governing combo only, parallelise runs per combination (openseespy is single-process; use multiprocessing).
8. **Solver robustness** at collapse (snap-through at brace buckling, chevron unbalanced force): arc-length fallback and explicit post-peak tracking are in scope from 1a.
9. **CFS stud walls**: no calibration, no finite-strip code in the stack yet, and the elastic design itself sits on a bounding-box idealisation for irregular plans — Phase 2b is honestly research-grade.
10. **Licensing**: all three Steltic repos are MIT; the DDM engine can live as `steltic_ddm` (MIT) with the same spec-free rule — nothing from AISC/AISI text ships.

---

## Appendix A — Sources consulted during scoping

Steltic repos (mirrored read-only): `Steltic/steltic` (README, `contract/AGENT_START.md`, `steel_engine/*`, `steltic/bundle.py`, `test_buildings/*`), `Steltic/steltic_cfs` (README, contracts, `steel_engine/cfs_*`, `wall_line.py`, `eff_stiffness.py`, `test_buildings/CFS_*`, rubric, `frontend/analysis_fidelity_explainer.md`), `Steltic/steltic_grokbot` (README, `skills/*`, `scripts/*`).

Literature: University of Sydney Structures Group project page *System reliability-based criteria for designing steel structures by advanced analysis*; Arrayago, Rasmussen & Zhang, *System-based reliability analysis of stainless steel frames under gravity loads* (open-access preprint, UPC); ScienceDirect / ResearchGate / Sydney eScholarship abstracts for the papers in §1.4; Wan, *System reliability of semi-rigid steel frames designed with Direct Design* (USyd thesis); JHU CFS-NEES publications on OpenSees modelling of CFS buildings.

## Appendix B — Attached files

* `ddm_gmnia_spike.py` — the openseespy proof-of-concept (column curve + frame sweep), runnable as-is with `pip install openseespy`.
* `spike_results.json` — the numbers quoted in §4.5.
