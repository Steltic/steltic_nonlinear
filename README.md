# Steltic_nonlinear (SNL) — the three nonlinear checks of a Steltic steel design, in one repository and one bot

`Steltic_nonlinear` takes the output package of **Steltic** (the HR Steel App design: `report.html`, `cfg.py`,
`model_opensees.py`, `design/…`, `viewer_3d.html`) and runs the three nonlinear analyses that the Steltic Grok Bot set
previously spread over three repositories and three bots:

| step | engine | question | outputs |
|---|---|---|---|
| **Pushover** | `pushover/` (ASCE 41-23 NSP, AISC 342-22 components) | mechanism, system strength Ω, target displacements, IO/LS/CP acceptance | `pushover/pushover_report.html`, `pushover_package.json`, `hinge_params_used.json`, curves, `pushover_viewer_3d.html` |
| **NLRHA** | `nlrha/` (ASCE 7-22 Chapter 16) | mean drifts and element demands under ≥ 11 MCE<sub>R</sub> pairs, Section 16.4 acceptance for the Risk Category | `nlrha/nlrha_report.html`, `nlrha_package.json`, `gm_scaling.json`, `raw_results.pkl`, `nlrha_viewer_3d.html` |
| **DDM** | `steltic_ddm/` (GMNIA system capacity) | load factor λ<sub>u</sub> of every ASCE 7 combination against φ<sub>s</sub>λ<sub>u</sub> ≥ 1 | `ddm_report.html`, `ddm_results.json`, `model_gmnia.py`, `ddm_viewer_3d.html` |
| **Comparison** | `snl/compare.py` | the Steltic design values beside the three results | `four_analyses.html`, `snl_summary.json`, `steltic_viewer_bundle.html` |

One command does all of it:

```bash
python -m snl run Ex22_SMF.zip --params ex22_hinge_params.json --steltic-engine /path/to/steltic/steel_engine
```

The Grok Bot **Steltic Nonlinear (SNL)** is set up like every other Steltic bot: create the bot, paste the two prompts in
`prompts/bootstrap_prompts.md` — Prompt 1 loads the skills (`Skill_querying_PACKAGED.md` from `steltic_grokbot` +
`skills/Skill_SNL_PACKAGED.md` from here), Prompt 2 starts "Clone https://github.com/Steltic/Steltic_nonlinear …" and
installs, proves the example and sets the per-job protocol. The bot then wraps the command in the engineering protocol: it retrieves the component parameters from ASCE/SEI 41-23 → ANSI/AISC 342-22
through Query file manager, fills the job copy of `hinge_params.json`, runs, judges each result with its own rules
(BPON levels for the Risk Category, Chapter 16 RC rules, the transfer gate and φ<sub>s</sub> class), and writes the
four-analyses narrative.

> **Prototype. Not for construction.** The repository `pushover/hinge_params.json` is a placeholder (red UNVERIFIED
> banner until a job copy filled from the standards is passed with `--params`); φ<sub>s</sub> is a literature value;
> Chapter 16 requires design criteria (16.1.4) and independent review (16.5). Every result must be checked and sealed by a
> licensed professional engineer.

## For videos and demonstrations see [stelticai.com](https://stelticai.com)

## Product defaults

See [`docs/PRODUCT_DEFAULTS.md`](docs/PRODUCT_DEFAULTS.md) (rules 1–9) and [`docs/FIBRE_MESH_CONVERGENCE.md`](docs/FIBRE_MESH_CONVERGENCE.md).

- **NSP / HR DDM:** fibre + mesh 10%
- **NLRHA:** ModIMK → PZ×1 → fibre+mesh; dual-gate; early abort on 2 NC
- **CFS DDM:** Tier 2 fidelity gate (no shell)

## Install

