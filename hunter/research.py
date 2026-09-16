"""P8 — MANUAL RESEARCH INTAKE & DOCUMENT EVIDENCE.

    PROPERTY FILE → UNKNOWN / MANUAL_ONLY → RESEARCH TASK → a person checks → DOCUMENT / OBSERVATION / REFERENCE
    → MANUAL_VERIFICATION evidence (through cases.log_manual, the existing manual path)
    → cases.refresh (the existing question engine) → Property File → outreach gate re-evaluated (never sent)

One active task per property + question. Tasks come only from real gaps a workup found. A person's result is
typed exactly as P3A demands: a deed they read is a FACT; what they saw from the street is an OBSERVATION; a
guess is a NOTE and answers nothing. NOT_FOUND needs the source that answered. CONFLICTING keeps both records.
Nothing here fetches, scrapes, executes or infers. Bee has no entry point into this module.
"""
from __future__ import annotations

import re

from . import cases, db, store
from .db import jdump, jload, utcnow

VERSION = "p8.1"
STATUSES = ("OPEN", "IN_PROGRESS", "COMPLETED", "SKIPPED", "BLOCKED")
ACTIVE = ("OPEN", "IN_PROGRESS", "BLOCKED")
RESULTS = ("FOUND", "NOT_FOUND", "UNKNOWN", "CONFLICTING")
EVIDENCE_TYPES = ("FACT", "OBSERVATION", "CALCULATION", "ESTIMATE")
CONFIDENCES = ("HIGH", "MEDIUM", "LOW")

# question → task kind. A kind fixes the intake fields and the strongest evidence type a person can claim.
KIND_FOR = {"deed": "DEED_TITLE", "title": "DEED_TITLE", "lien_clerk": "LIEN_CLERK", "mailing_address": "MAILING_ADDRESS", "owner": "OWNER_RECORD",
            "listing": "LISTING", "sale_state": "LISTING", "inspection": "INSPECTION", "tax_state": "COLLECTOR", "tax_delinquent": "COLLECTOR",
            "collector_asof": "COLLECTOR", "lien_city": "MUNICIPAL", "lien_detail": "MUNICIPAL", "vacancy": "MUNICIPAL", "code": "MUNICIPAL", "code_detail": "MUNICIPAL",
            "state_inventory": "STATE_LANDS", "state_record": "STATE_LANDS", "state_history": "STATE_LANDS", "auction": "STATE_LANDS", "flood": "GENERIC", "access": "GENERIC",
            "values": "GENERIC", "identity": "GENERIC", "other_signals": "GENERIC"}
