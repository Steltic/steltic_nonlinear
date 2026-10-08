"""postprocess.py -- turn a recorded pushover into ASCE 41 NSP quantities, FEMA P-695 factors and
component acceptance ratios. Pure numpy; no OpenSees here.

Clause and equation numbers are ASCE 41-23:
  7.4.3.2.5 idealised force-displacement curve (Figure 7-3)       7.4.3.2.6 Te, Eq. (7-28)
  7.4.3.3.1 acceptance at the control-node displacement >= delta_t 7.4.3.3.2 delta_t, Eq. (7-29)
  C1 Eq. (7-30), C2 Eq. (7-31), mu_strength Eq. (7-32), mu_max Eq. (7-33), alpha_e Eq. (7-34)
  Table 7-4 Cm, Table 7-5 C0 (here C0 = Gamma_1 * phi_roof, the first option of 7.4.3.3.2).

What is evaluated where (NL-09):
  * delta_t is iterated with the idealisation it depends on; the idealisation runs to
    Delta_d = min(delta_t, displacement at V_max) -- always a point ON the recorded curve.
  * component acceptance, storey drifts and the mechanism census are read at the first recorded step whose
    roof displacement equals or exceeds delta_t (7.4.3.3.1). When the push never got there, no D/C and no
    drift are reported -- never the last converged point presented as if it were delta_t -- and the reason
    decides the verdict (NL-R2-24, `target_shortfall`): a genuine strength loss / mechanism before delta_t
    (V fell to 0.8 Vmax, the P-695 tail criterion, or gravity hinges reached rotation b) is TARGET NOT
    REACHED, `acceptable = False`; a numerical stop (solver non-convergence or the drift cap) with V still
    above 0.8 Vmax is NOT EVALUATED, `acceptable = None` -- not evidence of collapse.
  * a level with no monitored component (or no monitored beam/column in a frame that has moment-frame
    members) is NOT EVALUATED: worst D/C None, never 0.00 (NL-01).
"""
from __future__ import annotations
import math
import numpy as np
_trap = getattr(np, 'trapezoid', None) or getattr(np, 'trapz')

G_IN = 386.4

# acceptance status strings (pushover_package.json acceptance[level]["status"])
EVALUATED = "evaluated"
NOT_EVALUATED = "not_evaluated"
TARGET_NOT_REACHED = "target_not_reached"


# NL-R2-24: why a push ended short of delta_t (acceptance[level]["shortfall"]["kind"])
SHORT_STRENGTH = "strength_loss"            # V fell to 0.8 Vmax before delta_t -> TARGET NOT REACHED, NOT ACCEPTABLE
SHORT_COMPONENT = "component_limit"         # gravity hinges at rotation b before delta_t -> TARGET NOT REACHED, NOT ACCEPTABLE
SHORT_NUMERICAL = "numerical"               # solver stopped with V > 0.8 Vmax -> NOT EVALUATED
SHORT_DRIFT_CAP = "drift_cap"               # --max-drift reached first -> NOT EVALUATED


def target_shortfall(run, disp):
    """None when the recorded push reached `disp`; otherwise why it did not (NL-R2-24). A stop before delta_t is a
    genuine strength loss / mechanism only when the curve shows it: V at or below 0.8 Vmax after the peak (the
    FEMA P-695 / tail criterion used for delta_u) or gravity hinges at rotation b (tail status component_limit) at a
    roof displacement short of delta_t. A solver stop while V is still above 0.8 Vmax (Ex8: V/Vmax = 1.000) is a
    NUMERICAL stop and says nothing about collapse."""
    u = np.asarray(run["rec"]["u"], dtype=float); V = np.asarray(run["rec"]["V"], dtype=float)
    if len(u) and u.max() >= disp - 1e-9:
        return None
    Vmax = float(V.max()) if len(V) else 0.0
    i_max = int(np.argmax(V)) if len(V) else 0
    r_end = float(V[-1] / Vmax) if Vmax > 0 else float("nan")
    base = dict(u_end_in=float(u[-1]) if len(u) else 0.0, target_disp_in=float(disp), V_end_over_Vmax=r_end, Vmax_kip=Vmax)
    post = np.where((np.arange(len(V)) > i_max) & (V <= 0.8 * Vmax))[0]
    if len(post):
        return dict(base, kind=SHORT_STRENGTH, u_event_in=float(u[post[0]]),
                    text="strength loss before the target: V fell to 0.8 Vmax at roof u = %.2f in < delta_t %.2f in"
                         % (float(u[post[0]]), disp))
    tail = run.get("tail") or {}
    if tail.get("status") == "component_limit" and float(tail.get("u_component_limit", float("inf"))) < disp:
        return dict(base, kind=SHORT_COMPONENT, u_event_in=float(tail["u_component_limit"]),
                    text="gravity-carrying hinges reached rotation b (loss of gravity capacity) at roof u = %.2f in < delta_t %.2f in"
                         % (float(tail["u_component_limit"]), disp))
    if str(run.get("stop_reason", "")).startswith("reached max"):
        return dict(base, kind=SHORT_DRIFT_CAP,
                    text="analysis stopped at the drift cap (--max-drift) at V/Vmax = %.3f before the target displacement "
                         "(roof u = %.2f in < delta_t %.2f in)" % (r_end, base["u_end_in"], disp))
    return dict(base, kind=SHORT_NUMERICAL,
                text="analysis stopped numerically at V/Vmax = %.3f before the target displacement (roof u = %.2f in < "
                     "delta_t %.2f in); a numerical stop above 0.8 Vmax is not evidence of collapse" % (r_end, base["u_end_in"], disp))


