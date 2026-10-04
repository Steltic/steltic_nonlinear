# steltic_pushover — Pushover Analyst for the Steltic Grok Bot set

Turns a finished **Steltic** HR design package (AISC 360/341, elastic OpenSees model) into a
**nonlinear static (pushover) supplement**: converts `model_opensees.py` to a concentrated-plasticity
model, pushes it in X and Y with a first-mode pattern and P-Δ, evaluates the capacity curve to the
ASCE 41 nonlinear static procedure (target displacement, component acceptance IO/LS/CP, mechanism) and
FEMA P-695-style overstrength/ductility, and writes `pushover_report.html` next to `report.html`.

It is meant to be driven by a Grok Bot, **Pushover Analyst**, that sits next to Query file
manager / HR Steel App / CFS Steel App / DDM Steel App (see `prompts/bootstrap_prompts.md`). The bot supplies the
one thing this code refuses to invent: the component modelling parameters and acceptance criteria,
retrieved verbatim from ASCE/SEI 41-23 → ANSI/AISC 342-22 through Query file manager.

> **Prototype. Not for construction.** `pushover/hinge_params.json` is the template: `verified: false` and every
> value listed in its group's `unverified` list; every report carries a red banner naming the values the user has
> not supplied and verified against the licensed standards. All results must be independently checked and sealed by a licensed PE.

```
Steltic package (zip / folder)                      pushover/
  report.html  cfg.py  model_opensees.py   ──►      package_reader   parse (never exec) the elastic model + basis
  design/calc_package.json, member_schedule.csv     hinge_models     a,b,c / IO,LS,CP from hinge_params.json
                                                    nonlinear_model  IMKPeakOriented hinges, gravity, modal pattern, push
                                                    postprocess      ASCE 41 idealisation, δt (BSE-1N/2N), μmax, P-695 Ω, μT, acceptance
                                                    report_supplement pushover_report.html + pushover_package.json + curves
                                                    viewer3d         pushover_viewer_3d.html (Steltic viewer bundle)
```

## Install

```bash
git clone <this repo> && cd steltic_pushover
python3.12 -m venv .venv && . .venv/bin/activate      # openseespy: Python 3.10–3.12 only
pip install -e .
python -m pushover inspect examples/SMF6_B02           # read-only look at the example package
python -m pushover run examples/SMF6_B02 --site-class D   # ~5-10 min -> examples/SMF6_B02/pushover/pushover_report.html
```

## 3-D viewer (pushover_viewer_3d.html)

Every run also writes **`pushover_viewer_3d.html`** — a self-contained, interactive 3-D viewer in the style of Steltic's own
`viewer_3d.html` (three.js r128 vendored, MIT; no network, opens from disk). It is one of the three viewers of the
**Steltic viewer bundle** (Pushover · NLRHA · DDM): the same geometry, orbit controls, dark engineering palette,
component-state colours and panel layout in all three, so an engineer can open Steltic's viewer and the three analysis
viewers side by side and read the building the same way in each.

The capacity curve is the scrubber: drag along V–δroof (or the Time slider / ▶ play) and the building takes the
recorded shape at that step with every hinge and brace coloured by its ASCE 41 state (elastic / yielded / > IO / > LS /
> CP / beyond b; braces: buckled Δ > Δc, tension yield). Buttons jump to δt BSE-1N, δt BSE-2N, Vmax and the end of the
push; the chart carries the idealised bilinear curve, the design base shear V (R-reduced) and the wind base shear as
reference lines. Right panel: values at the step, story drift bars, component census, NSP summary (Te, Sa, δt,
μstrength ≤ μmax, Ω, μT, worst D/C at each hazard level).

Common to the bundle: drag to orbit, right-drag / shift-drag to pan, wheel to zoom, click a member for its data
(section, level, state, Steltic governing combination and demand), `R` resets the camera, space plays / pauses, the
Plan / Elev X / Elev Y buttons snap the view, the Deformation slider scales the rigid-diaphragm shape, and the Show
toggles hide columns / beams / braces / slabs / supports / undeformed ghost. Component states use one palette everywhere:
grey elastic · yellow yielded · purple brace buckled · amber > IO/LS · red > CP · dark red beyond the valid range b.
**Hinge dots** — solid camera-facing discs at the member ends (braces: mid-length) wherever a plastic hinge has formed
at the shown step; dot colour follows the state, or `red = any plastic hinge` from the Hinge dots selector. The legend
sits under the right-hand panel; the member info box opens bottom-left.

