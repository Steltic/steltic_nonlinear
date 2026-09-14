"""Site-specific hazard: canned USGS responses (no network), the conditional spectrum, the near-fault screen, user
record libraries and the selection against a site-specific target."""
import csv, json, os, shutil, sys, tempfile
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

from nlrha import site_hazard as SH, ground_motions as GM  # noqa: E402

DESIGN = json.load(open(os.path.join(FIX, "usgs_design_la.json")))
DEAGG = json.load(open(os.path.join(FIX, "usgs_disagg_la.json")))


def test_imt_labels():
    assert SH.imt_for(1.0) == ("SA1P0", 1.0) and SH.imt_for(0.2) == ("SA0P2", 0.2) and SH.imt_for(0.075) == ("SA0P075", 0.075)
    assert SH.imt_for(1.1)[0] == "SA1P0" and SH.imt_for(8.0)[0] == "SA7P5" and SH.imt_for(0.0)[0] == "SA0P01"


def test_baker_jayaram_correlation():
    assert abs(SH.rho_baker_jayaram(1.0, 1.0) - 1.0) < 1e-9
    assert abs(SH.rho_baker_jayaram(0.5, 1.0) - SH.rho_baker_jayaram(1.0, 0.5)) < 1e-12
    assert 0.6 < SH.rho_baker_jayaram(0.5, 1.0) < 0.9 and SH.rho_baker_jayaram(0.1, 3.0) < 0.4
    assert SH.rho_baker_jayaram(0.05, 0.08) > 0.9


def test_conditional_mean_spectrum_pins_the_conditioning_period():
    periods = np.sort(np.append(np.geomspace(0.05, 5.0, 40), 1.0))
    uhs = SH.interp_spectrum(DESIGN["multiPeriodMCErSpectrum"]["periods"], DESIGN["multiPeriodMCErSpectrum"]["ordinates"], periods)
    cms, sig = SH.conditional_mean_spectrum(periods, uhs, 1.0, 1.12)
    i = int(np.argmin(np.abs(periods - 1.0)))
    assert abs(cms[i] / uhs[i] - 1.0) < 0.03                              # rho -> 1 at T*
    assert (cms <= uhs + 1e-9).all() and cms[0] < 0.85 * uhs[0]           # below the UHS away from T*, more so far away
    assert abs(sig[i]) < 0.1 and sig[0] > 0.4                              # conditional sigma vanishes at T*
    # a zero epsilon gives the UHS back
    cms0, _ = SH.conditional_mean_spectrum(periods, uhs, 1.0, 0.0)
    assert np.allclose(cms0, uhs)


def test_build_site_hazard_offline_and_targets():
    hz = SH.build_site_hazard(34.05, -118.25, 0.97, 1.12, site_class="D", risk_category="III", design=DESIGN, deaggs=DEAGG, fetch=False,
                              conditioning_periods=[1.0, 0.3])
    assert hz["design"]["sds"] == 1.52 and hz["site"]["vs30"] == 260
    cs = hz["targets"]["cs"]
    assert [c["T_star"] for c in cs] == [1.0, 0.3] and cs[0]["M"] == 7.31 and cs[0]["eps"] == 1.12 and cs[0]["imt"] == "SA1P0"
    env = hz["envelope"]; assert 0.5 < env["min_ratio_to_mcer"] <= 1.0
    nf = hz["near_fault"]
    assert nf["near_fault"] and any("Puente Hills" in s["name"] for s in nf["sources"]) and 0.4 < nf["pulse_fraction"] <= 1.0
    p, sa, label = SH.target_from_hazard(hz, "mcer"); assert len(p) == len(sa) == 22 and "multi-period" in label
    p, sa, label = SH.target_from_hazard(hz, "cs", 1); assert "T* = 0.30" in label
    html = SH.hazard_summary_html(hz); assert "NEAR-FAULT" in html and "7.31" in html
    json.dumps(hz)


def test_build_site_hazard_needs_a_deagg_when_offline():
    try:
        SH.build_site_hazard(34.05, -118.25, 0.97, 1.12, design=DESIGN, deaggs={}, fetch=False, conditioning_periods=[2.0]); assert False
    except RuntimeError as e:
        assert "disaggregation" in str(e)