def shortfall_is_failure(sf) -> bool:
    return bool(sf) and sf.get("kind") in (SHORT_STRENGTH, SHORT_COMPONENT)


# --------------------------------------------------------------------------- spectra
def spectrum_sa(T, SXS, SX1):
    """ASCE 7 / ASCE 41 general horizontal response spectrum (5% damping, TL ignored -> flagged)."""
    Ts = SX1 / SXS; T0 = 0.2 * Ts
    if T < T0:
        return SXS * (0.4 + 0.6 * T / T0)
    if T <= Ts:
        return SXS
    return SX1 / T


# --------------------------------------------------------------------------- NSP applicability, higher modes
HM_RATIO_LIMIT = 1.30            # ASCE 41-23 7.3.2.1 item 2: "exceeds 130% of the corresponding story shear"
HM_MASS_TARGET = 0.90            # "using sufficient modes to produce 90% mass participation"
HM_DAMPING = 0.05                # CQC cross-modal coefficients at the 5 % damping of the spectrum


def _cqc_rho(Ti, Tj, xi=HM_DAMPING):
    """CQC cross-modal coefficient (Der Kiureghian 1981, equal damping), r = w_j / w_i = T_i / T_j."""
    r = Ti / Tj if Tj > 0 else 0.0
    den = (1 - r * r) ** 2 + 4 * xi * xi * r * (1 + r) ** 2
    return 8 * xi * xi * (1 + r) * r ** 1.5 / den if den > 0 else 1.0


def higher_mode_check(pattern, SXS, SX1, limit=HM_RATIO_LIMIT, mass_target=HM_MASS_TARGET):
    """ASCE 41-23 7.3.2.1 item 2 (NL-R2-12): "a modal response spectrum analysis shall be performed for the structure
    using sufficient modes to produce 90% mass participation. A second response spectrum analysis shall also be
    performed, considering only the first mode participation. Higher mode effects shall be considered significant if
    the shear in any story resulting from the modal analysis considering modes required to obtain 90% mass
    participation exceeds 130% of the corresponding story shear considering only the first mode response."

    Implemented from the eigen solve of the pushover model (initial stiffness, after gravity) in `pattern`
    (nonlinear_model.modal_pattern): modes in eigen order up to and including the one where the cumulative effective
    mass in the push direction first reaches 90 %; modal level forces F_kn = Gamma_n m_k phi_kn Sa(T_n) g on the
    diaphragm levels; story shear V_i,n = sum over levels k >= i; modes combined by CQC (5 % damping); "first mode" =
    the push-direction mode the load pattern uses. Spectrum: ASCE 7 / 41 general shape with S_XS, S_X1 (T_L ignored);
    the ratio does not depend on the hazard level because BSE-1N and BSE-2N have the same shape.
    -> dict(status "not_significant" | "significant" | "not_evaluated", ratios per story, max_ratio, ...)."""
    out = dict(clause="ASCE 41-23 7.3.2.1 item 2", limit=limit, mass_target=mass_target,
               combination="CQC (5% damping)", spectrum="S_XS %.3f g, S_X1 %.3f g (T_L ignored)" % (SXS, SX1),
               ratios=[], max_ratio=None, story_max=None, n_modes_used=0, cum_mass_frac=None)
    modes = (pattern or {}).get("modes") or []
    first = (pattern or {}).get("mode")
    mk = (pattern or {}).get("masses") or {}
    if not modes or first is None or not mk:
        return dict(out, status="not_evaluated", reason="no modal data in the run (pushover written before NL-R2-12)")
    used, cum = [], 0.0
    for m in modes:
        used.append(m); cum += m.get("meff_frac") or 0.0
        if cum >= mass_target:
            break
    out.update(n_modes_used=len(used), cum_mass_frac=cum, n_modes_computed=len(modes))
    m1 = next((m for m in modes if m["mode"] == first), None)
    if m1 is None:
        return dict(out, status="not_evaluated", reason="first mode %s not among the computed modes" % first)
    if m1 not in used:
        used.append(m1)
    ks = sorted(mk)

    def shears(m):
        sa = spectrum_sa(m["T"], SXS, SX1) * G_IN
        F = {k: m["gamma"] * mk[k] * float(m["phi"].get(k, m["phi"].get(str(k), 0.0))) * sa for k in ks}
        return [sum(F[k] for k in ks[i:]) for i in range(len(ks))]

    Vn = [shears(m) for m in used]
    V1 = shears(m1)
    rho = [[_cqc_rho(a["T"], b["T"]) for b in used] for a in used]
    for i in range(len(ks)):
        v2 = sum(rho[a][b] * Vn[a][i] * Vn[b][i] for a in range(len(used)) for b in range(len(used)))
        Vm = math.sqrt(max(v2, 0.0)); v1 = abs(V1[i])
        out["ratios"].append(dict(story=i + 1, V_modal_kip=Vm, V_mode1_kip=v1, ratio=(Vm / v1) if v1 > 0 else float("inf")))
    worst = max(out["ratios"], key=lambda r: r["ratio"])
    out.update(max_ratio=worst["ratio"], story_max=worst["story"])
    if cum < mass_target - 1e-9:
        return dict(out, status="not_evaluated",
                    reason="the %d computed modes reach only %.1f%% mass participation (< 90%%)" % (len(modes), 100 * cum))
    sig = worst["ratio"] > limit
    return dict(out, status="significant" if sig else "not_significant",
                reason="story %d: modal (%d modes, %.1f%% mass) / first-mode story shear = %.3f %s 1.30"
                       % (worst["story"], len(used), 100 * cum, worst["ratio"], ">" if sig else "<="))


