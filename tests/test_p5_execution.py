"""P5 safe investigation execution: an ACCEPTED proposal runs exactly one registered existing adapter check;
the guard refuses everything else; a failed, blocked, timed-out or malformed source creates no evidence and
leaves the question UNKNOWN; a real result becomes AUTOMATED_SOURCE evidence through the canonical pipeline
and can answer FOUND or NOT_FOUND; the proposal completes only when the evidence answers the question."""
import json
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


def _prop(parcel, county="05051", rpid=None, lat=34.5, lon=-93.05):
    from hunter import store, db
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, address=f"{parcel} Run St", owner_name="POE, EDGAR", lat=lat, lon=lon))
    for field, value in (("owner_name", "POE, EDGAR"), ("parcel_id", parcel)):
        if not store.latest_evidence(pid, field):
            store.store_evidence(pid, [{"field": field, "value": value, "evidence_type": "FACT", "confidence": "HIGH", "source": "ar_gis_parcels", "source_name": "ar_gis_parcels", "effective_date": "2025-10-31"}])
    if rpid:
        db.ex("UPDATE properties SET rpid=? WHERE id=?", (rpid, pid))
    return pid


def _accepted(cid, key):
    """Bee proposes from the rules; a person accepts the proposal for `key`."""
    from hunter import bee
    snap = bee.snapshot(cid); r = bee.rules(snap)
    bee.run(cid, asker=lambda p, system=None, model=None: ({"summary": "s", "known": [], "unknown": [], "conflicts": [], "proposed_checks": []}, {"model": "fake", "error": None, "raw": "{}"}))
    pid = next(p["id"] for p in bee.proposals(cid) if p["question_key"] == key)
    bee.decide(pid, "ACCEPT", actor="topher")
    return pid


def _only(stub, name):
    """Route one source to the stub; every other source stays the real registered adapter."""
    from hunter.sources import get_source as real
    return lambda n: stub if n == name else real(n)


class Stub:
    """A stand-in for an existing adapter: same contract (name, label, enabled, health_check, enrich, record_attempt)."""
    def __init__(self, name, result=None, exc=None, health=None):
        from hunter.sources import get_source
        real = get_source(name)
        self.name, self.label, self.url = name, real.label if real else name, real.url if real else ""
        self._result, self._exc, self._health = result, exc, health
        self.calls = 0
    def enabled(self): return True
    def health_check(self): return self._health or self._result
    def record_attempt(self, res, changed=0): pass
    def ev(self, field, value, **kw): return dict(field=field, value=value, evidence_type=kw.get("etype", "FACT"), confidence=kw.get("confidence", "HIGH"), source=self.name, source_name=self.label, source_url=kw.get("source_url"), effective_date=kw.get("effective_date"), raw_ref=kw.get("raw_ref"))
    def enrich(self, prop, **kw):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._result


def _ok(name, evidence, fields=None, detail="ok"):
    from hunter.sources.base import OK, Record, SourceResult
    return SourceResult(status=OK, detail=detail, records=[Record(source=name, identity={}, fields=fields or {}, evidence=evidence)])


@pytest.fixture(autouse=True)
def _registered():
    from hunter.sources import register_all
    register_all()


# ------------------------------------------------------------------ 1, 11, 12, 13, 16, 17, 24: a real adapter contract, FOUND and NOT_FOUND

