"""P3B EVIDENCE-GATED OWNER OUTREACH PREPARATION.  DRAFT ONLY.

An outreach preparation belongs to one investigation case and one neutral purpose. Before a draft can
exist, an evidence GATE is evaluated from the case's three-state questions and the P3A provenance model:
every requirement is FOUND / NOT_FOUND / UNKNOWN with source, origin, date and evidence reference.
Required requirements must be FOUND; recommended ones are shown but never block.

The draft contains only supported statements. Human-entered text is kept apart and labelled. A person's
edit becomes a new HUMAN_EDITED version; regeneration never overwrites it. Every step is an action on
the case timeline. There is no send path in this module, and no other module sends on its behalf:
nothing here emails, texts, posts, submits or contacts anyone.
"""
from __future__ import annotations

import re

from . import cases, db, store
from .db import jdump, jload, utcnow

PURPOSES = {
    "OFF_MARKET_INQUIRY":      "Asking the owner of record whether they would consider a private sale",
    "PROPERTY_STATUS_INQUIRY": "Asking the owner of record about the property's current status",
    "OWNER_CONTACT_REQUEST":   "Asking the owner of record for a way to be in contact",
    "RECORD_FOLLOWUP":         "Following up on a specific public record with the owner of record",
    "OTHER":                   "A purpose the person states in their own words",
}
STATUSES = ("PREPARING", "READY", "DRAFTED", "EDITED", "REVIEWED", "DISCARDED")
REQ = {
    "property_identity":   "PROPERTY IDENTITY",
    "owner_identity":      "OWNER IDENTITY",
    "mailing_address":     "MAILING ADDRESS",
    "reason_for_contact":  "REASON FOR CONTACT",
    "tax_information":     "TAX INFORMATION",
    "title_record":        "TITLE / OWNERSHIP RECORD",
    "physical_observation": "PHYSICAL / PROPERTY OBSERVATION",
}
# purpose -> (required to draft, recommended before draft)
REQUIREMENTS = {
    "OFF_MARKET_INQUIRY":      (["property_identity", "owner_identity", "mailing_address", "reason_for_contact"], ["tax_information", "title_record", "physical_observation"]),
    "PROPERTY_STATUS_INQUIRY": (["property_identity", "owner_identity", "mailing_address", "reason_for_contact"], ["tax_information", "physical_observation"]),
    "OWNER_CONTACT_REQUEST":   (["property_identity", "owner_identity", "mailing_address", "reason_for_contact"], ["title_record"]),
    "RECORD_FOLLOWUP":         (["property_identity", "owner_identity", "mailing_address", "reason_for_contact"], ["tax_information", "title_record"]),
    "OTHER":                   (["property_identity", "owner_identity", "mailing_address", "reason_for_contact"], ["tax_information", "title_record", "physical_observation"]),
}
# words the system never writes on its own; a person may type them and they are labelled human-authored
FORBIDDEN_SYSTEM_WORDS = ("motivated", "great deal", "good investment", "distressed", "buy this property", "clear title", "for sale", "not for sale", "vacant", "owes")


# ------------------------------------------------------------------ gate

def _from_question(q, *, allow_manual_not_found=False):
    """Map a case question to a gate requirement entry with provenance."""
    if not q:
        return {"state": "UNKNOWN", "text": "no such question on the case", "source": None, "origin": None, "date": None, "refs": []}
    origin = "MANUAL_VERIFICATION" if q.get("checked_by") == "manual" else "AUTOMATED_SOURCE" if q.get("checked_by") == "source" else None
    state = q["state"]
    if state == "NOT_FOUND" and q.get("checked_by") == "manual" and not allow_manual_not_found:
        state = "NOT_FOUND"
    return {"state": state, "text": q.get("answer") or "", "source": q.get("source"), "origin": origin, "origin_label": store.ORIGIN_LABEL.get(origin, "ORIGIN NOT RECORDED") if origin else None,
            "date": q.get("checked_at"), "refs": q.get("evidence_refs") or [], "source_url": q.get("source_url"), "question": q["key"]}