```bash
git clone <this repo> Steltic_nonlinear && cd Steltic_nonlinear
python3.12 -m venv .venv && . .venv/bin/activate        # openseespy: Python 3.10–3.12 only
pip install -e .                                        # openseespy, numpy, scipy, matplotlib; ground motions ship in records/
export STELTIC_ENGINE_DIR=/path/to/steltic/steel_engine # the DDM regenerates the ASCE 7 combinations with Steltic's design_pipeline
python -m snl inspect examples/Ex22_SMF                 # read the design basis
python -m snl report  examples/Ex22_SMF                 # rebuild the four-analyses sheet from the shipped outputs (no analysis)
```

## Command line

    python -m nlrha hazard <package> --lat .. --lon ..   # site hazard -> nlrha/site_hazard.json
    python -m nlrha library <folder>                    # index a folder of .AT2 / CSV pairs
    python -m nlrha criteria <package>                  # 16.1.4 design criteria (docx + html)
    python -m snl feedback <job> [--loop ...] [--run]   # the three loops back to HR Steel

```
python -m snl run <package.zip | folder> [--out DIR] [--steltic-engine DIR] [--params job_hinge_params.json]
                  [--only pushover nlrha ddm] [--skip ...] [--parallel 2]
                  [--site-class D] [--risk-category IV] [--n-records 11] [--dt 0.01] [--integrator hht|newmark]
                  [--tail auto|fine_step|arclength|none] [--post-cap-ratio 0.5] [--no-block]
python -m snl report  <job folder>        # four_analyses.html + snl_summary.json + the viewer hub from what exists
python -m snl inspect <package.zip | folder>
```

`run` unpacks the zip next to itself (or into `--out`), then runs the three engines **in sequence, each in its own
process** (openseespy is a process singleton), logging to `<job>/snl_run.log` and `<job>/snl_run.json`. A failing step does
not stop the others. The individual engines remain callable on their own (`python -m pushover run …`, `python -m nlrha run
… / report …`, `python -m steltic_ddm run … / report … / viewer …`) with the options documented in `docs/README_pushover.md`,
`docs/README_nlrha.md` and `docs/README_ddm.md`. Typical times for a 250–700-member building on two cores: pushover 5–15 min,
NLRHA 30–90 min (dt 0.01 s), DDM 30–70 min.

## Site-specific ground motions (16.2)

`python -m nlrha hazard <package> --lat 34.05 --lon -118.25 --site-class D [--cs-period 1.0 0.3] [--t1 1.1]` pulls the
ASCE 7-22 multi-period MCE<sub>R</sub> spectrum from the USGS design-maps service and the mean M / R / ε (with the
contributing faults) from the USGS NSHM disaggregation at the conditioning period, builds conditional (mean) spectra
(Baker 2011 form, Baker & Jayaram 2008 correlation), screens the site for near-fault sources and the pulse share that
implies, and writes `nlrha/site_hazard.json` + `site_hazard.html`. `nlrha run --target mcer|cs` then selects and
scales against the site-specific target, ranking records by spectral shape **and** M / R consistency with the
disaggregation (16.2.2), reserving the pulse share for records flagged `pulse` in their index. The library grows
beyond the shipped FEMA P-695 far-field set with `--records-set <folder> ...`: folders of PEER `.AT2` pairs (NGA-West2
downloads with their `_SearchResults.csv`) or two-column CSVs are indexed on the fly (`python -m nlrha library <folder>`).
See `docs/README_nlrha.md`.

## Design criteria document (16.1.4)

`python -m nlrha criteria <package> [--project ...] [--engineer ...] [--reviewer ...]` — and every `snl run` — drafts
the Section 16.1.4 design criteria document from the package, `ch16_params.json`, the hinge parameters, the site
hazard, the selected suite and any results on file: scope, governing documents, hazard, ground motions, modelling,
acceptance criteria, the linear basis (16.1.2, including any drift relief), the 16.5 review scope, open items, the
retrieval log. Written as `design_criteria_16_1_4.docx` (for mark-up; no python-docx needed) and `.html`.

## Feedback loops back to HR Steel

Once the Chapter 16 run is complete, the **Feedback** tab (hub) or `python -m snl feedback <job>` offers three
re-design loops — design drift to the measured response (16.1.2 relief, RC I–III), resize by system role, mechanism
shaping through SCWB and panel zones — each with a reviewed change set, the brief HR Steel's agent applies, a
verification with the same analyses, and one button to make a verified candidate the design of record. See
`docs/README_feedback.md`.