def test_accepted_collector_check_runs_once_and_evidence_answers(monkeypatch, client):
    from hunter import bee, cases, execution, db, store
    import hunter.sources.countypay as cp
    pid = _prop("1100-1"); cid = cases.open_or_create(pid, SIG)["investigation_id"]
    prop_id = _accepted(cid, "tax_state")
    pl = execution.plan(prop_id)
    assert pl["check_type"] == "COLLECTOR_RECHECK" and pl["source"] == "county_tax_collector" and pl["authorization"]["accepted_by"] == "topher"
    # the registry says the Collector is down for Garland today -> READY but probe-first (the adapter's own health check)
    stub = Stub("county_tax_collector", result=_ok("county_tax_collector", [
        {"field": "tax_status_check", "value": "no open real-estate tax bill on the Collector's payment site", "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": "county_tax_collector", "source_name": "County Tax Collector", "source_url": f"{cp.BASE}/garland/search", "effective_date": "2026-09-16"}], detail="no open tax bill at the Collector"))
    monkeypatch.setattr(execution, "get_source", _only(stub, "county_tax_collector"))
    before = db.q1("SELECT COUNT(*) n FROM evidence")["n"]
    r = client.post(f"/api/bee/proposal/{prop_id}/run", json={"actor": "topher"}).json()
    assert r["status"] == "SUCCEEDED" and stub.calls == 1 and r["check_type"] == "COLLECTOR_RECHECK" and r["actor"] == "topher"
    assert r["authorization"] == {"accepted_by": "topher", "accepted_at": pl["authorization"]["accepted_at"]}
    assert r["evidence_created"] and db.q1("SELECT COUNT(*) n FROM evidence")["n"] == before + 1
    e = store.with_origin(dict(db.q1("SELECT * FROM evidence WHERE id=?", (int(r["evidence_created"][0].split(":")[1]),))))
    assert e["origin"] == "AUTOMATED_SOURCE" and e["source"] == "county_tax_collector" and e["evidence_type"] == "OBSERVATION" and e["source_url"].startswith("https://countypay.ark.org/") and e["effective_date"] == "2026-09-16"
    # the question moved only through the case refresh, from that evidence: tax_state FOUND (CURRENT_VERIFIED), tax_delinquent NOT_FOUND
    assert r["question_before"] == "UNKNOWN" and r["question_after"] == "FOUND" and r["proposal_before"] == "ACCEPTED" and r["proposal_after"] == "COMPLETED"
    qs = {q["key"]: q for q in cases.get_case(cid)["questions"]}
    assert qs["tax_state"]["state"] == "FOUND" and qs["tax_state"]["checked_by"] == "source" and "CURRENT" in qs["tax_state"]["answer"]
    assert qs["tax_delinquent"]["state"] == "NOT_FOUND"
    p = next(x for x in bee.proposals(cid) if x["id"] == prop_id)
    assert p["status"] == "COMPLETED" and p["decided_by"] == "evidence"
    ev = [e["cls"] for e in cases.get_case(cid)["events"]]
    for k in ("INVESTIGATION CHECK STARTED", "INVESTIGATION CHECK PRODUCED EVIDENCE", "QUESTION REFRESHED", "PROPOSAL COMPLETED", "INVESTIGATION CHECK SUCCEEDED"):
        assert k in ev, k
    # a completed proposal cannot run again
    again = client.post(f"/api/bee/proposal/{prop_id}/run", json={}).json()
    assert again["status"] == "REJECTED" and "already completed" in again["error"] and stub.calls == 1
    # a second identical reading later: the dedup keeps one row and refreshes retrieved_at; no duplicate, no deletion
    n_before = db.q1("SELECT COUNT(*) n FROM evidence WHERE property_id=? AND field='tax_status_check'", (pid,))["n"]
    store.store_evidence(pid, stub._result.records[0].evidence)
    assert db.q1("SELECT COUNT(*) n FROM evidence WHERE property_id=? AND field='tax_status_check'", (pid,))["n"] == n_before


# ------------------------------------------------------------------ 2, 3, 5, 6, 15, 21, 22: the guard

def test_guard_refuses_everything_that_is_not_an_accepted_registered_check(client):
    from hunter import bee, cases, execution
    pid = _prop("1101-1"); cid = cases.open_or_create(pid, SIG)["investigation_id"]
    bee.run(cid, asker=lambda p, system=None, model=None: ({"summary": "s", "known": [], "unknown": [], "conflicts": [], "proposed_checks": []}, {"model": "fake", "error": None, "raw": "{}"}))
    props = {p["question_key"]: p for p in bee.proposals(cid)}
    # not accepted
    r = client.post(f"/api/bee/proposal/{props['tax_state']['id']}/run", json={}).json()
    assert r["status"] == "REJECTED" and "accept it first" in r["error"] and r["evidence_created"] == []
    # rejected
    bee.decide(props["tax_state"]["id"], "REJECT")
    assert execution.plan(props["tax_state"]["id"])["state"] == "REJECTED"
    # no registered check for title / inspection / listing: manual action required, never executable
    for key in ("title", "inspection", "listing", "lien_clerk"):
        pl = execution.plan(props[key]["id"])
        assert pl["state"] == "NOT_EXECUTABLE" and pl["ready"] is False and "MANUAL ACTION REQUIRED" in pl["reasons"][0]
    bee.decide(props["title"]["id"], "ACCEPT")
    assert execution.plan(props["title"]["id"])["state"] == "NOT_EXECUTABLE"
    # the runner takes no url / command / check type from the caller
    for bad in ({"url": "https://example.com"}, {"command": "ls"}, {"check_type": "SHELL"}):
        assert client.post(f"/api/bee/proposal/{props['title']['id']}/run", json=bad).status_code == 400
    assert client.post("/api/bee/proposal/999999/run", json={}).status_code == 404
    # nothing in the runner can execute anything but the registered adapters
    src = Path(execution.__file__).read_text()
    for bad in ("subprocess", "os.system", "webbrowser", "httpx", "requests", "urllib", "eval(", "exec(", "importlib"):
        assert bad not in src, bad
    assert set(execution.CHECKS) == {"COLLECTOR_RECHECK", "STATE_LANDS_RECHECK", "FEMA_RECHECK", "ROAD_RECORD_RECHECK", "CITY_VACANCY_RECHECK", "CITY_LIEN_RECHECK", "CITY_CODE_RECHECK", "OWNER_MAILING_RECHECK"}
    # Bee's text is never the authorization: a proposal with a made-up where/url still maps only through its question
    from hunter import db
    db.ex("UPDATE bee_proposals SET where_json=? WHERE id=?", (json.dumps({"label": "GO HERE", "href": "https://evil.example/run", "manual": False}), props["flood"]["id"]))
    bee.decide(props["flood"]["id"], "ACCEPT")
    pl = execution.plan(props["flood"]["id"])
    assert pl["check_type"] == "FEMA_RECHECK" and pl["source"] == "fema_nfhl"


