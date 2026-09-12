"""Structured per-property investigation (spec 32).

Eighteen stages. Each one either does real work against a real source, or
honestly reports that only a human can answer it and leaves a task behind.
Nothing in here invents a finding.
"""
from __future__ import annotations

import threading
from typing import Any

from . import analyzers, db, distress, reports, scoring, store
from .db import jdump, jload, utcnow
from .sources import get_source
from .sources.base import OK

STAGES = [
    ("identity", "Identity - is this one property or several?"),
    ("ownership", "Ownership of record"),
    ("taxes", "Taxes and delinquency"),
    ("cosl", "State land / tax-sale status"),
    ("vacancy", "Vacancy and abandonment"),
    ("code", "Code enforcement"),
    ("liens", "Liens and encumbrances"),
    ("gis", "Parcel geometry and buildings"),
    ("zoning", "Zoning"),
    ("flood", "Flood"),
    ("utilities", "Utilities"),
    ("access", "Access and road frontage"),
    ("market", "Market context"),
    ("rental", "Rental picture"),
    ("rehab", "Rehab picture"),
    ("financial", "The numbers"),
    ("risk", "Risk"),
    ("recommendation", "Final recommendation"),
]

_running: dict[int, dict] = {}


def state(prop_id: int) -> dict | None:
    return _running.get(prop_id)


def investigate(prop_id: int, background: bool = False) -> dict:
    if background:
        t = threading.Thread(target=_run, args=(prop_id,), daemon=True)
        t.start()
        return {"started": True, "property_id": prop_id}
    return _run(prop_id)


def _run(prop_id: int) -> dict:
    p = store.get_property(prop_id)
    if not p:
        return {"error": "no such property"}
    stages = [{"key": k, "label": l, "status": "waiting", "findings": [], "detail": ""}
              for k, l in STAGES]
    cur = db.ex("INSERT INTO investigations(property_id,status,stages_json,started_at) "
                "VALUES(?,'running',?,?)", (prop_id, jdump(stages), utcnow()))
    inv_id = cur.lastrowid
    _running[prop_id] = {"id": inv_id, "stages": stages}

    def stage(key) -> dict:
        return next(s for s in stages if s["key"] == key)

    def run(key, fn):
        s = stage(key)
        s["status"] = "running"
        _save(inv_id, stages)
        try:
            findings, detail, status = fn(p)
            s["findings"], s["detail"], s["status"] = findings, detail, status
        except Exception as exc:
            s["status"], s["detail"] = "failed", f"{type(exc).__name__}: {exc}"
        _save(inv_id, stages)
        p.update(store.get_property(prop_id) or {})

    for key, _ in STAGES[:-1]:
        run(key, _HANDLERS[key])

    # Final recommendation, computed from everything above.
    p = store.get_property(prop_id)
    distress.refresh(p)
    p = store.get_property(prop_id)
    scores = scoring.compute(p)
    dot = analyzers.deal_or_trap(p, scoring.scores_for(prop_id))
    final = stage("recommendation")
    final["status"] = "done"
    final["detail"] = f"{p['recommendation']} - {dot['verdict']}"
    final["findings"] = [
        {"text": scores["recommendation_reason"], "confidence": scores["overall"]["confidence"],
         "type": "AI_OPINION" if False else "CALCULATION"},
        {"text": dot["why"], "confidence": dot["confidence"], "type": "CALCULATION"},
    ]
    summary = {
        "recommendation": p["recommendation"],
        "deal_or_trap": dot,
        "scores": {k: v.get("score") for k, v in scoring.scores_for(prop_id).items()},
        "open_questions": scores["overall"]["unknowns"],
        "next_steps": [s["title"] for s in analyzers.next_steps(p)[:5]],
    }
    db.ex("UPDATE investigations SET status='complete', stages_json=?, summary_json=?, "
          "finished_at=? WHERE id=?", (jdump(stages), jdump(summary), utcnow(), inv_id))
    store.add_timeline(prop_id, "investigation", "Investigation completed",
                       f"Result: {p['recommendation']} ({dot['verdict']})")
    _running.pop(prop_id, None)
    return {"id": inv_id, "property_id": prop_id, "stages": stages, "summary": summary}


def _save(inv_id: int, stages: list[dict]) -> None:
    db.ex("UPDATE investigations SET stages_json=? WHERE id=?", (jdump(stages), inv_id))