## The comparison sheet

`four_analyses.html` is data-driven: it reads `report.html` (design drift table, base shears), `design/calc_package.json`
(D/C), the three package files, and writes five sheets — the four verdicts, what each package answers, the same quantities
four ways (periods; design force vs effective yield strength; Ω vs Ω<sub>0</sub> vs λ<sub>u</sub>; MCE<sub>R</sub> roof
displacement δ<sub>t</sub> vs the record mean; storey drift design vs NSP vs suite mean/max against both limits;
deformation- and force-controlled components), the records / element checks / sweeps in full, and the disclosures read from
the outputs (tail status, post-capping ratio, integrator and time step, retried records, φ<sub>s</sub> statuses). Packages
that were not run are shown as *not run*. `snl_summary.json` carries the same numbers for the bot. The engineering
interpretation is the bot's narrative; `docs/ex18_four_analyses.html` and `docs/ex22_four_analyses.html` show what that
narrative looks like for a wind-governed R = 3 braced frame and a Risk Category IV SMF.

## Viewers

Every engine writes its 3-D viewer (three.js vendored, MIT; opens from disk) in the style of Steltic's `viewer_3d.html`:
same geometry, orbit controls, palette (grey elastic · yellow yielded · purple buckled brace · amber > IO/LS · red > CP ·
dark red beyond b), plastic-hinge dots and panel layout. The strip Steltic · Pushover · NLRHA · DDM in each viewer is a
module selector (greyed where the sibling file is missing) and `steltic_viewer_bundle.html` in the job root shows the four
in one page. One viewer core (`viewer_core.py/.html`) is kept as identical copies inside the three engines so each can still
be shipped alone; `tests/test_snl.py` asserts they match.

## Repo map

`snl/` orchestrator + comparison · `pushover/`, `nlrha/`, `steltic_ddm/` the three engines (unchanged import names) ·
`records/` FEMA P-695 far-field set · `skills/` the SNL skill and the three constituent skills · `prompts/` Grok Bot set-up ·
`contract/DDM_START.md` · `docs/` engine READMEs, scoping documents, φ<sub>s</sub> sources, the two example narratives ·
`examples/Ex22_SMF` (6-storey RC IV SMF, all outputs, the AISC 342-verified job parameter file) and `examples/Ex18_R3`
(8-storey R = 3 X-braced, all outputs) · `tests/`.

## Tests

`python -m pytest tests -q` from the repo root (a few minutes; needs openseespy). `test_feedback.py`, `test_loop.py`
(against `tests/fake_hr.py`, a stand-in HR Steel server), `test_site_hazard.py` (canned USGS responses, no network) and
`test_design_criteria.py` cover the feedback loops, the site-specific hazard and the 16.1.4 document. `test_snl.py` covers packaging, the
identical viewer cores, zip unpacking, `snl report`, the `snl run` step selection with stubbed engines, and the
four-analyses sheet regenerated from both examples; `test_pushover.py`, `test_nlrha.py` and `test_ddm.py` are the three
engines' own smoke tests pointed at the packaged examples (the DDM Ex18 ingest/gate test needs `STELTIC_ENGINE_DIR`);
`test_viewer_core_*.py` renders a small frame through each engine's copy of the viewer core.

## License

MIT (see `LICENSE`, `NOTICE.md`). No specification text is included; clause and table numbers are retrieval targets for
Query file manager.

## Fibre mesh-convergence

Fibre is the default plasticity for NSP and NLRHA (`--plasticity fibre --member-nseg 4`).
DDM remains fibre GMNIA. Run a mesh ladder with a **10%** relative stop band:

```bash
python -m snl mesh-converge <job> --analyses nsp nlrha ddm --tol 0.10
```

See [`docs/FIBRE_MESH_CONVERGENCE.md`](docs/FIBRE_MESH_CONVERGENCE.md).
