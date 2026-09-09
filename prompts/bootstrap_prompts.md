# Steltic Nonlinear (SNL) — Grok Bot set-up prompts

Same pattern as every other Steltic bot (`steltic_grokbot/README.md`): create the bot, paste two prompts, done.
**Steltic Nonlinear (SNL)** replaces the three separate bots (Pushover Analyst, Non Linear Dynamic Bot, DDM Steel App)
— one upload of the Steltic package zip, one command, all three nonlinear analyses plus the four-analyses comparison.

Create a Grok Bot named **Steltic Nonlinear (SNL)** alongside Query file manager, HR Steel App and CFS Steel App.

## Prompt 1 — load the skills

Load **two** skills, exactly as for HR Steel App:

1. `skills/Skill_querying_PACKAGED.md` from `steltic_grokbot` — the retrieval-plan skill; how the bot talks to Query file manager.
2. `skills/Skill_SNL_PACKAGED.md` from this repository — the SNL protocol (inspect → one retrieval plan → fill the job
   `hinge_params.json` → `snl run` → judge each of the three results → the four-analyses narrative). It is self-contained;
   the three constituent skills in the same folder are reference only and do not need to be loaded.

## Prompt 2 — clone the repo, install, prove it on the example

Paste this into the bot:

```
Clone https://github.com/Steltic/Steltic_nonlinear onto your computer, next to the Steltic repository (the DDM step needs its steel_engine folder). Create a Python 3.12 venv (openseespy only ships wheels for 3.10–3.12) and `pip install -e .` in the repo — that installs the three engines (pushover, nlrha, steltic_ddm), the orchestrator (snl), openseespy, numpy, scipy and matplotlib; the P-695 ground-motion library ships in records/. Set STELTIC_ENGINE_DIR to the absolute path of steltic/steel_engine.

Verify the install on the packaged example, no retrieval needed:
  python -m snl inspect examples/Ex22_SMF   -> 251 nodes / 558 elements / 6 diaphragms; SDS=1.0, SD1=0.6, R=8, Cd=5.5, Om0=3.0, Ie=1.5, system SMF
  python -m snl report  examples/Ex22_SMF   -> rebuilds examples/Ex22_SMF/four_analyses.html and snl_summary.json from the shipped outputs (no analysis)
  python -m pytest tests -q                 -> 26 passed (about a minute)
  python -m snl run examples/Ex22_SMF --only pushover --params examples/Ex22_SMF/hinge_params_ex22_aisc342.json
                                            -> 5–10 minutes; examples/Ex22_SMF/pushover/pushover_report.html appears WITHOUT the red UNVERIFIED banner, Omega about 5.5 (X) / 4.3 (Y)
Open examples/Ex22_SMF/steltic_viewer_bundle.html and flip through the four viewers with the module strip.

From now on, for every job, follow skills/Skill_SNL_PACKAGED.md: `python -m snl inspect <zip>`; send ONE wave-1 retrieval plan to Query file manager (AISC_342_22 Tables C2.2 / C3.6 / C5.5 / C3.4 and Sec. A5.2; ASCE_41_23 Sec. 7.4.3 and Table 2-5; ASCE7 Chapter 16 and the Table 12.12-1 row for the Risk Category; AISC_341_22 Tables A3.2 / D1.1); fill the job copy of hinge_params.json from the excerpts and set verified=true only when every block the package needs is cited; run `python -m snl run <zip> --params <job>_hinge_params.json --steltic-engine $STELTIC_ENGINE_DIR --parallel 2`; judge the pushover, NLRHA and DDM results each with its own protocol; deliver the job folder (pushover/, nlrha/, ddm_*, the four viewers with steltic_viewer_bundle.html, four_analyses.html, snl_summary.json) with your four-analyses narrative, retrieval_log.md and open items.

Report back now: the example's Omega in X and Y, the BSE-2N target displacements, the BPON D/C for Risk Category IV, the hinge census, and confirm the viewer bundle opened.
```

## Nothing else to set up

Query file manager needs `ASCE_41_23` and `AISC_342_22` in the corpus (in addition to `ASCE7` and `AISC_341_22`). If the
Pushover Analyst was ever set up, that is already done. If not, paste this into **Query file manager** once:

```
Two specifications join the corpus for the Steltic Nonlinear bot. I have placed the PDFs in standards/: ASCE/SEI 41-23 (licensed) and ANSI/AISC 342-22 (free download from aisc.org). Convert them with the standard pipeline using the canonical stems ASCE_41_23 and AISC_342_22, recover image pages, postprocess, validate, then rebuild the index and FTS and restart serve_queue. Probe: ASCE_41_23 Sec. 7.4.3.3.2, Table 7-4, Table 7-5, Table 2-5; AISC_342_22 Tables C2.2, C3.4, C3.6, C5.5, Sec. C5.4a.1.a.1, Sec. A5.2. The Table C3.6 and C5.5 cells contain equations the converter may flatten into digit strings — make the page images retrievable so the bot can read exponents and caps directly. Report the ids that resolved and any found:false.
```

## What the user then does per job

1. Finish the building in **HR Steel App**; download the design package (.zip).
2. Upload the zip to **Steltic Nonlinear**: "Run the nonlinear set for this package." Nothing else is needed: the bot
   reads the Risk Category from `cfg.py` (else from I<sub>e</sub>) and assumes site class D for the one place it enters
   (the ASCE 41 C<sub>1</sub> site factor), and states both in its report — correct it only if either is wrong
   ("… site class C", "… Risk Category III").
3. The bot inspects, sends the retrieval plan, fills the parameters, runs the three analyses (1–3 hours for a
   250–700-member building) and returns the job folder: `pushover/`, `nlrha/`, `ddm_*`, the four viewers with
   `steltic_viewer_bundle.html`, `four_analyses.html`, its narrative, retrieval log and open items.
