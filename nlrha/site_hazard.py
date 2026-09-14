"""site_hazard.py -- site-specific hazard for the Chapter 16 suite: the USGS ASCE 7-22 multi-period MCE_R spectrum,
the USGS NSHM disaggregation at the conditioning period, a conditional (mean) spectrum target, a near-fault
screen, and a record library that grows beyond the shipped FEMA P-695 set (user folders of PEER .AT2 pairs or
two-column CSVs, indexed automatically). Everything here is written to <job>/nlrha/site_hazard.json with its
sources; nlrha run --target mcer|cs then selects and scales against it.

Web services (both public, no key):
    design  https://earthquake.usgs.gov/ws/designmaps/asce7-22.json?latitude=..&longitude=..&riskCategory=..&siteClass=..
            -> sds, sd1, sms, sm1, ss, s1, tl, multiPeriodMCErSpectrum, multiPeriodDesignSpectrum, ... (redirects to
               /ws/building-codes/asce7-22/calculate)
    disagg  https://earthquake.usgs.gov/ws/nshmp/<model>/dynamic/disagg/<lon>/<lat>/<vs30>/<returnPeriod>?imt=SA1P0
            -> mean (m, r, eps0) over all sources, modes, and the contributing sources with their distances

Conditional spectrum (Baker 2011): ln CMS(T) = mu(T) + rho(T, T*) eps(T*) sigma(T). With the UHS at each period
approximated by mu(T) + eps(T*) sigma(T) (a constant-epsilon reading of the deaggregation), the mean target follows
from the multi-period MCE_R spectrum alone:  CMS(T) = MCE_R(T) exp(sigma(T) eps (rho(T,T*) - 1)). The correlation
rho is Baker & Jayaram (2008); sigma_ln(T) is a documented NGA-West2-representative curve (SIGMA_MODEL) the user can
override. CMS(T*) equals the MCE_R at the conditioning period whatever sigma is, and the 16.2.3.2 rule (suite mean
>= 0.9 x target over the period range) is applied against the chosen target exactly as for the code spectrum.
"""
from __future__ import annotations
import csv, json, math, os, re, time, urllib.parse, urllib.request
import numpy as np

USGS_DESIGN = "https://earthquake.usgs.gov/ws/designmaps/asce7-22.json"
USGS_DISAGG = "https://earthquake.usgs.gov/ws/nshmp/{model}/dynamic/disagg/{lon}/{lat}/{vs30}/{rp}?imt={imt}"
NSHM_MODEL = "conus-2023.R2"
NSHM_IMTS = [0.01, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.5, 10.0]
SITE_VS30 = {"A": 1500, "B": 1080, "BC": 760, "C": 530, "CD": 365, "D": 260, "DE": 185, "E": 150}          # ASCE 7-22 Table 20.2-1 centre values
# NGA-West2-representative total ln standard deviation of Sa (M >= 6, active crust) -- an approximation the user may
# replace with a GMPE table; it enters the CMS only through sigma * eps * (rho - 1).
SIGMA_MODEL = dict(source="representative of BSSA14 / CB14 / CY14 total sigma for M >= 6 (approximate; override with --sigma)",
                   periods=[0.01, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0],
                   sigma=[0.60, 0.61, 0.64, 0.66, 0.68, 0.70, 0.72, 0.73, 0.74, 0.74])
NEAR_FAULT = dict(clause="ASCE 7-22 11.4.1 (near-fault site) / 16.2.2 (pulse-type motions in an appropriate proportion)",
                  km_M7=15.0, km_M6=10.0, min_contribution_pct=5.0)


