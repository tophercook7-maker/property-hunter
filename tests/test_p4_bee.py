"""P4 Bee investigator foundation: deterministic reference-based snapshot; strict output contract; AI text is
AI_OPINION and never evidence; UNKNOWN stays UNKNOWN; proposals are not checks; acceptance creates no fact;
failures are explicit and harmless; no send, no contact, no draft; privacy intact; P0-P3 untouched."""
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


def _prop(parcel, county="05051", owner="POE, EDGAR", mailing=None, mailing_date="2004-02-20"):
    from hunter import store
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, address=f"{parcel} Bee St", owner_name=owner))
    rows = [("owner_name", owner, "ar_gis_parcels", "2025-10-31"), ("parcel_id", parcel, "ar_gis_parcels", "2025-10-31")]
    if mailing:
        rows.append(("owner_mailing_address", mailing, "hs_gis_owner_mailing", mailing_date))
    for field, value, src, d in rows:
        if not store.latest_evidence(pid, field):
            store.store_evidence(pid, [{"field": field, "value": value, "evidence_type": "FACT", "confidence": "HIGH", "source": src, "source_name": src, "effective_date": d}])
    return pid


def _case(pid):
    from hunter import cases
    return cases.open_or_create(pid, SIG)["investigation_id"]


def _state(cid):
    from hunter import cases, db
    c = cases.get_case(cid)
    return ({q["key"]: (q["state"], q["checked_by"]) for q in c["questions"]}, db.q1("SELECT COUNT(*) n FROM evidence")["n"], [(d["version"], d["text"]) for p in __import__("hunter.outreach", fromlist=["for_case"]).for_case(cid) for d in p["drafts"]])


def good_output(snap, r):
    known = [{"question": q["key"], "evidence_refs": q["evidence_refs"][:1], "text": f"{q['key']} from {q['source']} dated {q['checked_at']}"} for q in snap["questions"] if q["state"] == "FOUND" and q["evidence_refs"]][:3]
    return {"summary": "Owner and parcel are on the roll; taxes, title and a visit are still unknown.",
            "known": known, "unknown": [{"question": q["key"], "text": "no source has answered"} for q in snap["questions"] if q["state"] == "UNKNOWN"][:4],
            "conflicts": [], "inferences": [{"text": "The mailing record may be old; a newer Assessor reading would settle it.", "evidence_refs": []}],
            "proposed_checks": [{"proposal_id": r["candidates"][0]["proposal_id"], "priority": 1, "why": "Tax state is unknown and the question changes the case."}],
            "warnings": ["The Collector portal is down; do not read that as paid."]}


# ------------------------------------------------------------------ 1, 2, 3, 26: snapshot

def test_snapshot_is_deterministic_reference_based_and_redacted():
    from hunter import bee
    pid = _prop("1000-1", mailing="7 RAVEN CT, HOT SPRINGS AR 71901")
    cid = _case(pid)
    a, b = bee.snapshot(cid), bee.snapshot(cid)
    assert a["hash"] == b["hash"] and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    s = json.dumps(a)
    assert "RAVEN CT" not in s, "the mailing address value never enters the snapshot"
    assert any(e["field"] == "owner_mailing_address" and e["value"] == "[value held locally]" and e["origin"] == "AUTOMATED_SOURCE" and e["date"] == "2004-02-20" for e in a["evidence"])
    for e in a["evidence"]:
        assert e["ref"].startswith("evidence:") and e["origin"] in ("AUTOMATED_SOURCE", "MANUAL_VERIFICATION", "NOTE", "DERIVED", "AI_OPINION") and "type" in e and "confidence" in e
    assert a["tax"]["state"] in ("UNKNOWN", "SOURCE_UNAVAILABLE") and a["title_state"] == "UNKNOWN" and a["physical_state"] == "UNKNOWN"
    assert "countypay" in a["sources"] and a["sources"]["countypay"]["status"] in ("AVAILABLE", "TEMPORARILY_UNAVAILABLE", "BLOCKED", "MANUAL_ONLY", "NOT_FOUND", "NOT_APPLICABLE", "UNKNOWN")
    for secret in ("PH_", "smtp", "password", "api_key", "token"):
        assert secret.lower() not in s.lower()
    # the snapshot answers what exists, not the answer text of the mailing question
    mq = next(q for q in a["questions"] if q["key"] == "mailing_address")
    assert mq["state"] == "FOUND" and "held locally" in mq["answer"]


