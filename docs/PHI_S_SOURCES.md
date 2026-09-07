# φ_s source hunt — where the calibrated tables actually live (status 2026-09-07 — CLOSED for hot-rolled buildings)

The DDM check is φ_s·λ_u ≥ 1.0. The journal papers with the tabulated system resistance
factors are paywalled; this note records the **open** copies found, what each contains, and what is
still missing. Values marked VERIFIED were read from an open document by this project; values
marked PROVISIONAL come from abstracts / secondary citations only.

## Open documents that contain φ_s tables

| document | access | what it contains | status |
|---|---|---|---|
| Zhang H., Rasmussen K.J.R., Shayan S., Ellingwood B.R., *System reliability of steel frames designed by inelastic analysis*, SSRC Annual Stability Conference 2013 (St. Louis) — PDF hosted by AISC: <https://www.aisc.org/globalassets/continuing-education/ssrc-proceedings/2013/system-reliability-of-steel-frames-designed-by-inelastic-analysis.pdf> | **open** | Tables 6, 7, 9, 10: φ_s vs β_T for 9 planar moment frames + 8 Ziemian frames, gravity 1.2G+1.5Q, failure modes BFY-CPY / CFY / BPY / sway. Statistical models (F_y LN 1.05F_n COV 0.10; E N COV 0.06; D N 1.05 COV 0.10; L ET-I COV 0.25; residual-stress scale N 1.047 COV 0.21). | **READ — VERIFIED** (see table below) |
| Shayan S., *System reliability-based design of 2D steel frames by advanced analysis*, PhD thesis, University of Sydney 2013 — <https://ses.library.usyd.edu.au/handle/2123/10196> (bitstream `shayan_s_thesis.pdf`) | **open access** (repository returned HTTP 503 on 2026-09-05 — down, not restricted) | Full derivation behind Zhang et al. 2016 I/II: braced + moment frames, ductile vs non-ductile modes, L/D ratios, the recommended φ_s table. | TO READ when the repository is back |
| Wan W., *System reliability of semi-rigid steel frames designed with Direct Design Method*, PhD thesis, University of Sydney 2024 — <https://ses.library.usyd.edu.au/handle/2123/32474> (bitstream `wan_w_thesis.pdf`, 257 pp.) | **open access** | Ch. 6: system reliability of five planar hot-rolled frames (incl. two from Shayan 2013) under 1.2D + 0.5L + 1.0W with rigid and three semi-rigid connection types, φ_s = 0.80 and 0.85, W_T/G_T = 0.10 and 0.15; Tables 6.10–6.15; β–φ_s law Eq. 6.9. The literature review (Ch. 2) does NOT re-tabulate the earlier φ_s values — it covers connections and reliability methods. | **READ — VERIFIED** (see below) |
| Arrayago I., Rasmussen K.J.R., *System-based reliability analysis of stainless steel frames under gravity loads*, Eng. Struct. 2021 — UPCommons postprint <https://upcommons.upc.edu/bitstreams/4d39f717-86c0-4aec-bf07-f67aaebdc654/download> | **open** | Same framework applied to stainless frames: φ_s(US, β=3.0) = 0.88–1.02, recommended 0.95; φ_s(AU, β=3.0) = 0.85–0.97, recommended 0.90; γ_M,s(EC, β=3.8) = 1.15. Non-sway frames higher than sway. Load ratio α = L/D 1–3. Does **not** tabulate the carbon-steel values. | READ — context only |
| Tran H., Thai H.-T., Uy B., Hicks S.J., Kang W.-H., *System reliability-based design of steel-concrete composite frames with CFST columns and composite beams*, 2022 — Warwick WRAP PDF <https://wrap.warwick.ac.uk/id/eprint/165275/> | **open** | Composite frames: φ = 0.78 gravity (L/D = 3), 0.80 gravity+wind (US and AS). Same Sydney/Monte-Carlo framework. | READ — context only |
| Zhang H. et al., *On the system-based design for steel frames using inelastic analysis*, PLSE 2015 — UQ eSpace <https://espace.library.uq.edu.au/view/UQ:399357/PLSE2015_85_ZHANG_On_The_System.pdf> | open (403 to automated fetch) | Conference summary of the 2016 JCSR pair. | TO READ manually |
| Sena Cardoso F. et al. 2019 (CFS portal frames) — a copy is on Scribd (document 408663801) | requires JS/login | Tables of φ_s for CFS portal frames by failure mode and β_T. | not retrievable by tool |

Project page listing all 19 publications of the programme: Sydney Structures Group,
<https://structuresgroup-eng.sydney.edu.au/research-projects/project-page-3/system-reliability-based-criteria-for-designing-steel-structures-by-advanced-analysis/>.
No "R-series" School of Civil Engineering research report for this programme was found in the
searches; the theses are the open long-form record.

