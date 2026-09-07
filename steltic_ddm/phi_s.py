"""
phi_s.py -- system resistance factor policy for the Direct Design Method check  phi_s * lambda_u >= 1.

STATUS (2026-09-07, FINAL for hot-rolled buildings) -- see docs/PHI_S_SOURCES.md for the source hunt.
  * Gravity + wind, hot-rolled (HR-W): full beta curve VERIFIED from Wan W. (2024), PhD thesis, University of
    Sydney (open access, hdl 2123/32474), Tables 6.10-6.14 RIGID-connection rows for five planar hot-rolled frames
    under 1.2D + 0.5L + 1.0W (US framework, W_T/G_T = 0.10 and 0.15, FORM checked against 250,000-sample
    Monte Carlo within 2 %): mean beta = 3.02 at phi_s = 0.80 and 2.80 at phi_s = 0.85 -> beta = -4.42 phi_s + 6.56
    -> phi_s = 0.92 / 0.80 / 0.69 at beta_T = 2.5 / 3.0 / 3.5. At beta_T = 2.5 the more conservative 0.85 quoted for
    Liu 2019 / Zhang 2016 II is kept. Semi-rigid frames (all three connection types): beta = -4.41 phi_s + 6.65 (Eq. 6.9).
  * Gravity, hot-rolled (HR-G): 0.80 at beta_T = 3.0  -- VERIFIED from an open document (Zhang, Rasmussen,
    Shayan & Ellingwood, SSRC Annual Stability Conf. 2013, Table 10: 0.90 / 0.85 / 0.80 / 0.70 at
    beta_T = 2.5 / 2.75 / 3.0 / 3.5; 1.2G+1.5Q; nine planar moment frames; all failure modes).
  * Gravity + wind (…-W): values as CITED by Rasmussen & Zhang themselves in an open CC-BY paper
    (Arrayago, Rasmussen & Zhang, Structural Safety 97 (2022) 102211, section 5.3): "phi_s = 0.80-0.85 for
    hot-rolled planar frames [Zhang 2016 II], 0.85 for hot-rolled spatial frames [Liu 2019], 0.85-0.90 for
    cold-formed spatial frames [Liu 2018] and 0.85 for cold-formed portal frames [Sena Cardoso 2019]",
    all for combined gravity and wind at beta_0 ~ 2.50 (the ASCE 7 / AS 5104 target for wind-governed,
    non-sudden failure modes). Lower bounds of the quoted ranges are adopted -> VERIFIED-CITED.
  * Gravity, cold-formed HSS (HSS-G) and CFS portal (CFS-P-G): still PROVISIONAL (abstract level).
  * The ductile / non-ductile split of Zhang 2016 II is NOT applied until that table is transcribed;
    the mechanism class is reported but does not change phi_s.
The bot must NEVER cite a specification clause for phi_s -- it is a literature value.
"""

PROVISIONAL = True      # global switch: some classes are still abstract-level

SOURCES = {
    "SSRC2013": "Zhang H., Rasmussen K.J.R., Shayan S., Ellingwood B.R. -- System reliability of steel frames designed by "
                "inelastic analysis, SSRC Annual Stability Conference 2013 (open PDF, aisc.org). Table 10: phi_s = 0.90/0.85/0.80/0.70 "
                "at beta_T = 2.5/2.75/3.0/3.5, 1.2G+1.5Q, nine planar moment frames",
    "ARZ2022": "Arrayago I., Rasmussen K.J.R., Zhang H. -- System-based reliability analysis of stainless steel frames subjected to "
               "gravity and wind loads, Structural Safety 97 (2022) 102211 (CC-BY, UPCommons), sec. 5.3, quoting the carbon-steel "
               "calibrations for gravity + wind at beta_0 ~ 2.50",
    "HR": "Zhang H., Shayan S., Rasmussen K.J.R., Ellingwood B.R. -- System-based design of planar steel frames, II: Reliability "
          "results and design recommendations, J. Constr. Steel Res. 123 (2016) 154-161",
    "HR-AISC-APP1": "Zhang H., Liu H., Ellingwood B.R., Rasmussen K.J.R. -- System reliabilities of planar gravity steel frames designed "
                    "by the inelastic method in AISC 360-10, ASCE J. Struct. Eng. 144(3) (2018) 04018011",
    "HSS": "Liu W., Zhang H., Rasmussen K.J.R. -- System reliability-based Direct Design Method for space frames with cold-formed "
           "steel hollow sections, Eng. Struct. 166 (2018) 79-92",
    "WIND": "Liu W., Zhang H., Rasmussen K.J.R., Yan S. -- System-based limit state design criterion for 3D steel frames under wind "
            "loads, J. Constr. Steel Res. 157 (2019) 440-449",
    "CFS-P": "Sena Cardoso F., Zhang H., Rasmussen K.J.R., Yan S. -- Reliability calibrations for the design of cold-formed steel "
             "portal frames by advanced analysis, Eng. Struct. 182 (2019) 164-171",
    "WAN2024": "Wan W. -- System reliability of semi-rigid steel frames designed with Direct Design Method, PhD thesis, "
               "University of Sydney 2024 (open access, hdl 2123/32474): Tables 6.10-6.14 (rigid rows: mean beta 3.02 at phi_s 0.80, "
               "2.80 at 0.85; five planar hot-rolled frames, 1.2D+0.5L+W), Table 6.15 and Eq. 6.9 beta = -4.41 phi_s + 6.65 (semi-rigid)",
    "SHAYAN2013": "Shayan S. -- System reliability-based design of 2D steel frames by advanced analysis, PhD thesis, University of "
                  "Sydney 2013 (open access, hdl 2123/10196) -- derivation behind Zhang 2016 I/II (not yet transcribed)",
}