# ------------------------------------------------------------------ 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 22-25, 30: contract and provenance

def test_bee_output_is_validated_and_never_becomes_evidence(client):
    from hunter import bee, db, cases
    pid = _prop("1001-1")
    cid = _case(pid)
    before = _state(cid)
    snap = bee.snapshot(cid); r = bee.rules(snap)
    # malformed outputs are rejected, recorded as FAILED, and change nothing
    for bad in ("not json", {"summary": "x"}, {"summary": "x", "known": [{"question": "tax_state", "evidence_refs": ["evidence:1"], "text": "paid"}], "unknown": [], "conflicts": [], "proposed_checks": []},
                {"summary": "x", "known": [], "unknown": [{"question": "identity", "text": "?"}], "conflicts": [], "proposed_checks": []},
                {"summary": "x", "known": [], "unknown": [], "conflicts": [], "proposed_checks": [{"proposal_id": "made_up:google", "why": "w"}]},
                {"summary": "x", "known": [], "unknown": [], "conflicts": [], "proposed_checks": [{"proposal_id": r["candidates"][0]["proposal_id"], "status": "COMPLETED"}]},
                {"summary": "The house is vacant and the owner is motivated.", "known": [], "unknown": [], "conflicts": [], "proposed_checks": []},
                {"summary": "Taxes are delinquent.", "known": [], "unknown": [], "conflicts": [], "proposed_checks": []},
                {"summary": "x", "known": [{"question": "owner", "evidence_refs": ["evidence:999999"], "text": "t"}], "unknown": [], "conflicts": [], "proposed_checks": []}):
        obj = bad if isinstance(bad, dict) else None
        res = bee.run(cid, asker=lambda p, system=None, model=None: (obj, {"model": "fake-model", "error": None if obj is not None else "response was not valid JSON", "raw": str(bad)}))
        assert res["status"] == "FAILED" and res["error"] and res["output"] is None and res["origin"] == "AI_OPINION"
    assert _state(cid) == before, "failed analyses change no question, no evidence, no draft"
    # a good output is recorded as AI_OPINION with model, prompt/schema version, timestamp, evidence refs
    res = bee.run(cid, asker=lambda p, system=None, model=None: (good_output(snap, r), {"model": "fake-model", "error": None, "raw": "{}"}))
    assert res["status"] == "OK" and res["origin"] == "AI_OPINION" and res["provider"] == "ollama" and res["model"] == "fake-model"
    assert res["prompt_version"] == bee.PROMPT_VERSION and res["schema_version"] == bee.SCHEMA_VERSION and res["created_at"] and res["snapshot_hash"] == snap["hash"]
    assert res["evidence_refs"] and all(x.startswith("evidence:") for x in res["evidence_refs"])
    assert res["output"]["inferences"][0]["provenance"] == "AI_OPINION" and "may be old" in res["output"]["inferences"][0]["text"]
    # a speculation about vacancy / distress / motivation / sale / title is dropped and named in the warnings, never kept as an inference
    spec = dict(good_output(snap, r), inferences=[{"text": "The house may be vacant and the owner motivated.", "evidence_refs": []}, {"text": "The mailing record may be old.", "evidence_refs": []}])
    res2 = bee.run(cid, asker=lambda p, system=None, model=None: (spec, {"model": "fake-model", "error": None, "raw": "{}"}))
    assert res2["status"] == "OK" and [i["text"] for i in res2["output"]["inferences"]] == ["The mailing record may be old."]
    assert any("may not infer vacancy" in w for w in res2["output"]["warnings"])
    assert _state(cid) == before, "a successful analysis changes no question, no evidence, no draft"
    assert not db.q1("SELECT 1 FROM evidence WHERE property_id=? AND (source LIKE '%bee%' OR origin='AI_OPINION')", (pid,)), "Bee writes no evidence row"
    # proposals exist, are PROPOSED, distinct from checks, and carry where / proves / does-not-prove
    props = bee.proposals(cid)
    assert props and all(p["status"] == "PROPOSED" for p in props)
    p0 = next(p for p in props if p["proposal_id"] == r["candidates"][0]["proposal_id"])
    assert p0["origin"] == "AI_OPINION" and p0["priority"] == 1 and p0["why"].startswith("Tax state is unknown")
    assert p0["expected_information"] and p0["would_not_answer"] and p0["where"]["label"] and p0["source_status"]
    skipped = [p for p in props if p["origin"] == "DERIVED"]
    assert skipped and all(p["priority"] >= 100 for p in skipped), "candidates the model skipped are still listed, lowest priority"
    # the case timeline records the analysis; the analysis history stays auditable; nothing says completed
    ev = [e for e in cases.get_case(cid)["events"] if e["cls"] == "BEE ANALYSIS"]
    assert len(ev) == 11 and ev[-1]["ref"].startswith("bee:")
    hist = bee.view(cid)["history"]
    assert len(hist) == 11 and hist[0]["status"] == "OK" and sum(1 for h in hist if h["status"] == "FAILED") == 9
    assert all(p["status"] != "COMPLETED" for p in props)