NSP_STATUS_TEXT = {
    "permitted": "NSP permitted (ASCE 41-23 7.3.2.1: mu_strength < mu_max and higher-mode effects not significant)",
    "permitted_with_LDP": ("NSP NOT permitted alone -- higher-mode effects significant (ASCE 41-23 7.3.2.1 item 2): the NSP "
                           "is permitted only with a supplementary LDP, both meeting their acceptance criteria"),
    "not_permitted": "NSP NOT permitted -- mu_strength >= mu_max (ASCE 41-23 7.3.2.1 item 1): an NDP is required",
    "not_evaluated": "NSP applicability NOT EVALUATED -- the higher-mode test (ASCE 41-23 7.3.2.1 item 2) was not completed",
}


def nsp_status(strength_ok, hm):
    """Both tests of ASCE 41-23 7.3.2.1. Never "permitted" unless the higher-mode test ran and passed."""
    if not strength_ok:
        return "not_permitted"
    st = (hm or {}).get("status")
    if st == "significant":
        return "permitted_with_LDP"
    if st == "not_significant":
        return "permitted"
    return "not_evaluated"


# --------------------------------------------------------------------------- idealisation
def idealize(u, V, u_target):
    """ASCE 41-23 7.4.3.2.5 / Figure 7-3 bilinear idealisation.

    (Vd, Delta_d) is the point ON the curve at the target displacement or at the displacement of the maximum
    base shear, whichever is least. The first segment is the secant through the curve at 0.6 Vy (Ke); the
    second runs from (Delta_y, Vy) to (Vd, Delta_d); Vy is iterated until the areas under the actual and the
    idealised curves up to Delta_d balance, and is not taken greater than the maximum base shear."""
    u = np.asarray(u, dtype=float); V = np.asarray(V, dtype=float)
    i_max = int(np.argmax(V)); Vmax = float(V[i_max]); u_Vmax = float(u[i_max])
    ud = float(min(u_target, u_Vmax))
    ud = max(ud, float(u[1]) if len(u) > 1 else ud)
    Vd = float(np.interp(ud, u[:i_max + 1], V[:i_max + 1])) if i_max > 0 else float(V[0])
    mask = u <= ud
    uu, VV = u[mask], V[mask]
    if uu[-1] < ud:
        uu = np.append(uu, ud); VV = np.append(VV, Vd)
    A_actual = _trap(VV, uu)

    def u_at(Vt):                                   # displacement where the rising curve first reaches Vt
        j = int(np.argmax(VV >= Vt)) if (VV >= Vt).any() else len(VV) - 1
        if j == 0:
            return float(uu[0])
        return float(np.interp(Vt, [VV[j - 1], VV[j]], [uu[j - 1], uu[j]]))

    Vy = min(Vd, Vmax)
    elastic = False
    for _ in range(80):
        Ke = 0.6 * Vy / max(u_at(0.6 * Vy), 1e-9)
        uy = Vy / Ke
        if uy >= ud:                                   # curve still elastic at Delta_d
            uy, Vy, elastic = ud, Vd, True
            break
        A_ideal = 0.5 * uy * Vy + 0.5 * (Vy + Vd) * (ud - uy)
        err = (A_ideal - A_actual) / max(A_actual, 1e-9)
        if abs(err) < 1e-5:
            break
        Vy_new = min(Vy * (1 - 0.5 * err), Vmax)
        if abs(Vy_new - Vy) < 1e-9 * max(Vy, 1.0):
            break
        Vy = Vy_new
    Vy = min(Vy, Vmax)
    Ke = 0.6 * Vy / max(u_at(0.6 * Vy), 1e-9) if not elastic else Vd / max(ud, 1e-9)
    uy = min(Vy / Ke, ud)
    alpha1 = ((Vd - Vy) / max(ud - uy, 1e-9)) / Ke if ud > uy else 0.0
    return dict(Vy=float(Vy), uy=float(uy), Ke=float(Ke), ud=float(ud), Vd=float(Vd), alpha1=float(alpha1),
                Vmax=Vmax, u_at_Vmax=u_Vmax, Vy_capped_at_Vmax=bool(Vy >= Vmax - 1e-9))


