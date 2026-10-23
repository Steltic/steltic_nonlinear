"""flattened.py -- read the flattened equation cells of a converted table WITHOUT a model.

The PDF converter keeps a table cell that held an equation as its symbols, its operators and its digits,
in the order the PDF's text layer prints them, with every decimal point moved to a run of dots at the end:

    a = 5.5 (h/tw)^-0.95 (L/ry)^-0.5 (1 - PG/Pye)^2.4 <= 0.07
      -> "a h t L r P P w y G ye = ( )( ) - ( ) <= - - 5 5 1 0 07 0 95 0 5 2 4 . . . . ."

The numbers come out in a fixed order: first the ones on the baseline, left to right (the coefficient, the 1
of (1 - PG/Pye), the cap, the 2 of 2tf, a constant), then the raised ones, left to right (the exponents).
A decimal prints as two digit groups ("0 95"), an integer as one ("20"), and the dot run counts the decimals.

A model asked to undo this reasons about it for pages and can still get it wrong -- the column_flexure line 2
cell of AISC 342-22 Table C3.6 is where live runs either stalled ("Let me parse print order with symbols ...")
or handed back a sum of every symbol that evaluates negative. The STRUCTURE of each cell is not in doubt,
only its numbers, so the program reads them: for each cell a template names the numbers in print order and
the literals the cell must hold (the 1, the 2, the -1 exponent of a reciprocal). Every way of grouping the
digits into those numbers that uses exactly the printed dots is tried; the cell is read only when exactly ONE
grouping fits, and the symbols and the operator signs must match the template too. Anything else -- another
converter, a damaged cell, two readings that both fit -- returns a problem and Collect falls back to the model.

Checked against the printed page: the line 1 and line 2 readings below are the values read off AISC 342-22
p. 46 (pdf 78) in the round-2 verification of the parameter library (a = 1.2 (1 - PG/Pye)^1.2 (1.4 L/ry +
0.1 h/tw + 0.9 bf/2tf)^-1 - 0.0023 >= 0 ...), and the tests pin them.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- the cell templates
# slots: a name is a number read from the digits; a quoted literal ("'1'") is a digit group the cell must
# hold at that position. `expr` is written with the names. `minus` / `plus`: the operator signs the cell
# prints between "=" and its digits (exponent signs included). `symbols`: letters its variable list holds.
_C36_FLEX = {"symbols": set("htLrP"), "minus": 3, "plus": 0,
             "slots": ["C", "'1'", "cap", "p_htw", "p_Lry", "p_P"],
             "expr": "{C}*(h/tw)**-{p_htw}*(L/ry)**-{p_Lry}*(1-PG/Pye)**{p_P}",
             "bound": "max"}                                     # the "<= 0.07" in the same cell
_C36_LINE2 = {"symbols": set("PLrhtbf"), "minus": 3, "plus": 2,
              "slots": ["C", "'1'", "k_Lry", "k_htw", "k_bf", "'2'", "k0", "e_P", "'1'"],
              "expr": "{C}*(1-PG/Pye)**{e_P}*({k_Lry}*L/ry+{k_htw}*h/tw+{k_bf}*bf/(2*tf))**-1-{k0}",
              "bound": "min"}                                    # the "≥ 0" after the digits
_C36_C = {"symbols": set("P"), "minus": 1, "plus": 0, "slots": ["C1", "C2"], "expr": "{C1}-{C2}*PG/Pye"}

# AISC 342-22 Table C3.6, columns and braces in compression: the line's label, then its a / b / c cells
C36 = {
    "highly_ductile": {"label": "1. Highly ductile", "a": _C36_FLEX, "b": _C36_FLEX, "c": _C36_C},
    "non_moderately_ductile": {"label": "2. Non-moderately ductile", "a": _C36_LINE2, "b": _C36_LINE2, "c": _C36_C},
}

_GLYPHS = set("⎛⎝⎜⎞⎠⎟()[]{}")
_MINUS = {"-", "−", "–"}
_DIGITS = re.compile(r"^\d+$")


class Unreadable(ValueError):
    """The cell does not have the structure its template describes (or reads more than one way)."""


# ---------------------------------------------------------------- the table row
def row_cells(text: str, label: str) -> list | None:
    """The cells of the first markdown table row whose first cell starts with `label`, or None."""
    want = re.sub(r"\s+", " ", label).strip().lower()
    for line in (text or "").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and re.sub(r"\s+", " ", cells[0]).lower().startswith(want):
            return cells
    return None


def segments(cell: str) -> dict:
    """Split an equation cell into its a / b / c parts -> {var: (quote, symbols, body tokens)}.

    A part starts at a lone a / b / c followed by its symbol letters and "="; its body runs to the next part.
    The symbol list may itself hold a 'b' (bf), which is why a part starts only where letters run into '='."""
    toks = cell.split()
    starts = []
    i = 0
    while i < len(toks):
        if toks[i] in ("a", "b", "c"):
            j = i + 1
            while j < len(toks) and re.fullmatch(r"[A-Za-z]{1,2}", toks[j]):
                j += 1
            if j < len(toks) and toks[j] == "=" and (not starts or i > starts[-1][2]):
                starts.append((toks[i], i, j))
                i = j + 1
                continue
        i += 1
    out = {}
    for k, (var, s, eq) in enumerate(starts):
        end = starts[k + 1][1] if k + 1 < len(starts) else len(toks)
        if var in out:
            continue                                             # the first a / b / c of the line wins
        out[var] = (" ".join(toks[s:end]), set("".join(toks[s + 1:eq])), toks[eq + 1:end])
    return out


def _groupings(digits: list, slots: list, n_dots: int) -> list:
    """Every way to read `digits` as the template's slots using exactly `n_dots` decimals."""
    res = []

    def walk(i, k, dots, acc):
        if k == len(slots):
            if i == len(digits) and dots == n_dots:
                res.append(dict(acc))
            return
        s = slots[k]
        if i >= len(digits):
            return
        if s.startswith("'"):
            if digits[i] == s.strip("'"):
                walk(i + 1, k + 1, dots, acc)
            return
        one = digits[i]
        if one == "0" or not one.startswith("0"):                # an integer: no leading zero
            if int(one) != 0:                                    # a printed coefficient / exponent / cap is never 0
                acc[s] = one
                walk(i + 1, k + 1, dots, acc)
                del acc[s]
        if i + 1 < len(digits) and dots < n_dots and (one == "0" or not one.startswith("0")):
            v = one + "." + digits[i + 1]
            if float(v) != 0:
                acc[s] = v
                walk(i + 2, k + 1, dots + 1, acc)
                del acc[s]
    walk(0, 0, 0, {})
    return res