**Module selector / bundle hub.** The strip at the top of the left panel (Steltic · Pushover · NLRHA · DDM) is a
selector: the current module is highlighted, the others are clickable links to the sibling viewers when those files
exist next to this one (`viewer_3d.html`, `pushover/pushover_viewer_3d.html`, `nlrha/nlrha_viewer_3d.html`,
`ddm_viewer_3d.html`, checked when the page opens) and greyed out with a strike-through when they do not. Every run also
writes **`steltic_viewer_bundle.html`** in the package root — one page with the four viewers in tabs (each loaded on
first use and kept alive, so flipping is instant); modules missing from the folder are greyed out there too, and the
module strip inside each embedded viewer switches the tab. The `⧉` chip in a standalone viewer opens the hub.

## Component parameters: the ASCE 41-23 / AISC 342-22 schema

`hinge_params.json` is read at run time (`--params my_file.json` for a project copy -- keep the repository file the
template). The user verifies every component value against the printed standard in a manual step and supplies it
in the job copy. The schema (`"schema": "ASCE41-23/AISC342-22"`, see the file's `_README` and
`pushover/params_schema.py`):

- **Printed form accepted.** A cell may be written as the table prints it -- `"9 θy"`, `"0.25 a"`, `"a"`,
  `"0.75 b"`, `"b"` (Tables C2.2 / C3.6), `"1.5 Δc"`, `"0.7 n Δc"`, `"n Δc"` (Table C3.4) -- or in the field form
  (`a_over_thetay`, `IO_frac_of_a`, `LS_frac_of_a`/`_of_b`, `CP_frac_of_a`/`_of_b`, `IO_over_dc`, `LS_frac_of_n` ...).
  Table C2.2 line 1 IO = 0.25 a = 2.25 θ<sub>y</sub> is therefore representable.
- **Both printed lines, row chosen per section.** `beam_flexure` (`"mode": "member"`, Table C2.2) and
  `column_flexure` (Table C3.6) carry `rows.highly_ductile` and `rows.non_moderately_ductile`. Each section is
  classified by its own flange and web slenderness against AISC 341-22 Table D1.1b (R<sub>y</sub>F<sub>y</sub> →
  F<sub>ye</sub>, α<sub>s</sub>P<sub>r</sub> → P<sub>G</sub>; web case 11 for moment-frame members, case 14 otherwise)
  and line 3 is applied: linear interpolation for each element, lowest value used. There is no "moderately
  ductile" row and the system never selects the row. Without line 2 a non-highly-ductile section gets the flat
  `noncompact_reduction` / `non_highly_ductile_reduction` -- flagged in the hinge and reported UNVERIFIED.
- **FR connections.** `"mode": "fr_connection"` = Table C5.5: `a_expr` in `h, tw, bf, tf, Lb, ry, L, d`, `a_max`,
  `b_abs`, `c_residual`, `IO_frac_of_a`, `LS_frac_of_b`, `CP_frac_of_b`, `Lb_over_ry` or `Lb_divisor`, optional
  `rbs_c_frac_bf` / `rbs_c_in` and a `modifiers` list (`factor`, `why`, optional `sections`) for C5.4a.1.a.1.
- **Columns.** Expressions in `h/tw`, `L/ry`, `PG/Pye` (line 2 may also use `bf`, `tf`), caps `a_max`/`b_max`,
  `Mpce_axial_reduction` = AISC 342-22 Eqs. C3-5/C3-6 `min(1 - PG/(2*Pye), 9/8*(1 - PG/Pye))`. A file that still
  carries the superseded `1.18*(1-PG/Pye)` (Commentary Eq. C-C3-5) is corrected by the engine and flagged.
- **Braces.** `brace_axial` `"mode": "table_C3_4"` = Table C3.4 buckling braces: `compression` / `tension` with
  `n_expr` in `lam_ratio` (λ/λ<sub>hd</sub>) and `slend` ((L<sub>c</sub>/r)/√(E/F<sub>ye</sub>)), `f`, `IO`, `LS`, `CP`
  (printed), `tension_only` (note [d]). The old stocky/slender ASCE 41-17 form is still read but always reported
  UNVERIFIED. Buckling-restrained braces (Table C3.3) are not modelled.
- **Provenance.** Each group has a `source` and an `unverified` list (the template lists every field). A value
  the user did not supply -- template, MOCK, or a field Collect wrote without a quote -- is listed in the report's
  red banner by name, and the run's effective `verified` is false whatever the file claims.

`numerics.post_cap_ratio` (default 0.15 ≈ vertical drop) is the tail-protocol rung 3 -- raising it is a disclosed
modelling change.

## BPON performance levels

`--risk-category` (or cfg.py / I<sub>e</sub>) selects the ASCE 41-23 Table 2-5 levels: RC I/II LS at BSE-1N and CP at
BSE-2N; RC III Damage Control / Limited Safety; RC IV IO / LS. Damage Control is half-way IO-LS (Table 2-1) but not
above the `a` point (Section 7.5.3.2.2); Limited Safety is the average of LS and CP. The report, the package
(`risk_category`, `bpon_levels`, per-group `DC`/`LtdS` limits and D/C) and the four-analyses sheet use the same
mapping (`pushover/performance.py`).