def _f(text: str, confidence: str = "MEDIUM", kind: str = "OBSERVATION",
       source: str = "", url: str = "") -> dict:
    return {"text": text, "confidence": confidence, "type": kind,
            "source": source, "source_url": url}


def _manual(prop, source_name: str, extra: str = "") -> tuple[list, str, str]:
    src = get_source(source_name)
    if not src:
        return ([], "source not registered", "failed")
    store.add_task(prop["id"], src.manual_task(prop["id"]))
    return ([_f(src.what_to_check, "NONE", "UNKNOWN", src.name, src.url)],
            f"MANUAL VERIFICATION REQUIRED - {src.label}. {extra}".strip(),
            "manual")


# --------------------------------------------------------------- handlers --

def _identity(p):
    aliases = db.q("SELECT alias_type, alias_value FROM property_aliases "
                   "WHERE property_id=? ORDER BY alias_type", (p["id"],))
    dupes = db.q("SELECT id,address FROM properties WHERE id!=? AND address_norm=? "
                 "AND address_norm IS NOT NULL AND county_fips=?",
                 (p["id"], p.get("address_norm"), p.get("county_fips")))
    findings = [_f(f"Known by {len(aliases)} identifiers across sources "
                   f"({', '.join(sorted({a['alias_type'] for a in aliases}))}).",
                   "HIGH", "FACT", "property_hunter")]
    if dupes:
        findings.append(_f(f"{len(dupes)} other record(s) share this address - "
                           f"different parcels can sit at one address.",
                           "MEDIUM", "OBSERVATION"))
    return findings, f"{len(aliases)} identifiers, {len(dupes)} same-address neighbours", "done"


def _ownership(p):
    owner = p.get("owner_name")
    ev = store.latest_evidence(p["id"], "owner_name")
    findings = []
    if owner:
        findings.append(_f(f"Tax roll owner of record: {owner}"
                           + (f" (as of {ev['effective_date']})" if ev and ev.get("effective_date") else ""),
                           "HIGH", "FACT", ev["source"] if ev else "", ev["source_url"] if ev else ""))
        findings.append(_f("The tax roll lags real transfers. The deed is the truth, and "
                           "nobody has read it yet.", "HIGH", "UNKNOWN"))
    else:
        findings.append(_f("No owner name in any source we hold.", "HIGH", "UNKNOWN"))
    f2, d2, s2 = _manual(p, "garland_recorder")
    return findings + f2, d2, "manual"


def _taxes(p):
    return _manual(p, "garland_tax_collector",
                   "Nothing in the data we can reach says whether taxes are paid.")


def _cosl(p):
    findings, detail, status = _manual(p, "cosl")
    findings.append(_f("Reminder: in Arkansas, paying somebody else's delinquent taxes "
                       "does not make you the owner. Only a completed purchase from the "
                       "Commissioner does, and even then have an attorney check it.",
                       "HIGH", "FACT", "cosl"))
    return findings, detail, status


def _vacancy(p):
    findings = []
    fp = store.latest_evidence(p["id"], "structure_present")
    if fp:
        findings.append(_f(fp["value"], fp["confidence"], fp["evidence_type"], fp["source"]))
    sig = {s["key"] for s in (p.get("distress") or [])}
    if "low_improvement_value" in sig:
        findings.append(_f("The improvement value is low enough that the building may "
                           "be in poor shape - that often goes with vacancy.",
                           "LOW", "OBSERVATION"))
    f2, d2, s2 = _manual(p, "hs_vacant_structures")
    return findings + f2, d2, "manual"


def _code(p):
    return _manual(p, "hs_code_enforcement")


def _liens(p):
    findings, detail, status = _manual(p, "garland_recorder",
                                       "Liens and judgements are recorded documents.")
    findings.insert(0, _f("No lien information is held. That is a gap, not a clean "
                          "title.", "HIGH", "UNKNOWN"))
    return findings, detail, status


