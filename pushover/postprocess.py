"""postprocess.py -- turn a recorded pushover into ASCE 41 NSP quantities, FEMA P-695 factors and
component acceptance ratios. Pure numpy; no OpenSees here.

Clause numbers quoted are ASCE 41-17 (7.4.3.x, Eq. 7-27..7-32, Tables 7-4/7-5) -- the Grok Bot must
confirm the ASCE 41-23 numbering through Query file manager before they appear in a stamped report.
"""
from __future__ import annotations
import math
import numpy as np
_trap = getattr(np, 'trapezoid', None) or getattr(np, 'trapz')

G_IN = 386.4


# --------------------------------------------------------------------------- spectra
def spectrum_sa(T, SXS, SX1):
    """ASCE 7 / ASCE 41 general horizontal response spectrum (5% damping, TL ignored -> flagged)."""
    Ts = SX1 / SXS; T0 = 0.2 * Ts
    if T < T0:
        return SXS * (0.4 + 0.6 * T / T0)
    if T <= Ts:
        return SXS
    return SX1 / T


# --------------------------------------------------------------------------- idealisation
def idealize(u, V, u_target):
    """ASCE 41 7.4.3.2.4 bilinear idealisation of the capacity curve up to u_target:
    first segment through the point at 0.6*Vy, second segment through (u_target, V(u_target)),
    Vy iterated so the areas under the actual and idealised curves balance."""
    u = np.asarray(u); V = np.asarray(V)
    ud = min(u_target, u[-1])
    Vd = float(np.interp(ud, u, V))
    mask = u <= ud
    uu, VV = u[mask], V[mask]
    if uu[-1] < ud:
        uu = np.append(uu, ud); VV = np.append(VV, Vd)
    A_actual = _trap(VV, uu)
    Vy = Vd
    for _ in range(60):
        u06 = float(np.interp(0.6 * Vy, VV, uu)) if 0.6 * Vy <= VV.max() else uu[-1]
        Ke = 0.6 * Vy / max(u06, 1e-9)
        uy = Vy / Ke
        if uy >= ud:                                   # curve still elastic at ud
            uy = ud; Vy = Vd; A_ideal = 0.5 * ud * Vd; break
        A_ideal = 0.5 * uy * Vy + 0.5 * (Vy + Vd) * (ud - uy)
        err = (A_ideal - A_actual) / max(A_actual, 1e-9)
        if abs(err) < 1e-4:
            break
        Vy *= (1 - 0.5 * err)
    u06 = float(np.interp(0.6 * Vy, VV, uu)) if 0.6 * Vy <= VV.max() else uu[-1]
    Ke = 0.6 * Vy / max(u06, 1e-9); uy = Vy / Ke
    alpha1 = ((Vd - Vy) / max(ud - uy, 1e-9)) / Ke if ud > uy else 0.0
    return dict(Vy=float(Vy), uy=float(uy), Ke=float(Ke), ud=float(ud), Vd=float(Vd), alpha1=float(alpha1))


def initial_stiffness(u, V, frac=0.3):
    u = np.asarray(u); V = np.asarray(V)
    Vm = V.max(); i = int(np.argmax(V >= frac * Vm))
    return float(V[i] / u[i]) if u[i] > 0 else float("nan")


