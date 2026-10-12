"""NL-R2-06 (gravity roof levels): split-level buildings. A bay is a roof on the top level, on a cfg roof_levels
level, or when no bay of ANY higher level covers it (the HR engine's rule, static_model._finish_model) -- not when the
NEXT level up (possibly the other wing) does not cover it. Ex13 (office wing 14/26/38/50 ft, workshop wing 20/36 ft):
office floors k = 1..3 were read as roofs (Lr 20 psf instead of 65 / 125 psf, snow on them)."""
import json, os, sys, types
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def _ch16():
    return json.load(open(os.path.join(ROOT, "nlrha", "ch16_params.json")))


def _split_pkg(tmp_path, cfg_text):
    """Wing A (x 0..360): floor k1 z 144, roof k3 z 288. Wing B (x 360..720): roof k2 z 200. y 0..360. Column lines at
    x = 0 / 360 / 720; the x = 360 line is shared (nodes at 144, 200, 288)."""
    nodes, elements, schedule, tag = {}, [], {}, 0
    lv = {1: (144.0, (0, 1)), 2: (200.0, (1, 2)), 3: (288.0, (0, 1))}
    for i in range(3):
        for j in range(2):
            nodes[i * 10 + j] = (i * 360.0, j * 360.0, 0.0)
    for k, (z, ii) in lv.items():
        for i in ii:
            for j in range(2):
                nodes[k * 1000 + i * 10 + j] = (i * 360.0, j * 360.0, z)
    for i in range(3):
        for j in range(2):
            line = sorted([n for n in nodes if n % 1000 == i * 10 + j], key=lambda n: nodes[n][2])
            for a, b in zip(line[:-1], line[1:]):
                tag += 1; elements.append(dict(tag=tag, n1=a, n2=b)); schedule[tag] = {"member": "col", "section": "W14X90"}
    dia, masses = [], {}
    for k, (z, ii) in lv.items():
        m = k * 1000 + 500
        nodes[m] = (180.0 + 360.0 * (ii[0]), 180.0, z)
        dia.append((3, m, [n for n in nodes if k * 1000 <= n < k * 1000 + 500]))
        masses[m] = [100.0 / 386.4] * 3 + [0] * 3
    model = types.SimpleNamespace(nodes=nodes, fixes={n: [1] * 6 for n in nodes if n < 1000}, diaphragms=dia, elements=elements, masses=masses)
    (tmp_path / "cfg.py").write_text(cfg_text)
    return types.SimpleNamespace(model=model, schedule=schedule, root=tmp_path, basis=types.SimpleNamespace(L_floor_psf=None, sources={}))


def test_split_level_floor_covered_by_a_higher_level_of_its_own_wing(tmp_path):
    from nlrha import gravity as GR, model as MD
    pkg = _split_pkg(tmp_path, "cfg = dict(L_floor=65.0, Lr=20.0, snow=30.0)\n")
    trib, info = GR.tributary(pkg)
    t = {lv["k"]: lv for lv in trib}
    assert t[1]["floor_ft2"] == pytest.approx(900.0) and t[1]["roof_ft2"] == 0.0        # was a roof (k2 covers wing B only)
    assert t[2]["roof_ft2"] == pytest.approx(900.0) and t[3]["roof_ft2"] == pytest.approx(900.0)
    assert t[1]["L0_kip"] == pytest.approx(65.0 * 900 / 1000) and t[1]["Lr_kip"] == 0.0
    _, _, split = MD.ch16_gravity(pkg, _ch16())
    assert split["sum_Lexp"] == pytest.approx(0.2 * (65.0 * 900 + 20.0 * 1800) / 1000.0)
    S, _ = GR.column_snow_axial(pkg)                                                   # no snow on the k1 floor
    col_k1 = next(e["tag"] for e in pkg.model.elements if e["n1"] == 0 and e["n2"] == 1000)
    assert S[col_k1] == pytest.approx(30.0 * 225.0 / 1000.0)                          # only the k3 roof quarter above


def test_cfg_roof_levels_declares_a_roof(tmp_path):
    from nlrha import gravity as GR
    pkg = _split_pkg(tmp_path, "cfg = dict(L_floor=65.0, Lr=20.0, roof_levels=[1])\n")
    trib, info = GR.tributary(pkg)
    t = {lv["k"]: lv for lv in trib}
    assert info["roof_levels"] == [1] and info["sources"]["roof_levels"] == "cfg.py"
    assert t[1]["roof_ft2"] == pytest.approx(900.0) and t[1]["floor_ft2"] == 0.0
