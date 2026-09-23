"""Public samples: a workup's Property File published on purpose, with everything private to the license removed."""
import importlib
import json
import re
import sys
from pathlib import Path

import pytest

from test_p6_resolver import roll  # noqa: F401
from test_p7_workup import stubs, _resolved, _run  # noqa: F401
from test_p8_manual_research import DEED

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ps(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    m = importlib.import_module("publish_sample")
    monkeypatch.setattr(m, "SAMPLES", str(tmp_path / "samples"))
    return m


def test_sample_keeps_public_facts_and_drops_everything_private(roll, stubs, ps):
    from hunter import research, store
    r = _resolved()
    w = _run(r["search_id"])
    pid = r["identity"]["property_id"]
    mr = research.for_property(pid)
    deed = next(t for t in mr["tasks"] if t["question_key"] == "deed")
    research.complete(deed["task_id"], {"result": "FOUND", "source": "TEST RECORD — Clerk", "fields": DEED, "document": {"doc_type": "DEED", "title": "TEST RECORD deed"}}, actor="Topher")
    out = ps.publish(w["workup_id"], "lincoln-sample", "test note")
    d = json.load(open(out["path"]))
    blob = json.dumps(d)
    # public facts stay: parcel, roll owner, flood, road, State Lands check, City checks, tax semantics, unknowns, ledger, next actions
    assert d["identity"]["parcel_id"] == "300-06307-000" and d["identity"]["county"] == "Garland" and d["identity"]["verified_by"] == "AUTOMATED_SOURCE"
    fields = {i["field"] for i in d["what_we_know"]}
    assert {"parcel_id", "owner_name", "flood_zone", "road_access"} <= fields
    assert d["tax"]["tax_state"] in ("CURRENT_BILL_OPEN", "SOURCE_UNAVAILABLE", "UNKNOWN") and d["listing"]["sale_state"] == "UNKNOWN" and d["checks_performed"] and d["next_actions"] and d["evidence"]
    # private things are gone: mailing address value, manual research, documents, outreach, license/search/task ids, actor
    for bad in ("PO BOX 77", "owner_mailing_address", "TEST GRANTEE", "TEST-2026", "TEST RECORD deed", "manual:", "research_task", "task_id", "license_id", "PH-", "session", "search_id", "Topher", "refresh_token"):
        assert bad not in blob, bad
    assert d["outreach_gate"] is None and d["manual_research"] is None and d["links"]["investigation"] is None and d["identity"].get("search_id") is None
    own = d["sections"]["OWNER_MAILING"]
    assert own["attempt"]["status"] == "PRIVATE" and all(i["field"] == "owner_name" for i in own["items"]) and next(q for q in own["questions"] if q["key"] == "mailing_address")["state"] == "PRIVATE"
    assert next(a for a in d["checks_performed"] if a["domain"] == "OWNER_MAILING")["status"] == "PRIVATE" and not any(a["question"] == "mailing_address" for a in d["next_actions"])
    # a person's answers are theirs: the deed question shows UNKNOWN publicly, with no answer text
    dq = next(q for s in d["sections"].values() for q in s["questions"] if q["key"] == "deed")
    assert dq["state"] == "UNKNOWN" and "private" in dq["answer"] and dq["refs"] == []
    assert all(e["cls"] not in ("MANUAL VERIFICATION", "RESEARCH TASK COMPLETED", "OUTREACH GATE RE-EVALUATED", "EVIDENCE ADDED") for e in d["timeline"])
    # index and idempotent republish
    idx = json.load(open(Path(out["path"]).parent / "index.json"))
    assert [e["slug"] for e in idx["samples"]] == ["lincoln-sample"] and idx["samples"][0]["parcel_id"] == "300-06307-000"
    ps.publish(w["workup_id"], "lincoln-sample", "test note")
    assert len(json.load(open(Path(out["path"]).parent / "index.json"))["samples"]) == 1
    ps.remove("lincoln-sample")
    assert not Path(out["path"]).exists()


def test_sample_pages_are_static_and_publisher_is_opt_in():
    for name in ("sample.html", "samples.html"):
        t = (ROOT / "docs" / name).read_text()
        assert "PH.apiFetch" not in t and "ph-auth.js" not in t and "8234" not in t and "fetch('http" not in t
    assert "pf-render.js" in (ROOT / "docs" / "property-file.html").read_text() and "pf-render.js" in (ROOT / "docs" / "sample.html").read_text()
    pub = (ROOT / "tools" / "publish_sample.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+(requests|urllib|httpx|subprocess)\b", pub, re.M)
    # the publish loop never stages samples on its own; a person publishes each one
    assert "docs/samples" not in (ROOT / "tools" / "publish_scan.py").read_text() and "publish_sample" not in (ROOT / "tools" / "build_share.py").read_text()


def test_feedback_page_is_static_and_routes_to_one_inbox():
    t = (ROOT / "docs" / "feedback.html").read_text()
    assert 'action="https://formsubmit.co/topher@mixedmakershop.com"' in t and 'name="_honey"' in t and 'name="_subject"' in t
    assert "PH.apiFetch" not in t and "ph-auth.js" not in t and "8234" not in t
    for page in ("samples.html", "sample.html", "get-a-file.html", "index.html"):
        assert "feedback.html" in (ROOT / "docs" / page).read_text(), page