def evaluate_gate(case: dict, purpose: str, reason: str | None) -> dict:
    """The outreach-readiness assessment. Evidence only; never the signal score."""
    required, recommended = REQUIREMENTS.get(purpose, REQUIREMENTS["OTHER"])
    qs = {q["key"]: q for q in case["questions"]}
    pid = case["property_id"]
    out = {}
    out["property_identity"] = _from_question(qs.get("identity"))
    out["owner_identity"] = _from_question(qs.get("owner"))
    if out["owner_identity"]["state"] == "FOUND":
        e = store.latest_answer(pid, "owner_name") or store.latest_answer(pid, "manual:owner")
        if e:
            out["owner_identity"].update(origin=e["origin"], origin_label=e["origin_label"], refs=[e["ref"]], source=e.get("source_name") or e["source"], date=e["date"], value=e["value"])
    out["mailing_address"] = _from_question(qs.get("mailing_address"))
    e = store.latest_answer(pid, "owner_mailing_address") or store.latest_answer(pid, "manual:mailing_address")
    if e:
        out["mailing_address"].update(state="FOUND", origin=e["origin"], origin_label=e["origin_label"], refs=[e["ref"]], source=e.get("source_name") or e["source"], date=e["date"], value=e["value"],
                                      text=f"{e['value']} ({e['origin_label'].lower()})")
    else:
        out["mailing_address"].update(state="UNKNOWN", text="MAILING ADDRESS: UNKNOWN — no mailing address of record; the situs address is never substituted", value=None)
    r = (reason or "").strip()
    sig = [f"{s['label']} ({s['cls']}, {s['event_date'] or 'undated'}, {s['src'] or 'source not named'})" for s in case.get("signals", []) if s["event"] != "MANUAL"]
    out["reason_for_contact"] = {"state": "FOUND" if r else "UNKNOWN", "text": r or "state, in your own words, why you are writing", "source": "human-entered" if r else None,
                                 "origin": "NOTE" if r else None, "origin_label": "HUMAN-ENTERED" if r else None, "date": utcnow()[:10] if r else None, "refs": [], "signals": sig}
    # tax: the shared model as the case reports it
    tx = case.get("tax") or {}
    tq = qs.get("tax_state")
    out["tax_information"] = _from_question(tq)
    out["tax_information"].update(tax_state=("SOURCE_UNAVAILABLE" if tx.get("source_unavailable") else tx.get("st")), amount=tx.get("amt"), as_of=tx.get("as_of"), tax_source=tx.get("src"), refs=tx.get("evidence") or out["tax_information"]["refs"])
    if out["tax_information"]["state"] == "FOUND" and not out["tax_information"].get("origin"):
        out["tax_information"]["origin"], out["tax_information"]["origin_label"] = "AUTOMATED_SOURCE", "AUTOMATED SOURCE"
    # title: deed OR title OR clerk liens; never the tax roll
    t = [qs.get(k) for k in ("title", "deed", "lien_clerk") if qs.get(k)]
    found = [q for q in t if q["state"] == "FOUND"]
    nf = [q for q in t if q["state"] == "NOT_FOUND"]
    if found:
        out["title_record"] = _from_question(found[0])
    elif nf:
        out["title_record"] = _from_question(nf[0])
    else:
        out["title_record"] = {"state": "UNKNOWN", "text": "TITLE/DEED: UNKNOWN — the tax roll is not a title search; nothing has been read at the Circuit Clerk", "source": None, "origin": None, "date": None, "refs": [],
                               "next": {"label": "OPEN COUNTY SOURCE", "note": "Circuit Clerk / county records / manual research"}}
    # physical: only a person's own inspection
    iq = qs.get("inspection")
    if iq and iq.get("checked_by") == "manual" and iq["state"] in ("FOUND", "NOT_FOUND"):
        out["physical_observation"] = _from_question(iq)
    else:
        out["physical_observation"] = {"state": "UNKNOWN", "text": "PHYSICAL OBSERVATION: UNKNOWN — nobody has recorded standing in front of it; occupancy and condition are never inferred from the roll, the mailing address, register absence or aerials alone",
                                       "source": None, "origin": None, "date": None, "refs": []}
    rows = []
    for k in required + recommended:
        d = dict(out[k], key=k, label=REQ[k], level="REQUIRED TO DRAFT" if k in required else "RECOMMENDED BEFORE DRAFT")
        if d["state"] == "UNKNOWN":
            d["level_note"] = "UNKNOWN / NOT AVAILABLE"
        rows.append(d)
    blocking = [r["label"] for r in rows if r["level"] == "REQUIRED TO DRAFT" and r["state"] != "FOUND"]
    missing_rec = [r["label"] for r in rows if r["level"] == "RECOMMENDED BEFORE DRAFT" and r["state"] != "FOUND"]
    return {"purpose": purpose, "purpose_text": PURPOSES.get(purpose, PURPOSES["OTHER"]), "evaluated_at": utcnow(), "ready": not blocking,
            "blocking": blocking, "recommended_missing": missing_rec, "requirements": rows,
            "explanation": ("Draft may be prepared: every required item is FOUND." if not blocking else
                            "Draft cannot be prepared until these REQUIRED items are FOUND: " + "; ".join(blocking) + ".") +
                           (f" Recommended before sending anything, still not FOUND: {'; '.join(missing_rec)}." if missing_rec else "")}