def initial_stiffness(u, V, frac=0.3):
    u = np.asarray(u); V = np.asarray(V)
    Vm = V.max(); i = int(np.argmax(V >= frac * Vm))
    return float(V[i] / u[i]) if u[i] > 0 else float("nan")


def _alpha2(u, V, ide):
    """Figure 7-3 third segment: from (Vd, Delta_d) to the point where the base shear degrades to 0.6 Vy.
    Returns (alpha2, basis). If the curve never degrades to 0.6 Vy, the last recorded point is used (the
    slope magnitude is then a lower bound); with no descending branch alpha2 = 0."""
    u = np.asarray(u, dtype=float); V = np.asarray(V, dtype=float)
    i_max = int(np.argmax(V))
    Vd, ud, Ke, Vy = ide["Vd"], ide["ud"], ide["Ke"], ide["Vy"]
    post = np.where((np.arange(len(V)) > i_max) & (V <= 0.6 * Vy))[0]
    if len(post):
        j = int(post[0])
        u6 = float(np.interp(0.6 * Vy, [V[j], V[j - 1]], [u[j], u[j - 1]]))
        return min(0.0, ((0.6 * Vy - Vd) / max(u6 - ud, 1e-9)) / Ke), "to 0.6 Vy on the descending branch"
    if i_max < len(u) - 3 and u[-1] > ud:
        return min(0.0, ((V[-1] - Vd) / max(u[-1] - ud, 1e-9)) / Ke), "to the last recorded point (curve did not degrade to 0.6 Vy)"
    return 0.0, "no descending branch recorded"


def _cm(basis, prm, n_stories, T1):
    """ASCE 41-23 Table 7-4 effective mass factor: 1.0 for 1-2 storeys; 0.9 for steel moment, concentrically
    or eccentrically braced frames of 3+ storeys; 1.0 ('Other', e.g. BRBF) otherwise; 1.0 if T > 1.0 s
    (T = the fundamental period of the model, not Te)."""
    tab = prm.get("nsp") or {}
    if n_stories <= 2:
        return float(tab.get("Cm_1_2_stories", 1.0)), "Table 7-4, 1-2 stories"
    if T1 > 1.0:
        return 1.0, "Table 7-4 note: T = %.2f s > 1.0 s" % T1
    sysname = str(getattr(basis, "system", "") or "").upper()
    import re
    if re.search(r"BRB|BUCKLING[- ]RESTRAINED", sysname):
        return float(tab.get("Cm_other_3plus_stories", 1.0)), "Table 7-4 'Other' (BRBF)"
    if re.search(r"\b(SMF|IMF|OMF)\b|MOMENT", sysname):
        return float(tab.get("Cm_steel_MF_3plus_stories", 0.9)), "Table 7-4 steel moment frame"
    if re.search(r"\bEBF\b|ECCENTRIC", sysname):
        return float(tab.get("Cm_steel_EBF_3plus_stories", 0.9)), "Table 7-4 steel eccentrically braced frame"
    if re.search(r"\b(SCBF|OCBF|CBF)\b|CONCENTRIC", sysname):
        return float(tab.get("Cm_steel_CBF_3plus_stories", 0.9)), "Table 7-4 steel concentrically braced frame"
    return float(tab.get("Cm_other_3plus_stories", 1.0)), "Table 7-4 'Other' (system %r not recognised)" % sysname[:40]


