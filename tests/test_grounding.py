"""Provenance of the component parameters: verified / grounded / unverified.

The bug these pin: `snl revise` looked the clauses up, recorded them, and then told the engineer to
re-run the analyses to "pick the citation up". Every report asked one boolean (`verified`), which
Revise deliberately does not set -- so the red UNVERIFIED banner came back unchanged. Re-running made
it worse: pushover/cli.py copies the input parameters over hinge_params_used.json, erasing the
annotation. The loop had no exit.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from snl import grounding as G            # noqa: E402

PRM = {"_README": "notes", "verified": False, "beam_flexure": {"a": 0.02, "b": 0.05},
       "column_flexure": {"a": 0.01}, "brace_axial": {"c": 0.4}}
EV = {"asked": "2026-09-25T16:08:30",
      "groups": {"beam_flexure": {"grounded": True, "document": "A342", "citation": "[AISC 342-22 §C5.4b, p. 78]",
                                  "section": "C5.4b", "page": "78"},
                 "column_flexure": {"grounded": True, "citation": "[AISC 342-22 §C3.4b, p. 50]"},
                 "brace_axial": {"grounded": True, "citation": "[AISC 342-22 §C3.4a, p. 34]"}},
      "clauses": {"16.4.1.2": {"grounded": True, "citation": "[ASCE 7-22 §16.4.1.2, p. 189]"}}}


def _job(tmp, ev=EV, prm=PRM, fingerprint=True):
    job = tmp / "job"; (job / "pushover").mkdir(parents=True)
    (job / "pushover" / "hinge_params_used.json").write_text(json.dumps(prm), encoding="utf-8")
    if ev is not None:
        e = dict(ev)
        if fingerprint:
            e["params"] = {"path": "pushover/hinge_params_used.json", "fingerprint": G.fingerprint(prm)}
        (job / G.EVIDENCE).write_text(json.dumps(e), encoding="utf-8")
    return job


def test_the_three_states(tmp_path):
    job = _job(tmp_path)
    assert G.state(PRM, str(job))[0] == G.GROUNDED                       # Revise ran
    assert G.state(dict(PRM, verified=True), str(job))[0] == G.VERIFIED  # an engineer signed it
    assert G.state(PRM, str(tmp_path / "nothing"))[0] == G.UNVERIFIED    # nobody looked


def test_it_is_found_from_the_output_folder_too(tmp_path):
    """The supplements render from pushover/ and nlrha/; the record lives at the job root."""
    job = _job(tmp_path)
    assert G.state(PRM, str(job / "pushover"))[0] == G.GROUNDED
    assert G.job_root(str(job / "pushover")) == str(job)


def test_the_fingerprint_ignores_provenance_so_a_re_run_keeps_the_grounding(tmp_path):
    """The run copies the parameters back in without `grounding`/`source`. Same numbers, same hash."""
    annotated = dict(PRM, grounding={"asked": "..."}, source="cited from the corpus ...")
    assert G.fingerprint(annotated) == G.fingerprint(PRM)
    job = _job(tmp_path)
    assert G.state(PRM, str(job))[0] == G.GROUNDED          # the run's fresh copy still matches


def test_grounding_recorded_against_other_parameters_does_not_count(tmp_path):
    """Otherwise a stale record would 'ground' backbones the search never saw."""
    job = _job(tmp_path)
    other = dict(PRM, beam_flexure={"a": 0.09, "b": 0.11})   # someone supplied different backbones
    assert G.fingerprint(other) != G.fingerprint(PRM)
    assert G.state(other, str(job))[0] == G.UNVERIFIED


def test_evidence_with_nothing_grounded_is_not_grounding(tmp_path):
    job = _job(tmp_path, ev={"asked": "x", "groups": {"beam_flexure": {"grounded": False}}, "clauses": {}})
    assert G.state(PRM, str(job))[0] == G.UNVERIFIED


def test_a_damaged_evidence_file_never_raises(tmp_path):
    job = _job(tmp_path); (job / G.EVIDENCE).write_text("{not json", encoding="utf-8")
    assert G.state(PRM, str(job)) == (G.UNVERIFIED, None)


# ---------------------------------------------------------------- what the reports end up saying
LEGACY_PUSH = ('<h1>x</h1><div class="banner">UNVERIFIED MODELLING PARAMETERS — hinge_params.json has '
               'verified=false. Source note: placeholder</div><div class="note">Not for construction.</div>')
LEGACY_NL = ('<h1>x</h1><div class="banner">UNVERIFIED COMPONENT PARAMETERS — backbones are placeholders. '
             'Cyclic deterioration is OFF in this prototype although 16.3.1 requires it unless shown not to '
             'govern.</div><div class="note">Not for construction.</div>')


def test_the_grounded_block_says_what_was_and_was_not_established():
    html = G.block_html(G.GROUNDED, EV, PRM)
    assert "C5.4b" in html and "p. 78" in html                    # the citation is in the document
    assert "NOT read out of that" in html and "verified" in html  # and so is what it does not claim
    assert 'data-provenance="grounded"' in html
    assert "UNVERIFIED" not in html


def test_patching_a_supplement_replaces_only_the_provenance_block(tmp_path):
    p = tmp_path / "r.html"; p.write_text(LEGACY_PUSH, encoding="utf-8")
    assert G.patch(str(p), G.GROUNDED, EV, PRM) == G.PATCHED
    out = p.read_text(encoding="utf-8")
    assert "UNVERIFIED" not in out and "C5.4b" in out
    assert "<h1>x</h1>" in out and "Not for construction." in out  # nothing else touched


def test_patching_keeps_a_disclosure_that_was_sharing_the_banner(tmp_path):
    """The NLRHA banner also carried the cyclic-deterioration deviation. That is not provenance."""
    p = tmp_path / "n.html"; p.write_text(LEGACY_NL, encoding="utf-8")
    assert G.patch(str(p), G.GROUNDED, EV, PRM) == G.PATCHED
    out = p.read_text(encoding="utf-8")
    assert "Cyclic deterioration is OFF" in out and "16.3.1" in out
    assert "UNVERIFIED" not in out


def test_patching_twice_is_not_an_error(tmp_path):
    p = tmp_path / "r.html"; p.write_text(LEGACY_PUSH, encoding="utf-8")
    assert G.patch(str(p), G.GROUNDED, EV, PRM) == G.PATCHED
    assert G.patch(str(p), G.GROUNDED, EV, PRM) == G.UNCHANGED     # not ABSENT: it already says this
    assert p.read_text(encoding="utf-8").count("data-provenance") == 1


def test_a_report_with_no_provenance_block_says_so(tmp_path):
    p = tmp_path / "r.html"; p.write_text("<h1>x</h1><p>nothing here</p>", encoding="utf-8")
    assert G.patch(str(p), G.GROUNDED, EV, PRM) == G.ABSENT
    assert G.patch(str(tmp_path / "missing.html"), G.GROUNDED, EV, PRM) == G.ABSENT


def test_going_back_to_unverified_is_possible(tmp_path):
    """A parameter file swapped for one nothing was retrieved for must lose the citation."""
    p = tmp_path / "r.html"; p.write_text(LEGACY_PUSH, encoding="utf-8")
    G.patch(str(p), G.GROUNDED, EV, PRM)
    assert G.patch(str(p), G.UNVERIFIED, None, PRM) == G.PATCHED
    assert "UNVERIFIED MODELLING PARAMETERS" in p.read_text(encoding="utf-8")


def test_the_one_line_summary_distinguishes_all_three():
    assert G.summary(G.UNVERIFIED, None, PRM) == "UNVERIFIED placeholders"
    assert "retrieved" in G.summary(G.GROUNDED, EV, PRM) and "not read from it" in G.summary(G.GROUNDED, EV, PRM)
    assert G.summary(G.VERIFIED, None, dict(PRM, source="Table C3.6 checked")).startswith("verified ·")


def test_the_analysis_run_re_applies_the_grounding_it_used_to_erase(tmp_path):
    """pushover/cli.py copies the input parameters over hinge_params_used.json at the end of a run."""
    from pushover.cli import _copy_params
    job = _job(tmp_path)
    src = tmp_path / "input_params.json"
    src.write_text(json.dumps(PRM), encoding="utf-8")            # the plain file the run was given
    dst = job / "pushover" / "hinge_params_used.json"
    _copy_params(str(src), str(dst))
    out = json.loads(dst.read_text(encoding="utf-8"))
    assert out["grounding"]["groups"]["beam_flexure"]["citation"] == "[AISC 342-22 §C5.4b, p. 78]"
    assert out["verified"] is False                               # re-applying a citation verifies nothing
    assert G.state(out, str(job))[0] == G.GROUNDED


def test_a_run_with_different_parameters_does_not_inherit_the_citation(tmp_path):
    from pushover.cli import _copy_params
    job = _job(tmp_path)
    src = tmp_path / "other.json"
    src.write_text(json.dumps(dict(PRM, beam_flexure={"a": 0.09})), encoding="utf-8")
    dst = job / "pushover" / "hinge_params_used.json"
    _copy_params(str(src), str(dst))
    out = json.loads(dst.read_text(encoding="utf-8"))
    assert "grounding" not in out and G.state(out, str(job))[0] == G.UNVERIFIED