# ------------------------------------------------------------------ 14, 15: unavailable sources

def test_unavailable_source_stays_unavailable_and_gets_a_manual_path():
    from hunter import bee
    pid = _prop("1002-1")
    cid = _case(pid)
    snap = bee.snapshot(cid)
    snap["sources"]["countypay"] = {"status": "TEMPORARILY_UNAVAILABLE", "public_url": "https://countypay.ark.org/index.php/garland", "name": "County Collector online search (CountyPay)", "last_checked": "2026-09-16T01:00:00"}
    r = bee.rules(snap)
    tax = next(c for c in r["candidates"] if c["question_id"] == "tax_state")
    assert tax["source_status"] == "TEMPORARILY_UNAVAILABLE" and tax["type"] == "MANUAL_VERIFICATION" and tax["status"] == "PROPOSED"
    assert tax["alternate"] and tax["alternate"]["status"] in ("MANUAL_ONLY", "AVAILABLE", "BLOCKED", "UNKNOWN")
    assert next(u for u in r["unknown"] if u["question"] == "tax_state")["state"] == "UNKNOWN"
    assert not any(w in json.dumps(r["known"]).lower() for w in ("paid", "current", "clear", "not delinquent"))
    # the model may not turn an unavailable source into a checked one
    clean, problems = bee.validate({"summary": "x", "known": [{"question": "tax_state", "evidence_refs": [snap["evidence"][0]["ref"]], "text": "checked: no bill"}], "unknown": [], "conflicts": [], "proposed_checks": []}, snap, r)
    assert clean is None and any("not FOUND" in p for p in problems)


# ------------------------------------------------------------------ 16, 17, 18, 28, 29: human review