# kind → (title, what to check, why, what would resolve it, max evidence type, default evidence type, structured fields)
TEMPLATES = {
    "DEED_TITLE":      ("Review the latest deed at the county Circuit Clerk", "Read the most recent recorded deed for this parcel and record the instrument number, book/page, record date, grantor and grantee exactly as recorded.",
                        "Current ownership and the vesting record have not been established by an automated source; the tax roll is not a title search.",
                        "A deed/instrument identifying the ownership record. A title search result, if you ran one. Property Hunter never calls title clear on its own.",
                        "FACT", "FACT", ("record_source", "instrument_number", "book_page", "record_date", "grantor", "grantee", "legal_reference", "title_search_performed", "title_search_result", "observations")),
    "LIEN_CLERK":      ("Search the Circuit Clerk index for recorded liens", "Search the county's recorded-instrument index for mortgages, judgments and tax liens against this parcel or owner, and record what the index returned.",
                        "Recorded liens follow the property; no automated Clerk source exists.",
                        "The index result: each instrument's type, filing reference, record date, amount if recorded, and any release — or a dated 'no matching instruments' from the index you searched.",
                        "FACT", "FACT", ("record_source", "lien_type", "amount", "filing_reference", "record_date", "release_info", "observations")),
    "MAILING_ADDRESS": ("Verify the owner's mailing address from an authoritative record", "Read the mailing address on the county Assessor's or Collector's current record for this parcel and record it with the record's date.",
                        "Outreach cannot be prepared without a mailing address of record. The situs address is never used in its place.",
                        "The mailing address as recorded, its source and the record date. If the record is old, say so; the system will not assume it is current.",
                        "FACT", "FACT", ("record_source", "mailing_address", "record_date", "observations")),
    "OWNER_RECORD":    ("Read the owner of record at the county", "Read the owner name on the county's current record and record it with the record date.",
                        "The State roll copy may be dated; the county record is the authoritative owner of record.",
                        "The owner name exactly as recorded, source and record date.",
                        "FACT", "FACT", ("record_source", "owner_name", "record_date", "observations")),
    "LISTING":         ("Check whether a current listing record exists", "Search a public listing source for this address and record what the search returned.",
                        "No listing source is connected. Absence from one source is not 'not for sale', and nothing here says whether the owner wants to sell.",
                        "A listing record (URL or identifier, status, asking price and listing date as shown) — or a dated 'no listing returned' from the source you searched.",
                        "OBSERVATION", "OBSERVATION", ("source_checked", "date_checked", "search_result", "listing_url", "listing_id", "listing_status", "asking_price", "listing_date", "observations")),
    "INSPECTION":      ("Inspect the exterior in person", "Stand in front of the property and record what is visible: structure, occupancy indicators, exterior condition, roof, windows and doors, vegetation, posted notices, visible address, apparent access. Photograph what you describe.",
                        "Condition and occupancy are never inferred from records, registers, mailing addresses or aerials.",
                        "Your dated observations and photographs. They stay observations; the system draws no conclusion about distress, vacancy or abandonment from them.",
                        "OBSERVATION", "OBSERVATION", ("visible_structure", "apparent_occupancy_indicators", "exterior_condition", "roof_observation", "windows_doors_observation", "vegetation_observation", "posted_notice", "visible_address", "apparent_access", "safety_notes", "photographs")),
    "COLLECTOR":       ("Check the county Collector for this parcel", "Ask the Collector (online or by phone/counter) for the current bill and any delinquency on this parcel, and record the answer with its as-of date.",
                        "The automated Collector read did not answer. A missing answer is not delinquency; absence from State Lands is not 'current'.",
                        "The Collector's answer: bill status, amount owed, delinquent years, as-of date — or the Collector's own statement that it could not answer.",
                        "FACT", "FACT", ("record_source", "as_of_date", "bill_status", "amount_owed", "delinquent_years", "observations")),
    "MUNICIPAL":       ("Check the City record for this parcel", "Ask the City (register, lien layer or code office) about this parcel and record the answer.",
                        "The City source did not answer or does not cover this county.",
                        "The City's answer with its date, or a dated 'no record' from the City office.",
                        "FACT", "OBSERVATION", ("record_source", "record_date", "record_result", "reference", "observations")),
    "STATE_LANDS":     ("Check the Commissioner of State Lands record", "Search State Lands by parcel/RPID and record the listing, sale or redemption information shown.",
                        "Only the State can say whether the parcel is certified, sold or redeemed.",
                        "The State's per-parcel record with its date.",
                        "FACT", "FACT", ("record_source", "record_date", "record_result", "reference", "observations")),
    "GENERIC":         ("Resolve this question from a named source", "Check the source named on the property file and record exactly what it said.",
                        "The question is open and no automated source answered it.",
                        "A dated reading from a named source.",
                        "OBSERVATION", "OBSERVATION", ("record_source", "record_date", "record_result", "reference", "observations")),
}
INTAKE_LABELS = {"record_source": "Record source (office / site / person)", "instrument_number": "Instrument number", "book_page": "Book / page", "record_date": "Record date (YYYY-MM-DD)", "grantor": "Grantor",
                 "grantee": "Grantee", "legal_reference": "Legal description reference", "title_search_performed": "Did you run a title search? (yes/no)", "title_search_result": "Title search result, if run",
                 "observations": "Observations (what you saw, in your words)", "lien_type": "Lien / instrument type", "amount": "Amount, only if recorded", "filing_reference": "Filing reference",
                 "release_info": "Release / satisfaction, if present", "mailing_address": "Mailing address as recorded", "owner_name": "Owner name as recorded", "source_checked": "Source checked",
                 "date_checked": "Date checked (YYYY-MM-DD)", "search_result": "What the search returned", "listing_url": "Listing URL, only if present", "listing_id": "Listing identifier, if present",
                 "listing_status": "Listing status as shown", "asking_price": "Asking price, only if shown", "listing_date": "Listing date as shown", "visible_structure": "Visible structure",
                 "apparent_occupancy_indicators": "Apparent occupancy indicators (lights, vehicles, mail, curtains…)", "exterior_condition": "Exterior condition observations", "roof_observation": "Roof observation",
                 "windows_doors_observation": "Windows / doors observation", "vegetation_observation": "Vegetation observation", "posted_notice": "Posted notices (text as posted)", "visible_address": "Visible address",
                 "apparent_access": "Apparent access", "safety_notes": "Safety notes", "photographs": "Photographs (photo ids or local references)", "as_of_date": "As-of date (YYYY-MM-DD)",
                 "bill_status": "Bill status as stated", "amount_owed": "Amount owed as stated", "delinquent_years": "Delinquent years as stated", "record_result": "What the record said", "reference": "Reference"}
DOC_TYPES = ("DEED", "TITLE_SEARCH", "CLERK_INDEX", "ASSESSOR_RECORD", "COLLECTOR_RECORD", "LISTING", "PHOTO", "CITY_RECORD", "STATE_RECORD", "OTHER")
# a request may carry only these keys; anything else (evidence_id, property_id, url, sql, command…) is refused
COMPLETE_KEYS = {"actor", "result", "source", "date", "fields", "document", "notes", "reference", "evidence_type", "confidence", "photo_ids", "document_ids", "conflict_with"}
DOC_KEYS = {"doc_type", "title", "source", "record_date", "retrieved_at", "instrument", "book_page", "reference", "url", "local_reference", "notes"}