# ------------------------------------------------------------------ 4: staleness

def test_stale_acceptance_requires_review(client):
    from hunter import bee, cases, execution, store, db
    pid = _prop("1102-1"); cid = cases.open_or_create(pid, SIG)["investigation_id"]
    prop_id = _accepted(cid, "flood")
    assert execution.plan(prop_id)["state"] == "READY"
    # evidence changes after acceptance -> stale; the run is refused and recorded; the proposal goes back for re-acceptance
    store.store_evidence(pid, [{"field": "cleanup_lien_amount", "value": "900", "evidence_type": "FACT", "confidence": "HIGH", "source": "hs_gis_liens", "effective_date": "2026-09-16"}])
    pl = execution.plan(prop_id)
    assert pl["state"] == "STALE_REVIEW_REQUIRED" and "changed after this proposal was accepted" in pl["reasons"][0]
    r = client.post(f"/api/bee/proposal/{prop_id}/run", json={}).json()
    assert r["status"] == "STALE_REVIEW_REQUIRED" and r["evidence_created"] == [] and r["proposal_after"] == "PROPOSED"
    assert db.q1("SELECT status FROM bee_proposals WHERE id=?", (prop_id,))["status"] == "PROPOSED"
    assert any(e["cls"] == "INVESTIGATION CHECK STALE" for e in cases.get_case(cid)["events"])
    # an acceptance from before fingerprints existed is also stale
    bee.decide(prop_id, "ACCEPT")
    db.ex("UPDATE bee_proposals SET accepted_fingerprint=NULL WHERE id=?", (prop_id,))
    assert execution.plan(prop_id)["state"] == "STALE_REVIEW_REQUIRED"
    # re-accept against the current evidence -> ready again
    bee.decide(prop_id, "ACCEPT")
    assert execution.plan(prop_id)["state"] == "READY"


# ------------------------------------------------------------------ 7, 8, 9, 10, 14, 16: failures create nothing

