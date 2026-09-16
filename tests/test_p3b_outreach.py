"""P3B evidence-gated owner-outreach PREPARATION. Draft only. One active preparation per case and purpose;
the gate is evidence with provenance, never the score; UNKNOWN stays UNKNOWN; the system never writes an
unsupported claim; human text is labelled and never overwritten; every step is a case action; the public
snapshot carries no draft text and no address; nothing can be sent."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_record

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NO_INV = {"built_at": None, "listings": {}}
HUNT_NONE = {"sources": {}, "finished_at": None}
SIG = {"event": "NEW_LIEN", "cls": "WORLD_EVENT", "label": "New City lien", "date": "2026-09-12", "src": "City of Hot Springs", "evidence_ref": "evidence:77", "status": "VERIFIED", "kind": "verified", "why": "The City spent money here"}


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


def _prop(parcel, county="05069", **over):
    from hunter import store
    owner = over.pop("owner_name", "SMITH, JOHN")
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, address=over.pop("address", f"{parcel} Outreach St"), owner_name=owner, **over))
    for field, value in (("owner_name", owner), ("parcel_id", parcel)):   # the roll adapter records these as evidence; the fixture record does not
        if not store.latest_evidence(pid, field):
            store.store_evidence(pid, [{"field": field, "value": value, "evidence_type": "FACT", "confidence": "HIGH", "source": "ar_gis_parcels", "source_name": "Arkansas GIS Office - statewide parcels (county assessor CAMA)", "effective_date": "2025-10-31"}])
    return pid


def _ev(pid, **e):
    from hunter import store
    store.store_evidence(pid, [e])


def _case(client, pid):
    return client.post(f"/api/property/{pid}/case", json={"signal": SIG}).json()["investigation_id"]


# ------------------------------------------------------------------ 1, 2, 3, 13, 14: linkage, duplicates, requirements, blocking

def test_preparation_links_to_case_and_gate_blocks_honestly(client):
    from hunter import outreach
    pid = _prop("900-1")
    cid = _case(client, pid)
    r = client.post(f"/api/case/{cid}/outreach", json={"purpose": "OFF_MARKET_INQUIRY", "reason": "A City lien was recorded and I want to ask whether they would consider selling."})
    assert r.status_code == 200
    p = r.json()
    assert p["case_id"] == cid and p["property_id"] == pid and p["purpose"] == "OFF_MARKET_INQUIRY" and p["status"] == "PREPARING" and p["active"] == 1
    g = p["gate"]
    assert g["ready"] is False and "MAILING ADDRESS" in g["blocking"] and g["explanation"].startswith("Draft cannot be prepared")
    by = {x["key"]: x for x in g["requirements"]}
    assert by["property_identity"]["state"] == "FOUND" and by["property_identity"]["level"] == "REQUIRED TO DRAFT"
    assert by["owner_identity"]["state"] == "FOUND" and by["owner_identity"]["origin"] == "AUTOMATED_SOURCE" and by["owner_identity"]["refs"] and by["owner_identity"]["value"] == "SMITH, JOHN"
    assert by["mailing_address"]["state"] == "UNKNOWN" and "MAILING ADDRESS: UNKNOWN" in by["mailing_address"]["text"] and by["mailing_address"].get("value") is None
    assert by["reason_for_contact"]["state"] == "FOUND" and by["reason_for_contact"]["origin_label"] == "HUMAN-ENTERED" and by["reason_for_contact"]["signals"]
    for k in ("tax_information", "title_record", "physical_observation"):
        assert by[k]["level"] == "RECOMMENDED BEFORE DRAFT" and by[k]["state"] == "UNKNOWN" and by[k]["level_note"] == "UNKNOWN / NOT AVAILABLE"
    assert "TITLE/DEED: UNKNOWN" in by["title_record"]["text"] and by["title_record"]["next"]["label"] == "OPEN COUNTY SOURCE"
    assert "PHYSICAL OBSERVATION: UNKNOWN" in by["physical_observation"]["text"]
    for r_ in g["requirements"]:
        assert not re.search(r"\b(NO|CLEAR|CURRENT|PAID|VACANT|FOR SALE|NOT FOR SALE|GOOD|BAD)\b", r_["text"]) or r_["state"] == "FOUND", r_
    # the same purpose again returns the same active preparation; a different purpose is separate
    p2 = client.post(f"/api/case/{cid}/outreach", json={"purpose": "OFF_MARKET_INQUIRY"}).json()
    assert p2["id"] == p["id"] and len(outreach.for_case(cid)) == 1
    p3 = client.post(f"/api/case/{cid}/outreach", json={"purpose": "RECORD_FOLLOWUP", "reason": "x"}).json()
    assert p3["id"] != p["id"] and len(outreach.for_case(cid)) == 2
    assert client.post(f"/api/case/{cid}/outreach", json={"purpose": "BUY_THIS_PROPERTY"}).status_code == 400
    # a blocked gate refuses to draft, with the gate in the answer
    d = client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "T", "contact": "t@example.com"})
    assert d.status_code == 409 and "MAILING ADDRESS" in d.json()["detail"]["error"]
    assert outreach.get_prep(p["id"])["drafts"] == []
    # requirement sets are explicit and purpose-specific
    req = client.get(f"/api/case/{cid}/outreach").json()["requirements"]
    assert req["OFF_MARKET_INQUIRY"]["required"] == ["property_identity", "owner_identity", "mailing_address", "reason_for_contact"]
    assert "physical_observation" in req["OFF_MARKET_INQUIRY"]["recommended"] and "physical_observation" not in req["OWNER_CONTACT_REQUEST"]["recommended"]


# ------------------------------------------------------------------ 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 18, 19, 20, 23

def test_gate_opens_only_on_evidence_and_draft_states_only_what_is_supported(client):
    from hunter import outreach, cases, db
    pid = _prop("901-1", "05051", address="12 Gate St", owner_name="DOE, JANE")
    cid = _case(client, pid)
    p = client.post(f"/api/case/{cid}/outreach", json={"purpose": "OFF_MARKET_INQUIRY", "reason": "I would like to ask about a private sale."}).json()
    assert not p["gate"]["ready"]
    # a note with an address is NOT a mailing address
    client.post(f"/api/property/{pid}/note", json={"body": "Someone said she lives at 5 Elsewhere Rd."})
    p = client.post(f"/api/outreach/{p['id']}/gate", json={}).json()
    assert {x["key"]: x["state"] for x in p["gate"]["requirements"]}["mailing_address"] == "UNKNOWN"
    # a manual verification of the mailing address is legitimate and labelled
    client.post(f"/api/case/{cid}/log", json={"source": "Garland County Assessor (actDataScout, in browser)", "date": "2026-09-16", "question": "mailing_address", "state": "FOUND", "result": "PO BOX 12, HOT SPRINGS AR 71901"})
    p = client.post(f"/api/outreach/{p['id']}/gate", json={}).json()
    g = p["gate"]; by = {x["key"]: x for x in g["requirements"]}
    assert g["ready"] and p["status"] == "READY"
    assert by["mailing_address"]["state"] == "FOUND" and by["mailing_address"]["origin"] == "MANUAL_VERIFICATION" and "manual verification" in by["mailing_address"]["text"] and by["mailing_address"]["refs"][0].startswith("evidence:")
    assert by["tax_information"]["state"] == "UNKNOWN" and by["tax_information"]["tax_state"] in ("UNKNOWN", "SOURCE_UNAVAILABLE")
    # the draft: supported statements only, human text labelled, evidence snapshot recorded
    d = client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "Topher Cook", "contact": "501-555-0100", "message": "I grew up two streets over and would keep the house as it is."}).json()
    assert d["status"] == "DRAFTED" and d["current_version"] == 1 and d["current"]["kind"] == "SYSTEM"
    text = d["current"]["text"]
    assert "Jane Doe" in text and "PO BOX 12, HOT SPRINGS AR 71901" in text and "12 Gate St" in text and "901-1" in text
    assert "I have not checked the property taxes." in text and "I have not read the deed or title records." in text and "I have not visited the property." in text
    for bad in ("motivated", "distressed", "vacant", "owes", "for sale", "good investment", "clear title", "worth"):
        assert bad not in text.lower(), bad
    kinds = {s["kind"] for s in d["current"]["segments"]}
    assert kinds == {"SYSTEM", "HUMAN_ADDED"}
    human = [s["text"] for s in d["current"]["segments"] if s["kind"] == "HUMAN_ADDED"]
    assert any("two streets over" in h for h in human) and any("Why I am writing (in my words)" in h for h in human)
    owner_seg = next(s for s in d["current"]["segments"] if s["kind"] == "SYSTEM" and "PO BOX 12" in s["text"])
    assert owner_seg["basis"] and all(b.startswith("evidence:") for b in owner_seg["basis"])
    basis = {b["requirement"]: b for b in d["current"]["evidence_basis"]}
    assert basis["OWNER IDENTITY"]["refs"] and basis["MAILING ADDRESS"]["refs"] and basis["MAILING ADDRESS"]["origin"] == "MANUAL_VERIFICATION" and basis["TAX INFORMATION"]["state"] == "UNKNOWN"
    assert d["send_path"] is None
    # tax sentence appears only when the shared model has a verified state, and says exactly that
    db.ex("UPDATE properties SET tax_status='CURRENT_BILL_OPEN' WHERE id=?", (pid,))
    _ev(pid, field="tax_bill", value="$66.65 owed to the County Collector (current real estate $66.65)", evidence_type="FACT", confidence="HIGH", source="county_tax_collector", source_name="County Tax Collector", effective_date="2026-09-15")
    d2 = client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "Topher Cook", "contact": "501-555-0100"}).json()
    t2 = d2["current"]["text"]
    assert "current-year tax bill open ($66.65)" in t2 and "not a delinquency" in t2 and "I have not checked the property taxes." not in t2
    assert "delinquent" not in t2.lower()
    assert {b["requirement"]: b for b in d2["current"]["evidence_basis"]}["TAX INFORMATION"]["state"] == "FOUND"
    # the first draft is still traceable to what supported it then
    v1 = next(x for x in d2["drafts"] if x["version"] == 1)
    assert {b["requirement"]: b for b in v1["evidence_basis"]}["TAX INFORMATION"]["state"] == "UNKNOWN"
    # timeline
    ev = [e["cls"] for e in cases.get_case(cid)["events"]]
    for k in ("OUTREACH PREPARATION STARTED", "OUTREACH GATE EVALUATED", "DRAFT GENERATED"):
        assert k in ev
    assert "SENT" not in " ".join(ev)


# ------------------------------------------------------------------ 17, 19: human edits are preserved

def test_human_edit_is_a_new_version_and_survives_regeneration(client):
    from hunter import outreach, cases
    pid = _prop("902-1", "05051", owner_name="ROE, RICHARD")
    _ev(pid, field="owner_mailing_address", value="100 MAIL RD, BENTON AR 72015", evidence_type="FACT", confidence="HIGH", source="hs_gis_owner_mailing", source_name="City of Hot Springs GIS - county parcel copy with owner mailing address", effective_date="2026-08-01")
    cid = _case(client, pid)
    p = client.post(f"/api/case/{cid}/outreach", json={"purpose": "PROPERTY_STATUS_INQUIRY", "reason": "Asking about status."}).json()
    by = {x["key"]: x for x in p["gate"]["requirements"]}
    assert p["gate"]["ready"] and by["mailing_address"]["origin"] == "AUTOMATED_SOURCE" and by["mailing_address"]["source"].startswith("City of Hot Springs")
    d = client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "T", "contact": "t@example.com"}).json()
    assert client.post(f"/api/outreach/{p['id']}/edit", json={"text": "   "}).status_code == 400
    e = client.post(f"/api/outreach/{p['id']}/edit", json={"text": d["current"]["text"] + "\n\nP.S. I can meet any weekday."}).json()
    assert e["status"] == "EDITED" and e["current_version"] == 2 and e["current"]["kind"] == "HUMAN_EDITED" and e["current"]["segments"][0]["edited_from"] == 1
    assert len(e["drafts"]) == 2 and e["drafts"][0]["text"] == d["current"]["text"], "the system version is kept"
    r = client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "T", "contact": "t@example.com"}).json()
    assert r["current_version"] == 2 and r["current"]["kind"] == "HUMAN_EDITED" and "P.S. I can meet" in r["current"]["text"], "regeneration never overwrites a person's edit"
    assert len(r["drafts"]) == 3 and r["drafts"][2]["kind"] == "SYSTEM"
    # return later: the same state
    again = client.get(f"/api/outreach/{p['id']}").json()
    assert again["current"]["text"] == r["current"]["text"] and again["current"]["evidence_basis"]
    ev = [x["cls"] for x in cases.get_case(cid)["events"]]
    assert "DRAFT EDITED" in ev and ev.count("DRAFT GENERATED") == 2
    # review / discard; never SENT
    assert client.post(f"/api/outreach/{p['id']}/status", json={"status": "SENT"}).status_code == 400
    assert client.post(f"/api/outreach/{p['id']}/status", json={"status": "REVIEWED"}).json()["status"] == "REVIEWED"
    dis = client.post(f"/api/outreach/{p['id']}/status", json={"status": "DISCARDED"}).json()
    assert dis["status"] == "DISCARDED" and dis["active"] == 0 and len(dis["drafts"]) == 3, "discarding keeps every draft"
    assert client.post(f"/api/outreach/{p['id']}/draft", json={}).status_code == 400
    new = client.post(f"/api/case/{cid}/outreach", json={"purpose": "PROPERTY_STATUS_INQUIRY", "reason": "again"}).json()
    assert new["id"] != p["id"], "a discarded preparation is no longer the active one"


# ------------------------------------------------------------------ 21, 22, 24: redaction, no-send, export

def test_public_snapshot_is_redacted_and_nothing_can_send(client):
    from hunter import outreach, cases
    import hunter.outreach as om, hunter.api as am
    pid = _prop("903-1", "05051", owner_name="POE, EDGAR")
    _ev(pid, field="owner_mailing_address", value="7 RAVEN CT, HOT SPRINGS AR 71901", evidence_type="FACT", confidence="HIGH", source="hs_gis_owner_mailing", effective_date="2026-08-01")
    cid = _case(client, pid)
    p = client.post(f"/api/case/{cid}/outreach", json={"purpose": "OWNER_CONTACT_REQUEST", "reason": "contact"}).json()
    client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "T", "contact": "t@example.com"})
    snap = json.dumps(cases.export_all())
    assert "RAVEN CT" not in snap and "t@example.com" not in snap and "Dear " not in snap
    pub = cases.export_all()["cases"][str(cid)]["outreach"][0]
    assert pub["purpose"] == "OWNER_CONTACT_REQUEST" and pub["status"] == "DRAFTED" and pub["gate"]["ready"] is True and pub["draft_version"] == 1
    assert {"label", "level", "state", "origin"} == set(pub["gate"]["requirements"][0])
    # no send path anywhere in the outreach module or its routes
    src = Path(om.__file__).read_text()
    for bad in ("smtplib", "sendmail", "httpx.post", "requests.post", "twilio", "\"SENT\""):
        assert bad not in src, bad
    api_src = Path(am.__file__).read_text()
    outreach_block = api_src[api_src.index("P3B: owner-outreach PREPARATION"):api_src.index('@app.post("/api/watch/import")')]
    assert "send" not in outreach_block.lower().replace("nothing here sends", "").replace("sends, posts", "").replace("that sends", "")
    assert "smtp" not in outreach_block.lower() and "notify" not in outreach_block
    # public pages: the outreach page never fetches data files and states the no-send rule
    page = (DOCS / "outreach.html").read_text()
    assert "data/investigations.json" not in page and "There is no send button" in page and "REQUIRED TO DRAFT" in page and "RECOMMENDED BEFORE DRAFT" in page and "UNKNOWN / NOT AVAILABLE" in page
    assert "SYSTEM-GENERATED TEXT" in page and "HUMAN-ADDED TEXT" in page and "HUMAN-EDITED TEXT" in page
    inv = (DOCS / "investigation.html").read_text()
    assert "outreach.html?case=" in inv
    live = json.loads((DOCS / "data" / "investigations.json").read_text())
    for c in live["cases"].values():
        for o in c.get("outreach", []):
            assert "text" not in o and "drafts" not in o
            assert all("value" not in r for r in o["gate"]["requirements"])
