# Ex18_R3 - member DEMAND envelope (ASCE 7-22 LRFD, second-order P-Delta)

Load combinations: 35 (gravity; seismic w/ Ev, rho, 100/30, +/-accidental torsion, Omega0 [col], wind). Each run as a factored P-Delta case; demands enveloped per element.

> **Capacities and D/C are NOT computed here.** The framework provides demands only; the design agent derives each AISC 360-22 limit-state capacity (compression E3, tension D2, flexure F2-F6, shear G2, beam-column interaction H1), the App.8 B2 amplifier, and the AISC 341 SCWB / Omega0 column check from the RAG, computes D/C, cites the clause, and records them in calc_package.json.

| member type | section | n | governing combo | P_comp | P_tens | Mx(k-ft) | My | V |
|---|---|---:|---|---:|---:|---:|---:|---:|
| roof | W24X62 | 49 | 1.2D+1.6Lr+0.5L | 0 | 0 | 343 | 0 | 46 |
| floor | W24X84 | 343 | 1.2D+1.6L+0.5Lr | 0 | 0 | 546 | 0 | 73 |
| brace | HSS6X6X3/8 | 32 | 1.2D+1.0WY-+0.5L+0.5Lr | 93 | 70 | 0 | 0 | 0 |
| brace | HSS8X8X1/2 | 32 | 1.2D+1.0WY++0.5L+0.5Lr | 199 | 149 | 0 | 0 | 0 |
| gravity_col | W14X109 | 44 | 1.2D+1.6L+0.5Lr | 1112 | 0 | 20 | 7 | 2 |
| lateral_col | W14X120 | 16 | (1.2+0.2SDS)D+Om0*EY-+0.5L [col] | 1025 | 546 | 28 | 6 | 3 |
| lateral_col | W14X159 | 16 | (1.2+0.2SDS)D+Om0*EY-+0.5L [col] | 1560 | 912 | 31 | 12 | 4 |
| gravity_col | W14X61 | 44 | 1.2D+1.6L+0.5Lr | 224 | 0 | 6 | 1 | 1 |
| lateral_col | W14X61 | 16 | (1.2+0.2SDS)D+Om0*EY-+0.5L [col] | 188 | 45 | 6 | 1 | 1 |
| gravity_col | W14X82 | 44 | 1.2D+1.6L+0.5Lr | 520 | 0 | 25 | 6 | 2 |
| lateral_col | W14X82 | 16 | (1.2+0.2SDS)D+Om0*EY-+0.5L [col] | 555 | 243 | 28 | 6 | 2 |
| gravity_col | W14X99 | 44 | 1.2D+1.6L+0.5Lr | 816 | 0 | 25 | 6 | 2 |
