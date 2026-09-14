"""The public site's shared logic never turns a missing record into a claim.

Runs the JavaScript unit tests in tests/ui/ph.test.js under node (skipped if node
is absent), and checks the exporter's vocabulary directly.
"""
import json, os, shutil, subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_site_logic_under_node():
    r = subprocess.run(["node", str(ROOT / "tests" / "ui" / "ph.test.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "checks passed" in r.stdout


def test_exporter_never_writes_excluded_rows_or_fake_zeroes(tmp_path, monkeypatch):
    from hunter import store
    from tests.conftest import make_record
    import tools.build_share as bs
    store.ingest(make_record(parcel_id="300-1", address="1 Keep St", city="HOT SPRINGS", lat=34.51, lon=-93.05, total_value=None, land_value=None, imp_value=None))
    store.ingest(make_record(parcel_id="300-2", address="2 Village Way", city="HOT SPRINGS VILLAGE", subdivision="HSV LAGO VISTA", lat=34.66, lon=-93.03))
    rows, labels = bs.export_rows()
    addrs = {r["a"] for r in rows}
    assert "1 Keep St" in addrs and "2 Village Way" not in addrs        # the Village never exports
    keep = next(r for r in rows if r["a"] == "1 Keep St")
    assert keep["tv"] is None and keep["lien"] == 0 and keep["tax"] is None   # missing stays missing
    assert keep["ts"] is None