# --------------------------------------------------------------------------- NSP target displacement
def nsp_target(run, basis, prm, hazard_factor, site_class="D"):
    """ASCE 41 Eq. 7-28 target displacement for one hazard level (hazard_factor 1.0 = BSE-1N, 1.5 = BSE-2N).
    Iterates because the idealisation depends on the target it produces."""
    u, V = run["rec"]["u"], run["rec"]["V"]
    W = basis.W_kip or sum(m * G_IN for m in run["pattern"]["masses"].values())
    SXS, SX1 = basis.SDS * hazard_factor, basis.SD1 * hazard_factor
    T1 = run["pattern"]["T1"]
    Ki = initial_stiffness(u, V)
    phi, mk = run["pattern"]["phi"], run["pattern"]["masses"]
    C0 = sum(mk[k] * phi[k] for k in phi) / sum(mk[k] * phi[k] ** 2 for k in phi) * 1.0     # Gamma1 * phi_roof (=1)
    nst = len(phi)
    Cm_tab = prm["nsp"]
    Cm = Cm_tab["Cm_1_2_stories"] if nst <= 2 else Cm_tab["Cm_steel_MF_3plus_stories"]
    a_site = Cm_tab["C1_site_factor_a"].get(site_class.upper(), 60)
    ud = max(u) * 0.5
    out = None
    for it in range(25):
        ide = idealize(u, V, ud)
        Te = T1 * math.sqrt(Ki / ide["Ke"]) if ide["Ke"] > 0 else T1          # Eq. 7-27
        Sa = spectrum_sa(Te, SXS, SX1)
        Cm_eff = 1.0 if Te > 1.0 else Cm
        mu_str = Sa * Cm_eff / (ide["Vy"] / W)                                  # Eq. 7-31
        Te_c = max(Te, 0.2)
        C1 = 1.0 if Te > 1.0 else 1.0 + (mu_str - 1.0) / (a_site * Te_c ** 2)   # Eq. 7-29
        C2 = 1.0 if Te > 0.7 else 1.0 + (1.0 / 800.0) * ((mu_str - 1.0) / Te_c) ** 2   # Eq. 7-30
        dt = C0 * C1 * C2 * Sa * (Te ** 2 / (4 * math.pi ** 2)) * G_IN         # Eq. 7-28 (in)
        new_ud = min(dt, u[-1])
        out = dict(hazard_factor=hazard_factor, SXS=SXS, SX1=SX1, Te=Te, Ki=Ki, Ke=ide["Ke"], Vy=ide["Vy"], uy=ide["uy"],
                   alpha1=ide["alpha1"], Sa=Sa, C0=C0, C1=C1, C2=C2, Cm=Cm_eff, mu_strength=mu_str,
                   target_disp_in=dt, target_over_H=dt / run["H"], reached_150pct=(u[-1] >= 1.5 * dt),
                   reached_target=(u[-1] >= dt), W_kip=W, iterations=it + 1)
        if abs(new_ud - ud) < 1e-3 * max(ud, 1.0):
            break
        ud = new_ud
    # Eq. 7-32 maximum strength ratio (NSP applicability). alpha2 from the post-peak slope if captured,
    # alpha_PDelta approximated by the first-story stability coefficient from the elastic range.
    Vmax_i = int(np.argmax(V))
    alpha2 = 0.0
    if Vmax_i < len(u) - 3:
        slope = (V[-1] - V[Vmax_i]) / max(u[-1] - u[Vmax_i], 1e-9)
        alpha2 = min(0.0, slope / out["Ke"])
    QG = sum(run["gravity_table_QG"]) if run.get("gravity_table_QG") else W
    i_el = max(1, int(np.argmax(np.asarray(V) >= 0.3 * max(V))))
    story1 = run["rec"]["story_u"][i_el][0]
    theta1 = QG * story1 / (V[i_el] * run["heights"][0]) if V[i_el] > 0 else 0.0
    alpha_pd = -theta1
    lam = 0.8 if SX1 >= 0.6 else 0.2
    alpha_e = alpha_pd + lam * (alpha2 - alpha_pd)
    h = 1.0 + 0.15 * math.log(max(out["Te"], 0.05))
    mu_max = (out["target_disp_in"] / max(out["uy"], 1e-9)) + (abs(alpha_e) ** (-h)) / 4.0 if alpha_e != 0 else float("inf")
    out.update(alpha2=alpha2, alpha_PDelta=alpha_pd, alpha_e=alpha_e, lambda_nf=lam, mu_max=mu_max,
               nsp_permitted=(out["mu_strength"] <= mu_max), theta_story1_elastic=theta1)
    return out


