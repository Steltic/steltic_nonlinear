"""Smoke: record library, spectrum solver vs Newmark, target spectrum, scaling on the example package."""
import os, sys, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
EX = os.path.join(os.path.dirname(HERE), "examples", "Ex18_R3")


def test_library_and_spectrum():
    from nlrha import ground_motions as GM
    idx, recs = GM.library()
    assert len(recs) == 22 and all(r["npts"] > 1000 for r in recs)
    r = recs[0]; T = np.array([0.5, 1.0, 2.0])
    sa = GM.sdof_peak(r["a1"], r["dt"], T)
    assert np.all(sa > 0) and sa[0] > sa[2]
    tgt = GM.target_mcer(np.array([0.1, 0.45, 1.0, 10.0]), 0.2, 0.09, 8.0)
    assert abs(tgt[1] - 0.30) < 1e-9 and abs(tgt[2] - 0.135) < 1e-9


def test_scaling():
    from nlrha import ground_motions as GM
    idx, recs = GM.library()
    gm, chosen = GM.select_and_scale(recs, 0.2, 0.09, 8.0, 0.32, 3.52, n_select=11, verbose=False)
    assert len(chosen) == 11 and gm["passes_90pct"] and gm["orientation_ok"]
