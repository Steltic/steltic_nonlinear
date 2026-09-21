"""`snl revise` — re-issuing the reports with the corpus behind them.

The analysis run queries nothing, so its reports mark every clause UNVERIFIED and call the
backbones placeholders. This step re-asks the corpus and replaces the placeholder wording with a
citation. The tests that matter are about what it refuses to claim.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from snl import revise                      # noqa: E402


def test_it_will_not_run_without_the_review():
    with pytest.raises(SystemExit) as e:
        revise.run("/nonexistent-project-xyz")
    assert "review.md" in str(e.value) and "Review tab" in str(e.value)


def test_an_empty_review_is_refused(tmp_path):
    (tmp_path / "review.md").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        revise.run(str(tmp_path))
    assert "empty" in str(e.value)


# --- the relevance gate: a citation to the wrong clause is worse than none -----------------------
def _res(*hits):
    return {"ok": True, "results": list(hits)}


def test_a_passage_must_be_the_table_and_name_the_component():
    gusset = {"section": "E2.4", "title": "Permissible Performance Parameters",
              "text": "The region of gusset boundary to the beam, column, and brace shall be modeled as rigid"}
    table = {"section": "C5.4b", "title": "Force-Controlled Actions", "page": 78,
             "text": "TABLE C5.5 Modeling Parameters and Permissible Deformations for Nonlinear Analysis Procedures - Beams"}
    # the gusset clause mentions 'beam' but is not a modelling-parameter table
    assert revise._relevant_hit(_res(gusset), ("beam",)) is None
    # the table is, and names it
    assert revise._relevant_hit(_res(gusset, table), ("beam",)) is table
    # ...but not for a component it does not mention
    assert revise._relevant_hit(_res(table), ("brace",)) is None
    # nothing at all from a failed search
    assert revise._relevant_hit({"ok": False, "results": [table]}, ("beam",)) is None


def test_the_citation_carries_the_document_clause_and_page():
    c = revise._cite({"section": "16.4.1.2", "page": 189}, "ASCE7")
    assert c == "[ASCE 7-22 §16.4.1.2, p. 189]"
    assert revise._cite({"section": "", "page": None}, "A342") == "[AISC 342-22]"


# --- what it writes back ------------------------------------------------------------------------
def test_grounding_is_recorded_but_verified_is_never_flipped(tmp_path):
    (tmp_path / "pushover").mkdir()
    prm = tmp_path / "pushover" / "hinge_params_used.json"
    prm.write_text(json.dumps({"verified": False, "source": "placeholder -- from memory",
                               "beam_flexure": {"basis": "x"}}), encoding="utf-8")
    ev = {"asked": "2026-09-21T10:00:00",
          "groups": {"beam_flexure": {"grounded": True, "document": "A342", "citation": "[AISC 342-22 §C5.4b, p. 78]",
                                      "section": "C5.4b", "page": 78, "passage": "TABLE C5.5 Modeling Parameters"}},
          "clauses": {"16.4.1.2": {"grounded": True, "citation": "[ASCE 7-22 §16.4.1.2, p. 189]"}}}
    out = revise.annotate_params(str(tmp_path), ev, log=lambda *a: None)
    assert out == str(prm)
    got = json.loads(prm.read_text(encoding="utf-8"))
    assert got["verified"] is False, "retrieving the table is not reconciling the numbers in it"
    assert got["grounding"]["groups"]["beam_flexure"]["citation"] == "[AISC 342-22 §C5.4b, p. 78]"
    assert got["grounding"]["clauses"]["16.4.1.2"] == "[ASCE 7-22 §16.4.1.2, p. 189]"
    assert "C5.4b" in got["source"] and "NOT been reconciled" in got["source"]
    assert got["beam_flexure"] == {"basis": "x"}, "nothing else in the file is touched"


def test_no_params_file_is_reported_not_crashed(tmp_path):
    msgs = []
    assert revise.annotate_params(str(tmp_path), {"groups": {}}, log=msgs.append) is None
    assert any("hinge_params_used.json" in m for m in msgs)


def test_every_group_names_a_real_document_and_a_component_word():
    for gid, what, words, tries in revise.GROUPS:
        assert words and all(w.islower() for w in words), gid
        for doc_key, query, chapter in tries:
            assert doc_key in revise.rag.DOCUMENTS, (gid, doc_key)
            assert query.strip() and chapter.strip()
    for cid, doc_key, query in revise.CLAUSES:
        assert doc_key in revise.rag.DOCUMENTS
