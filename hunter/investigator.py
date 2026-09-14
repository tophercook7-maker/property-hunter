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


def _city(prop, source_name: str) -> tuple[list, str, bool]:
    """Run one City GIS adapter, store what it says, return findings + whether it
    answered. The City map answers for parcels inside city limits; outside it
    the honest answer is 'the City has nothing on this' and the county has no
    equivalent."""
    src = get_source(source_name)
    if not src:
        return [], "City adapter not registered", False
    res = src.enrich(prop)
    src.record_attempt(res)
    if res.status != OK:
        return [_f(f"Could not read the City layer: {res.detail}", "NONE", "UNKNOWN",
                   source_name)], res.detail, False
    findings = []
    for rec in res.records:
        store.store_evidence(prop["id"], rec.evidence)
        store.store_timeline(prop["id"], rec.timeline)
        if rec.fields:
            if rec.fields.get("parcel_id") and not prop.get("parcel_id"):
                store.adopt_parcel_id(prop["id"], rec.fields["parcel_id"])
            keep = {k: v for k, v in rec.fields.items()
                    if k in ("zoning", "rpid", "owner_name", "legal", "total_value",
                             "land_value", "imp_value", "parcel_type") and v is not None}
            store.set_fields(prop["id"], keep, source_name)
        for e in rec.evidence:
            findings.append(_f(str(e["value"]), e["confidence"], e["evidence_type"],
                               e["source"], e.get("source_url") or ""))
    return findings, res.detail, True


# --------------------------------------------------------------- handlers --

def _identity(p):
    # City-owned check rides along here - ownership by the City changes everything.
    _city(p, "hs_gis_city_property")
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
    # Where the tax bill goes says a lot about how motivated the owner might be.
    mailing, mdetail, mok = _city(p, "hs_gis_owner_mailing")
    findings += mailing
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
    """Ask the County Collector's own payment site (CountyPay): is there an open
    bill on this parcel, current or delinquent, and for how much."""
    src = get_source("county_tax_collector")
    res = src.enrich(p) if src and p.get("parcel_id") else None
    if res is not None and res.status == OK:
        findings = []
        for rec in res.records:
            store.store_evidence(p["id"], rec.evidence)
            if rec.fields:
                store.set_fields(p["id"], rec.fields, src.name)
            for ev in rec.evidence:
                findings.append(_f(ev["value"], ev.get("confidence", "HIGH"), ev.get("evidence_type", "FACT"), src.name))
        findings.append(_f("Prior years already certified to the State show at State Lands (next stage), "
                           "not on the Collector's site.", "HIGH", "FACT", "property_hunter"))
        return findings, res.detail, "done"
    findings, detail, status = _manual(p, "garland_tax_collector",
                                       "The Collector's online search did not answer" +
                                       (f": {res.detail}" if res is not None else "") + ".")
    return findings, detail, status


def _cosl(p):
    """Ask the Commissioner of State Lands directly (public per-parcel search):
    is this parcel certified for unpaid taxes, and if so how far behind."""
    findings, detail, status = [], "", "done"
    src = get_source("cosl_listings")
    res = src.enrich(p) if src and p.get("rpid") else None
    if res is not None and res.status == OK:
        for rec in res.records:
            store.store_evidence(p["id"], rec.evidence)
            if rec.fields:
                store.set_fields(p["id"], rec.fields, src.name)
        for rec in res.records:
            for ev in rec.evidence:
                findings.append(_f(ev["value"], ev.get("confidence", "HIGH"), ev.get("evidence_type", "FACT"),
                                   src.name))
        detail = res.detail
    else:
        findings, detail, status = _manual(p, "cosl")
        if p.get("rpid") is None:
            findings.append(_f("No RPID on file, so the State Lands search could not be run "
                               "automatically; search by parcel at cosl.org.", "NONE", "UNKNOWN"))
    findings.append(_f("Reminder: in Arkansas, paying somebody else's delinquent taxes "
                       "does not make you the owner. Only a completed purchase from the "
                       "Commissioner does, and even then have an attorney check it.",
                       "HIGH", "FACT", "cosl"))
    return findings, detail, status


