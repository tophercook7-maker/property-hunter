"""P2 investigation case engine: one durable case per property; three-state questions; provenance kept;
manual verification labelled; notes never evidence; next actions only to real destinations."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_record

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
NO_INV = {"built_at": None, "listings": {}}
HUNT_CITY = {"sources": {"roll": True, "state_lands": True, "city_registers": True, "collector": "unavailable"}, "finished_at": "2026-09-15T17:10"}
HUNT_NONE = {"sources": {}, "finished_at": None}


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


def _prop(parcel="700-1", county="05051", **over):
    from hunter import store
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, address=over.pop("address", f"{parcel} Case St"), **over))
    return pid


def _ev(pid, field, value, **kw):
    from hunter import store
    e = {"field": field, "value": value, "evidence_type": "FACT", "confidence": "HIGH", "source": "hs_gis_liens",
         "source_name": "City of Hot Springs", "effective_date": "2026-09-10", "source_url": None}
    e.update(kw)
    store.store_evidence(pid, [e])


SIG = {"event": "NEW_LIEN", "cls": "WORLD_EVENT", "label": "New City lien", "date": "2026-09-12", "discovered_at": "2026-09-13T03:00",
       "src": "City of Hot Springs", "src_url": "https://services1.arcgis.com/x/FeatureServer/90", "evidence_ref": "evidence:77", "status": "VERIFIED",
       "kind": "verified", "why": "The City spent money here", "taxs": {"st": "UNKNOWN", "src": None}, "sale": {"st": "UNKNOWN", "src": None}}


# ------------------------------------------------------------------ 1, 2, 3, 4, 5, 6, 26

def test_case_created_once_and_signals_keep_provenance(client):
    pid = _prop()
    r = client.post(f"/api/property/{pid}/case", json={"signal": SIG}).json()
    assert r["status"] == "OPEN" and r["origin_cls"] == "WORLD_EVENT" and r["property_id"] == pid
    cid = r["investigation_id"]
    assert [e["cls"] for e in r["events"]][:2] == ["INVESTIGATION OPENED", "SIGNAL RECEIVED"]
    assert r["questions"] and all(q["state"] in ("FOUND", "NOT_FOUND", "UNKNOWN") for q in r["questions"])
    # the same signal again: same case, still one signal, still one opening
    r2 = client.post(f"/api/property/{pid}/case", json={"signal": SIG}).json()
    assert r2["investigation_id"] == cid and len(r2["signals"]) == 1
    assert sum(1 for e in r2["events"] if e["cls"] == "INVESTIGATION OPENED") == 1
    # a second, different signal on the same property joins the same case
    disc = dict(SIG, event="NEW_VACANCY_RECORD", cls="FIRST_DISCOVERY", evidence_ref="evidence:78", src_url="not a url", date="2024-11-20")
    r3 = client.post(f"/api/property/{pid}/case", json={"signal": disc}).json()
    assert r3["investigation_id"] == cid and len(r3["signals"]) == 2
    s1, s2 = r3["signals"]
    assert s1["event"] == "NEW_LIEN" and s1["cls"] == "WORLD_EVENT" and s1["src_url"] == SIG["src_url"] and s1["evidence_ref"] == "evidence:77"
    assert s1["taxs"] == {"st": "UNKNOWN", "src": None} and s1["sale"] == {"st": "UNKNOWN", "src": None} and s1["event_date"] == "2026-09-12"
    assert s2["cls"] == "FIRST_DISCOVERY", "a first discovery is never turned into a world event"
    assert s2["src_url"] is None, "a non-URL is dropped, never invented"
    from hunter import cases
    assert cases.index()["count"] == 1
    assert client.get("/api/cases/index").json()["by_property"][str(pid)]["id"] == cid
    # no signal at all: a person opened it
    pid2 = _prop("700-2")
    m = client.post(f"/api/property/{pid2}/case", json={}).json()
    assert m["origin_cls"] == "MANUAL" and m["signals"][0]["cls"] == "MANUAL"
    assert client.post(f"/api/property/{pid}/case", json={"signal": dict(SIG, cls="INVESTMENT")}).status_code == 400


def test_reopen_on_new_signal_after_close(client):
    pid = _prop("701-1")
    cid = client.post(f"/api/property/{pid}/case", json={"signal": SIG}).json()["investigation_id"]
    assert client.post(f"/api/case/{cid}/status", json={"status": "CLOSED"}).json()["status"] == "CLOSED"
    assert client.post(f"/api/case/{cid}/status", json={"status": "GOOD_DEAL"}).status_code == 400
    r = client.post(f"/api/property/{pid}/case", json={"signal": dict(SIG, event="NEW_CODE_CASE", evidence_ref="evidence:99")}).json()
    assert r["investigation_id"] == cid and r["status"] == "OPEN"
    assert [e for e in r["events"] if e["cls"] == "STATUS CHANGE"][-1]["title"] == "CLOSED → OPEN"


# ------------------------------------------------------------------ 7, 8, 9, 14, 15

def test_three_states_from_evidence_only():
    from hunter import cases
    a = _prop("702-1", "05051", address="1 Found St")
    _ev(a, "cleanup_lien_amount", "1500", raw_ref="lien 2024-17", source_url="https://services1.arcgis.com/x/90")
    b = _prop("702-2", "05051", address="2 Checked St")
    c = _prop("702-3", "05069", address="3 Unknown St")
    for pid in (a, b, c):
        cases.open_or_create(pid, dict(SIG, evidence_ref=f"evidence:{pid}"))
        cases.refresh(cases.case_for_property(pid)["id"], cp={}, hunt=HUNT_CITY if pid != c else HUNT_NONE, inv=NO_INV)
    qa = {q["key"]: q for q in cases.get_case(cases.case_for_property(a)["id"])["questions"]}
    qb = {q["key"]: q for q in cases.get_case(cases.case_for_property(b)["id"])["questions"]}
    qc = {q["key"]: q for q in cases.get_case(cases.case_for_property(c)["id"])["questions"]}
    assert qa["lien_city"]["state"] == "FOUND" and qa["lien_city"]["evidence_refs"] and qa["lien_city"]["source_url"].startswith("https://")
    assert qa["lien_detail"]["state"] == "FOUND" and "1500" in qa["lien_detail"]["answer"]
    assert qb["lien_city"]["state"] == "NOT_FOUND" and qb["lien_city"]["checked_at"] == "2026-09-15" and "City of Hot Springs" in qb["lien_city"]["source"]
    assert qc["lien_city"]["state"] == "UNKNOWN" and qc["lien_city"]["checked_at"] is None, "no register covers Jefferson: unknown, not 'no lien'"
    for qs in (qa, qb, qc):
        assert qs["tax_state"]["state"] == "UNKNOWN" and "never checked" in qs["tax_state"]["answer"]
        assert qs["tax_delinquent"]["state"] == "UNKNOWN"
        assert qs["state_inventory"]["state"] == "UNKNOWN", "no inventory read in this fixture: unknown, never 'not on the list'"
        assert qs["listing"]["state"] == "UNKNOWN" and qs["sale_state"]["state"] == "UNKNOWN"
        assert qs["lien_clerk"]["state"] == "UNKNOWN" and qs["title"]["state"] == "UNKNOWN" and qs["inspection"]["state"] == "UNKNOWN"
    # checklist union: NEW_LIEN keys present; add NEW_TAX_SALE and its keys appear
    assert {"lien_city", "lien_detail", "lien_clerk"} <= set(qa)
    cases.add_signal(cases.case_for_property(a)["id"], {"event": "NEW_TAX_SALE", "cls": "WORLD_EVENT", "evidence_ref": "change:1"})
    qa2 = {q["key"] for q in cases.get_case(cases.case_for_property(a)["id"])["questions"]}
    assert {"state_record", "state_history", "auction", "deed"} <= qa2
    # next actions: a real destination or an explicit MANUAL ACTION REQUIRED, never a fake button
    for q in cases.get_case(cases.case_for_property(c)["id"])["questions"]:
        n = q["next"]
        assert n["label"] and ((n["href"] and not n["manual"]) or (n["href"] is None and n["manual"] and n["label"] == "MANUAL ACTION REQUIRED"))
    assert qc["tax_state"]["next"]["label"] in ("CHECK COLLECTOR", "REQUEST COUNTY RECORD") and qc["tax_state"]["next"]["href"]
    assert qc["owner"]["next"]["label"] == "MANUAL ACTION REQUIRED" and qc["owner"]["next"]["href"] is None, "no online assessor known for Jefferson"
    assert qa["owner"]["next"]["href"].startswith("https://www.actdatascout.com/")
    nx = cases.get_case(cases.case_for_property(c)["id"])["next_action"]
    assert nx["question"] == "tax_state" and nx["href"]


def test_source_unavailable_and_collector_answers(client):
    from hunter import cases, db
    a = _prop("703-1", "05069", address="1 Down St")
    cid = cases.open_or_create(a, SIG)["investigation_id"]
    cases.refresh(cid, cp={"open": False, "down_since": "2026-09-14T14:25:00-0500"}, hunt=HUNT_NONE, inv=NO_INV)
    q = {x["key"]: x for x in cases.get_case(cid)["questions"]}
    assert q["tax_state"]["state"] == "UNKNOWN" and "SOURCE UNAVAILABLE" in q["tax_state"]["answer"]
    # the Collector answers: a current bill is NOT a delinquency; delinquency evidence is NOT_FOUND after a real check
    b = _prop("703-2", "05069", address="2 Bill St")
    db.ex("UPDATE properties SET tax_status='CURRENT_BILL_OPEN' WHERE id=?", (b,))
    _ev(b, "tax_bill", "$66.65 owed to the County Collector (current real estate $66.65)", source="county_tax_collector", source_name="County Tax Collector",
        effective_date="2026-09-15", source_url="https://countypay.ark.org/index.php/jefferson/search")
    cid2 = cases.open_or_create(b, SIG)["investigation_id"]
    cases.refresh(cid2, cp={}, hunt=HUNT_NONE, inv=NO_INV)
    c2 = cases.get_case(cid2)
    q = {x["key"]: x for x in c2["questions"]}
    assert q["tax_state"]["state"] == "FOUND" and "CURRENT BILL OPEN" in q["tax_state"]["answer"] and "$66.65" in q["tax_state"]["answer"]
    assert q["tax_delinquent"]["state"] == "NOT_FOUND" and q["tax_delinquent"]["checked_at"] == "2026-09-15" and q["tax_delinquent"]["source"] == "County Collector"
    assert c2["tax"]["st"] == "CURRENT_BILL_OPEN" and c2["tax"]["amt"] == 66.65 and c2["tax"]["evidence"]
    assert [x for x in c2["checked_no_result"] if x["key"] == "tax_delinquent"]
    # delinquent at the county
    d = _prop("703-3", "05069", address="3 Delinquent St")
    db.ex("UPDATE properties SET tax_status='DELINQUENT' WHERE id=?", (d,))
    _ev(d, "tax_bill", "$212.40 owed to the County Collector (delinquent real estate $212.40)", source="county_tax_collector", source_name="County Tax Collector", effective_date="2026-09-15")
    _ev(d, "tax_delinquent_county", "delinquent at the county: $212.40", source="county_tax_collector", source_name="County Tax Collector", effective_date="2026-09-15")
    _ev(d, "tax_amount_owed_county", "212.40", source="county_tax_collector", source_name="County Tax Collector", effective_date="2026-09-15")
    cid3 = cases.open_or_create(d, SIG)["investigation_id"]
    cases.refresh(cid3, cp={}, hunt=HUNT_NONE, inv=NO_INV)
    c3 = cases.get_case(cid3)
    q = {x["key"]: x for x in c3["questions"]}
    assert c3["tax"]["st"] == "DELINQUENT_VERIFIED" and q["tax_delinquent"]["state"] == "FOUND" and "$212.40" in q["tax_delinquent"]["answer"]


# ------------------------------------------------------------------ 16, 17: shared tax + sale models

def test_state_certified_case_uses_the_one_tax_model_and_state_sale_wording():
    from hunter import cases, db
    import tools.build_share as bs
    a = _prop("704-1", "05051", address="1 Certified St")
    db.ex("UPDATE properties SET tax_status='CERTIFIED_TO_STATE_FOR_SALE' WHERE id=?", (a,))
    _ev(a, "tax_delinquent", "certified to the State", source="cosl_listings", source_name="Arkansas Commissioner of State Lands", effective_date="2026-06-01",
        source_url="https://auction.cosl.org/Auctions/ListingsView?id=1")
    _ev(a, "tax_amount_owed", "1234.50", source="cosl_listings", source_name="Arkansas Commissioner of State Lands")
    cid = cases.open_or_create(a, {"event": "NEW_TAX_SALE", "cls": "WORLD_EVENT", "evidence_ref": "change:5", "src": "Commissioner of State Lands"})["investigation_id"]
    cases.refresh(cid, cp={}, hunt=HUNT_CITY, inv=NO_INV)
    c = cases.get_case(cid)
    row = bs.export_rows(only=[a])[0][0]
    assert c["tax"]["st"] == "TAX_SALE_VERIFIED" == row["taxs"]["st"] and c["tax"]["amt"] == 1234.5 == row["taxs"]["amt"] and c["tax"]["as_of"] == "2026-06-01"
    assert c["sale"]["st"] == "FOR_SALE_BY_STATE" and "TAX SALE" in c["sale"]["text"] and "not a private listing" in c["sale"]["text"] and c["sale"]["evidence"]
    q = {x["key"]: x for x in c["questions"]}
    assert q["tax_delinquent"]["state"] == "FOUND" and q["state_inventory"]["state"] == "FOUND" and q["sale_state"]["state"] == "FOUND"
    assert q["state_record"]["next"]["href"] in ("https://www.cosl.org/",) or q["state_record"]["next"]["href"].startswith("https://auction.cosl.org/")
    # the newer State record wins: redeemed after certification -> no longer for sale by the State
    _ev(a, "tax_redemption", "redeemed", source="cosl_history", source_name="Arkansas Commissioner of State Lands", effective_date="2026-09-10")
    cases.refresh(cid, cp={}, hunt=HUNT_CITY, inv=NO_INV)
    c = cases.get_case(cid)
    assert c["tax"]["st"] != "TAX_SALE_VERIFIED" and c["sale"]["st"] == "UNKNOWN" and "no connected listing source" in c["sale"]["text"]
    assert {x["key"]: x for x in c["questions"]}["state_history"]["state"] == "FOUND"


# ------------------------------------------------------------------ 10, 11, 12, 13, 21, 25

def test_manual_verification_is_labelled_and_never_silently_promoted(client):
    from hunter import cases, db
    a = _prop("705-1", "05069", address="1 Manual St")
    cid = client.post(f"/api/property/{a}/case", json={"signal": SIG}).json()["investigation_id"]
    ev_before = len(cases.get_case(cid)["evidence"])
    # UNKNOWN cannot become NOT_FOUND without a named source and what was searched
    assert client.post(f"/api/case/{cid}/log", json={"question": "lien_clerk", "state": "NOT_FOUND", "result": "nothing"}).status_code == 400
    assert client.post(f"/api/case/{cid}/log", json={"source": "Circuit Clerk", "question": "lien_clerk", "state": "NOT_FOUND"}).status_code == 400
    assert client.post(f"/api/case/{cid}/log", json={"source": "Circuit Clerk", "question": "lien_clerk", "state": "FOUND"}).status_code == 400
    assert client.post(f"/api/case/{cid}/log", json={"source": "Circuit Clerk", "question": "nope", "state": "FOUND", "result": "x"}).status_code == 400
    assert client.post(f"/api/case/{cid}/log", json={"source": "Circuit Clerk", "date": "yesterday", "result": "x"}).status_code == 400
    r = client.post(f"/api/case/{cid}/log", json={"source": "Jefferson County Circuit Clerk (in person)", "date": "2026-09-15", "question": "lien_clerk", "state": "NOT_FOUND",
                                                  "result": "searched the grantor/grantee index for the owner name 2016-2026; no mortgage, judgment or tax lien",
                                                  "document": "clerk receipt 4471", "source_url": "ftp://not-allowed", "notes": "clerk searched by name only"}).json()
    assert r["evidence_ref"].startswith("evidence:") and r["state"] == "NOT_FOUND"
    c = cases.get_case(cid)
    q = {x["key"]: x for x in c["questions"]}["lien_clerk"]
    assert q["state"] == "NOT_FOUND" and q["checked_by"] == "manual" and q["answer"].startswith("MANUAL VERIFICATION:") and q["checked_at"] == "2026-09-15" and q["source_url"] is None
    e = db.q1("SELECT * FROM evidence WHERE id=?", (int(r["evidence_ref"].split(":")[1]),))
    assert e["source"] == "manual_verification" and e["evidence_type"] == "OBSERVATION" and e["confidence"] == "MEDIUM" and e["field"] == "manual:lien_clerk"
    assert "MANUAL VERIFICATION" in e["source_name"] and "clerk receipt 4471" in e["raw_ref"] and e["source_url"] is None
    panel = {x["ref"]: x for x in c["evidence"]}[r["evidence_ref"]]
    assert panel["verification"] == "MANUAL VERIFICATION" and panel["url"] is None
    assert len(c["evidence"]) == ev_before + 1
    assert db.q1("SELECT 1 FROM documents WHERE property_id=? AND category='manual_verification'", (a,))
    kinds = [x["cls"] for x in c["events"]]
    assert kinds.count("MANUAL VERIFICATION") == 1 and "EVIDENCE ADDED" in kinds and "QUESTION ANSWERED" in kinds and "ACTION COMPLETED" in kinds
    # a refresh never downgrades the manual answer to UNKNOWN
    cases.refresh(cid, cp={}, hunt=HUNT_NONE, inv=NO_INV)
    assert {x["key"]: x for x in cases.get_case(cid)["questions"]}["lien_clerk"]["state"] == "NOT_FOUND"
    # a manual FOUND on inspection stays FOUND; recorded source evidence supersedes a manual NOT_FOUND and says so
    client.post(f"/api/case/{cid}/log", json={"source": "drive-by", "date": "2026-09-15", "question": "inspection", "state": "FOUND", "result": "roof intact, mail piling up"})
    client.post(f"/api/case/{cid}/log", json={"source": "City lien map by eye", "date": "2026-09-14", "question": "lien_city", "state": "NOT_FOUND", "result": "no lien polygon seen"})
    _ev(a, "cleanup_lien_amount", "900", source_url="https://services1.arcgis.com/x/90")
    cases.refresh(cid, cp={}, hunt=HUNT_NONE, inv=NO_INV)
    c = cases.get_case(cid)
    q = {x["key"]: x for x in c["questions"]}
    assert q["inspection"]["state"] == "FOUND" and q["inspection"]["checked_by"] == "manual"
    assert q["lien_city"]["state"] == "FOUND" and q["lien_city"]["checked_by"] == "source"
    assert any("supersedes the manual entry" in x["title"] for x in c["events"])
    # a manual UNKNOWN stays the person's UNKNOWN: the evaluator never reads the manual row back as FOUND
    client.post(f"/api/case/{cid}/log", json={"source": "drive-by attempt", "date": "2026-09-16", "question": "access", "state": "UNKNOWN", "result": "gate locked, could not see the road frontage"})
    qa = {x["key"]: x for x in client.get(f"/api/case/{cid}").json()["questions"]}["access"]
    assert qa["state"] == "UNKNOWN" and qa["checked_by"] == "manual" and qa["answer"].startswith("MANUAL VERIFICATION")
    # a manual log without a question is only a log entry: no question changes
    before = {x["key"]: x["state"] for x in client.get(f"/api/case/{cid}").json()["questions"]}
    client.post(f"/api/case/{cid}/log", json={"source": "phone call to Collector", "date": "2026-09-15", "result": "line busy"})
    assert {x["key"]: x["state"] for x in client.get(f"/api/case/{cid}").json()["questions"]} == before


def test_notes_are_notes_not_evidence(client):
    from hunter import cases, db
    a = _prop("706-1", "05069", address="1 Note St")
    cid = client.post(f"/api/property/{a}/case", json={}).json()["investigation_id"]
    before = cases.get_case(cid)
    assert client.post(f"/api/case/{cid}/note", json={"body": "   "}).status_code == 400
    client.post(f"/api/case/{cid}/note", json={"body": "Neighbour says nobody has lived there since 2023."})
    c = cases.get_case(cid)
    assert len(c["notes"]) == 1 and c["notes"][0]["confidence"] == "UNVERIFIED" and c["notes"][0]["kind"] == "investigation"
    assert len(c["evidence"]) == len(before["evidence"]), "a note never becomes an evidence row"
    assert db.q1("SELECT investigation_id FROM notes WHERE id=?", (c["notes"][0]["id"],))["investigation_id"] == cid
    assert c["events"][-1]["cls"] == "NOTE ADDED" and c["events"][-1]["ref"].startswith("note:")
    assert {x["key"]: x["state"] for x in c["questions"]}["inspection"] == "UNKNOWN"


def test_timeline_is_real_and_classified(client):
    from hunter import cases, db
    a = _prop("707-1", "05069", address="1 Time St")
    t0 = db.utcnow()
    c = client.post(f"/api/property/{a}/case", json={"signal": dict(SIG, date="2026-09-01")}).json()
    for e in c["events"]:
        assert e["cls"] in cases.EVENT_CLASSES and e["at"] >= t0 and e["actor"]
    sig_ev = next(e for e in c["events"] if e["cls"] == "SIGNAL RECEIVED")
    assert sig_ev["ref_date"] == "2026-09-01" and sig_ev["ref"] == "evidence:77" and sig_ev["at"] >= t0, "the record date is kept apart from when it was received"
    assert [e["at"] for e in c["events"]] == sorted(e["at"] for e in c["events"])
    db.ex("INSERT INTO investigations(property_id, status, stages_json, summary_json, started_at, finished_at) VALUES(?,?,?,?,?,?)", (a, "complete", "[]", "{}", "2026-09-10T01:00:00", "2026-09-10T01:05:00"))
    c = cases.get_case(c["investigation_id"])
    assert any(e["cls"] == "SOURCE CHECK" and e["ref"].startswith("investigation_run:") and e["at"] == "2026-09-10T01:05:00" for e in c["events"])


# ------------------------------------------------------------------ 22, 23, 18, 19, 20, 24

def test_export_and_public_snapshot(client):
    from hunter import cases
    a = _prop("708-1", "05069", address="1 Export St")
    client.post(f"/api/property/{a}/case", json={"signal": SIG})
    out = cases.export_all()
    assert out["count"] == 1 and out["by_status"] == {"OPEN": 1} and str(a) in out["by_property"] and f"05069:708-1" in out["by_parcel"]
    case = list(out["cases"].values())[0]
    for k in ("investigation_id", "property_id", "status", "created_at", "updated_at", "signals", "questions", "evidence", "findings", "unknowns", "next_action", "notes", "events", "tax", "sale"):
        assert k in case
    json.dumps(out)
    listing = client.get("/api/cases").json()
    assert listing["count"] == 1 and listing["cases"][0]["next_action"]["label"]
    pub = __import__("hunter.cases", fromlist=["export_all"]).export_all()
    assert {"built_at", "count", "by_property", "by_parcel", "cases", "by_status"} <= set(pub)
    for c in pub["cases"].values():
        assert c["status"] in cases.STATUSES
        for q in c["questions"]:
            assert q["state"] in cases.STATES and (q["source_url"] is None or q["source_url"].startswith("http"))
        for s in c["signals"]:
            assert s["cls"] in ("WORLD_EVENT", "FIRST_DISCOVERY", "MANUAL") and (s["src_url"] is None or s["src_url"].startswith("https://"))


def test_pages_offer_investigate_and_show_active_cases():
    look = (DOCS / "lookup.html").read_text()
    assert "PH.investigateBtn" in look and "PH.activeCaseHtml" in look and "PH.loadCaseIndex" in look
    disc = (DOCS / "discover.html").read_text()
    assert "PH.loadCaseIndex" in disc and "PH.sigCard" in disc
    ph = (DOCS / "ph.js").read_text()
    assert "INVESTIGATE PROPERTY" in ph and "OPEN INVESTIGATION" in ph and "investigateBtn(x, x)" in ph
    watch = (DOCS / "watch.html").read_text()
    assert "PH.activeCaseHtml" in watch and "PH.loadCaseIndex" in watch
    idx = (DOCS / "index.html").read_text()
    assert "PH.investigateBtn" in idx
    inv = (DOCS / "investigation.html").read_text()
    for k in ("INVESTIGATION STATUS", "LAST UPDATED", "Why it surfaced", "What we know", "What we don't know", "Evidence", "Questions", "Next action", "Timeline", "Note", "MANUAL ACTION REQUIRED",
              "MANUAL VERIFICATION", "local app is not answering", "/api/case/", "NOT FOUND", "UNKNOWN"):
        assert k in inv, k
    for bad in ("good investment", "great deal", "guaranteed", "buy now"):
        assert bad not in inv.lower()
    nav = (DOCS / "nav.js").read_text()
    assert "investigation.html" in nav
