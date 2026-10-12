"""existing.py -- NL-R2-04 safeguard (user decision: WARNING ONLY, the analysis is unchanged).

A package can declare that (part of) the building is an EXISTING structure (Ex32: cfg['existing_structure'] =
{levels, year, grade, connections, ...}, cfg['materials'] with an 'existing_*' grade). Every engine here assesses with
NEW-building criteria; for such a package each engine prints EXISTING_WARNING and every report / the 16.1.4 document
carries it prominently. Clause ids (retrieved from the ASCE 41-23 corpus):
  BPON              ASCE 41-23 Section 2.4.4, Table 2-5     (what is used: performance levels from the Risk Category)
  BSE-2N / BSE-1N   ASCE 41-23 Sections 2.3.1.1 / 2.3.1.2   (MCER per ASCE 7 Section 11.4 / two-thirds of it)
  BPOE              ASCE 41-23 Section 2.4.1, Table 2-3     (what an existing-building evaluation uses)
  BSE-2E / BSE-1E   ASCE 41-23 Sections 2.3.1.3 / 2.3.1.4   (5 % / 20 % in 50 years)
  knowledge factor  ASCE 41-23 Section 6.2.3.1, Table 6-1; property bounding for nonlinear procedures Section 6.2.3.2
"""
from __future__ import annotations

CLAUSES = dict(BPON="ASCE 41-23 Section 2.4.4, Table 2-5", BSE2N="ASCE 41-23 Section 2.3.1.1", BSE1N="ASCE 41-23 Section 2.3.1.2",
               BPOE="ASCE 41-23 Section 2.4.1, Table 2-3", BSE2E="ASCE 41-23 Section 2.3.1.3", BSE1E="ASCE 41-23 Section 2.3.1.4",
               kappa="ASCE 41-23 Section 6.2.3.1, Table 6-1", bounding="ASCE 41-23 Section 6.2.3.2")


def existing_structure(root=None, cfg=None):
    """dict(declared=True, markers=[...], summary=str) when the package declares an existing structure, else None.
    Markers (from the EXECUTED cfg, pushover.cfg_values -- never the text): a truthy cfg['existing_structure'], or a
    cfg['materials'] entry whose key or spec names an existing material. No cfg -> None (nothing declared)."""
    if cfg is None and root is not None:
        from . import cfg_values as CV
        cfg = CV.executed_cfg(root)[0]
    if not isinstance(cfg, dict):
        return None
    markers, summary = [], ""
    ex = cfg.get("existing_structure")
    if ex:
        markers.append("cfg['existing_structure']")
        if isinstance(ex, dict):
            bits = []
            if ex.get("levels") or ex.get("stories"):
                lv = ex.get("stories") or ex.get("levels")
                bits.append("storeys %s" % (", ".join(str(x) for x in lv) if isinstance(lv, (list, tuple)) else lv))
            for k in ("year", "grade"):
                if ex.get(k):
                    bits.append(str(ex[k]))
            summary = " · ".join(bits)
    mats = cfg.get("materials")
    if isinstance(mats, dict):
        for k, v in mats.items():
            spec = str((v or {}).get("spec", "")) if isinstance(v, dict) else str(v)
            if "existing" in str(k).lower() or "existing" in spec.lower():
                markers.append("cfg['materials']['%s']" % k)
    if not markers:
        return None
    return dict(declared=True, markers=markers, summary=summary)


DDM_CRITERIA = ("the AISC 360-22 / ASCE 7-22 new-building load combinations and resistance factors (no BPON / BPOE "
                "performance level, no BSE-1E / BSE-2E hazard)")


def warning_text(info, engine="this analysis", criteria=None) -> str:
    """The one statement every engine prints and every report carries (plain text). `criteria` replaces the BPON / MCER
    clause for an engine that uses neither (the DDM: DDM_CRITERIA)."""
    if not info:
        return ""
    what = (" (%s)" % info["summary"]) if info.get("summary") else ""
    crit = criteria or ("BPON performance levels from the Risk Category (%s), the MCER / BSE-2N hazard (%s; BSE-1N = 2/3 "
                        "of it, %s)" % (CLAUSES["BPON"], CLAUSES["BSE2N"], CLAUSES["BSE1N"]))
    return ("EXISTING STRUCTURE DECLARED by the package (%s)%s: %s assessed the building with NEW-BUILDING criteria -- "
            "%s, ONE material grade for all members of a shape type (no existing-material properties), and no knowledge "
            "factor or property bounding (%s; %s). This is NOT an ASCE 41 evaluation of the existing building, which needs the BPOE "
            "(%s) at the BSE-1E / BSE-2E hazards (%s / %s), the existing materials and connection capacities from data "
            "collection (ASCE 41-23 Section 6.2), and the knowledge factor. The analysis itself is unchanged."
            % (", ".join(info["markers"]), what, engine, crit, CLAUSES["kappa"], CLAUSES["bounding"], CLAUSES["BPOE"],
               CLAUSES["BSE1E"], CLAUSES["BSE2E"]))


def banner_html(info, engine="this analysis", criteria=None) -> str:
    """A red banner for the HTML reports ('' when nothing is declared)."""
    if not info:
        return ""
    import html as _h
    return ('<div class="banner existing-structure" style="border:2px solid #b00020;background:#fdecea;color:#5f0010;'
            'padding:10px 12px;margin:10px 0;font-weight:600">%s</div>' % _h.escape(warning_text(info, engine, criteria)))


def print_warning(info, engine="this analysis", criteria=None):
    """Console warning (printed by every engine at start-up)."""
    if info:
        print("!! " + warning_text(info, engine, criteria), flush=True)
