"""The retrieval policy (steltic_grokbot skills/Skill_querying_PACKAGED.md) as the tool applies it.

HR Steel and CFS put every standards call into policy form before it goes on the wire: ONE document,
an EXACT id when the provision is known, full text only to NAVIGATE to an id. The review was the one
agent that did not -- it sent whatever sentence the model typed and leaned on the escalation ladder
to recover, which costs two round trips and reports "not found" for provisions the corpus holds under
an id nobody asked for. These tests pin the policy, not the ladder.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from snl import rag, review                 # noqa: E402


def test_an_exact_type_is_sent_as_the_id_alone():
    p = rag.policy_plan("16.4.1.2", qtype="exact_section")
    assert p["exact"] == [("exact_section", "16.4.1.2")] and not p["nav"]
    p = rag.policy_plan("16.4-1", qtype="exact_equation")
    assert p["exact"] == [("exact_equation", "16.4-1")]
    p = rag.policy_plan("C3.6", qtype="exact_table")
    assert p["exact"] == [("exact_table", "C3.6")] and "exact_table C3.6" in p["label"]


def test_an_id_buried_in_a_sentence_is_asked_for_as_an_id_first():
    """The failure this exists to prevent: a nine-word query at a corpus indexed by id."""
    p = rag.policy_plan("ASCE 7-22 section 16.4.1.2 mean story drift ratio limit")
    assert ("id", "16.4.1.2") in p["exact"]                 # the id goes first...
    assert "mean story drift ratio limit" in p["nav"]       # ...and the words are kept to navigate with
    assert "7-22" not in p["nav"]                           # the document name is not a search term
    assert p["label"].startswith("exact-id 16.4.1.2")


def test_a_bare_id_typed_into_query_is_not_full_text_searched():
    for q in ("16.4.1.2", "12.12-1", "C5.4a", "E3.4a", "F2-1"):
        p = rag.policy_plan(q)
        assert p["exact"] and p["exact"][0][1] == q and not p["nav"], q


def test_a_clause_given_separately_is_still_asked_for_first():
    p = rag.policy_plan("unacceptable response", clause="16.4.1.1")
    assert p["exact"][0] == ("id", "16.4.1.1")
    assert p["nav"] == "unacceptable response"
    # and when the model chose navigation itself, an id it also gave is not thrown away
    p = rag.policy_plan("mean story drift", clause="16.4.1.2", qtype="fts")
    assert p["exact"] == [("id", "16.4.1.2")] and p["nav"] == "mean story drift"


def test_prose_with_no_id_navigates_and_asks_for_nothing_exact():
    p = rag.policy_plan("modelling parameters for steel columns in compression")
    assert p["exact"] == [] and p["nav"].startswith("modelling parameters")
    assert p["label"].startswith("fts")


def test_the_same_id_is_not_asked_for_twice():
    p = rag.policy_plan("section 16.4.1.2 and 16.4.1.2 again", clause="16.4.1.2")
    assert [i for _k, i in p["exact"]] == ["16.4.1.2"]


def test_the_review_tool_offers_the_policys_own_fields():
    """The model cannot follow the policy through a tool that has nowhere to put an exact type."""
    props = review.TOOLS[0]["function"]["parameters"]["properties"]
    for k in ("type", "document", "query", "clause", "chapter", "purpose", "want_commentary", "context_neighbors"):
        assert k in props, k
    assert set(props["type"]["enum"]) >= {"exact_section", "exact_equation", "exact_table", "fts"}
    assert props["top_k"]["maximum"] == 20                  # the server's cap, not an arbitrary 8
    assert "Skill_querying_PACKAGED.md" in review.SYSTEM    # the policy itself, not a paraphrase of it
    assert "ONE document per call" in review.SYSTEM


def test_the_review_is_not_capped_by_default():
    """`--max-searches 8` was a cost control that read as a quality target. Unlimited is the default."""
    import inspect
    assert inspect.signature(review.run).parameters["max_searches"].default == 0
    assert "no cap" in _help_for("--max-searches")
    assert "Search as often as the review needs" in review.SYSTEM


def _help_for(flag):
    import argparse, contextlib, io
    from snl import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
        cli.main(["review", "--help"])
    return buf.getvalue()

def test_an_exact_hit_is_passed_on_whole(monkeypatch):
    """Table C5.5 puts the RBS row 8,450 characters in and its footnote equations after 10,000; the
    old 2,500-character cap on every hit cut them off and Collect reported them absent. An exact lookup
    is one provision: it goes through whole. Navigation snippets stay short."""
    row = "| RBS | X 2 0 07 <= . | 0.028 | 0.2 | 0.5 a | 0.75 b | b |"
    table = "TABLE C5.5 " + ("| SMF filler row |" + " " * 60 + "\n") * 150 + row + "\n\nFOOTNOTE EQUATIONS printed with this table: X_2 = 0.55"
    assert table.index(row) > 2500
    sent = []

    def fake_post(payload, timeout):
        sent.append(payload)
        return {"results": [{"text": table, "doc": "AISC_342_22", "section_id": "C5.4a", "printed_label": "77"}]}, None

    monkeypatch.setattr(rag, "_post", fake_post)
    monkeypatch.setattr(rag, "url", lambda: "http://x/query")
    monkeypatch.setattr(rag, "status", lambda: {"known": True, "indexed_docs": ["AISC_342_22"]})
    res = rag.search("C5.5", "A342", qtype="exact_table", top_k=8, neighbors=2)
    assert res["ok"] and res["results"], res
    text = res["results"][0]["text"]
    assert row in text and "FOOTNOTE EQUATIONS" in text and "not shown" not in text
    assert sent[0].get("type") == "exact_table"
    # a navigation snippet is capped and says so
    long_snippet = "x" * (rag.FTS_MAX_CHARS + 500)
    monkeypatch.setattr(rag, "_post", lambda payload, timeout: ({"results": [{"text": long_snippet, "doc": "AISC_342_22"}]}, None))
    res = rag.search("reduced beam section modeling parameters", "A342", qtype="fts")
    t = res["results"][0]["text"]
    assert len(t) < len(long_snippet) and t.endswith("more characters not shown]")
    assert rag.EXACT_MAX_CHARS >= 30000                     # Table A3.2 of AISC 341 is 27,000 characters