# key -> dict(phi, beta, status, desc, sources)
#   status: "verified" (read from an open document), "verified-cited" (numeric value quoted by the
#   calibration's own authors in an open document), "provisional" (abstract-level)
TABLE = {
    "HR-G":   dict(phi=0.80, beta=3.0, status="verified",
                   desc="hot-rolled frame, gravity combination (any failure mode) -- SSRC 2013 Table 10 at beta_T = 3.0",
                   sources=["SSRC2013", "HR"]),
    "HR-W":   dict(phi=0.85, beta=2.5, status="verified",
                   desc="hot-rolled frame, gravity + wind -- Wan 2024 rigid-frame curve (0.92/0.80/0.69 at beta 2.5/3.0/3.5), capped at "
                        "beta 2.5 by the 0.85 quoted for Liu 2019 / Zhang 2016 II",
                   sources=["WAN2024", "ARZ2022", "WIND", "HR"]),
    "HR-SR-W": dict(phi=0.86, beta=2.5, status="verified",
                    desc="hot-rolled frame with SEMI-RIGID (EEPC / TSWAC / TSAC) connections, gravity + wind -- Wan 2024 Eq. 6.9 "
                         "beta = -4.41 phi_s + 6.65 (0.94/0.83/0.71 at beta 2.5/3.0/3.5), capped at beta 2.5 by 0.86",
                    sources=["WAN2024"]),
    "HSS-G":  dict(phi=0.80, beta=3.0, status="provisional",
                   desc="cold-formed HSS members govern, gravity combination -- lower end of the Liu 2018 abstract range",
                   sources=["HSS"]),
    "HSS-W":  dict(phi=0.85, beta=2.5, status="verified-cited",
                   desc="cold-formed HSS space frame, gravity + wind -- Liu 2018 as quoted (0.85-0.90), lower bound",
                   sources=["ARZ2022", "HSS"]),
    "CFS-P-G": dict(phi=0.85, beta=3.0, status="provisional",
                    desc="cold-formed steel portal frame, gravity -- Sena Cardoso 2019 abstract",
                    sources=["CFS-P"]),
    "CFS-P-W": dict(phi=0.85, beta=2.5, status="verified-cited",
                    desc="cold-formed steel portal frame with locally unstable members, gravity + wind -- Sena Cardoso 2019 as quoted",
                    sources=["ARZ2022", "CFS-P"]),
    "CFS-W":  dict(phi=None, beta=None, status="uncalibrated",
                   desc="cold-formed stud-wall building -- no calibration exists; report lambda_u only", sources=[]),
    "SEIS":   dict(phi=None, beta=None, status="n/a",
                   desc="seismic-pattern combination on an R > 3 system -- no phi_s pass/fail (outside the calibrations)", sources=[]),
}


