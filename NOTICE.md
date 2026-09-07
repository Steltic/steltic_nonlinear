# Notices

* `pushover/aisc_shapes.csv` and `steltic_ddm/data/aisc_shapes.csv` is the AISC shapes database as redistributed in the MIT-licensed
  Steltic repository (github.com/Steltic/steltic, steel_engine/aisc_shapes.csv). AISC retains its rights
  in the underlying data.
* No text of AISC 360/341/358, ASCE 7 or AISI S100/S240/S400 is included. Clause numbers named in the
  contract and skill are retrieval targets for the Query file manager, not quotations.
* The system resistance factors in `steltic_ddm/phi_s.py` are literature values (no specification clause):
  the hot-rolled gravity and gravity + wind classes are transcribed from open documents (Zhang, Rasmussen, Shayan &
  Ellingwood, SSRC 2013, Table 10; Wan 2024 PhD thesis, University of Sydney, Tables 6.10-6.14 / Eq. 6.9); the
  CF-HSS and CFS-portal gravity classes remain provisional and the engine flags them. See `docs/PHI_S_SOURCES.md`.
* `examples/Ex18_R3` and `examples/Ex22_SMF` are Steltic HR Steel App output packages for test buildings Ex18 (R = 3,
  8 storeys) and Ex22 (Risk Category IV SMF, 6 storeys), frozen for regression. Not for construction.
* `pushover/vendor/three.min.js`, `nlrha/vendor/three.min.js` and `steltic_ddm/vendor/three.min.js` are three.js r128 (MIT, © 2010-2021 three.js authors; licence in
  `steltic_ddm/vendor/THREE_LICENSE`), vendored so that `ddm_viewer_3d.html` opens from disk without network
  access. `viewer_core.py` / `viewer_core.html` are the viewer core shared with the steltic_pushover and
  steltic_nlrha repositories (Steltic viewer bundle) — keep the three copies identical when editing.
* `records/p695_farfield` is the FEMA P-695 far-field ground-motion set (PEER NGA records as distributed with FEMA P-695,
  public domain / PEER terms); see `records/p695_farfield/index.json` for the record list and scaling metadata.
* `Steltic_nonlinear` merges the former `steltic_pushover`, `steltic_nlrha` and `steltic_ddm` repositories (MIT) unchanged
  in their package names; their individual READMEs are kept in `docs/`.