# ------------------------------------------------------------------ preparations

def _event(case_id, cls, title, detail="", ref=None, actor="user"):
    cases._event(case_id, cls, title, detail, ref, actor)
    cases._touch(case_id)


def get_prep(prep_id: int) -> dict | None:
    r = db.q1("SELECT * FROM outreach_preps WHERE id=?", (prep_id,))
    if not r:
        return None
    d = dict(r)
    d["gate"] = jload(d.pop("gate_json"), None)
    d["drafts"] = []
    for x in db.q("SELECT * FROM outreach_drafts WHERE prep_id=? ORDER BY version", (prep_id,)):
        x = dict(x)
        x["segments"] = jload(x.pop("segments_json"), [])
        x["human"] = jload(x.pop("human_json"), {})
        x["evidence_basis"] = jload(x.pop("evidence_basis_json"), [])
        d["drafts"].append(x)
    d["current"] = next((x for x in d["drafts"] if x["version"] == d["current_version"]), None)
    d["send_path"] = None          # by construction: nothing in this system can send this draft
    return d


def open_or_get(case_id: int, purpose: str, reason: str | None = None, actor="user") -> dict:
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {list(PURPOSES)}")
    c = cases.get_case(case_id)
    if not c:
        raise KeyError("no such investigation")
    r = db.q1("SELECT id FROM outreach_preps WHERE case_id=? AND purpose=? AND active=1", (case_id, purpose))
    if r:
        if reason is not None and reason.strip():
            db.ex("UPDATE outreach_preps SET reason=?, updated_at=? WHERE id=?", (reason.strip(), utcnow(), r["id"]))
        return reevaluate(r["id"], actor)
    now = utcnow()
    cur = db.ex("INSERT INTO outreach_preps(case_id, property_id, purpose, status, reason, active, created_at, updated_at) VALUES(?,?,?,?,?,1,?,?)",
                (case_id, c["property_id"], purpose, "PREPARING", (reason or "").strip() or None, now, now))
    _event(case_id, "OUTREACH PREPARATION STARTED", f"{purpose}: {PURPOSES[purpose]}", (reason or "").strip()[:300], f"outreach:{cur.lastrowid}", actor)
    return reevaluate(cur.lastrowid, actor)


def reevaluate(prep_id: int, actor="user") -> dict:
    p = db.q1("SELECT * FROM outreach_preps WHERE id=?", (prep_id,))
    cases.refresh(p["case_id"])
    c = cases.get_case(p["case_id"])
    gate = evaluate_gate(c, p["purpose"], p["reason"])
    status = p["status"]
    if status in ("PREPARING", "READY"):
        status = "READY" if gate["ready"] else "PREPARING"
    db.ex("UPDATE outreach_preps SET gate_json=?, status=?, updated_at=? WHERE id=?", (jdump(gate), status, utcnow(), prep_id))
    _event(p["case_id"], "OUTREACH GATE EVALUATED", ("READY: every required item FOUND" if gate["ready"] else "BLOCKED: " + "; ".join(gate["blocking"])),
           f"recommended still missing: {'; '.join(gate['recommended_missing']) or 'none'}", f"outreach:{prep_id}", actor)
    return get_prep(prep_id)


# ------------------------------------------------------------------ draft

def _person_name(owner: str) -> str:
    raw = (owner or "").strip()
    if "," in raw:
        last, first = raw.split(",", 1)
        raw = f"{first.strip()} {last.strip()}"
    return " ".join(w.capitalize() if w.isupper() else w for w in raw.split())


def _tax_sentence(req: dict) -> str | None:
    st = req.get("tax_state")
    amt = req.get("amount")
    money = f"${amt:,.2f}" if isinstance(amt, (int, float)) else None
    src = req.get("tax_source") or "the county"
    asof = req.get("as_of") or "the date read"
    if st == "TAX_SALE_VERIFIED":
        return f"The Commissioner of State Lands lists this parcel as certified for unpaid taxes{' with ' + money + ' shown as owed' if money else ''}, as of {asof}."
    if st == "DELINQUENT_VERIFIED":
        return f"{src} shows the property taxes as delinquent{' (' + money + ')' if money else ''}, as of {asof}."
    if st == "CURRENT_BILL_OPEN":
        return f"{src} shows a current-year tax bill open{' (' + money + ')' if money else ''}, as of {asof}; that is a current bill, not a delinquency."
    if st == "CURRENT_VERIFIED":
        return f"{src} showed no open real-estate tax bill as of {asof}."
    return None