# --------------------------------------------------------------------------- FEMA P-695 factors
def p695_factors(run, basis, nsp_bse1):
    u = np.asarray(run["rec"]["u"]); V = np.asarray(run["rec"]["V"])
    Vmax = float(V.max()); i_max = int(np.argmax(V))
    Vdes = basis.V_design_kip
    Omega = Vmax / Vdes if Vdes else None
    post = np.where((np.arange(len(V)) > i_max) & (V <= 0.8 * Vmax))[0]
    tail = run.get("tail", {})
    if tail.get("status") == "component_limit" and (not len(post) or tail["u_component_limit"] <= u[post[0]]):
        du = float(tail["u_component_limit"]); du_bound = "at component rotation limit b (non-simulated collapse, P-695 rule)"
    elif len(post):
        du = float(np.interp(0.8 * Vmax, V[post[0] - 1:post[0] + 1][::-1], u[post[0] - 1:post[0] + 1][::-1])); du_bound = "captured"
    else:
        du = float(u[-1]); du_bound = "LOWER BOUND (curve did not lose 20% of Vmax before the run stopped)"
    W = nsp_bse1["W_kip"]; T = max(basis.T_design_s or 0.0, run["pattern"]["T1"])
    dy_eff = nsp_bse1["C0"] * (Vmax / W) * (G_IN / (4 * math.pi ** 2)) * T ** 2
    return dict(Vmax_kip=Vmax, u_at_Vmax_in=float(u[i_max]), V_design_kip=Vdes, Omega=Omega, Omega0_design=basis.Om0,
                delta_u_in=du, delta_u_basis=du_bound, delta_y_eff_in=dy_eff, mu_T=du / dy_eff, T_used_s=T,
                Vmax_over_W=Vmax / W)


# --------------------------------------------------------------------------- component acceptance
def step_at(run, disp):
    u = np.asarray(run["rec"]["u"])
    return int(min(np.searchsorted(u, disp), len(u) - 1))


def acceptance(run, hinges, disp, level_name):
    """Per-hinge plastic rotation at roof displacement `disp`, D/C against IO/LS/CP, grouped by
    (kind, section, level z). Also the yielded-hinge census that shows the mechanism."""
    i = step_at(run, disp)
    pl = run["rec"]["hinge_pl"][i]
    rows, groups = [], {}
    census = {}
    for j, t in enumerate(run["hinge_tags"]):
        h = hinges[t]; s = h["spec"]
        th = abs(pl[j])
        if h["kind"] == "brace" and pl[j] > 0:                       # elongating brace: tension limits
            lim = dict(IO=s.IO_t, LS=s.LS_t, CP=s.CP_t)
        else:
            lim = dict(IO=s.IO, LS=s.LS, CP=s.CP)
        dc = {k: (th / v if v > 0 else float("nan")) for k, v in lim.items()}
        yielded = (th > 0.5 * s.theta_y) if h["kind"] != "brace" else (th > (s.dc if pl[j] < 0 else s.dT))
        key = (h["kind"], h["section"], round(h["z"]))
        g = groups.setdefault(key, dict(kind=h["kind"], section=h["section"], z_in=round(h["z"]), n=0, n_yielded=0,
                                          theta_pl_max=0.0, IO=s.IO, LS=s.LS, CP=s.CP, DC_IO=0.0, DC_LS=0.0, DC_CP=0.0))
        g["n"] += 1; g["n_yielded"] += int(yielded)
        if th > g["theta_pl_max"]:
            g.update(theta_pl_max=th, DC_IO=dc["IO"], DC_LS=dc["LS"], DC_CP=dc["CP"])
        c = census.setdefault(round(h["z"]), dict(z_in=round(h["z"]), beam_hinges=0, beam_yielded=0, col_hinges=0, col_yielded=0,
                                                 brace_elements=0, brace_buckled=0, brace_yielded_T=0))
        if h["kind"] == "beam":
            c["beam_hinges"] += 1; c["beam_yielded"] += int(yielded)
        elif h["kind"] == "brace":
            c["brace_elements"] += 1
            if pl[j] < 0 and yielded: c["brace_buckled"] += 1
            if pl[j] > 0 and yielded: c["brace_yielded_T"] += 1
        else:
            c["col_hinges"] += 1; c["col_yielded"] += int(yielded)
    table = sorted(groups.values(), key=lambda g: (g["kind"], g["z_in"]))
    worst = {k: max((g["DC_" + k] for g in table), default=0.0) for k in ("IO", "LS", "CP")}
    story_u = run["rec"]["story_u"][i]
    drifts = []
    prev = 0.0
    for k, (uk, hk) in enumerate(zip(story_u, run["heights"])):
        drifts.append(dict(story=k + 1, drift_ratio=(uk - prev) / hk)); prev = uk
    colN = run["rec"]["col_N"][i] if run["rec"].get("col_N") else []
    return dict(level=level_name, roof_disp_in=float(run["rec"]["u"][i]), step=i, groups=table, worst_DC=worst,
                census=sorted(census.values(), key=lambda c: c["z_in"]), story_drifts=drifts,
                max_story_drift=max(d["drift_ratio"] for d in drifts), col_N_max_kip=float(max(colN)) if colN else None)
