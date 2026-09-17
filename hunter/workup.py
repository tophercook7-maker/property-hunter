"""P7 — FULL ARKANSAS PROPERTY WORKUP.

    RESOLVED IDENTITY (P6) → ONE WORKUP → every applicable domain attempted, classified or refused
    → evidence only through the canonical pipeline (P5 execute) → case questions refreshed
    → source-attempt ledger → next actions → outreach gate → ONE PROPERTY FILE

Responsibilities that stay where they are: P6 owns identity; P5 owns the one path that runs an adapter
check (execution.execute behind execution.check_guard); evidence owns the record; cases own state; outreach
owns contact preparation; Bee owns reasoning. This module orchestrates and renders. It has no network code,
no adapter of its own, no AI call, and no way to name a source that is not in execution.CHECKS.

COMPLETE means every applicable domain was attempted or explicitly classified (NOT_APPLICABLE / MANUAL_ONLY /
BLOCKED / SOURCE_UNAVAILABLE). It never means every question has an answer.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import cases, db, execution, outreach, store
from .db import jdump, jload, utcnow

VERSION = "p7.1"
STATUSES = ("NOT_STARTED", "RUNNING", "COMPLETE", "COMPLETE_WITH_UNKNOWN", "COMPLETE_WITH_SOURCE_FAILURES", "FAILED")
ATTEMPT_STATUSES = ("SUCCESS_WITH_EVIDENCE", "SUCCESS_NO_ANSWER", "NOT_APPLICABLE", "MANUAL_ONLY", "BLOCKED", "SOURCE_UNAVAILABLE", "FAILED")
FAILURE_STATUSES = ("BLOCKED", "SOURCE_UNAVAILABLE", "FAILED")
FRESH_HOURS = 6          # a check that SUCCEEDED this recently is reused, not re-read (idempotent reruns, no hammering)
HISTORICAL_DAYS = 365    # a record older than this, or older than the roll reading, is shown as HISTORICAL RECORD
OUTREACH_PURPOSE = "PROPERTY_STATUS_INQUIRY"

# domain → (label, executable check in execution.CHECKS or None, questions it informs, evidence fields it renders)
DOMAINS = (
    ("IDENTITY",          "Property identity",                 None,                   ("identity",),                        ("parcel_id", "address", "identity_resolution", "manual:identity")),
    ("ROLL_RECORD",       "State parcel / roll record",        None,                   ("values",),                          ("parcel_id", "address", "legal_description", "subdivision", "acreage", "land_value", "improvement_value", "total_assessed_value", "property_class", "parcel_type_code", "improvement_state")),
    ("TAX_COLLECTOR",     "County Collector tax state",        "COLLECTOR_RECHECK",    ("tax_state", "tax_delinquent", "collector_asof"), ("tax_bill", "tax_amount_owed_county", "tax_delinquent_county", "tax_status_check")),
    ("STATE_LANDS",       "State Lands / tax-sale history",    "STATE_LANDS_RECHECK",  ("state_inventory", "state_record", "state_history", "auction"), ("tax_delinquent", "tax_delinquent_removed", "tax_sale_history", "tax_redemption")),
    ("FLOOD",             "FEMA flood zone",                   "FEMA_RECHECK",         ("flood",),                           ("flood_zone", "flood_risk")),
    ("ROAD_ACCESS",       "Road / access",                     "ROAD_RECORD_RECHECK",  ("access",),                          ("road_access", "legal_access")),
    ("MUNICIPAL_VACANCY", "Municipal vacancy register",        "CITY_VACANCY_RECHECK", ("vacancy",),                         ("vacant_structure", "vacant_structure_check")),
    ("MUNICIPAL_LIEN",    "Municipal lien layer",              "CITY_LIEN_RECHECK",    ("lien_city", "lien_detail"),         ("cleanup_lien_amount", "cleanup_lien_check")),
    ("MUNICIPAL_CODE",    "Municipal code cases",              "CITY_CODE_RECHECK",    ("code", "code_detail"),              ("code_case_open", "code_case_check")),
    ("OWNER_MAILING",     "Owner / mailing address",           "OWNER_MAILING_RECHECK", ("owner", "mailing_address"),        ("owner_name", "owner_mailing_address", "owner_mailing_check", "manual:owner", "manual:mailing_address")),
    ("TITLE_DEED",        "Title / deed / Circuit Clerk",      None,                   ("deed", "title", "lien_clerk"),      ("deed_reference", "sourceref", "manual:deed", "manual:title", "manual:lien_clerk")),
    ("LISTING",           "Listing / sale status",             None,                   ("listing", "sale_state"),            ("manual:listing", "manual:sale_state")),
    ("PHYSICAL",          "Physical / occupancy",              None,                   ("inspection",),                      ("manual:inspection",)),
)
FIELD_LABEL = {"parcel_id": "Parcel number", "address": "Situs address", "identity_resolution": "Address resolution", "manual:identity": "Human property selection",
               "legal_description": "Legal description", "subdivision": "Subdivision", "acreage": "Acreage (roll)", "land_value": "Land value (assessor)", "improvement_value": "Improvement value (assessor)",
               "total_assessed_value": "Total value (assessor)", "property_class": "Property class", "parcel_type_code": "Parcel type code", "improvement_state": "Improvement state (roll code)",
               "tax_bill": "Collector bill", "tax_amount_owed_county": "Amount owed to the county", "tax_delinquent_county": "County delinquency", "tax_status_check": "Tax status check",
               "tax_delinquent": "State certification (unpaid taxes)", "tax_delinquent_removed": "Left the State inventory", "tax_sale_history": "State sale record", "tax_redemption": "State redemption record",
               "flood_zone": "FEMA flood zone", "flood_risk": "FEMA hazard area", "road_access": "Nearest mapped road", "legal_access": "Legal access record",
               "vacant_structure": "Vacancy register record", "vacant_structure_check": "Vacancy register check", "cleanup_lien_amount": "City lien", "cleanup_lien_check": "City lien check",
               "code_case_open": "Code case", "code_case_check": "Code case check", "owner_name": "Owner of record (roll)", "owner_mailing_address": "Mailing address of record", "owner_mailing_check": "Mailing address check (City roll copy)",
               "manual:owner": "Owner (human verification)", "manual:mailing_address": "Mailing address (human verification)", "deed_reference": "Deed reference", "sourceref": "Roll source reference",
               "manual:deed": "Deed (human verification)", "manual:title": "Title search (human verification)", "manual:lien_clerk": "Circuit Clerk liens (human verification)",
               "manual:listing": "Listing check (human verification)", "manual:sale_state": "Sale state (human verification)", "manual:inspection": "In-person inspection"}
NOT_FOUND_FIELDS = ("tax_status_check", "tax_delinquent_removed", "vacant_structure_check", "cleanup_lien_check", "code_case_check", "owner_mailing_check")
# what a person does for an UNKNOWN question, why, and what evidence closes it (destinations come from cases.next_action, never invented)
ACTIONS = {
    "tax_state":       ("Check the county Collector for this parcel", "The tax state decides whether this is a tax-sale, delinquent or current parcel; the automated Collector read did not answer", "A Collector answer with an as-of date (bill, delinquency or no open bill), recorded as a manual verification or by the poller"),
    "tax_delinquent":  ("Confirm delinquency at the Collector", "Delinquency is never assumed from absence on State Lands", "A Collector or State record naming the delinquent year and amount"),
    "collector_asof":  ("Ask the Collector when it last answered for this parcel", "An old answer is treated as unknown after 45 days", "A dated Collector response"),
    "state_inventory": ("Search State Lands by RPID", "Only the Commissioner of State Lands can say whether the parcel is certified", "A State Lands per-parcel answer (held / not held) with its date"),
    "state_record":    ("Open the State Lands listing", "The listing carries the delinquent year, bid and sale type", "The State listing record"),
    "state_history":   ("Check the State's sale and redemption reports for the county", "History explains prior certifications and redemptions", "A State report line for this parcel"),
    "auction":         ("Open the State Lands auction page", "Auction terms come only from the State", "The State's own auction listing"),
    "flood":           ("Read the FEMA flood map at the parcel", "The automated FEMA read did not answer; flood zone is a fact about the land, not a risk score", "A FEMA zone reading with the map date"),
    "access":          ("Read the road record near the parcel", "A road on the map is not a legal right of access; the deed or plat is", "A road-record reading, then the deed/plat easement for legal access"),
    "vacancy":         ("Open the City vacancy register", "Vacancy is never inferred; only the register or an inspection says it", "A register record or a dated 'not on the register' check"),
    "lien_city":       ("Open the City lien layer", "City liens attach to the parcel and survive a sale", "A lien record with filing date and amount, or a dated no-lien check"),
    "lien_detail":     ("Read the lien filing", "Amount and claimant are needed to price the lien", "The filing reference, date, claimant and amount"),
    "code":            ("Open the City code-case layer", "Code cases signal condition problems but are not proof of condition", "A code case record or a dated no-case check"),
    "code_detail":     ("Read the code case", "Status and violation type matter", "Case date, status and violation type"),
    "owner":           ("Read the owner of record at the county Assessor", "The State roll's owner is a roll fact, not a title opinion", "An Assessor or Clerk owner-of-record reading with its date"),
    "mailing_address": ("Read the owner's mailing address at the county Assessor", "Outreach cannot be prepared without a mailing address of record; the situs is never substituted", "The mailing address on the Assessor's record with its date"),
    "deed":            ("Pull the latest deed at the Circuit Clerk", "Vesting owner, legal description and instrument number come only from the recorded deed", "Deed book/page or instrument number, grantor/grantee, record date"),
    "title":           ("Order or run a title search at the Circuit Clerk", "Property Hunter never declares title clear; only a search does", "A title search result or Circuit Clerk index review, recorded as a manual verification"),
    "lien_clerk":      ("Search the Circuit Clerk index for mortgages, judgments and tax liens", "Recorded liens follow the property", "A Clerk index result naming any instruments, or a dated 'none found'"),
    "listing":         ("Search public listing sources for this address", "No listing source is connected; absence here is not 'not for sale'", "A listing record, or a dated 'no listing found' check recorded by a person"),
    "sale_state":      ("Confirm the sale state", "Only the State sale or a real listing establishes a sale state", "A State listing or a private listing record"),
    "inspection":      ("Inspect the property in person: exterior, visible structure, occupancy indicators, posted notices, apparent access", "Condition and occupancy are never inferred from records", "Your dated inspection log with photos"),
    "values":          ("Read the assessor's values on the roll", "Values here are assessor figures, not a price", "The roll's land, improvement and total values"),
    "identity":        ("Review the property file identity", "Everything else hangs on the parcel identity", "The parcel number, county and situs on the roll"),
    "other_signals":   ("Review the property file for other public-record signals", "Signals are pointers, not findings", "Any recorded signal with its source"),
}


# ------------------------------------------------------------------ helpers
def _days_ago(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso)[:19].replace("Z", ""))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() / 86400
    except Exception:
        return None


def _roll_date(pid: int) -> str | None:
    e = store.latest_answer(pid, "parcel_id")
    return ((e or {}).get("effective_date") or "")[:10] or None


ROLL_COPY_FIELDS = ("owner_name", "owner_mailing_address")     # compared against the roll reading's date; other sources have their own clocks


def item(pid: int, field: str, *, roll_date: str | None = None, manual_states: dict | None = None) -> dict:
    """One strict WHAT WE KNOW line: value + full provenance, or UNKNOWN. Never a sentence about the owner.
    A person's own log whose recorded result was UNKNOWN is an attempt on file, not a finding."""
    e = store.latest_answer(pid, field)
    base = {"field": field, "label": FIELD_LABEL.get(field, field.replace("_", " ")), "value": None, "state": "UNKNOWN", "source": None, "source_name": None, "source_url": None,
            "source_date": None, "read_date": None, "ref": None, "origin": None, "origin_label": None, "confidence": None, "verification": None, "historical": False, "conflict": False}
    if not e:
        return base
    if field.startswith("manual:") and (manual_states or {}).get(field[7:]) == "UNKNOWN":
        return dict(base, note=f"a person recorded an attempt on {(e.get('effective_date') or '')[:10]} and chose UNKNOWN: {str(e.get('value') or '')[:160]}", ref=f"evidence:{e['id']}")
    date = (e.get("effective_date") or "")[:10] or None
    hist = False
    if date:
        age = _days_ago(date)
        hist = (age is not None and age > HISTORICAL_DAYS) or bool(roll_date and field in ROLL_COPY_FIELDS and date < roll_date)
    conf = db.q1("SELECT 1 FROM conflicts WHERE property_id=? AND field=? AND status='NEEDS VERIFICATION'", (pid, field))
    return dict(base, value=e.get("value"), state="NOT_FOUND" if field in NOT_FOUND_FIELDS else "FOUND", source=e.get("source"), source_name=e.get("source_name") or e.get("source"),
                source_url=e.get("source_url") if str(e.get("source_url") or "").startswith("http") else None, source_date=date, read_date=(e.get("retrieved_at") or e.get("created_at") or "")[:16],
                ref=f"evidence:{e['id']}", origin=e["origin"], origin_label=e.get("origin_label"), confidence=e.get("confidence"), verification=e.get("evidence_type"),
                historical=hist, conflict=bool(conf), raw_ref=e.get("raw_ref"))