def read_cell(var: str, part: tuple, tpl: dict) -> dict:
    """One a / b / c part -> {"expr", "bound" (cap or floor, or None), "quote"}. Raises Unreadable."""
    quote, symbols, body = part
    if not tpl["symbols"] <= symbols:
        raise Unreadable("%s: the symbols %s are not those of the table (%s)" % (var, "".join(sorted(symbols)), "".join(sorted(tpl["symbols"]))))
    # the digit run is the last run of digit groups before the dots; what follows the dots is a bound (">= 0")
    try:
        d0 = next(i for i, t in enumerate(body) if _DIGITS.match(t))
    except StopIteration:
        raise Unreadable("%s: no digits in the cell" % var)
    ops = body[:d0]
    rest = body[d0:]
    digits = []
    while rest and _DIGITS.match(rest[0]):
        digits.append(rest.pop(0))
    n_dots = 0
    while rest and rest[0] == ".":
        n_dots += 1
        rest.pop(0)
    tail = rest
    minus = sum(1 for t in ops if t in _MINUS)
    plus = sum(1 for t in ops if t == "+")
    if minus != tpl["minus"] or plus != tpl["plus"]:
        raise Unreadable("%s: %d minus / %d plus signs where the table form has %d / %d" % (var, minus, plus, tpl["minus"], tpl["plus"]))
    if any(t not in _GLYPHS and t not in _MINUS and t not in ("+", "≤", "≥", "<=", ">=") for t in ops):
        raise Unreadable("%s: unexpected text before the digits (%s)" % (var, " ".join(t for t in ops if t not in _GLYPHS)))
    found = _groupings(digits, tpl["slots"], n_dots)
    if len(found) != 1:
        raise Unreadable("%s: the digits '%s' with %d decimal point(s) read %s" % (
            var, " ".join(digits), n_dots, "no way that fits the table form" if not found else "%d ways -- ambiguous" % len(found)))
    g = found[0]
    bound = None
    if tpl.get("bound") == "max":
        if "≤" not in ops and "<=" not in ops:
            raise Unreadable("%s: no '≤' cap in the cell" % var)
        bound = float(g["cap"])
    elif tpl.get("bound") == "min":
        if len(tail) < 2 or tail[0] not in ("≥", ">=") or not _DIGITS.match(tail[1]):
            raise Unreadable("%s: no '≥ 0' floor after the digits" % var)
        bound = float(tail[1])
    elif tail:
        raise Unreadable("%s: unexpected text after the digits (%s)" % (var, " ".join(tail)))
    return {"expr": tpl["expr"].format(**g), "bound": bound, "quote": quote, "numbers": g}