def _vacancy(p):
    findings = []
    city, detail, ok = _city(p, "hs_gis_vacant")
    findings += city
    fp = store.latest_evidence(p["id"], "structure_present")
    if fp:
        findings.append(_f(fp["value"], fp["confidence"], fp["evidence_type"], fp["source"]))
    # Make sure the aerials are on file, then let the local vision model look at
    # the newest one. Its output is AI_OPINION at LOW confidence, nothing more.
    src = get_source("ar_gis_imagery")
    if src:
        res = src.enrich(p)
        if res.status == OK:
            for rec in res.records:
                store.store_evidence(p["id"], rec.evidence)
        newest = db.q1("SELECT id FROM photos WHERE property_id=? AND kind='aerial' "
                       "ORDER BY captured_at DESC LIMIT 1", (p["id"],))
        if newest:
            from .vision import analyse_photo_record
            v = analyse_photo_record(newest["id"])
            if "error" in v:
                findings.append(_f(f"Could not read the aerial: {v['error']}", "NONE", "UNKNOWN"))
            else:
                for obs in v["observations"][:4]:
                    findings.append(_f(obs + " - LOW CONFIDENCE", "LOW", "AI_OPINION",
                                       "local_vision_model"))
    sig = {s["key"] for s in (p.get("distress") or [])}
    if "low_improvement_value" in sig:
        findings.append(_f("The improvement value is low enough that the building may "
                           "be in poor shape - that often goes with vacancy.",
                           "LOW", "OBSERVATION"))
    on_register = bool(store.latest_evidence(p["id"], "vacant_structure"))
    if on_register:
        f2, d2, _ = _manual(p, "hs_vacant_structures",
                            "It IS on the register - find out what the City intends.")
        return findings + f2, f"ON the City vacant-structure register; {d2}", "done"
    return findings, ("not on the City register" if ok else detail), "done" if ok else "manual"


def _code(p):
    findings, detail, ok = _city(p, "hs_gis_code_cases")
    if store.latest_evidence(p["id"], "code_case_open") or \
            store.latest_evidence(p["id"], "code_case"):
        f2, d2, _ = _manual(p, "hs_code_enforcement", "There is a 2025 case on file.")
        return findings + f2, f"{detail}; {d2}", "done"
    return findings, detail if ok else detail, "done" if ok else "manual"


def _liens(p):
    findings, detail, ok = _city(p, "hs_gis_liens")
    has_lien = bool(store.latest_evidence(p["id"], "cleanup_lien_total"))
    f2, d2, _ = _manual(p, "garland_recorder",
                        "City housing liens are read from the City GIS; mortgages, "
                        "judgements and tax liens are recorded at the Circuit Clerk.")
    findings.append(_f("Mortgages, judgements and tax liens are NOT in the City layer - "
                       "the Circuit Clerk's index is the only complete answer.",
                       "HIGH", "UNKNOWN"))
    return findings + f2, (f"{detail}; " if ok else "") + d2, "manual"


def _gis(p):
    findings = []
    src = get_source("ar_gis_terrain")
    if src and not store.latest_evidence(p["id"], "slope_pct"):
        res = src.enrich(p)
        if res.status == OK and res.records:
            store.store_evidence(p["id"], res.records[0].evidence)
    terr = store.latest_evidence(p["id"], "terrain")
    if terr:
        findings.append(_f(f"Terrain: {terr['value']}", terr["confidence"],
                           terr["evidence_type"], terr["source"], terr["source_url"] or ""))
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
    findings, detail, ok = _city(p, "hs_gis_zoning")
    if ok and (store.get_property(p["id"]) or {}).get("zoning"):
        f2, d2, _ = _manual(p, "hs_planning_zoning",
                            "The district is known; whether YOUR use is permitted is not.")
        return findings + f2, f"{detail}; confirm the use with Planning", "done"
    f2, d2, _ = _manual(p, "hs_planning_zoning",
                        "Outside the City map - ask whether county rules or covenants apply.")
    return findings + f2, d2, "manual"


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
    findings, detail, ok = _city(p, "hs_gis_utilities")
    if ok:
        water = store.latest_evidence(p["id"], "city_water")
        if water and "at this address" in (water["value"] or ""):
            findings.append(_f("City water is at the address. Sewer, power and internet "
                               "still have to be asked about.", "HIGH", "OBSERVATION"))
            store.add_task(p["id"], {
                "title": "MANUAL VERIFICATION REQUIRED - power, gas and internet",
                "detail": "City water is confirmed from the meter layer. Ask Entergy about "
                          "service size and the ISPs about availability.",
                "why": "3D printers and a workshop need a real electrical service.",
                "where_to_look": "Entergy / local ISPs", "manual": 1, "priority": 3,
                "source": "utilities"})
            return findings, f"{detail}; power/internet still to ask", "done"
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
        findings.append(_f(f"County appraised total ${total:,.0f} - the assessor's "
                           f"opinion of full value; tax is charged on 20% of it "
                           f"(${total*0.2:,.0f}). Not a sale price.",
                           "MEDIUM", "CALCULATION", "property_hunter"))
    city = p.get("city")
    if city and city.lower() not in ("unincorporated", "rural"):
        peers = db.q("""SELECT AVG(total_value) a, COUNT(*) n FROM properties
                        WHERE excluded=0 AND city=? AND property_type=? AND total_value>0""",
                     (city, p.get("property_type")))
        if peers and peers[0]["n"] > 3:
            findings.append(_f(f"Average appraised total for {peers[0]['n']} comparable "
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