# ------------------------------------------------------------------ tasks from real gaps
def ensure_tasks(workup_id: int) -> list[dict]:
    """Create one OPEN task per unresolved question the workup left UNKNOWN / MANUAL_ONLY. Idempotent."""
    w = db.q1("SELECT * FROM workups WHERE id=?", (workup_id,))
    if not w or not w["investigation_id"]:
        return []
    actions = jload(w["next_actions_json"], []) or []
    qs = jload(w["questions_json"], {}) or {}
    made = []
    for a in actions:
        key = a["question"]
        if (qs.get(key) or {}).get("state") != "UNKNOWN":
            continue
        made.append(_ensure_task(w["investigation_id"], w["property_id"], key, a, workup_id, w["license_id"], w["actor"]))
    return made


def _ensure_task(case_id: int, pid: int, key: str, action: dict, workup_id: int | None, license_id: int, actor: str) -> dict:
    active = db.q1("SELECT id FROM research_tasks WHERE property_id=? AND question_key=? AND status IN ('OPEN','IN_PROGRESS','BLOCKED')", (pid, key))
    if active:
        return view(active["id"])
    kind = KIND_FOR.get(key, "GENERIC")
    title, what, why, resolves, _max, _default, _fields = TEMPLATES[kind]
    where = action.get("where") or cases.next_action(key, store.get_property(pid) or {}, (cases.property_row(pid) or {}))
    now = utcnow()
    cur = db.ex("INSERT INTO research_tasks(case_id, property_id, question_key, kind, title, purpose, what_to_check, why, where_json, what_would_resolve, status, priority, "
                "created_at, actor, source_destination, created_from_workup_id, license_id, version, state_before) VALUES(?,?,?,?,?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?)",
                (case_id, pid, key, kind, title, cases.QUESTIONS[key][1], what, why, jdump(where), resolves, int(action.get("priority") or 99), now, actor,
                 (where or {}).get("href"), workup_id, license_id or 0, VERSION, "UNKNOWN"))
    cases._event(case_id, "RESEARCH TASK OPENED", f"{title} ({key})", f"from workup {workup_id}: {action.get('why') or why}", f"research_task:{cur.lastrowid}", "property_hunter")
    return view(cur.lastrowid)


def open_for_question(case_id: int, key: str, *, actor: str, license_id: int = 0, purpose: str = "GAP") -> dict:
    """CLOSE THIS GAP on a question the file shows UNKNOWN: the task-specific workflow, never a blank form.
    purpose=CONFLICT opens the same workflow on an answered question so a person can record a record that disagrees."""
    c = db.q1("SELECT * FROM investigation_cases WHERE id=?", (case_id,))
    if not c or key not in cases.QUESTIONS:
        raise KeyError("no such case or question")
    if purpose not in ("GAP", "CONFLICT"):
        raise ValueError("purpose must be GAP or CONFLICT")
    q = db.q1("SELECT state FROM investigation_questions WHERE case_id=? AND key=?", (case_id, key))
    if q and q["state"] != "UNKNOWN" and purpose != "CONFLICT":
        active = db.q1("SELECT id FROM research_tasks WHERE property_id=? AND question_key=? AND status IN ('OPEN','IN_PROGRESS','BLOCKED')", (c["property_id"], key))
        if active:
            return view(active["id"])
        raise ValueError(f"the question is already {q['state']} from recorded evidence; nothing to close")
    prop = store.get_property(c["property_id"]) or {}
    action = {"question": key, "where": cases.next_action(key, prop, cases.property_row(c["property_id"]) or {}), "priority": cases.PRIORITY.index(key) if key in cases.PRIORITY else 99,
              "why": "opened by a person from the property file" if purpose == "GAP" else "opened by a person to record a record that disagrees with the answer on file"}
    return _ensure_task(case_id, c["property_id"], key, action, None, license_id, actor)


# ------------------------------------------------------------------ lifecycle
def _task(task_id: int, license_id: int | None = None):
    t = db.q1("SELECT * FROM research_tasks WHERE id=?", (task_id,))
    if not t or (license_id is not None and t["license_id"] != license_id):
        raise KeyError("no such task on this license")
    return t


def start(task_id: int, *, actor: str, license_id: int | None = None) -> dict:
    t = _task(task_id, license_id)
    actor = (actor or "").strip()[:40]
    if not actor:
        raise ValueError("name the person doing the research")
    if t["status"] == "OPEN" or t["status"] == "BLOCKED":
        db.ex("UPDATE research_tasks SET status='IN_PROGRESS', started_at=IFNULL(started_at, ?), actor=?, updated_at=? WHERE id=?", (utcnow(), actor, utcnow(), task_id))
        cases._event(t["case_id"], "RESEARCH TASK STARTED", f"{t['title']} ({t['question_key']})", f"by {actor}", f"research_task:{task_id}", actor)
    return view(task_id)