def generate_draft(prep_id: int, human: dict, actor="user") -> dict:
    """SYSTEM text from supported evidence only + HUMAN_ADDED text kept apart. Refuses while the gate blocks."""
    p = get_prep(prep_id)
    if not p:
        raise KeyError("no such preparation")
    if p["status"] == "DISCARDED":
        raise ValueError("this preparation was discarded; start a new one")
    p = reevaluate(prep_id, actor)
    gate = p["gate"]
    if not gate["ready"]:
        raise PermissionError("gate blocked: " + "; ".join(gate["blocking"]))
    c = cases.get_case(p["case_id"])
    req = {r["key"]: r for r in gate["requirements"]}
    prop = c["property"]
    sender = (human.get("sender_name") or "").strip() or "[your name]"
    contact = (human.get("contact") or "").strip() or "[how to reach you]"
    message = (human.get("message") or "").strip()
    owner = _person_name(req["owner_identity"].get("value") or prop.get("owner") or "")
    mail = req["mailing_address"].get("value") or ""
    situs = prop.get("address") or f"the parcel described as {prop.get('parcel_id')}"
    county = prop.get("county") or prop.get("county_fips")
    date = utcnow()[:10]
    basis = [{"requirement": r["label"], "state": r["state"], "refs": r.get("refs") or [], "origin": r.get("origin"), "source": r.get("source"), "date": r.get("date")} for r in gate["requirements"]]
    seg = []
    def sysseg(text, keys):
        seg.append({"kind": "SYSTEM", "text": text, "basis": [x for k in keys for x in (req[k].get("refs") or [])]})
    seg.append({"kind": "HUMAN_ADDED", "text": f"{sender}\n{contact}\n{date}", "basis": []})
    sysseg(f"{owner}\n{mail}", ["owner_identity", "mailing_address"])
    sysseg(f"Re: {situs}, {county} County, parcel {prop.get('parcel_id')}", ["property_identity"])
    sysseg(f"Dear {owner},", ["owner_identity"])
    purpose_line = {
        "OFF_MARKET_INQUIRY": f"I am writing to ask whether you would consider a private sale of the property at {situs}. I am not an agent and I am not acting for anyone else.",
        "PROPERTY_STATUS_INQUIRY": f"I am writing to ask about the current status of the property at {situs}. I am not an agent and I am not acting for anyone else.",
        "OWNER_CONTACT_REQUEST": f"I am writing to ask for a way to be in contact with you about the property at {situs}.",
        "RECORD_FOLLOWUP": f"I am writing about a public record concerning the property at {situs}.",
        "OTHER": f"I am writing about the property at {situs}.",
    }[p["purpose"]]
    sysseg(purpose_line, ["property_identity"])
    seg.append({"kind": "HUMAN_ADDED", "text": f"Why I am writing (in my words): {p['reason']}", "basis": []})
    # only the requirements this purpose lists are spoken about; a FOUND one is stated exactly, an unmet one is disclaimed
    tax = req.get("tax_information")
    ts = _tax_sentence(tax) if tax and tax["state"] == "FOUND" else None
    if ts:
        sysseg(ts, ["tax_information"])
    unknown_lines = []
    if tax and tax["state"] != "FOUND":
        unknown_lines.append("I have not checked the property taxes.")
    if req.get("title_record") and req["title_record"]["state"] != "FOUND":
        unknown_lines.append("I have not read the deed or title records.")
    if req.get("physical_observation") and req["physical_observation"]["state"] != "FOUND":
        unknown_lines.append("I have not visited the property.")
    if unknown_lines:
        sysseg(" ".join(unknown_lines) + " I am not making any claim about its condition, occupancy, value or title.", [])
    if message:
        seg.append({"kind": "HUMAN_ADDED", "text": message, "basis": []})
    sysseg(f"If you would rather not be contacted, a note to {contact} is enough and I will not write again.", [])
    seg.append({"kind": "HUMAN_ADDED", "text": f"Sincerely,\n{sender}", "basis": []})
    sysseg(f"Prepared from public records on {date} with Property Hunter. This is a draft for the writer's review; it is not a contract and nothing has been sent.", [])
    text = "\n\n".join(x["text"] for x in seg)
    for w in FORBIDDEN_SYSTEM_WORDS:
        for x in seg:
            if x["kind"] == "SYSTEM" and re.search(r"\b" + re.escape(w) + r"\b", x["text"], re.I) and not (w == "for sale" and False):
                raise AssertionError(f"system text may not say '{w}'")
    version = (db.q1("SELECT MAX(version) v FROM outreach_drafts WHERE prep_id=?", (prep_id,))["v"] or 0) + 1
    db.ex("INSERT INTO outreach_drafts(prep_id, version, kind, text, segments_json, human_json, evidence_basis_json, actor, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
          (prep_id, version, "SYSTEM", text, jdump(seg), jdump({"sender_name": sender, "contact": contact, "message": message}), jdump(basis), actor, utcnow()))
    has_human = db.q1("SELECT 1 FROM outreach_drafts WHERE prep_id=? AND kind='HUMAN_EDITED'", (prep_id,))
    if has_human:
        # regeneration never replaces what a person edited: the edited version stays current
        db.ex("UPDATE outreach_preps SET updated_at=? WHERE id=?", (utcnow(), prep_id))
        _event(p["case_id"], "DRAFT GENERATED", f"version {version} regenerated; the human-edited version stays current", "", f"outreach:{prep_id}:v{version}", actor)
    else:
        db.ex("UPDATE outreach_preps SET current_version=?, status='DRAFTED', updated_at=? WHERE id=?", (version, utcnow(), prep_id))
        _event(p["case_id"], "DRAFT GENERATED", f"version {version} from {len([b for b in basis if b['state']=='FOUND'])} FOUND requirements", "system text + human-added text, labelled", f"outreach:{prep_id}:v{version}", actor)
    return get_prep(prep_id)


