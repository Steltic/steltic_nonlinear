"""The example packages' exported models run on their own, as their header says (`python model_opensees.py`).

HR's exporter used to write the engine's probe build followed by the real build, with the geomTransf registrations
only in the first: after the second wipe() the script stopped at its first element. The examples now hold the final
build only (what steltic#68 writes), which is the build the parsers read anyway.
"""
import os, re, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pushover import package_reader as PR        # noqa: E402
from steltic_ddm import ingest as I              # noqa: E402

CASES = [(ex, f) for ex in ("Ex22_SMF", "Ex18_R3") for f in ("model_opensees.py", "model_static.py")]


@pytest.mark.parametrize("ex,script", CASES)
def test_example_model_runs_standalone_and_parses_the_same(ex, script, tmp_path):
    p = ROOT / "examples" / ex / script
    src = p.read_text()
    assert src.count("ops.wipe()") == 1
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, cwd=str(tmp_path), timeout=900)
    assert r.returncode == 0, (r.stderr or r.stdout)[-2000:]
    n_nodes, n_el = (int(x) for x in re.search(r"nodes:\s*(\d+)\s+elements:\s*(\d+)", r.stdout).groups())
    nodes, fixes, masses, transf, elems, diaph = I.parse_replay(str(p))
    m = PR.parse_model_script(p)
    assert len(nodes) == len(m.nodes) == n_nodes and len(elems) == len(m.elements) == n_el
    assert set(m.transfs) == {int(e[10]) for e in elems if e[0] == "elasticBeamColumn"}
    if script == "model_opensees.py":
        T = [float(x) for x in re.findall(r"[\d.]+", r.stdout.split("periods T (s):")[1])]
        rep = [float(x) for x in re.findall(r"T = ([\d.]+) s", (ROOT / "examples" / ex / "report.html").read_text(errors="replace"))[:2]]
        assert rep and all(abs(a - b) <= 0.006 for a, b in zip(T, rep))      # the report's modal periods (2 decimals)
