"""Test fixtures. Every test runs against a throwaway database."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="ph-test-")
os.environ["PH_DATA_DIR"] = _tmp
os.environ["PH_DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["PH_AI_ENABLED"] = "0"          # tests never depend on a model
os.environ["PH_ENV"] = "development"          # P5.5: feature tests run with the gate off; the license tests turn it on explicitly
os.environ["PH_LICENSE_ENFORCED"] = "0"


@pytest.fixture(autouse=True)
def no_state_layer(monkeypatch):
    """The RPID -> parcel join (State layer camakey) is live-only; offline tests get no hits."""
    from hunter.sources import hot_springs
    monkeypatch.setattr(hot_springs, "STATE_QUERY", lambda *a, **k: {"features": []})


@pytest.fixture(autouse=True)
def clean_db():
    from hunter import db, exclusions
    db.init_db()
    conn = db.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS parcel_lookup (cell TEXT PRIMARY KEY, parcel_id TEXT, "
                 "owner_name TEXT, total_value REAL, land_value REAL, imp_value REAL, legal TEXT, "
                 "parcel_type TEXT, mailing TEXT, looked_up_at TEXT)")
    for t in ("parcel_lookup", "properties", "property_aliases", "evidence", "conflicts", "snapshots",
              "changes", "timeline", "scores", "watchlist", "alerts", "tasks",
              "notes", "decisions", "investigations", "logs", "scans", "address_searches", "documents", "research_tasks"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    exclusions.refresh_cache()
    yield
    exclusions.refresh_cache()


@pytest.fixture
def boundaries():
    """Insert the real exclusion polygons without hitting the network."""
    import json
    from hunter import db, exclusions
    from hunter.db import jdump, utcnow
    fixture = Path(__file__).parent / "fixtures" / "boundaries.json"
    data = json.loads(fixture.read_text())
    for key, b in data.items():
        db.ex("INSERT INTO geographies(kind,key,name,state,county_fips,geoid,"
              "boundary_json,bbox_json,source,retrieved_at) "
              "VALUES('exclusion',?,?,'AR','05051',?,?,?,'fixture',?) "
              "ON CONFLICT(kind,key) DO UPDATE SET boundary_json=excluded.boundary_json",
              (key, b["name"], b["geoid"], jdump({"rings": b["rings"]}),
               jdump(b["bbox"]), utcnow()))
    exclusions.refresh_cache()
    return data


def make_record(**over):
    """A minimal but realistic parcel record."""
    from hunter.sources.base import Record
    fields = {
        "county_fips": "05051", "territory": "garland_ar",
        "parcel_id": "300-06186-000", "address": "111 Isabelle St",
        "city": "HOT SPRINGS",
        "lat": 34.5100, "lon": -93.0500, "subdivision": "UNPLATTED HOT SPRINGS",
        "legal": "PT NE SE", "acreage": 2.154, "owner_name": "TUCKER ACQUISITIONS LLC",
        "parcel_type": "RI", "property_type": "house", "improved": 1,
        "land_value": 23200.0, "imp_value": 1050.0, "total_value": 24250.0,
    }
    fields.update(over)
    return Record(source="test_source", identity=dict(fields), fields=fields,
                  evidence=[{"field": "owner_name", "value": fields.get("owner_name"),
                             "evidence_type": "FACT", "confidence": "HIGH",
                             "source": "test_source", "effective_date": "2026-01-01"}],
                  raw=dict(fields))