def test_failures_and_blocks_create_no_evidence_and_leave_unknown(monkeypatch, client):
    from hunter import bee, cases, execution, db
    from hunter.sources.base import SourceResult, UNAVAILABLE, OK, Record
    pid = _prop("1103-1"); cid = cases.open_or_create(pid, SIG)["investigation_id"]
    prop_id = _accepted(cid, "flood")
    before = db.q1("SELECT COUNT(*) n FROM evidence")["n"]
    def with_stub(stub):
        monkeypatch.setattr(execution, "get_source", _only(stub, "fema_nfhl"))
        r = client.post(f"/api/bee/proposal/{prop_id}/run", json={"actor": "topher"}).json()
        assert db.q1("SELECT COUNT(*) n FROM evidence")["n"] == before, "no evidence from a failed or blocked check"
        assert db.q1("SELECT state FROM investigation_questions WHERE case_id=? AND key='flood'", (cid,))["state"] == "UNKNOWN"
        assert db.q1("SELECT status FROM bee_proposals WHERE id=?", (prop_id,))["status"] == "ACCEPTED", "a failed check does not resolve or complete the proposal"
        return r
    r = with_stub(Stub("fema_nfhl", result=SourceResult(status=UNAVAILABLE, detail="FEMA NFHL did not answer", error="timeout")))
    assert r["status"] == "FAILED" and "did not answer" in r["error"] and r["question_after"] == "UNKNOWN"
    r = with_stub(Stub("fema_nfhl", exc=TimeoutError("read timed out")))
    assert r["status"] == "FAILED" and "TimeoutError" in r["error"]
    r = with_stub(Stub("fema_nfhl", result=SourceResult(status=OK, detail="weird", records=[Record(source="fema_nfhl", evidence=[{"value": "no field"}])])))
    assert r["status"] == "FAILED" and "malformed" in r["error"]
    # a source last seen unavailable is probed first; a failing probe blocks without calling enrich
    db.ex("UPDATE sources SET status='unavailable' WHERE name='fema_nfhl'")
    stub = Stub("fema_nfhl", result=_ok("fema_nfhl", [{"field": "flood_zone", "value": "X", "evidence_type": "FACT", "confidence": "HIGH", "source": "fema_nfhl"}]), health=SourceResult(status=UNAVAILABLE, detail="still down"))
    assert execution.plan(prop_id)["probe_first"] is True
    r = with_stub(stub)
    assert r["status"] == "BLOCKED" and "still unavailable" in r["error"] and stub.calls == 0
    db.ex("UPDATE sources SET status='ok' WHERE name='fema_nfhl'")
    # a registry-blocked source never runs (Collector marked BLOCKED for this county)
    prop2 = _accepted(cid, "tax_state")
    monkeypatch.setattr(execution, "_registry", lambda cf, key: {"status": "BLOCKED", "failure_reason": "answers automation with 403"} if key == "countypay" else {})
    pl = execution.plan(prop2)
    assert pl["state"] == "BLOCKED" and "MANUAL ACTION REQUIRED" in pl["reasons"][0]
    r = client.post(f"/api/bee/proposal/{prop2}/run", json={}).json()
    assert r["status"] == "BLOCKED" and db.q1("SELECT COUNT(*) n FROM evidence")["n"] == before
    ev = [e["cls"] for e in cases.get_case(cid)["events"]]
    assert ev.count("INVESTIGATION CHECK FAILED") == 3 and ev.count("INVESTIGATION CHECK BLOCKED") == 2 and "INVESTIGATION CHECK SUCCEEDED" not in ev
    assert not any("SENT" in c for c in ev)
    # a source that answers but does not settle the question: EXECUTED_NO_ANSWER, not COMPLETED
    monkeypatch.setattr(execution, "_registry", lambda cf, key: {})
    stub = Stub("fema_nfhl", result=_ok("fema_nfhl", [{"field": "flood_note", "value": "layer answered, no zone polygon at the point", "evidence_type": "OBSERVATION", "confidence": "LOW", "source": "fema_nfhl"}]))
    monkeypatch.setattr(execution, "get_source", _only(stub, "fema_nfhl"))
    r = client.post(f"/api/bee/proposal/{prop_id}/run", json={}).json()
    assert r["status"] == "SUCCEEDED" and r["question_after"] == "UNKNOWN" and r["proposal_after"] == "EXECUTED_NO_ANSWER"
    assert db.q1("SELECT status FROM bee_proposals WHERE id=?", (prop_id,))["status"] == "EXECUTED_NO_ANSWER"
    # and a real flood reading later answers FOUND
    db.ex("UPDATE bee_proposals SET status='ACCEPTED' WHERE id=?", (prop_id,)); bee.decide(prop_id, "ACCEPT")
    stub = Stub("fema_nfhl", result=_ok("fema_nfhl", [{"field": "flood_zone", "value": "X (minimal flood hazard)", "evidence_type": "FACT", "confidence": "HIGH", "source": "fema_nfhl", "source_url": "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"}], fields={"flood_zone": "X (minimal flood hazard)"}))
    monkeypatch.setattr(execution, "get_source", _only(stub, "fema_nfhl"))
    r = client.post(f"/api/bee/proposal/{prop_id}/run", json={}).json()
    assert r["status"] == "SUCCEEDED" and r["question_after"] == "FOUND" and r["proposal_after"] == "COMPLETED"


# ------------------------------------------------------------------ 18, 19, 20, 23: integrity, outreach, privacy