# --------------------------------------------------------------------------- NSP target displacement
def nsp_target(run, basis, prm, hazard_factor, site_class="D"):
    """ASCE 41-23 Eq. (7-29) target displacement for one hazard level (hazard_factor 1.0 = BSE-1N, 1.5 = BSE-2N).
    Iterates because the idealisation (to Delta_d = min(delta_t, u at Vmax)) depends on the target it produces."""
    u, V = run["rec"]["u"], run["rec"]["V"]
    W = basis.W_kip or sum(m * G_IN for m in run["pattern"]["masses"].values())
    SXS, SX1 = basis.SDS * hazard_factor, basis.SD1 * hazard_factor
    T1 = run["pattern"]["T1"]
    Ki = initial_stiffness(u, V)
    phi, mk = run["pattern"]["phi"], run["pattern"]["masses"]
    C0 = sum(mk[k] * phi[k] for k in phi) / sum(mk[k] * phi[k] ** 2 for k in phi) * 1.0     # Gamma1 * phi_roof (=1)
    nst = len(phi)
    Cm, Cm_basis = _cm(basis, prm, nst, T1)
    a_site = prm["nsp"]["C1_site_factor_a"].get(site_class.upper(), 60)
    u_end = float(max(u))
    dt_guess = 0.5 * u_end
    out = None
    for it in range(40):
        ide = idealize(u, V, dt_guess)
        Te = T1 * math.sqrt(Ki / ide["Ke"]) if ide["Ke"] > 0 else T1          # Eq. (7-28)
        Sa = spectrum_sa(Te, SXS, SX1)
        mu_str = Sa * Cm / (ide["Vy"] / W)                                      # Eq. (7-32)
        C1 = 1.0 if Te > 1.0 else 1.0 + (mu_str - 1.0) / (a_site * max(Te, 0.2) ** 2)   # Eq. (7-30); T < 0.2 s -> value at 0.2 s
        C2 = 1.0 if Te > 0.7 else 1.0 + (1.0 / 800.0) * ((mu_str - 1.0) / Te) ** 2      # Eq. (7-31)
        dt = C0 * C1 * C2 * Sa * (Te ** 2 / (4 * math.pi ** 2)) * G_IN         # Eq. (7-29) (in)
        out = dict(hazard_factor=hazard_factor, SXS=SXS, SX1=SX1, Te=Te, Ki=Ki, Ke=ide["Ke"], Vy=ide["Vy"], uy=ide["uy"],
                   alpha1=ide["alpha1"], Delta_d=ide["ud"], V_d=ide["Vd"], Vy_capped_at_Vmax=ide["Vy_capped_at_Vmax"],
                   Sa=Sa, C0=C0, C1=C1, C2=C2, Cm=Cm, Cm_basis=Cm_basis, mu_strength=mu_str,
                   target_disp_in=dt, target_over_H=dt / run["H"], reached_150pct=(u_end >= 1.5 * dt),
                   reached_target=(u_end >= dt), u_end_in=u_end, W_kip=W, iterations=it + 1)
        if abs(dt - dt_guess) < 1e-4 * max(dt, 1.0):
            break
        dt_guess = dt
    ide = idealize(u, V, out["target_disp_in"])
    # Eq. (7-33) maximum strength ratio (NSP applicability, 7.3.2.1 item 1) with alpha_e from Eq. (7-34); the
    # higher-mode test (item 2) below -- `nsp_permitted` is True only when both pass (NL-R2-12).
    alpha2, a2_basis = _alpha2(u, V, ide)
    QG = sum(run["gravity_table_QG"]) if run.get("gravity_table_QG") else W
    Varr = np.asarray(V)
    i_el = max(1, int(np.argmax(Varr >= 0.3 * max(V))))
    story1 = run["rec"]["story_u"][i_el][0]
    theta1 = QG * story1 / (V[i_el] * run["heights"][0]) if V[i_el] > 0 else 0.0
    alpha_pd = -theta1                               # P-Delta slope ratio ~ -theta (first-storey stability coefficient)
    hl = (prm.get("nsp") or {}).get("hazard_levels") or {}
    SX1_bse2n = basis.SD1 * float(hl.get("BSE-2N", 1.5))
    lam = 0.8 if SX1_bse2n >= 0.6 else 0.2           # near-field factor on S_X1 for BSE-2N (Eq. 7-34)
    alpha_e = alpha_pd + lam * (alpha2 - alpha_pd)
    h = 1.0 + 0.15 * math.log(max(out["Te"], 0.05))
    mu_max = (ide["ud"] / max(ide["uy"], 1e-9)) + (abs(alpha_e) ** (-h)) / 4.0 if alpha_e != 0 else float("inf")
    strength_ok = out["mu_strength"] < mu_max                      # 7.3.2.1 item 1
    hm = higher_mode_check(run.get("pattern"), SXS, SX1)           # 7.3.2.1 item 2 (NL-R2-12)
    nst = nsp_status(strength_ok, hm)
    permitted = nst == "permitted"                                 # NSP alone permitted: BOTH tests passed
    sf = target_shortfall(run, out["target_disp_in"])              # NL-R2-24: failure vs numerical stop
    if sf is None:
        tstat = "reached"
    elif shortfall_is_failure(sf):
        tstat = "TARGET NOT REACHED: %s -- NOT ACCEPTABLE (ASCE 41-23 7.4.3.3.1)" % sf["text"]
    else:
        tstat = "NOT EVALUATED -- %s (ASCE 41-23 7.4.3.3.1)" % sf["text"]
    out.update(alpha2=alpha2, alpha2_basis=a2_basis, alpha_PDelta=alpha_pd, alpha_e=alpha_e, lambda_nf=lam,
               SX1_BSE2N=SX1_bse2n, mu_max=mu_max, nsp_permitted=permitted, nsp_strength_ok=bool(strength_ok),
               higher_modes=hm, nsp_status=nst, nsp_status_text=NSP_STATUS_TEXT[nst], theta_story1_elastic=theta1,
               nsp_ok=bool(permitted and out["reached_target"]), target_shortfall=sf, target_status=tstat)
    return out