# --------------------------------------------------------------------------- USGS
def _get_json(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "steltic-nonlinear/0.3 (Chapter 16 record selection)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_design(lat, lon, risk_category="II", site_class="D", title="Steltic"):
    """ASCE 7-22 design values and the multi-period spectra from the USGS service."""
    rc = {"I_II": "II"}.get(risk_category, risk_category or "II")
    q = urllib.parse.urlencode(dict(latitude=lat, longitude=lon, riskCategory=rc, siteClass=site_class, title=title))
    url = USGS_DESIGN + "?" + q
    raw = _get_json(url)
    data = (raw.get("response") or {}).get("data") or {}
    md = (raw.get("response") or {}).get("metadata") or {}
    if not data or data.get("sds") is None:
        raise RuntimeError("USGS design service returned no data for this site: %s" % json.dumps(raw)[:300])
    out = dict(url=url, fetched=time.strftime("%Y-%m-%dT%H:%M:%S"), risk_category=rc, site_class=site_class, vs30=md.get("vs30"),
               model_version=md.get("modelVersion"))
    for k in ("sds", "sd1", "sms", "sm1", "ss", "s1", "tl", "ts", "t0", "pgam", "sdc", "cv"):
        out[k] = data.get(k)
    for k in ("multiPeriodMCErSpectrum", "multiPeriodDesignSpectrum", "twoPeriodMCErSpectrum", "twoPeriodDesignSpectrum"):
        v = data.get(k) or {}
        out[k] = dict(periods=list(v.get("periods") or []), ordinates=list(v.get("ordinates") or []))
    return out


def imt_for(T):
    """The NSHM IMT label nearest a period: 1.0 -> SA1P0, 0.2 -> SA0P2, 0.075 -> SA0P075."""
    Tn = min(NSHM_IMTS, key=lambda t: abs(math.log(t) - math.log(max(T, 0.01))))
    s = ("%g" % Tn)
    if "." not in s:
        s += ".0"
    return "SA" + s.replace(".", "P"), Tn


def fetch_disagg(lat, lon, vs30, T_star, return_period=2475, model=NSHM_MODEL):
    """USGS NSHM disaggregation at the IMT nearest T*: mean/mode (m, r, eps0), the IML, and the contributing sources."""
    imt, Tn = imt_for(T_star)
    url = USGS_DISAGG.format(model=model, lon=lon, lat=lat, vs30=int(round(vs30)), rp=int(return_period), imt=imt)
    raw = _get_json(url, timeout=240)
    if raw.get("status") != "success":
        raise RuntimeError("USGS disaggregation failed: %s" % json.dumps(raw)[:300])
    dis = (raw.get("response") or {}).get("disaggs") or []
    if not dis:
        raise RuntimeError("USGS disaggregation returned no data")
    total = next((d for d in dis[0]["data"] if d.get("component") == "Total"), dis[0]["data"][0])
    out = dict(url=url, fetched=time.strftime("%Y-%m-%dT%H:%M:%S"), model=model, imt=imt, T_imt=Tn, T_star=T_star, return_period=return_period, vs30=vs30)
    for s in total.get("summary", []):
        name = s.get("name", ""); vals = {e["name"]: e.get("value") for e in s.get("data", [])}
        if name.startswith("Disaggregation targets"):
            out["iml_g"] = next((v for k, v in vals.items() if "ground motion" in k.lower()), None); out["exceedance_rate"] = vals.get("Exceedance rate")
        elif name.startswith("Mean"):
            out["mean"] = dict(M=vals.get("m"), R_km=vals.get("r"), eps=vals.get("ε₀", vals.get("ε0")))
        elif name.startswith("Mode (largest m-r bin)"):
            out["mode_mr"] = dict(M=vals.get("m"), R_km=vals.get("r"), eps=vals.get("ε₀", vals.get("ε0")), contribution_pct=vals.get("Contribution"))
        elif name.startswith("Mode (largest m-r-"):
            out["mode_mre"] = dict(M=vals.get("m"), R_km=vals.get("r"), eps=vals.get("ε₀", vals.get("ε0")), contribution_pct=vals.get("Contribution"))
    srcs = []
    for s in total.get("sources") or []:
        if s.get("type") == "SET":
            continue
        srcs.append(dict(name=s.get("name"), contribution_pct=s.get("contribution"), R_km=s.get("r"), M=s.get("m"), eps=s.get("ε", s.get("eps")), source=s.get("source")))
    out["sources"] = sorted(srcs, key=lambda x: -(x["contribution_pct"] or 0))[:25]
    return out


# --------------------------------------------------------------------------- conditional spectrum
def rho_baker_jayaram(T1, T2):
    """Baker & Jayaram (2008) correlation of epsilons at two periods (0.01-10 s)."""
    Tmin, Tmax = min(T1, T2), max(T1, T2)
    Tmin, Tmax = max(Tmin, 0.01), max(Tmax, 0.01)
    C1 = 1 - math.cos(math.pi / 2 - 0.366 * math.log(Tmax / max(Tmin, 0.109)))
    if Tmax < 0.2:
        C2 = 1 - 0.105 * (1 - 1 / (1 + math.exp(100 * Tmax - 5))) * ((Tmax - Tmin) / (Tmax - 0.0099))
    else:
        C2 = 0.0
    C3 = C2 if Tmax < 0.109 else C1
    C4 = C1 + 0.5 * (math.sqrt(C3) - C3) * (1 + math.cos(math.pi * Tmin / 0.109))
    if Tmax < 0.109:
        return C2
    if Tmin > 0.109:
        return C1
    if Tmax < 0.2:
        return min(C2, C4)
    return C4


def sigma_ln(T, model=None):
    m = model or SIGMA_MODEL
    return float(np.interp(np.log(max(T, 0.01)), np.log(m["periods"]), m["sigma"]))


def interp_spectrum(periods, sa, T):
    """Log-log interpolation of a spectrum (T = 0 handled as PGA); flat beyond the ends."""
    p = np.array([max(x, 0.01) for x in periods], float); s = np.array(sa, float)
    T = np.asarray(T, float); T = np.where(T < 0.01, 0.01, T)
    return np.exp(np.interp(np.log(T), np.log(p), np.log(np.maximum(s, 1e-6))))


def conditional_mean_spectrum(periods, uhs, T_star, eps, sigma_model=None):
    """CMS(T) = UHS(T) exp(sigma(T) eps (rho(T, T*) - 1)); also the conditional sigma sqrt(1 - rho^2) sigma."""
    out, sig_c = [], []
    for T, u in zip(periods, uhs):
        r = rho_baker_jayaram(T, T_star); s = sigma_ln(T, sigma_model)
        out.append(float(u) * math.exp(s * eps * (r - 1.0))); sig_c.append(s * math.sqrt(max(0.0, 1 - r * r)))
    return np.array(out), np.array(sig_c)


def near_fault_screen(deagg, rules=NEAR_FAULT):
    """Sources within the 11.4.1 distances contributing at least min_contribution_pct -> near-fault flag + recommended pulse share."""
    hits = []
    for s in (deagg or {}).get("sources", []):
        M, R, c = s.get("M") or 0, s.get("R_km"), s.get("contribution_pct") or 0
        if R is None:
            continue
        if ((M >= 7.0 and R <= rules["km_M7"]) or (M >= 6.0 and R <= rules["km_M6"])) and c >= rules["min_contribution_pct"]:
            hits.append(s)
    share = sum(h["contribution_pct"] for h in hits)
    return dict(near_fault=bool(hits), sources=hits, hazard_share_pct=round(share, 1), pulse_fraction=round(min(1.0, share / 100.0), 2),
                clause=rules["clause"], note="pulse-type records in the suite in about this proportion of the near-fault hazard share (16.2.2); the shipped P-695 far-field set has none -- add a pulse library")


def build_site_hazard(lat, lon, T1x, T1y, site_class="D", risk_category="II", vs30=None, return_period=2475, T_lower=None, T_upper=None,
                      conditioning_periods=None, sigma_model=None, design=None, deaggs=None, fetch=True):
    """The site_hazard.json content. design / deaggs may be supplied (offline); fetch=True calls the USGS services."""
    rc = risk_category
    if fetch and design is None:
        design = fetch_design(lat, lon, rc, site_class)
    if design is None:
        raise RuntimeError("no design-map data: fetch from the USGS service or supply --design-json")
    vs30 = vs30 or design.get("vs30") or SITE_VS30.get(site_class.upper(), 260)
    mp = design.get("multiPeriodMCErSpectrum") or {}
    periods = [float(t) for t in mp.get("periods") or []]; sa = [float(v) for v in mp.get("ordinates") or []]
    if not periods:
        raise RuntimeError("the design-map data carries no multi-period MCE_R spectrum")
    Tmax, Tmin = max(T1x, T1y), min(T1x, T1y)
    T_lower = T_lower or 0.2 * Tmin; T_upper = T_upper or 2.0 * Tmax
    cps = list(conditioning_periods or [Tmax])
    grid = np.geomspace(max(0.05, 0.5 * T_lower), max(1.2 * T_upper, 0.06), 60)
    mcer = interp_spectrum(periods, sa, grid)
    cs_list = []; deaggs = dict(deaggs or {})
    for Ts in cps:
        key = "%.3f" % Ts
        d = deaggs.get(key)
        if d is None and fetch:
            d = fetch_disagg(lat, lon, vs30, Ts, return_period); deaggs[key] = d
        if d is None:
            raise RuntimeError("no disaggregation for T* = %.3f s (fetch or supply --deagg-json)" % Ts)
        eps = ((d.get("mean") or {}).get("eps")) or 0.0
        cms, sigc = conditional_mean_spectrum(grid, mcer, Ts, eps, sigma_model)
        cs_list.append(dict(T_star=Ts, imt=d.get("imt"), T_imt=d.get("T_imt"), eps=eps, M=(d.get("mean") or {}).get("M"), R_km=(d.get("mean") or {}).get("R_km"),
                            iml_g=d.get("iml_g"), periods=grid.tolist(), sa=cms.tolist(), sigma_cond=sigc.tolist(), return_period=return_period))
    env = np.max([np.array(c["sa"]) for c in cs_list], axis=0) if cs_list else None
    inrange = (grid >= T_lower) & (grid <= T_upper)
    envelope = None
    if env is not None:
        ratio = env[inrange] / mcer[inrange]
        envelope = dict(min_ratio_to_mcer=float(ratio.min()), covered_share=float(np.mean(ratio >= 1.0 - 1e-6)), covered_90_share=float(np.mean(ratio >= 0.9 - 1e-6)),
                        note="16.2.1.2 (Method 2): the envelope of the conditional spectra is checked against the MCE_R over the period range; add conditioning periods (--cs-period) where it falls short. RE-VERIFY the clause wording through Query file manager.")
    nf = near_fault_screen(deaggs.get("%.3f" % cps[0]) if cps else None)
    return dict(schema=1, generated=time.strftime("%Y-%m-%dT%H:%M:%S"), site=dict(latitude=lat, longitude=lon, site_class=site_class, risk_category=rc, vs30=vs30),
                periods_of_building=dict(T1x=T1x, T1y=T1y, T_lower=T_lower, T_upper=T_upper), design=design, deagg=deaggs,
                targets=dict(mcer_multi_period=dict(periods=periods, sa=sa, clause="16.2.1.1 -> 11.4.5.1 multi-period MCE_R (USGS ASCE 7-22 service, maximum direction)"),
                             code_two_period=dict(SDS=design.get("sds"), SD1=design.get("sd1"), TL=design.get("tl"), clause="16.2.1.1 -> 11.4.6 (1.5 x the two-period design spectrum)"),
                             cs=cs_list, cs_clause="16.2.1.2 conditional (mean) spectra at the conditioning period(s); Baker (2011) form with Baker & Jayaram (2008) correlation"),
                envelope=envelope, near_fault=nf, sigma_model=sigma_model or SIGMA_MODEL,
                sources=["USGS ASCE 7-22 design web service (multi-period MCE_R and design spectra, TL, site class)",
                         "USGS NSHM %s disaggregation web service (mean M, R, epsilon at the conditioning period; contributing sources)" % NSHM_MODEL,
                         "Baker & Jayaram (2008) correlation of spectral acceleration values from NGA ground motion models, Earthquake Spectra 24(1)",
                         "Baker (2011) Conditional mean spectrum: tool for ground-motion selection, J. Struct. Eng. 137(3)"])


def target_from_hazard(hz, kind="mcer", cs_index=0):
    """(periods, sa, label) of the chosen target; kind: mcer | cs | code."""
    if kind == "cs":
        c = hz["targets"]["cs"][cs_index]
        return np.array(c["periods"]), np.array(c["sa"]), "conditional spectrum, T* = %.2f s (eps %.2f, M %.1f, R %.0f km; 16.2.1.2)" % (c["T_star"], c["eps"], c["M"] or 0, c["R_km"] or 0)
    if kind == "mcer":
        t = hz["targets"]["mcer_multi_period"]
        return np.array(t["periods"]), np.array(t["sa"]), "site-specific multi-period MCE_R (USGS; 16.2.1.1 / 11.4.5.1)"
    raise ValueError(kind)


# --------------------------------------------------------------------------- record libraries
def _pair_key(stem):
    """Strip the component marker from a file stem so the two horizontal components pair up."""
    s = stem
    for pat in (r"[-_]?(H1|H2|HN[EN]|HL[EN]|HHE|HHN|BHE|BHN|E|N|EW|NS|X|Y|T|L|FN|FP)$", r"(\d{3})$", r"[-_](UP|V|Z|HNZ|HLZ)$"):
        m = re.search(pat, s, flags=re.I)
        if m:
            return s[:m.start()].rstrip("-_"), m.group(1).upper()
    return s, ""


def _is_vertical(marker):
    return marker.upper() in ("UP", "V", "Z", "HNZ", "HLZ", "DN") or marker.upper().endswith("Z")


def read_csv_record(path):
    """Two-column time, acceleration (g) CSV (header optional); returns (dt, a)."""
    t, a = [], []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.reader(f):
            try:
                t.append(float(row[0])); a.append(float(row[1]))
            except (ValueError, IndexError):
                continue
    if len(t) < 3:
        raise ValueError("no numeric rows in %s" % path)
    dt = float(np.median(np.diff(t)))
    return dt, np.array(a, float)


def _search_results(folder):
    """PEER NGA-West2 _SearchResults.csv metadata (RSN -> M, Rrup, Vs30, mechanism, pulse, file names), if present."""
    meta = {}
    for name in os.listdir(folder):
        if not name.lower().endswith(".csv") or "search" not in name.lower():
            continue
        with open(os.path.join(folder, name), newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.reader(f))
        hdr_i = next((i for i, r in enumerate(rows) if any("Record Sequence Number" in c for c in r)), None)
        if hdr_i is None:
            continue
        hdr = [c.strip() for c in rows[hdr_i]]
        def col(*keys):
            for k in keys:
                for i, h in enumerate(hdr):
                    if k.lower() in h.lower():
                        return i
            return None
        ci = dict(rsn=col("Record Sequence Number"), eq=col("Earthquake Name"), yr=col("Year"), sta=col("Station Name"), M=col("Magnitude"),
                  mech=col("Mechanism"), rjb=col("Rjb"), rrup=col("Rrup"), vs30=col("Vs30"), lf=col("Lowest Useable"), h1=col("Horizontal-1"), h2=col("Horizontal-2"), pulse=col("Pulse"))
        for r in rows[hdr_i + 1:]:
            if ci["rsn"] is None or ci["rsn"] >= len(r) or not r[ci["rsn"]].strip().isdigit():
                continue
            g = lambda k: (r[ci[k]].strip() if ci[k] is not None and ci[k] < len(r) else "")
            def fl(k):
                try:
                    return float(g(k))
                except ValueError:
                    return None
            rec = dict(id=int(g("rsn")), earthquake=g("eq"), year=(int(fl("yr")) if fl("yr") else None), station=g("sta"), M=fl("M"), mechanism=g("mech"),
                       r_jb_km=fl("rjb"), r_rup_km=fl("rrup"), vs30=fl("vs30"), lowest_freq_hz=fl("lf"), comp1=g("h1"), comp2=g("h2"), pulse=(g("pulse").strip().lower() in ("1", "yes", "true", "pulse")))
            meta[rec["id"]] = rec
            for fn in (rec["comp1"], rec["comp2"]):
                if fn:
                    meta[fn] = rec
    return meta


def scan_folder(folder, write_index=True):
    """Build (and write) index.json for a user folder of .AT2 pairs / CSV pairs, using PEER _SearchResults.csv when present."""
    folder = os.path.abspath(folder)
    if os.path.exists(os.path.join(folder, "index.json")):
        return json.load(open(os.path.join(folder, "index.json"), encoding="utf-8"))
    meta = _search_results(folder)
    files = []
    for dirpath, _, names in os.walk(folder):
        for n in names:
            if n.lower().endswith((".at2", ".csv")) and "search" not in n.lower():
                files.append(os.path.relpath(os.path.join(dirpath, n), folder).replace(os.sep, "/"))
    groups = {}
    for rel in sorted(files):
        stem = os.path.splitext(os.path.basename(rel))[0]
        key, marker = _pair_key(stem)
        if _is_vertical(marker):
            continue
        groups.setdefault(os.path.join(os.path.dirname(rel), key), []).append(rel)
    records = []
    for key, comps in sorted(groups.items()):
        if len(comps) < 2:
            continue
        c1, c2 = comps[0], comps[1]
        m = meta.get(os.path.basename(c1)) or meta.get(os.path.basename(c2)) or {}
        rsn = re.search(r"RSN(\d+)", os.path.basename(c1), flags=re.I)
        rid = m.get("id") or (int(rsn.group(1)) if rsn else len(records) + 1)
        records.append(dict(id=rid, M=m.get("M"), year=m.get("year"), earthquake=m.get("earthquake") or os.path.basename(key), station=m.get("station") or "",
                            site_class=None, vs30=m.get("vs30"), mechanism=m.get("mechanism"), r_rup_km=m.get("r_rup_km"), r_jb_km=m.get("r_jb_km"),
                            nga_seq=m.get("id"), lowest_freq_hz=m.get("lowest_freq_hz"), comp1=c1, comp2=c2, pulse=bool(m.get("pulse")), pga_g=None, pgv_cms=None))
    idx = dict(set="user library %s (%d pairs, auto-indexed%s)" % (os.path.basename(folder), len(records), "; PEER _SearchResults.csv metadata" if meta else "; no metadata -- M and R unknown"),
               source=folder, records=records, auto_indexed=time.strftime("%Y-%m-%dT%H:%M:%S"))
    if write_index and records:
        json.dump(idx, open(os.path.join(folder, "index.json"), "w", encoding="utf-8"), indent=1)
    return idx


def library_from(dirs):
    """Merge several record sets (indexed folders, or user folders scanned on the fly); ids are made unique."""
    from . import ground_motions as GM
    recs, sets = [], []
    for d in dirs:
        if not os.path.exists(os.path.join(d, "index.json")):
            scan_folder(d)
        idx = json.load(open(os.path.join(d, "index.json"), encoding="utf-8"))
        n = 0
        for r in idx["records"]:
            try:
                p1, p2 = os.path.join(d, r["comp1"]), os.path.join(d, r["comp2"])
                dt1, a1 = (read_csv_record(p1) if p1.lower().endswith(".csv") else GM.read_at2(p1))
                dt2, a2 = (read_csv_record(p2) if p2.lower().endswith(".csv") else GM.read_at2(p2))
                if abs(dt1 - dt2) > 1e-9:
                    continue
                m = min(len(a1), len(a2))
                rec = dict(r, dt=dt1, a1=a1[:m], a2=a2[:m], npts=m, duration_s=m * dt1, set_dir=d)
                rec["id"] = "%s:%s" % (os.path.basename(d.rstrip("/\\")), r["id"]) if len(dirs) > 1 else r["id"]
                if rec.get("pga_g") is None:
                    rec["pga_g"] = float(max(np.abs(a1).max(), np.abs(a2).max()))
                recs.append(rec); n += 1
            except Exception as ex:                                        # noqa: BLE001
                print("[library] skipped %s: %s" % (r.get("comp1"), ex))
        sets.append(dict(dir=d, set=idx.get("set"), n=n))
    return sets, recs


# --------------------------------------------------------------------------- consistency ranking (16.2.2)
def consistency_penalty(rec, deagg, w_M=0.5, w_R=0.25):
    """Soft penalty for M / R distance from the disaggregation mean; 0 when unknown (never excludes a record)."""
    if not deagg:
        return 0.0
    mean = deagg.get("mean") or {}
    pen = 0.0
    if rec.get("M") is not None and mean.get("M") is not None:
        pen += w_M * abs(float(rec["M"]) - float(mean["M"]))
    if rec.get("r_rup_km") is not None and mean.get("R_km"):
        pen += w_R * abs(math.log(max(float(rec["r_rup_km"]), 1.0) / max(float(mean["R_km"]), 1.0)))
    return pen


def hazard_summary_html(hz):
    """A short HTML block for the reports / the design criteria document."""
    s = hz["site"]; d = hz["design"]; nf = hz.get("near_fault") or {}
    rows = ["<table><tr><th>Item</th><th>Value</th><th>Source</th></tr>",
            "<tr><td>Site</td><td>%.4f, %.4f · site class %s · V<sub>s30</sub> %s m/s · Risk Category %s</td><td>input</td></tr>" % (s["latitude"], s["longitude"], s["site_class"], s.get("vs30"), s["risk_category"]),
            "<tr><td>S<sub>S</sub> / S<sub>1</sub> · S<sub>MS</sub> / S<sub>M1</sub> · S<sub>DS</sub> / S<sub>D1</sub> (g)</td><td>%s / %s · %s / %s · %s / %s · T<sub>L</sub> %s s · SDC %s</td><td>USGS ASCE 7-22 service</td></tr>"
            % (d.get("ss"), d.get("s1"), d.get("sms"), d.get("sm1"), d.get("sds"), d.get("sd1"), d.get("tl"), d.get("sdc"))]
    for c in hz["targets"].get("cs", []):
        rows.append("<tr><td>Disaggregation at T* = %.2f s (%s, %d yr)</td><td>mean M %.2f · R %.1f km · ε %.2f</td><td>USGS NSHM %s</td></tr>"
                    % (c["T_star"], c["imt"], c["return_period"], c["M"] or 0, c["R_km"] or 0, c["eps"], NSHM_MODEL))
    if hz.get("envelope"):
        e = hz["envelope"]; rows.append("<tr><td>CS envelope vs MCE<sub>R</sub> over the period range</td><td>min ratio %.2f · at or above MCE<sub>R</sub> over %.0f%% of the range</td><td>16.2.1.2</td></tr>" % (e["min_ratio_to_mcer"], 100 * e["covered_share"]))
    rows.append("<tr><td>Near-fault screen</td><td>%s%s</td><td>%s</td></tr>" % ("NEAR-FAULT: " if nf.get("near_fault") else "not near-fault: ",
                ("%s (%.0f%% of the hazard) -> pulse share ≈ %.0f%%" % (", ".join("%s (M %.1f, %.1f km)" % (x["name"], x["M"], x["R_km"]) for x in nf.get("sources", [])[:4]), nf.get("hazard_share_pct", 0), 100 * nf.get("pulse_fraction", 0))) if nf.get("near_fault") else "no source with >= %g%% contribution inside the 11.4.1 distances" % NEAR_FAULT["min_contribution_pct"], nf.get("clause", "")))
    rows.append("</table>")
    return "".join(rows)