def _user_library(tmp):
    src = os.path.join(ROOT, "records", "p695_farfield"); idx = json.load(open(os.path.join(src, "index.json")))
    rows = []
    for k, r in enumerate(idx["records"][:3]):
        rsn = 1000 + k
        names = ["RSN%d_EQ_H1.AT2" % rsn, "RSN%d_EQ_H2.AT2" % rsn]
        shutil.copy(os.path.join(src, r["comp1"]), os.path.join(tmp, names[0])); shutil.copy(os.path.join(src, r["comp2"]), os.path.join(tmp, names[1]))
        rows.append([rsn, r["earthquake"], r["year"], r["station"], r["M"], r["mechanism"], r["r_rup_km"] - 1, r["r_rup_km"], r["vs30"], r["lowest_freq_hz"], names[0], names[1], "Yes" if k == 0 else "No"])
    with open(os.path.join(tmp, "_SearchResults.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["-- Summary --"])
        w.writerow(["Record Sequence Number", "Earthquake Name", "Year", "Station Name", "Earthquake Magnitude", "Mechanism", "Rjb (km)", "Rrup (km)", "Vs30 (m/sec)", "Lowest Useable Frequency (Hz)", "Horizontal-1 Acc. Filename", "Horizontal-2 Acc. Filename", "Pulse"])
        w.writerows(rows)
    r = idx["records"][3]
    for c, tag in ((r["comp1"], "X"), (r["comp2"], "Y")):
        dt, a = GM.read_at2(os.path.join(src, c))
        with open(os.path.join(tmp, "site_%s.csv" % tag), "w") as f:
            f.write("t,a\n"); f.writelines("%.4f,%.6f\n" % (i * dt, v) for i, v in enumerate(a))
    # a vertical component that must be ignored
    shutil.copy(os.path.join(src, r["comp1"]), os.path.join(tmp, "RSN1000_EQ_UP.AT2"))


def test_scan_folder_and_merged_library():
    tmp = tempfile.mkdtemp(); _user_library(tmp)
    idx = SH.scan_folder(tmp)
    recs = {r["id"]: r for r in idx["records"]}
    assert len(recs) == 4 and recs[1000]["pulse"] and not recs[1001]["pulse"] and recs[1000]["M"] == 6.7 and recs[1000]["r_rup_km"] > 0
    assert os.path.exists(os.path.join(tmp, "index.json"))
    csvrec = next(r for r in idx["records"] if r["comp1"].endswith(".csv"))
    assert csvrec["M"] is None
    sets, lib = SH.library_from([os.path.join(ROOT, "records", "p695_farfield"), tmp])
    assert [s["n"] for s in sets] == [22, 4] and len(lib) == 26 and lib[-1]["id"].startswith(os.path.basename(tmp))
    assert abs(lib[-1]["dt"] - 0.01) < 1e-6 and lib[-1]["npts"] > 1000


def test_selection_against_a_conditional_spectrum_with_consistency_and_pulse():
    tmp = tempfile.mkdtemp(); _user_library(tmp)
    hz = SH.build_site_hazard(34.05, -118.25, 0.97, 1.12, design=DESIGN, deaggs=DEAGG, fetch=False, conditioning_periods=[1.0])
    sets, lib = SH.library_from([os.path.join(ROOT, "records", "p695_farfield"), tmp])
    lib = lib[:10] + lib[22:]                                              # 14 pairs keep the spectra cheap
    tgt = SH.target_from_hazard(hz, "cs")
    gm, chosen = GM.select_and_scale(lib, 1.0, 0.6, 8.0, 0.2, 2.2, n_select=11, target=tgt, deagg=hz["deagg"]["1.000"], pulse_fraction=0.1, sets=sets, verbose=False)
    assert gm["passes_90pct"] and gm["orientation_ok"] and len(gm["selected"]) == 11
    assert gm["target_label"].startswith("conditional spectrum") and gm["deagg"]["M"] == 7.31 and gm["n_pulse"] == 1
    assert any(r["pulse"] for r in gm["selected"]) and all(r["consistency_penalty"] >= 0 for r in gm["selected"])
    # the target array is the CS interpolated on the grid: equal to the MCE_R at T* within tolerance
    T = np.array(gm["periods"]); i = int(np.argmin(np.abs(T - 1.0)))
    mcer = SH.interp_spectrum(DESIGN["multiPeriodMCErSpectrum"]["periods"], DESIGN["multiPeriodMCErSpectrum"]["ordinates"], T)
    assert abs(gm["target"][i] / mcer[i] - 1) < 0.05
    # M / R consistency moves the ranking: a far-off-M record is penalised
    far = dict(lib[0], M=5.0, r_rup_km=150.0)
    assert SH.consistency_penalty(far, hz["deagg"]["1.000"]) > 1.0 and SH.consistency_penalty(dict(lib[0], M=None, r_rup_km=None), hz["deagg"]["1.000"]) == 0.0
    # sf bounds drop the records that need a factor outside the band
    gm2, _ = GM.select_and_scale(lib, 1.0, 0.6, 8.0, 0.2, 2.2, n_select=11, target=tgt, sf_bounds=(0.25, 4.0), verbose=False)
    assert gm2["n_pool"] <= len(lib) and gm2["sf_bounds"] == [0.25, 4.0]
    # the code path is unchanged
    gm3, _ = GM.select_and_scale(lib[:10], 1.0, 0.6, 8.0, 0.2, 2.2, n_select=6, verbose=False)
    assert gm3["target_label"].startswith("MCE_R = 1.5") and gm3["deagg"] is None
