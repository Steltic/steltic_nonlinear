"""Unit tests for RBS 7-seg remesh helpers (no full-building build)."""
import math
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def test_segment_stations_and_props():
    from pushover import rbs_remesh as RBS
    L, a, b, c = 360.0, 3.75, 16.0, 1.75
    st = RBS.segment_stations(L, a, b)
    assert len(st) == 7
    assert abs(st[0][1] - a) < 1e-9
    assert abs(st[1][1] - (a + 0.5 * b)) < 1e-9
    assert abs(sum(s1 - s0 for s0, s1, _ in st) - L) < 1e-9
    assert [k for *_, k in st] == ["full", "rbs", "rbs", "full", "rbs", "rbs", "full"]
    # hinge offsets
    assert abs(st[1][1] - (a + b / 2)) < 1e-12
    assert abs(st[5][0] - (L - (a + b / 2))) < 1e-12
    red = RBS.reduced_props("W24X55", c, A=16.2, I_strong=1350.0, I_weak=29.1, J=0.7)
    assert red["bf_r"] < 7.01
    assert red["A"] < 16.2
    assert red["I_strong"] < 1350.0
    assert 0.5 < red["ratio_I"] < 1.0


def test_want_rbs_gate():
    from pushover import rbs_remesh as RBS
    bp = {
        "rbs_segments": 7,
        "rbs_geometry_nist_fig_2_11b": {
            "W24X55": {"a_in": 3.75, "b_in": 16.0, "c_in": 1.75},
            "note": "x",
        },
    }
    prm = {"beam_flexure": bp}
    g = RBS.want_rbs_remesh("beam", "W24X55", True, True, prm)
    assert g and g["a_in"] == 3.75
    assert RBS.want_rbs_remesh("col", "W24X55", True, True, prm) is None
    assert RBS.want_rbs_remesh("beam", "W24X55", True, False, prm) is None
    prm2 = {"beam_flexure": {**bp, "rbs_segments": 0}}
    assert RBS.want_rbs_remesh("beam", "W24X55", True, True, prm2) is None