# --------------------------------------------------------------------------- FEMA P-695 factors
def p695_factors(run, basis, nsp_bse1):
    from .package_reader import dir_basis
    u = np.asarray(run["rec"]["u"]); V = np.asarray(run["rec"]["V"])
    Vmax = float(V.max()); i_max = int(np.argmax(V))
    db = dir_basis(basis, run.get("direction"))                   # NL-R2-17: the push direction's own V / T / Om0 (mixed systems)
    Vdes = db["V_design_kip"]
    Omega = Vmax / Vdes if Vdes else None
    post = np.where((np.arange(len(V)) > i_max) & (V <= 0.8 * Vmax))[0]
    tail = run.get("tail", {})
    if tail.get("status") == "component_limit" and (not len(post) or tail["u_component_limit"] <= u[post[0]]):
        du = float(tail["u_component_limit"]); du_bound = "at component rotation limit b (non-simulated collapse, P-695 rule)"
    elif len(post):
        du = float(np.interp(0.8 * Vmax, V[post[0] - 1:post[0] + 1][::-1], u[post[0] - 1:post[0] + 1][::-1])); du_bound = "captured"
    else:
        du = float(u[-1]); du_bound = "LOWER BOUND (curve did not lose 20% of Vmax before the run stopped)"
    W = nsp_bse1["W_kip"]; T = max(db["T_design_s"] or 0.0, run["pattern"]["T1"])
    dy_eff = nsp_bse1["C0"] * (Vmax / W) * (G_IN / (4 * math.pi ** 2)) * T ** 2
    return dict(Vmax_kip=Vmax, u_at_Vmax_in=float(u[i_max]), V_design_kip=Vdes, Omega=Omega, Omega0_design=db["Om0"],
                R_design=db["R"], Cd_design=db["Cd"], system_design=db["system"], per_direction_basis=db["per_direction"],
                delta_u_in=du, delta_u_basis=du_bound, delta_y_eff_in=dy_eff, mu_T=du / dy_eff, T_used_s=T,
                Vmax_over_W=Vmax / W)


# --------------------------------------------------------------------------- component acceptance
def step_at(run, disp):
    """First recorded step whose roof displacement equals or exceeds `disp` (ASCE 41-23 7.4.3.3.1), or None
    when the push never reached it. (The old version clamped to the last step, NL-09.)"""
    u = np.asarray(run["rec"]["u"], dtype=float)
    hit = np.where(u >= disp - 1e-9)[0]
    return int(hit[0]) if len(hit) else None


