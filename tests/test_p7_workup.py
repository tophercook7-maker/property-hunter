"""P7 — FULL ARKANSAS PROPERTY WORKUP. A resolved P6 identity produces one Property File; every applicable domain is
attempted or classified; evidence only through the P5 execute path; UNKNOWN stays UNKNOWN; source failures stay
source failures; Bee stays advisory; outreach stays human; nothing private is exported; reruns are idempotent."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_record
from test_p5_execution import Stub, _ok, _only
from test_p6_resolver import LINCOLN, MAINS, feat, roll  # noqa: F401
from test_p55_license import Device, _code, admin, client, enforced  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
FEMA_EV = [{"field": "flood_zone", "value": "X (area of minimal flood hazard)", "evidence_type": "FACT", "confidence": "HIGH", "source": "fema_nfhl", "source_name": "FEMA NFHL", "source_url": "https://hazards.fema.gov/x", "effective_date": "2024-05-01"}]
ROAD_EV = [{"field": "road_access", "value": "public road within 12 m - LINCOLN ST", "evidence_type": "OBSERVATION", "confidence": "HIGH", "source": "ar_gis_roads", "source_name": "AR roads", "source_url": "https://gis.arkansas.gov/r", "effective_date": "2025-10-08",
            "raw_ref": "A road on the map is NOT the same as a legal right to use it"}]
CP_EV = [{"field": "tax_bill", "value": "$412.10 owed to the County Collector (2025 real estate)", "evidence_type": "FACT", "confidence": "HIGH", "source": "county_tax_collector", "source_name": "CountyPay", "source_url": "https://countypay.ark.org/garland", "effective_date": "2026-09-16"}]
COSL_EV = [{"field": "tax_status_check", "value": "not held by the Commissioner of State Lands as of this check", "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": "cosl_listings", "source_name": "COSL", "source_url": "https://www.cosl.org/", "effective_date": "2026-09-16"}]
VAC_EV = [{"field": "vacant_structure_check", "value": "not on the City's vacant-structure register", "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": "hs_gis_vacant", "source_name": "City vacancy register", "source_url": "https://gis.cityhs.net/v", "effective_date": "2026-09-16"}]
LIEN_EV = [{"field": "cleanup_lien_check", "value": "no City lien recorded", "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": "hs_gis_liens", "source_name": "City liens", "source_url": "https://gis.cityhs.net/l", "effective_date": "2026-09-16"}]
CODE_EV = [{"field": "code_case_check", "value": "no 2025 code case at this address", "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": "hs_gis_code_cases", "source_name": "City code cases", "source_url": "https://gis.cityhs.net/c", "effective_date": "2026-09-16"}]
MAIL_EV = [{"field": "owner_mailing_address", "value": "PO BOX 77 LITTLE ROCK AR 72201", "evidence_type": "FACT", "confidence": "HIGH", "source": "hs_gis_owner_mailing", "source_name": "City roll copy", "source_url": "https://gis.cityhs.net/m", "effective_date": "2026-03-01"}]
NO_MAIL_EV = [{"field": "owner_mailing_check", "value": "no mailing address on the City's roll copy for this parcel", "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": "hs_gis_owner_mailing", "source_name": "City roll copy", "source_url": "https://gis.cityhs.net/m", "effective_date": "2026-03-01"}]
SOURCES = {"county_tax_collector": CP_EV, "cosl_listings": COSL_EV, "fema_nfhl": FEMA_EV, "ar_gis_roads": ROAD_EV, "hs_gis_vacant": VAC_EV, "hs_gis_liens": LIEN_EV, "hs_gis_code_cases": CODE_EV, "hs_gis_owner_mailing": MAIL_EV}


@pytest.fixture
def stubs(monkeypatch):
    """Every executable adapter answers from a fixture; tests flip one to an outage. No network anywhere."""
    from hunter import execution
    from hunter.sources import get_source as real
    from hunter.sources.base import UNAVAILABLE, SourceResult
    st = {n: Stub(n, result=_ok(n, ev)) for n, ev in SOURCES.items()}
    monkeypatch.setattr(execution, "get_source", lambda n: st.get(n) or real(n))
    monkeypatch.setattr("hunter.cases.collector_status", lambda: {"open": False, "down_since": "2026-09-14T14:25:00", "detail": "Online payments are currently unavailable."})
    monkeypatch.setattr("hunter.cases.state_inventory_ctx", lambda: {"built_at": None, "listings": {}})
    monkeypatch.setattr("hunter.cases.hunt_sources", lambda fips: {"sources": {}, "finished_at": None})      # no hunt has read the City registers in the test world
    st["_unavailable"] = lambda name, detail="did not answer": SourceResult(status=UNAVAILABLE, detail=detail, error=detail)
    return st


def _resolved(rpid="50201"):
    from hunter import db, resolver
    r = resolver.resolve("302 Lincoln St, Hot Springs, AR 71901", actor="Topher")
    assert r["state"] == "EXACT_MATCH"
    if rpid:
        db.ex("UPDATE properties SET rpid=? WHERE id=?", (rpid, r["identity"]["property_id"]))
    return r


def _run(search_id, actor="Topher"):
    from hunter import workup
    w = workup.start(search_id, actor=actor)
    return workup.run(w["workup_id"])


# ------------------------------------------------------------------ 1, 4, 5, 9, 26: the whole thing
def test_resolved_identity_runs_the_full_workup(roll, stubs):
    from hunter import db, workup
    r = _resolved()
    before = db.q1("SELECT count(*) c FROM evidence")["c"]
    w = _run(r["search_id"])
    assert w["status"] == "COMPLETE_WITH_UNKNOWN" and w["version"] == workup.VERSION and w["completed_at"] and w["investigation_id"]
    assert [a["domain"] for a in w["attempts"]] == [d for d, *_ in workup.DOMAINS] and len(w["attempts"]) == 13
    by = {a["domain"]: a for a in w["attempts"]}
    assert by["IDENTITY"]["status"] == "SUCCESS_WITH_EVIDENCE" and by["ROLL_RECORD"]["status"] == "SUCCESS_WITH_EVIDENCE"
    for d in ("TAX_COLLECTOR", "STATE_LANDS", "FLOOD", "ROAD_ACCESS", "MUNICIPAL_VACANCY", "MUNICIPAL_LIEN", "MUNICIPAL_CODE", "OWNER_MAILING"):
        assert by[d]["status"] == "SUCCESS_WITH_EVIDENCE" and by[d]["execution_id"] and by[d]["check_type"], d
    for d in ("TITLE_DEED", "LISTING", "PHYSICAL"):
        assert by[d]["status"] == "MANUAL_ONLY" and by[d]["manual_required"] == 1 and by[d]["execution_id"] is None, d
    assert w["questions"]["mailing_address"]["state"] == "FOUND" and w["questions"]["owner"]["state"] == "FOUND"
    for a in w["attempts"]:
        assert a["status"] in workup.ATTEMPT_STATUSES and a["started_at"] and a["completed_at"] and a["ms"] is not None and a["questions"]["after"] is not None
    # evidence entered through the canonical model, AUTOMATED_SOURCE, with dates
    assert db.q1("SELECT count(*) c FROM evidence")["c"] == before + 8 and len(w["evidence_produced"]) == 8
    for ref in w["evidence_produced"]:
        e = db.q1("SELECT * FROM evidence WHERE id=?", (int(ref.split(":")[1]),))
        assert e["origin"] == "AUTOMATED_SOURCE" and e["source"] in SOURCES and e["effective_date"]
    ex = db.q("SELECT * FROM investigation_executions WHERE workup_id=?", (w["workup_id"],))
    assert len(ex) == 8 and all(x["proposal_id"] is None and x["status"] == "SUCCEEDED" and "requested_by" in x["authorization_json"] for x in ex)
    q = w["questions"]
    assert q["flood"]["state"] == "FOUND" and q["access"]["state"] == "FOUND" and q["vacancy"]["state"] == "NOT_FOUND" and q["lien_city"]["state"] == "NOT_FOUND" and q["code"]["state"] == "NOT_FOUND"
    assert q["state_inventory"]["state"] == "NOT_FOUND" and q["title"]["state"] == "UNKNOWN" and q["inspection"]["state"] == "UNKNOWN" and q["listing"]["state"] == "UNKNOWN"
    cls = [e["cls"] for e in db.q("SELECT cls FROM investigation_events WHERE case_id=? ORDER BY id", (w["investigation_id"],))]
    assert cls.count("WORKUP STARTED") == 1 and cls.count("WORKUP COMPLETED") == 1 and "INVESTIGATION OPENED FROM ADDRESS SEARCH" in cls


# ------------------------------------------------------------------ 2, 30: refusals
def test_unresolved_identity_is_refused(roll, stubs):
    from hunter import resolver, workup
    amb = resolver.resolve("102 Main St")
    assert amb["state"] == "AMBIGUOUS"
    with pytest.raises(ValueError):
        workup.start(amb["search_id"], actor="Topher")
    nm = resolver.resolve("9999 Nowhere Rd, Hot Springs")
    with pytest.raises(ValueError):
        workup.start(nm["search_id"], actor="Topher")
    with pytest.raises(KeyError):
        workup.start(999999, actor="Topher")
    assert all(s.calls == 0 for n, s in stubs.items() if not n.startswith("_"))


# ------------------------------------------------------------------ 22: the Property File and its honest states
def test_property_file_structure_and_honesty(roll, stubs):
    from hunter import db, store, workup
    r = _resolved()
    pid = r["identity"]["property_id"]
    # a historical owner-mailing record from the City's roll copy, dated long before the roll reading
    store.store_evidence(pid, [{"field": "owner_mailing_address", "value": "PO BOX 1 HOT SPRINGS AR 71902", "evidence_type": "FACT", "confidence": "HIGH", "source": "hs_gis_owner_mailing", "source_name": "City roll copy", "effective_date": "2004-01-15"}])
    w = _run(r["search_id"])
    f = workup.property_file(w["workup_id"])
    for k in ("workup", "identity", "what_we_know", "checked_not_found", "tax", "sections", "listing", "what_we_dont_know", "source_failures", "conflicts", "checks_performed", "next_actions", "outreach_gate", "investigation", "timeline", "evidence", "links"):
        assert k in f, k
    idn = f["identity"]
    assert idn["parcel_id"] == "300-06307-000" and idn["county"] == "Garland" and idn["county_fips"] == "05051" and idn["rpid"] == "50201" and idn["verification_state"] == "VERIFIED"
    assert idn["verified_by"] == "AUTOMATED_SOURCE" and idn["source_date"] and idn["read_date"] and idn["evidence_ref"].startswith("evidence:") and idn["lat"] and idn["situs"]
    # WHAT WE KNOW is strict: every line has value + provenance; no sentence about motivation anywhere in the file
    for i in f["what_we_know"]:
        assert i["state"] == "FOUND" and i["value"] is not None and i["source"] and i["ref"] and i["origin"] in ("AUTOMATED_SOURCE", "MANUAL_VERIFICATION") and i["verification"] and i["read_date"], i
    blob = json.dumps(f).lower()
    for bad in ("motivated", "is willing to sell", "clear title", "no liens", "appears to own", "likely vacant", "distressed"):
        assert bad not in blob, bad
    assert "never says an owner wants to sell" in blob and "not saying this is for sale or not for sale" in blob     # the only sale-language present is the disclaimer
    # 15: the mailing record is labelled HISTORICAL with its record date
    mail = next(i for i in f["sections"]["OWNER_MAILING"]["items"] if i["field"] == "owner_mailing_address")
    assert mail["state"] == "FOUND" and mail["historical"] is False and mail["source_date"] == "2026-03-01"          # the live City read supersedes the 2004 copy; the old row stays on file
    assert db.q1("SELECT count(*) c FROM evidence WHERE property_id=? AND field='owner_mailing_address'", (pid,))["c"] == 2
    hist = workup.item(pid, "owner_mailing_address", roll_date="2027-01-01")                                           # a record older than the roll reading is labelled
    assert hist["historical"] is True
    # 14: a road nearby is not legal access
    acc = f["sections"]["ROAD_ACCESS"]
    assert any(i["field"] == "road_access" and i["state"] == "FOUND" for i in acc["items"]) and acc["legal_access"]["state"] == "UNKNOWN" and "deed or plat" in acc["legal_access"]["text"]
    # 16, 17, 18
    assert f["sections"]["TITLE_DEED"]["status"].startswith("TITLE STATUS: UNKNOWN — MANUAL CIRCUIT CLERK REVIEW REQUIRED")
    assert f["listing"]["sale_state"] == "UNKNOWN" and "not saying" in f["listing"]["text"] and f["listing"]["for_sale_by_state"] is False
    assert f["sections"]["PHYSICAL"]["status"].startswith("UNKNOWN")
    # a person's log that chose UNKNOWN is an attempt, not a finding; a road record dated before the roll is not "historical"
    from hunter import cases
    cases.log_manual(w["investigation_id"], {"source": "Drive-by", "result": "could not see the structure from the road", "question": "inspection", "state": "UNKNOWN"}, actor="Topher")
    w2 = _run(r["search_id"]); f2 = workup.property_file(w2["workup_id"])
    assert {a["domain"]: a["status"] for a in w2["attempts"]}["PHYSICAL"] == "MANUAL_ONLY" and f2["sections"]["PHYSICAL"]["status"].startswith("UNKNOWN")
    assert not any(i["field"] == "manual:inspection" and i["state"] == "FOUND" for i in f2["what_we_know"])
    assert next(i for i in f2["sections"]["ROAD_ACCESS"]["items"] if i["field"] == "road_access")["historical"] is False
    # what we don't know lists the open questions; next actions cover them with what / why / where / resolves
    unknown_keys = {q["key"] for q in f["what_we_dont_know"]}
    assert {"title", "deed", "lien_clerk", "inspection", "listing"} <= unknown_keys and "mailing_address" not in unknown_keys     # the City read answers it
    acts = {a["question"]: a for a in f["next_actions"]}
    assert {"title", "deed", "inspection", "listing"} <= set(acts)
    for a in acts.values():
        assert a["what"] and a["why"] and a["resolves"] and a["where"] is not None and a["source_status"]
    assert acts["title"]["source_status"] == "MANUAL_ONLY" and acts["deed"]["where"]["href"].startswith("https://www.actdatascout.com")
    # ledger answers "what did the system actually check" on its own
    assert len(f["checks_performed"]) == 13 and all(a["status"] and a["completed_at"] for a in f["checks_performed"])
    assert f["investigation"]["id"] == w["investigation_id"] and f["links"]["investigation"].endswith(str(w["investigation_id"]))


# ------------------------------------------------------------------ 6, 8, 11, 12, 13, 27: failures stay failures; tax semantics
def test_source_failures_and_tax_semantics(roll, stubs, monkeypatch):
    from hunter import db, execution, workup
    r = _resolved(rpid=None)
    stubs["fema_nfhl"] = Stub("fema_nfhl", exc=ConnectionError("FEMA lookup failed: [Errno 54] Connection reset"))
    stubs["county_tax_collector"] = Stub("county_tax_collector", result=stubs["_unavailable"]("county_tax_collector", "Online payments are currently unavailable."))
    stubs["hs_gis_code_cases"] = Stub("hs_gis_code_cases", result=stubs["_unavailable"]("hs_gis_code_cases", "timed out"))
    w = _run(r["search_id"])
    by = {a["domain"]: a for a in w["attempts"]}
    assert w["status"] == "COMPLETE_WITH_SOURCE_FAILURES"
    assert by["FLOOD"]["status"] == "SOURCE_UNAVAILABLE" and by["FLOOD"]["failure_category"] and w["questions"]["flood"]["state"] == "UNKNOWN"          # 13: never NOT_FOUND
    assert by["TAX_COLLECTOR"]["status"] == "SOURCE_UNAVAILABLE" and by["MUNICIPAL_CODE"]["status"] == "SOURCE_UNAVAILABLE"
    assert by["STATE_LANDS"]["status"] == "BLOCKED" and "RPID" in by["STATE_LANDS"]["failure_detail"] and by["STATE_LANDS"]["execution_id"]        # 8: refused, recorded, not faked
    assert w["questions"]["code"]["state"] == "UNKNOWN" and w["questions"]["state_inventory"]["state"] == "UNKNOWN"
    assert {x["domain"] for x in w["failures"]} == {"FLOOD", "TAX_COLLECTOR", "MUNICIPAL_CODE", "STATE_LANDS"}
    f = workup.property_file(w["workup_id"])
    assert f["tax"]["tax_state"] == "SOURCE_UNAVAILABLE" and f["tax"]["delinquency"] == "UNKNOWN" and f["tax"]["collector"]["status"] == "UNAVAILABLE"   # 11: outage ≠ NOT_FOUND ≠ delinquent
    assert "not a finding" in f["tax"]["meaning"].lower()
    # 12: State Lands "not held" is NOT_FOUND for the inventory but says nothing about the county bill
    r2 = _resolved()
    stubs["fema_nfhl"] = Stub("fema_nfhl", result=_ok("fema_nfhl", FEMA_EV)); stubs["hs_gis_code_cases"] = Stub("hs_gis_code_cases", result=_ok("hs_gis_code_cases", CODE_EV))
    w2 = _run(r2["search_id"])
    f2 = workup.property_file(w2["workup_id"])
    assert w2["questions"]["state_inventory"]["state"] == "NOT_FOUND" and "county bill" in w2["questions"]["state_inventory"]["answer"]
    assert f2["tax"]["tax_state"] == "SOURCE_UNAVAILABLE" and f2["tax"]["delinquency"] == "UNKNOWN" and w2["questions"]["tax_delinquent"]["state"] == "UNKNOWN"
    # a registry that says BLOCKED / MANUAL_ONLY is a classification, never a fake "checked"
    monkeypatch.setattr(execution, "_registry", lambda cf, key: {"status": "MANUAL_ONLY", "failure_reason": "FOIA request only"} if key == "countypay" else {})
    w3 = _run(_resolved()["search_id"])
    tc = next(a for a in w3["attempts"] if a["domain"] == "TAX_COLLECTOR")
    assert tc["status"] == "MANUAL_ONLY" and tc["manual_required"] == 1 and "MANUAL ACTION REQUIRED" in tc["failure_detail"]
    # 27: FAILED when the orchestration itself breaks; nothing pretends to be complete
    monkeypatch.setattr("hunter.workup._finalize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    w4 = _run(_resolved()["search_id"])
    assert w4["status"] == "FAILED" and "boom" in w4["error"]


def test_complete_means_every_domain_classified_not_every_question_answered(roll, stubs):
    from hunter import cases, workup
    r = _resolved()
    w = _run(r["search_id"])
    assert w["status"] == "COMPLETE_WITH_UNKNOWN" and not w["failures"]
    cid = w["investigation_id"]
    for k in [k for k, v in w["questions"].items() if v["state"] == "UNKNOWN"]:
        cases.log_manual(cid, {"source": "Garland County Circuit Clerk (in person)", "result": f"checked {k}", "question": k, "state": "NOT_FOUND"}, actor="Topher")
    w2 = _run(r["search_id"])
    assert w2["status"] == "COMPLETE" and all(v["state"] != "UNKNOWN" for v in w2["questions"].values())
    assert {a["domain"]: a["status"] for a in w2["attempts"]}["TITLE_DEED"] == "SUCCESS_WITH_EVIDENCE"       # a person's dated verification is on file; still no adapter


# ------------------------------------------------------------------ 10, 25: only qualifying evidence answers; conflicts stay
def test_only_qualifying_evidence_answers_and_conflicts_are_preserved(roll, stubs):
    from hunter import db, store, workup
    r = _resolved()
    pid = r["identity"]["property_id"]
    stubs["fema_nfhl"] = Stub("fema_nfhl", exc=ConnectionError("down"))
    store.store_evidence(pid, [{"field": "flood_zone", "value": "AE", "evidence_type": "AI_OPINION", "confidence": "LOW", "source": "local_vision_model", "source_name": "model"},
                              {"field": "flood_zone", "value": "probably X", "evidence_type": "OBSERVATION", "confidence": "LOW", "source": "field_note", "origin": "NOTE"},
                              {"field": "flood_zone", "value": "X", "evidence_type": "ESTIMATE", "confidence": "LOW", "source": "property_hunter.distress", "origin": "DERIVED"}])
    w = _run(r["search_id"])
    assert w["questions"]["flood"]["state"] == "UNKNOWN"
    f = workup.property_file(w["workup_id"])
    assert not any(i["field"] == "flood_zone" and i["state"] == "FOUND" for i in f["what_we_know"])
    # a second automated source disagreeing on the owner is a CONFLICT: both rows stay, precedence shown, nothing deleted
    n_before = db.q1("SELECT count(*) c FROM evidence WHERE property_id=? AND field='owner_name'", (pid,))["c"]
    store.store_evidence(pid, [{"field": "owner_name", "value": "SOMEONE ELSE LLC", "evidence_type": "FACT", "confidence": "HIGH", "source": "hs_gis_owner_mailing", "source_name": "City roll copy", "effective_date": "2004-01-15"}])
    w2 = _run(r["search_id"])
    f2 = workup.property_file(w2["workup_id"])
    cf = next(c for c in w2["conflicts"] if c["field"] == "owner_name")
    assert cf["value_a"] != cf["value_b"] and cf["precedence"]["rule"].startswith("P3A") and cf["unresolved"] and cf["status"] == "NEEDS VERIFICATION"
    assert db.q1("SELECT count(*) c FROM evidence WHERE property_id=? AND field='owner_name'", (pid,))["c"] == n_before + 1
    owner = next(i for i in f2["sections"]["OWNER_MAILING"]["items"] if i["field"] == "owner_name")
    assert owner["conflict"] is True and owner["value"] == "OWNER LLC"       # the 2025 roll reading keeps precedence over a 2004 copy by record date


# ------------------------------------------------------------------ 24: idempotent reruns
def test_rerun_is_idempotent(roll, stubs):
    from hunter import db, workup
    r = _resolved()
    w1 = _run(r["search_id"])
    counts = lambda: {t: db.q1(f"SELECT count(*) c FROM {t}")["c"] for t in ("evidence", "investigation_cases", "investigation_questions", "investigation_executions", "properties", "conflicts")}
    c1 = counts()
    calls1 = {n: s.calls for n, s in stubs.items() if not n.startswith("_")}
    w2 = _run(r["search_id"])
    assert w2["workup_id"] != w1["workup_id"] and counts() == c1                                    # history kept; nothing duplicated
    assert {n: s.calls for n, s in stubs.items() if not n.startswith("_")} == calls1                  # fresh reads reused, sources not hammered
    assert all(a["reused"] == 1 for a in w2["attempts"] if a["check_type"]) and w2["questions"] == w1["questions"] and w2["next_actions"] == w1["next_actions"]
    ev = [e["cls"] for e in db.q("SELECT cls FROM investigation_events WHERE case_id=?", (w1["investigation_id"],))]
    assert ev.count("INVESTIGATION OPENED FROM ADDRESS SEARCH") == 1 and ev.count("INVESTIGATION CHECK STARTED") == 8
    assert workup.latest_for_property(r["identity"]["property_id"])["workup_id"] == w2["workup_id"] and workup.view(w1["workup_id"])["status"] == w1["status"]


# ------------------------------------------------------------------ 19, 20: outreach and Bee stay where they are
def test_outreach_gate_evaluated_without_contact_and_bee_cannot_mutate(roll, stubs):
    from hunter import bee, db, workup
    r = _resolved()
    pid = r["identity"]["property_id"]
    snap_before = bee.snapshot(1) if db.q1("SELECT 1 FROM investigation_cases WHERE id=1") else None
    w = _run(r["search_id"])
    g = w["outreach"]
    assert g["verdict"] in ("OUTREACH NEEDS REVIEW", "OUTREACH READY") and g["evidence_blocking"] == [] and g["purpose"] == "PROPERTY_STATUS_INQUIRY"     # the City read supplied the mailing address; only the person's reason is missing
    assert db.q1("SELECT count(*) c FROM outreach_preps")["c"] == 0 and db.q1("SELECT count(*) c FROM outreach_drafts")["c"] == 0 and db.q1("SELECT count(*) c FROM outreach_actions")["c"] == 0
    assert "No draft was generated" in g["note"]
    # Bee sees the workup's evidence; a model answer that tries to change identity or assert facts is dropped
    cid = w["investigation_id"]
    snap = bee.snapshot(cid)
    assert any(e["field"] == "flood_zone" for e in snap["evidence"]) and snap["property"]["parcel_id"] == "300-06307-000"
    ev_n = db.q1("SELECT count(*) c FROM evidence")["c"]; q_n = db.q("SELECT key, state FROM investigation_questions WHERE case_id=?", (cid,))
    bad = {"summary": "The parcel is really 999-99999-999 and the owner is motivated; title is clear.", "known": [{"question": "title", "text": "clear title"}], "unknown": [], "conflicts": [],
           "proposed_checks": [{"question": "identity", "check": "CHANGE_PARCEL", "source": "made-up", "reason": "trust me"}]}
    out = bee.run(cid, asker=lambda p, system=None, model=None: (bad, {"model": "fake", "error": None, "raw": json.dumps(bad)}))
    assert db.q1("SELECT count(*) c FROM evidence")["c"] == ev_n and db.q("SELECT key, state FROM investigation_questions WHERE case_id=?", (cid,)) == q_n
    assert db.q1("SELECT parcel_id FROM properties WHERE id=?", (pid,))["parcel_id"] == "300-06307-000"
    assert not any(p["question_key"] == "identity" and p["status"] == "ACCEPTED" for p in bee.proposals(cid))
    assert workup.property_file(w["workup_id"])["identity"]["parcel_id"] == "300-06307-000"


# ------------------------------------------------------------------ 3, 28, 29, 30: licensed, narrow, rate limited
def test_workup_api_is_licensed_narrow_and_isolated(client, admin, roll, stubs, monkeypatch):
    from hunter import db, licensing
    monkeypatch.setenv("PH_WORKUP_SYNC", "1")
    for path, m in (("/api/property/workup", "post"), ("/api/property/workup/1", "get"), ("/api/property/workup/1/file", "get"), ("/api/property/1/workup", "get")):
        r = client.post(path, json={"search_id": 1}) if m == "post" else client.get(path)
        assert r.status_code == 401 and r.json()["code"] == "LICENSE_REQUIRED", path
    a, b = Device(), Device()
    assert a.activate(client, _code(client, admin)["code"]).status_code == 200 and b.activate(client, _code(client, admin)["code"]).status_code == 200
    sa = client.post("/api/property/resolve", json={"address": "302 Lincoln St, Hot Springs", "actor": "A"}, headers=a.h()).json()["search_id"]
    calls = lambda: sum(s.calls for n, s in stubs.items() if not n.startswith("_"))
    for bad in ({"search_id": sa, "url": "https://x"}, {"search_id": sa, "source": "countypay"}, {"search_id": sa, "adapter": "fema_nfhl"}, {"search_id": sa, "command": "ls"}, {"property_id": 78}, {"search_id": "abc"}, {}):
        assert client.post("/api/property/workup", json=bad, headers=a.h()).status_code == 400, bad
    assert calls() == 0
    assert client.post("/api/property/workup", json={"search_id": sa, "actor": "B"}, headers=b.h()).status_code == 404       # 3: not B's search
    w = client.post("/api/property/workup", json={"search_id": sa, "actor": "A"}, headers=a.h())
    assert w.status_code == 200 and w.json()["status"].startswith("COMPLETE")
    wid = w.json()["workup_id"]
    assert client.get(f"/api/property/workup/{wid}", headers=b.h()).status_code == 404 and client.get(f"/api/property/workup/{wid}/file", headers=b.h()).status_code == 404
    assert client.get(f"/api/property/workup/{wid}/file", headers=a.h()).status_code == 200
    pid = w.json()["property_id"]
    assert client.get(f"/api/property/{pid}/workup", headers=b.h()).status_code == 404 and client.get(f"/api/property/{pid}/workup", headers=a.h()).json()["workup_id"] == wid
    # 28: the P5.5 limiter
    monkeypatch.setitem(licensing.LIMITS, "workup", (2, 600)); licensing.reset_rate_limits()
    codes = [client.post("/api/property/workup", json={"search_id": sa}, headers=a.h()).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    licensing.reset_rate_limits()
    # 29: revoked
    lic_a = client.get("/api/license/me", headers=a.h()).json()["license_id"]
    licensing.revoke(lic_a, "test")
    assert client.post("/api/property/workup", json={"search_id": sa}, headers=a.h()).status_code == 401 and client.get(f"/api/property/workup/{wid}/file", headers=a.h()).status_code == 401
    blob = json.dumps(client.get(f"/api/property/workup/{wid}/file", headers=b.h()).json())
    assert "PH-" not in blob and "refresh_token" not in blob
    licensing.reset_rate_limits()


# ------------------------------------------------------------------ 21, 22, 23: one execution path, no network, nothing public
def test_no_second_network_path_and_nothing_public():
    src = (ROOT / "hunter" / "workup.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+(requests|urllib|httpx|aiohttp|socket|subprocess|shlex|pty)\b", src, re.M) and "eval(" not in src and "exec(" not in src and "os.system" not in src
    assert "arcgis_query" not in src and "http." not in src.replace("https://", "") and "def enrich" not in src and "p7_fetch" not in src
    assert "execution.execute(" in src and "execution.check_guard(" in src and "from .sources" not in src           # P5's path is the only executable path
    ex = (ROOT / "hunter" / "execution.py").read_text()
    assert set(re.findall(r'^    "([A-Z_]+_RECHECK)":', ex, re.M)) == {"COLLECTOR_RECHECK", "STATE_LANDS_RECHECK", "FEMA_RECHECK", "ROAD_RECORD_RECHECK", "CITY_VACANCY_RECHECK", "CITY_LIEN_RECHECK", "CITY_CODE_RECHECK", "OWNER_MAILING_RECHECK"}
    api = (ROOT / "hunter" / "api.py").read_text()
    assert "/api/property/workup" not in api.split("PUBLIC_API = ")[1].split("\n")[0]
    share = (ROOT / "tools" / "build_share.py").read_text() + (ROOT / "tools" / "publish_scan.py").read_text()
    assert "workup" not in share.lower()
    for f in (ROOT / "docs" / "data").glob("*.json"):
        t = f.read_text()[:3_000_000]
        assert "workup_attempts" not in t and "checks_performed" not in t and "what_we_dont_know" not in t, f
    ui = (ROOT / "docs" / "property-file.html").read_text() + (ROOT / "docs" / "find.html").read_text()
    assert "PH.apiFetch(" in ui and "arcgis" not in ui.lower() and "fetch('http" not in ui
    assert "workup" not in (ROOT / "hunter" / "bee.py").read_text()
