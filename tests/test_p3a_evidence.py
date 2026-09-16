"""P3A evidence integrity: one canonical origin on every reading (AUTOMATED_SOURCE / MANUAL_VERIFICATION /
NOTE / DERIVED / AI_OPINION), distinct from verification (evidence_type) and confidence; a person's entry is
never an automated FACT/HIGH; notes never become evidence; precedence keeps every row; provenance survives
export, the UI and the model's view."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_record

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NO_INV = {"built_at": None, "listings": {}}
HUNT_NONE = {"sources": {}, "finished_at": None}


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


def _prop(parcel="800-1", county="05069", **over):
    from hunter import store
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, address=over.pop("address", f"{parcel} Origin St"), **over))
    return pid


def _ev(pid, **e):
    from hunter import store
    store.store_evidence(pid, [e])


# ------------------------------------------------------------------ 1-4, 8: classification is deterministic and separate from confidence

def test_origin_classification_and_confidence_are_distinct():
    from hunter import store
    a = _prop()
    _ev(a, field="cleanup_lien_amount", value="1500", evidence_type="FACT", confidence="HIGH", source="hs_gis_liens", source_name="City of Hot Springs")
    _ev(a, field="manual:title", value="deed index pulled", evidence_type="OBSERVATION", confidence="MEDIUM", source="manual_verification")
    _ev(a, field="field_observation", value="neighbour says empty", evidence_type="OBSERVATION", confidence="LOW", source="topher_field_note")
    _ev(a, field="acreage_from_geometry", value="0.21", evidence_type="CALCULATION", confidence="MEDIUM", source="ar_gis_parcels")
    _ev(a, field="signal:vacant_land", value="vacant land", evidence_type="OBSERVATION", confidence="HIGH", source="property_hunter.distress")
    _ev(a, field="vision:aerial:1", value="roof looks intact", evidence_type="AI_OPINION", confidence="LOW", source="local_vision_model")
    _ev(a, field="photo:street", value="own photo", evidence_type="OBSERVATION", confidence="HIGH", source="topher_photo")
    v = {e["field"]: e for e in store.evidence_view(a)}
    assert v["cleanup_lien_amount"]["origin"] == "AUTOMATED_SOURCE" and v["cleanup_lien_amount"]["confidence"] == "HIGH"
    assert v["manual:title"]["origin"] == "MANUAL_VERIFICATION" and v["manual:title"]["verification"] == "OBSERVATION" and v["manual:title"]["confidence"] == "MEDIUM"
    assert v["field_observation"]["origin"] == "NOTE"
    assert v["acreage_from_geometry"]["origin"] == "DERIVED" and v["signal:vacant_land"]["origin"] == "DERIVED", "a HIGH-confidence derived signal is still DERIVED"
    assert v["vision:aerial:1"]["origin"] == "AI_OPINION"
    assert v["photo:street"]["origin"] == "MANUAL_VERIFICATION" and v["photo:street"]["confidence"] == "HIGH", "confidence is the person's, origin stays manual"
    for e in v.values():
        assert e["origin_label"] == store.ORIGIN_LABEL[e["origin"]] and e["ref"].startswith("evidence:") and e["date"]
        assert e["origin"] in store.ORIGINS
    # the column is written at insert time, not only computed on read
    from hunter import db
    assert {r["origin"] for r in db.q("SELECT origin FROM evidence WHERE property_id=?", (a,))} <= set(store.ORIGINS)
    # an explicit origin is honoured only when it is a real one
    assert store.origin_of({"origin": "MANUAL_VERIFICATION", "source": "hs_gis_liens"}) == "MANUAL_VERIFICATION"
    assert store.origin_of({"origin": "TRUST_ME", "source": "hs_gis_liens"}) == "AUTOMATED_SOURCE"


# ------------------------------------------------------------------ 5, 6: task completion

def test_task_completion_is_manual_verification_never_automated_fact(client):
    from hunter import store, cases
    a = _prop("801-1")
    cid = cases.open_or_create(a, None)["investigation_id"]
    tid = store.add_task(a, {"title": "Check taxes at the Collector window", "source": "garland_tax_collector", "manual": 1, "source_url": "https://www.arkansastaxsearch.com/garland.html"})
    r = client.post(f"/api/task/{tid}", json={"status": "done", "evidence": "Clerk said 2024 and 2025 paid; receipt 8812", "actor": "topher"})
    assert r.status_code == 200
    ev = [e for e in store.evidence_view(a) if e["field"] == "manual:garland_tax_collector"]
    assert len(ev) == 1
    e = ev[0]
    assert e["origin"] == "MANUAL_VERIFICATION" and e["source"] == "manual_verification"
    assert e["evidence_type"] == "OBSERVATION" and e["confidence"] == "MEDIUM"
    assert e["source_name"].endswith("MANUAL VERIFICATION by topher") and e["raw_ref"] == f"MANUAL VERIFICATION; task:{tid}"
    assert e["effective_date"] and e["source_url"].startswith("https://")
    assert not [x for x in store.evidence_for(a) if x["evidence_type"] == "FACT" and x["source"] in store.MANUAL_SOURCES], "no manual FACT anywhere"
    c = cases.get_case(cid)
    kinds = [x["cls"] for x in c["events"]]
    assert "MANUAL VERIFICATION" in kinds and "EVIDENCE ADDED" in kinds and "ACTION COMPLETED" in kinds
    panel = next(x for x in c["evidence"] if x["ref"] == e["ref"])
    assert panel["verification"] == "MANUAL VERIFICATION" and panel["origin"] == "MANUAL_VERIFICATION"
    # the person's task answer does not by itself flip a case question: the question engine reads only its own fields
    assert {q["key"]: q["state"] for q in c["questions"]}["tax_state"] == "UNKNOWN"


# ------------------------------------------------------------------ 3: notes are notes

def test_notes_never_become_evidence(client):
    from hunter import store
    a = _prop("802-1")
    n0 = len(store.evidence_for(a))
    client.post(f"/api/property/{a}/note", json={"body": "Mailbox overflowing, grass three feet high."})
    assert len(store.evidence_for(a)) == n0
    notes = store.notes_for(a)
    assert notes and notes[-1]["confidence"] == "UNVERIFIED"
    # a historical note-evidence row (from before P3A) is classified NOTE and never answers a question
    _ev(a, field="field_observation", value="old style note row", evidence_type="OBSERVATION", confidence="LOW", source="topher_field_note")
    assert store.latest_answer(a, "field_observation") is None
    assert store.with_origin(store.latest_evidence(a, "field_observation"))["origin"] == "NOTE"


# ------------------------------------------------------------------ 9, 10, 11: three states obey origin

def test_questions_answered_only_by_answerable_origins():
    from hunter import cases, store
    a = _prop("803-1", "05069")
    cid = cases.open_or_create(a, {"event": "NEW_LIEN", "cls": "WORLD_EVENT", "evidence_ref": "evidence:1"})["investigation_id"]
    # a note, a derived signal and an AI opinion about road access answer nothing
    _ev(a, field="road_access", value="neighbour says there is a road", evidence_type="OBSERVATION", confidence="LOW", source="topher_field_note")
    _ev(a, field="road_access", value="model sees a road", evidence_type="AI_OPINION", confidence="LOW", source="local_vision_model")
    cases.refresh(cid, cp={}, hunt=HUNT_NONE, inv=NO_INV)
    assert {q["key"]: q["state"] for q in cases.get_case(cid)["questions"]}["access"] == "UNKNOWN"
    # an automated source reading answers FOUND
    _ev(a, field="road_access", value="fronts a county road (911 centerline within 15 m)", evidence_type="OBSERVATION", confidence="MEDIUM", source="ar_gis_roads", source_name="Arkansas GIS Office roads")
    cases.refresh(cid, cp={}, hunt=HUNT_NONE, inv=NO_INV)
    q = {x["key"]: x for x in cases.get_case(cid)["questions"]}["access"]
    assert q["state"] == "FOUND" and q["evidence_refs"] and q["source"] == "Arkansas GIS Office roads"
    # NOT_FOUND still requires a named, dated check; a note cannot produce it
    assert store.latest_answer(a, "cleanup_lien_amount") is None
    assert {x["key"]: x for x in cases.get_case(cid)["questions"]}["lien_city"]["state"] == "UNKNOWN"


# ------------------------------------------------------------------ 12, 13: history and conflicts are preserved

def test_precedence_supersedes_without_deleting_and_conflicts_stay():
    from hunter import store, db
    a = _prop("804-1")
    _ev(a, field="zoning", value="R-1", evidence_type="FACT", confidence="HIGH", source="hs_gis_zoning", effective_date="2026-01-01")
    _ev(a, field="zoning", value="R-1", evidence_type="OBSERVATION", confidence="MEDIUM", source="manual_verification")   # lower rank: never supersedes
    _ev(a, field="zoning", value="R-2", evidence_type="FACT", confidence="HIGH", source="hs_gis_owner_mailing", effective_date="2026-03-01")
    rows = [dict(r) for r in db.q("SELECT * FROM evidence WHERE property_id=? AND field='zoning' ORDER BY id", (a,))]
    assert len(rows) == 3, "nothing is deleted"
    first, manual, second = rows
    assert first["superseded_by"] is None or first["superseded_by"] != manual["id"], "a manual reading does not supersede an automated one"
    assert manual["superseded_by"] == second["id"], "an automated reading supersedes the lower-ranked manual one"
    assert second["superseded_by"] is None
    assert db.q1("SELECT 1 FROM conflicts WHERE property_id=? AND field='zoning' AND status='NEEDS VERIFICATION'", (a,)), "two sources disagreeing is a recorded conflict"
    view = {e["id"]: e for e in store.evidence_view(a)}
    assert view[manual["id"]]["superseded"] and view[second["id"]]["conflict"]
    # historical rows without an origin are classified on read, never rewritten
    db.ex("UPDATE evidence SET origin=NULL WHERE id=?", (first["id"],))
    assert store.with_origin(dict(db.q1("SELECT * FROM evidence WHERE id=?", (first["id"],))))["origin"] == "AUTOMATED_SOURCE"
    assert db.q1("SELECT origin FROM evidence WHERE id=?", (first["id"],))["origin"] is None


# ------------------------------------------------------------------ 14: the model's view

def test_ai_evidence_block_names_origin_and_separates_notes(client):
    from hunter import store, ai
    a = _prop("805-1")
    _ev(a, field="cleanup_lien_amount", value="1500", evidence_type="FACT", confidence="HIGH", source="hs_gis_liens")
    _ev(a, field="manual:title", value="deed index clear", evidence_type="OBSERVATION", confidence="MEDIUM", source="manual_verification")
    _ev(a, field="signal:vacant_land", value="vacant land", evidence_type="OBSERVATION", confidence="HIGH", source="property_hunter.distress")
    client.post(f"/api/property/{a}/note", json={"body": "Neighbour thinks the owner died."})
    prop = dict(store.get_property(a), notes=store.notes_for(a))
    block = ai.evidence_block(prop, store.evidence_view(a))
    assert "[AUTOMATED SOURCE | hs_gis_liens | HIGH | FACT" in block
    assert "[MANUAL VERIFICATION | manual_verification | MEDIUM | OBSERVATION" in block
    assert "[DERIVED | property_hunter.distress | HIGH | OBSERVATION" in block
    assert "NOTES (human commentary, UNVERIFIED" in block and "NOTE: Neighbour thinks the owner died." in block
    assert "evidence:" in block
    for line in block.splitlines():
        if line.strip().startswith("- ") and "[" in line and "NOTE:" not in line:
            assert any(lbl in line for lbl in ("AUTOMATED SOURCE", "MANUAL VERIFICATION", "DERIVED", "AI OPINION")), line


# ------------------------------------------------------------------ 7, 15, 16: exports, public JSON, UI

def test_provenance_survives_export_and_ui(client):
    from hunter import cases, store, reports
    import tools.build_share as bs
    a = _prop("806-1", "05051")
    cid = cases.open_or_create(a, {"event": "NEW_LIEN", "cls": "WORLD_EVENT", "evidence_ref": "evidence:1"})["investigation_id"]
    _ev(a, field="cleanup_lien_amount", value="900", evidence_type="FACT", confidence="HIGH", source="hs_gis_liens", effective_date="2026-09-01")
    client.post(f"/api/case/{cid}/log", json={"source": "City lien desk", "date": "2026-09-15", "question": "lien_detail", "state": "FOUND", "result": "payoff letter says $900 plus interest"})
    out = cases.export_all()
    case = out["cases"][str(cid)]
    origins = {e["origin"] for e in case["evidence"]}
    assert {"AUTOMATED_SOURCE", "MANUAL_VERIFICATION"} <= origins
    for e in case["evidence"]:
        assert e["origin"] in store.ORIGINS and e["verification"] == store.ORIGIN_LABEL[e["origin"]] and e["ref"] and e["source"]
    rows, _ = bs.export_rows(only=[a])
    tl = bs.build_timelines({rows[0]["i"]: rows[0]})["05051"][str(a)]
    assert any(e["cls"] == "MANUAL" and e["origin"] == "MANUAL_VERIFICATION" for e in tl)
    assert any(e["origin"] == "AUTOMATED_SOURCE" for e in tl if e["ref"].startswith("evidence:"))
    d = reports.dossier(a)
    assert d["provenance"] and all("origin" in e for e in d["evidence"]) and isinstance(d["notes"], list)
    html = reports.dossier_html(a)
    assert "MANUAL VERIFICATION" in html and "AUTOMATED SOURCE" in html and "<th>Origin</th>" in html
    pub = json.loads((DOCS / "data" / "investigations.json").read_text())
    for c in pub["cases"].values():
        for e in c["evidence"]:
            assert e.get("origin") in store.ORIGINS, e
    inv = (DOCS / "investigation.html").read_text()
    assert "PH.originLabel(e.origin)" in inv and "SUPERSEDED" in inv and "CONFLICT OPEN" in inv
    ph = (DOCS / "ph.js").read_text()
    assert "PH.ORIGIN = " in ph and "org-${PH.esc(e.origin)}" in ph
    assert (DOCS / "ph.css").read_text().count(".org-") >= 5
