"""P2 INVESTIGATION CASE ENGINE.

One durable research object per property:  SIGNAL -> INVESTIGATION -> EVIDENCE -> ANSWERED / UNKNOWN
QUESTIONS -> NEXT ACTION.  A case records research state, never investment quality.

Three-state evidence model for every question:
  FOUND      verified evidence answering the question exists (an evidence row, or a manual verification)
  NOT_FOUND  a legitimate check was performed (named source, dated) and produced no such record
  UNKNOWN    the source is unavailable, not checked, stale, or otherwise insufficient
UNKNOWN never becomes NOT_FOUND without a named source check. NOT_FOUND never becomes FOUND without evidence.
Silence is never an answer.

Tax state comes from the ONE model in tools/build_share.tax_state (via export_rows(only=[pid])).
Sale state follows the same rule the site uses (PH.saleStatus): FOR SALE BY THE STATE only when the
State's own inventory certifies the parcel; otherwise UNKNOWN, because no listing source is connected.
Nothing here sends, contacts, spends or decides. A person performs consequential actions.
"""
from __future__ import annotations

import json
import os
import re

from . import db, store
from .db import jdump, jload, utcnow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DATA = os.path.join(ROOT, "docs", "data")

STATUSES = ("OPEN", "RESEARCHING", "WAITING_ON_SOURCE", "READY_FOR_REVIEW", "CLOSED")
STATES = ("FOUND", "NOT_FOUND", "UNKNOWN")
CATEGORIES = ("IDENTITY", "OWNERSHIP", "TAXES", "SALE_STATUS", "LIENS", "VACANCY", "CODE", "PROPERTY",
              "ACCESS", "MARKET", "PHYSICAL", "LEGAL_RECORD", "OTHER")
EVENT_CLASSES = ("SIGNAL RECEIVED", "INVESTIGATION OPENED", "SOURCE CHECK", "MANUAL VERIFICATION", "EVIDENCE ADDED",
                 "QUESTION ANSWERED", "QUESTION REMAINS UNKNOWN", "ACTION COMPLETED", "STATUS CHANGE", "NOTE ADDED",
                 # P3B outreach preparation (draft only; there is no SENT class because nothing can be sent)
                 "OUTREACH PREPARATION STARTED", "OUTREACH GATE EVALUATED", "DRAFT GENERATED", "DRAFT EDITED", "DRAFT REVIEWED", "DRAFT DISCARDED",
                 # P3C: a person's own record of what they did with a draft. HUMAN-REPORTED; never a system assertion of delivery.
                 "HUMAN OUTREACH ACTION",
                 # P6: the case was opened or attached from an address search (identity by AUTOMATED_SOURCE or HUMAN PROPERTY SELECTION)
                 "INVESTIGATION OPENED FROM ADDRESS SEARCH",
                 # P7: one orchestrated workup over every applicable domain
                 "WORKUP STARTED", "WORKUP COMPLETED",
                 # P8: a person's research task lifecycle; conflicts a person found; the gate looked at again (never sent)
                 "RESEARCH TASK OPENED", "RESEARCH TASK STARTED", "RESEARCH TASK COMPLETED", "RESEARCH TASK SKIPPED", "RESEARCH TASK BLOCKED", "CONFLICT RECORDED", "OUTREACH GATE RE-EVALUATED",
                 # P4 Bee: an AI_OPINION analysis was recorded (OK or FAILED); a person decided on a proposal
                 "BEE ANALYSIS", "BEE PROPOSAL DECISION",
                 # P5: one authorized check per accepted proposal
                 "INVESTIGATION CHECK STARTED", "INVESTIGATION CHECK SUCCEEDED", "INVESTIGATION CHECK FAILED", "INVESTIGATION CHECK BLOCKED", "INVESTIGATION CHECK STALE",
                 "INVESTIGATION CHECK PRODUCED EVIDENCE", "QUESTION REFRESHED", "PROPOSAL COMPLETED")
MANUAL_SOURCE = "manual_verification"
MANUAL_SOURCES = store.MANUAL_SOURCES          # P3A: one vocabulary, defined in store
NOTE_SOURCES = store.NOTE_SOURCES

