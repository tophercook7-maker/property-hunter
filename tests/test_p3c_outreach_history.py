"""P3C: the case-gated outreach workflow is the only way a new letter comes to exist; the legacy flag-driven
Letters path is retired; a person records what THEY did (HUMAN-REPORTED), which never means delivery,
never creates SENT, never sends; privacy, provenance and history stay intact."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_record

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SIG = {"event": "NEW_LIEN", "cls": "WORLD_EVENT", "label": "New City lien", "date": "2026-09-12", "src": "City of Hot Springs", "evidence_ref": "evidence:77", "status": "VERIFIED", "kind": "verified", "why": "w"}


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


def _prop(parcel, county="05051", owner="POE, EDGAR", mailing="7 RAVEN CT, HOT SPRINGS AR 71901"):
    from hunter import store
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, address=f"{parcel} History St", owner_name=owner))
    for field, value, src in (("owner_name", owner, "ar_gis_parcels"), ("parcel_id", parcel, "ar_gis_parcels"), ("owner_mailing_address", mailing, "hs_gis_owner_mailing")):
        if value and not store.latest_evidence(pid, field):
            store.store_evidence(pid, [{"field": field, "value": value, "evidence_type": "FACT", "confidence": "HIGH", "source": src, "source_name": src, "effective_date": "2025-10-31"}])
    return pid


def _drafted_prep(client, pid):
    cid = client.post(f"/api/property/{pid}/case", json={"signal": SIG}).json()["investigation_id"]
    p = client.post(f"/api/case/{cid}/outreach", json={"purpose": "PROPERTY_STATUS_INQUIRY", "reason": "asking about status"}).json()
    assert p["gate"]["ready"]
    p = client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "T", "contact": "t@example.com"}).json()
    return cid, p


# ------------------------------------------------------------------ 1, 2, 3, 4, 26: legacy path retired, history preserved

def test_legacy_letters_page_no_longer_generates_letters():
    page = (DOCS / "campaign.html").read_text()
    for gone in ("letterFor", "printPacket", "My offer", "costing you", "none on record", "Most reason to let it go", "leverage(", "copyCsv"):
        assert gone not in page, gone
    for kept in ("moved into investigation cases", "investigation.html?id=", "outreach.html?case=", "INVESTIGATE PROPERTY", "read-only", "ph-campaign"):
        assert kept in page, kept
    assert "data/garland.json" not in page, "the flag table that fed letters is gone from the page"
    look = (DOCS / "lookup.html").read_text()
    for gone in ("Draft the owner letter", "o-letter", "distressed sales actually happen", "I would like to buy it myself", "requiredForLetter"):
        assert gone not in look, gone
    assert "PH.investigateBtn(r, null)" in look and "outreach is prepared in the case" in look
    idx = (DOCS / "index.html").read_text()
    assert "campaign.html?pid=" not in idx and "Prepare outreach" in idx and "the site never mails" in idx.lower() or "The site never mails" in idx
    assert '"Outreach"' in (DOCS / "nav.js").read_text()
    # the new workflow's own generator never writes the retired wording
    from hunter import outreach
    src = Path(outreach.__file__).read_text()
    for w in ("costing you", "My offer", "none on record", "let it go"):
        assert w not in src, w
    for w in ("motivated", "distressed", "for sale", "clear title", "vacant", "owes"):
        assert w in outreach.FORBIDDEN_SYSTEM_WORDS


def test_history_and_p3b_drafts_are_untouched(client):
    """Retiring the legacy path deletes nothing: earlier drafts, versions and evidence stay exactly as they were."""
    from hunter import db, outreach
    pid = _prop("950-1")
    cid, p = _drafted_prep(client, pid)
    e = client.post(f"/api/outreach/{p['id']}/edit", json={"text": p["current"]["text"] + "\n\nP.S."}).json()
    before_ev = db.q1("SELECT COUNT(*) n FROM evidence")["n"]
    before_drafts = [(d["version"], d["kind"], d["text"]) for d in e["drafts"]]
    client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "OUTREACH_PRINTED_BY_HUMAN", "action_date": "2026-09-16"})
    client.post(f"/api/outreach/{p['id']}/status", json={"status": "REVIEWED"})
    after = outreach.get_prep(p["id"])
    assert [(d["version"], d["kind"], d["text"]) for d in after["drafts"]] == before_drafts
    assert db.q1("SELECT COUNT(*) n FROM evidence")["n"] == before_ev
    assert after["current"]["evidence_basis"] and after["status"] == "REVIEWED"


# ------------------------------------------------------------------ 5-9, 14-17: human action records

def test_human_action_is_recorded_as_human_reported_and_never_sent(client):
    from hunter import cases, outreach, db
    pid = _prop("951-1")
    cid, p = _drafted_prep(client, pid)
    # nothing drafted yet on a fresh preparation: printing/mailing has nothing to refer to
    p0 = client.post(f"/api/case/{cid}/outreach", json={"purpose": "RECORD_FOLLOWUP", "reason": "x"}).json()
    assert client.post(f"/api/outreach/{p0['id']}/action", json={"action_type": "OUTREACH_MAILED_BY_HUMAN", "action_date": "2026-09-16"}).status_code == 400
    assert client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "SENT", "action_date": "2026-09-16"}).status_code == 400
    assert client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "OUTREACH_MAILED_BY_HUMAN", "action_date": "yesterday"}).status_code == 400
    assert client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "OUTREACH_MAILED_BY_HUMAN", "action_date": "2026-09-16", "draft_version": 9}).status_code == 400
    r = client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "OUTREACH_MAILED_BY_HUMAN", "action_date": "2026-09-16", "draft_version": 1, "note": "Dropped at the post office; owner called me back the next day", "actor": "topher"}).json()
    assert r["duplicate"] is False and r["provenance"] == "HUMAN_REPORTED" and r["actor"] == "topher" and r["draft_version"] == 1 and r["case_id"] == cid and r["property_id"] == pid
    assert r["label"] == "Mailed by me" and "does not mean the recipient received" in r["meaning"]
    # deterministic duplicate: same person, type, date, version -> the same record, no second event
    r2 = client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "OUTREACH_MAILED_BY_HUMAN", "action_date": "2026-09-16", "draft_version": 1, "note": "again", "actor": "topher"}).json()
    assert r2["duplicate"] is True and r2["id"] == r["id"]
    assert len(outreach.actions_for_prep(p["id"])) == 1
    # a different action or date is a new record
    r3 = client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "OUTREACH_CONTACTED_BY_HUMAN", "action_date": "2026-09-17", "actor": "topher"}).json()
    assert r3["duplicate"] is False and len(outreach.actions_for_prep(p["id"])) == 2
    # survives reload; the preparation status is untouched by actions and never becomes SENT
    again = client.get(f"/api/outreach/{p['id']}").json()
    assert [a["id"] for a in again["actions"]] == [r["id"], r3["id"]] and again["status"] == "DRAFTED" and again["send_path"] is None
    assert "SENT" not in {a["action_type"] for a in again["actions"]} and again["status"] not in ("SENT", "DELIVERED", "RECEIVED")
    # timeline: one event per record, HUMAN OUTREACH ACTION, with the human's reference; nothing named SENT
    ev = cases.get_case(cid)["events"]
    acts = [e for e in ev if e["cls"] == "HUMAN OUTREACH ACTION"]
    assert len(acts) == 2 and acts[0]["ref"] == f"outreach_action:{r['id']}" and acts[0]["actor"] == "topher" and "recorded by a human" in acts[0]["detail"]
    assert "owner called me back" in acts[0]["detail"] and "not a system assertion" in acts[0]["detail"]
    assert not any(e["cls"] in ("SENT", "DELIVERED", "RECEIVED") or "SENT" in e["cls"] for e in ev)
    assert not any(("DELIVERED" in e["cls"] or "RECEIVED" in e["cls"]) for e in ev if (e.get("ref") or "").startswith("outreach")), "no outreach event asserts delivery or receipt"
    # the note stays a human note: no evidence row was created from it
    assert not [x for x in db.q("SELECT field FROM evidence WHERE property_id=?", (pid,)) if "called" in (x["field"] or "")]
    assert db.q1("SELECT provenance FROM outreach_actions WHERE id=?", (r["id"],))["provenance"] == "HUMAN_REPORTED"


# ------------------------------------------------------------------ 10, 11, 12, 13, 24: no send, no inference, approvals unreachable

def test_no_send_no_inference_no_approval_path():
    import hunter.outreach as om, hunter.api as am, hunter.cases as cm
    src = Path(om.__file__).read_text()
    for bad in ("smtplib", "sendmail", "notify", "approvals", "contact_seller", "send_message", "httpx", "requests", "webbrowser", "subprocess", '"SENT"', "'SENT'"):
        assert bad not in src, bad
    api_src = Path(am.__file__).read_text()
    block = api_src[api_src.index("P3B: owner-outreach PREPARATION"):api_src.index('@app.post("/api/watch/import")')]
    for bad in ("notify", "approvals", "contact_seller", "send_message", "smtp", "window.print"):
        assert bad not in block, bad
    assert "SENT" not in cm.EVENT_CLASSES and not any("SENT" in c for c in cm.EVENT_CLASSES)
    assert set(om.ACTION_TYPES) == {"OUTREACH_PRINTED_BY_HUMAN", "OUTREACH_MAILED_BY_HUMAN", "OUTREACH_HAND_DELIVERED_BY_HUMAN", "OUTREACH_CONTACTED_BY_HUMAN", "OUTREACH_OTHER_HUMAN_ACTION"}
    page = (DOCS / "outreach.html").read_text()
    assert "records nothing" in page and "Record action" in page and "HUMAN-REPORTED" in page
    assert "There is no send button" in page and not re.search(r">\s*Send\b", page) and "SENT" not in page
    # printing is a plain browser print with no API call; nothing records an action on print
    for m in re.finditer(r"window\.print\(\)", page):
        line = page[max(0, m.start() - 200): m.end() + 200]
        assert "api(" not in line, "print must not call the app"
    # the generic approval system still exists untouched and is not referenced by outreach
    from hunter.config import APPROVAL_REQUIRED_ACTIONS
    assert "contact_seller" in APPROVAL_REQUIRED_ACTIONS and "send_message" in APPROVAL_REQUIRED_ACTIONS


# ------------------------------------------------------------------ 18-23, 25: privacy, semantics, provenance, gate

def test_public_export_redacts_actions_and_gate_and_provenance_remain(client):
    from hunter import cases, store
    pid = _prop("952-1")
    cid, p = _drafted_prep(client, pid)
    client.post(f"/api/outreach/{p['id']}/action", json={"action_type": "OUTREACH_MAILED_BY_HUMAN", "action_date": "2026-09-16", "draft_version": 1, "note": "SECRET NOTE about the neighbour", "actor": "topher"})
    out = cases.export_all()
    s = json.dumps(out)
    assert "SECRET NOTE" not in s and "7 RAVEN CT" not in s and "t@example.com" not in s and "Dear " not in s
    pub = out["cases"][str(cid)]["outreach"][0]
    assert pub["human_actions"]["counts_by_type"] == {"OUTREACH_MAILED_BY_HUMAN": 1} and pub["human_actions"]["provenance"] == "HUMAN_REPORTED"
    assert "note" not in json.dumps(pub["human_actions"]["counts_by_type"]) and "actor" not in pub["human_actions"]
    idx = cases.index()["by_property"][str(pid)]
    assert idx["outreach"] == [{"purpose": "PROPERTY_STATUS_INQUIRY", "status": "DRAFTED"}]
    # UNKNOWN semantics and the P3B gate are unchanged
    g = client.get(f"/api/case/{cid}/outreach?purpose=OFF_MARKET_INQUIRY").json()["preview"]
    by = {r["key"]: r for r in g["requirements"]}
    assert by["title_record"]["state"] == "UNKNOWN" and "TITLE/DEED: UNKNOWN" in by["title_record"]["text"]
    assert by["physical_observation"]["state"] == "UNKNOWN" and by["reason_for_contact"]["state"] == "UNKNOWN"
    assert by["mailing_address"]["state"] == "FOUND" and by["mailing_address"]["origin"] == "AUTOMATED_SOURCE"
    # P3A provenance intact on every evidence row of the case
    for e in cases.get_case(cid)["evidence"]:
        assert e["origin"] in store.ORIGINS and e["verification"] == store.ORIGIN_LABEL[e["origin"]]
    live = json.loads((DOCS / "data" / "investigations.json").read_text())
    for c in live["cases"].values():
        for o in c.get("outreach", []):
            assert "human_actions" in o and set(o["human_actions"]) == {"counts_by_type", "provenance", "note"}
            assert "text" not in o and "drafts" not in o and "actions" not in o
