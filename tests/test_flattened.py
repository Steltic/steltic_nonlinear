"""snl/flattened.py -- the program reads AISC 342-22 Table C3.6's flattened equation cells itself.

The cells below are exactly as the PDF converter leaves them (tests/fixtures/collect_tables.json, from the
corpus on a user's PC). The expected readings are the ones read off the printed page (AISC 342-22 p. 46,
pdf 78) in the round-2 verification of the parameter library."""
import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from snl import flattened as F          # noqa: E402

FX = json.load(open(os.path.join(ROOT, "tests", "fixtures", "collect_tables.json"), encoding="utf-8"))
FULL = FX["C3.6"] + "\n" + FX["C3.6_line2_row"]


def _ev(expr, **kw):
    env = dict(h=30.0, tw=0.5, L=168.0, ry=4.0, PG=100.0, Pye=1000.0, bf=16.0, tf=1.2)
    env.update(kw)
    return eval(expr, {"__builtins__": {}}, env)


def test_both_lines_read_as_printed():
    f, p = F.read_c36([{"text": FULL}])
    assert p == {}
    v = {k: x["value"] for k, x in f.items()}
    assert v["rows.highly_ductile.a_expr"] == "5.5*(h/tw)**-0.95*(L/ry)**-0.5*(1-PG/Pye)**2.4"
    assert v["rows.highly_ductile.b_expr"] == "20*(h/tw)**-0.9*(L/ry)**-0.5*(1-PG/Pye)**3.4"
    assert v["rows.highly_ductile.c_expr"] == "0.4-0.4*PG/Pye"
    assert v["rows.highly_ductile.a_max"] == v["rows.highly_ductile.b_max"] == 0.07
    assert (v["rows.highly_ductile.IO_frac_of_a"], v["rows.highly_ductile.LS_frac_of_b"], v["rows.highly_ductile.CP_frac_of_b"]) == (0.5, 0.75, 1.0)
    assert v["rows.non_moderately_ductile.a_expr"] == "1.2*(1-PG/Pye)**1.2*(1.4*L/ry+0.1*h/tw+0.9*bf/(2*tf))**-1-0.0023"
    assert v["rows.non_moderately_ductile.b_expr"] == "2.5*(1-PG/Pye)**1.8*(0.1*L/ry+0.2*h/tw+2.7*bf/(2*tf))**-1-0.0097"
    assert v["rows.non_moderately_ductile.c_expr"] == "0.5-0.5*PG/Pye"
    assert v["rows.non_moderately_ductile.a_min"] == v["rows.non_moderately_ductile.b_min"] == 0.0
    # every quote is the cell text itself
    for k, x in f.items():
        assert " ".join(x["quote"].split()) in " ".join(FULL.split()), k


@pytest.mark.parametrize("PG", [0.0, 50.0, 200.0, 400.0])
def test_line_2_evaluates_like_the_printed_equation(PG):
    f, _ = F.read_c36([{"text": FULL}])
    a = _ev(f["rows.non_moderately_ductile.a_expr"]["value"], PG=PG)
    b = _ev(f["rows.non_moderately_ductile.b_expr"]["value"], PG=PG)
    r = PG / 1000.0
    k = 168.0 / 4.0; hw = 30.0 / 0.5; bt = 16.0 / 2.4
    assert a == pytest.approx(1.2 * (1 - r) ** 1.2 / (1.4 * k + 0.1 * hw + 0.9 * bt) - 0.0023)
    assert b == pytest.approx(2.5 * (1 - r) ** 1.8 / (0.1 * k + 0.2 * hw + 2.7 * bt) - 0.0097)
    assert 0 < a < b


def test_line_1_alone_reads_and_line_2_is_reported_missing():
    f, p = F.read_c36([{"text": FX["C3.6"]}])
    assert "rows.highly_ductile.a_expr" in f and not any(k.startswith("rows.non_moderately") for k in f)
    assert "non_moderately_ductile" in p and "not in the passages" in p["non_moderately_ductile"]


def test_digits_that_fit_no_reading_are_refused():
    bad = FX["C3.6"].replace("c P P G ye = - 0 4 0 4 . .", "c P P G ye = - 0 4 0 4 7 . .")
    f, p = F.read_c36([{"text": bad}])
    assert not any(k.startswith("rows.highly_ductile") for k in f), "line 1 is read whole or not at all"
    assert "no way that fits" in p["highly_ductile"]


def test_a_cell_of_another_form_is_refused():
    # an extra sign: not the structure of the printed equation
    bad = FX["C3.6"].replace("≤ - - 5 5 1 0 07", "≤ - - - 5 5 1 0 07")
    _, p = F.read_c36([{"text": bad}])
    assert "minus" in p["highly_ductile"]


def test_two_readings_that_both_fit_are_refused():
    # a template with two adjacent free numbers and one dot: '1 2 3' reads 1.2|3 or 1|2.3
    tpl = {"symbols": set("P"), "minus": 1, "plus": 0, "slots": ["C1", "C2"], "expr": "{C1}-{C2}*PG/Pye"}
    part = ("c P P G ye = - 1 2 3 .", set("PGye"), "- 1 2 3 .".split())
    with pytest.raises(F.Unreadable, match="ambiguous"):
        F.read_cell("c", part, tpl)


def test_fraction_cells():
    assert F.read_fraction("0.5 a", "a") == 0.5 and F.read_fraction("b", "b") == 1.0 and F.read_fraction("0.75  b", "b") == 0.75
    with pytest.raises(F.Unreadable):
        F.read_fraction("0.5 a", "b")