# ------------------------------------------------------------------ questions (neutral wording)
# key: (category, wording, action kind)
QUESTIONS = {
    "identity":        ("IDENTITY",     "Is the parcel identity verified on the county roll (parcel number, county, situs)?", "REVIEW PROPERTY FILE"),
    "owner":           ("OWNERSHIP",    "Who is the recorded owner on the county roll, and as of what date?", "OPEN COUNTY SOURCE"),
    "deed":            ("OWNERSHIP",    "Is there a recorded deed reference, and if so, what does it document?", "OPEN COUNTY SOURCE"),
    "mailing_address": ("OWNERSHIP",    "Is there a mailing address of record for the owner, and from which source?", "OPEN COUNTY SOURCE"),
    "tax_state":       ("TAXES",        "What is the current tax state, from which source, and as of when?", "CHECK COLLECTOR"),
    "tax_delinquent":  ("TAXES",        "Is there verified delinquent tax evidence, and if so, what amount is documented?", "CHECK COLLECTOR"),
    "collector_asof":  ("TAXES",        "When did the county Collector last answer for this parcel, and what did it say?", "CHECK COLLECTOR"),
    "state_inventory": ("TAXES",        "Is the parcel in the State tax-sale inventory (Commissioner of State Lands)?", "OPEN STATE LANDS RECORD"),
    "state_record":    ("TAXES",        "Is there a State Lands listing record, and if so, what amount and sale information does it document?", "OPEN STATE LANDS RECORD"),
    "state_history":   ("TAXES",        "Is there a State sale or redemption record for this parcel?", "OPEN STATE LANDS RECORD"),
    "listing":         ("SALE_STATUS",  "Is there a connected public listing, and if so, from which source?", "SEARCH PUBLIC LISTINGS"),
    "sale_state":      ("SALE_STATUS",  "What is the sale state, and from which source?", "SEARCH PUBLIC LISTINGS"),
    "lien_city":       ("LIENS",        "Is there a recorded City cleanup or demolition lien on this parcel?", "REVIEW LIEN"),
    "lien_detail":     ("LIENS",        "If a lien is recorded: what filing date, claimant and amount are documented?", "REVIEW LIEN"),
    "lien_clerk":      ("LEGAL_RECORD", "Is there a recorded mortgage, judgment or tax lien at the Circuit Clerk?", "OPEN COUNTY SOURCE"),
    "vacancy":         ("VACANCY",      "Is there a City vacancy register record for this parcel?", "REVIEW VACANCY RECORD"),
    "code":            ("CODE",         "Is there a City code-enforcement case for this address?", "REVIEW CODE CASE"),
    "code_detail":     ("CODE",         "If a code case exists: what case date, status and violation type are documented?", "REVIEW CODE CASE"),
    "values":          ("PROPERTY",     "What does the county roll record for land, building and total value?", "REVIEW PROPERTY FILE"),
    "flood":           ("PROPERTY",     "Is there a FEMA flood-zone reading for this parcel?", "REVIEW PROPERTY FILE"),
    "access":          ("ACCESS",       "Is there evidence of road access to the parcel?", "REVIEW PROPERTY FILE"),
    "inspection":      ("PHYSICAL",     "Has a person inspected the property in person, and what did they record?", "PHYSICAL INSPECTION"),
    "title":           ("LEGAL_RECORD", "Is there a title search or Circuit Clerk index result on record?", "OPEN COUNTY SOURCE"),
    "auction":         ("MARKET",       "Is auction or acquisition information publicly documented (bid, sale date, terms)?", "OPEN STATE LANDS RECORD"),
    "other_signals":   ("OTHER",        "Are other public-record signals recorded on this parcel?", "REVIEW PROPERTY FILE"),
}
BASE = ["identity", "owner", "mailing_address", "tax_state", "tax_delinquent", "state_inventory", "sale_state", "listing", "other_signals"]
CHECKLISTS = {
    "NEW_TAX_SALE":         ["state_record", "identity", "state_history", "tax_state", "deed", "listing", "inspection", "auction"],
    "STATE_SOLD":           ["state_history", "state_record", "tax_state", "owner"],
    "STATE_REDEEMED":       ["state_history", "state_record", "tax_state", "owner"],
    "STATE_LEFT":           ["state_inventory", "state_history", "tax_state", "owner"],
    "NEW_LIEN":             ["lien_city", "lien_detail", "identity", "owner", "tax_state", "other_signals", "lien_clerk"],
    "NEW_VACANCY_RECORD":   ["vacancy", "owner", "tax_state", "lien_city", "code", "listing", "inspection"],
    "NEW_CODE_CASE":        ["code", "code_detail", "owner", "vacancy", "lien_city", "tax_state", "inspection"],
    "VERIFIED_DELINQUENCY": ["tax_delinquent", "collector_asof", "state_inventory", "sale_state", "owner", "lien_city"],
    "MANUAL":               ["values", "flood", "access", "title", "inspection"],
}
SIGNAL_LABEL = {"NEW_TAX_SALE": "New State tax-sale listing", "STATE_SOLD": "Sold at the State tax sale", "STATE_REDEEMED": "Redeemed by the owner (State report)",
                "STATE_LEFT": "Left the State tax-sale inventory", "NEW_VACANCY_RECORD": "New vacancy register record", "NEW_LIEN": "New City lien",
                "NEW_CODE_CASE": "New code-enforcement case", "VERIFIED_DELINQUENCY": "Delinquent at the county (verified)", "MANUAL": "Opened by a person"}


def _load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


# ------------------------------------------------------------------ property row through THE tax model

def property_row(pid: int) -> dict | None:
    from tools.build_share import export_rows
    rows, _ = export_rows(only=[pid])
    return rows[0] if rows else None


def sale_state(row: dict) -> dict:
    """Same rule as PH.saleStatus in docs/ph.js. No listing source is connected."""
    s = (row or {}).get("sale") or {}
    if s.get("st") in ("FOR_SALE", "NOT_FOR_SALE") and s.get("src"):
        return {"st": s["st"], "src": s["src"], "as_of": s.get("as_of"), "text": f"{s['st'].replace('_', ' ')} — {s['src']}"}
    t = (row or {}).get("taxs") or {}
    certified = t.get("st") == "TAX_SALE_VERIFIED" if t else str((row or {}).get("ts") or "").startswith("CERTIFIED")
    if certified:
        return {"st": "FOR_SALE_BY_STATE", "src": "Commissioner of State Lands", "as_of": t.get("as_of"),
                "text": "FOR SALE BY THE STATE — TAX SALE (Commissioner of State Lands); not a private listing"}
    return {"st": "UNKNOWN", "src": None, "as_of": None, "text": "UNKNOWN — no connected listing source. Property Hunter is not saying this is for sale or not for sale."}


def collector_status() -> dict:
    return (_load_json(os.path.join(DOCS_DATA, "status.json"), {}) or {}).get("countypay") or {}


def hunt_sources(fips: str) -> dict:
    c = ((_load_json(os.path.join(DOCS_DATA, "hunt_status.json"), {}) or {}).get("counties") or {}).get(fips) or {}
    return {"sources": c.get("sources") or {}, "finished_at": c.get("finished_at")}


def state_inventory_ctx() -> dict:
    d = _load_json(os.path.join(DOCS_DATA, "state_lands.json"), {}) or {}
    return {"built_at": d.get("built_at"), "listings": {(x.get("fips"), x.get("parcel_id")): x for x in d.get("listings", [])}}


# ------------------------------------------------------------------ evaluation from evidence only

def _ev(pid, field):
    """Only an AUTOMATED_SOURCE or MANUAL_VERIFICATION reading can answer a question (P3A)."""
    return store.latest_answer(pid, field)


def _ref(e):
    return f"evidence:{e['id']}" if e else None


def _when(e):
    return ((e or {}).get("effective_date") or (e or {}).get("created_at") or "")[:10] or None


def _found(answer, refs, source, url=None, at=None):
    return {"state": "FOUND", "answer": answer, "refs": [r for r in refs if r], "source": source, "source_url": url, "checked_at": at}


def _not_found(answer, source, url=None, at=None, refs=()):
    """Only ever called with a NAMED source and a date: a check that actually happened."""
    assert source and at, "NOT_FOUND requires a named source and a check date"
    return {"state": "NOT_FOUND", "answer": answer, "refs": [r for r in refs if r], "source": source, "source_url": url, "checked_at": at}


def _unknown(answer, source=None):
    return {"state": "UNKNOWN", "answer": answer, "refs": [], "source": source, "source_url": None, "checked_at": None}


