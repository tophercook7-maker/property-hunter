"""P4 BEE INVESTIGATOR FOUNDATION.

Bee is an evidence-first investigation assistant that works on ONE investigation case at a time.
Bee may propose, explain, prioritise and summarise. Bee never creates or changes evidence, never
answers a question FOUND / NOT_FOUND, never contacts anyone, never drafts outreach, and never turns a
guess into a fact. Everything Bee writes is persisted as origin AI_OPINION (model output) or DERIVED
(deterministic rules); neither is ever an evidence row.

Flow:  CASE -> SNAPSHOT (deterministic, reference-based) -> RULES (known / unknown / conflicts /
candidate checks, DERIVED) -> MODEL (chooses, orders and explains candidates; AI_OPINION; strictly
validated) -> PROPOSALS (PROPOSED until a person decides) -> HUMAN / EXISTING SAFE CHECK -> new
evidence enters the canonical pipeline -> Bee sees the new state.

The model can only pick from candidate checks that the rules generated from the case's open questions
and the county source registry: it cannot invent a source, a URL, a check, a fact or a completion.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from . import ai, cases, db, store
from .db import jdump, jload, utcnow

PROVIDER = "ollama"
PROMPT_VERSION = "bee-p4-1"
SCHEMA_VERSION = "bee-output-1"
PROPOSAL_TYPES = ("SOURCE_CHECK", "MANUAL_VERIFICATION", "DOCUMENT_REVIEW", "RECORD_COMPARISON", "FOLLOW_UP_RESEARCH")
PROPOSAL_STATUSES = ("PROPOSED", "ACCEPTED", "REJECTED", "COMPLETED", "BLOCKED", "EXECUTED_NO_ANSWER")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DATA = os.path.join(ROOT, "docs", "data")
PRIVATE_FIELDS = ("owner_mailing_address", "manual:mailing_address")     # the value never reaches the model or the export; only that it exists, its origin and date
# words the model may not use inside KNOWN items or the summary as if they were facts
# topics Bee may never even speculate about: the records do not carry them, and P3B/P3C keep them out of outreach
FORBIDDEN_INFERENCE_TOPICS = ("vacant", "vacancy", "distress", "motivated", "motivation", "for sale", "willing to sell", "abandoned", "clear title", "title is clear", "wants to sell", "eager")
INFERENCE_WORDS = ("vacant", "motivated", "distressed", "for sale", "not for sale", "clear title", "willing to sell", "abandoned", "delinquent", "owes", "current address", "still lives", "stale")


# ------------------------------------------------------------------ snapshot

def _registry(cf: str) -> dict:
    try:
        d = json.load(open(os.path.join(DOCS_DATA, "tax_sources.json")))
        c = (d.get("counties") or {}).get(cf) or {}
        return {k: {"status": v.get("status"), "public_url": v.get("public_url"), "name": v.get("source_name"), "last_checked": v.get("last_checked")} for k, v in (c.get("sources") or {}).items()}
    except Exception:
        return {}


def snapshot(case_id: int) -> dict | None:
    """Deterministic, reference-based view of one case. Sorted, no secrets, private values redacted."""
    c = cases.get_case(case_id)
    if not c:
        return None
    prop = c["property"]
    ev = []
    for e in sorted(c["evidence"], key=lambda x: int(x["ref"].split(":")[1])):
        ev.append({"ref": e["ref"], "field": e["field"], "value": ("[value held locally]" if e["field"] in PRIVATE_FIELDS else (e["value"] or "")[:160]),
                   "origin": e["origin"], "type": e["etype"], "confidence": e["conf"], "date": e["date"], "source": e["source_name"] or e["source"],
                   "superseded": bool(e.get("superseded")), "conflict": bool(e.get("conflict"))})
    qs = []
    for q in sorted(c["questions"], key=lambda x: x["key"]):
        qs.append({"key": q["key"], "category": q["category"], "wording": q["wording"], "state": q["state"], "answer": (q["answer"] or "")[:200] if q["key"] != "mailing_address" or q["state"] != "FOUND" else "[mailing address of record held locally]",
                   "checked_by": q["checked_by"], "source": q["source"], "checked_at": q["checked_at"], "evidence_refs": sorted(q.get("evidence_refs") or []),
                   "next": {"label": q["next"]["label"], "href": q["next"]["href"], "manual": q["next"]["manual"]}})
    sigs = [{"event": s["event"], "cls": s["cls"], "label": s["label"], "date": s["event_date"], "source": s["src"], "ref": s["evidence_ref"]} for s in sorted(c["signals"], key=lambda x: x["id"])]
    # Bee's own past commentary is not part of the case state it reasons about (and would make the hash self-referential)
    events = [{"at": e["at"], "cls": e["cls"], "title": (e["title"] or "")[:140], "ref": e["ref"]} for e in c["events"] if e["cls"] not in ("BEE ANALYSIS", "BEE PROPOSAL DECISION")][-40:]
    conflicts = sorted({e["field"] for e in c["evidence"] if e.get("conflict")})
    tax = c["tax"]
    outreach = [{"purpose": o["purpose"], "status": o["status"], "draft_version": o["current_version"], "gate_ready": (o.get("gate") or {}).get("ready"), "blocking": (o.get("gate") or {}).get("blocking")} for o in _outreach_for(case_id)]
    snap = {
        "schema": SCHEMA_VERSION, "investigation_id": c["investigation_id"], "property_id": c["property_id"], "status": c["status"],
        "property": {"address": prop.get("address"), "parcel_id": prop.get("parcel_id"), "county": prop.get("county"), "county_fips": prop.get("county_fips"), "city": prop.get("city"),
                     "owner_of_record": prop.get("owner"), "appraised_total": prop.get("total_value")},
        "why_case_exists": [f"{s['label']} ({s['cls']}, {s['date'] or 'undated'}, {s['source'] or 'source not named'})" for s in sigs],
        "signals": sigs, "questions": qs, "evidence": ev, "conflicts": conflicts,
        "tax": {"state": ("SOURCE_UNAVAILABLE" if tax.get("source_unavailable") else tax.get("st")), "source": tax.get("src"), "as_of": tax.get("as_of"), "amount": tax.get("amt"), "evidence": tax.get("evidence")},
        "sale": {"state": c["sale"]["st"], "source": c["sale"]["src"]},
        "title_state": next((q["state"] for q in qs if q["key"] == "title"), "UNKNOWN"),
        "physical_state": next((q["state"] for q in qs if q["key"] == "inspection"), "UNKNOWN"),
        "sources": _registry(prop.get("county_fips") or ""),
        "events": events, "outreach": outreach,
        "counts": {"found": sum(1 for q in qs if q["state"] == "FOUND"), "not_found": sum(1 for q in qs if q["state"] == "NOT_FOUND"), "unknown": sum(1 for q in qs if q["state"] == "UNKNOWN")},
    }
    snap["hash"] = hashlib.sha256(json.dumps(snap, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return snap


def fingerprint(snap: dict) -> str:
    """The evidence state a person accepts a proposal against: question states and their references, the
    evidence references on file, and the tax state. Notes, Bee events and outreach do not change it."""
    core = {"q": [(q["key"], q["state"], q["checked_at"], q["evidence_refs"]) for q in snap["questions"]],
            "e": [e["ref"] for e in snap["evidence"]], "tax": snap["tax"]["state"], "conflicts": snap["conflicts"]}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _outreach_for(case_id):
    from . import outreach
    return outreach.for_case(case_id)


# ------------------------------------------------------------------ rules (DERIVED): known / unknown / conflicts / candidate checks

SOURCE_FOR = {   # question -> registry source keys that could answer it, in order
    "tax_state": ["countypay", "county_delinquent_list", "arkansastaxsearch"], "tax_delinquent": ["countypay", "county_delinquent_list", "arkansastaxsearch"],
    "collector_asof": ["countypay"], "state_inventory": ["state_lands"], "state_record": ["state_lands"], "state_history": ["state_lands"], "auction": ["state_lands"],
    "owner": ["assessor_actdatascout"], "deed": ["assessor_actdatascout"], "mailing_address": ["assessor_actdatascout"], "title": [], "lien_clerk": [],
}
TYPE_FOR = {"inspection": "MANUAL_VERIFICATION", "title": "DOCUMENT_REVIEW", "lien_clerk": "DOCUMENT_REVIEW", "deed": "DOCUMENT_REVIEW", "listing": "FOLLOW_UP_RESEARCH",
            "sale_state": "FOLLOW_UP_RESEARCH", "other_signals": "RECORD_COMPARISON", "identity": "RECORD_COMPARISON"}
PROVES = {
    "tax_state": ("A Collector bill page or the county's delinquent list naming this parcel with an amount and a date", "The State's inventory saying nothing, the roll, a note, or an assumption from a missing record"),
    "tax_delinquent": ("A Collector record or county list showing a delinquent amount for this parcel", "Absence from the State list; a current-year bill (that is a current bill, not a delinquency)"),
    "mailing_address": ("The Assessor's current mailing address for the owner of record, with its date", "The situs address, a neighbour, a search-engine result, or an old roll copy taken as current"),
    "owner": ("The current county roll or Assessor record naming the owner, with its date", "A mailing address, a similar name, or a deed guess"),
    "deed": ("A recorded instrument number or book/page from the Circuit Clerk or Assessor", "A tax-roll owner line; the roll is not a title search"),
    "title": ("A Circuit Clerk index search or a title company report naming liens, mortgages and judgments", "No City lien on the map; that layer is not the Clerk's index"),
    "lien_clerk": ("A Circuit Clerk index search for this parcel or owner", "The City lien layer, which covers cleanup liens only"),
    "inspection": ("A person standing in front of the property recording what they saw and when", "Aerials, the roll, the mailing address, or register absence"),
    "listing": ("A named listing source with a date", "A search that found nothing; that only says nothing was found by that search"),
    "sale_state": ("A listing source or the State's own inventory", "Silence"),
    "state_inventory": ("The State Lands inventory read on a given date", "The county Collector; the two sources do not speak for each other"),
    "state_record": ("The State's listing page for this parcel", "The roll"), "state_history": ("The State's monthly sale or redemption report", "Absence from the inventory alone"),
    "auction": ("The State's listing with bid and dates", "Any private guess"), "vacancy": ("The City's vacant-structure register record", "Aerials or a low improvement value"),
    "code": ("The City's code-case record", "A vacancy record"), "lien_city": ("The City's lien layer record with an amount", "A code case"),
    "access": ("A recorded easement or platted frontage, or the State's road centreline reading", "A mapped road alone is not legal access"),
    "flood": ("The FEMA layer reading for this parcel", "A neighbour's zone"), "values": ("The county roll", "A listing price"),
    "identity": ("The parcel number matched on the county roll and the City record", "An address match alone"),
    "other_signals": ("Register or State records on this parcel", "The signal score"), "collector_asof": ("A Collector answer with its date", "A guess"),
    "lien_detail": ("The City lien record's amount, date and reference", "A rounded figure"), "code_detail": ("The City case record", "A rumour"),
}


def rules(snap: dict) -> dict:
    """Deterministic understanding of the case. origin DERIVED. Never touches the database."""
    known, unknown, not_found, conflicts, candidates = [], [], [], [], []
    for q in snap["questions"]:
        line = {"question": q["key"], "wording": q["wording"], "state": q["state"], "text": q["answer"], "source": q["source"], "date": q["checked_at"], "evidence_refs": q["evidence_refs"],
                "provenance": "MANUAL_VERIFICATION" if q["checked_by"] == "manual" else "AUTOMATED_SOURCE" if q["checked_by"] == "source" else None}
        if q["state"] == "FOUND":
            known.append(line)
        elif q["state"] == "NOT_FOUND":
            not_found.append(line)
        else:
            unknown.append(line)
    for f in snap["conflicts"]:
        refs = [e["ref"] for e in snap["evidence"] if e["field"] == f]
        conflicts.append({"field": f, "text": f"Two sources disagree about {f.replace('_', ' ')}; the readings are kept side by side", "evidence_refs": refs})
    # dated-record caution, stated as an inference, never as a fact
    inferences = []
    for q in snap["questions"]:
        if q["state"] == "FOUND" and q["key"] in ("mailing_address", "owner") and q["checked_at"] and q["checked_at"] < "2020-01-01":
            inferences.append({"about": q["key"], "text": f"The only {q['key'].replace('_', ' ')} record is dated {q['checked_at']}; it may no longer be current. A newer Assessor reading would settle it.", "provenance": "DERIVED", "evidence_refs": q["evidence_refs"]})
    reg = snap["sources"]
    for q in snap["questions"]:
        if q["state"] != "UNKNOWN":
            continue
        keys = SOURCE_FOR.get(q["key"], [])
        src_key = next((k for k in keys if reg.get(k, {}).get("status") == "AVAILABLE"), None) or (keys[0] if keys else None)
        src = reg.get(src_key) if src_key else None
        status = (src or {}).get("status") or ("NOT_APPLICABLE" if not keys else "UNKNOWN")
        ptype = TYPE_FOR.get(q["key"], "SOURCE_CHECK")
        if status in ("MANUAL_ONLY", "BLOCKED", "NOT_FOUND", "TEMPORARILY_UNAVAILABLE", "NOT_APPLICABLE") and ptype == "SOURCE_CHECK":
            ptype = "MANUAL_VERIFICATION"
        proves, not_proves = PROVES.get(q["key"], ("A record from a named source with a date", "An assumption from a missing record"))
        where = q["next"]
        nxt = where["label"]
        # a source that is down still gets a proposal, with the honest status and a manual/alternate path
        alt = None
        if status in ("TEMPORARILY_UNAVAILABLE", "NOT_FOUND", "BLOCKED") and len(keys) > 1:
            alt_key = next((k for k in keys[1:] if reg.get(k)), None)
            alt = {"source": (reg.get(alt_key) or {}).get("name") or alt_key, "status": (reg.get(alt_key) or {}).get("status"), "url": (reg.get(alt_key) or {}).get("public_url")} if alt_key else None
        candidates.append({
            "proposal_id": f"{q['key']}:{src_key or 'manual'}", "question_id": q["key"], "type": ptype, "purpose": f"Answer: {q['wording']}",
            "source_candidate": (src or {}).get("name") or {"MANUAL_VERIFICATION": "a person, in the field or at the counter", "DOCUMENT_REVIEW": "a person at the Circuit Clerk / county records",
                                                             "FOLLOW_UP_RESEARCH": "a person, with a named public source", "RECORD_COMPARISON": "the records already on the property file"}.get(ptype, "the property file"),
            "source_status": status, "where": {"label": nxt, "href": where["href"], "manual": where["manual"]}, "alternate": alt,
            "expected_information": proves, "would_not_answer": not_proves,
            "risk": "none: reading a public record" if ptype in ("SOURCE_CHECK", "RECORD_COMPARISON") else "a person's time; nothing is sent or promised",
            "requires_human_action": ptype != "SOURCE_CHECK" or status != "AVAILABLE", "authorization_required": False,
            "changes_case_state": q["key"] in ("tax_state", "tax_delinquent", "mailing_address", "owner", "title", "lien_clerk", "inspection", "state_inventory"),
            "status": "PROPOSED",
        })
    order = ["tax_state", "mailing_address", "owner", "title", "lien_clerk", "inspection", "tax_delinquent", "state_inventory", "deed", "listing", "sale_state", "access", "flood", "collector_asof", "state_history", "auction", "other_signals", "identity"]
    candidates.sort(key=lambda p: (order.index(p["question_id"]) if p["question_id"] in order else 99, p["proposal_id"]))
    outreach_notes = []
    for o in snap["outreach"]:
        if o["gate_ready"] is False:
            outreach_notes.append(f"Outreach ({o['purpose']}) is blocked: {'; '.join(o['blocking'] or [])}. It stays blocked until the evidence changes; Bee does not prepare drafts.")
        else:
            outreach_notes.append(f"Outreach ({o['purpose']}) is {o['status']}, draft v{o['draft_version'] or '-'}; a person reviews and acts. Bee never edits a draft.")
    return {"origin": "DERIVED", "known": known, "not_found": not_found, "unknown": unknown, "conflicts": conflicts, "inferences": inferences,
            "candidates": candidates, "outreach_notes": outreach_notes,
            "summary": f"{len(known)} answered from evidence, {len(not_found)} checked with no result, {len(unknown)} still unknown, {len(conflicts)} conflict{'s' if len(conflicts) != 1 else ''}."}


# ------------------------------------------------------------------ the model (AI_OPINION), strictly validated

BEE_SYSTEM = """You are Bee, an investigator's assistant for ONE property investigation. You reduce uncertainty.
You only know what is in the CASE SNAPSHOT. You may propose, explain, prioritise and summarise. You may not decide, contact, send, buy, offer, or state anything as a fact that the snapshot does not show with a source.
Never say a property is vacant, distressed, for sale, not for sale, abandoned, or that the owner is motivated or willing to sell. Never say taxes are delinquent, paid or current unless the tax state in the snapshot says exactly that. Never say title is clear. Never treat a mailing address as current because it exists; say the record's date. Never treat a missing record as an answer. Never claim a check was done.
Plain everyday English, short sentences, no jargon."""

OUTPUT_SCHEMA = {
    "summary": "string, 1-3 sentences, plain words",
    "known": [{"question": "question key from the snapshot", "evidence_refs": ["evidence:N from the snapshot"], "text": "what the evidence shows, with the source and date"}],
    "unknown": [{"question": "question key in UNKNOWN state", "text": "why it is unknown, in plain words"}],
    "conflicts": [{"field": "a field listed under conflicts", "text": "what disagrees"}],
    "inferences": [{"text": "what Bee suspects, worded as a possibility, with the evidence it rests on", "evidence_refs": ["evidence:N"]}],
    "proposed_checks": [{"proposal_id": "one of the candidate proposal_ids", "priority": "1 = check first", "why": "why this check is next, in plain words"}],
    "warnings": ["anything a person should be careful about"],
}


def _prompt(snap: dict, r: dict) -> str:
    cands = [{"proposal_id": c["proposal_id"], "question": c["question_id"], "type": c["type"], "source": c["source_candidate"], "source_status": c["source_status"], "expected": c["expected_information"]} for c in r["candidates"]]
    return ("CASE SNAPSHOT (the only thing you know):\n" + json.dumps({k: snap[k] for k in ("property", "status", "why_case_exists", "questions", "evidence", "conflicts", "tax", "sale", "title_state", "physical_state", "sources", "outreach", "counts")}, indent=0, sort_keys=True)
            + "\n\nCANDIDATE CHECKS (choose and order from these only; you cannot add a check, a source or a URL):\n" + json.dumps(cands, indent=0)
            + "\n\nAnswer with ONE JSON object and nothing else, exactly this shape:\n" + json.dumps(OUTPUT_SCHEMA, indent=0)
            + "\nRules: 'known' entries must cite evidence_refs that exist in the snapshot and questions whose state is FOUND. 'unknown' entries must name questions whose state is UNKNOWN. Every proposed_checks.proposal_id must be a candidate id. Do not mark anything completed. Do not write a letter.")


def validate(obj, snap: dict, r: dict) -> tuple[dict | None, list[str]]:
    """Strict contract. Returns (clean, problems). Any structural problem rejects the whole output."""
    problems = []
    if not isinstance(obj, dict):
        return None, ["output is not a JSON object"]
    for k in ("summary", "known", "unknown", "conflicts", "proposed_checks"):
        if k not in obj:
            problems.append(f"missing {k}")
    if problems:
        return None, problems
    if not isinstance(obj["summary"], str) or not obj["summary"].strip():
        problems.append("summary must be text")
    refs = {e["ref"] for e in snap["evidence"]}
    qstate = {q["key"]: q["state"] for q in snap["questions"]}
    cand = {c["proposal_id"]: c for c in r["candidates"]}
    known, unknown, conflicts, inferences, checks, warnings = [], [], [], [], [], []
    for x in obj.get("known") or []:
        if not isinstance(x, dict) or qstate.get(x.get("question")) != "FOUND":
            problems.append(f"known item names a question that is not FOUND: {x.get('question') if isinstance(x, dict) else x}")
            continue
        xr = [t for t in (x.get("evidence_refs") or []) if t in refs]
        if not xr:
            problems.append(f"known item without a real evidence reference: {x.get('question')}")
            continue
        known.append({"question": x["question"], "evidence_refs": sorted(xr), "text": str(x.get("text") or "")[:300]})
    for x in obj.get("unknown") or []:
        if not isinstance(x, dict) or qstate.get(x.get("question")) != "UNKNOWN":
            problems.append(f"unknown item names a question that is not UNKNOWN: {x.get('question') if isinstance(x, dict) else x}")
            continue
        unknown.append({"question": x["question"], "text": str(x.get("text") or "")[:300]})
    for x in obj.get("conflicts") or []:
        if isinstance(x, dict) and x.get("field") in snap["conflicts"]:
            conflicts.append({"field": x["field"], "text": str(x.get("text") or "")[:300]})
        else:
            problems.append("conflict item names a field that is not in conflict")
    dropped = []
    for x in obj.get("inferences") or []:
        if isinstance(x, dict) and str(x.get("text") or "").strip():
            low = str(x["text"]).lower()
            hit = next((w for w in FORBIDDEN_INFERENCE_TOPICS if w in low), None)
            if hit:
                dropped.append(f"Bee's remark about '{hit}' was removed: Bee may not infer vacancy, distress, motivation, sale status or title from records that do not say so.")
                continue
            inferences.append({"text": str(x["text"])[:300], "evidence_refs": sorted(t for t in (x.get("evidence_refs") or []) if t in refs), "provenance": "AI_OPINION"})
    seen = set()
    for x in obj.get("proposed_checks") or []:
        if not isinstance(x, dict) or x.get("proposal_id") not in cand or x["proposal_id"] in seen:
            problems.append(f"proposed check is not a candidate: {x.get('proposal_id') if isinstance(x, dict) else x}")
            continue
        seen.add(x["proposal_id"])
        try:
            pr = int(x.get("priority") or len(checks) + 1)
        except (TypeError, ValueError):
            pr = len(checks) + 1
        if str(x.get("status") or "PROPOSED").upper() != "PROPOSED":
            problems.append("a proposed check claimed a status other than PROPOSED")
            continue
        checks.append({"proposal_id": x["proposal_id"], "priority": pr, "why": str(x.get("why") or "")[:400]})
    for w in obj.get("warnings") or []:
        if isinstance(w, str) and w.strip():
            warnings.append(w[:300])
    warnings.extend(dropped)
    # inference words may never appear in KNOWN text or the summary as facts
    tax_state = (snap.get("tax") or {}).get("state")
    for k in known + [{"text": obj["summary"]}]:
        low = k["text"].lower()
        for w in INFERENCE_WORDS:
            if w in low and not (w in ("delinquent", "owes") and tax_state in ("DELINQUENT_VERIFIED", "TAX_SALE_VERIFIED")):
                problems.append(f"'{w}' stated as a fact in known/summary")
    if problems:
        return None, problems
    checks.sort(key=lambda x: (x["priority"], x["proposal_id"]))
    return {"summary": obj["summary"].strip()[:600], "known": known, "unknown": unknown, "conflicts": conflicts, "inferences": inferences, "proposed_checks": checks, "warnings": warnings}, []


def _model_meta(model: str) -> dict:
    """Digest / size from the local registry when reachable; never a secret."""
    try:
        import httpx
        from .config import OLLAMA_URL
        for m in httpx.get(f"{OLLAMA_URL}/api/tags", timeout=6).json().get("models", []):
            if m.get("name") == model:
                return {"digest": (m.get("digest") or "")[:12], "parameter_size": (m.get("details") or {}).get("parameter_size")}
    except Exception:
        pass
    return {}


def run(case_id: int, *, model: str | None = None, actor: str = "user", asker=None) -> dict:
    """One Bee analysis. Persists an AI_OPINION record (OK or FAILED) and (re)proposes candidate checks.
    Never writes evidence, never changes a question, never touches outreach."""
    if not cases.case_for_property_id(case_id) if hasattr(cases, "case_for_property_id") else not db.q1("SELECT 1 FROM investigation_cases WHERE id=?", (case_id,)):
        raise KeyError("no such investigation")
    cases.refresh(case_id)              # question states come from recorded evidence through the canonical path, never from Bee
    snap = snapshot(case_id)
    r = rules(snap)
    prompt = _prompt(snap, r)
    t0 = time.time()
    asker = asker or ai.ask_json
    obj, meta = asker(prompt, system=BEE_SYSTEM, model=model)
    clean, problems = (validate(obj, snap, r) if obj is not None else (None, [meta.get("error") or "no response"]))
    status = "OK" if clean else "FAILED"
    used_refs = sorted({t for k in (clean or {}).get("known", []) for t in k["evidence_refs"]} | {t for k in (clean or {}).get("inferences", []) for t in k["evidence_refs"]})
    mm = _model_meta(meta.get("model") or "") if meta.get("model") else {}
    cur = db.ex("""INSERT INTO bee_analyses(case_id, snapshot_hash, snapshot_json, provider, model, model_version, prompt_version, schema_version, status, error, output_json, raw_excerpt, origin, evidence_refs_json, duration_ms, actor, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (case_id, snap["hash"], jdump(snap), PROVIDER, meta.get("model") or (model or ""), jdump(mm) if mm else None, PROMPT_VERSION, SCHEMA_VERSION, status,
                 "; ".join(problems)[:1000] if problems else None, jdump(clean) if clean else None, (meta.get("raw") or "")[:2000], "AI_OPINION", jdump(used_refs), int((time.time() - t0) * 1000), actor, utcnow()))
    aid = cur.lastrowid
    # proposals: candidates come from the rules; the model only chose, ordered and explained. A candidate the model skipped
    # is still stored (lowest priority) so a person sees every legitimate next check.
    chosen = {c["proposal_id"]: c for c in (clean or {}).get("proposed_checks", [])}
    n_new = 0
    for i, cand in enumerate(r["candidates"]):
        pick = chosen.get(cand["proposal_id"])
        priority = pick["priority"] if pick else 100 + i
        why = pick["why"] if pick else "Listed by the evidence rules: the question is still UNKNOWN. Bee did not comment on it."
        existing = db.q1("SELECT id, status FROM bee_proposals WHERE case_id=? AND proposal_id=? AND active=1", (case_id, cand["proposal_id"]))
        if existing and existing["status"] != "PROPOSED":
            db.ex("UPDATE bee_proposals SET analysis_id=?, updated_at=? WHERE id=?", (aid, utcnow(), existing["id"]))     # a decided proposal keeps its decision
            continue
        if existing:
            db.ex("""UPDATE bee_proposals SET analysis_id=?, priority=?, why=?, source_status=?, where_json=?, alternate_json=?, origin=?, updated_at=? WHERE id=?""",
                  (aid, priority, why, cand["source_status"], jdump(cand["where"]), jdump(cand["alternate"]), "AI_OPINION" if pick else "DERIVED", utcnow(), existing["id"]))
            continue
        db.ex("""INSERT INTO bee_proposals(analysis_id, case_id, proposal_id, question_key, type, purpose, why, expected_information, would_not_answer, source_candidate, source_status, where_json, alternate_json,
                 risk, requires_human_action, authorization_required, changes_case_state, priority, status, origin, active, created_at, updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
              (aid, case_id, cand["proposal_id"], cand["question_id"], cand["type"], cand["purpose"], why, cand["expected_information"], cand["would_not_answer"], cand["source_candidate"], cand["source_status"],
               jdump(cand["where"]), jdump(cand["alternate"]), cand["risk"], int(cand["requires_human_action"]), int(cand["authorization_required"]), int(cand["changes_case_state"]), priority, "PROPOSED",
               "AI_OPINION" if pick else "DERIVED", utcnow(), utcnow()))
        n_new += 1
    # a candidate that no longer exists (its question got answered) is closed as COMPLETED-by-evidence, never by Bee
    live = {c["proposal_id"] for c in r["candidates"]}
    for row in db.q("SELECT id, proposal_id, question_key FROM bee_proposals WHERE case_id=? AND active=1 AND status IN ('PROPOSED','ACCEPTED')", (case_id,)):
        if row["proposal_id"] not in live:
            st = next((q["state"] for q in snap["questions"] if q["key"] == row["question_key"]), None)
            if st in ("FOUND", "NOT_FOUND"):
                db.ex("UPDATE bee_proposals SET status='COMPLETED', decided_by='evidence', decided_at=?, updated_at=?, human_note=IFNULL(human_note, 'question answered by recorded evidence') WHERE id=?", (utcnow(), utcnow(), row["id"]))
    cases._event(case_id, "BEE ANALYSIS", f"Bee analysis #{aid} {status} ({meta.get('model') or 'no model'})", (clean or {}).get("summary") or ("; ".join(problems))[:300], f"bee:{aid}", actor)
    cases._touch(case_id)
    return analysis(aid)


# ------------------------------------------------------------------ read model

def analysis(aid: int) -> dict | None:
    r = db.q1("SELECT * FROM bee_analyses WHERE id=?", (aid,))
    if not r:
        return None
    d = dict(r)
    d["output"] = jload(d.pop("output_json"), None)
    d["model_version"] = jload(d["model_version"], None) if d.get("model_version") else None
    d["evidence_refs"] = jload(d.pop("evidence_refs_json"), [])
    d.pop("snapshot_json", None)
    d["raw_excerpt"] = None if d["status"] == "OK" else (d.get("raw_excerpt") or "")[:400]
    d["label"] = "AI OPINION — Bee's analysis; not evidence, not a decision"
    return d


def proposals(case_id: int) -> list[dict]:
    out = []
    from . import execution as _exec
    for r in db.q("SELECT * FROM bee_proposals WHERE case_id=? AND active=1 ORDER BY CASE status WHEN 'ACCEPTED' THEN 0 WHEN 'PROPOSED' THEN 1 WHEN 'EXECUTED_NO_ANSWER' THEN 2 WHEN 'BLOCKED' THEN 3 WHEN 'COMPLETED' THEN 4 ELSE 5 END, priority, id", (case_id,)):
        d = dict(r)
        d["where"] = jload(d.pop("where_json"), {}); d["alternate"] = jload(d.pop("alternate_json"), None); d["edited"] = jload(d.pop("edited_json"), None)
        d["requires_human_action"] = bool(d["requires_human_action"]); d["authorization_required"] = bool(d["authorization_required"]); d["changes_case_state"] = bool(d["changes_case_state"])
        d["meaning"] = {"PROPOSED": "Bee proposes this; nothing has been checked.", "ACCEPTED": "A person accepted this proposal for execution or review. Accepting does not make any statement true and records no evidence.",
                        "REJECTED": "A person declined this proposal.", "COMPLETED": "The question was answered by recorded evidence; Bee did not complete anything.", "BLOCKED": "The source is not usable right now.",
                        "EXECUTED_NO_ANSWER": "The authorized check ran and the source answered, but the recorded evidence did not settle the question; it stays UNKNOWN."}.get(d["status"], "")
        d["execution"] = _exec.plan(d["id"]) if d["status"] in ("ACCEPTED", "PROPOSED", "EXECUTED_NO_ANSWER") else {"state": "REJECTED" if d["status"] == "REJECTED" else "NOT_EXECUTABLE", "ready": False, "reasons": []}
        d["executions"] = [x for x in _exec.executions_for_case(case_id) if x["proposal_id"] == d["id"]]
        out.append(d)
    return out


def view(case_id: int) -> dict:
    if not db.q1("SELECT 1 FROM investigation_cases WHERE id=?", (case_id,)):
        raise KeyError("no such investigation")
    cases.refresh(case_id)
    snap = snapshot(case_id)
    latest = db.q1("SELECT id FROM bee_analyses WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,))
    latest_ok = db.q1("SELECT id FROM bee_analyses WHERE case_id=? AND status='OK' ORDER BY id DESC LIMIT 1", (case_id,))
    history = [dict(r) for r in db.q("SELECT id, status, model, prompt_version, schema_version, created_at, error, actor FROM bee_analyses WHERE case_id=? ORDER BY id DESC LIMIT 20", (case_id,))]
    return {"investigation_id": case_id, "snapshot_hash": snap["hash"], "understanding": rules(snap), "latest": analysis(latest["id"]) if latest else None,
            "latest_ok": analysis(latest_ok["id"]) if latest_ok and (not latest or latest_ok["id"] != latest["id"]) else None,
            "proposals": proposals(case_id), "history": history, "model": ai.status(),
            "boundary": "Bee proposes, explains and prioritises. It never creates evidence, never answers a question, never contacts anyone, never drafts outreach, and never decides."}


# ------------------------------------------------------------------ human review boundary

def decide(proposal_id: int, decision: str, actor="user", note: str = "", edits: dict | None = None) -> dict:
    r = db.q1("SELECT * FROM bee_proposals WHERE id=?", (proposal_id,))
    if not r:
        raise KeyError("no such proposal")
    decision = (decision or "").upper()
    if decision not in ("ACCEPT", "REJECT", "REVIEWED", "EDIT", "REOPEN"):
        raise ValueError("decision must be ACCEPT, REJECT, REVIEWED, EDIT or REOPEN")
    now = utcnow()
    if decision == "EDIT":
        allowed = {k: str(v)[:400] for k, v in (edits or {}).items() if k in ("why", "expected_information", "would_not_answer", "purpose") and str(v).strip()}
        if not allowed:
            raise ValueError("nothing to edit")
        prev = jload(r["edited_json"], {}) or {}
        prev.update(allowed)
        db.ex("UPDATE bee_proposals SET edited_json=?, human_note=?, updated_at=? WHERE id=?", (jdump(prev), note or r["human_note"], now, proposal_id))
        cases._event(r["case_id"], "BEE PROPOSAL DECISION", f"Proposal {r['proposal_id']} edited by a person", ", ".join(allowed), f"bee_proposal:{proposal_id}", actor)
    elif decision == "REVIEWED":
        db.ex("UPDATE bee_proposals SET reviewed_at=?, human_note=?, updated_at=? WHERE id=?", (now, note or r["human_note"], now, proposal_id))
        cases._event(r["case_id"], "BEE PROPOSAL DECISION", f"Proposal {r['proposal_id']} marked reviewed", note, f"bee_proposal:{proposal_id}", actor)
    else:
        status = {"ACCEPT": "ACCEPTED", "REJECT": "REJECTED", "REOPEN": "PROPOSED"}[decision]
        fp = fingerprint(snapshot(r["case_id"])) if status == "ACCEPTED" else None
        db.ex("UPDATE bee_proposals SET status=?, decided_by=?, decided_at=?, human_note=?, accepted_fingerprint=?, updated_at=? WHERE id=?", (status, actor, now, note or r["human_note"], fp, now, proposal_id))
        cases._event(r["case_id"], "BEE PROPOSAL DECISION", f"Proposal {r['proposal_id']} {status} by a person",
                     (note or "") + (" — acceptance means a person will run or review this check; it makes no statement true and records no evidence" if status == "ACCEPTED" else ""), f"bee_proposal:{proposal_id}", actor)
    cases._touch(r["case_id"])
    d = dict(db.q1("SELECT * FROM bee_proposals WHERE id=?", (proposal_id,)))
    d["where"] = jload(d.pop("where_json"), {}); d["alternate"] = jload(d.pop("alternate_json"), None); d["edited"] = jload(d.pop("edited_json"), None)
    return d


def public_summary(case_id: int) -> dict:
    """Safe metadata only: no text, no refs to private values, no prompts."""
    latest = db.q1("SELECT status, model, prompt_version, created_at FROM bee_analyses WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,))
    by = {}
    for r in db.q("SELECT status, COUNT(*) n FROM bee_proposals WHERE case_id=? AND active=1 GROUP BY status", (case_id,)):
        by[r["status"]] = r["n"]
    from . import execution as _exec
    return {"last_analysis": ({"status": latest["status"], "model": latest["model"], "prompt_version": latest["prompt_version"], "at": latest["created_at"]} if latest else None),
            "proposals_by_status": by, "executions": _exec.public_summary(case_id), "origin": "AI_OPINION", "note": "Bee's text, reasons and proposals are held in the local app; nothing here is evidence"}
