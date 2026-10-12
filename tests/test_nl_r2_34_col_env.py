"""NL-R2-34 (columns): the force-controlled column envelope (16.4.2.1) and its concurrent instants are folded in at
EVERY committed step, not only at the viewer frames."""
import inspect, math, os, sys

import openseespy.opensees as ops
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from nlrha import run as RN  # noqa: E402


def _stub(seq):
    it = iter(seq)

    def forces(ci):
        P, Ma, Mi = next(it)
        return P, Ma, Mi, ((P, Ma, Mi), (P, 0.5 * Ma, 0.5 * Mi))
    return forces


def test_update_col_env_envelope_and_concurrent_instants():
    env, peak = RN.new_col_env([7])
    ci = {7: dict(proxy=(1000.0, 10000.0, 5000.0))}
    seq = [(100.0, 1000.0, 100.0), (400.0, 2000.0, 200.0), (50.0, 8000.0, 1000.0), (-300.0, 3000.0, 300.0), (-50.0, 6000.0, 2500.0)]
    f = _stub(seq)
    for _ in seq:
        RN.update_col_env(env, peak, ci, forces=f)
    e = env[7]
    assert e["Pc"] == 400.0 and e["Pt"] == -300.0 and e["Mmaj"] == 8000.0 and e["Mmin"] == 2500.0 and peak[7] == 400.0
    # most utilised instant max(P, 0) / A Fye + moments: 0.64 at step 2, 1.05 at step 3, 0 + 0.6 + 0.5 = 1.10 at the
    # last (tension) step -> the last step
    assert e["conc_c"] == (-50.0, 6000.0, 2500.0)
    # tension: 0.3 + 0.3 + 0.06 = 0.66 vs 0.05 + 0.6 + 0.5 = 1.15 -> last step
    assert e["conc_t"] == (-50.0, 6000.0, 2500.0)
    # largest moment pair while in compression (Exception 2 C3-9 moments): step 3, never a tension instant
    assert e["conc_m"] == (50.0, 8000.0, 1000.0)


def test_run_record_tracks_the_column_envelope_every_committed_step():
    src = inspect.getsource(RN.run_record)
    i_step = src.index("step += 1")
    i_col = src.index("update_col_env(col_env, peak_colN, colinfo)")
    i_frame = src.index("if t >= next_rec - 1e-9:")
    assert i_step < i_col < i_frame
    assert src.count("_column_forces(") == 1 and src.count("update_col_env(") == 1      # the one left: the gravity state


def test_frame_sampling_misses_a_column_peak_that_every_step_tracking_keeps():
    """A real 3D elastic column under an axial + lateral pulse that peaks between two viewer frames."""
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0, 0, 0); ops.node(2, 0, 0, 144.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.geomTransf("Linear", 1, 0, 1, 0)
    ops.element("elasticBeamColumn", 7, 1, 2, 26.5, 29000.0, 11200.0, 4.06, 999.0, 362.0, 1)
    ops.timeSeries("Path", 1, "-time", 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, "-values", 0, 0.2, 0.4, 0.6, 1.0, 0.5, 0.3, 0.2, 0.1, 0.0, 0.0)
    ops.pattern("Plain", 1, 1); ops.load(2, 3.0, 0, -300.0, 0, 0, 0)
    ops.system("UmfPack"); ops.numberer("Plain"); ops.constraints("Plain"); ops.test("NormDispIncr", 1e-10, 10)
    ops.algorithm("Newton"); ops.integrator("LoadControl", 1.0); ops.analysis("Static")
    ci = {7: dict(ti=7, tj=7, maj=(5, 11), mnr=(4, 10), proxy=(1457.5, 157 * 55.0, 75.6 * 55.0))}
    every = RN.new_col_env([7]); sampled = RN.new_col_env([7])
    for k in range(1, 10):
        assert ops.analyze(1) == 0
        RN.update_col_env(*every, ci)
        if k % 3 == 0:                                                # the former frame-only update
            RN.update_col_env(*sampled, ci)
    assert every[0][7]["Pc"] == pytest.approx(300.0) and sampled[0][7]["Pc"] < 0.7 * 300.0
    assert every[0][7]["Mmaj"] == pytest.approx(3.0 * 144.0, rel=1e-6) and sampled[0][7]["Mmaj"] < every[0][7]["Mmaj"]
    assert every[1][7] == pytest.approx(300.0) and every[0][7]["conc_m"][1] == pytest.approx(3.0 * 144.0, rel=1e-6)
