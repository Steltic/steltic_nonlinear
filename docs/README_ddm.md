# steltic_ddm — Direct Design Method for Steltic steel buildings

**System capacity by advanced analysis (GMNIA) in OpenSees, from a finished Steltic design package.**

Steltic's HR Steel App designs a building member by member to AISC 360/341 (linear-elastic OpenSees
demands + hand-derived capacities). `steltic_ddm` takes that finished package and asks the other
question: *what is the ultimate load factor of the building as a system?* It rebuilds the same building
as a geometrically and materially nonlinear fibre model with imperfections and residual stresses,
scales each factored ASCE 7-22 combination by a load factor λ to collapse, and applies the Direct
Design Method check of Rasmussen and co-workers:

> **φ<sub>s</sub> · λ<sub>u</sub> ≥ 1.0**

where φ<sub>s</sub> is a *system* resistance factor calibrated by system reliability
(Zhang, Shayan, Rasmussen & Ellingwood, JCSR 123, 2016).

> **Not for construction.** Outputs are produced by an analysis engine and an AI agent; they must be
> independently checked and sealed by a licensed professional engineer. The DDM result does not
> replace the member-based design, AISC 341 detailing or connection design.

> **φ<sub>s</sub> status.** Hot-rolled classes are verified from open documents: gravity 0.90/0.85/0.80/0.70 at
> β<sub>T</sub> 2.5/2.75/3.0/3.5 (Zhang, Rasmussen, Shayan & Ellingwood, SSRC 2013, Table 10) and gravity + wind
> 0.85/0.80/0.69 at β<sub>T</sub> 2.5/3.0/3.5 (Wan 2024 PhD thesis, Tables 6.10–6.14, rigid rows; semi-rigid
> curve Eq. 6.9). CF-HSS and CFS-portal wind values are the authors' own citations in Arrayago, Rasmussen & Zhang
> 2022 (CC-BY); their gravity values remain provisional and are flagged. Seismic-pattern cases on R > 3 systems get
> no pass/fail. See `docs/PHI_S_SOURCES.md`. φ<sub>s</sub> is a literature value — it has no specification clause.

## Install

```bash
# 1. Steltic (the design app) -- its steel_engine is imported for the load combinations
git clone https://github.com/Steltic/steltic
# 2. this engine
git clone https://github.com/Steltic/steltic_ddm && cd steltic_ddm
python3.12 -m venv .venv && . .venv/bin/activate      # openseespy needs CPython 3.10-3.12
pip install -e .
export STELTIC_ENGINE_DIR=/path/to/steltic/steel_engine
python -m steltic_ddm selftest                        # fibre column vs AISC E3 curve: must PASS
```

## Run

```bash
python -m steltic_ddm run /path/to/jobs/<building> --workers 2 --sensitivity
python -m steltic_ddm report /path/to/jobs/<building> [--risk-category IV]   # re-apply the current phi_s policy, rebuild report/block/viewer (no re-analysis)
```

