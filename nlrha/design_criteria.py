"""design_criteria.py -- the ASCE 7-22 Section 16.1.4 design criteria document, drafted from the package.

Chapter 16 requires design criteria to be prepared and reviewed before the analysis. Every input the document
needs already sits in the job folder: the Steltic package (system, hazard parameters, R/Cd/Om0/Ie, Risk Category,
drift basis), ch16_params.json (the clauses the tool implements, with the pdf pages read), the hinge parameters
(component models and their AISC 342 / ASCE 41 sources), site_hazard.json when a site-specific study was run,
gm_scaling.json / nlrha_package.json when the suite was selected or run, and retrieval_log.md if the analyst kept
one. The draft is written as design_criteria_16_1_4.docx (for mark-up) and .html (for the hub), and it says on
its face that it is a draft for the engineer of record and the 16.5 reviewer.
"""
from __future__ import annotations
import datetime, html as H, json, os, re

from . import docx_writer as DW

_HERE = os.path.dirname(os.path.abspath(__file__))
REVIEW_SCOPE = [
    ("Design criteria", "these criteria: the seismic hazard, the target spectra and the ground-motion selection, the modelling approach, the acceptance criteria, and the peer-review process itself (16.5)"),
    ("Site hazard and ground motions", "site class and V_s30, the MCE_R target (Method 1 or 2), record set provenance, tectonic-regime / M / R consistency, period range, scaling, orientation, near-fault pulse content (16.2)"),
    ("Analytical model", "3-D model, element formulations and backbones (AISC 342 tables and the adjustments applied), expected material strengths, panel zones, gravity loads and P-delta, damping, integration, diaphragm and foundation idealisation (16.3)"),
    ("Acceptance", "unacceptable-response criteria, mean drift limits, residual drift where applicable, deformation-controlled and force-controlled element checks with their gamma, phi and B, gravity-system compatibility (16.4)"),
    ("Linear analysis", "the Chapter 12 analysis the package rests on (16.1.2), including any 16.1.2 drift relief and the values of rho and Omega_0 taken"),
    ("Documentation", "the analysis package (report, per-record results, element demands), the reviewer's findings and their resolution, and the sealed design"),
]


def _json(path, default=None):
    if not os.path.exists(path):
        return default
    s = open(path, encoding="utf-8").read()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s))


def _rc(pkg, calc, cfg_text, override=None):
    from . import acceptance as AC
    return AC.risk_category(pkg, override)


def gather(job, params_path=None, risk_category=None):
    """Everything the document reads, with a source for each item."""
    from pushover import package_reader as PR, hinge_models as HM
    job = os.path.abspath(job)
    pkg = PR.load(job)
    ch16 = json.load(open(os.path.join(_HERE, "ch16_params.json"), encoding="utf-8"))
    prm_path = params_path or next((p for p in (os.path.join(job, "pushover", "hinge_params_used.json"), os.path.join(job, "hinge_params_base.json")) if os.path.exists(p)), None)
    prm = HM.load_params(prm_path) if prm_path else HM.load_params(None)
    cfg_text = open(os.path.join(job, "cfg.py"), encoding="utf-8", errors="replace").read() if os.path.exists(os.path.join(job, "cfg.py")) else ""
    rc = _rc(pkg, pkg.calc, cfg_text, risk_category)
    relief = None
    m = re.search(r"drift_relief_16_1_2['\"]?\]?\s*=\s*(\{.*?\})\s*$", cfg_text, flags=re.M | re.S)
    if m:
        try:
            relief = json.loads(m.group(1).replace("'", '"'))
        except Exception:
            relief = {"present": True}
    return dict(job=job, pkg=pkg, name=pkg.name, ch16=ch16, prm=prm, prm_path=prm_path, rc=rc, cfg_text=cfg_text, relief=relief,
                calc=pkg.calc or {}, hazard=_json(os.path.join(job, "nlrha", "site_hazard.json")),
                gm=(_json(os.path.join(job, "nlrha", "gm_scaling.json")) or {}).get("gm"),
                nl=_json(os.path.join(job, "nlrha", "nlrha_package.json")), po=_json(os.path.join(job, "pushover", "pushover_package.json")),
                dor=_json(os.path.join(job, "design", "design_of_record.json")),
                retrieval=next((open(p, encoding="utf-8", errors="replace").read() for p in (os.path.join(job, "retrieval_log.md"), os.path.join(job, "nlrha", "retrieval_log.md"), os.path.join(job, "pushover", "retrieval_log.md")) if os.path.exists(p)), None),
                drift_limit=(float(re.search(r"drift_limit\s*=\s*([0-9.]+)", cfg_text).group(1)) if re.search(r"drift_limit\s*=\s*([0-9.]+)", cfg_text) else None),
                rho=(float(re.search(r"\brho\s*=\s*([0-9.]+)", cfg_text).group(1)) if re.search(r"\brho\s*=\s*([0-9.]+)", cfg_text) else None))


def _readable(v, limit=400):
    """A capacity-design / check record as 'key: value' text, not a raw JSON dump (register item NL-26)."""
    if isinstance(v, str):
        out = v
    elif isinstance(v, dict):
        bits = []
        for k, x in v.items():
            if isinstance(x, (list, dict)):
                x = ("%d entries" % len(x)) if isinstance(x, list) else ", ".join("%s %s" % (kk, xx) for kk, xx in list(x.items())[:4] if not isinstance(xx, (list, dict)))
            bits.append("%s %s" % (str(k).replace("_", " "), x))
        out = "; ".join(bits)
    elif isinstance(v, list):
        out = "%d entries" % len(v)
    else:
        out = str(v)
    return out[:limit] + ("..." if len(out) > limit else "")


def _params_status(g):
    """(text naming the values not supplied by the user, effective verified) -- the run's own record when the
    pushover ran (pushover_package.json), else the parameter file's provenance for the groups this building uses."""
    from pushover import params_schema as PS
    prm, po = g["prm"], g.get("po") or {}
    if "params_verified" in po and "params_unverified" in po:
        unv = po.get("params_unverified") or {}
        txt = "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in unv.items()) or "(see pushover report)"
        return txt[:600], bool(po.get("params_verified"))
    kinds = {str(v.get("member") or "").lower() for v in (g["pkg"].schedule or {}).values() if isinstance(v, dict)}
    unv = {k: v for k, v in (prm.get("_unverified") or {}).items() if v and (k != "brace_axial" or "brace" in kinds)}
    view = dict(prm, _used_unverified=unv)
    return ((PS.unverified_text(view) if unv else "") or "the file has verified=false"), bool(prm.get("verified")) and not unv