## VERIFIED values (SSRC 2013, Zhang–Rasmussen–Shayan–Ellingwood)

Gravity combination 1.2G + 1.5Q (AS 4100 style), planar low-to-mid-rise moment frames, GMNIA with
random F_y, E, geometry, imperfections, residual stresses; bias R̄/R_n = 1.02–1.12, COV_R = 0.093–0.106.

| target β | φ_s Frame 5 (BFY-CPY) | φ_s Ziemian frames (avg) | φ_s 9 proposed frames (avg, range) |
|---|---|---|---|
| 2.5 | 0.89 | 0.89 | 0.90 (0.85–0.94) |
| 2.75 | 0.84 | — | 0.85 |
| 3.0 | 0.79 | 0.79 | **0.80** |
| 3.5 | 0.71 | — | 0.70 |

Failure modes BFY-CPY (beam flexural yielding + column partial yielding), CFY, BPY-CPY, BPY-CFY, sway
instability all fall in the 0.85–0.94 band at β = 2.5 — ductility mattered less than expected in that
data set. The JCSR 2016-II paper refined this into the final recommendations (still to be transcribed).

## Open CC-BY paper that quotes the carbon-steel WIND values (found 2026-09-05)

**Arrayago I., Rasmussen K.J.R., Zhang H., *System-based reliability analysis of stainless steel frames subjected
to gravity and wind loads*, Structural Safety 97 (2022) 102211** — CC-BY 4.0, PDF:
<https://upcommons.upc.edu/bitstreams/127a7111-3b48-4061-a24f-6e6dd8e15431/download>. Section 5.3, verbatim:

> "The φs-factors corresponding to a target value of about β0 = 2.50 proposed in previous studies for steel frames
> subjected to combined gravity and wind loads in the US framework were φs = 0.80 − 0.85 for hot-rolled planar frames
> [11], φs = 0.85 for hot-rolled spatial frames [12], φs = 0.85 − 0.90 for cold-formed spatial frames [13] and
> φs = 0.85 for cold-formed portal frames with locally unstable members [14]."

with [11] = Zhang, Shayan, Rasmussen & Ellingwood 2016 II; [12] = Liu, Zhang, Rasmussen & Yan 2019 (3-D frames, wind);
[13] = Liu, Zhang & Rasmussen 2018 (CF-HSS space frames); [14] = Sena Cardoso, Zhang, Rasmussen & Yan 2019 (CFS portals).
Same paper: ASCE 7 / AS 5104 targets for non-sudden failure modes are β0 = 2.5 (RC I) and 3.0 (RC II) for a 50-year
reference; their own stainless recommendation for gravity + wind is φs = 0.90 (US and AU) at β0 = 3.0.
Wind statistics used (Table 2): US mean 0.75 W_n,50 (0.47 W_n,700), COV 0.35, Extreme Type I; AU mean 0.68 W_n,50, COV 0.39.

Related open documents from the same programme (context, no carbon-steel tables):
Arrayago & Rasmussen 2021, Eng. Struct. 231 (gravity, stainless): φs = 0.95 US / 0.90 AU at β0 = 3.0 —
<https://upcommons.upc.edu/bitstreams/4d39f717-86c0-4aec-bf07-f67aaebdc654/download>;
Arrayago & Rasmussen 2022, JCSR 190 (imperfection direction): "all buckling modes with positive amplitudes" is within
1 % of the critical direction; max/min λ_u ratios 0–6 % — <https://upcommons.upc.edu/bitstreams/45a7661d-fcfd-42be-8144-90e772125723/download>
(this supports the engine's rule of leaning the frame with the lateral load and not searching directions).
Full publication list of the Marie Curie project NewGeneSS: <https://cordis.europa.eu/project/id/842395/results>.

## VERIFIED values (Wan 2024 — gravity + wind, hot-rolled planar frames)

Load model (Table 6.1): F_y LN 1.10/0.06; E LN 1.00/0.04; D N 1.05/0.10; L_apt Gamma 0.24/0.60; W_max ET-I 0.575/0.37;
A, I N 1.00/0.05; imperfection scale X N 1.05/0.20. Limit state g = R_w − W (lateral capacity with D + L applied);
FORM-based estimate validated against 250,000-sample direct Monte Carlo within 2 %. Nominal design: W_n = φ_s λ_u W_T,
1.2D_n + 0.5L_n = φ_s λ_u G_T (Eq. 6.7). Frames: 1 (portal), 2, 3 and 4 (Shayan 2013 frames), 5 (column-governed).

Rigid-connection rows of Tables 6.10–6.14 (β):

| frame | W/G 0.10, φ_s 0.80 | W/G 0.10, φ_s 0.85 | W/G 0.15, φ_s 0.80 | W/G 0.15, φ_s 0.85 |
|---|---|---|---|---|
| 1 | 3.29 | 3.02 | 3.02 | 2.80 |
| 2 | 3.16 | 2.94 | 2.93 | 2.73 |
| 3 | 3.03 | 2.81 | 2.84 | 2.65 |
| 4 | 3.06 | 2.82 | 2.87 | 2.66 |
| 5 | 3.01 | 2.79 | (2.82 at 0.85) | — |
| **mean** | **3.02 at φ_s = 0.80** | | **2.80 at φ_s = 0.85** | |

Linear law through the means: β = −4.42 φ_s + 6.56 → **φ_s = 0.92 / 0.80 / 0.69 at β_T = 2.5 / 3.0 / 3.5**.
Semi-rigid frames, all connection types averaged (Table 6.15, Eq. 6.9): β = −4.41 φ_s + 6.65 → 0.94 / 0.83 / 0.71;
semi-rigid frames are slightly *more* reliable than rigid ones (connection strength test-to-nominal 1.2). Wan's frames are
2-D, fully laterally restrained, compact — the same idealisation as steltic_ddm's fibre model (LTB / local buckling
excluded), and the same restriction applies (§7.2(a) of the thesis).