def skip(task_id: int, *, actor: str, reason: str, blocked: bool = False, license_id: int | None = None) -> dict:
    t = _task(task_id, license_id)
    actor = (actor or "").strip()[:40]; reason = (reason or "").strip()[:500]
    if not actor or len(reason) < 4:
        raise ValueError("name the person and say why")
    if t["status"] == "COMPLETED":
        raise ValueError("a completed task stays completed; open a new task if the question reopens")
    st = "BLOCKED" if blocked else "SKIPPED"
    db.ex("UPDATE research_tasks SET status=?, actor=?, notes=?, completed_at=?, updated_at=? WHERE id=?", (st, actor, reason, None if blocked else utcnow(), utcnow(), task_id))
    cases._event(t["case_id"], "RESEARCH TASK BLOCKED" if blocked else "RESEARCH TASK SKIPPED", f"{t['title']} ({t['question_key']})", f"{actor}: {reason}", f"research_task:{task_id}", actor)
    cases._touch(t["case_id"])
    return view(task_id)


def _date(v, what="date"):
    v = (v or "").strip()
    if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise ValueError(f"{what} must be YYYY-MM-DD")
    return v or None


def _yes(v) -> bool:
    return str(v or "").strip().lower() in ("yes", "y", "true", "1")


def add_document(pid: int, doc: dict, *, actor: str, case_id: int | None = None, task_id: int | None = None) -> int:
    """A reference to what the person read: never the contents, never invented. private=1 always."""
    extra = set(doc) - DOC_KEYS
    if extra:
        raise ValueError(f"document may carry only {sorted(DOC_KEYS)} (unexpected: {', '.join(sorted(extra))[:60]})")
    dt = (doc.get("doc_type") or "OTHER").upper()
    if dt not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {DOC_TYPES}")
    title = (doc.get("title") or "").strip()[:160]
    if not title:
        raise ValueError("give the document a title (e.g. 'Warranty deed, instrument 2024-12345')")
    url = (doc.get("url") or "").strip() or None
    url = url if url and re.match(r"^https?://", url) else None
    cur = db.ex("INSERT INTO documents(property_id, category, title, path, url, notes, added_at, doc_type, source, record_date, retrieved_at, instrument, book_page, reference, actor, private, case_id, task_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (pid, "manual_verification", title, (doc.get("local_reference") or "").strip()[:300] or None, url, (doc.get("notes") or "").strip()[:1000] or None, utcnow(), dt,
                 (doc.get("source") or "").strip()[:160] or None, _date(doc.get("record_date"), "record_date"), _date(doc.get("retrieved_at"), "retrieved_at") or utcnow()[:10],
                 (doc.get("instrument") or "").strip()[:80] or None, (doc.get("book_page") or "").strip()[:80] or None, (doc.get("reference") or "").strip()[:160] or None, actor, case_id, task_id))
    return cur.lastrowid


