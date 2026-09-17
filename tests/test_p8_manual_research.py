"""P8 — MANUAL RESEARCH INTAKE & DOCUMENT EVIDENCE. A P7 gap becomes one actionable task; a person's typed result
becomes MANUAL_VERIFICATION evidence through cases.log_manual; questions move only through cases.refresh / the
person's own answer; observations stay observations; NOT_FOUND needs a source that answered; conflicts keep both
sides; outreach is re-evaluated and never sent; Bee cannot submit; nothing private is exported."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_p5_execution import Stub, _ok
from test_p6_resolver import LINCOLN, MAINS, feat, roll  # noqa: F401
from test_p7_workup import SOURCES, stubs, _resolved, _run  # noqa: F401
from test_p55_license import Device, _code, admin, client, enforced  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_mailing_on_city_copy(stubs):
    """P8 tests exercise the manual mailing-address path: the City copy answers 'no mailing address' (a dated reading)."""
    from test_p7_workup import NO_MAIL_EV
    stubs["hs_gis_owner_mailing"] = Stub("hs_gis_owner_mailing", result=_ok("hs_gis_owner_mailing", NO_MAIL_EV))


DEED = {"record_source": "TEST RECORD — Garland County Circuit Clerk (walkthrough)", "instrument_number": "TEST-2026-000001", "book_page": "TEST 1/1", "record_date": "2026-01-15",
        "grantor": "TEST GRANTOR", "grantee": "TEST GRANTEE LLC", "legal_reference": "PT SW SE (test)", "observations": "TEST RECORD only; not a real deed"}


def _workup_with_tasks():
    r = _resolved()
    w = _run(r["search_id"])
    from hunter import research
    return r, w, research.for_property(r["identity"]["property_id"])


def _task(mr, key):
    return next(t for t in mr["tasks"] if t["question_key"] == key)


# ------------------------------------------------------------------ 1, 2, 24, 25: tasks from real gaps, one active per question
def test_tasks_come_from_real_gaps_and_never_duplicate(roll, stubs):
    from hunter import db, research, workup
    r, w, mr = _workup_with_tasks()
    keys = {t["question_key"] for t in mr["tasks"]}
    domain_q = {k for _, _, _, ks, _ in workup.DOMAINS for k in ks}
    unknown = {k for k, v in w["questions"].items() if v["state"] == "UNKNOWN" and k in domain_q}       # other_signals is not a source check, so no task
    assert keys == unknown and {"title", "deed", "lien_clerk", "mailing_address", "listing", "inspection"} <= keys       # 1: every gap, only gaps
    assert not any(t["question_key"] == "flood" for t in mr["tasks"])                                                       # answered questions get no task
    for t in mr["tasks"]:
        assert t["status"] == "OPEN" and t["title"] and t["what_to_check"] and t["why"] and t["what_would_resolve"] and t["where"] is not None and t["created_from_workup_id"] == w["workup_id"]
        assert t["kind"] == research.KIND_FOR[t["question_key"]] and t["state_before"] == "UNKNOWN"
    n = len(mr["tasks"])
    _run(r["search_id"])                                                                                                       # 2: a second workup adds nothing
    assert db.q1("SELECT count(*) c FROM research_tasks")["c"] == n
    # a source that later answers the question closes the open task as SKIPPED by the system, never COMPLETED
    from test_p7_workup import MAIL_EV
    stubs["hs_gis_owner_mailing"] = Stub("hs_gis_owner_mailing", result=_ok("hs_gis_owner_mailing", MAIL_EV))
    import hunter.workup as _wk; _orig = _wk._fresh_execution; _wk._fresh_execution = lambda pid, check: None       # force a re-read (the earlier read is seconds old)
    try:
        w3 = _run(r["search_id"])
    finally:
        _wk._fresh_execution = _orig
    mt = db.q1("SELECT status, actor, notes FROM research_tasks WHERE property_id=? AND question_key='mailing_address'", (r["identity"]["property_id"],))
    assert w3["questions"]["mailing_address"]["state"] == "FOUND" and mt["status"] == "SKIPPED" and mt["actor"] == "property_hunter" and "no person did this task" in mt["notes"]
    assert db.q1("SELECT count(*) c FROM research_tasks")["c"] == n
    t = _task(mr, "title")
    assert research.open_for_question(t["case_id"], "title", actor="Topher")["task_id"] == t["task_id"]                     # CLOSE THIS GAP reuses the active task
    with pytest.raises(ValueError):
        research.open_for_question(t["case_id"], "flood", actor="Topher")                                                    # nothing to close on an answered question
    # 24: skipped / blocked stay historical; a new task can then be opened for the same question
    s = research.skip(t["task_id"], actor="Topher", reason="Clerk closed today")
    assert s["status"] == "SKIPPED" and s["completed_at"]
    b = research.skip(_task(mr, "deed")["task_id"], actor="Topher", reason="index offline", blocked=True)
    assert b["status"] == "BLOCKED" and b["completed_at"] is None
    t2 = research.open_for_question(t["case_id"], "title", actor="Topher")
    assert t2["task_id"] != t["task_id"] and research.view(t["task_id"])["status"] == "SKIPPED"
    assert db.q1("SELECT count(*) c FROM research_tasks WHERE question_key='title'")["c"] == 2
    cls = [e["cls"] for e in db.q("SELECT cls FROM investigation_events WHERE case_id=?", (t["case_id"],))]                  # 25
    assert cls.count("RESEARCH TASK OPENED") == n + 1 and "RESEARCH TASK SKIPPED" in cls and "RESEARCH TASK BLOCKED" in cls
    # a file rendered after the tasks exist links each next action to its task
    f = workup.property_file(w["workup_id"])
    assert all(a.get("task_id") for a in f["next_actions"] if a["question"] not in ("title", "deed", "mailing_address")) and f["manual_research"]["counts"]["open"] >= 1


# ------------------------------------------------------------------ 4, 5, 9, 10, 14, 15, 16, 17, 23: the deed intake
def test_deed_found_is_fact_evidence_and_never_clear_title(roll, stubs):
    from hunter import cases, db, research, store
    r, w, mr = _workup_with_tasks()
    pid, cid = r["identity"]["property_id"], w["investigation_id"]
    t = _task(mr, "deed")
    s = research.start(t["task_id"], actor="Topher")                                                                          # 4
    assert s["status"] == "IN_PROGRESS" and s["started_at"] and "RESEARCH TASK STARTED" in [e["cls"] for e in db.q("SELECT cls FROM investigation_events WHERE case_id=?", (cid,))]
    ev_before = db.q1("SELECT count(*) c FROM evidence")["c"]
    old_unknown_events = db.q1("SELECT count(*) c FROM investigation_events WHERE case_id=? AND cls='QUESTION REMAINS UNKNOWN'", (cid,))["c"]
    done = research.complete(t["task_id"], {"result": "FOUND", "source": "TEST RECORD — Garland County Circuit Clerk (walkthrough)", "date": "2026-09-16", "fields": DEED, "evidence_type": "FACT", "confidence": "HIGH",
                                            "document": {"doc_type": "DEED", "title": "TEST RECORD — warranty deed (walkthrough, not a real document)", "source": "TEST RECORD", "record_date": "2026-01-15", "instrument": "TEST-2026-000001", "book_page": "TEST 1/1", "local_reference": "test-only; no file"},
                                            "notes": "TEST RECORD entered for the P8 walkthrough"}, actor="Topher")
    assert done["status"] == "COMPLETED" and done["result_state"] == "FOUND" and done["evidence_type"] == "FACT" and done["confidence"] == "HIGH"
    # 5: MANUAL_VERIFICATION rows typed FACT, with the structured fields on the raw ref, through the existing manual path
    refs = done["evidence_refs"]
    assert len(refs) == 2                                                                                                   # deed record + grantee as owner of record
    for ref in refs:
        e = db.q1("SELECT * FROM evidence WHERE id=?", (int(ref.split(":")[1]),))
        assert e["origin"] == "MANUAL_VERIFICATION" and e["source"] == "manual_verification" and e["evidence_type"] == "FACT" and e["confidence"] == "HIGH" and "TEST-2026-000001" in e["raw_ref"] and e["effective_date"] == "2026-09-16"
    assert db.q1("SELECT count(*) c FROM evidence")["c"] == ev_before + 2
    # 10: deed → deed FOUND and owner FOUND (grantee); title stays UNKNOWN; nothing says "clear"
    qs = {q["key"]: q for q in cases.get_case(cid)["questions"]}
    assert qs["deed"]["state"] == "FOUND" and qs["deed"]["checked_by"] == "manual" and qs["owner"]["state"] == "FOUND" and "TEST GRANTEE LLC" in qs["owner"]["answer"] and "not a title opinion" in qs["owner"]["answer"]
    assert qs["title"]["state"] == "UNKNOWN"
    assert "clear" not in json.dumps(cases.get_case(cid)["questions"]).lower()
    # 9: the document reference is attached to the property, the task and the evidence — reference only, private
    d = db.q1("SELECT * FROM documents WHERE task_id=?", (t["task_id"],))
    assert d and d["doc_type"] == "DEED" and d["instrument"] == "TEST-2026-000001" and d["property_id"] == pid and d["private"] == 1 and d["evidence_id"] == int(refs[0].split(":")[1]) and d["actor"] == "Topher"
    assert done["document_id"] == d["id"] and done["documents"][0]["title"].startswith("TEST RECORD")
    # 15: the old UNKNOWN stays in history; 14: the transition is on the timeline from the person's answer
    ev = db.q("SELECT cls, title FROM investigation_events WHERE case_id=? ORDER BY id", (cid,))
    cls = [e["cls"] for e in ev]
    assert db.q1("SELECT count(*) c FROM investigation_events WHERE case_id=? AND cls='QUESTION REMAINS UNKNOWN'", (cid,))["c"] == old_unknown_events
    assert "RESEARCH TASK COMPLETED" in cls and "QUESTION ANSWERED" in cls and "MANUAL VERIFICATION" in cls and "EVIDENCE ADDED" in cls
    assert done["state_before"] == "UNKNOWN" and done["state_after"] == "FOUND"
    # 16, 17: the gate was re-evaluated; nothing was drafted or sent
    assert done["outreach"]["verdict"] in ("OUTREACH BLOCKED", "OUTREACH NEEDS REVIEW", "OUTREACH READY FOR HUMAN REVIEW") and "OUTREACH GATE RE-EVALUATED" in cls
    assert db.q1("SELECT count(*) c FROM outreach_preps")["c"] == 0 and db.q1("SELECT count(*) c FROM outreach_drafts")["c"] == 0 and db.q1("SELECT count(*) c FROM outreach_actions")["c"] == 0
    # 23: completing again is idempotent
    again = research.complete(t["task_id"], {"result": "FOUND", "source": "x", "fields": DEED}, actor="Topher")
    assert again.get("idempotent") is True and db.q1("SELECT count(*) c FROM evidence")["c"] == ev_before + 2 and db.q1("SELECT count(*) c FROM documents")["c"] == 1
    # the automated roll owner is untouched and now sits beside the person's reading with a conflict flag only if they differ by field rules
    assert store.latest_answer(pid, "owner_name")["value"] == "OWNER LLC"
    # a title search actually run answers title
    t3 = research.open_for_question(cid, "title", actor="Topher")
    d3 = research.complete(t3["task_id"], {"result": "FOUND", "source": "TEST RECORD — title company", "fields": dict(DEED, title_search_performed="yes", title_search_result="TEST RECORD: no exceptions listed on the test report")}, actor="Topher")
    assert {q["key"]: q["state"] for q in cases.get_case(cid)["questions"]}["title"] == "FOUND" and d3["state_after"] == "FOUND"


# ------------------------------------------------------------------ 6, 7, 8, 26: NOT_FOUND, UNKNOWN, CONFLICTING
def test_not_found_unknown_and_conflicting_semantics(roll, stubs):
    from hunter import cases, db, research
    r, w, mr = _workup_with_tasks()
    pid, cid = r["identity"]["property_id"], w["investigation_id"]
    lien = _task(mr, "lien_clerk")
    with pytest.raises(ValueError):                                                                                           # 6: NOT_FOUND needs the source that answered
        research.complete(lien["task_id"], {"result": "NOT_FOUND"}, actor="Topher")
    with pytest.raises(ValueError):
        research.complete(lien["task_id"], {"result": "NOT_FOUND", "source": "Clerk index"}, actor="Topher")            # ...and what was searched / returned
    d = research.complete(lien["task_id"], {"result": "NOT_FOUND", "source": "TEST RECORD — Clerk index (walkthrough)", "fields": {"record_source": "Clerk index", "observations": "searched owner name and parcel; index returned no instruments (TEST)"}}, actor="Topher")
    assert d["result_state"] == "NOT_FOUND" and {q["key"]: q["state"] for q in cases.get_case(cid)["questions"]}["lien_clerk"] == "NOT_FOUND"
    e = db.q1("SELECT * FROM evidence WHERE id=?", (d["result_evidence_id"],))
    assert e["origin"] == "MANUAL_VERIFICATION" and "no matching instrument" in e["value"]
    # 7: UNKNOWN answers nothing
    mail = _task(mr, "mailing_address")
    u = research.complete(mail["task_id"], {"result": "UNKNOWN", "source": "Assessor site", "notes": "record page would not load"}, actor="Topher")
    assert u["result_state"] == "UNKNOWN" and {q["key"]: q["state"] for q in cases.get_case(cid)["questions"]}["mailing_address"] == "UNKNOWN"
    assert db.q1("SELECT evidence_type FROM evidence WHERE id=?", (u["result_evidence_id"],))["evidence_type"] == "OBSERVATION"
    # 8, 26: CONFLICTING keeps both records, answers nothing, and the automated reading stays
    with pytest.raises(ValueError):
        research.open_for_question(cid, "owner", actor="Topher")                                   # answered: no gap to close...
    own = research.open_for_question(cid, "owner", actor="Topher", purpose="CONFLICT")               # ...but a disagreeing record can be recorded
    with pytest.raises(ValueError):
        research.complete(own["task_id"], {"result": "CONFLICTING", "source": "x", "fields": {"owner_name": "SOMEONE ELSE"}}, actor="Topher")       # conflict_with required
    c = research.complete(own["task_id"], {"result": "CONFLICTING", "source": "TEST RECORD — county card", "fields": {"owner_name": "TEST OTHER OWNER", "record_source": "county card", "record_date": "2026-02-01"},
                                           "conflict_with": "State roll owner OWNER LLC dated 2025-10-31"}, actor="Topher")
    assert c["result_state"] == "CONFLICTING" and {q["key"]: q["state"] for q in cases.get_case(cid)["questions"]}["owner"] == "FOUND"      # the roll's answer stands; nothing overwritten
    cf = db.q1("SELECT * FROM conflicts WHERE property_id=? AND field='manual:owner'", (pid,))
    assert cf and cf["value_a"] == "OWNER LLC" and "TEST OTHER OWNER" in cf["value_b"] and cf["status"] == "NEEDS VERIFICATION" and cf["source_a"] == "ar_gis_parcels"
    assert db.q1("SELECT count(*) c FROM evidence WHERE property_id=? AND field='owner_name'", (pid,))["c"] == 1
    assert "CONFLICT RECORDED" in [e["cls"] for e in db.q("SELECT cls FROM investigation_events WHERE case_id=?", (cid,))]
    from hunter import workup
    f = workup.property_file(w["workup_id"])
    assert any(x["field"] == "manual:owner" for x in f["manual_research"]["conflicts"])


# ------------------------------------------------------------------ 11, 12, 13: mailing, inspection, listing
def test_mailing_inspection_and_listing_semantics(roll, stubs):
    from hunter import cases, db, research, store
    r, w, mr = _workup_with_tasks()
    pid, cid = r["identity"]["property_id"], w["investigation_id"]
    # 11: a mailing address of record never touches the situs and is dated
    m = research.complete(_task(mr, "mailing_address")["task_id"], {"result": "FOUND", "source": "TEST RECORD — Assessor card", "fields": {"record_source": "Assessor card", "mailing_address": "TEST PO BOX 9 HOT SPRINGS AR 71902", "record_date": "2026-03-01"}}, actor="Topher")
    assert m["result_state"] == "FOUND" and store.get_property(pid)["address"] == "302 Lincoln St"
    q = {x["key"]: x for x in cases.get_case(cid)["questions"]}
    assert q["mailing_address"]["state"] == "FOUND" and "TEST PO BOX 9" in q["mailing_address"]["answer"] and "not assumed current" in q["mailing_address"]["answer"]
    assert store.latest_answer(pid, "manual:mailing_address")["evidence_type"] == "FACT"
    # 12: an inspection is always an OBSERVATION, even if the person asks for FACT; nothing says vacant / distressed
    insp = _task(mr, "inspection")
    with pytest.raises(ValueError):
        research.complete(insp["task_id"], {"result": "FOUND", "source": "street", "fields": {"visible_structure": "house"}, "evidence_type": "FACT"}, actor="Topher")
    i = research.complete(insp["task_id"], {"result": "FOUND", "source": "TEST RECORD — drive-by (walkthrough)", "date": "2026-09-16",
                                            "fields": {"visible_structure": "single-storey frame house visible from Lincoln St", "apparent_occupancy_indicators": "no vehicle; curtains drawn; mail slot taped (TEST)", "exterior_condition": "peeling paint on south side", "posted_notice": "none visible", "visible_address": "302 on the door", "apparent_access": "from Lincoln St", "photographs": "photo refs TEST-1, TEST-2"}}, actor="Topher")
    assert i["evidence_type"] == "OBSERVATION"
    e = db.q1("SELECT * FROM evidence WHERE id=?", (i["result_evidence_id"],))
    assert e["evidence_type"] == "OBSERVATION" and e["origin"] == "MANUAL_VERIFICATION" and "photographs: photo refs TEST-1" in e["raw_ref"]
    blob = json.dumps(cases.get_case(cid)["questions"]).lower()
    assert "vacant = true" not in blob and "distress" not in blob and "abandon" not in blob
    assert q["inspection"]["state"] == "UNKNOWN" and {x["key"]: x["state"] for x in cases.get_case(cid)["questions"]}["inspection"] == "FOUND"
    assert store.get_property(pid).get("state") != "VACANT"
    # 13: a listing search with nothing returned is NOT_FOUND for that source and day, never NOT_FOR_SALE; a found listing needs a URL or id
    lst = _task(mr, "listing")
    n = research.complete(lst["task_id"], {"result": "NOT_FOUND", "source": "TEST RECORD — public listing search", "fields": {"source_checked": "listing site A", "date_checked": "2026-09-16", "search_result": "no listing returned for 302 Lincoln St (TEST)"}}, actor="Topher")
    qq = {x["key"]: x for x in cases.get_case(cid)["questions"]}
    assert n["result_state"] == "NOT_FOUND" and qq["listing"]["state"] == "NOT_FOUND" and "Not 'not for sale'" in qq["listing"]["answer"] and qq["sale_state"]["state"] == "UNKNOWN"
    assert cases.sale_state(cases.property_row(pid))["st"] == "UNKNOWN"
    t2 = research.open_for_question(cid, "sale_state", actor="Topher")
    with pytest.raises(ValueError):
        research.complete(t2["task_id"], {"result": "FOUND", "source": "site B", "fields": {"source_checked": "site B", "listing_status": "active"}}, actor="Topher")


# ------------------------------------------------------------------ 18: Bee cannot submit; 3, 19, 20, 28, 29: API
def test_bee_cannot_submit_and_api_is_licensed_and_narrow(client, admin, roll, stubs, monkeypatch):
    from hunter import bee, db, licensing, research
    monkeypatch.setenv("PH_WORKUP_SYNC", "1")
    for path, m in (("/api/property/1/research", "get"), ("/api/property/research/tasks/1", "get"), ("/api/property/research/tasks/1/start", "post"), ("/api/property/research/tasks/1/complete", "post"), ("/api/property/research/tasks/1/skip", "post"), ("/api/case/1/research/open", "post")):
        r = client.post(path, json={"actor": "x"}) if m == "post" else client.get(path)
        assert r.status_code == 401 and r.json()["code"] == "LICENSE_REQUIRED", path
    a, b = Device(), Device()
    assert a.activate(client, _code(client, admin)["code"]).status_code == 200 and b.activate(client, _code(client, admin)["code"]).status_code == 200
    sa = client.post("/api/property/resolve", json={"address": "302 Lincoln St, Hot Springs", "actor": "A"}, headers=a.h()).json()["search_id"]
    w = client.post("/api/property/workup", json={"search_id": sa, "actor": "A"}, headers=a.h()).json()
    pid = w["property_id"]
    mr = client.get(f"/api/property/{pid}/research", headers=a.h()).json()
    tid = next(t["task_id"] for t in mr["tasks"] if t["question_key"] == "deed")
    # 20: another license sees none of it
    assert client.get(f"/api/property/{pid}/research", headers=b.h()).json()["tasks"] == []
    assert client.get(f"/api/property/research/tasks/{tid}", headers=b.h()).status_code == 404
    assert client.post(f"/api/property/research/tasks/{tid}/complete", json={"actor": "B", "result": "FOUND", "source": "x", "fields": DEED}, headers=b.h()).status_code == 404
    # 19, 28: injection and malformed requests are refused before anything is written
    ev_n = db.q1("SELECT count(*) c FROM evidence")["c"]
    for bad in ({"actor": "A", "result": "FOUND", "source": "x", "fields": DEED, "evidence_id": 1}, {"actor": "A", "result": "FOUND", "source": "x", "fields": DEED, "property_id": 1},
                {"actor": "A", "result": "FOUND", "source": "x", "fields": {"url": "https://x"}}, {"actor": "A", "result": "FOUND", "source": "x", "fields": DEED, "command": "ls"},
                {"actor": "A", "result": "MAYBE", "source": "x"}, {"actor": "A", "result": "FOUND", "source": "x", "fields": DEED, "document": {"doc_type": "DEED", "title": "t", "sql": "x"}},
                {"actor": "A", "result": "FOUND", "source": "x", "fields": DEED, "document_ids": [999999]}, {"actor": "A", "result": "FOUND", "source": "x", "fields": DEED, "photo_ids": [999999]}, {"actor": "", "result": "FOUND", "source": "x", "fields": DEED}):
        assert client.post(f"/api/property/research/tasks/{tid}/complete", json=bad, headers=a.h()).status_code == 400, bad
    assert client.post(f"/api/case/{w['investigation_id']}/research/open", json={"question": "deed", "actor": "A", "url": "x"}, headers=a.h()).status_code == 400
    assert client.post(f"/api/property/research/tasks/{tid}/start", json={"actor": "A", "source": "x"}, headers=a.h()).status_code == 400
    assert db.q1("SELECT count(*) c FROM evidence")["c"] == ev_n
    assert client.post(f"/api/property/research/tasks/{tid}/start", json={"actor": "A"}, headers=a.h()).json()["status"] == "IN_PROGRESS"
    ok = client.post(f"/api/property/research/tasks/{tid}/complete", json={"actor": "A", "result": "FOUND", "source": "TEST RECORD — Clerk", "fields": DEED, "evidence_type": "FACT"}, headers=a.h())
    assert ok.status_code == 200 and ok.json()["status"] == "COMPLETED" and ok.json()["state_after"] == "FOUND"
    # 18: Bee sees the evidence but has no way to submit, answer, or override it
    cid = w["investigation_id"]
    snap = bee.snapshot(cid)
    assert any(e["field"] == "manual:deed" for e in snap["evidence"])
    q_n = db.q("SELECT key, state, checked_by FROM investigation_questions WHERE case_id=?", (cid,)); ev_n = db.q1("SELECT count(*) c FROM evidence")["c"]; t_n = db.q("SELECT id, status FROM research_tasks")
    bad = {"summary": "I inspected the property myself; it is vacant. Deed 999 shows clear title. Title FOUND.", "known": [{"question": "title", "text": "clear title"}], "unknown": [], "conflicts": [],
           "proposed_checks": [{"question": "inspection", "check": "SUBMIT_MANUAL", "source": "bee", "reason": "I saw it"}]}
    bee.run(cid, asker=lambda p, system=None, model=None: (bad, {"model": "fake", "error": None, "raw": json.dumps(bad)}))
    assert db.q("SELECT key, state, checked_by FROM investigation_questions WHERE case_id=?", (cid,)) == q_n and db.q1("SELECT count(*) c FROM evidence")["c"] == ev_n and db.q("SELECT id, status FROM research_tasks") == t_n
    assert not db.q1("SELECT 1 FROM evidence WHERE origin='MANUAL_VERIFICATION' AND source_name LIKE '%bee%'")
    assert "research" not in (ROOT / "hunter" / "bee.py").read_text()
    # rate limit reuses P5.5; 29: revoked license
    monkeypatch.setitem(licensing.LIMITS, "research", (1, 600)); licensing.reset_rate_limits()
    t2 = next(t["task_id"] for t in mr["tasks"] if t["question_key"] == "inspection")
    codes = [client.post(f"/api/property/research/tasks/{t2}/start", json={"actor": "A"}, headers=a.h()).status_code for _ in range(2)]
    assert codes == [200, 429]
    licensing.reset_rate_limits()
    licensing.revoke(client.get("/api/license/me", headers=a.h()).json()["license_id"], "test")
    assert client.get(f"/api/property/{pid}/research", headers=a.h()).status_code == 401
    licensing.reset_rate_limits()


# ------------------------------------------------------------------ 21, 22, 27, 30: static: no network, nothing public, UI wired, additive schema
def test_no_network_nothing_public_ui_wired_schema_additive():
    src = (ROOT / "hunter" / "research.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+(requests|urllib|httpx|aiohttp|socket|subprocess|shlex|pty)\b", src, re.M) and "eval(" not in src and "exec(" not in src and "os.system" not in src
    assert "arcgis" not in src and "from .sources" not in src and "execution." not in src and "http.get" not in src
    assert "cases.log_manual(" in src and "cases.refresh(" in src and "store.store_evidence(" not in src        # evidence only through the existing manual path
    api = (ROOT / "hunter" / "api.py").read_text()
    assert "/api/property/research" not in api.split("PUBLIC_API = ")[1].split("\n")[0]
    share = (ROOT / "tools" / "build_share.py").read_text() + (ROOT / "tools" / "publish_scan.py").read_text()
    assert "research_tasks" not in share and "documents" not in share
    for f in (ROOT / "docs" / "data").glob("*.json"):
        t = f.read_text()[:3_000_000]
        assert "research_tasks" not in t and "intake_json" not in t and "TEST RECORD" not in t and "manual:" not in t, f
    ui = (ROOT / "docs" / "research.html").read_text()
    pf = (ROOT / "docs" / "property-file.html").read_text()
    assert "PH.apiFetch(" in ui and "arcgis" not in ui.lower() and "fetch('http" not in ui and "/complete" in ui and "/skip" in ui and "intake_schema" in ui        # 27
    assert "Close this gap" in pf and "research.html?task=" in pf and "manual_research" in pf and "/research/open" in pf
    db_src = (ROOT / "hunter" / "db.py").read_text()
    assert "CREATE TABLE IF NOT EXISTS research_tasks" in db_src and 'ensure_column(conn, "documents", "doc_type"' in db_src        # 30
    assert "DROP TABLE" not in db_src.split("CREATE TABLE IF NOT EXISTS research_tasks")[1] and "ALTER TABLE documents DROP" not in db_src
    ev_src = (ROOT / "hunter" / "cases.py").read_text()
    assert '"evidence_type": etype' in ev_src and 'or "OBSERVATION").upper()' in ev_src                                          # the P2 default is unchanged


def test_public_timeline_keeps_the_fact_not_the_content(roll, stubs):
    """22: the public timeline export (docs/data/timeline) says a person verified something on a date, never what."""
    import importlib, sys
    sys.path.insert(0, str(ROOT / "tools"))
    bs = importlib.import_module("build_share")
    from hunter import db, research
    r, w, mr = _workup_with_tasks()
    pid = r["identity"]["property_id"]
    research.complete(_task(mr, "deed")["task_id"], {"result": "FOUND", "source": "TEST RECORD — Clerk", "fields": DEED}, actor="Topher")
    research.complete(_task(mr, "mailing_address")["task_id"], {"result": "FOUND", "source": "TEST RECORD — Assessor", "fields": {"record_source": "Assessor", "mailing_address": "TEST PO BOX 9 HOT SPRINGS AR 71902"}}, actor="Topher")
    tl = bs.build_timelines({pid: {"cf": "05051"}})
    blob = json.dumps(tl)
    assert "TEST GRANTEE" not in blob and "TEST PO BOX" not in blob and "TEST-2026" not in blob and "Topher" not in blob
    assert blob.count("Manual verification recorded by a person") >= 3 and "private to the license holder" in blob


def test_documents_and_photos_must_belong_to_the_property(roll, stubs):
    from hunter import db, research
    r, w, mr = _workup_with_tasks()
    pid = r["identity"]["property_id"]
    other = db.ex("INSERT INTO properties(canonical_key, created_at, updated_at, first_seen, last_seen, county_fips, parcel_id) VALUES('x','2026','2026','2026','2026','05051','OTHER-1')").lastrowid
    did = db.ex("INSERT INTO documents(property_id, category, title, added_at) VALUES(?,?,?,?)", (other, "other", "not mine", "2026")).lastrowid
    with pytest.raises(ValueError):
        research.complete(_task(mr, "deed")["task_id"], {"result": "FOUND", "source": "x", "fields": DEED, "document_ids": [did]}, actor="Topher")
    mine = db.ex("INSERT INTO documents(property_id, category, title, added_at) VALUES(?,?,?,?)", (pid, "other", "TEST RECORD scan", "2026")).lastrowid
    d = research.complete(_task(mr, "deed")["task_id"], {"result": "FOUND", "source": "TEST RECORD", "fields": DEED, "document_ids": [mine]}, actor="Topher")
    assert db.q1("SELECT task_id FROM documents WHERE id=?", (mine,))["task_id"] == d["task_id"] and mine in json.loads(db.q1("SELECT document_ids_json FROM research_tasks WHERE id=?", (d["task_id"],))["document_ids_json"])