# --------------------------------------------------------------------------- NL-R2-05: what the run used
def _used(g):
    """What the analyses on file actually built, so section 5 states the run, not fixed strings: the NLRHA's element
    census / 16.3.1 statement (nlrha_package.json model_stats, degradation_16_3_1), the pushover's (pushover_package.json
    hinge_stats), and the member kinds of the package. Absent pieces stay None -- the rows then say "not run"."""
    from pushover import sections_db as SDB
    nl, po = g.get("nl") or {}, g.get("po") or {}
    sched = [v for v in (g["pkg"].schedule or {}).values() if isinstance(v, dict)]
    kind = lambda v: str(v.get("member") or "").lower()
    secs = [str(v.get("section") or "") for v in sched if kind(v) == "brace"]
    nls, pos = nl.get("model_stats") or None, po.get("hinge_stats") or None
    return dict(nl=bool(nl), po=bool(po), nls=nls, pos=pos, deg=nl.get("degradation_16_3_1"),
                n_brace=sum(1 for x in secs if not SDB.is_brb(x)), n_brb=sum(1 for x in secs if SDB.is_brb(x)),
                n_col=sum(1 for v in sched if kind(v) == "col"), n_beam=sum(1 for v in sched if kind(v) == "beam"),
                links=max(int((nls or {}).get("links") or 0), int((pos or {}).get("links") or 0)),
                n_moment_beam=sum(1 for e in g["pkg"].model.elements if "etype" not in e and not e.get("release")
                                  and kind(g["pkg"].schedule.get(e["tag"]) or {}) == "beam"))


def _census(st, kind):
    """'44 nonlinear (44 physical-theory fibre, 0 Table C3.4 truss)' / '348 beam hinges, imk' from a builder census, or None."""
    if not st:
        return None
    if kind == "brace":
        nb = int(st.get("brace_nonlinear") or 0) - int(st.get("brb") or 0)
        npt = int(st.get("brace_physical_theory") or 0)
        return ("%d nonlinear (%d physical-theory fibre, %d Table C3.4 truss)" % (nb, npt, nb - npt)) if nb > 0 else "none nonlinear"
    n = st.get(kind)
    if n is None:
        return None
    return "%s %s hinges, %s" % (n, "beam" if kind == "beam" else "column", (st.get("degradation") or {}).get("plasticity") or st.get("plasticity") or "plasticity not recorded")


def _run_text(u, kind):
    run = "; ".join(x for x in (("NLRHA built " + _census(u["nls"], kind)) if _census(u["nls"], kind) else None,
                                ("pushover built " + _census(u["pos"], kind)) if _census(u["pos"], kind) else None) if x)
    return run or "no analysis on file yet (not run)"


def _hinge_row(group, u, kind):
    base = str(group.get("basis") or group.get("table") or "")
    txt = ((("mode %s: " % group["mode"]) if group.get("mode") else "") + base) if base else "not in the parameter file -- repository template (UNVERIFIED)"
    return txt[:300] + " -- " + _run_text(u, kind)


def _lambda_text(ls):
    if not ls:
        return ""
    return "; Lambda as built: " + ", ".join("%s %s-%s rad (%d hinges%s)" % ("beams" if k == "beam" else "columns", v["min"], v["max"], v["n"],
                                                                           (", %d at 0" % v["n_zero"]) if v.get("n_zero") else "") for k, v in sorted(ls.items()))


def _degradation_row(g, u):
    """(criterion text, source) of the 16.3.1 row: the run's own statement when the NLRHA ran, else what the parameter
    file would build, saying plainly that the NLRHA has not run."""
    deg = u["deg"]
    if deg:
        parts = "; ".join("%s: %s" % (i.get("component"), i.get("modelled")) for i in deg.get("items") or [])
        verdict = ("modelled for every component family present" if deg.get("demonstrated") else
                   "NOT demonstrated for: %s -- 16.3.1 requires it unless shown not to govern [engineer to justify or add]"
                   % ", ".join(str(i.get("component")) for i in deg.get("items") or [] if not i.get("ok")))
        return ("%s. %s%s" % (verdict, parts, _lambda_text((u["nls"] or {}).get("lambda_summary"))))[:900], "nlrha/nlrha_package.json degradation_16_3_1 · ASCE 7-22 16.3.1"
    cdet = g["prm"].get("cyclic_deterioration") or {}
    mode = str(cdet.get("mode") or "none").lower()
    plan = ("not modelled (cyclic_deterioration.mode = none)" if mode in ("none", "off", "0") else
            "IMK energy-based deterioration, cyclic_deterioration.mode = %s%s" % (mode, (" -- %s" % str(cdet.get("source"))[:120]) if cdet.get("source") else ""))
    if u["nl"]:
        return ("not recorded by the NLRHA on file (its nlrha_package.json predates the 16.3.1 statement) -- re-run; the parameter file sets: %s" % plan,
                "component parameters file · ASCE 7-22 16.3.1")
    return ("NLRHA not run yet -- the parameter file sets: %s; the run states what it built" % plan, "component parameters file · ASCE 7-22 16.3.1")