def complete(task_id: int, payload: dict, *, actor: str, license_id: int | None = None) -> dict:
    """The person's result becomes MANUAL_VERIFICATION evidence through cases.log_manual, typed by what it is.
    FOUND needs a result that establishes the fact; NOT_FOUND needs the source that answered; UNKNOWN answers nothing;
    CONFLICTING records both sides and answers nothing. Idempotent: a completed task is not completed twice."""
    t = _task(task_id, license_id)
    if not isinstance(payload, dict):
        raise ValueError("send a JSON object")
    extra = set(payload) - COMPLETE_KEYS
    if extra:
        raise ValueError(f"only {sorted(COMPLETE_KEYS)} are accepted (unexpected: {', '.join(sorted(extra))[:60]})")
    if t["status"] == "COMPLETED":
        return dict(view(task_id), idempotent=True)
    actor = (actor or payload.get("actor") or "").strip()[:40]
    if not actor:
        raise ValueError("name the person who did the research")
    result = (payload.get("result") or "").strip().upper()
    if result not in RESULTS:
        raise ValueError(f"result must be one of {RESULTS}")
    source = (payload.get("source") or "").strip()[:160]
    date = _date(payload.get("date")) or utcnow()[:10]
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    kind = t["kind"]
    title, _what, _why, _resolves, max_type, default_type, allowed = TEMPLATES[kind]
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"this {kind} intake accepts only {allowed} (unexpected: {', '.join(sorted(unknown))[:60]})")
    fields = {k: (str(v).strip()[:500] if v is not None else "") for k, v in fields.items()}
    et = (payload.get("evidence_type") or default_type).upper()
    conf = (payload.get("confidence") or "MEDIUM").upper()
    if et not in EVIDENCE_TYPES or conf not in CONFIDENCES:
        raise ValueError(f"evidence_type must be one of {EVIDENCE_TYPES} and confidence one of {CONFIDENCES}")
    rank = {"OBSERVATION": 1, "ESTIMATE": 1, "CALCULATION": 2, "FACT": 3}
    if rank[et] > rank[max_type]:
        raise ValueError(f"a {kind} result can be at most {max_type}; what a person saw or worked out is not a recorded fact")
    if kind == "INSPECTION":
        et = "OBSERVATION"                                           # standing in front of it is always an observation
    notes = (payload.get("notes") or "").strip()[:2000]
    reference = (payload.get("reference") or "").strip()[:200]
    pid, cid, key = t["property_id"], t["case_id"], t["question_key"]
    # documents and photos: references that belong to THIS property only
    doc_id = None
    if payload.get("document"):
        doc_id = add_document(pid, payload["document"], actor=actor, case_id=cid, task_id=task_id)
    doc_ids = []
    for d in payload.get("document_ids") or []:
        row = db.q1("SELECT id FROM documents WHERE id=? AND property_id=?", (int(d), pid))
        if not row:
            raise ValueError(f"document {d} is not on this property")
        doc_ids.append(row["id"])
    photo_ids = []
    for p in payload.get("photo_ids") or []:
        row = db.q1("SELECT id FROM photos WHERE id=? AND property_id=?", (int(p), pid))
        if not row:
            raise ValueError(f"photo {p} is not on this property")
        photo_ids.append(row["id"])
    if kind == "INSPECTION" and fields.get("photographs"):
        reference = (reference + "; " if reference else "") + f"photographs: {fields['photographs']}"
    # what the result establishes, kind by kind
    rows: list[tuple[str, str, str, str]] = []          # (question key, state, result text, evidence_type)
    summary = _summary(kind, fields, notes)
    if result == "FOUND":
        if not source and not fields.get("record_source") and not fields.get("source_checked"):
            raise ValueError("FOUND needs the source you read")
        if not summary:
            raise ValueError("FOUND needs what the record established, in the structured fields")
        rows += _found_rows(kind, key, fields, summary)
    elif result == "NOT_FOUND":
        src = source or fields.get("record_source") or fields.get("source_checked")
        if not src:
            raise ValueError("NOT_FOUND needs the source that answered your search")
        if not (fields.get("search_result") or fields.get("record_result") or fields.get("observations") or notes):
            raise ValueError("NOT_FOUND needs what you searched for and what the source returned")
        rows += _not_found_rows(kind, key, fields, notes, src)
    elif result == "UNKNOWN":
        rows.append((key, "UNKNOWN", f"could not determine: {notes or summary or 'no reason given'}", "OBSERVATION"))
    else:  # CONFLICTING
        other = (payload.get("conflict_with") or "").strip()[:300]
        if not other or not summary:
            raise ValueError("CONFLICTING needs both records: the structured fields for what you read, and conflict_with naming the other record")
        rows.append((key, None, f"CONFLICTING RECORDS: {summary} — versus — {other}", et))
    src_name = source or fields.get("record_source") or fields.get("source_checked") or "source not named"
    evidence_refs = []
    q_before = dict(db.q1("SELECT state FROM investigation_questions WHERE case_id=? AND key=?", (cid, key)) or {})
    for qkey, state, text, etype in rows:
        out = cases.log_manual(cid, {"source": src_name, "date": date, "question": qkey, "state": state, "result": text, "notes": notes or None,
                                     "source_url": (fields.get("listing_url") if kind == "LISTING" else None), "document_id": doc_id,
                                     "evidence_type": etype, "confidence": conf, "fields": fields, "reference": reference,
                                     "document": None}, actor=actor)
        if out.get("evidence_ref"):
            evidence_refs.append(out["evidence_ref"])
    if result == "CONFLICTING":
        auto = _automated_answer(pid, key)
        db.ex("INSERT INTO conflicts(property_id, field, value_a, source_a, date_a, value_b, source_b, date_b, status, created_at) VALUES(?,?,?,?,?,?,?,?,'NEEDS VERIFICATION',?)",
              (pid, f"manual:{key}", (auto or {}).get("value") or payload.get("conflict_with"), (auto or {}).get("source") or "record named by the person", (auto or {}).get("effective_date"),
               summary, f"{src_name} — MANUAL VERIFICATION by {actor}", date, utcnow()))
        cases._event(cid, "CONFLICT RECORDED", f"{cases.QUESTIONS[key][1]}: records disagree", f"{summary} vs {payload.get('conflict_with')}; both kept; the question is not answered by either", evidence_refs[0] if evidence_refs else f"question:{key}", actor, date)
    for d in doc_ids:
        db.ex("UPDATE documents SET task_id=?, case_id=? WHERE id=?", (task_id, cid, d))
    if evidence_refs:
        eid = int(evidence_refs[0].split(":")[1])
        db.ex("UPDATE documents SET evidence_id=? WHERE task_id=? AND evidence_id IS NULL", (eid, task_id))
    cases.refresh(cid)                                                # the existing engine; a person's answer stands (P2/P3A)
    q_after = dict(db.q1("SELECT state FROM investigation_questions WHERE case_id=? AND key=?", (cid, key)) or {})
    from . import workup as _w
    gate = _w.outreach_gate(cases.get_case(cid))
    if gate["verdict"] == "OUTREACH READY":
        gate["verdict"] = "OUTREACH READY FOR HUMAN REVIEW"
    now = utcnow()
    db.ex("UPDATE research_tasks SET status='COMPLETED', actor=?, completed_at=?, updated_at=?, result_state=?, result_evidence_id=?, document_id=?, notes=?, reference=?, intake_json=?, "
          "photo_ids_json=?, document_ids_json=?, evidence_refs_json=?, state_after=?, outreach_json=?, evidence_type=?, confidence=? WHERE id=?",
          (actor, now, now, result, int(evidence_refs[0].split(":")[1]) if evidence_refs else None, doc_id, notes or None, reference or None, jdump(fields), jdump(photo_ids), jdump(doc_ids + ([doc_id] if doc_id else [])),
           jdump(evidence_refs), q_after.get("state"), jdump(gate), et, conf, task_id))
    cases._event(cid, "RESEARCH TASK COMPLETED", f"{t['title']} ({key}) → {result}", f"{actor}; {src_name}; {date}; question {q_before.get('state') or '?'} → {q_after.get('state') or '?'}; evidence {', '.join(evidence_refs) or 'none'}" + (f"; document:{doc_id}" if doc_id else ""), f"research_task:{task_id}", actor, date)
    cases._event(cid, "OUTREACH GATE RE-EVALUATED", gate["verdict"], gate["explanation"], f"research_task:{task_id}", "property_hunter")
    cases._touch(cid)
    return view(task_id)