def _gis(p):
    findings = []
    for field in ("acreage_from_geometry", "parcel_perimeter_m", "coordinates",
                  "building_footprint_sqft"):
        ev = store.latest_evidence(p["id"], field)
        if ev:
            findings.append(_f(f"{field.replace('_',' ')}: {ev['value']}",
                               ev["confidence"], ev["evidence_type"], ev["source"],
                               ev["source_url"] or ""))
    if p.get("acreage") and store.latest_evidence(p["id"], "acreage_from_geometry"):
        try:
            geom = float(store.latest_evidence(p["id"], "acreage_from_geometry")["value"])
            roll = float(p["acreage"])
            if roll and abs(geom - roll) / roll > 0.25:
                findings.append(_f(f"The mapped polygon ({geom:.2f} ac) and the tax roll "
                                   f"({roll:.2f} ac) disagree by more than 25%.",
                                   "MEDIUM", "CONFLICTING"))
        except (TypeError, ValueError):
            pass
    src = get_source("ar_gis_footprints")
    if src and not findings:
        res = src.enrich(p)
        if res.status == OK and res.records:
            store.store_evidence(p["id"], res.records[0].evidence)
            findings.append(_f(res.detail, "MEDIUM", "OBSERVATION", src.name))
    return findings or [_f("No geometry detail held.", "LOW", "UNKNOWN")], \
        f"{len(findings)} geometry findings", "done"


def _zoning(p):
    return _manual(p, "hs_planning_zoning",
                   "Never assume a use is allowed until the City says it is.")


def _flood(p):
    src = get_source("fema_nfhl")
    if p.get("flood_zone"):
        return ([_f(f"FEMA flood zone: {p['flood_zone']}", "HIGH", "FACT", "fema_nfhl")],
                f"flood zone {p['flood_zone']}", "done")
    if not src:
        return [], "FEMA source not registered", "failed"
    res = src.enrich(p)
    if res.status != OK:
        return ([_f(res.detail, "NONE", "UNKNOWN")], res.detail, "unavailable")
    for rec in res.records:
        store.store_evidence(p["id"], rec.evidence)
        if rec.fields:
            db.ex("UPDATE properties SET flood_zone=? WHERE id=?",
                  (rec.fields.get("flood_zone"), p["id"]))
    return ([_f(res.detail, "HIGH", "FACT", "fema_nfhl")] +
            [_f("Checked at the parcel centroid only. Part of a parcel can be in a "
                "zone when the middle is not.", "HIGH", "OBSERVATION")],
            res.detail, "done")


def _utilities(p):
    store.add_task(p["id"], {
        "title": "MANUAL VERIFICATION REQUIRED - utilities at the road",
        "detail": "Find out what is actually available at this parcel: city water, "
                  "sewer or septic, electric service and its size, gas, and internet. "
                  "Ask the utility, not the seller.",
        "why": "Bringing utilities in can cost more than the land.",
        "where_to_look": "Hot Springs Utilities / Entergy / the local ISP",
        "manual": 1, "priority": 2, "source": "utilities"})
    findings = [_f("No utility data source is available to us for this county. "
                   "This has to be asked, not looked up.", "NONE", "UNKNOWN")]
    if (p.get("city") or "").lower() in ("", "unincorporated", "rural"):
        findings.append(_f("The parcel is outside city limits, which makes city water "
                           "and sewer less likely.", "LOW", "OBSERVATION"))
    return findings, "MANUAL VERIFICATION REQUIRED - utilities", "manual"


def _access(p):
    findings = []
    src = get_source("ar_gis_roads")
    if src:
        res = src.enrich(p)
        if res.status == OK and res.records:
            store.store_evidence(p["id"], res.records[0].evidence)
            if res.records[0].fields.get("road_class"):
                db.ex("UPDATE properties SET road_class=? WHERE id=?",
                      (res.records[0].fields["road_class"], p["id"]))
            findings.append(_f(res.detail, "HIGH", "OBSERVATION", src.name))
        else:
            findings.append(_f(f"Could not check roads: {res.detail}", "NONE", "UNKNOWN"))
    ev = store.latest_evidence(p["id"], "road_frontage_candidates")
    if ev:
        findings.append(_f(f"More than one road touches this parcel ({ev['value']}) - "
                           f"it may be a corner lot.", "MEDIUM", "OBSERVATION", ev["source"]))
    findings.append(_f("A road on the map is not the same as legal access. Recorded "
                       "frontage or an easement is what counts, and that is in the deed.",
                       "HIGH", "UNKNOWN"))
    return findings, "road context checked; legal access still unverified", "done"