# ------------------------------------------------------------------ contract
def start(search_id: int, *, actor: str, license_id: int = 0, session_id: int = 0) -> dict:
    """Validate the resolved identity, open/attach the one investigation, create the workup row. Runs nothing."""
    from . import resolver
    s = db.q1("SELECT * FROM address_searches WHERE id=?", (search_id,))
    if not s or (license_id is not None and s["license_id"] != license_id):
        raise KeyError("no such search on this license")
    if not s["selected_property_id"] or not s["identity_json"]:
        raise ValueError(f"the search is {s['state'].replace('_', ' ')}: no verified property identity, so there is nothing to work up")
    identity = jload(s["identity_json"], None) or {}
    if not identity.get("verified") or identity.get("verified_by") not in ("AUTOMATED_SOURCE", "MANUAL_VERIFICATION"):
        raise ValueError("the identity on this search is not verified by a source or a person")
    pid = s["selected_property_id"]
    prop = store.get_property(pid)
    if not prop or prop.get("id") != identity.get("property_id"):
        raise ValueError("the property behind this search no longer exists")
    actor = (actor or "user").strip()[:40] or "user"
    inv = resolver.open_investigation(search_id, actor=actor, license_id=license_id)
    now = utcnow()
    cur = db.ex("INSERT INTO workups(property_id, investigation_id, search_id, license_id, session_id, actor, status, version, started_at, identity_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pid, inv["case_id"], search_id, license_id or 0, session_id or 0, actor, "NOT_STARTED", VERSION, now, jdump(identity)))
    return view(cur.lastrowid)


def _attempt(wid: int, domain: str, **kw) -> int:
    cols = {"workup_id": wid, "domain": domain, "started_at": kw.pop("started_at", utcnow())}
    cols.update(kw)
    for k in ("health_json", "questions_json"):
        if k in cols and not isinstance(cols[k], str):
            cols[k] = jdump(cols[k])
    cur = db.ex(f"INSERT INTO workup_attempts({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values()))
    return cur.lastrowid


def _fresh_execution(pid: int, check: str) -> dict | None:
    since = (datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)).isoformat(timespec="seconds")
    r = db.q1("SELECT id FROM investigation_executions WHERE property_id=? AND check_type=? AND status='SUCCEEDED' AND finished_at>=? ORDER BY id DESC LIMIT 1", (pid, check, since))
    return execution.execution(r["id"]) if r else None


def _classify_execution(ex: dict, guard: dict) -> tuple[str, str | None, str | None]:
    """execution status → ledger status, failure category, detail."""
    st = ex["status"]
    err = ex.get("error") or ""
    if st == "SUCCEEDED":
        return ("SUCCESS_WITH_EVIDENCE" if (ex.get("evidence_created") or ex.get("evidence_touched")) else "SUCCESS_NO_ANSWER"), None, ex.get("result_ref")
    if st == "BLOCKED":
        cat = guard.get("category") if not guard.get("ready") else "SOURCE_UNAVAILABLE"
        if err.startswith("source still unavailable"):
            return "SOURCE_UNAVAILABLE", "PROBE_FAILED", err
        return ({"MANUAL_ONLY": "MANUAL_ONLY", "NOT_APPLICABLE": "NOT_APPLICABLE"}.get(cat, "BLOCKED")), (cat or "BLOCKED"), err
    if st == "FAILED":
        if err.startswith("source unavailable") or err.startswith("source degraded") or "did not answer" in err or "Errno" in err or "timed out" in err.lower():
            return "SOURCE_UNAVAILABLE", "ADAPTER_UNAVAILABLE", err
        return "FAILED", "MALFORMED_RESULT" if "malformed" in err else "ADAPTER_ERROR", err
    return "FAILED", st, err


def _question_states(case_id: int, keys) -> dict:
    return {r["key"]: r["state"] for r in db.q("SELECT key, state FROM investigation_questions WHERE case_id=?", (case_id,)) if r["key"] in keys}


def run(workup_id: int) -> dict:
    """The orchestration. Deterministic; no model. Every domain ends in the ledger."""
    w = db.q1("SELECT * FROM workups WHERE id=?", (workup_id,))
    if not w:
        raise KeyError("no such workup")
    if w["status"] != "NOT_STARTED":
        return view(workup_id)
    pid, cid, actor = w["property_id"], w["investigation_id"], w["actor"]
    t0 = time.perf_counter()
    timings = {}
    db.ex("UPDATE workups SET status='RUNNING' WHERE id=?", (workup_id,))
    cases._event(cid, "WORKUP STARTED", f"Full property workup {workup_id} started by {actor}", f"version {VERSION}; identity from search {w['search_id']}; {len(DOMAINS)} domains", f"workup:{workup_id}", actor)
    try:
        cases.ensure_questions(cid)
        for key in ("deed", "lien_clerk", "code", "vacancy", "lien_city", "state_record", "state_history", "auction", "collector_asof", "tax_delinquent"):
            cat, wording, _ = cases.QUESTIONS[key]
            now = utcnow()
            db.ex("INSERT OR IGNORE INTO investigation_questions(case_id, key, category, wording, state, created_at, updated_at) VALUES(?,?,?,?,'UNKNOWN',?,?)", (cid, key, cat, wording, now, now))
        prop = store.get_property(pid)
        roll_date = _roll_date(pid)
        authorization = {"workup_id": workup_id, "requested_by": actor, "search_id": w["search_id"], "identity_verified_by": (jload(w["identity_json"], {}) or {}).get("verified_by")}
        for domain, label, check, qkeys, fields in DOMAINS:
            ts = time.perf_counter()
            started = utcnow()
            before = _question_states(cid, qkeys)
            if check and domain == "OWNER_MAILING" and not execution.check_guard(check, prop)["ready"]:
                # outside Hot Springs the City copy does not exist: the Assessor path is manual, not "not applicable"
                st, cat, detail, src, manual = _classify_on_file(domain, pid, prop, fields, roll_date)
                _attempt(workup_id, domain, source=src, check_type=None, execution_id=None, status=st, failure_category=cat, failure_detail=detail, evidence_count=sum(1 for f in fields if store.latest_answer(pid, f)),
                         questions_json={"before": before, "after": before}, manual_required=int(manual), started_at=started, completed_at=utcnow(), ms=int((time.perf_counter() - ts) * 1000))
            elif check:
                guard = execution.check_guard(check, prop)
                fresh = _fresh_execution(pid, check)
                if guard["ready"] and fresh:
                    st, cat, detail = _classify_execution(fresh, guard)
                    _attempt(workup_id, domain, source=guard["source"], check_type=check, execution_id=fresh["id"], status=st, health_json=guard.get("source_health"), failure_category=cat,
                             failure_detail=f"reused: SUCCEEDED at {fresh['finished_at']} (within {FRESH_HOURS} h); not re-read", evidence_count=len(fresh.get("evidence_created") or []) + len(fresh.get("evidence_touched") or []),
                             questions_json={"before": before, "after": _question_states(cid, qkeys)}, manual_required=0, reused=1, started_at=started, completed_at=utcnow(), ms=int((time.perf_counter() - ts) * 1000))
                else:
                    ex = execution.execute(check, cid, pid, actor=actor, authorization=authorization, plan_=guard, workup_id=workup_id)
                    st, cat, detail = _classify_execution(ex, guard)
                    _attempt(workup_id, domain, source=guard["source"], check_type=check, execution_id=ex["id"], status=st, health_json=guard.get("source_health"), failure_category=cat,
                             failure_detail=(detail or "")[:300] or None, evidence_count=len(ex.get("evidence_created") or []) + len(ex.get("evidence_touched") or []),
                             questions_json={"before": before, "after": _question_states(cid, qkeys)}, manual_required=int(st in ("MANUAL_ONLY", "BLOCKED", "SOURCE_UNAVAILABLE", "FAILED")),
                             started_at=started, completed_at=utcnow(), ms=int((time.perf_counter() - ts) * 1000))
            else:
                st, cat, detail, src, manual = _classify_on_file(domain, pid, prop, fields, roll_date)
                _attempt(workup_id, domain, source=src, check_type=None, execution_id=None, status=st, failure_category=cat, failure_detail=detail, evidence_count=sum(1 for f in fields if store.latest_answer(pid, f)),
                         questions_json={"before": before, "after": before}, manual_required=int(manual), started_at=started, completed_at=utcnow(), ms=int((time.perf_counter() - ts) * 1000))
            timings[domain] = int((time.perf_counter() - ts) * 1000)
        cases.refresh(cid)
        _finalize(workup_id, cid, pid, timings, int((time.perf_counter() - t0) * 1000))
    except Exception as exc:                                              # pragma: no cover - defensive
        db.ex("UPDATE workups SET status='FAILED', error=?, completed_at=?, timings_json=? WHERE id=?", (f"{type(exc).__name__}: {exc}"[:300], utcnow(), jdump(timings), workup_id))
        cases._event(cid, "WORKUP COMPLETED", f"Workup {workup_id} FAILED", f"{type(exc).__name__}: {exc}"[:200], f"workup:{workup_id}", actor)
    return view(workup_id)


def _classify_on_file(domain: str, pid: int, prop: dict, fields, roll_date) -> tuple:
    """Domains with no per-parcel automated adapter: report what is on file, and say honestly what a person must do."""
    on_file = [f for f in fields if store.latest_answer(pid, f)]
    if domain == "IDENTITY":
        return ("SUCCESS_WITH_EVIDENCE" if on_file else "FAILED"), (None if on_file else "NO_IDENTITY_EVIDENCE"), "identity resolved by P6; not re-run by the workup", "p6_resolver", False
    if domain == "ROLL_RECORD":
        if on_file:
            return "SUCCESS_WITH_EVIDENCE", None, f"State roll reading on file dated {roll_date or 'unknown'} (read at resolution / by the hunt); the roll adapter has no per-parcel re-read", "ar_gis_parcels", False
        return "MANUAL_ONLY", "NO_ROLL_READING", "no roll reading on file; resolve the address again to read the roll", "ar_gis_parcels", True
    if domain == "OWNER_MAILING":
        owner = store.latest_answer(pid, "owner_name") or store.latest_answer(pid, "manual:owner")
        mail = store.latest_answer(pid, "owner_mailing_address") or store.latest_answer(pid, "manual:mailing_address")
        if owner and mail:
            return "SUCCESS_WITH_EVIDENCE", None, "owner and mailing address on file (see record dates; the Assessor refuses automated reading, so no re-read)", (mail.get("source") or owner.get("source")), False
        if owner:
            return "MANUAL_ONLY", "ASSESSOR_BLOCKED", "owner of record on file from the State roll; no mailing address of record — the Assessor's site refuses automation; a person reads it", owner.get("source"), True
        return "MANUAL_ONLY", "ASSESSOR_BLOCKED", "no owner or mailing record on file; the Assessor's site refuses automation", None, True
    if domain == "TITLE_DEED":
        if any(store.latest_answer(pid, f) for f in ("manual:title", "manual:deed", "deed_reference")):
            return "SUCCESS_WITH_EVIDENCE", None, "a deed/title reading is on file (see its date and origin); no automated Circuit Clerk source exists", "manual_verification", True
        return "MANUAL_ONLY", "NO_CLERK_ADAPTER", "TITLE STATUS: UNKNOWN — MANUAL CIRCUIT CLERK REVIEW REQUIRED; no automated Clerk/title source exists and none is simulated", None, True
    if domain == "LISTING":
        row = cases.property_row(pid) or {}
        sale = cases.sale_state(row)
        if sale["st"] == "FOR_SALE_BY_STATE":
            return "SUCCESS_WITH_EVIDENCE", None, "for sale by the State (tax sale) per State evidence; that is not a private listing", "cosl_listings", False
        if store.latest_answer(pid, "manual:listing") or store.latest_answer(pid, "manual:sale_state"):
            return "SUCCESS_WITH_EVIDENCE", None, "a person recorded a listing check (see its date)", "manual_verification", False
        return "MANUAL_ONLY", "NO_LISTING_ADAPTER", "no listing source is connected; a person searches and records the result — absence is not 'not for sale'", None, True
    if domain == "PHYSICAL":
        q = db.q1("SELECT state, checked_by FROM investigation_questions q JOIN investigation_cases c ON c.id=q.case_id WHERE c.property_id=? AND q.key='inspection'", (pid,))
        if q and q["checked_by"] == "manual" and q["state"] in ("FOUND", "NOT_FOUND"):
            return "SUCCESS_WITH_EVIDENCE", None, "an in-person inspection is on file (see its date and what the person recorded)", "manual_verification", False
        if store.latest_answer(pid, "manual:inspection"):
            return "MANUAL_ONLY", "INSPECTION_REQUIRED", "a person logged an attempt but recorded UNKNOWN; condition and occupancy are never inferred from records — inspect", None, True
        return "MANUAL_ONLY", "INSPECTION_REQUIRED", "condition and occupancy are never inferred from records; a person inspects", None, True
    return "NOT_APPLICABLE", None, "no rule for this domain", None, False


def _finalize(workup_id: int, cid: int, pid: int, timings: dict, total_ms: int) -> None:
    attempts = [dict(r) for r in db.q("SELECT * FROM workup_attempts WHERE workup_id=? ORDER BY id", (workup_id,))]
    c = cases.get_case(cid)
    qs = {q["key"]: {"state": q["state"], "checked_by": q["checked_by"], "source": q["source"], "checked_at": q["checked_at"], "answer": q["answer"]} for q in c["questions"]}
    failures = [{"domain": a["domain"], "source": a["source"], "status": a["status"], "category": a["failure_category"], "detail": a["failure_detail"], "at": a["completed_at"]} for a in attempts if a["status"] in FAILURE_STATUSES]
    conflicts = [dict(r) for r in db.q("SELECT id, field, value_a, source_a, date_a, value_b, source_b, date_b, status, created_at FROM conflicts WHERE property_id=? AND status='NEEDS VERIFICATION' ORDER BY id", (pid,))]
    for cf in conflicts:
        win = store.latest_answer(pid, cf["field"])
        cf["precedence"] = ({"ref": f"evidence:{win['id']}", "origin": win["origin"], "source": win.get("source"), "date": (win.get("effective_date") or "")[:10], "rule": "P3A: higher origin precedence, then newest record date, then newest row"} if win else None)
        cf["unresolved"] = "both readings stay on file; a person verifies which record is current"
    evidence = sorted({e for a in attempts if a["execution_id"] for e in ((execution.execution(a["execution_id"]) or {}).get("evidence_created") or [])})
    actions = next_actions(cid, pid, attempts, c)
    gate = outreach_gate(c)
    workup_q = {k for _, _, _, ks, _ in DOMAINS for k in ks}
    unknown = [k for k, v in qs.items() if v["state"] == "UNKNOWN" and k in workup_q]
    status = "COMPLETE_WITH_SOURCE_FAILURES" if failures else "COMPLETE_WITH_UNKNOWN" if unknown else "COMPLETE"
    db.ex("UPDATE workups SET status=?, completed_at=?, evidence_json=?, questions_json=?, failures_json=?, conflicts_json=?, next_actions_json=?, outreach_json=?, timings_json=? WHERE id=?",
          (status, utcnow(), jdump(evidence), jdump(qs), jdump(failures), jdump(conflicts), jdump(actions), jdump(gate), jdump(dict(timings, total_ms=total_ms)), workup_id))
    from . import research
    research.ensure_tasks(workup_id)                                  # P8: one OPEN task per real gap, never duplicated
    counts = {"found": sum(1 for v in qs.values() if v["state"] == "FOUND"), "not_found": sum(1 for v in qs.values() if v["state"] == "NOT_FOUND"), "unknown": len(unknown)}
    cases._event(cid, "WORKUP COMPLETED", f"Workup {workup_id}: {status.replace('_', ' ')}",
                 f"{len(attempts)} domains; {sum(1 for a in attempts if a['status'].startswith('SUCCESS'))} answered; {len(failures)} source failures; {sum(1 for a in attempts if a['status'] == 'MANUAL_ONLY')} manual-only; "
                 f"questions FOUND {counts['found']} / NOT FOUND {counts['not_found']} / UNKNOWN {counts['unknown']}; {len(evidence)} new evidence rows; outreach {gate['verdict']}", f"workup:{workup_id}", "property_hunter")


def outreach_gate(case: dict) -> dict:
    """The existing P3B gate, evaluated and reported. Nothing is drafted, nothing is sent, no prep is created."""
    g = outreach.evaluate_gate(case, OUTREACH_PURPOSE, None)
    evidence_blocking = [b for b in g["blocking"] if b != "REASON FOR CONTACT"]
    verdict = "OUTREACH BLOCKED" if evidence_blocking else ("OUTREACH NEEDS REVIEW" if g["recommended_missing"] else "OUTREACH READY")
    return {"verdict": verdict, "purpose": OUTREACH_PURPOSE, "evaluated_at": g["evaluated_at"], "blocking": g["blocking"], "evidence_blocking": evidence_blocking,
            "recommended_missing": g["recommended_missing"], "requirements": [{k: r.get(k) for k in ("key", "label", "level", "state", "text", "origin_label", "date", "source")} for r in g["requirements"]],
            "explanation": g["explanation"], "note": "Evaluated only. No draft was generated, nothing was sent, and there is no send path. The reason for contact is entered by a person when preparing outreach."}


def next_actions(cid: int, pid: int, attempts: list[dict], case: dict) -> list[dict]:
    prop = store.get_property(pid)
    row = case.get("row") or {}
    by_domain = {a["domain"]: a for a in attempts}
    out, seen = [], set()
    for domain, label, check, qkeys, _ in DOMAINS:
        a = by_domain.get(domain) or {}
        for k in qkeys:
            q = next((x for x in case["questions"] if x["key"] == k), None)
            if not q or q["state"] != "UNKNOWN" or k in seen:
                continue
            seen.add(k)
            what, why, resolves = ACTIONS.get(k, (f"Resolve: {q['wording']}", "The question is open", "Evidence from a named source"))
            dest = cases.next_action(k, prop, row)
            reason = {"MANUAL_ONLY": "no automated source; a person does this", "SOURCE_UNAVAILABLE": "the automated source did not answer", "BLOCKED": "the automated source is not usable right now",
                      "FAILED": "the automated check failed", "NOT_APPLICABLE": "no source covers this county"}.get(a.get("status"), "the sources that answered did not establish it")
            out.append({"domain": domain, "question": k, "wording": q["wording"], "what": what, "why": f"{why}. Now: {reason}.", "where": dest, "resolves": resolves,
                        "source_status": a.get("status"), "priority": cases.PRIORITY.index(k) if k in cases.PRIORITY else 99})
    out.sort(key=lambda x: x["priority"])
    return out


# ------------------------------------------------------------------ read models
def view(workup_id: int, license_id: int | None = None) -> dict | None:
    w = db.q1("SELECT * FROM workups WHERE id=?", (workup_id,))
    if not w or (license_id is not None and w["license_id"] != license_id):
        return None
    attempts = [dict(r) for r in db.q("SELECT * FROM workup_attempts WHERE workup_id=? ORDER BY id", (workup_id,))]
    for a in attempts:
        a["health"] = jload(a.pop("health_json"), None); a["questions"] = jload(a.pop("questions_json"), None)
        a["label"] = next((l for d, l, *_ in DOMAINS if d == a["domain"]), a["domain"])
    done = {a["domain"] for a in attempts}
    return {"workup_id": w["id"], "property_id": w["property_id"], "investigation_id": w["investigation_id"], "search_id": w["search_id"], "actor": w["actor"], "status": w["status"],
            "status_label": {"NOT_STARTED": "WORKUP NOT STARTED", "RUNNING": "WORKUP RUNNING", "COMPLETE": "WORKUP COMPLETE", "COMPLETE_WITH_UNKNOWN": "WORKUP COMPLETE — UNKNOWN ITEMS",
                             "COMPLETE_WITH_SOURCE_FAILURES": "WORKUP COMPLETE — SOURCE FAILURES", "FAILED": "WORKUP FAILED"}[w["status"]],
            "version": w["version"], "started_at": w["started_at"], "completed_at": w["completed_at"], "error": w["error"], "identity": jload(w["identity_json"], None),
            "progress": [{"domain": d, "label": l, "done": d in done, "status": next((a["status"] for a in attempts if a["domain"] == d), None)} for d, l, *_ in DOMAINS],
            "attempts": attempts, "evidence_produced": jload(w["evidence_json"], []) or [], "questions": jload(w["questions_json"], {}) or {}, "failures": jload(w["failures_json"], []) or [],
            "conflicts": jload(w["conflicts_json"], []) or [], "next_actions": jload(w["next_actions_json"], []) or [], "outreach": jload(w["outreach_json"], None), "timings": jload(w["timings_json"], {}) or {}}


def latest_for_property(pid: int, license_id: int | None = None) -> dict | None:
    r = db.q1("SELECT id FROM workups WHERE property_id=? " + ("AND license_id=? " if license_id is not None else "") + "ORDER BY id DESC LIMIT 1", (pid, license_id) if license_id is not None else (pid,))
    return view(r["id"]) if r else None


def property_file(workup_id: int, license_id: int | None = None) -> dict | None:
    """THE unified surface. Deterministic rendering of qualifying evidence; nothing summarised by a model."""
    w = view(workup_id, license_id)
    if not w:
        return None
    pid, cid = w["property_id"], w["investigation_id"]
    prop = store.get_property(pid) or {}
    c = cases.get_case(cid) if cid else None
    roll_date = _roll_date(pid)
    manual_states = {q["key"]: q["state"] for q in (c or {}).get("questions", []) if q.get("checked_by") == "manual"}
    ident = w["identity"] or {}
    id_ev = store.latest_answer(pid, "manual:identity") if ident.get("verified_by") == "MANUAL_VERIFICATION" else store.latest_answer(pid, "identity_resolution")
    identity = {"property_id": pid, "parcel_id": prop.get("parcel_id"), "rpid": prop.get("rpid"), "county": ident.get("county") or (c or {}).get("property", {}).get("county"), "county_fips": prop.get("county_fips"),
                "situs": prop.get("address"), "city": prop.get("city"), "zip": prop.get("zip"), "lat": prop.get("lat"), "lon": prop.get("lon"), "acreage": prop.get("acreage"),
                "verification_state": "VERIFIED" if ident.get("verified") else "NOT VERIFIED", "verified_by": ident.get("verified_by"), "identity_source": ident.get("source_label") or ident.get("source"),
                "source_date": (ident.get("source_date") or "")[:10] or None, "read_date": (ident.get("read_at") or "")[:16] or None, "evidence_ref": f"evidence:{id_ev['id']}" if id_ev else ident.get("evidence_id") and f"evidence:{ident['evidence_id']}",
                "actor": ident.get("actor"), "search_id": w["search_id"]}
    sections = {}
    for domain, label, check, qkeys, fields in DOMAINS:
        if domain == "IDENTITY":
            continue
        items = [item(pid, f, roll_date=roll_date, manual_states=manual_states) for f in fields]
        a = next((x for x in w["attempts"] if x["domain"] == domain), None)
        qs = [q for q in (c or {}).get("questions", []) if q["key"] in qkeys]
        sections[domain] = {"label": label, "check": check, "attempt": a, "items": items, "known": [i for i in items if i["state"] == "FOUND"], "checked_not_found": [i for i in items if i["state"] == "NOT_FOUND"],
                            "questions": [{"key": q["key"], "wording": q["wording"], "state": q["state"], "answer": q["answer"], "source": q["source"], "checked_at": q["checked_at"], "checked_by": q["checked_by"], "refs": q.get("evidence_refs") or []} for q in qs]}
    row = (c or {}).get("row") or {}
    taxs = row.get("taxs") or {"st": "UNKNOWN"}
    cp = cases.collector_status()
    tax = {"tax_state": "SOURCE_UNAVAILABLE" if (taxs.get("st") in (None, "UNKNOWN") and cp.get("open") is False) else taxs.get("st") or "UNKNOWN", "source": taxs.get("src"), "as_of": taxs.get("as_of"), "amount": taxs.get("amt"),
           "delinquency": ("DELINQUENT" if taxs.get("st") in ("TAX_SALE_VERIFIED", "DELINQUENT_VERIFIED") else "NOT DELINQUENT PER COLLECTOR" if taxs.get("st") in ("CURRENT_BILL_OPEN", "CURRENT_VERIFIED") else "UNKNOWN"),
           "collector": {"status": "UNAVAILABLE" if cp.get("open") is False else "OPEN" if cp.get("open") else "UNTESTED", "down_since": cp.get("down_since"), "checked_at": cp.get("checked_at"), "detail": cp.get("detail")},
           "meaning": {"TAX_SALE_VERIFIED": "Certified to the State for unpaid taxes (State evidence).", "DELINQUENT_VERIFIED": "Delinquent at the county Collector (verified).", "CURRENT_BILL_OPEN": "A current-year bill is open at the Collector. That is not delinquency.",
                       "CURRENT_VERIFIED": "The Collector shows no open bill.", "STALE": "The last Collector answer is older than 45 days; treated as unknown until re-checked.", "UNKNOWN": "Never answered by the Collector and not on the State list. Not a finding either way.",
                       "SOURCE_UNAVAILABLE": "The Collector's online source is down; nothing was checked. Not a finding either way."}.get("SOURCE_UNAVAILABLE" if (taxs.get("st") in (None, "UNKNOWN") and cp.get("open") is False) else taxs.get("st") or "UNKNOWN")}
    sale = cases.sale_state(row)
    listing = {"sale_state": sale["st"], "text": sale["text"], "source": sale["src"], "as_of": sale.get("as_of"), "for_sale_by_state": sale["st"] == "FOR_SALE_BY_STATE",
               "private_listing": item(pid, "manual:listing", roll_date=roll_date, manual_states=manual_states), "note": "Absence from every list is not 'not for sale'. Property Hunter never says an owner wants to sell."}
    access = sections["ROAD_ACCESS"]
    legal = store.latest_answer(pid, "legal_access")
    access["legal_access"] = {"state": "UNKNOWN", "text": "Legal access (easement / right of way) is established only by the deed or plat; a mapped road nearby is not that record."} if not legal or legal.get("source") == "ar_gis_roads" else \
        {"state": "FOUND", "text": legal["value"], "ref": f"evidence:{legal['id']}", "source": legal.get("source_name") or legal.get("source"), "date": (legal.get("effective_date") or "")[:10]}
    owner = sections["OWNER_MAILING"]
    for i in owner["items"]:
        if i["state"] == "FOUND" and i["historical"]:
            i["qualifier"] = f"HISTORICAL RECORD — record date {i['source_date']}" + (f"; the roll reading on file is dated {roll_date}" if roll_date else "")
    title = sections["TITLE_DEED"]
    title["status"] = "TITLE STATUS: UNKNOWN — MANUAL CIRCUIT CLERK REVIEW REQUIRED" if not any(i["state"] == "FOUND" for i in title["items"]) else "A deed/title reading is on file; see its date and origin. Property Hunter never declares title clear."
    physical = sections["PHYSICAL"]
    iq = next((q for q in physical["questions"] if q["key"] == "inspection"), None)
    physical["status"] = (f"Inspected by a person: {iq['answer']}" if iq and iq["checked_by"] == "manual" and iq["state"] in ("FOUND", "NOT_FOUND") else
                          "UNKNOWN — condition and occupancy are never inferred from records" + ("; a person logged an attempt and recorded UNKNOWN" if any(i.get("note") for i in physical["items"]) else ""))
    known = [dict(i, domain=d) for d, s in sections.items() for i in s["known"]]
    checked_nf = [dict(i, domain=d) for d, s in sections.items() for i in s["checked_not_found"]]
    unknown_q = [{"key": q["key"], "wording": q["wording"], "answer": q["answer"], "category": q["category"]} for q in (c or {}).get("questions", []) if q["state"] == "UNKNOWN"]
    ledger = [{k: a.get(k) for k in ("domain", "label", "source", "check_type", "execution_id", "status", "health", "failure_category", "failure_detail", "evidence_count", "questions", "manual_required", "reused", "started_at", "completed_at", "ms")} for a in w["attempts"]]
    events = [{k: e.get(k) for k in ("at", "actor", "cls", "title", "detail", "ref")} for e in (c or {}).get("events", [])]
    from . import research
    manual = research.for_property(pid, license_id)
    task_for = {t["question_key"]: t["task_id"] for t in manual["tasks"] if t["status"] in research.ACTIVE}
    actions = [dict(a, task_id=task_for.get(a["question"])) for a in w["next_actions"]]
    gate_now = outreach_gate(c) if c else w["outreach"]
    if gate_now and gate_now.get("verdict") == "OUTREACH READY":
        gate_now["verdict"] = "OUTREACH READY FOR HUMAN REVIEW"
    return {"workup": {k: w[k] for k in ("workup_id", "status", "status_label", "version", "started_at", "completed_at", "actor", "timings", "error")}, "manual_research": manual,
            "identity": identity, "what_we_know": known, "checked_not_found": checked_nf, "tax": tax, "sections": sections, "listing": listing, "what_we_dont_know": unknown_q,
            "source_failures": w["failures"], "conflicts": w["conflicts"], "checks_performed": ledger, "next_actions": actions, "outreach_gate": gate_now, "outreach_gate_at_workup": w["outreach"],
            "investigation": ({"id": cid, "status": c["status"], "counts": c["counts"], "signals": [{"label": s["label"], "cls": s["cls"], "date": s["event_date"], "src": s["src"]} for s in c["signals"]], "bee": f"investigation.html?id={cid}"} if c else None),
            "timeline": events, "evidence": (c or {}).get("evidence", []),
            "links": {"investigation": f"investigation.html?id={cid}" if cid else None, "roll_file": f"lookup.html?county={prop.get('county_fips')}&q={prop.get('parcel_id') or prop.get('address') or ''}", "search": f"find.html?search={w['search_id']}"}}