def _summary(kind: str, f: dict, notes: str) -> str:
    g = lambda k: f.get(k) or ""
    if kind == "DEED_TITLE":
        parts = [x for x in (f"instrument {g('instrument_number')}" if g("instrument_number") else "", f"book/page {g('book_page')}" if g("book_page") else "", f"recorded {g('record_date')}" if g("record_date") else "",
                             f"grantor {g('grantor')}" if g("grantor") else "", f"grantee {g('grantee')}" if g("grantee") else "", f"legal {g('legal_reference')}" if g("legal_reference") else "") if x]
        return "; ".join(parts)
    if kind == "LIEN_CLERK":
        return "; ".join(x for x in (g("lien_type"), f"amount {g('amount')}" if g("amount") else "", f"filing {g('filing_reference')}" if g("filing_reference") else "", f"recorded {g('record_date')}" if g("record_date") else "", f"release {g('release_info')}" if g("release_info") else "") if x)
    if kind == "MAILING_ADDRESS":
        return g("mailing_address") + (f" (record date {g('record_date')})" if g("record_date") else "")
    if kind == "OWNER_RECORD":
        return g("owner_name") + (f" (record date {g('record_date')})" if g("record_date") else "")
    if kind == "LISTING":
        return "; ".join(x for x in (g("listing_status"), g("listing_url") or g("listing_id"), f"asking {g('asking_price')}" if g("asking_price") else "", f"listed {g('listing_date')}" if g("listing_date") else "", g("search_result")) if x)
    if kind == "INSPECTION":
        return "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in f.items() if v and k != "photographs")
    if kind == "COLLECTOR":
        return "; ".join(x for x in (g("bill_status"), f"owed {g('amount_owed')}" if g("amount_owed") else "", f"delinquent years {g('delinquent_years')}" if g("delinquent_years") else "", f"as of {g('as_of_date')}" if g("as_of_date") else "") if x)
    return "; ".join(x for x in (g("record_result"), g("reference"), g("observations"), notes) if x)


def _found_rows(kind: str, key: str, f: dict, summary: str) -> list[tuple]:
    """What a FOUND result establishes. A deed establishes the deed record and (grantee) the owner of record;
    it never establishes title unless the person ran a title search and says so."""
    if kind == "DEED_TITLE":
        rows = []
        if f.get("instrument_number") or f.get("book_page") or f.get("record_date"):
            rows.append(("deed", "FOUND", f"Deed record read by a person: {summary}", "FACT"))
        if f.get("grantee"):
            rows.append(("owner", "FOUND", f"Grantee on the latest deed read: {f['grantee']} (deed {f.get('instrument_number') or f.get('book_page') or 'reference not given'}, recorded {f.get('record_date') or 'date not given'}); a deed grantee, not a title opinion", "FACT"))
        if _yes(f.get("title_search_performed")) and f.get("title_search_result"):
            rows.append(("title", "FOUND", f"Title search run by a person: {f['title_search_result']}", "FACT"))
        elif key == "title":
            rows.append(("title", "UNKNOWN", "TITLE STATUS: UNKNOWN — a deed was read but no title search was run; Property Hunter never declares title clear", "OBSERVATION"))
        if not rows:
            rows.append((key, "FOUND", summary, "FACT"))
        return rows
    if kind == "LIEN_CLERK":
        return [("lien_clerk", "FOUND", f"Recorded instrument found by a person: {summary}", "FACT")]
    if kind == "MAILING_ADDRESS":
        return [("mailing_address", "FOUND", f"{f.get('mailing_address')} (mailing address of record per {f.get('record_source') or 'the source named'}; record date {f.get('record_date') or 'not given'}; not assumed current beyond that date)", "FACT")]
    if kind == "OWNER_RECORD":
        return [("owner", "FOUND", f"{f.get('owner_name')} (owner of record per {f.get('record_source') or 'the source named'}, record date {f.get('record_date') or 'not given'})", "FACT")]
    if kind == "LISTING":
        if not (f.get("listing_url") or f.get("listing_id")):
            raise ValueError("a FOUND listing needs the listing URL or identifier as shown")
        return [("listing", "FOUND", f"Listing record seen by a person: {summary}", "OBSERVATION"), ("sale_state", "FOUND", f"PRIVATE LISTING seen on {f.get('source_checked') or 'the source checked'}: {f.get('listing_status') or 'status not shown'}", "OBSERVATION")]
    if kind == "INSPECTION":
        return [("inspection", "FOUND", f"Exterior observations by a person: {summary}", "OBSERVATION")]
    if kind == "COLLECTOR":
        return [("tax_state", "FOUND", f"Collector answer read by a person: {summary}", "FACT"), ("collector_asof", "FOUND", f"Collector answered {f.get('as_of_date') or 'date not given'}: {summary}", "FACT")]
    return [(key, "FOUND", summary, "OBSERVATION" if TEMPLATES[kind][4] == "OBSERVATION" else "FACT")]


