"""P5 SAFE INVESTIGATION EXECUTION.

    ACCEPTED PROPOSAL -> REVALIDATE -> ONE AUTHORIZED EXISTING CHECK -> SOURCE RESULT -> CANONICAL EVIDENCE
    -> REFRESH QUESTION -> COMPLETE THE PROPOSAL ONLY IF THE EVIDENCE ANSWERS THE QUESTION

There is no general execution capability here. A proposal can only be executed when its question maps to
one of the CHECKS registered below, and each of those is an EXISTING source adapter (hunter/sources/*)
called through its normal `enrich(prop)` entry point, exactly as the scanner and the investigator do.
No URL, no command, no model text is ever executed. One check per action; nothing chains.

A source that is down, blocked, manual-only, times out, errors or answers without a usable reading is a
FAILED or BLOCKED execution: no evidence, no question change, no completion. Only a source result that
passes through store.store_evidence (origin AUTOMATED_SOURCE by P3A classification) and then changes the
question through cases.refresh can complete a proposal.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import bee, cases, db, store
from .db import jdump, jload, utcnow
from .sources import get_source, register_all
from .sources.base import OK

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DATA = os.path.join(ROOT, "docs", "data")
STATES = ("READY", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "STALE_REVIEW_REQUIRED", "REJECTED", "NOT_EXECUTABLE")

# The only checks the dispatcher knows. Each names an existing adapter, the case questions it can answer,
# the P1 registry key that governs it (if any), and where it works.
CHECKS = {
    "COLLECTOR_RECHECK":     {"source": "county_tax_collector", "questions": ("tax_state", "tax_delinquent", "collector_asof"), "registry": "countypay", "label": "County Collector re-check (CountyPay)", "needs": "parcel_id"},
    "STATE_LANDS_RECHECK":   {"source": "cosl_listings", "questions": ("state_inventory", "state_record"), "registry": "state_lands", "label": "State Lands per-parcel re-read", "needs": "rpid"},
    "FEMA_RECHECK":          {"source": "fema_nfhl", "questions": ("flood",), "registry": None, "label": "FEMA flood-zone read at the parcel centroid", "needs": "latlon"},
    "ROAD_RECORD_RECHECK":   {"source": "ar_gis_roads", "questions": ("access",), "registry": None, "label": "State road centreline read near the parcel", "needs": "latlon"},
    "CITY_VACANCY_RECHECK":  {"source": "hs_gis_vacant", "questions": ("vacancy",), "registry": None, "label": "City of Hot Springs vacant-structure register re-read", "needs": "garland"},
    "CITY_LIEN_RECHECK":     {"source": "hs_gis_liens", "questions": ("lien_city", "lien_detail"), "registry": None, "label": "City of Hot Springs lien layer re-read", "needs": "garland"},
    "CITY_CODE_RECHECK":     {"source": "hs_gis_code_cases", "questions": ("code", "code_detail"), "registry": None, "label": "City of Hot Springs code-case layer re-read", "needs": "garland"},
}
NOT_AUTOMATED = {"title": "Circuit Clerk index: no automated source; a person reads it", "lien_clerk": "Circuit Clerk index: no automated source", "deed": "Assessor / Clerk: automated reading is blocked by the operator",
                 "owner": "Assessor: automated reading is blocked by the operator; the roll is re-read by the daily hunt", "mailing_address": "Assessor: automated reading is blocked by the operator",
                 "inspection": "only a person can inspect", "listing": "no listing source is connected", "sale_state": "no listing source is connected", "auction": "no per-parcel auction adapter",
                 "state_history": "monthly State reports are imported by a tool, not re-read per parcel", "values": "the roll is re-read by the daily hunt", "identity": "the roll is re-read by the daily hunt", "other_signals": "not a source check"}


def _registry(cf: str, key: str | None) -> dict:
    if not key:
        return {}
    try:
        d = json.load(open(os.path.join(DOCS_DATA, "tax_sources.json")))
        return (((d.get("counties") or {}).get(cf) or {}).get("sources") or {}).get(key) or {}
    except Exception:
        return {}


def check_for(question_key: str) -> str | None:
    for k, c in CHECKS.items():
        if question_key in c["questions"]:
            return k
    return None


# ------------------------------------------------------------------ revalidation (the guard)

def plan(proposal_id: int) -> dict:
    """Everything that must be true before the button exists. Never touches a source."""
    p = db.q1("SELECT * FROM bee_proposals WHERE id=?", (proposal_id,))
    if not p:
        return {"state": "REJECTED", "ready": False, "reasons": ["no such proposal"]}
    c = db.q1("SELECT * FROM investigation_cases WHERE id=?", (p["case_id"],))
    reasons = []
    if not c:
        return {"state": "REJECTED", "ready": False, "reasons": ["the investigation no longer exists"]}
    prop = store.get_property(c["property_id"])
    if not prop:
        return {"state": "REJECTED", "ready": False, "reasons": ["the property no longer exists"]}
    q = db.q1("SELECT * FROM investigation_questions WHERE case_id=? AND key=?", (p["case_id"], p["question_key"]))
    check = check_for(p["question_key"])
    out = {"proposal_id": proposal_id, "case_id": p["case_id"], "property_id": c["property_id"], "question": p["question_key"], "proposal_status": p["status"],
           "check_type": check, "source": CHECKS[check]["source"] if check else None, "label": CHECKS[check]["label"] if check else None,
           "authorization": {"accepted_by": p["decided_by"], "accepted_at": p["decided_at"]} if p["status"] == "ACCEPTED" else None}
    if not check:
        return dict(out, state="NOT_EXECUTABLE", ready=False, reasons=[f"MANUAL ACTION REQUIRED — {NOT_AUTOMATED.get(p['question_key'], 'no registered automated check for this question')}"])
    if p["status"] == "REJECTED":
        return dict(out, state="REJECTED", ready=False, reasons=["the proposal was rejected by a person"])
    if p["status"] == "COMPLETED":
        return dict(out, state="REJECTED", ready=False, reasons=["the proposal is already completed: recorded evidence answered the question"])
    if p["status"] != "ACCEPTED":
        return dict(out, state="REJECTED", ready=False, reasons=[f"the proposal is {p['status']}; a person must accept it first"])
    if not q:
        return dict(out, state="REJECTED", ready=False, reasons=["the question no longer exists on the case"])
    if q["state"] != "UNKNOWN":
        return dict(out, state="REJECTED", ready=False, reasons=[f"the question is already {q['state']} from recorded evidence"])
    # staleness: the case's evidence fingerprint must be what the person accepted
    snap = bee.snapshot(p["case_id"])
    fp = bee.fingerprint(snap)
    if not p["accepted_fingerprint"]:
        return dict(out, state="STALE_REVIEW_REQUIRED", ready=False, fingerprint=fp, reasons=["accepted before execution tracking existed: reopen the proposal and accept it again so the acceptance covers the current evidence"])
    if p["accepted_fingerprint"] != fp:
        return dict(out, state="STALE_REVIEW_REQUIRED", ready=False, fingerprint=fp, reasons=["the case's evidence or question states changed after this proposal was accepted; review and accept it again"])
    # the candidate must still be one the rules would propose now
    live = {x["proposal_id"] for x in bee.rules(snap)["candidates"]}
    if p["proposal_id"] not in live:
        return dict(out, state="STALE_REVIEW_REQUIRED", ready=False, fingerprint=fp, reasons=["the evidence rules no longer list this check for the case"])
    spec = CHECKS[check]
    src = get_source(spec["source"])
    if not src:
        register_all()
        src = get_source(spec["source"])
    if not src:
        return dict(out, state="NOT_EXECUTABLE", ready=False, reasons=[f"adapter {spec['source']} is not registered"])
    if not src.enabled():
        return dict(out, state="BLOCKED", ready=False, reasons=[f"source {spec['source']} is disabled in the app"])
    need = spec["needs"]
    if need == "parcel_id" and not prop.get("parcel_id"):
        return dict(out, state="BLOCKED", ready=False, reasons=["no parcel number on file"])
    if need == "rpid" and not prop.get("rpid"):
        return dict(out, state="BLOCKED", ready=False, reasons=["no State RPID on file for this parcel; the State search needs it"])
    if need == "latlon" and (prop.get("lat") is None or prop.get("lon") is None):
        return dict(out, state="BLOCKED", ready=False, reasons=["no coordinates on file"])
    if need == "garland" and prop.get("county_fips") != "05051":
        return dict(out, state="BLOCKED", ready=False, reasons=["the City layers cover Hot Springs (Garland County) only"])
    reg = _registry(prop.get("county_fips") or "", spec["registry"])
    rstatus = reg.get("status")
    if rstatus in ("BLOCKED", "MANUAL_ONLY", "NOT_FOUND", "NOT_APPLICABLE"):
        return dict(out, state="BLOCKED", ready=False, registry=reg, reasons=[f"source registry says {rstatus}: {reg.get('failure_reason') or 'not usable by the automated runner'}; MANUAL ACTION REQUIRED"])
    srow = db.q1("SELECT status, status_detail, last_attempt FROM sources WHERE name=?", (spec["source"],))
    probe = rstatus == "TEMPORARILY_UNAVAILABLE" or (srow and srow["status"] == "unavailable")
    return dict(out, state="READY", ready=True, fingerprint=fp, registry=reg, source_health={"status": srow["status"] if srow else None, "detail": srow["status_detail"] if srow else None, "last_attempt": srow["last_attempt"] if srow else None},
                probe_first=bool(probe), reasons=(["the source was last seen unavailable; the adapter's own health check runs first and the check stops if it still does not answer"] if probe else []))


# ------------------------------------------------------------------ one check

def _new_evidence_ids(pid: int, after_id: int) -> list[str]:
    return [f"evidence:{r['id']}" for r in db.q("SELECT id FROM evidence WHERE property_id=? AND id>? ORDER BY id", (pid, after_id))]


def run(proposal_id: int, actor: str = "user") -> dict:
    """Execute exactly one authorized check for one accepted proposal. Returns the execution record."""
    pl = plan(proposal_id)
    p = db.q1("SELECT * FROM bee_proposals WHERE id=?", (proposal_id,))
    now = utcnow()
    q_before = db.q1("SELECT state FROM investigation_questions WHERE case_id=? AND key=?", (p["case_id"], p["question_key"])) if p else None
    base = {"proposal_id": proposal_id, "case_id": p["case_id"] if p else None, "property_id": pl.get("property_id"), "question_key": p["question_key"] if p else None,
            "check_type": pl.get("check_type"), "source": pl.get("source"), "registry_json": jdump(pl.get("registry") or {}), "actor": actor,
            "authorization_json": jdump(pl.get("authorization") or {}), "question_before": q_before["state"] if q_before else None, "proposal_before": p["status"] if p else None}
    if not pl["ready"]:
        eid = _record(dict(base, status=pl["state"], error="; ".join(pl["reasons"]), started_at=now, finished_at=now, question_after=base["question_before"], proposal_after=base["proposal_before"]))
        if p:
            cls = {"BLOCKED": "INVESTIGATION CHECK BLOCKED", "STALE_REVIEW_REQUIRED": "INVESTIGATION CHECK STALE"}.get(pl["state"], "INVESTIGATION CHECK BLOCKED")
            cases._event(p["case_id"], cls, f"{pl.get('label') or p['question_key']}: {pl['state']}", "; ".join(pl["reasons"]), f"execution:{eid}", actor)
            if pl["state"] == "STALE_REVIEW_REQUIRED" and p["status"] == "ACCEPTED":
                db.ex("UPDATE bee_proposals SET status='PROPOSED', human_note=?, updated_at=? WHERE id=?", ("re-accept needed: " + pl["reasons"][0], now, proposal_id))
                db.ex("UPDATE investigation_executions SET proposal_after='PROPOSED' WHERE id=?", (eid,))
            cases._touch(p["case_id"])
        return execution(eid)
    spec = CHECKS[pl["check_type"]]
    src = get_source(spec["source"])
    prop = store.get_property(pl["property_id"])
    eid = _record(dict(base, status="RUNNING", started_at=now))
    cases._event(p["case_id"], "INVESTIGATION CHECK STARTED", f"{spec['label']} for {p['question_key']}", f"authorized by {pl['authorization']['accepted_by']} on {(pl['authorization']['accepted_at'] or '')[:16]}", f"execution:{eid}", actor)
    # a source last seen unavailable is probed with its own health check first; the probe is the adapter's, not ours
    if pl.get("probe_first"):
        try:
            h = src.health_check()
        except Exception as exc:
            h = type("H", (), {"status": "unavailable", "detail": f"{type(exc).__name__}: {exc}", "error": str(exc)})()
        if h.status != OK:
            src.record_attempt(h)
            return _finish(eid, p, "BLOCKED", error=f"source still unavailable: {h.detail or h.error}", actor=actor, label=spec["label"])
    max_ev = (db.q1("SELECT MAX(id) m FROM evidence WHERE property_id=?", (pl["property_id"],)) or {"m": 0})["m"] or 0
    try:
        res = src.enrich(prop)
    except Exception as exc:
        res = None
        err = f"{type(exc).__name__}: {exc}"[:300]
    if res is None:
        try:
            src.record_attempt(type("R", (), {"status": "unavailable", "detail": err, "error": err, "records": []})())
        except Exception:
            pass
        return _finish(eid, p, "FAILED", error=err, actor=actor, label=spec["label"])
    try:
        src.record_attempt(res)
    except Exception:
        pass
    if res.status != OK:
        return _finish(eid, p, "FAILED", error=f"source {res.status}: {res.detail or res.error or 'no detail'}"[:300], actor=actor, label=spec["label"], result_ref=res.detail)
    items = [e for rec in (res.records or []) for e in (getattr(rec, "evidence", None) or []) if isinstance(e, dict) and e.get("field")]
    if not items:
        return _finish(eid, p, "FAILED", error="malformed source result: answered OK but carried no usable reading", actor=actor, label=spec["label"], result_ref=res.detail)
    # the canonical pipeline, exactly as the investigator uses it
    for rec in res.records:
        store.store_evidence(pl["property_id"], rec.evidence)
        if getattr(rec, "fields", None):
            store.set_fields(pl["property_id"], rec.fields, src.name)
        if getattr(rec, "timeline", None):
            store.store_timeline(pl["property_id"], rec.timeline)
    created = _new_evidence_ids(pl["property_id"], max_ev)
    touched = [f"evidence:{r['id']}" for r in db.q("SELECT id FROM evidence WHERE property_id=? AND id<=? AND retrieved_at>=? ORDER BY id", (pl["property_id"], max_ev, now))]
    if created:
        cases._event(p["case_id"], "INVESTIGATION CHECK PRODUCED EVIDENCE", f"{len(created)} reading{'s' if len(created) != 1 else ''} from {src.label}", ", ".join(created), f"execution:{eid}", src.name)
    cases.refresh(p["case_id"])
    q_after = db.q1("SELECT state, checked_by FROM investigation_questions WHERE case_id=? AND key=?", (p["case_id"], p["question_key"]))
    cases._event(p["case_id"], "QUESTION REFRESHED", f"{p['question_key']}: {base['question_before']} → {q_after['state']}", "from recorded evidence through the case refresh, never from the runner", f"question:{p['question_key']}", "property_hunter")
    if q_after["state"] in ("FOUND", "NOT_FOUND"):
        db.ex("UPDATE bee_proposals SET status='COMPLETED', decided_by='evidence', decided_at=?, updated_at=?, human_note=IFNULL(human_note,'') || ' | answered by execution ' || ? WHERE id=?", (utcnow(), utcnow(), str(eid), proposal_id))
        cases._event(p["case_id"], "PROPOSAL COMPLETED", f"{p['proposal_id']}: the question is now {q_after['state']} from recorded evidence", ", ".join(created) or "existing readings re-confirmed", f"bee_proposal:{proposal_id}", "evidence")
        p_after = "COMPLETED"
    else:
        db.ex("UPDATE bee_proposals SET status='EXECUTED_NO_ANSWER', updated_at=? WHERE id=?", (utcnow(), proposal_id))
        p_after = "EXECUTED_NO_ANSWER"
    return _finish(eid, p, "SUCCEEDED", actor=actor, label=spec["label"], result_ref=res.detail, evidence_created=created, evidence_touched=touched, question_after=q_after["state"], proposal_after=p_after)


def _record(d: dict) -> int:
    cols = ("proposal_id", "case_id", "property_id", "question_key", "check_type", "source", "registry_json", "actor", "authorization_json", "status", "error", "started_at", "finished_at",
            "result_ref", "evidence_created_json", "evidence_touched_json", "question_before", "question_after", "proposal_before", "proposal_after")
    cur = db.ex(f"INSERT INTO investigation_executions({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(d.get(k) for k in cols))
    return cur.lastrowid


def _finish(eid: int, p, status: str, *, error: str | None = None, actor="user", label="", result_ref=None, evidence_created=(), evidence_touched=(), question_after=None, proposal_after=None) -> dict:
    row = db.q1("SELECT question_before, proposal_before FROM investigation_executions WHERE id=?", (eid,))
    db.ex("""UPDATE investigation_executions SET status=?, error=?, finished_at=?, result_ref=?, evidence_created_json=?, evidence_touched_json=?, question_after=?, proposal_after=? WHERE id=?""",
          (status, error, utcnow(), (result_ref or "")[:300] or None, jdump(list(evidence_created)), jdump(list(evidence_touched)), question_after or row["question_before"], proposal_after or row["proposal_before"], eid))
    cls = {"SUCCEEDED": "INVESTIGATION CHECK SUCCEEDED", "FAILED": "INVESTIGATION CHECK FAILED", "BLOCKED": "INVESTIGATION CHECK BLOCKED"}[status]
    cases._event(p["case_id"], cls, f"{label}: {status}", error or (result_ref or ""), f"execution:{eid}", actor)
    cases._touch(p["case_id"])
    return execution(eid)


# ------------------------------------------------------------------ read model

def execution(eid: int) -> dict | None:
    r = db.q1("SELECT * FROM investigation_executions WHERE id=?", (eid,))
    if not r:
        return None
    d = dict(r)
    d["registry"] = jload(d.pop("registry_json"), {}); d["authorization"] = jload(d.pop("authorization_json"), {})
    d["evidence_created"] = jload(d.pop("evidence_created_json"), []) or []; d["evidence_touched"] = jload(d.pop("evidence_touched_json"), []) or []
    d["label"] = CHECKS.get(d["check_type"], {}).get("label") if d["check_type"] else None
    d["meaning"] = {"SUCCEEDED": "The source answered. What it established is in the evidence; the question state below is the only verdict.",
                    "FAILED": "The attempted check did not establish the fact. Nothing was recorded as evidence; the question stays as it was.",
                    "BLOCKED": "The check was not attempted: the source is not usable by the automated runner right now.",
                    "STALE_REVIEW_REQUIRED": "The case changed after the proposal was accepted; a person must accept it again.",
                    "REJECTED": "The runner refused: the proposal is not in a state that authorizes a check.", "RUNNING": "In progress.",
                    "NOT_EXECUTABLE": "No registered automated check exists for this question; a person does it."}.get(d["status"], "")
    return d


def executions_for_case(case_id: int) -> list[dict]:
    return [execution(r["id"]) for r in db.q("SELECT id FROM investigation_executions WHERE case_id=? ORDER BY id DESC", (case_id,))]


def public_summary(case_id: int) -> dict:
    by = {}
    for r in db.q("SELECT status, COUNT(*) n FROM investigation_executions WHERE case_id=? GROUP BY status", (case_id,)):
        by[r["status"]] = r["n"]
    last = db.q1("SELECT check_type, source, status, finished_at, evidence_created_json FROM investigation_executions WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,))
    return {"counts_by_status": by, "last": ({"check_type": last["check_type"], "source": last["source"], "status": last["status"], "at": last["finished_at"], "evidence_created": len(jload(last["evidence_created_json"], []) or [])} if last else None),
            "checks_available": sorted(CHECKS), "note": "one accepted proposal, one existing source check, results only through the evidence pipeline"}