def evaluate(prop: dict, row: dict, *, cp=None, hunt=None, inv=None) -> dict:
    """Automated answers, strictly from recorded evidence and named source checks."""
    pid = prop["id"]
    cp = collector_status() if cp is None else cp
    hunt = hunt_sources(prop["county_fips"]) if hunt is None else hunt
    inv = state_inventory_ctx() if inv is None else inv
    taxs = (row or {}).get("taxs") or {"st": "UNKNOWN"}
    out = {}
    # identity / ownership / values
    e = _ev(pid, "parcel_id")
    out["identity"] = _found(f"Parcel {prop.get('parcel_id')} in county {prop.get('county_fips')}; situs {prop.get('address') or 'not on the roll'}",
                             [_ref(e)], "Arkansas GIS Office (county assessor roll)", (e or {}).get("source_url"), _when(e)) if e else \
        _unknown("No roll evidence row for the parcel number on file")
    e = _ev(pid, "owner_name")
    out["owner"] = _found(f"{prop.get('owner_name')} (owner of record on the county roll, not a title opinion)", [_ref(e)],
                          "Arkansas GIS Office (county assessor roll)", (e or {}).get("source_url"), _when(e)) if e and prop.get("owner_name") else \
        _unknown("Owner of record not found on the roll reading; not a finding about ownership")
    e = _ev(pid, "owner_mailing_address") or _ev(pid, "manual:mailing_address")
    # a person's own row names its manual source so refresh() lets the person's chosen state stand (P8: an UNKNOWN attempt is not an address)
    out["mailing_address"] = _found(f"{e['value']} (where the tax bill goes; {e['origin_label'].lower()})", [_ref(e)], (e["source"] if e["origin"] == "MANUAL_VERIFICATION" else e.get("source_name") or e["source"]), e.get("source_url"), _when(e)) if e else \
        _unknown("MAILING ADDRESS: UNKNOWN — no mailing address of record has been read; the situs address is never substituted")
    e = _ev(pid, "deed_reference") or _ev(pid, "sourceref")
    out["deed"] = _found(f"Deed reference {e['value']}", [_ref(e)], e.get("source_name") or e["source"], e.get("source_url"), _when(e)) if e else \
        _unknown("No deed reference read; the Circuit Clerk index is the only complete answer")
    out["values"] = _found(f"land {prop.get('land_value')}, building {prop.get('imp_value')}, total {prop.get('total_value')} (assessor's figures, not a price)",
                           [_ref(_ev(pid, "total_assessed_value"))], "Arkansas GIS Office (county assessor roll)", None, _when(_ev(pid, "total_assessed_value"))) \
        if prop.get("total_value") is not None else _unknown("No value reading on the roll")
    e = _ev(pid, "flood_zone")
    out["flood"] = _found(f"FEMA zone {e['value']}", [_ref(e)], "FEMA National Flood Hazard Layer", e.get("source_url"), _when(e)) if e else _unknown("FEMA not asked for this parcel yet")
    e = _ev(pid, "road_access")
    out["access"] = _found(str(e["value"]), [_ref(e)], e.get("source_name") or e["source"], e.get("source_url"), _when(e)) if e else _unknown("No road-access reading on file")
    # taxes: the one model
    st = taxs.get("st")
    tax_text = {"TAX_SALE_VERIFIED": "TAX SALE — certified to the State for unpaid taxes", "DELINQUENT_VERIFIED": "DELINQUENT — verified",
                "CURRENT_BILL_OPEN": "CURRENT BILL OPEN — not delinquent", "CURRENT_VERIFIED": "CURRENT — no open bill at the Collector"}.get(st)
    cert = _ev(pid, "tax_delinquent"); bill = _ev(pid, "tax_bill"); chk = _ev(pid, "tax_status_check"); dq = _ev(pid, "tax_delinquent_county")
    amt = taxs.get("amt")
    if tax_text:
        ref = _ref(cert if st == "TAX_SALE_VERIFIED" else dq if (dq and st == "DELINQUENT_VERIFIED") else bill if bill else chk)
        out["tax_state"] = _found(f"{tax_text}{' — $' + format(amt, ',.2f') if amt is not None else ''} [{taxs.get('as_of')}]", [ref], taxs.get("src"), None, taxs.get("as_of"))
    elif st == "STALE":
        out["tax_state"] = _unknown(f"Last Collector answer ({taxs.get('was')}) is from {taxs.get('as_of')}, older than 45 days; treat as unknown until re-checked", taxs.get("src"))
    elif cp.get("open") is False:
        out["tax_state"] = _unknown(f"SOURCE UNAVAILABLE — the Collector's online search has been down since {(cp.get('down_since') or '')[:10]}; this parcel was not checked", "County Collector (CountyPay)")
    else:
        out["tax_state"] = _unknown("UNKNOWN — never checked at the Collector; not on the State list. Not a finding either way")
    if st in ("TAX_SALE_VERIFIED", "DELINQUENT_VERIFIED"):
        out["tax_delinquent"] = _found(f"Yes — {tax_text}{'; documented amount $' + format(amt, ',.2f') if amt is not None else '; amount not documented'}",
                                       [_ref(cert), _ref(dq)], taxs.get("src"), None, taxs.get("as_of"))
    elif st in ("CURRENT_BILL_OPEN", "CURRENT_VERIFIED"):
        out["tax_delinquent"] = _not_found(f"The Collector answered on {taxs.get('as_of')} with {'an open current-year bill' if st == 'CURRENT_BILL_OPEN' else 'no open real-estate bill'}; no delinquency documented",
                                           "County Collector", (bill or chk or {}).get("source_url"), taxs.get("as_of"), [_ref(bill or chk)])
    else:
        out["tax_delinquent"] = dict(out["tax_state"], answer="Cannot be answered: " + out["tax_state"]["answer"])
    ans = bill or (chk if chk and chk.get("source") in ("county_tax_collector",) else None)
    out["collector_asof"] = _found(f"{_when(ans)}: {ans['value'][:120]}", [_ref(ans)], "County Collector", ans.get("source_url"), _when(ans)) if ans else \
        _unknown("The Collector has never answered for this parcel" + (" (source unavailable)" if cp.get("open") is False else ""))
    # State Lands
    listing = inv["listings"].get((prop.get("county_fips"), prop.get("parcel_id")))
    if st == "TAX_SALE_VERIFIED":
        out["state_inventory"] = _found(f"Yes — in the State inventory as of {taxs.get('as_of')}", [_ref(cert)], "Commissioner of State Lands", (cert or {}).get("source_url"), taxs.get("as_of"))
        out["state_record"] = _found(f"Listing: starting bid ${listing.get('starting_bid') or 0:,.2f}; delinquent year {listing.get('delinquent_year') or 'not stated'}; sale type {listing.get('sale_type_text') or 'not stated'}",
                                     [_ref(cert)], "Commissioner of State Lands", listing.get("listing_url"), (inv.get("built_at") or "")[:10]) if listing else \
            _found("Certified per State evidence; listing detail not in today's export", [_ref(cert)], "Commissioner of State Lands", (cert or {}).get("source_url"), taxs.get("as_of"))
    elif (_ev(pid, "tax_status_check") or {}).get("source") == "cosl_listings" and not inv.get("built_at"):
        # P7: the State Lands per-parcel search answered "not held" for this parcel (dated), and there is no inventory export to read
        sc = _ev(pid, "tax_status_check")
        out["state_inventory"] = _not_found(f"{sc['value'][:140]} (State Lands per-parcel search, {_when(sc)}). Says nothing about the county bill", "Commissioner of State Lands", sc.get("source_url"), _when(sc), [_ref(sc)])
        out["state_record"] = out["state_inventory"]
    elif inv.get("built_at"):
        rem = _ev(pid, "tax_delinquent_removed")
        out["state_inventory"] = _not_found(f"Not in the State's inventory as of {inv['built_at'][:10]}. Says nothing about the county bill",
                                            "Commissioner of State Lands inventory", "https://www.cosl.org/", inv["built_at"][:10], [_ref(rem)])
        out["state_record"] = out["state_inventory"]
    else:
        out["state_inventory"] = _unknown("State inventory not read yet")
        out["state_record"] = out["state_inventory"]
    sold, red = _ev(pid, "tax_sale_history"), _ev(pid, "tax_redemption")
    h = sold or red
    out["state_history"] = _found(f"{'Sold' if sold else 'Redeemed'}: {h['value'][:140]}", [_ref(h)], "Commissioner of State Lands (monthly report)", h.get("source_url"), _when(h)) if h else \
        _unknown("No State sale or redemption record read for this parcel; monthly reports cover only counties with a history file")
    out["auction"] = _found(f"Starting bid ${listing.get('starting_bid') or 0:,.2f}; current bid ${listing.get('current_bid') or 0:,.2f}; ends {listing.get('ends') or 'not stated'}",
                            [_ref(cert)], "Commissioner of State Lands", listing.get("listing_url"), (inv.get("built_at") or "")[:10]) if listing else \
        _unknown("No public auction information on file; only the State's own listing carries it")
    # sale
    sale = sale_state(row)
    out["sale_state"] = _found(sale["text"], [_ref(cert)], sale["src"], (cert or {}).get("source_url"), sale.get("as_of")) if sale["st"] != "UNKNOWN" else _unknown(sale["text"])
    out["listing"] = _unknown("No listing source is connected; a public search is the only check and its result must be recorded by a person") if sale["st"] != "FOR_SALE_BY_STATE" else \
        _found("The seller is the State (tax sale), not a private listing", [_ref(cert)], "Commissioner of State Lands", None, sale.get("as_of"))
    # City registers (Garland only): FOUND from evidence; NOT_FOUND only when the scan read the registers
    city_ok = prop.get("county_fips") == "05051" and (hunt.get("sources") or {}).get("city_registers") and hunt.get("finished_at")
    for key, field, name, check_field in (("lien_city", "cleanup_lien_amount", "City of Hot Springs lien layer", "cleanup_lien_check"), ("vacancy", "vacant_structure", "City of Hot Springs vacant-structure register", "vacant_structure_check"),
                                          ("code", "code_case_open", "City of Hot Springs code-case layer", "code_case_check")):
        e = _ev(pid, field)
        chk_e = _ev(pid, check_field)
        if e and not (chk_e and (chk_e.get("effective_date") or chk_e.get("created_at") or "") > (e.get("effective_date") or e.get("created_at") or "")):
            out[key] = _found(str(e["value"])[:160], [_ref(e)], e.get("source_name") or name, e.get("source_url"), _when(e))
        elif chk_e:
            # P7: the City layer was asked for THIS parcel and answered "no record" (a dated reading from the adapter)
            out[key] = _not_found(f"{chk_e['value']} (the {name} answered for this parcel on {_when(chk_e)})", chk_e.get("source_name") or name, chk_e.get("source_url"), _when(chk_e), [_ref(chk_e)])
        elif city_ok:
            out[key] = _not_found(f"No record in the {name} when the hunt read it on {hunt['finished_at'][:10]}", name, None, hunt["finished_at"][:10])
        else:
            out[key] = _unknown("No City register covers this county; the county has no equivalent layer" if prop.get("county_fips") != "05051" else "City registers not read for this parcel yet")
    lien = _ev(pid, "cleanup_lien_amount")
    out["lien_detail"] = _found(f"Amount {lien['value']}; filing reference {lien.get('raw_ref') or 'not stated'}; claimant City of Hot Springs; date {_when(lien) or 'not stated'}",
                                [_ref(lien)], lien.get("source_name") or "City of Hot Springs", lien.get("source_url"), _when(lien)) if lien else \
        (out["lien_city"] if out["lien_city"]["state"] == "NOT_FOUND" else _unknown("No lien record to detail"))
    code = _ev(pid, "code_case_open")
    out["code_detail"] = _found(f"{code['value']}; date {_when(code) or 'not stated'}", [_ref(code)], code.get("source_name") or "City of Hot Springs", code.get("source_url"), _when(code)) if code else \
        (out["code"] if out["code"]["state"] == "NOT_FOUND" else _unknown("No code case to detail"))
    out["lien_clerk"] = _unknown("Circuit Clerk instruments are not read by this site; a person pulls the index")
    out["title"] = _unknown("No title search on record")
    # An inspection is answered only by the person who did it (log_manual sets the state they chose).
    # The automated evaluator never reads a manual row back as a source finding.
    out["inspection"] = _unknown("Nobody has recorded an in-person inspection")
    sig = [k for k in ("vac", "cc") if (row or {}).get(k)] + (["lien"] if (row or {}).get("lien") else []) + (["cert"] if st == "TAX_SALE_VERIFIED" else [])
    out["other_signals"] = _found("Recorded signals: " + ", ".join({"vac": "vacancy register", "cc": "code case", "lien": "City lien", "cert": "State certification"}[k] for k in sig),
                                  [], "property file (evidence above)", None, utcnow()[:10]) if sig else _unknown("No other verified public-record signal recorded on this parcel")
    return out