def _not_found_rows(kind: str, key: str, f: dict, notes: str, src: str) -> list[tuple]:
    what = f.get("search_result") or f.get("record_result") or f.get("observations") or notes
    if kind == "LISTING":
        return [("listing", "NOT_FOUND", f"No listing returned by {src} on {f.get('date_checked') or 'the date checked'}: {what}. Not 'not for sale'; one source, one day", "OBSERVATION")]
    if kind == "LIEN_CLERK":
        return [("lien_clerk", "NOT_FOUND", f"Index searched at {src}: {what} — no matching instrument returned for the search described", "FACT")]
    if kind == "DEED_TITLE":
        return [("deed", "NOT_FOUND", f"Searched {src}: {what}", "FACT"), ("title", "UNKNOWN", "TITLE STATUS: UNKNOWN — no deed located; nothing establishes title", "OBSERVATION")] if key != "title" else [("title", "NOT_FOUND", f"Title search at {src}: {what}", "FACT")]
    if kind == "INSPECTION":
        return [("inspection", "NOT_FOUND", f"Inspected from {src}: {what} (nothing of the kind described was visible)", "OBSERVATION")]
    return [(key, "NOT_FOUND", f"Checked {src}: {what}", "OBSERVATION")]


def _automated_answer(pid: int, key: str):
    field = {"owner": "owner_name", "mailing_address": "owner_mailing_address", "deed": "deed_reference", "tax_state": "tax_bill", "flood": "flood_zone", "access": "road_access",
             "lien_city": "cleanup_lien_amount", "vacancy": "vacant_structure", "code": "code_case_open"}.get(key)
    return store.latest_answer(pid, field) if field else None


# ------------------------------------------------------------------ read models
def view(task_id: int, license_id: int | None = None) -> dict | None:
    try:
        t = dict(_task(task_id, license_id))
    except KeyError:
        return None
    title, what, why, resolves, max_type, default_type, fields = TEMPLATES[t["kind"]]
    q = db.q1("SELECT * FROM investigation_questions WHERE case_id=? AND key=?", (t["case_id"], t["question_key"]))
    pid = t["property_id"]
    known = [dict(store.with_origin(dict(r)), ref=f"evidence:{r['id']}") for r in db.q("SELECT * FROM evidence WHERE property_id=? AND (field=? OR field=?) AND superseded_by IS NULL ORDER BY id DESC LIMIT 6", (pid, f"manual:{t['question_key']}", _automated_field(t["question_key"]) or "-"))]
    docs = [dict(r) for r in db.q("SELECT id, doc_type, title, source, record_date, retrieved_at, instrument, book_page, reference, url, path, actor, added_at, evidence_id FROM documents WHERE task_id=? ORDER BY id", (task_id,))]
    return {"task_id": t["id"], "case_id": t["case_id"], "property_id": pid, "question_key": t["question_key"], "kind": t["kind"], "title": t["title"], "purpose": t["purpose"],
            "what_to_check": t["what_to_check"], "why": t["why"], "where": jload(t["where_json"], None), "what_would_resolve": t["what_would_resolve"], "status": t["status"],
            "priority": t["priority"], "created_at": t["created_at"], "started_at": t["started_at"], "completed_at": t["completed_at"], "updated_at": t.get("updated_at"), "actor": t["actor"],
            "result_state": t["result_state"], "result_evidence_id": t["result_evidence_id"], "evidence_refs": jload(t["evidence_refs_json"], []) or [], "document_id": t["document_id"],
            "documents": docs, "photo_ids": jload(t["photo_ids_json"], []) or [], "notes": t["notes"], "reference": t["reference"], "intake": jload(t["intake_json"], None),
            "evidence_type": t["evidence_type"], "confidence": t["confidence"], "source_destination": t["source_destination"], "created_from_workup_id": t["created_from_workup_id"], "version": t["version"],
            "state_before": t["state_before"], "state_after": t["state_after"], "outreach": jload(t["outreach_json"], None),
            "question": ({"key": q["key"], "wording": q["wording"], "state": q["state"], "answer": q["answer"], "checked_by": q["checked_by"], "source": q["source"], "checked_at": q["checked_at"]} if q else None),
            "intake_schema": {"fields": [{"key": k, "label": INTAKE_LABELS.get(k, k)} for k in fields], "max_evidence_type": max_type, "default_evidence_type": default_type,
                              "results": RESULTS, "evidence_types": [e for e in EVIDENCE_TYPES if {"OBSERVATION": 1, "ESTIMATE": 1, "CALCULATION": 2, "FACT": 3}[e] <= {"OBSERVATION": 1, "ESTIMATE": 1, "CALCULATION": 2, "FACT": 3}[max_type]],
                              "confidences": CONFIDENCES, "doc_types": DOC_TYPES, "doc_fields": sorted(DOC_KEYS)},
            "evidence_known": [{"ref": e["ref"], "field": e["field"], "value": (e.get("value") or "")[:200], "origin_label": e.get("origin_label"), "source": e.get("source_name") or e.get("source"), "date": (e.get("effective_date") or "")[:10]} for e in known],
            "links": {"property_file": None, "investigation": f"investigation.html?id={t['case_id']}"}}


