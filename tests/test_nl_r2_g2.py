"""Regression tests for the round-2 findings NL-R2-05, 06, 08, 10 and NL-R2-L1 (package reading, the 16.1.4 criteria
document, the feedback loops, BRB data, mesh-convergence dry run). Small synthetic packages only -- no analysis."""
import json, os, sys, tempfile
from pathlib import Path
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# --------------------------------------------------------------------------- NL-R2-06: heights from cfg VALUES
_CFG_COMMENT = '''"""Two storeys.  heights = [14, 12] ft (inter-level offsets) -- a COMMENT, never to be read."""
H1 = 14 * 12.0
cfg = dict(system="SMF", heights=[H1, 144.0], L_floor=50.0)
'''
_MODEL = """import openseespy.opensees as ops
ops.wipe()
ops.model('basic', '-ndm', 3, '-ndf', 6)
ops.node(1, 0.0, 0.0, 0.0)
ops.node(100001, 0.0, 0.0, 168.0)
ops.node(200001, 0.0, 0.0, 312.0)
ops.node(199999, 0.0, 0.0, 168.0)
ops.node(299999, 0.0, 0.0, 312.0)
ops.fix(1, 1, 1, 1, 1, 1, 1)
ops.mass(199999, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0)
ops.mass(299999, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0)
ops.rigidDiaphragm(3, 199999, 100001)
ops.rigidDiaphragm(3, 299999, 200001)
"""


def _job(cfg_text=_CFG_COMMENT, model=_MODEL):
    d = Path(tempfile.mkdtemp(prefix="nlr2g2_"))
    (d / "model_opensees.py").write_text(model)
    if cfg_text is not None:
        (d / "cfg.py").write_text(cfg_text)
    return d


def _engine_ok():
    try:
        from steltic_ddm.ingest import load_cfg
        d = _job("cfg = dict(heights=[1.0])\n")
        return load_cfg(str(d)).get("heights") == [1.0]
    except Exception:
        return False


@pytest.mark.skipif(not _engine_ok(), reason="the HR engine is not importable (STELTIC_ENGINE_DIR)")
def test_heights_come_from_executed_cfg_not_the_docstring():
    from pushover import package_reader as PR
    p = PR.load(_job())
    assert p.basis.heights_in == [168.0, 144.0]                      # not [14, 12] from the docstring
    assert "executed" in p.basis.sources["heights_in"]
    assert p.basis.L_floor_psf == 50.0


def test_heights_fall_back_to_node_elevations(monkeypatch):
    from pushover import package_reader as PR
    import steltic_ddm.ingest as ING
    def boom(*a, **k):
        raise ImportError("engine not importable")
    monkeypatch.setattr(ING, "load_cfg", boom)
    p = PR.load(_job())
    assert p.basis.heights_in == [168.0, 144.0]
    assert "diaphragm master elevations" in p.basis.sources["heights_in"] and "not executable" in p.basis.sources["heights_in"]
    p2 = PR.load(_job(cfg_text=None))                                # no cfg.py at all
    assert p2.basis.heights_in == [168.0, 144.0] and "no cfg.py" in p2.basis.sources["heights_in"]