## Panel zones (opt-in)

Default `panel_zones.mode` is **`rigid`** (centreline model, no behaviour change). Set `"mode": "scissors"` in
`hinge_params.json` (or a project `--params` copy) to enable scissors-style flexible panel zones at **FR**
moment-frame joints only (unreleased beam + column ends):

- Column stack stays on the joint node; FR beam ends attach to a coincident beam-side node (not an RD slave).
- One 6-DOF `zeroLength` between column joint and beam-side node: rigid on translations / torsion / unused
  flexure, PZ spring on rot DOF(s) 4/5 (`strong_rot_dof`), kip-in-sec. **Not** `equalDOF` (conflicts with
  `rigidDiaphragm` under Transformation — caused spurious short T1 / collapsed base shear before the fix).
- `material`: `"elastic"` (smoke / K<sub>θ</sub> = G·t<sub>p</sub>·d<sub>c</sub>·d<sub>b</sub>) or `"hysteretic"`
  (Gupta–Krawinkler trilinear). Optional `K_theta` or `hysteretic` envelope overrides; `doubler_t_in` adds to t<sub>w</sub>.
- IMK end hinges remain at member ends. **Do not** also apply the AISC 342 C5.4a.1.a.1(b) PZ ductility modifier
  when scissors is on. This is **not** a full 8-bar Krawinkler rectangle (no geometric rigid offsets of beam/column depth).

Same builder feeds NSP and NLRHA (`nlrha/model.py` imports `pushover.nonlinear_model`).

## Command line

```
python -m pushover inspect <package.zip | folder>
python -m pushover run     <package.zip | folder> [--out DIR] [--dirs X Y] [--max-drift 0.08]
                                                  [--site-class D] [--params hinge_params.json] [--system SMF]
                                                  [--tail auto|fine_step|arclength|none] [--post-cap-ratio 0.5]
```

## Examples

`examples/SMF6_B02` (space SMF, drift-governed) and, in the companion `steltic_nlrha` repo, `examples/Ex18_R3`
(R = 3 X-braced frame — exercises the brace model; Ω 6.5 / 6.2, braces buckle at levels 5–7 at δ<sub>t</sub>).

## What the SMF6_B02 example shows (SMF6_B02: B02 archetype, 6-storey space SMF, R=8, SDS=1.0 g)

The B02 archetype is drift-sized with W14X311 columns and W33X130 beams at every joint, so the
pushover finds a system overstrength Ω ≈ 15–17 against the ELF base shear (Ω<sub>0</sub> = 3) and
target displacements of ~0.7% (BSE-1N) and ~1.0% (BSE-2N) of height with the hinges still near yield
— exactly the kind of finding the linear package cannot make on its own, and a warning that
Ω<sub>0</sub>Q<sub>E</sub> is not an upper bound for its capacity-designed elements.

## Repo map

`pushover/` the tool (`viewer3d.py` + `viewer_core.py/.html` + `vendor/three.min.js` are the viewer) · `skills/Skill_pushover_analyst_PACKAGED.md` the Grok Bot skill ·
`prompts/bootstrap_prompts.md` the set-up prompts (bot + Query file manager addition) ·
`examples/SMF6_B02/` a real Steltic package (produced with `steltic` MOCK-free pipeline run) and its
pushover output · `tests/` smoke test · `docs/` the scoping report.

## Limitations of this prototype (roadmap)

- Braces: phenomenological `corotTruss` + `Hysteretic` axial backbone (AISC 342-22 Table C3.4 form, d = nΔ and f,
  K = 1 on the recorded brace length); no fracture, no cyclic degradation. Table C3.4 notes [c] and [e] are not applied. EBF links, BRB cores and SPSW panels are
  not modelled. HSS local slenderness is not checked (no wall thickness in aisc_shapes.csv).
- Panel zones default **rigid**; opt-in `panel_zones.mode=scissors` (joint rotational spring). Bare centreline, no composite slab, no fracture, fixed bases as in the linear model. Full 8-bar Krawinkler is not implemented.
- Gravity spread equally over each level's column nodes (footprint from node extents); use
  `model_static.py` tributary loads for irregular plans.
- Higher-mode (LDP) supplement and the force-controlled Eq. 7-38 check are reported as open items.
- Descending branch: the tool first checks whether hinges reached rotation *b* (a valid P-695 end point),
  then escalates through a fine-step and an arc-length rung; only if both fail does it flag `lower_bound`
  and hand the decision (`--post-cap-ratio`, a modelling change) to the user via the bot.

## License

MIT for the code. `pushover/aisc_shapes.csv` is the AISC Shapes Database v16 as redistributed by the
steltic repo (see its NOTICE). No copyrighted specification text is included or embedded.