_FRAC = re.compile(r"^(\d+(?:\.\d+)?)\s*([ab])$")


def read_fraction(cell: str, of: str) -> float:
    """An IO / LS / CP cell: '0.5 a' -> 0.5 (of a), a bare 'b' -> 1.0 (of b)."""
    c = re.sub(r"\s+", " ", cell or "").strip()
    if c == of:
        return 1.0
    m = _FRAC.match(c)
    if not m or m.group(2) != of:
        raise Unreadable("the cell %r is not a fraction of %s" % (c, of))
    return float(m.group(1))


# ---------------------------------------------------------------- Table C3.6 -> collect fields
def read_c36(passages: list) -> tuple[dict, dict]:
    """AISC 342-22 Table C3.6, lines 1 and 2, from the passages' text.

    -> (fields {dotted collect field: {"value", "quote"}}, problems {line: why}). Line 1 is read whole or not
    at all; line 2 likewise (its absence is not a problem for the caller, see collect.OPTIONAL_FIELDS)."""
    text = "\n".join(p.get("text") or "" for p in passages)
    fields: dict = {}
    problems: dict = {}
    for line, spec in C36.items():
        cells = row_cells(text, spec["label"])
        if not cells:
            problems[line] = "the row '%s' is not in the passages" % spec["label"]
            continue
        try:
            if len(cells) < 4:
                raise Unreadable("the row has %d cells, not 4 (a/b/c, IO, LS, CP)" % len(cells))
            parts = segments(cells[0])
            missing = [v for v in ("a", "b", "c") if v not in parts]
            if missing:
                raise Unreadable("no %s cell in the row" % " / ".join(missing))
            got = {}
            for var in ("a", "b", "c"):
                r = read_cell(var, parts[var], spec[var])
                got[var + "_expr"] = (r["expr"], r["quote"])
                if r["bound"] is not None:
                    key = var + ("_max" if spec[var].get("bound") == "max" else "_min")
                    got[key] = (r["bound"], r["quote"])
            got["IO_frac_of_a"] = (read_fraction(cells[1], "a"), cells[1])
            got["LS_frac_of_b"] = (read_fraction(cells[2], "b"), cells[2])
            got["CP_frac_of_b"] = (read_fraction(cells[3], "b"), cells[3])
        except Unreadable as e:
            problems[line] = str(e)
            continue
        for k, (v, q) in got.items():
            fields["rows.%s.%s" % (line, k)] = {"value": v, "quote": q}
    return fields, problems
