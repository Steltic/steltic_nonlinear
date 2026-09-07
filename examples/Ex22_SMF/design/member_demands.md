# Ex22_SMF - member DEMAND envelope (ASCE 7-22 LRFD, second-order P-Delta)

Load combinations: 27 (gravity; seismic w/ Ev, rho, 100/30, +/-accidental torsion, Omega0 [col], wind). Each run as a factored P-Delta case; demands enveloped per element.

> **Capacities and D/C are NOT computed here.** The framework provides demands only; the design agent derives each AISC 360-22 limit-state capacity (compression E3, tension D2, flexure F2-F6, shear G2, beam-column interaction H1), the App.8 B2 amplifier, and the AISC 341 SCWB / Omega0 column check from the RAG, computes D/C, cites the clause, and records them in calc_package.json.

| member type | section | n | governing combo | P_comp | P_tens | Mx(k-ft) | My | V |
|---|---|---:|---|---:|---:|---:|---:|---:|
| roof | W24X76 | 38 | 1.2D+1.6Lr+0.5L | 0 | 0 | 493 | 0 | 66 |
| floor | W27X94 | 190 | 1.2D+1.6L+0.5Lr | 0 | 0 | 871 | 0 | 116 |
| roof | W33X141 | 12 | 1.2D+1.6Lr+0.5L | 0 | 0 | 493 | 0 | 66 |
| floor | W36X194 | 24 | 1.2D+1.6L+0.5Lr | 0 | 0 | 871 | 0 | 116 |
| floor | W36X232 | 36 | (1.2+0.2SDS)D+rhoEX+t++0.5L | 0 | 0 | 985 | 0 | 116 |
| roof | W36X232 | 8 | 1.2D+1.6Lr+0.5L | 0 | 0 | 493 | 0 | 66 |
| floor | W36X256 | 16 | (1.2+0.2SDS)D+rhoEY-t-+0.5L | 0 | 0 | 1033 | 0 | 116 |
| floor | W40X277 | 24 | (1.2+0.2SDS)D+rhoEY+t++0.5L | 0 | 0 | 1423 | 0 | 116 |
| gravity_col | W14X145 | 30 | 1.2D+1.6L+0.5Lr | 819 | 0 | 6 | 3 | 1 |
| gravity_col | W14X193 | 30 | 1.2D+1.6L+0.5Lr | 1283 | 0 | 23 | 11 | 2 |
| lateral_col | W14X500 | 40 | 1.2D+1.6L+0.5Lr | 195 | 17 | 796 | 374 | 107 |
| lateral_col | W14X605 | 40 | 1.2D+1.6L+0.5Lr | 451 | 111 | 1192 | 581 | 167 |
| lateral_col | W14X730 | 40 | 1.2D+1.6L+0.5Lr | 711 | 237 | 1593 | 644 | 198 |
| gravity_col | W14X90 | 30 | 1.2D+1.6L+0.5Lr | 355 | 0 | 10 | 6 | 1 |