`report` re-reads `ddm_results.json`, re-runs the φ<sub>s</sub> policy on the stored λ<sub>u</sub> and mechanism classes
(after a `phi_s.py` update, or to see another Risk Category's β<sub>T</sub> row) and rewrites `ddm_report.html`, the
`ddm_analysis` block, `ddm_results.json` and the viewer. The block now carries `phi_s_provisional` (true only when a class
the run actually used is provisional / extrapolated), `phi_s_status` and `phi_s_classes`.

Inputs read from the job folder: `cfg.py`, `model_opensees.py`, `design/member_schedule.csv`,
`design/calc_package.json`. Outputs written beside them: `ddm_report.html`, `ddm_results.json`,
`model_gmnia.py` (standalone replay of the GMNIA model) and a `ddm_analysis` block inserted into
`design/calc_package.json` (backup `calc_package.json.pre_ddm.bak`) so that re-rendering the Steltic
report shows the system-capacity section.

Useful options: `--only <substr>` (subset of combinations), `--combos all`, `--om0` (Ω₀ column cases),
`--gravity-dirs one|two|all` (imperfection direction search on gravity cases), `--nsub 4 4 6`
(finer subdivision), `--residual lehigh|eccs|none`, `--fast` (displacement-based elements),
`--dlam 0.05`, `--time-limit`, `--no-block`.

## 3-D viewer (ddm_viewer_3d.html)

Every run also writes **`ddm_viewer_3d.html`** — a self-contained, interactive 3-D viewer in the style of Steltic's own
`viewer_3d.html` (three.js r128 vendored, MIT; no network, opens from disk). It is one of the three viewers of the
**Steltic viewer bundle** (Pushover · NLRHA · DDM): the same geometry, orbit controls, dark engineering palette,
component-state colours and panel layout in all three, so an engineer can open Steltic's viewer and the three analysis
viewers side by side and read the building the same way in each.

One load combination at a time (selector lists kind, λu and φs·λu): the λ–Δ curve is the chart (other
combinations of the same kind faded behind it; λ = 1.0, first yield, φs·λu and λu as reference lines). Slide or click
along the curve (or ▶) and the building follows: the solver records a viewer frame every second converged load step
(and at λu) — diaphragm-master displacements, the fibre strain ratio ε/εy of every member at or above 0.5 εy with the
position of its worst integration segment, and the buckled braces — so the deformed shape, the member colours
(elastic · 0.5–1 εy · yielded · plastic hinge ≥ 3 εy · buckled brace, N < 0 and mid-length offset > L/200) and the hinge
dots all move with λ. Other colour modes: member role, type, section. Right panel: system capacity table (λu, first
yield, λ at 1.25 Δu, φs class and check, mechanism, control DOF), story drift at λu, member census at the shown λ (group
table at λu), transfer-gate rows. Rebuild without re-analysis: `python -m steltic_ddm viewer <job_dir>` (from
`ddm_results.json`, which now carries the frames; the job folder and `STELTIC_ENGINE_DIR` are still needed to read the
model). Results files written by older versions have no frames — the viewer then shows the peak snapshot only.

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

## What the engine does

| step | module | what |
|---|---|---|
| ingest | `ingest.py` | parses `model_opensees.py` (nodes, fixities, masses, transforms, elements with releases, diaphragms; the LAST of Steltic's two recorded builds), joins section labels from `member_schedule.csv`, roles from the topology, exec's `cfg.py` |
| combinations | `loads.py` | regenerates the exact ASCE 7-22 §2.3 set with Steltic's `design_pipeline.combos(cfg)`; prunes to gravity + ±X/±Y wind and seismic-pattern strength cases; gravity as Steltic's two-way tributary beam UDLs |
| transfer gate | `transfer_gate.py` | elastic GMNIA topology vs the Steltic model: periods and ELF roof drifts within 5 % or the run stops with a diagnosis |
| model | `model_gmnia.py`, `sections_fiber.py`, `imperfections.py` | forceBeamColumn / Corotational / 5 Lobatto points; fibre W (Galambos–Ketter residual stresses) and HSS sections from `aisc_shapes.csv`; H/500 out-of-plumb with the lateral load, L/1000 bows (weak axis / out-of-plane); Steltic pins as true pins; brace ends pinned in bending with torsion retained |
| sweep | `solver.py` | proportional λ with adaptive displacement control, Newton → KrylovNewton with step halving, first-limit-point and plastic-plateau detection, post-peak branch, yield-ratio map, brace-buckling flags, mechanism classification, viewer frames every 2nd converged step |
| check | `phi_s.py` | φ<sub>s</sub> class per combination by load family (gravity / gravity + wind), material (HR, HR semi-rigid, CF-HSS, CFS portal) and Risk Category β<sub>T</sub> row; seismic-pattern cases on R > 3 systems reported without pass/fail |
| report | `report_ddm.py` | `ddm_report.html`, `ddm_analysis` block |
| viewer | `viewer3d.py` + `viewer_core.py/.html` | `ddm_viewer_3d.html` (Steltic viewer bundle) |

Validation: `python -m steltic_ddm selftest` reproduces the AISC 360 E3 column curve with the production
fibre builder (W14X90, KL/r 60 and 120, strong and weak axis, ratios 0.93–1.03); the Ex18 transfer
gate matches the Steltic model to 0.4 %.

## Repo map

`steltic_ddm/` engine (`viewer3d.py`, `viewer_core.py/.html`, `vendor/three.min.js` = the 3-D viewer) · `contract/DDM_START.md` the bot's working contract · `skills/Skill_ddm_PACKAGED.md`
the Grok Bot skill · `test_packages/` frozen Steltic packages for regression (Ex18_R3) ·
`tests/` · `docs/DDM_AGENT_SCOPE.md` the scoping document.

## License

MIT. Contains no specification text; `data/aisc_shapes.csv` is the AISC shapes database as
redistributed by Steltic (see NOTICE).