def _census_drifts(run, hinges, i):
    """Groups, census, drifts and column axial at recorded step i."""
    pl = run["rec"]["hinge_pl"][i]
    groups, census = {}, {}
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
        brb = bool(h.get("brb")) or bool(getattr(s, "Asc", 0.0))
        mon = h.get("form") or ("axial" if h["kind"] == "brace" else "zeroLength")
        if brb:
            mon = "axial, TOTAL deformation (Table C3.3 limits = (1 + n) x Delta_y)"
        elif h["kind"] == "link":
            mon = "link shear spring, plastic deformation gamma_p x e (in; Table C2.4 limits x e)"
        g = groups.setdefault(key, dict(kind=h["kind"], section=h["section"], z_in=round(h["z"]), n=0, n_yielded=0,
                                          theta_pl_max=0.0, IO=s.IO, LS=s.LS, CP=s.CP, DC_IO=0.0, DC_LS=0.0, DC_CP=0.0,
                                          monitor=mon, brb=brb,
                                          units=("in" if h["kind"] in ("brace", "link") else "rad")))
        g["n"] += 1; g["n_yielded"] += int(yielded)
        # Group D/C = the largest D/C of its members, each against ITS OWN limits. The members of one
        # (kind, section, level) group can carry different limits (columns: Table C3.6 a/b vary with P_G/P_ye;
        # beams: span / Lb / RBS cut; braces: tension vs compression), so the member with the largest
        # deformation is not necessarily the governing one. The limits shown are those of the member that
        # governs CP (theta_at_governing is its deformation); theta_pl_max stays the largest deformation.
        if th > g["theta_pl_max"]:
            g["theta_pl_max"] = th
        for k in ("IO", "LS", "CP"):
            if dc[k] == dc[k] and dc[k] > g["DC_" + k]:
                g["DC_" + k] = dc[k]
                if k == "CP":
                    g.update(IO=lim["IO"], LS=lim["LS"], CP=lim["CP"], theta_at_governing=th)
        c = census.setdefault(round(h["z"]), dict(z_in=round(h["z"]), beam_hinges=0, beam_yielded=0, col_hinges=0, col_yielded=0,
                                                 brace_elements=0, brace_buckled=0, brace_yielded_T=0,
                                                 brb_elements=0, brb_yielded_C=0, brb_yielded_T=0, link_hinges=0, link_yielded=0))
        if h["kind"] == "beam":
            c["beam_hinges"] += 1; c["beam_yielded"] += int(yielded)
        elif h["kind"] == "brace" and brb:                             # BRBs yield in compression, they do not buckle
            c["brb_elements"] += 1
            if pl[j] < 0 and yielded: c["brb_yielded_C"] += 1
            if pl[j] > 0 and yielded: c["brb_yielded_T"] += 1
        elif h["kind"] == "brace":
            c["brace_elements"] += 1
            if pl[j] < 0 and yielded: c["brace_buckled"] += 1
            if pl[j] > 0 and yielded: c["brace_yielded_T"] += 1
        elif h["kind"] == "link":                                       # EBF link shear spring (NL-03)
            c["link_hinges"] += 1; c["link_yielded"] += int(yielded)
        else:
            c["col_hinges"] += 1; c["col_yielded"] += int(yielded)
    table = sorted(groups.values(), key=lambda g: (g["kind"], g["z_in"]))
    worst = {k: max((g["DC_" + k] for g in table if g["DC_" + k] == g["DC_" + k]), default=None) for k in ("IO", "LS", "CP")}
    story_u = run["rec"]["story_u"][i]
    drifts, prev = [], 0.0
    for k, (uk, hk) in enumerate(zip(story_u, run["heights"])):
        drifts.append(dict(story=k + 1, drift_ratio=(uk - prev) / hk)); prev = uk
    colN = run["rec"]["col_N"][i] if run["rec"].get("col_N") else []
    return table, worst, sorted(census.values(), key=lambda c: c["z_in"]), drifts, (float(max(colN)) if colN else None)