# ------------------------------------------------------------------ next actions: only real destinations

def next_action(key: str, prop: dict, row: dict, *, listing=None) -> dict:
    kind = QUESTIONS[key][2]
    cf, pid, cn = prop.get("county_fips") or "", prop.get("parcel_id") or "", (row or {}).get("cn") or ""
    file = f"lookup.html?county={cf}&q={pid or prop.get('address') or ''}"
    from .sources import countypay
    slug = countypay.SLUGS.get(cf)
    if kind == "OPEN STATE LANDS RECORD":
        return {"label": kind, "href": (listing or {}).get("listing_url") or "https://www.cosl.org/", "manual": False}
    if kind == "CHECK COLLECTOR":
        reg = (((_load_json(os.path.join(DOCS_DATA, "tax_sources.json"), {}) or {}).get("counties") or {}).get(cf) or {}).get("sources", {}).get("countypay", {})
        if slug and reg.get("status") != "NOT_FOUND":
            return {"label": kind, "href": f"{countypay.BASE}/{slug}", "manual": False, "also": {"label": "REQUEST COUNTY RECORD", "href": f"request.html?county={cn}"}}
        return {"label": "REQUEST COUNTY RECORD", "href": f"request.html?county={cn}", "manual": False}
    if kind == "SEARCH PUBLIC LISTINGS":
        from urllib.parse import quote
        return {"label": kind, "href": "https://www.google.com/search?q=" + quote(" ".join(x for x in (prop.get("address"), (row or {}).get("c"), "Arkansas") if x) + " listing"), "manual": False}
    if kind == "OPEN COUNTY SOURCE":
        if cf == "05051":
            return {"label": kind, "href": "https://www.actdatascout.com/RealProperty/Arkansas/Garland", "manual": False, "note": "browser only; the assessor refuses automated reading"}
        return {"label": "MANUAL ACTION REQUIRED", "href": None, "manual": True, "note": f"No known online source for county {cf}; the county clerk or assessor in person"}
    if kind in ("REVIEW LIEN", "REVIEW CODE CASE", "REVIEW VACANCY RECORD", "REVIEW PROPERTY FILE"):
        return {"label": kind, "href": file, "manual": False}
    if kind == "PHYSICAL INSPECTION":
        if prop.get("lat") and prop.get("lon"):
            return {"label": kind, "href": f"https://www.google.com/maps/dir/?api=1&destination={prop['lat']},{prop['lon']}", "manual": False}
        return {"label": "MANUAL ACTION REQUIRED", "href": None, "manual": True, "note": "No coordinates on the roll; locate by legal description"}
    return {"label": "MANUAL ACTION REQUIRED", "href": None, "manual": True}