def test_state_lands_negative_is_not_paid_and_outreach_and_privacy_untouched(monkeypatch, client):
    from hunter import bee, cases, execution, db, outreach
    pid = _prop("1104-1", rpid="115601")
    from hunter import store
    store.store_evidence(pid, [{"field": "owner_mailing_address", "value": "7 RAVEN CT, HOT SPRINGS AR 71901", "evidence_type": "FACT", "confidence": "HIGH", "source": "hs_gis_owner_mailing", "effective_date": "2026-08-01"}])
    cid = cases.open_or_create(pid, SIG)["investigation_id"]
    prep = client.post(f"/api/case/{cid}/outreach", json={"purpose": "PROPERTY_STATUS_INQUIRY", "reason": "asking"}).json()
    d = client.post(f"/api/outreach/{prep['id']}/draft", json={"sender_name": "T", "contact": "t@example.com"}).json()
    draft_before = [(x["version"], x["text"]) for x in d["drafts"]]
    # state_inventory is NOT_FOUND from the daily inventory in this repo; make it UNKNOWN for the test by hiding the inventory
    monkeypatch.setattr(cases, "state_inventory_ctx", lambda: {"built_at": None, "listings": {}})
    cases.refresh(cid)
    prop_id = _accepted(cid, "state_inventory")
    pl = execution.plan(prop_id)
    assert pl["check_type"] == "STATE_LANDS_RECHECK" and pl["state"] == "READY"
    stub = Stub("cosl_listings", result=_ok("cosl_listings", [{"field": "tax_status_check", "value": "not held by the Commissioner of State Lands as of this check - so not 2+ years delinquent; the county Collector alone knows if the current year is paid", "evidence_type": "OBSERVATION", "confidence": "HIGH", "source": "cosl_listings", "source_name": "Arkansas Commissioner of State Lands", "source_url": "https://www.cosl.org/Home/SearchByParcel", "effective_date": "2026-09-16"}], detail="not certified to the State (checked COSL)"))
    monkeypatch.setattr(execution, "get_source", _only(stub, "cosl_listings"))
    hist_before = db.q1("SELECT COUNT(*) n FROM evidence")["n"]
    r = client.post(f"/api/bee/proposal/{prop_id}/run", json={}).json()
    assert r["status"] == "SUCCEEDED" and r["evidence_created"]
    c = cases.get_case(cid)
    qs = {q["key"]: q for q in c["questions"]}
    assert c["tax"]["st"] == "UNKNOWN" and qs["tax_state"]["state"] == "UNKNOWN", "not held by the State never becomes paid or current"
    assert c["tax"].get("cosl_check") or any(e["field"] == "tax_status_check" for e in c["evidence"])
    assert db.q1("SELECT COUNT(*) n FROM evidence")["n"] == hist_before + 1 and db.q1("SELECT COUNT(*) n FROM evidence WHERE property_id=? AND field='owner_mailing_address'", (pid,))["n"] == 1
    # outreach untouched
    after = outreach.get_prep(prep["id"])
    assert [(x["version"], x["text"]) for x in after["drafts"]] == draft_before and after["status"] == "DRAFTED" and len(outreach.for_case(cid)) == 1
    src = Path(execution.__file__).read_text()
    for bad in ("outreach", "generate_draft", "notify", "smtp", "SENT", "approvals"):
        assert bad not in src, bad
    # public snapshot: execution metadata only
    out = cases.export_all()
    s = json.dumps(out)
    assert "RAVEN CT" not in s and "t@example.com" not in s
    b = out["cases"][str(cid)]["bee"]["executions"]
    assert b["counts_by_status"]["SUCCEEDED"] == 1 and b["last"]["check_type"] == "STATE_LANDS_RECHECK" and b["last"]["source"] == "cosl_listings" and b["last"]["evidence_created"] == 1
    assert set(b) == {"counts_by_status", "last", "checks_available", "note"}
    for e in out["cases"][str(cid)]["events"]:
        if e["cls"].startswith("INVESTIGATION CHECK") or e["cls"] in ("QUESTION REFRESHED", "PROPOSAL COMPLETED"):
            assert "RAVEN" not in (e.get("detail") or "")
    live = __import__("hunter.cases", fromlist=["export_all"]).export_all()
    for cc in live["cases"].values():
        assert "executions" in cc["bee"]
    page = (DOCS / "investigation.html").read_text()
    for k in ("RUN CHECK", "READY TO RUN", "STALE — REVIEW REQUIRED", "MANUAL ACTION REQUIRED", "Authorization:", "question ", "proposal "):
        assert k in page, k