def _base_text(pkg):
    """Column-base restraint as replayed from model_opensees.py (the supports the run uses), not a cfg regex."""
    m = pkg.model
    if not m.fixes or not m.nodes:
        return "no support records read"
    z0 = min(v[2] for v in m.nodes.values())
    kinds = {}
    for t, f in m.fixes.items():
        if t in m.nodes and abs(m.nodes[t][2] - z0) < 1e-6 and len(f) >= 6 and all(f[:3]):
            k = "fixed" if all(f[3:6]) else ("pinned" if not any(f[3:6]) else "partially restrained")
            kinds[k] = kinds.get(k, 0) + 1
    return (", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())) + " base node(s)") if kinds else "no fixed base nodes read"


# --------------------------------------------------------------------------- the content, once, for both outputs
class Section:
    def __init__(self, title, level=1):
        self.title, self.level, self.items = title, level, []

    def p(self, text, style=None):
        self.items.append(("p", text, style))

    def b(self, text, lead=None):
        self.items.append(("b", text, lead))

    def t(self, rows):
        self.items.append(("t", rows, None))

    def n(self, text):
        self.items.append(("n", text, None))

    def c(self, text):
        self.items.append(("c", text, None))


def content(g, project=None, engineer=None, reviewer=None):
    from . import report as RP                                     # NL-R2-18: the record-window disclosure texts
    pkg, b, ch16, prm, rc = g["pkg"], g["pkg"].basis, g["ch16"], g["prm"], g["rc"]
    calc = g["calc"]; cd = calc.get("capacity_design") or {}
    rcs = rc.replace("_", "/")
    hn_ft = (sum(b.heights_in) / 12.0) if b.heights_in else None
    if hn_ft is None and g["po"] and g["po"].get("gravity"):
        hn_ft = g["po"]["gravity"][-1]["z_in"] / 12.0
    S = []
    s = Section("1. Purpose, project and scope"); S.append(s)
    s.p("This document states the design criteria for the nonlinear response history analysis of %s under ASCE/SEI 7-22 Chapter 16, as Section 16.1.4 requires before the analysis is performed. It records the seismic hazard and ground motions, the analytical model, the acceptance criteria, the linear analysis the design rests on, and the scope of the independent design review of Section 16.5. It is a DRAFT generated from the design package by the Nonlinear module for the engineer of record to complete and the reviewer to accept; bracketed items and the open-items list mark what the engineer must supply."
        % pkg.name)
    s.t([["Item", "Value", "Source"],
         ["Project", project or "[project name / address]", "input"],
         ["Building", "%s · %s · %s levels%s" % (pkg.name, b.system or cd.get("system") or "[SFRS]", len(b.heights_in) if b.heights_in else (len(g["po"]["gravity"]) if g["po"] else "?"), (" · h_n = %.0f ft" % hn_ft) if hn_ft else ""), "cfg.py / calc_package.json"],
         ["Seismic force-resisting system", str(cd.get("system") or b.system or "[system]"), "calc_package.json capacity_design.system"]]
        + [["Direction %s system (mixed, ASCE 7-22 12.2.2)" % d, "%s -- R %s, C_d %s, Omega_0 %s" % (x.get("system"), x.get("R"), x.get("Cd"), x.get("Om0")),
            "; ".join(sorted(set((x.get("source") or {}).values())))] for d, x in sorted((getattr(b, "by_dir", None) or {}).items())]      # NL-R2-17
        + [
         ["Risk Category / I_e", "%s / %s" % (rcs, b.Ie), "cfg.py (Ie) · nlrha acceptance rule"],
         ["Engineer of record", engineer or "[name, licence]", "input"], ["Independent reviewer (16.5)", reviewer or "[name, licence, firm]", "input"],
         ["Design of record", ("promoted from loop %s on %s" % (g["dor"].get("loop"), g["dor"].get("promoted_at"))) if g["dor"] else "the Steltic package in this job folder", "design/design_of_record.json"]])
    s.p("Scope. The Chapter 16 analysis supplements, and does not replace, the Chapter 12 linear design (16.1.2): every member, connection and the drift of the linear package remain the basis of the design except where these criteria state otherwise. Nonstructural components, foundations and the soil-structure interface are outside the nonlinear model; their demands come from the linear package and Chapter 13.")

    s = Section("2. Governing documents"); S.append(s)
    for d in ("ASCE/SEI 7-22, Minimum Design Loads and Associated Criteria for Buildings and Other Structures -- Chapters 11, 12, 16, 20, 21", "AISC 360-22, Specification for Structural Steel Buildings (LRFD)",
              "AISC 341-22, Seismic Provisions for Structural Steel Buildings", "AISC 358-22, Prequalified Connections for Special and Intermediate Steel Moment Frames",
              "ANSI/AISC 342-22, Seismic Provisions for Evaluation and Retrofit of Existing Structural Steel Buildings (component models and acceptance criteria, through ASCE 41-23)",
              "ASCE/SEI 41-23, Seismic Evaluation and Retrofit of Existing Buildings (Chapter 7 analysis procedures, where cited by the component models)",
              "FEMA P-695 (2009), Quantification of Building Seismic Performance Factors -- far-field record set", "[locally adopted building code and amendments]"):
        s.b(d)
    s.p("Clause references in this document were read against the analyst's converted ASCE 7-22 text (Chapter 16, pdf pages %s); the rules are paraphrased, never quoted." % ", ".join(sorted({str(v.get("pdf_page")) for v in ch16.values() if isinstance(v, dict) and v.get("pdf_page")})))

    s = Section("3. Seismic hazard"); S.append(s)
    hz = g["hazard"]
    s.t([["Parameter", "Value", "Source"],
         ["S_DS / S_D1 (g)", "%s / %s" % (b.SDS, b.SD1), "cfg.py"], ["S_MS / S_M1 (g) = 1.5 x design", "%.3f / %.3f" % (1.5 * (b.SDS or 0), 1.5 * (b.SD1 or 0)), "16.2.1.1 -> 11.4.6"],
         ["S_1 (g) · T_L (s)", "%s · %s" % (b.S1, (re.search(r"TL\s*=\s*([0-9.]+)", g["cfg_text"]).group(1) if re.search(r"TL\s*=\s*([0-9.]+)", g["cfg_text"]) else "8.0")), "cfg.py"],
         ["Site class", (hz["site"]["site_class"] if hz else (b.site_class or "[site class from the geotechnical report]")), "site_hazard.json" if hz else "input"],
         ["Seismic Design Category", str(hz["design"].get("sdc")) if hz else "[per 11.6]", "USGS service" if hz else "input"]])
    if hz:
        d = hz["design"]; s.p("A site-specific study was run with the USGS ASCE 7-22 web service and the USGS NSHM disaggregation service (site %.4f, %.4f; V_s30 %s m/s). USGS values: S_S %s, S_1 %s, S_MS %s, S_M1 %s, S_DS %s, S_D1 %s, T_L %s s%s."
                               % (hz["site"]["latitude"], hz["site"]["longitude"], hz["site"].get("vs30"), d.get("ss"), d.get("s1"), d.get("sms"), d.get("sm1"), d.get("sds"), d.get("sd1"), d.get("tl"),
                                  (" -- these DIFFER from the package's S_DS / S_D1 (%s / %s); the linear design basis must be reconciled before the analysis" % (b.SDS, b.SD1)) if (abs((d.get("sds") or 0) - (b.SDS or 0)) > 0.05 or abs((d.get("sd1") or 0) - (b.SD1 or 0)) > 0.05) else ""))
        rows = [["Conditioning period T* (s)", "IMT", "Return period", "mean M", "mean R (km)", "mean epsilon"]]
        for c in hz["targets"].get("cs", []):
            rows.append(["%.2f" % c["T_star"], c.get("imt"), "%s yr" % c.get("return_period"), "%.2f" % (c["M"] or 0), "%.1f" % (c["R_km"] or 0), "%.2f" % c["eps"]])
        if len(rows) > 1:
            s.p("Disaggregation of the site hazard at the conditioning period(s):"); s.t(rows)
        nf = hz.get("near_fault") or {}
        s.p("Near-fault screen (11.4.1 / 16.2.2): %s" % (("NEAR-FAULT -- %s carry %.0f%% of the hazard inside the 11.4.1 distances; pulse-type motions shall make up about %.0f%% of the suite." % (", ".join("%s (M %.1f, %.0f km, %.0f%%)" % (x["name"], x["M"], x["R_km"], x["contribution_pct"]) for x in nf.get("sources", [])[:5]), nf.get("hazard_share_pct", 0), 100 * nf.get("pulse_fraction", 0))) if nf.get("near_fault") else "no source with at least %g%% contribution lies inside the 11.4.1 distances; the site is not treated as near-fault." % 5.0))
    else:
        s.p("No site-specific hazard study is on file: the target spectrum is Method 1 of 16.2.1.1 (1.5 x the design spectrum of the package). [State whether a site-specific study (Chapter 21) is required for this site and Risk Category.]")

    s = Section("4. Target spectrum, ground motions, scaling (16.2)"); S.append(s)
    gm = g["gm"] or (g["nl"] or {}).get("ground_motions")
    s.t([["Criterion", "Rule adopted", "Clause"],
         ["Target spectrum", (gm.get("target_label") if gm and gm.get("target_label") else ch16["target_spectrum"]["rule"]), ch16["target_spectrum"]["clause"]],
         ["Number of motions", "%d pairs of orthogonal horizontal components (minimum %d)" % ((len(gm["selected"]) if gm else 11), ch16["n_motions"]["min"]), ch16["n_motions"]["clause"]],
         ["Record set(s)", ("; ".join("%s (%s pairs)" % (x.get("set"), x.get("n")) for x in gm.get("sets") or []) if gm and gm.get("sets") else "FEMA P-695 far-field set, 22 pairs (PEER NGA files as distributed by ATC-63)"), "16.2.2"],
         ["Selection", ("spectral-shape fit to the target over the period range" + ("; ranked with a consistency penalty on M and R against the disaggregation mean (M %.2f, R %.0f km)" % ((gm["deagg"] or {}).get("M") or 0, (gm["deagg"] or {}).get("R_km") or 0) if gm and gm.get("deagg") else "; M / R / tectonic regime consistency to be confirmed against the project hazard")) if gm or True else "", "16.2.2"],
         ["Period range", ("%.2f - %.2f s" % (gm["T_lower"], gm["T_upper"])) if gm else "0.2 x T_min (and the modes giving 90%% mass) to 2.0 x T_max", ch16["period_range"]["clause"]],
         ["Scaling", ch16["amplitude_scaling"]["rule"] + ((" -- suite mean / target min %.3f, factors %.2f-%.2f" % (gm["min_ratio_in_range"], min(r["sf"] for r in gm["selected"]), max(r["sf"] for r in gm["selected"]))) if gm else ""), ch16["amplitude_scaling"]["clause"]],
         ["Spectral matching", "not used", ch16["spectral_matching"]["clause"]],
         ["Orientation", ch16["orientation"]["rule"] + ((" -- deviations X %.2f, Y %.2f" % (gm["orientation_dev_x"], gm["orientation_dev_y"])) if gm else ""), ch16["orientation"]["clause"]],
         ["Pulse-type share", ("%.0f%% of the suite (%d records)" % (100 * gm.get("pulse_fraction", 0), gm.get("n_pulse", 0))) if gm and gm.get("pulse_fraction") else "none required (not near-fault) [confirm]", "16.2.2 / 16.2.4"],
         # NL-R2-18: the record window (end cut, optional trimming) is a modification of the motions -> disclosed here
         ["Record window / trimming", re.sub(r"<[^>]+>", "", RP.window_text(gm["record_window"])) if gm and gm.get("record_window") else
          "each record from its start to the 99.5 % Arias point plus free vibration [end cut not yet checked: re-run `nlrha scale`]", "16.1.4 / 16.2.3.2"]])
    if gm:
        rows = [["#", "Earthquake · station", "M", "R_rup (km)", "scale factor", "component to X", "pulse", "analysed window (s) · spectral check"]]
        for i, r in enumerate(gm["selected"]):
            rows.append([str(i + 1), "%s · %s" % (r.get("earthquake") or r["id"], r.get("station") or ""), ("%.1f" % r["M"]) if isinstance(r.get("M"), (int, float)) else "-", ("%.1f" % r["r_rup_km"]) if isinstance(r.get("r_rup_km"), (int, float)) else "-",
                         "%.2f" % r["sf"], str(r["comp1"] if r["x_comp"] == 1 else r["comp2"]), "yes" if r.get("pulse") else "",
                         re.sub(r"<[^>]+>", "", RP.window_cell(r.get("window"))).replace("&gt;", ">")])
        s.p("Selected suite (from %s):" % ("nlrha/gm_scaling.json" if g["gm"] else "nlrha/nlrha_package.json")); s.t(rows)
    else:
        s.p("[The suite has not been selected yet: run `nlrha scale` (or the full analysis) and re-issue this document; the selected records, factors and orientation are then tabulated here.]")

    s = Section("5. Analytical model (16.3)"); S.append(s)
    bf, cf, br = prm.get("beam_flexure") or {}, prm.get("column_flexure") or {}, prm.get("brace_axial") or {}
    unv_txt, prm_ok = _params_status(g)
    if bf.get("modifiers"):
        adj = "; ".join("x%.2f: %s" % (float(m.get("factor")), m.get("why")) for m in bf["modifiers"])
    elif str(bf.get("mode") or "") == "fr_connection" and not bf.get("modifier_checks"):
        adj = ("NOT EVALUATED -- the Table C5.5 footnote / Section C5.4a.1.a.1(a)-(d) conditions (continuity plates, panel-zone "
               "strength ratio, clear span to depth, flange and web slenderness) were not checked, so no modifier was applied "
               "[engineer to evaluate]")
    else:
        adj = "none required by the checks recorded"
    if bf.get("modifier_checks"):
        adj += "; checks: " + _readable(bf.get("modifier_checks"))
    mat = prm.get("material") or {}
    u = _used(g)                                                     # NL-R2-05: what the run actually built
    deg_txt, deg_src = _degradation_row(g, u)
    pz_mode = (u["nls"] or u["pos"] or {}).get("panel_zone_mode") or (prm.get("panel_zones") or {}).get("mode", "rigid")
    brace_txt = ("%d buckling braces in the package. Parameter file: %s; NLRHA element: %s. %s"
                 % (u["n_brace"], str(br.get("basis") or br.get("table") or "brace_axial not in the file")[:200], br.get("nlrha_element") or "truss",
                    _run_text(u, "brace"))) if u["n_brace"] else "no buckling braces in the package schedule"
    nl_damp = ((g["nl"] or {}).get("ch16") or {}).get("damping") or ch16["damping"]
    s.t([["Aspect", "Criterion adopted", "Source / clause"],
         ["Model", "three-dimensional model of the package (nodes, elements, diaphragms and masses replayed from model_opensees.py); hysteretic behaviour per AISC 342 component models", ch16["modeling"]["clause"]],
         ["Expected material", "F_ye = R_y F_y = %s x %s ksi%s%s" % (mat.get("Ry_expected"), mat.get("Fy_ksi"), (" (grade %s)" % mat["active_grade"]) if mat.get("active_grade") else "",
                                                                  ("; braces %s x %s ksi" % (br.get("Ry_expected"), br.get("Fy_ksi"))) if (u["n_brace"] and br.get("Fy_ksi")) else ""),
          "component parameters material.Fy_ksi / Ry_expected (AISC 342 A5.2)"],
         ["Beam hinges", _hinge_row(bf, u, "beam") if u["n_beam"] else "no beams in the package schedule", "AISC 342 Table C5.5 / C2.2"],
         ["Adjustments to beam hinges", adj, "AISC 342 C5.4a.1.a.1"],
         ["Column hinges", _hinge_row(cf, u, "col") if u["n_col"] else "no columns in the package schedule", "AISC 342 Table C3.6"],
         ["Braces", brace_txt, "AISC 342 Table C3.4 / Commentary C3"],
         *([["Buckling-restrained braces", "%d BRBs in the package; AISC 342 Table C3.3 backbone, core strength (F_ysc, omega, beta, R_y) from the parameter file or the HR package%s"
             % (u["n_brb"], ("; NLRHA built %s BRB elements" % u["nls"]["brb"]) if (u["nls"] or {}).get("brb") is not None else "; NLRHA not run"), "AISC 342 Table C3.3 / AISC 341 F4.2a"]] if u["n_brb"] else []),
         *([["EBF links", "%d link shear springs as built (AISC 342 Table C2.4 backbone)" % u["links"], "AISC 342 Table C2.4"]] if u["links"] else []),
         ["Panel zones", "%s (mode: %s)" % ("modelled as scissors zones" if pz_mode != "rigid" else "rigid joints (no explicit panel-zone spring)", pz_mode) + ("; design check: %s" % _readable(cd.get("panel_zone")) if cd.get("panel_zone") else ""), "AISC 342 C4.3a / AISC 341 E3.6e"],
         ["Cyclic deterioration", deg_txt, deg_src],
         ["Gravity loads", ch16["gravity"]["rule"], ch16["gravity"]["clause"]],
         ["P-delta", ch16["p_delta"]["rule"], ch16["p_delta"]["clause"]],
         ["Torsion", "inherent eccentricity of the package masses; accidental torsion is NOT applied by the analysis (16.3.4 requires it where a Type 1 horizontal irregularity exists) [engineer to confirm]", ch16["torsion"]["clause"]],
         ["Damping", "%.1f%% Rayleigh on the elastic elements and mass at T_1 and 0.2 T_1 (cap %.1f%%)" % (100 * nl_damp.get("xi_used", 0.025), 100 * nl_damp.get("xi_max", 0.025)), ch16["damping"]["clause"]],
         ["Integration", "%s, dt %s s, adaptive sub-stepping on non-convergence, free vibration after the record window (section 4)" % (str(nl_damp.get("integrator", "hht")).upper(), nl_damp.get("dt_s", 0.01)), "analysis settings"],
         ["Diaphragms / foundations", "%s; column bases as replayed from the package model (%s); no soil-structure interaction" % (("%d rigid diaphragm(s) as in the linear model" % len(pkg.model.diaphragms)) if pkg.model.diaphragms else "no rigid-diaphragm constraint in the package model", _base_text(pkg)), "16.3.6 [engineer to confirm]"],
         ["Component parameters file", os.path.basename(g["prm_path"]) if g["prm_path"] else "repository template (UNVERIFIED)",
          ("verified -- %s" % (prm.get("source") or "")[:200]) if prm_ok else ("UNVERIFIED -- values not supplied by the user: %s" % unv_txt)]])

    s = Section("6. Acceptance criteria (16.4)"); S.append(s)
    x2c = ch16["force_controlled"].get("exception_2") or {}
    tab = ch16["transient_drift"]["table_12_12_1_all_other"].get(rc, 0.02)
    mean_lim = ch16["transient_drift"]["factor_on_table_12_12_1"] * tab
    tall = None
    if hn_ft and hn_ft > ch16["transient_drift"]["tall_height_ft"]:
        tall = max(ch16["transient_drift"]["tall_a"] - ch16["transient_drift"]["tall_b"] * hn_ft, ch16["transient_drift"]["tall_floor"])
    s.t([["Criterion", "Rule adopted for Risk Category %s" % rcs, "Clause"],
         ["Suite statistic", ch16["results_basis"]["rule"], ch16["results_basis"]["clause"]],
         ["Unacceptable response", ch16["unacceptable_response"]["rule"] + " -- permitted here: %d" % ch16["unacceptable_response"]["max_unacceptable"].get(rc, 0), ch16["unacceptable_response"]["clause"]],
         ["Mean transient story drift", "<= %.1f%% of h_sx (2 x the Table 12.12-1 value %.3f)%s; peak per record <= %.1f%%" % (100 * mean_lim, tab, (" and <= %.2f%% by the height formula" % (100 * tall)) if tall else "", 150 * mean_lim)
          + ((" -- per direction (mixed systems, 12.2.2): %s; each takes the 'all other structures' row of Table 12.12-1 (16.4.1.2), so the limit is the same in both directions"
              % ", ".join("%s %s" % (d, x.get("system")) for d, x in sorted((getattr(b, "by_dir", None) or {}).items()))) if getattr(b, "by_dir", None) else ""), ch16["transient_drift"]["clause"]],
         ["Residual drift", ("<= %.1f%% of h_sx (h_n > %d ft)" % (100 * ch16["residual_drift"]["limit"], ch16["residual_drift"]["height_ft"])) if (hn_ft and hn_ft > ch16["residual_drift"]["height_ft"]) else "not applicable (h_n <= %d ft)" % ch16["residual_drift"]["height_ft"], ch16["residual_drift"]["clause"]],
         ["Deformation-controlled elements", "mean of the per-record peak deformations <= CP of the component model and within the valid modelling range b; " + ch16["deformation_controlled"]["rule"], ch16["deformation_controlled"]["clause"]],
         ["Force-controlled elements", ch16["force_controlled"]["rule"] + " -- gamma = %.1f, phi = %s (critical) / %.1f (ordinary), B = %.1f" % (ch16["force_controlled"]["gamma"], ch16["force_controlled"]["phi_critical"], ch16["force_controlled"]["phi_ordinary"], ch16["force_controlled"]["B"]), ch16["force_controlled"]["clause"]],
         # NL-R2-16: the use of 16.4.2.1 Exception 2 is stated in the criteria before the analysis
         ["Force-controlled actions limited by a yield mechanism", ((x2c.get("rule") or "") + ". APPLIED to: " + (x2c.get("implemented_for") or "") + " (" + (x2c.get("decision") or "") + ")")
          if x2c.get("apply", True) else "16.4.2.1 Exception 2 NOT used: every force-controlled action by the default equations", x2c.get("clause", "16.4.2.1 Exception 2")],
         ["Gravity system", ch16["gravity_system"]["rule"], ch16["gravity_system"]["clause"]]])
    s.n("Table 12.12-1 values for Risk Category III and IV are transcribed in ch16_params.json and marked RE-VERIFY through Query file manager before use in a deliverable.")
    s.p("Element classification for this building:", style=None)
    dc_items = [x for x in (("beam flexural hinges (%s)" % str(bf.get("basis") or bf.get("table") or "AISC 342 Table C2.2 / C5.5")[:120]) if u["n_beam"] else None,
                            "column hinges with P_G/P_ye <= 0.6 (Table C3.6)" if u["n_col"] else None,
                            "buckling-brace axial deformation (Table C3.4)" if u["n_brace"] else None,
                            "BRB axial deformation (Table C3.3)" if u["n_brb"] else None,
                            "EBF link shear (Table C2.4)" if u["links"] else None) if x]
    s.b("deformation-controlled: %s; the acceptance limit is CP for the Chapter 16 check with the valid range b." % "; ".join(dc_items or ["none identified in the package schedule"]), "Deformation-controlled --")
    s.b("force-controlled: column axial compression in %s and any column with P_G/P_ye > 0.6 (critical; checked by the analysis, 16.4.2.1), column splices and base plates (critical; NOT checked by the analysis -- from the linear package), %sdiaphragm and collector forces (from the linear package with Omega_0)."
        % ("braced-frame and gravity columns" if (u["n_brace"] or u["n_brb"]) else "gravity columns",
           "beam-column panel-zone shear where rigid joints are assumed (ordinary), " if (u["n_moment_beam"] and pz_mode == "rigid") else ""), "Force-controlled --")
    if x2c.get("apply", True):
        s.b("16.4.2.1 Exception 2 is used for the axial force of moment-frame and braced-frame columns whose flexure is deformation-controlled and whose column line receives seismic force only from modelled yielding components (beam hinges, braces): such an axial force is limited by the yield mechanism and is checked by Eqs. (16.4-3) / (16.4-4) with the capacity-limited effect E_mc (load factor 1.0) instead of Eqs. (16.4-1) / (16.4-2). The analysis report lists the columns it was applied to and reports the default check beside it. Gravity columns and columns with force-controlled flexure keep the default check.", "Exception 2 --")
    s.b("[The engineer of record designates any further critical / ordinary / noncritical actions and the B factor where expected strength is used.]", "To complete --")

    s = Section("7. The linear analysis the design rests on (16.1.2)"); S.append(s)
    bdir = getattr(b, "by_dir", None) or {}                          # NL-R2-17: mixed systems (12.2.2)
    rel = g["relief"]
    s.t([["Item", "Value", "Source"],
         ["Design procedure", "ASCE 7-22 Chapter 12 %s; AISC 360/341 LRFD member and connection design (HR Steel package)" % ("ELF + MRSA" if "RS" in g["cfg_text"] else "ELF"), "cfg.py analyses"],
         ["R / C_d / Omega_0 / I_e", ("%s / %s / %s / %s" % (b.R, b.Cd, b.Om0, b.Ie)) if not bdir else
          " · ".join("%s (%s): %s / %s / %s / %s" % (d, x.get("system"), x.get("R"), x.get("Cd"), x.get("Om0"), b.Ie) for d, x in sorted(bdir.items())),
          "cfg.py" if not bdir else "per direction (12.2.2): " + b.sources.get("by_dir", "")],
         ["Redundancy rho", (str(g["rho"] if g["rho"] is not None else "[rho]") if not bdir else " · ".join("%s: %s" % (d, x.get("rho", "[rho]")) for d, x in sorted(bdir.items())))
          + " (16.1.2 permits rho = 1.0 with a Chapter 16 analysis)", "cfg.py" if not bdir else "per direction"],
         ["Design base shear / seismic weight", ("%s / %s kip" % (b.V_design_kip, b.W_kip)) if not bdir else
          " · ".join("V_%s %s" % (d, x.get("V_design_kip")) for d, x in sorted(bdir.items())) + " / W %s kip" % b.W_kip, "report.html" if not bdir else "per direction (12.2.2): " + b.sources.get("by_dir", "")],
         ["Story drift limit of the linear design", (("%.3f h_sx" % g["drift_limit"]) if g["drift_limit"] else "[Table 12.12-1]") if not (bdir and all(x.get("drift_limit") for x in bdir.values())) else
          " · ".join("%s %.3f h_sx" % (d, x["drift_limit"]) for d, x in sorted(bdir.items())), "cfg.py drift_limit" if not bdir else "per direction"],
         ["16.1.2 drift relief", ("IN FORCE: linear target %s from the Chapter 16 result of job %s (mean drift %s vs %s); the relaxed design is provisional until the Chapter 16 analysis is re-run on it" % (rel.get("linear_target"), rel.get("nlrha_job"), rel.get("nlrha_mean_drift"), rel.get("nlrha_limit"))) if rel else ("not applicable (Risk Category IV keeps the 12.12.1 limits)" if rc == "IV" else "not applied -- the 12.12.1 limits govern the linear design"), "cfg.py drift_relief_16_1_2"],
         ["Capacity design", "; ".join("%s: %s" % (k, _readable(v, 160)) for k, v in cd.items() if k in ("SCWB", "panel_zone", "redundancy")) or "[per AISC 341]", "calc_package.json capacity_design"]])

    s = Section("8. Independent design review (16.5)"); S.append(s)
    s.p(ch16["design_review"]["rule"] + ". The reviewer's scope, agreed before the analysis:")
    for lead, txt in REVIEW_SCOPE:
        s.b(txt, lead + " --")
    s.p("Submittal to the reviewer: this document; the HR Steel package (report.html, cfg.py, design/calc_package.json, member_schedule.csv, model_opensees.py); the pushover supplement and hinge_params_used.json; nlrha/gm_scaling.json (or the suite table above); after the analysis, nlrha/nlrha_report.html and nlrha_package.json with per-record results; the four-analyses sheet; the retrieval log of the standards consulted.")

    s = Section("9. Open items and deviations"); S.append(s)
    deg = u["deg"]
    if deg and deg.get("demonstrated"):
        deg_item = None                                              # nothing open: the run models it (section 5)
    elif deg:
        deg_item = ("16.3.1 strength / stiffness degradation is NOT demonstrated by the model for: %s -- the engineer of record must justify this (response not sufficient to produce it) or model it."
                    % ", ".join(str(i.get("component")) for i in deg.get("items") or [] if not i.get("ok")))
    else:
        deg_item = "16.3.1 strength / stiffness degradation: %s -- confirm from the NLRHA's own statement once it has run." % (
            "the NLRHA on file does not record it" if u["nl"] else "the NLRHA has not been run")
    nlc = (g["nl"] or {}).get("no_live_case") or {}
    nlc_txt = (("On file: the no-live case is %s%s." % ("required" if nlc.get("required") else "not required", (" and %s" % ("was run" if nlc.get("run") else "was NOT run")) if nlc.get("required") else ""))
               if nlc else ("On file: %s." % ("the NLRHA does not record the no-live case" if g["nl"] else "the NLRHA has not been run")))
    items = [deg_item,
             (("Component parameters not supplied/verified by the user: %s. Check them against the printed AISC 342-22 / ASCE 41-23 tables and re-issue." % unv_txt)
              if not prm_ok else "Component backbones were verified against %s." % ((prm.get("source") or "")[:160])),
             ("Ground motions are ranked against the site disaggregation; the tectonic regime and any pulse content rest on the library's metadata -- confirm against the project hazard report." if (gm and gm.get("deagg")) else "Ground-motion selection uses spectral-shape fit to the code spectrum; 16.2.2 consistency with the site's controlling M, R and tectonic regime needs the project hazard (run `nlrha hazard`)."),
             "Accidental torsion is not applied by the analysis; 16.3.4 requires it where a Type 1 horizontal irregularity exists -- the engineer of record confirms there is none. Gravity follows the framed floor plate (floors at L0, roofs at Lr, quarter-bay tributaries); the 1.0 D analysis without live load is required unless the 16.3.2 exception (sum 0.5L <= 25% sum D and L0 < 100 psf over >= 75% of the area) applies -- the verdict is withheld (INCOMPLETE) if it is required and was not run. " + nlc_txt,
             "The force-controlled column check (16.4.2.1, both equations) combines axial force with concurrent flexure (AISC 360 H1-1 = AISC 342 C3-9; a deformation-controlled moment, m = 1 and M_CE, only with compression per AISC 342-22 C3.4a.2b) using AISC 360 E3 / F2-F6 with the F_y of the component parameters, K = 1, L_b = column length, C_b = 1; connections, splices and base plates are checked from the linear package, not from the nonlinear demands."
             + (" Where 16.4.2.1 Exception 2 applies (column axial force limited by the yield mechanism), the column is accepted when it meets either the default equations or Eqs. (16.4-3) / (16.4-4) (an exception relaxes the requirement; the lower D/C governs and the report states which), with E_mc from the mechanism statics of the column line (model capacities, never below the analysed suite maximum), applied moments neglected as AISC 341-22 D1.4a(b) permits; the engineer of record and the reviewer must accept this interpretation (user decision 7 Oct 2026, review item NL-R2-16)." if x2c.get("apply", True) else ""),
             "Foundations, soil-structure interaction and vertical ground motion (16.1.3) are not modelled.",
             "[Site class and V_s30 from the geotechnical report; project-specific performance objectives beyond the code minimum, if any.]"]
    if rel:
        items.append("The design carries the 16.1.2 drift relief; issue only after the Chapter 16 analysis of the relieved design passes 16.4.")
    for it in items:
        if it:
            s.b(it)

    if g["nl"]:
        s = Section("10. Results on file (for the reviewer)"); S.append(s)
        v, l = g["nl"]["verdict"], g["nl"]["limits"]
        _pc = lambda x: ("%.2f%%" % (100 * x)) if isinstance(x, (int, float)) and x == x else "not computed"
        s.t([["Check", "Result"],
             ["Unacceptable responses", "%d of %d (allowed %d)" % (v["n_unacceptable"], v["n_records"], v["unacceptable_allowed"])],
             ["Mean transient story drift", "max %s vs %s" % (_pc(v.get("mean_drift_max")), _pc(l["mean_limit"]))],
             ["Deformation-controlled", "CP %s, valid range %s" % ("ok" if v["deformation_ok"] else "EXCEEDED", "ok" if v["valid_range_ok"] else "EXCEEDED")],
             ["Force-controlled columns", "%s (worst D/C %s)" % ("ok" if v["force_controlled_ok"] else "NG", v.get("worst_FC_DC"))
              + ((" -- 16.4.2.1 Exception 2 used for: %s; default check without it: worst D/C %s" % ("; ".join((v.get("FC_exception_2") or {}).get("members") or []), v.get("worst_FC_DC_default")))
                 if (v.get("FC_exception_2") or {}).get("used") else (" -- 16.4.2.1 Exception 2 not used" if v.get("FC_exception_2") is not None else ""))],
             ["Overall", v.get("status") or ("ACCEPTABLE" if v["overall"] else "NOT ACCEPTABLE")]])
        s.n("Generated %s by the Nonlinear module; the report nlrha/nlrha_report.html carries the per-record and per-group tables." % g["nl"].get("generated"))

    s = Section("Appendix A. Chapter 16 rules implemented by the analysis tool", 1); S.append(s)
    rows = [["Clause", "pdf page", "Rule as implemented"]]
    for k, v in ch16.items():
        if isinstance(v, dict) and v.get("clause"):
            rows.append([v["clause"], str(v.get("pdf_page", "")), (v.get("rule") or v.get("note") or "")[:400]])
            x = v.get("exception_2")
            if isinstance(x, dict) and x.get("clause"):                  # NL-R2-16
                rows.append([x["clause"], str(x.get("pdf_page", "")), ("%s -- %s" % (x.get("rule") or "", "applied" if x.get("apply", True) else "NOT applied"))[:600]])
    s.t(rows)
    s = Section("Appendix B. Standards retrieval log", 1); S.append(s)
    if g["retrieval"]:
        s.p("The analyst's retrieval log (every plan sent to the Query file manager, every excerpt used, every `found: false`):"); s.c(g["retrieval"][:20000])
    else:
        s.p("[No retrieval_log.md in the job folder. The analyst bots keep one per the SNL skill; attach it here so the reviewer can see which clauses and tables were read, and which could not be found.]")
    s = Section("Appendix C. Sources of the numbers in this document", 1); S.append(s)
    for src in ("cfg.py and design/calc_package.json of the HR Steel package (system, hazard parameters, R / C_d / Omega_0 / I_e, drift limit, capacity design)",
                "nlrha/ch16_params.json (clauses, pdf pages and the numeric rules the tool implements)",
                "%s (component models)" % (os.path.basename(g["prm_path"]) if g["prm_path"] else "repository template pushover/hinge_params.json (UNVERIFIED)"),
                ("nlrha/nlrha_package.json model_stats / degradation_16_3_1 and pushover/pushover_package.json hinge_stats (what the analyses built)"
                 if (u["nl"] or u["po"]) else "no analysis output on file yet: section 5 states the parameter file, not a run"),
                "nlrha/site_hazard.json (USGS design maps and disaggregation, conditional spectra, near-fault screen)" if hz else "no site hazard file",
                "nlrha/gm_scaling.json / nlrha/nlrha_package.json (selected suite, scale factors, orientation)" if gm else "no suite selected yet"):
        s.b(src)
    return S


# --------------------------------------------------------------------------- outputs
def write(job, out=None, project=None, engineer=None, reviewer=None, params_path=None, risk_category=None):
    g = gather(job, params_path, risk_category)
    S = content(g, project, engineer, reviewer)
    out = out or g["job"]
    os.makedirs(out, exist_ok=True)
    title = "Design criteria for nonlinear response history analysis (ASCE 7-22 §16.1.4)"
    sub = "%s · Risk Category %s · DRAFT generated %s by Steltic_nonlinear for the engineer of record and the §16.5 reviewer" % (g["name"], g["rc"].replace("_", "/"), datetime.date.today().isoformat())
    d = DW.Doc(title=title, subtitle=sub, footer="%s -- design criteria §16.1.4 (draft)" % g["name"])
    for s in S:
        d.heading(s.title, s.level)
        for kind, a, b_ in s.items:
            if kind == "p":
                d.para(a, style=b_)
            elif kind == "b":
                d.bullet(a, bold_lead=b_)
            elif kind == "t":
                d.table(a, header=True)
            elif kind == "n":
                d.note(a)
            elif kind == "c":
                d.code(a)
    docx = d.save(os.path.join(out, "design_criteria_16_1_4.docx"))
    # html twin for the hub
    from . import report as RP
    parts = ["<style>%s</style><title>%s -- design criteria 16.1.4</title>" % (RP.CSS, H.escape(g["name"])), "<h1>%s</h1><div class='sub'>%s</div>" % (H.escape(title), H.escape(sub)),
             "<div class='note'><b>Draft.</b> Generated from the package files; bracketed items are for the engineer of record. Not for construction; the design must be sealed by a licensed professional engineer after the 16.5 review.</div>"]
    for s in S:
        parts.append("<h%d>%s</h%d>" % (min(3, s.level + 1), H.escape(s.title), min(3, s.level + 1)))
        ul = False
        for kind, a, b_ in s.items:
            if kind == "b":
                if not ul:
                    parts.append("<ul>"); ul = True
                parts.append("<li>%s%s</li>" % (("<b>%s</b> " % H.escape(b_)) if b_ else "", H.escape(a)))
                continue
            if ul:
                parts.append("</ul>"); ul = False
            if kind == "p":
                parts.append("<p%s>%s</p>" % (" class='note'" if b_ == "Note" else "", H.escape(a)))
            elif kind == "n":
                parts.append("<p class='note'>%s</p>" % H.escape(a))
            elif kind == "c":
                parts.append("<pre>%s</pre>" % H.escape(a))
            elif kind == "t":
                parts.append("<table>" + "".join("<tr>" + "".join(("<th>%s</th>" if i == 0 else "<td>%s</td>") % H.escape(str(c)) for c in row) + "</tr>" for i, row in enumerate(a)) + "</table>")
        if ul:
            parts.append("</ul>")
    html_path = os.path.join(out, "design_criteria_16_1_4.html")
    open(html_path, "w", encoding="utf-8").write("\n".join(parts))
    return docx, html_path