def _automated_field(key: str) -> str | None:
    return {"owner": "owner_name", "mailing_address": "owner_mailing_address", "deed": "deed_reference", "tax_state": "tax_bill", "flood": "flood_zone", "access": "road_access",
            "lien_city": "cleanup_lien_amount", "vacancy": "vacant_structure", "code": "code_case_open", "inspection": "photo:inspection", "listing": "manual:sale_state"}.get(key)


def for_property(pid: int, license_id: int | None = None) -> dict:
    """The MANUAL RESEARCH section: every task, grouped, plus the latest manual evidence and documents. Readable on its own."""
    rows = db.q("SELECT id FROM research_tasks WHERE property_id=? " + ("AND license_id=? " if license_id is not None else "") + "ORDER BY CASE status WHEN 'IN_PROGRESS' THEN 0 WHEN 'OPEN' THEN 1 WHEN 'BLOCKED' THEN 2 ELSE 3 END, priority, id",
                (pid, license_id) if license_id is not None else (pid,))
    tasks = [view(r["id"]) for r in rows]
    for t in tasks:
        t["last_action"] = (f"completed {t['completed_at'][:16]} by {t['actor']} → {t['result_state']}" if t["status"] == "COMPLETED" else f"{t['status'].lower().replace('_', ' ')} since {(t['started_at'] or t['created_at'])[:16]}" + (f" by {t['actor']}" if t["actor"] else ""))
        t["next_action"] = {"OPEN": "start the task and check the source", "IN_PROGRESS": "record what you found", "BLOCKED": t["notes"] or "blocked", "SKIPPED": "skipped; reopen from the property file if needed", "COMPLETED": "nothing; the evidence is on file"}[t["status"]]
    manual_ev = [dict(store.with_origin(dict(r)), ref=f"evidence:{r['id']}") for r in db.q("SELECT * FROM evidence WHERE property_id=? AND origin='MANUAL_VERIFICATION' ORDER BY id DESC LIMIT 20", (pid,))]
    docs = [dict(r) for r in db.q("SELECT id, doc_type, title, source, record_date, retrieved_at, instrument, book_page, reference, url, path, actor, added_at, task_id, evidence_id FROM documents WHERE property_id=? AND category='manual_verification' ORDER BY id DESC LIMIT 50", (pid,))]
    conflicts = [dict(r) for r in db.q("SELECT id, field, value_a, source_a, date_a, value_b, source_b, date_b, status, created_at FROM conflicts WHERE property_id=? AND field LIKE 'manual:%' AND status='NEEDS VERIFICATION' ORDER BY id DESC", (pid,))]
    return {"property_id": pid, "open": [t for t in tasks if t["status"] in ("OPEN", "IN_PROGRESS")], "blocked": [t for t in tasks if t["status"] == "BLOCKED"],
            "completed": [t for t in tasks if t["status"] == "COMPLETED"], "skipped": [t for t in tasks if t["status"] == "SKIPPED"], "tasks": tasks,
            "latest_manual_evidence": [{"ref": e["ref"], "field": e["field"], "value": (e.get("value") or "")[:200], "type": e.get("evidence_type"), "confidence": e.get("confidence"), "date": (e.get("effective_date") or "")[:10],
                                        "recorded_at": (e.get("created_at") or "")[:16], "source": e.get("source_name"), "superseded": bool(e.get("superseded_by"))} for e in manual_ev],
            "documents": docs, "conflicts": conflicts, "counts": {s.lower(): sum(1 for t in tasks if t["status"] == s) for s in STATUSES}}