def edit_draft(prep_id: int, text: str, actor="user") -> dict:
    p = get_prep(prep_id)
    if not p or not p["drafts"]:
        raise ValueError("nothing to edit yet: prepare a draft first")
    if p["status"] == "DISCARDED":
        raise ValueError("this preparation was discarded")
    text = (text or "").rstrip()
    if not text.strip():
        raise ValueError("empty draft")
    base = p["current"] or p["drafts"][-1]
    version = p["drafts"][-1]["version"] + 1
    seg = [{"kind": "HUMAN_EDITED", "text": text, "basis": [], "edited_from": base["version"]}]
    db.ex("INSERT INTO outreach_drafts(prep_id, version, kind, text, segments_json, human_json, evidence_basis_json, actor, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
          (prep_id, version, "HUMAN_EDITED", text, jdump(seg), jdump(base["human"]), jdump(base["evidence_basis"]), actor, utcnow()))
    db.ex("UPDATE outreach_preps SET current_version=?, status='EDITED', updated_at=? WHERE id=?", (version, utcnow(), prep_id))
    _event(p["case_id"], "DRAFT EDITED", f"version {version} (human-edited from v{base['version']})", "", f"outreach:{prep_id}:v{version}", actor)
    return get_prep(prep_id)


def set_status(prep_id: int, status: str, actor="user") -> dict:
    if status not in ("REVIEWED", "DISCARDED"):
        raise ValueError("a person may mark a draft REVIEWED or DISCARDED; nothing else, and never SENT")
    p = get_prep(prep_id)
    if not p:
        raise KeyError("no such preparation")
    if status == "REVIEWED" and not p["drafts"]:
        raise ValueError("nothing to review yet")
    db.ex("UPDATE outreach_preps SET status=?, active=?, updated_at=? WHERE id=?", (status, 0 if status == "DISCARDED" else 1, utcnow(), prep_id))
    _event(p["case_id"], "DRAFT REVIEWED" if status == "REVIEWED" else "DRAFT DISCARDED", f"version {p['current_version'] or '-'}", "", f"outreach:{prep_id}", actor)
    return get_prep(prep_id)


def for_case(case_id: int) -> list[dict]:
    return [get_prep(r["id"]) for r in db.q("SELECT id FROM outreach_preps WHERE case_id=? ORDER BY id", (case_id,))]


def public_summary(case_id: int) -> list[dict]:
    """What the public snapshot may carry: no draft text, no mailing address, no person's contact details."""
    out = []
    for p in for_case(case_id):
        g = p.get("gate") or {}
        out.append({"id": p["id"], "purpose": p["purpose"], "status": p["status"], "updated_at": p["updated_at"], "draft_version": p["current_version"],
                    "gate": {"ready": g.get("ready"), "blocking": g.get("blocking"), "requirements": [{"label": r["label"], "level": r["level"], "state": r["state"], "origin": r.get("origin")} for r in g.get("requirements", [])]},
                    "note": "draft text and addresses are held only in the local app; nothing is sent by the system"})
    return out