def acceptance(run, hinges, disp, level_name):
    """Per-hinge plastic rotation at the first recorded roof displacement >= `disp` (delta_t), D/C against
    IO/LS/CP, grouped by (kind, section, level z); the yielded-hinge census that shows the mechanism; the
    storey drifts. ASCE 41-23 7.4.3.3.1: element deformations at the control-node displacement equalling or
    exceeding delta_t shall satisfy 7.5.3.

    `status`: "evaluated" | "not_evaluated" (no monitored component, or no monitored beam/column although the
    frame has moment-frame members: worst_DC None, NEVER 0.00; or, NL-R2-24, reason "stopped_before_target": the push
    stopped numerically / at the drift cap short of delta_t with V > 0.8 Vmax) | "target_not_reached" (strength loss or
    rotation b before delta_t: worst_DC None, drifts None, acceptable False). `shortfall` (target_shortfall) says why. `acceptable` is None unless evaluated or the target
    was not reached (False); the performance level a Risk Category needs is applied by the consumer.
    `at_last_converged` (diagnostic only, when the target was not reached) carries the numbers at the last
    converged step, labelled as such."""
    kinds = {}
    for t in run["hinge_tags"]:
        kinds[hinges[t]["kind"]] = kinds.get(hinges[t]["kind"], 0) + 1
    n_bc = kinds.get("beam", 0) + kinds.get("col", 0)
    n_mf = run.get("n_moment_frame_members", 0) or 0
    n_brb = sum(1 for t in run["hinge_tags"] if hinges[t]["kind"] == "brace" and (hinges[t].get("brb") or getattr(hinges[t]["spec"], "Asc", 0.0)))
    monitored = dict(beam=kinds.get("beam", 0), col=kinds.get("col", 0), brace=kinds.get("brace", 0) - n_brb, brb=n_brb,
                     link=kinds.get("link", 0),
                     other=sum(v for k, v in kinds.items() if k not in ("beam", "col", "brace", "link")))
    base = dict(level=level_name, target_disp_in=float(disp), monitored=monitored, n_moment_frame_members=n_mf)
    i = step_at(run, disp)
    if i is None:
        j = len(run["rec"]["u"]) - 1
        table, worst, census, drifts, colN = _census_drifts(run, hinges, j)
        sf = target_shortfall(run, disp)
        if shortfall_is_failure(sf):                 # genuine strength loss / mechanism before delta_t
            verdict = dict(status=TARGET_NOT_REACHED, acceptable=False,
                           note="TARGET NOT REACHED: %s -- NOT ACCEPTABLE; component acceptance and drift at delta_t "
                                "not evaluated (ASCE 41-23 7.4.3.3.1)." % sf["text"])
        else:                                        # NL-R2-24: numerical stop / drift cap, still above 0.8 Vmax
            verdict = dict(status=NOT_EVALUATED, acceptable=None, reason="stopped_before_target",
                           note="NOT EVALUATED -- %s; component acceptance and drift at delta_t not evaluated "
                                "(ASCE 41-23 7.4.3.3.1)." % sf["text"])
        return dict(base, evaluated=False, shortfall=sf, **verdict,
                    roof_disp_in=None, step=None, groups=[], worst_DC=dict(IO=None, LS=None, CP=None), census=[],
                    story_drifts=[], max_story_drift=None, col_N_max_kip=None,
                    at_last_converged=dict(roof_disp_in=float(run["rec"]["u"][j]), step=j, worst_DC=worst,
                                           max_story_drift=max(d["drift_ratio"] for d in drifts) if drifts else None,
                                           note="diagnostic only -- NOT the target displacement"))
    table, worst, census, drifts, colN = _census_drifts(run, hinges, i)
    out = dict(base, roof_disp_in=float(run["rec"]["u"][i]), step=i, groups=table, worst_DC=worst, census=census,
               story_drifts=drifts, max_story_drift=max(d["drift_ratio"] for d in drifts), col_N_max_kip=colN)
    if not table:
        why = "no monitored component"
    elif n_mf > 0 and n_bc == 0:
        why = ("the frame has %d moment-frame beams but no beam or column hinge was monitored "
               "(only %s)" % (n_mf, ", ".join("%d %s" % (v, k) for k, v in kinds.items())))
    else:
        why = None
    if why:
        out.update(status=NOT_EVALUATED, evaluated=False, acceptable=None, worst_DC=dict(IO=None, LS=None, CP=None),
                   note="BPON NOT EVALUATED: %s -- no component acceptance can be claimed (ASCE 41-23 7.5.3)." % why)
        return out
    out.update(status=EVALUATED, evaluated=True, acceptable=None,
               note="%d monitored components (%s) at roof u = %.2f in >= delta_t %.2f in"
                    % (sum(kinds.values()), ", ".join("%d %s" % (v, k) for k, v in sorted(kinds.items())), out["roof_disp_in"], disp))
    return out


def level_verdict(acc, perf):
    """True / False / None for one acceptance block against performance level `perf` ("IO"|"LS"|"CP"):
    False when the target was not reached through strength loss, None when not evaluated (including a numerical
    stop before the target, NL-R2-24), else worst D/C <= 1.0."""
    if not acc:
        return None
    st = acc.get("status")
    if st == TARGET_NOT_REACHED:
        return False
    if st == NOT_EVALUATED:
        return None
    v = (acc.get("worst_DC") or {}).get(perf)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if st is None and not acc.get("groups"):          # package written before the status field: empty = not evaluated
        return None
    return bool(v <= 1.0)
