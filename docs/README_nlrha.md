# steltic_nlrha — Non Linear Dynamic Bot (ASCE 7-22 Chapter 16) for the Steltic set

Takes a Steltic HR design package (+ its pushover supplement if present) and runs an **ASCE 7-22 Chapter 16
nonlinear response history analysis** on the same hinge model the Pushover Analyst builds: MCE_R target spectrum
(16.2.1.1 → 11.4.6), ≥ 11 pairs from the FEMA P-695 far-field set, period range and RotD100 amplitude scaling
(16.2.3), orthogonal application (16.2.4), 1.0D + 0.5L gravity (16.3.2), ≤ 2.5 % Rayleigh damping (16.3.5),
and the 16.4 global / element acceptance. Writes `nlrha_report.html` + `nlrha_package.json` + `nlrha_viewer_3d.html` next to `report.html`.

> Prototype. Not for construction. Component backbones come from `steltic_pushover/hinge_params.json`
> (`verified: false` placeholders → red banner). The Chapter 16 rules in `nlrha/ch16_params.json` were read
> against the user's converted ASCE 7-22 (pdf pp. 248-251) and paraphrased — no spec text is embedded.

```
Steltic package + pushover/  ─►  nlrha/ground_motions  library, RotD100, target, period range, select + scale
                                 nlrha/model           steltic_pushover hinge model + Ch.16 gravity + damping + modal
                                 nlrha/run             per-record Newmark run, peaks (drift at edges, hinge/brace def, column P)
                                 nlrha/acceptance      16.4.1.1 unacceptable, 16.4.1.2 drift, 16.4.2.1 force-, 16.4.2.2 deformation-controlled
                                 nlrha/report          nlrha_report.html + nlrha_package.json
                                 nlrha/viewer3d        nlrha_viewer_3d.html (Steltic viewer bundle)
```

## Install / run

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ../steltic_pushover -e .
python -m nlrha scale examples/Ex18_R3            # selection + scaling only (~1 min)
python -m nlrha run   examples/Ex18_R3 --parallel 2   # full suite (20-40 min)
python -m nlrha run <pkg> [--n 11] [--dt 0.02] [--xi 0.025] [--records 1-3] [--records-set DIR] [--out DIR] [--pushover-dir DIR]
                          [--risk-category IV]   # default: read from cfg.py ('Risk Category IV'), else from Ie (1.25 -> III, 1.5 -> IV)
                          [--integrator hht|newmark]   # HHT alpha = 0.9 (default) damps the spurious high modes of the stiff hinge springs; the time step is adaptive (halved on failure down to dt/64, regrown after 8 clean steps)
```

## 3-D viewer (nlrha_viewer_3d.html)

Every run also writes **`nlrha_viewer_3d.html`** — a self-contained, interactive 3-D viewer in the style of Steltic's own
`viewer_3d.html` (three.js r128 vendored, MIT; no network, opens from disk). It is one of the three viewers of the
**Steltic viewer bundle** (Pushover · NLRHA · DDM): the same geometry, orbit controls, dark engineering palette,
component-state colours and panel layout in all three, so an engineer can open Steltic's viewer and the three analysis
viewers side by side and read the building the same way in each.

One ground-motion pair at a time (selector lists SF, peak drift and the 16.4.1.1 flag): ▶ play the response in
real time (0.5–4×) or scrub the roof-displacement / roof-drift / ground-acceleration trace; the building takes the
recorded rigid-diaphragm shape (ux, uy, rz per level every 0.1 s), braces are coloured by their instantaneous axial
deformation, beams and columns by their peak plastic rotation over the record. Buttons jump to the X and Y peaks and to
the end (residual). The significant-duration window (Arias 0.1–99.5 %) and free vibration are shaded on the trace and the
pushover δt BSE-2N is drawn for comparison. Right panel: values at the instant, story drift bars for this record against
the suite mean and the 16.4.1.2 limit, live/peak component census, and the Section 16.4 suite verdict (unacceptable
count, mean drift, deformation- and force-controlled D/C, scaling and damping basis). Also rebuilt without re-analysis by
`python -m nlrha report <package>` (from `raw_results.pkl`).

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

## Ground-motion library

`records/p695_farfield/` — the 22 far-field pairs (44 horizontal components) of FEMA P-695 Appendix A as
distributed by the ATC-63 project (PEER NGA .AT2, unscaled), with `index.json` built from Tables A-4A…A-4D.
Any other set can be used with the same `index.json` layout.

## Limitations (roadmap)

- Cyclic deterioration off (Λ = 0); braces are phenomenological trusses (no fracture); panel zones default rigid (opt-in `panel_zones.mode=scissors` in the shared hinge_params — same builder as pushover); bare frame.
- Selection by spectral shape against a code spectrum — not a hazard-consistent (M, R) selection (16.2.2).
- No-live-load gravity case (16.3.2) not run automatically; accidental torsion (16.3.4), vertical motion (16.1.3),
  foundations (16.3.6), spectral matching (16.2.3.3) not implemented.
- Force-controlled check uses AISC 360 E3 computed in-tool (Fy = 50, K = 1); connections not checked.

## License

MIT (code). Ground motions: public PEER NGA records as redistributed by the ATC-63/FEMA P-695 project.