def test_acceptance_creates_no_fact_and_decisions_persist(client):
    from hunter import bee, cases, db
    pid = _prop("1003-1")
    cid = _case(pid)
    snap = bee.snapshot(cid); r = bee.rules(snap)
    bee.run(cid, asker=lambda p, system=None, model=None: (good_output(snap, r), {"model": "fake-model", "error": None, "raw": "{}"}))
    before = _state(cid)
    props = client.get(f"/api/case/{cid}/bee").json()["proposals"]
    a, b = props[0], props[1]
    acc = client.post(f"/api/bee/proposal/{a['id']}/decide", json={"decision": "ACCEPT", "actor": "topher"}).json()
    assert acc["status"] == "ACCEPTED" and acc["decided_by"] == "topher"
    rej = client.post(f"/api/bee/proposal/{b['id']}/decide", json={"decision": "REJECT", "note": "not worth a trip", "actor": "topher"}).json()
    assert rej["status"] == "REJECTED" and rej["human_note"] == "not worth a trip"
    ed = client.post(f"/api/bee/proposal/{a['id']}/decide", json={"decision": "EDIT", "edits": {"why": "My own reason: the clerk is open Tuesdays."}}).json()
    assert ed["edited"]["why"].startswith("My own reason") and ed["why"] == a["why"], "the original AI reason is kept beside the human edit"
    assert client.post(f"/api/bee/proposal/{a['id']}/decide", json={"decision": "COMPLETE"}).status_code == 400
    assert client.post(f"/api/bee/proposal/{a['id']}/decide", json={"decision": "EDIT", "edits": {"status": "COMPLETED"}}).status_code == 400
    assert _state(cid) == before, "accepting, rejecting and editing change no question, no evidence, no draft"
    # regenerate: decisions and edits survive; a new analysis is appended, the old one stays
    bee.run(cid, asker=lambda p, system=None, model=None: (good_output(snap, r), {"model": "fake-model", "error": None, "raw": "{}"}))
    again = {p["id"]: p for p in client.get(f"/api/case/{cid}/bee").json()["proposals"]}
    assert again[a["id"]]["status"] == "ACCEPTED" and again[a["id"]]["edited"]["why"].startswith("My own reason") and again[b["id"]]["status"] == "REJECTED"
    assert len(bee.view(cid)["history"]) == 2
    ev = [e["cls"] for e in cases.get_case(cid)["events"]]
    assert ev.count("BEE PROPOSAL DECISION") == 3 and not any(x in " ".join(ev) for x in ("SENT", "CONTACT"))
    assert _state(cid) == before
    # a question answered later by real evidence closes its proposal as COMPLETED by evidence, not by Bee
    from hunter import store
    store.store_evidence(pid, [{"field": "tax_bill", "value": "$66.65 owed to the County Collector (current real estate $66.65)", "evidence_type": "FACT", "confidence": "HIGH", "source": "county_tax_collector", "source_name": "County Tax Collector", "effective_date": "2026-09-16"}])
    db.ex("UPDATE properties SET tax_status='CURRENT_BILL_OPEN' WHERE id=?", (pid,))
    bee.run(cid, asker=lambda p, system=None, model=None: (None, {"model": "fake-model", "error": "timeout", "raw": ""}))
    done = [p for p in bee.proposals(cid) if p["question_key"] == "tax_state"]
    assert done and done[0]["status"] == "COMPLETED" and done[0]["decided_by"] == "evidence"


# ------------------------------------------------------------------ 19, 20, 21, 27: boundaries