PRIORITY = ["tax_state", "tax_delinquent", "state_record", "state_inventory", "lien_city", "vacancy", "code", "sale_state", "listing", "owner", "mailing_address",
            "deed", "lien_detail", "code_detail", "state_history", "collector_asof", "inspection", "lien_clerk", "title", "access", "flood", "values", "auction", "other_signals", "identity"]


# ------------------------------------------------------------------ case lifecycle

def _event(case_id, cls, title, detail="", ref=None, actor="property_hunter", ref_date=None):
    assert cls in EVENT_CLASSES
    db.ex("INSERT INTO investigation_events(case_id, at, actor, cls, title, detail, ref, ref_date) VALUES(?,?,?,?,?,?,?,?)",
          (case_id, utcnow(), actor, cls, title, detail or "", ref, ref_date))


def _touch(case_id):
    db.ex("UPDATE investigation_cases SET updated_at=? WHERE id=?", (utcnow(), case_id))


def case_for_property(pid: int):
    r = db.q1("SELECT * FROM investigation_cases WHERE property_id=?", (pid,))
    return dict(r) if r else None


def open_or_create(pid: int, signal: dict | None = None, actor: str = "user") -> dict:
    """INVESTIGATE PROPERTY: one case per property, ever. A repeated signal is recorded once."""
    prop = store.get_property(pid)
    if not prop:
        raise KeyError(f"no property {pid}")
    c = case_for_property(pid)
    created = False
    if not c:
        cls = (signal or {}).get("cls") or "MANUAL"
        now = utcnow()
        cur = db.ex("INSERT INTO investigation_cases(property_id, status, origin_cls, created_at, updated_at) VALUES(?,?,?,?,?)", (pid, "OPEN", cls, now, now))
        c = case_for_property(pid)
        created = True
        _event(c["id"], "INVESTIGATION OPENED", f"Investigation opened by {actor}", f"origin: {cls}", f"property:{pid}", actor)
    elif c["status"] == "CLOSED" and signal:
        set_status(c["id"], "OPEN", actor, reason=f"reopened by a new signal {signal.get('event')}")
    if signal:
        add_signal(c["id"], signal, actor)
    else:
        add_signal(c["id"], {"event": "MANUAL", "cls": "MANUAL", "label": SIGNAL_LABEL["MANUAL"], "src": actor, "evidence_ref": f"property:{pid}", "status": "OBSERVED", "kind": "observed"}, actor) if created else None
    ensure_questions(c["id"])
    refresh(c["id"])
    return get_case(c["id"])