# SSRC 2013 Table 10 (verified): phi_s vs beta_T for hot-rolled frames under gravity -- used to move
# between target reliability rows when the Risk Category is not II.
HR_G_BY_BETA = {2.5: 0.90, 2.75: 0.85, 3.0: 0.80, 3.5: 0.70}
# Wan 2024 Tables 6.10-6.14, rigid rows (verified): beta = -4.42 phi_s + 6.56 -> phi_s = (6.56 - beta)/4.42,
# capped at beta 2.5 by the more conservative cited value 0.85.
HR_W_BY_BETA = {2.5: 0.85, 3.0: 0.80, 3.5: 0.69}
# Wan 2024 Eq. 6.9, all three semi-rigid connection types: beta = -4.41 phi_s + 6.65
HR_SR_W_BY_BETA = {2.5: 0.86, 3.0: 0.83, 3.5: 0.71}
CURVES = {"HR-G": HR_G_BY_BETA, "HR-W": HR_W_BY_BETA, "HR-SR-W": HR_SR_W_BY_BETA}

# ASCE 7-22 Table 1.3-1 targets (failure not sudden, no widespread progression), 50-year reference:
# gravity rows; the wind calibrations of the Sydney group were reported at beta_0 ~ 2.5 (RC II wind
# practice), so wind rows for RC III/IV are flagged as extrapolated.
BETA_TARGET = {"gravity": {"I": 2.5, "II": 3.0, "III": 3.25, "IV": 3.5},
               "lateral": {"I": 2.5, "II": 2.5, "III": 3.0, "IV": 3.0}}


def _interp(table, b):
    ks = sorted(table)
    if b <= ks[0]:
        return table[ks[0]]
    if b >= ks[-1]:
        return table[ks[-1]]
    for a, c in zip(ks, ks[1:]):
        if a <= b <= c:
            return table[a] + (table[c] - table[a]) * (b - a) / (c - a)


def choose(combo_kind, system_R, cls, governed_by_braces=False, hss_braces=False, material="HR", risk_category="II"):
    """Pick the phi_s class for one combination.
    combo_kind: 'gravity' | 'wind' | 'seismic'; cls: mechanism class (reported, does not change phi_s yet);
    material: 'HR' (hot-rolled, rigid/pinned joints) | 'HR-SR' (hot-rolled with semi-rigid joints) | 'CFS-P' (cold-formed portal) | 'CFS-W' (stud-wall)."""
    if material == "CFS-W":
        key = "CFS-W"
    elif combo_kind == "seismic" and system_R is not None and system_R > 3.0:
        key = "SEIS"
    else:
        lateral = combo_kind in ("wind", "seismic")      # seismic with R <= 3 = elastic-force lateral strength case
        if material == "CFS-P":
            key = "CFS-P-W" if lateral else "CFS-P-G"
        elif material == "HR-SR" and lateral:
            key = "HR-SR-W"
        elif governed_by_braces and hss_braces:
            key = "HSS-W" if lateral else "HSS-G"
        else:
            key = "HR-W" if lateral else "HR-G"
    t = dict(TABLE[key])
    prov = t["status"] == "provisional"
    note = None
    # Risk Category other than II: move to the target-reliability row for that category
    rc = str(risk_category or "II").upper()
    lateral_kind = combo_kind in ("wind", "seismic")
    bT = BETA_TARGET["lateral" if lateral_kind else "gravity"].get(rc, t["beta"])
    if t["phi"] is not None and bT is not None and abs(bT - (t["beta"] or bT)) > 1e-6:
        if key in CURVES:
            t["phi"] = round(_interp(CURVES[key], bT), 3); t["beta"] = bT
            t["status"] = "verified" if bT in CURVES[key] else "verified-interpolated"
            t["desc"] += " -- Risk Category %s row (beta_T = %.2f)" % (rc, bT)
        else:
            # HSS / CFS classes are known at one beta only: scale with the matching verified hot-rolled curve and flag it
            ref = HR_W_BY_BETA if lateral_kind else HR_G_BY_BETA
            ratio = _interp(ref, bT) / _interp(ref, t["beta"])
            t["phi"] = round(t["phi"] * ratio, 3); t["beta"] = bT
            t["status"] = "provisional-extrapolated"; prov = True
            t["desc"] += " -- Risk Category %s: scaled to beta_T = %.2f with the hot-rolled curve (NOT a calibrated value)" % (rc, bT)
    return dict(cls=key, phi_s=t["phi"], beta_T=t["beta"], status=t["status"], description=t["desc"],
                source="; ".join(SOURCES[s] for s in t["sources"]) or None, mechanism_class=cls,
                provisional=prov, note=note,
                flag=("PROVISIONAL phi_s -- abstract-level value; transcribe the calibrated table from the full paper"
                      if prov and t["phi"] is not None else None))


def check(phi, lam_u):
    if phi is None or lam_u is None:
        return None, "n/a"
    v = phi * lam_u
    return round(v, 3), ("PASS" if v >= 1.0 else "FAIL")