def test_no_send_no_contact_no_draft_and_p3_intact(client):
    import hunter.bee as bm, hunter.api as am
    from hunter import outreach, bee
    src = Path(bm.__file__).read_text()
    for bad in ("smtplib", "sendmail", "notify", "approvals", "subprocess", "webbrowser", "requests.", "generate_draft", "edit_draft", "log_manual", "store_evidence", "set_status", "outreach.open_or_get"):
        assert bad not in src, bad
    assert "httpx" not in src.replace("import httpx", "").replace("httpx.get(f\"{OLLAMA_URL}/api/tags\"", "")
    api_src = Path(am.__file__).read_text()
    block = api_src[api_src.index("P4: Bee investigator"):api_src.index('@app.post("/api/watch/import")')]
    for bad in ("notify", "approvals", "smtp", "outreach", "store_evidence", "send"):
        assert bad not in block.lower().replace("never contacts anyone and never drafts outreach", "").replace("never answers a question", ""), bad
    page = (DOCS / "investigation.html").read_text()
    for k in ("Bee investigator", "What we know (from evidence)", "What we don't know", "What Bee thinks we should check next", "Accept proposal", "Reject", "Mark as reviewed", "AI OPINION", "it does not make anything true and records no evidence", "Where to check", "What would prove it", "What would not prove it"):
        assert k in page, k
    for bad in ("chain-of-thought", "agentic", "latent", "vector context", "Send", "SENT"):
        assert bad not in page, bad
    # P3B outreach on a case is untouched by Bee runs
    pid = _prop("1004-1", mailing="1 MAIL RD, BENTON AR 72015", mailing_date="2026-08-01")
    cid = _case(pid)
    p = client.post(f"/api/case/{cid}/outreach", json={"purpose": "PROPERTY_STATUS_INQUIRY", "reason": "asking"}).json()
    d = client.post(f"/api/outreach/{p['id']}/draft", json={"sender_name": "T", "contact": "t@example.com"}).json()
    e = client.post(f"/api/outreach/{p['id']}/edit", json={"text": d["current"]["text"] + "\nP.S."}).json()
    snap = bee.snapshot(cid); r = bee.rules(snap)
    assert not any("t@example.com" in json.dumps(x) or "MAIL RD" in json.dumps(x) for x in (snap,)), "drafts and contact details never enter the snapshot"
    assert snap["outreach"][0]["status"] == "EDITED" and snap["outreach"][0]["draft_version"] == 2
    client.post(f"/api/case/{cid}/bee/run", json={"model": "no-such-model-installed"})
    after = outreach.get_prep(p["id"])
    assert [(x["version"], x["text"]) for x in after["drafts"]] == [(x["version"], x["text"]) for x in e["drafts"]] and after["status"] == "EDITED"
    assert len(outreach.for_case(cid)) == 1, "Bee opened no outreach"
    v = client.get(f"/api/case/{cid}/bee").json()
    assert v["latest"]["status"] == "FAILED" and "not installed" in v["latest"]["error"]
    assert any("Outreach" in t and "never edits" in t for t in v["understanding"]["outreach_notes"])


# ------------------------------------------------------------------ 26: privacy, export

def test_public_export_carries_bee_metadata_only(client):
    from hunter import bee, cases
    pid = _prop("1005-1", mailing="7 RAVEN CT, HOT SPRINGS AR 71901")
    cid = _case(pid)
    snap = bee.snapshot(cid); r = bee.rules(snap)
    bee.run(cid, asker=lambda p, system=None, model=None: (dict(good_output(snap, r), warnings=["PRIVATE WARNING TEXT"]), {"model": "fake-model", "error": None, "raw": "{}"}))
    bee.decide(bee.proposals(cid)[0]["id"], "REJECT", note="PRIVATE DECISION NOTE")
    out = cases.export_all()
    s = json.dumps(out)
    for leak in ("PRIVATE WARNING TEXT", "PRIVATE DECISION NOTE", "RAVEN CT", "CASE SNAPSHOT", "Tax state is unknown and the question changes"):
        assert leak not in s, leak
    b = out["cases"][str(cid)]["bee"]
    assert b["last_analysis"]["status"] == "OK" and b["last_analysis"]["model"] == "fake-model" and b["last_analysis"]["prompt_version"] == bee.PROMPT_VERSION
    assert b["proposals_by_status"]["REJECTED"] == 1 and b["origin"] == "AI_OPINION"
    assert set(b) == {"last_analysis", "proposals_by_status", "origin", "note"}
    live = json.loads((DOCS / "data" / "investigations.json").read_text())
    for c in live["cases"].values():
        assert "bee" in c and set(c["bee"]) == {"last_analysis", "proposals_by_status", "origin", "note"}
        for e in c["events"]:
            if e["cls"] in ("BEE ANALYSIS", "BEE PROPOSAL DECISION"):
                assert e["detail"] == "[Bee text held in the local app]"
