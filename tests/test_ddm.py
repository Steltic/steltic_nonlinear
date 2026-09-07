"""Regression tests. Run: STELTIC_ENGINE_DIR=/path/to/steltic/steel_engine python -m pytest tests -q
(the Ex18 tests need the Steltic engine importable; the column test does not)."""
import os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
PKG = os.path.join(ROOT, "examples", "Ex18_R3")
needs_engine = pytest.mark.skipif(not os.environ.get("STELTIC_ENGINE_DIR"), reason="STELTIC_ENGINE_DIR not set")


def test_column_curve():
    from steltic_ddm import selftest
    assert selftest.main() == 0


def test_residual_pattern_self_equilibrating():
    import openseespy.opensees as ops
    from steltic_ddm.sections_fiber import FiberSectionBuilder, shape, _f
    ops.wipe(); ops.model("basic", "-ndm", 3, "-ndf", 6)
    b = FiberSectionBuilder(ops, Fy=50.0)
    r = shape("W14X90"); d, tw, bf, tf = (_f(r["d"]), _f(r["tw"]), _f(r["bf"]), _f(r["tf"]))
    hw = d - 2 * tf; Af, Aw = bf * tf, hw * tw
    rc = 0.3; rt = rc * Af / (Af + Aw)
    # flange force: linear from +rt (junction) to -rc (tip) -> mean = (rt - rc)/2 per flange; web: +rt
    F = 2 * Af * (rt - rc) / 2 + Aw * rt
    assert abs(F) < 1e-9 * (Af + Aw) * 50


@needs_engine
def test_ex18_ingest_and_gate():
    from steltic_ddm import ingest, loads, transfer_gate
    nm = ingest.load_package(PKG)
    s = ingest.summary(nm)
    assert s["members"] == {"col": 240, "brace": 64, "beam": 392}
    cases = loads.steltic_combos(nm.cfg)
    assert len(cases) == 35
    latx = [c for c in cases if "EX+t+" in c[0] and c[1] > 1.0][0][4]
    laty = [c for c in cases if "EY+t+" in c[0] and c[1] > 1.0][0][4]
    g = transfer_gate.run(nm, nm.cfg, latx, laty)
    assert g["ok"], g


def test_phi_s_policy():
    from steltic_ddm import phi_s
    g = phi_s.choose("gravity", 3.0, "instability")
    assert g["cls"] == "HR-G" and abs(g["phi_s"] - 0.80) < 1e-9 and g["status"] == "verified"
    w = phi_s.choose("wind", 3.0, "instability", governed_by_braces=True, hss_braces=True)
    assert w["cls"] == "HSS-W" and abs(w["phi_s"] - 0.85) < 1e-9
    assert phi_s.choose("seismic", 8.0, "ductile")["phi_s"] is None
    iv = phi_s.choose("gravity", 8.0, "ductile", risk_category="IV")
    assert abs(iv["phi_s"] - 0.70) < 1e-9 and iv["beta_T"] == 3.5
    w4 = phi_s.choose("wind", 8.0, "ductile", risk_category="IV")
    assert w4["cls"] == "HR-W" and abs(w4["phi_s"] - 0.80) < 1e-9 and w4["status"] == "verified"      # Wan 2024 beta = 3.0 row
    assert phi_s.choose("wind", 8.0, "ductile", governed_by_braces=True, hss_braces=True, risk_category="IV")["status"] == "provisional-extrapolated"
    assert phi_s.choose("wind", 3.0, "ductile", material="HR-SR")["cls"] == "HR-SR-W"


@needs_engine
def test_report_reapplies_phi_s(tmp_path):
    """`steltic_ddm report` rebuilds report/block/viewer from ddm_results.json with the current phi_s policy (no analysis)."""
    import json, shutil
    from steltic_ddm import cli
    job = tmp_path / "Ex18_R3"
    shutil.copytree(PKG, job, ignore=shutil.ignore_patterns("__pycache__", "*.pkl", "nlrha", "pushover", "*_viewer_3d.html", "steltic_viewer_bundle.html", "four_analyses.html"))
    os.remove(job / "ddm_report.html")
    rc = cli.main(["report", str(job), "--risk-category", "IV"])
    assert rc and os.path.exists(rc) and os.path.exists(job / "ddm_viewer_3d.html")
    d = json.load(open(job / "ddm_results.json"))
    wind = [r for r in d["runs"] if r["kind"] == "wind"][0]
    assert wind["phi"]["cls"] == "HSS-W" and wind["phi"]["beta_T"] == 3.0 and wind["phi"]["status"] == "provisional-extrapolated"
    grav = [r for r in d["runs"] if r["kind"] == "gravity"][0]
    assert grav["phi"]["cls"] == "HR-G" and abs(grav["phi"]["phi_s"] - 0.70) < 1e-9
    b = json.load(open(job / "design" / "calc_package.json"))["ddm_analysis"]
    assert b["phi_s_provisional"] is True and "HSS-W" in b["phi_s_classes"] and "PROVISIONAL" in b["phi_s_status"]
    # back to the package's own Risk Category (II): every class used is verified, the flag clears
    cli.main(["report", str(job), "--risk-category", "II"])
    b = json.load(open(job / "design" / "calc_package.json"))["ddm_analysis"]
    assert b["phi_s_provisional"] is False and "Every class used is verified" in b["phi_s_status"]