Status of the five open items after the thesis: (1) HSS / CFS-portal **gravity** rows — still abstract-level (Wan is
wind-only); (2) **wind row at β = 3.0 — CLOSED** (0.80, rigid; 0.83 semi-rigid); (3) ductile / non-ductile split —
not needed: Wan's rigid data span beam-hinge and column-governed frames (Frame 5) within ±0.1 in β, so one curve is
used; (4) exact Zhang 2016 II gravity table — SSRC 2013 value 0.80 retained, Wan's 2.5-row (0.92) is above the cited
0.80–0.85, so the cited 0.85 is kept as the conservative cap; (5) seismic on R > 3 — no calibration exists (Wan §7.2(b)
lists it as future work).

## What steltic_ddm uses now (`phi_s.py`)

| class | value (RC II) | β_T | status | basis |
|---|---|---|---|---|
| HR-G (hot-rolled, gravity) | **0.80** | 3.0 | VERIFIED | SSRC 2013 Table 10 (0.90/0.85/0.80/0.70 at β 2.5/2.75/3.0/3.5 — used for other Risk Categories) |
| HR-W (hot-rolled, gravity + wind) | **0.85 / 0.80 / 0.69** at β 2.5 / 3.0 / 3.5 | curve | VERIFIED | Wan 2024 rigid rows (0.92 at β 2.5 capped by the cited 0.85; 0.80 at 3.0; 0.69 at 3.5) |
| HR-SR-W (hot-rolled, semi-rigid joints, gravity + wind) | 0.86 / 0.83 / 0.71 | curve | VERIFIED | Wan 2024 Eq. 6.9 |
| HSS-G (CF-HSS governs, gravity) | 0.80 | 3.0 | PROVISIONAL | lower end of the Liu 2018 abstract range |
| HSS-W (CF-HSS space frame, gravity + wind) | **0.85** | 2.5 | VERIFIED-CITED | Liu 2018 as quoted (0.85–0.90), lower bound |
| CFS-P-G / CFS-P-W (CFS portal) | 0.85 / 0.85 | 3.0 / 2.5 | PROVISIONAL / VERIFIED-CITED | Sena Cardoso 2019 |
| CFS-W (stud-wall) | — | — | uncalibrated | report λ_u only |
| SEIS (R > 3 seismic pattern) | — | — | n/a | no pass/fail |

Risk Category: `phi_s.choose(..., risk_category=)` moves HR-G along the SSRC 2013 curve and HR-W along the Wan 2024
curve (RC IV → 0.70 gravity, 0.80 wind at β_T 3.5 / 3.0); HSS and CFS classes are known at one β only and are scaled
with the matching hot-rolled curve, flagged `provisional-extrapolated`, for RC III/IV.

Ex18 (RC II): governing check 0.80 × 1.278 = **1.023** (gravity, HR-G, verified); wind-Y 0.85 × 1.891 = 1.61 (HSS-W).

## To close the flag

1. When <https://ses.library.usyd.edu.au> is back: download `shayan_s_thesis.pdf` (handle 2123/10196)
   and `wan_w_thesis.pdf` (handle 2123/32474) — both open access — drop them in the job folder
   (`ddm_refs/`) and ask the DDM agent to transcribe the recommendation tables into `phi_s.TABLE`,
   setting `PROVISIONAL = False` per class as each is verified.
2. Wan 2024 §2 (literature review) should give the Liu 2018 (HSS space frames), Liu 2019 (wind) and
   Sena Cardoso 2019 (CFS portal) tables in one place.
3. Keep the SSRC 2013 PDF in `ddm_refs/` as the verified anchor for HR-B.