def _market(p):
    total = p.get("total_value") or 0
    findings = []
    if total:
        findings.append(_f(f"County assessed total ${total:,.0f}. Arkansas assesses at "
                           f"20% of appraised value, so the county's implied market "
                           f"opinion is about ${total*5:,.0f}.",
                           "MEDIUM", "CALCULATION", "property_hunter"))
    city = p.get("city")
    if city and city.lower() not in ("unincorporated", "rural"):
        peers = db.q("""SELECT AVG(total_value) a, COUNT(*) n FROM properties
                        WHERE excluded=0 AND city=? AND property_type=? AND total_value>0""",
                     (city, p.get("property_type")))
        if peers and peers[0]["n"] > 3:
            findings.append(_f(f"Average assessed total for {peers[0]['n']} comparable "
                               f"{p.get('property_type')} parcels we hold in {city}: "
                               f"${peers[0]['a']:,.0f}.",
                               "LOW", "CALCULATION", "property_hunter",
                               ))
    f2, d2, s2 = _manual(p, "public_listings")
    findings.append(_f("We do not scrape listing portals. Real sale prices have to come "
                       "from you, an agent, or the recorded deeds.", "HIGH", "UNKNOWN"))
    return findings + f2, "assessment-based context only - no sales data held", "manual"


def _rental(p):
    if p.get("improved") != 1:
        return ([_f("Nothing standing to rent.", "MEDIUM", "OBSERVATION")],
                "not a rental as it sits", "done")
    fin = reports.default_financials(p)
    r = fin.get("rental")
    if not r:
        return ([_f("Not enough information to model a rental - we do not have a "
                    "building size.", "NONE", "UNKNOWN")], "no model", "done")
    ret = r["returns"]
    return ([_f(f"On the default assumptions: about ${ret['monthly_cash_flow']:,.0f}/month "
                f"cash flow, {ret['cap_rate']*100:.1f}% cap, "
                f"{ret['cash_on_cash']*100:.1f}% cash-on-cash.",
                "LOW", "ESTIMATE", "property_hunter"),
             _f(f"Break-even rent is about ${ret['break_even_rent']:,.0f}/month.",
                "LOW", "CALCULATION"),
             _f("The rent number driving all of this is an estimate from square "
                "footage, not a real rent on this street.", "HIGH", "UNKNOWN")],
            "modelled on default assumptions", "done")


def _rehab(p):
    sqft = p.get("building_sqft")
    if not sqft:
        return ([_f("No building size held, so no rehab estimate can be made.",
                    "NONE", "UNKNOWN")], "no size", "done")
    from .finance import rehab_estimate
    est = rehab_estimate(sqft, "medium")
    return ([_f(f"Rule-of-thumb medium rehab on {est['sqft']:,} sqft: "
                f"${est['estimate']:,.0f} (range ${est['range_low']:,.0f} - "
                f"${est['range_high']:,.0f}).", "LOW", "ESTIMATE"),
             _f(est["caveat"], "HIGH", "UNKNOWN")],
            f"${est['estimate']:,.0f} estimated", "done")


def _financial(p):
    fin = reports.default_financials(p)
    findings = [_f(fin["assessed_note"], "HIGH", "CALCULATION")]
    deal = fin.get("deal")
    if deal:
        findings.append(_f(deal["plain_english"][0], "LOW", "ESTIMATE"))
        findings.append(_f(deal["plain_english"][1], "LOW", "ESTIMATE"))
    st = fin.get("storage")
    if st and st["estimated_units_10x10"] > 0:
        findings.append(_f(f"As a storage site: roughly {st['estimated_units_10x10']} "
                           f"10x10 units, NOI around ${st['noi']:,.0f} on a "
                           f"${st['total_project_cost']:,.0f} project. Sketch only.",
                           "LOW", "ESTIMATE"))
    return findings, "modelled", "done"


def _risk(p):
    sheet = scoring.risk(p).as_dict()
    findings = [_f(l["reason"], "HIGH", "OBSERVATION") for l in sheet["lines"]]
    return findings, f"risk score {sheet['score']}", "done"


_HANDLERS = {
    "identity": _identity, "ownership": _ownership, "taxes": _taxes, "cosl": _cosl,
    "vacancy": _vacancy, "code": _code, "liens": _liens, "gis": _gis,
    "zoning": _zoning, "flood": _flood, "utilities": _utilities, "access": _access,
    "market": _market, "rental": _rental, "rehab": _rehab, "financial": _financial,
    "risk": _risk,
}
