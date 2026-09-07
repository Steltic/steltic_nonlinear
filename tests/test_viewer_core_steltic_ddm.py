"""Smoke test of the shared viewer core (Steltic viewer bundle): a 2-storey portal renders to a self-contained html."""
import os, tempfile
from steltic_ddm import viewer_core as VC


def test_render_minimal():
    nodes = {1: (0, 0, 0), 2: (240, 0, 0), 3: (0, 0, 144), 4: (240, 0, 144), 99999: (120, 0, 144)}
    els = [(1, "col", "W14X90", 1, 3), (2, "col", "W14X90", 2, 4), (3, "beam", "W24X62", 3, 4)]
    model = VC.model_dict(nodes, els, fixed=[1, 2], diaphragms=[(3, 99999, [3, 4])], support_label="fixed")
    assert model["levels"] == [0.0, 144.0] and model["masters"]["1"] == 99999 and len(model["elements"]) == 3
    out = os.path.join(tempfile.mkdtemp(), "v.html")
    VC.render(out, "t", "pushover", dict(title="t"), model, dict(x=1), "<div id='a'></div>", "<div id='b'></div>",
              "function init(){}", amp=5)
    s = open(out, encoding="utf-8").read()
    assert "THREE" in s and '"x":1' in s and "__DATA__" not in s and "__MODULE_JS__" not in s and len(s) > 500_000


def test_bundle_links_and_hub():
    root = tempfile.mkdtemp(); sub = os.path.join(root, "pushover"); os.makedirs(sub)
    links = VC.bundle_links(sub, root)
    assert links["steltic"] == "../viewer_3d.html" and links["nlrha"] == "../nlrha/nlrha_viewer_3d.html" and links["hub"] == "../steltic_viewer_bundle.html"
    assert VC.bundle_links(root, root)["ddm"] == "ddm_viewer_3d.html"
    hub = VC.write_hub(root, "T")
    s = open(hub, encoding="utf-8").read()
    assert os.path.basename(hub) == "steltic_viewer_bundle.html" and "pushover/pushover_viewer_3d.html" in s and "T · Steltic viewer bundle" in s