def add_signal(case_id: int, sig: dict, actor="user") -> bool:
    """Record an originating signal with its provenance and classification, exactly once."""
    event = sig.get("event") or "MANUAL"
    ref = sig.get("evidence_ref") or ""
    if db.q1("SELECT 1 FROM investigation_signals WHERE case_id=? AND event=? AND IFNULL(evidence_ref,'')=?", (case_id, event, ref)):
        return False
    cls = sig.get("cls") or ("MANUAL" if event == "MANUAL" else "WORLD_EVENT")
    url = sig.get("src_url")
    url = url if url and re.match(r"^https?://", str(url)) else None      # never invent, never accept junk
    db.ex("""INSERT INTO investigation_signals(case_id, event, cls, label, event_date, discovered_at, src, src_url, evidence_ref, status, kind, why, taxs_json, sale_json, received_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (case_id, event, cls, sig.get("label") or SIGNAL_LABEL.get(event, event), (sig.get("date") or "")[:10] or None, sig.get("discovered_at"),
           sig.get("src"), url, ref or None, sig.get("status"), sig.get("kind"), sig.get("why"),
           jdump(sig.get("taxs")) if sig.get("taxs") else None, jdump(sig.get("sale")) if sig.get("sale") else None, utcnow()))
    _event(case_id, "SIGNAL RECEIVED", f"{SIGNAL_LABEL.get(event, event)} ({cls})", sig.get("why") or "", ref or None, sig.get("src") or actor, (sig.get("date") or "")[:10] or None)
    ensure_questions(case_id)
    _touch(case_id)
    return True


def ensure_questions(case_id: int):
    keys = list(BASE)
    for s in db.q("SELECT event FROM investigation_signals WHERE case_id=?", (case_id,)):
        for k in CHECKLISTS.get(s["event"], []):
            if k not in keys:
                keys.append(k)
    for k in CHECKLISTS["MANUAL"]:
        if k not in keys:
            keys.append(k)
    now = utcnow()
    for k in keys:
        cat, wording, _ = QUESTIONS[k]
        db.ex("INSERT OR IGNORE INTO investigation_questions(case_id, key, category, wording, state, created_at, updated_at) VALUES(?,?,?,?,'UNKNOWN',?,?)",
              (case_id, k, cat, wording, now, now))


def refresh(case_id: int, *, cp=None, hunt=None, inv=None) -> None:
    """Re-read the evidence and update automated answers. A manual FOUND is never downgraded by an
    automated non-answer; an automated FOUND (recorded evidence) supersedes a manual NOT_FOUND/UNKNOWN,
    and says so on the timeline."""
    c = db.q1("SELECT * FROM investigation_cases WHERE id=?", (case_id,))
    prop = store.get_property(c["property_id"])
    row = property_row(c["property_id"]) or {}
    auto = evaluate(prop, row, cp=cp, hunt=hunt, inv=inv)
    for qrow in db.q("SELECT * FROM investigation_questions WHERE case_id=?", (case_id,)):
        a = auto.get(qrow["key"])
        if not a:
            continue
        manual = qrow["checked_by"] == "manual"
        if manual and not (a["state"] == "FOUND" and qrow["state"] != "FOUND" and a["source"] and a["source"] not in MANUAL_SOURCES):
            continue      # a person's answer stands unless a recorded SOURCE finding supersedes it
        changed = a["state"] != qrow["state"] or (a["answer"] or "") != (qrow["answer"] or "")
        if not changed:
            continue
        db.ex("""UPDATE investigation_questions SET state=?, answer=?, evidence_refs_json=?, checked_at=?, checked_by=?, source=?, source_url=?, updated_at=? WHERE id=?""",
              (a["state"], a["answer"], jdump(a["refs"]), a["checked_at"], "source" if a["state"] != "UNKNOWN" else None, a["source"], a["source_url"], utcnow(), qrow["id"]))
        if a["state"] == "UNKNOWN":
            if qrow["state"] != "UNKNOWN":
                _event(case_id, "QUESTION REMAINS UNKNOWN", QUESTIONS[qrow["key"]][1], a["answer"], f"question:{qrow['key']}", a["source"] or "property_hunter")
        else:
            _event(case_id, "QUESTION ANSWERED", f"{QUESTIONS[qrow['key']][1]} → {a['state']}" + (" (recorded evidence supersedes the manual entry)" if manual else ""),
                   a["answer"], (a["refs"] or [f"question:{qrow['key']}"])[0], a["source"] or "property_hunter", a["checked_at"])
    _touch(case_id)


def set_status(case_id: int, status: str, actor="user", reason="") -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    old = db.q1("SELECT status FROM investigation_cases WHERE id=?", (case_id,))["status"]
    if old != status:
        db.ex("UPDATE investigation_cases SET status=?, updated_at=? WHERE id=?", (status, utcnow(), case_id))
        _event(case_id, "STATUS CHANGE", f"{old} → {status}", reason, None, actor)
    return {"status": status, "previous": old}


def add_note(case_id: int, body: str, actor="user") -> int:
    """A NOTE. Never evidence: it is stored in `notes` only, confidence UNVERIFIED."""
    if not (body or "").strip():
        raise ValueError("empty note")
    c = db.q1("SELECT property_id FROM investigation_cases WHERE id=?", (case_id,))
    cur = db.ex("INSERT INTO notes(property_id, investigation_id, kind, body, author, confidence, created_at) VALUES(?,?,?,?,?,?,?)",
                (c["property_id"], case_id, "investigation", body.strip(), actor, "UNVERIFIED", utcnow()))
    _event(case_id, "NOTE ADDED", body.strip()[:80], "", f"note:{cur.lastrowid}", actor)
    _touch(case_id)
    return cur.lastrowid


def log_manual(case_id: int, payload: dict, actor="user") -> dict:
    """MANUAL RESEARCH LOG. A person records: source checked, date, result, notes, optional evidence
    reference and document reference, optionally answering one question.
      - stored as an evidence row with source 'manual_verification' (OBSERVATION, MEDIUM), field 'manual:<key>'
      - never as an official source record; the panel labels it MANUAL VERIFICATION
      - NOT_FOUND requires a named source; FOUND requires a result; UNKNOWN records the attempt only."""
    c = db.q1("SELECT * FROM investigation_cases WHERE id=?", (case_id,))
    source = (payload.get("source") or "").strip()
    result = (payload.get("result") or "").strip()
    date = (payload.get("date") or utcnow()[:10]).strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date must be YYYY-MM-DD")
    if not source:
        raise ValueError("name the source you checked")
    key = payload.get("question")
    state = (payload.get("state") or "").upper() or None
    if key and key not in QUESTIONS:
        raise ValueError("unknown question")
    if state and state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    if state == "FOUND" and not result:
        raise ValueError("FOUND needs the result you found")
    if state == "NOT_FOUND" and not result:
        raise ValueError("NOT_FOUND needs what you searched for and where")
    url = payload.get("source_url") or None
    url = url if url and re.match(r"^https?://", str(url)) else None
    doc = (payload.get("document") or "").strip() or None
    field = f"manual:{key or 'check'}"
    value = result or f"checked {source}: no result recorded"
    # P8: what a person submits is typed by what it is. A deed they read is a FACT; what they saw is an OBSERVATION;
    # a figure they worked out is a CALCULATION. Default stays OBSERVATION / MEDIUM (the P2 behaviour).
    etype = (payload.get("evidence_type") or "OBSERVATION").upper()
    conf = (payload.get("confidence") or "MEDIUM").upper()
    if etype not in ("FACT", "OBSERVATION", "CALCULATION", "ESTIMATE") or conf not in ("HIGH", "MEDIUM", "LOW"):
        raise ValueError("evidence_type must be FACT / OBSERVATION / CALCULATION / ESTIMATE and confidence HIGH / MEDIUM / LOW")
    doc_id = payload.get("document_id")
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else None
    raw = "MANUAL VERIFICATION" + (f"; document: {doc}" if doc else "") + (f"; document_id: {doc_id}" if doc_id else "") + (f"; ref: {payload['evidence_ref']}" if payload.get("evidence_ref") else "") \
          + (f"; reference: {payload['reference']}" if payload.get("reference") else "") + (f"; fields: {jdump({k: v for k, v in fields.items() if v})}" if fields else "")
    store.store_evidence(c["property_id"], [{
        "field": field, "value": value, "evidence_type": etype, "confidence": conf, "origin": "MANUAL_VERIFICATION",
        "source": MANUAL_SOURCE, "source_name": f"{source} — MANUAL VERIFICATION by {actor}", "source_url": url,
        "effective_date": date, "raw_ref": raw[:2000]}])
    e = db.q1("SELECT id FROM evidence WHERE property_id=? AND field=? ORDER BY id DESC LIMIT 1", (c["property_id"], field))
    ref = f"evidence:{e['id']}" if e else None
    if doc:
        db.ex("INSERT INTO documents(property_id, category, title, url, notes, added_at) VALUES(?,?,?,?,?,?)",
              (c["property_id"], "manual_verification", doc[:120], url, f"case {case_id}; {source}; {date}", utcnow()))
    _event(case_id, "MANUAL VERIFICATION", f"{source} checked on {date}", result or "(no result recorded)", ref, actor, date)
    _event(case_id, "EVIDENCE ADDED", f"Manual verification stored as {field}", "labelled MANUAL VERIFICATION; not an official source record", ref, actor, date)
    if key and state:
        refs = [r for r in (ref, payload.get("evidence_ref")) if r]
        db.ex("""UPDATE investigation_questions SET state=?, answer=?, evidence_refs_json=?, checked_at=?, checked_by='manual', source=?, source_url=?, notes=?, updated_at=?
                 WHERE case_id=? AND key=?""",
              (state, f"MANUAL VERIFICATION: {result or 'checked, no result recorded'}", jdump(refs), date, source, url, payload.get("notes"), utcnow(), case_id, key))
        if state == "UNKNOWN":
            _event(case_id, "QUESTION REMAINS UNKNOWN", QUESTIONS[key][1], f"{source}: {result or 'insufficient to answer'}", f"question:{key}", actor, date)
        else:
            _event(case_id, "QUESTION ANSWERED", f"{QUESTIONS[key][1]} → {state} (manual)", result, ref or f"question:{key}", actor, date)
            _event(case_id, "ACTION COMPLETED", QUESTIONS[key][2], f"{source} on {date}", f"question:{key}", actor, date)
    _touch(case_id)
    return {"evidence_ref": ref, "question": key, "state": state}


def complete_action(case_id: int, key: str, actor="user", note="") -> None:
    if key not in QUESTIONS:
        raise ValueError("unknown question")
    _event(case_id, "ACTION COMPLETED", QUESTIONS[key][2], note, f"question:{key}", actor)
    _touch(case_id)


# ------------------------------------------------------------------ read model

def evidence_panel(pid: int) -> list[dict]:
    """Every evidence row that can answer a question, labelled by verification state and origin."""
    skip = ("coordinates", "acreage_from_geometry", "parcel_perimeter_m", "gis_publication_date", "parcel_type_code", "property_class",
            "improvement_state", "slope_pct", "terrain")
    out = []
    conf = {r["field"] for r in db.q("SELECT field FROM conflicts WHERE property_id=? AND status='NEEDS VERIFICATION'", (pid,))}
    for e in db.q("SELECT * FROM evidence WHERE property_id=? ORDER BY id DESC LIMIT 400", (pid,)):
        f = e["field"]
        if f in skip or f.startswith("signal:"):
            continue
        e = store.with_origin(dict(e))
        if e["evidence_type"] == "UNKNOWN" or e["origin"] in ("NOTE", "AI_OPINION"):
            state = "UNKNOWN"          # commentary and model opinions answer nothing
        elif f in ("tax_status_check", "tax_delinquent_removed") or f.endswith("_removed"):
            state = "NOT_FOUND"
        else:
            state = "FOUND"
        out.append({"ref": e["ref"], "field": f, "value": (e["value"] or "")[:200], "source": e["source"], "source_name": e["source_name"],
                    "url": e["source_url"] if e["source_url"] and str(e["source_url"]).startswith("http") else None,
                    "date": e["date"], "recorded_at": (e["created_at"] or "")[:16], "etype": e["evidence_type"], "conf": e["confidence"],
                    "state": state, "origin": e["origin"], "verification": e["origin_label"], "superseded": e["superseded"], "conflict": f in conf, "raw_ref": e["raw_ref"]})
    return out


def get_case(case_id: int) -> dict | None:
    c = db.q1("SELECT * FROM investigation_cases WHERE id=?", (case_id,))
    if not c:
        return None
    c = dict(c)
    prop = store.get_property(c["property_id"])
    row = property_row(c["property_id"]) or {}
    inv = state_inventory_ctx()
    listing = inv["listings"].get((prop.get("county_fips"), prop.get("parcel_id")))
    sigs = [dict(s) for s in db.q("SELECT * FROM investigation_signals WHERE case_id=? ORDER BY id", (case_id,))]
    for s in sigs:
        s["taxs"] = jload(s.pop("taxs_json"), None); s["sale"] = jload(s.pop("sale_json"), None)
    qs = []
    for qrow in db.q("SELECT * FROM investigation_questions WHERE case_id=?", (case_id,)):
        d = dict(qrow)
        d["evidence_refs"] = jload(d.pop("evidence_refs_json"), []) or []
        d["next"] = next_action(d["key"], prop, row, listing=listing)
        d["order"] = PRIORITY.index(d["key"]) if d["key"] in PRIORITY else 99
        qs.append(d)
    qs.sort(key=lambda x: (x["state"] != "UNKNOWN", x["order"]))
    open_q = [x for x in qs if x["state"] == "UNKNOWN"]
    nxt = dict(open_q[0]["next"], question=open_q[0]["key"], wording=open_q[0]["wording"]) if open_q else \
        {"label": "REVIEW PROPERTY FILE", "href": f"lookup.html?county={prop.get('county_fips')}&q={prop.get('parcel_id') or ''}", "manual": False, "question": None, "wording": "Every question has an answer or a recorded non-result; review and set the status"}
    events = [dict(e) for e in db.q("SELECT * FROM investigation_events WHERE case_id=? ORDER BY at, id", (case_id,))]
    for r in db.q("SELECT id, status, started_at, finished_at FROM investigations WHERE property_id=? AND status='complete'", (c["property_id"],)):
        events.append({"id": None, "case_id": case_id, "at": r["finished_at"] or r["started_at"], "actor": "property_hunter.investigator", "cls": "SOURCE CHECK",
                       "title": "Automated source sweep completed", "detail": "the local investigator asked every registered source", "ref": f"investigation_run:{r['id']}", "ref_date": None})
    events.sort(key=lambda e: e["at"] or "")
    notes = [dict(n) for n in db.q("SELECT id, kind, body, author, confidence, created_at FROM notes WHERE investigation_id=? ORDER BY id", (case_id,))]
    taxs = row.get("taxs") or {"st": "UNKNOWN", "src": None, "as_of": None, "amt": None, "conf": None}
    cp = collector_status()
    tax = dict(taxs, source_unavailable=bool(taxs.get("st") == "UNKNOWN" and cp.get("open") is False), collector=cp,
               evidence=[x for x in ([f"evidence:{store.latest_evidence(c['property_id'], f)['id']}" for f in ("tax_delinquent", "tax_bill", "tax_status_check", "tax_delinquent_county") if store.latest_evidence(c["property_id"], f)])])
    sale = sale_state(row)
    sale["evidence"] = [f"evidence:{store.latest_evidence(c['property_id'], 'tax_delinquent')['id']}"] if sale["st"] == "FOR_SALE_BY_STATE" and store.latest_evidence(c["property_id"], "tax_delinquent") else []
    found = [x for x in qs if x["state"] == "FOUND"]; nf = [x for x in qs if x["state"] == "NOT_FOUND"]
    return {"investigation_id": c["id"], "property_id": c["property_id"], "status": c["status"], "origin_cls": c["origin_cls"], "created_at": c["created_at"], "updated_at": c["updated_at"],
            "property": {"id": prop["id"], "address": prop.get("address"), "city": prop.get("city"), "county_fips": prop.get("county_fips"), "county": row.get("cn"),
                         "parcel_id": prop.get("parcel_id"), "owner": prop.get("owner_name"), "lat": prop.get("lat"), "lon": prop.get("lon"), "total_value": prop.get("total_value"),
                         "imp_value": prop.get("imp_value"), "land_value": prop.get("land_value"), "file": f"lookup.html?county={prop.get('county_fips')}&q={prop.get('parcel_id') or prop.get('address') or ''}"},
            "row": row, "signals": sigs, "tax": tax, "sale": sale, "questions": qs,
            "findings": [{"key": x["key"], "wording": x["wording"], "answer": x["answer"], "source": x["source"], "checked_at": x["checked_at"], "refs": x["evidence_refs"], "manual": x["checked_by"] == "manual"} for x in found],
            "checked_no_result": [{"key": x["key"], "wording": x["wording"], "answer": x["answer"], "source": x["source"], "checked_at": x["checked_at"], "manual": x["checked_by"] == "manual"} for x in nf],
            "unknowns": [{"key": x["key"], "wording": x["wording"], "answer": x["answer"], "next": x["next"]} for x in open_q],
            "next_action": nxt, "evidence": evidence_panel(c["property_id"]), "events": events, "notes": notes,
            "counts": {"found": len(found), "not_found": len(nf), "unknown": len(open_q), "signals": len(sigs), "evidence": len(evidence_panel(c["property_id"])), "notes": len(notes)}}


def index() -> dict:
    """Light lookup for every page: property id -> case summary, plus parcel keys for the watchlist."""
    by_prop, by_parcel = {}, {}
    for r in db.q("""SELECT c.id, c.property_id, c.status, c.updated_at, p.county_fips, p.parcel_id,
                            (SELECT COUNT(*) FROM investigation_questions q WHERE q.case_id=c.id AND q.state='UNKNOWN') unknown,
                            (SELECT COUNT(*) FROM investigation_signals s WHERE s.case_id=c.id) signals
                     FROM investigation_cases c JOIN properties p ON p.id=c.property_id"""):
        d = {"id": r["id"], "status": r["status"], "updated_at": r["updated_at"], "unknown": r["unknown"], "signals": r["signals"],
             "outreach": [{"purpose": o["purpose"], "status": o["status"]} for o in db.q("SELECT purpose, status FROM outreach_preps WHERE case_id=? AND active=1", (r["id"],))]}
        by_prop[str(r["property_id"])] = d
        by_parcel[f"{r['county_fips']}:{r['parcel_id']}"] = r["id"]
    return {"built_at": utcnow(), "count": len(by_prop), "by_property": by_prop, "by_parcel": by_parcel}


def export_all() -> dict:
    """Public read-only snapshot (docs/data/investigations.json): the index plus every case in full."""
    out = index()
    from . import outreach
    out["cases"] = {}
    for k in out["by_property"].values():
        c = get_case(k["id"])
        c["outreach"] = outreach.public_summary(k["id"])          # redacted: no draft text, no addresses
        from . import bee
        c["bee"] = bee.public_summary(k["id"])                     # metadata only: no text, no prompts
        out["cases"][str(k["id"])] = _redact_public(c)
    out["by_status"] = {}
    for c in out["cases"].values():
        out["by_status"][c["status"]] = out["by_status"].get(c["status"], 0) + 1
    out["note"] = "Snapshot exported by the local app; to continue an investigation, open it with the app running on this Mac (port 8234)."
    return out


REDACT_FIELDS = ("owner_mailing_address", "manual:mailing_address")


def _redact_public(c: dict) -> dict:
    """The public snapshot never carries where the owner receives mail. Origin, source, date and reference
    stay so provenance is still visible; the value itself is held only in the local app."""
    if c.get("row"):
        c["row"] = dict(c["row"], m=None)
    held = "[mailing address held in the local app]"
    c["evidence"] = [dict(e, value=held) if e["field"] in REDACT_FIELDS else e for e in c.get("evidence", [])]
    for q in c.get("questions", []):
        if q["key"] == "mailing_address" and q["state"] == "FOUND":
            q["answer"] = held
    for f in c.get("findings", []):
        if f["key"] == "mailing_address":
            f["answer"] = held
    c["events"] = [dict(e, detail=held) if (e.get("cls") in ("MANUAL VERIFICATION", "QUESTION ANSWERED") and "mailing" in (e.get("title") or "").lower()) else e for e in c.get("events", [])]
    # P3C: human notes and human-reported outreach details never leave the local app
    hn = "[human note held in the local app]"
    c["events"] = [dict(e, detail=hn + " — recorded by a human; not a system assertion that anyone received or answered") if e.get("cls") == "HUMAN OUTREACH ACTION"
                   else dict(e, title=hn, detail="") if e.get("cls") == "NOTE ADDED" else e for e in c["events"]]
    c["notes"] = [dict(n, body=hn) for n in c.get("notes", [])]
    c["events"] = [dict(e, detail="[Bee text held in the local app]") if e.get("cls") in ("BEE ANALYSIS", "BEE PROPOSAL DECISION") else e for e in c["events"]]
    return c
