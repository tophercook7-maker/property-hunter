"""Distress signal detection.

Every signal here is derived from evidence we actually hold, and each one
declares how confident it is and what would confirm it. Nothing in this module
asserts a fact about a property that a source did not give us.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from . import db, store
from .db import jdump

# Owner-name patterns that legitimately change how a deal is approached.
OWNER_PATTERNS = [
    (r"\bESTATE\b|\bHEIRS?\b|\bDECEASED\b|\bDECD\b", "estate_owner",
     "Owner of record looks like an estate or heirs",
     "Estates often want to sell and often have title work to finish first.",
     "MEDIUM"),
    (r"\b(BANK|MORTGAGE|FEDERAL NATIONAL|FANNIE|FREDDIE|HUD|SECRETARY OF HOUSING|"
     r"WELLS FARGO|US BANK|DEUTSCHE|CITIBANK)\b", "institutional_owner",
     "Owner of record looks like a bank or lender",
     "A lender holding a house usually took it back. They are motivated sellers.",
     "MEDIUM"),
    (r"^(CITY OF|COUNTY OF|GARLAND COUNTY|STATE OF|ARKANSAS )", "government_owner",
     "Owner of record looks like a government body",
     "Government-owned parcels are sometimes sold by a public process rather than "
     "on the open market.", "MEDIUM"),
    (r"\b(LLC|INC|CORP|PROPERTIES|INVESTMENTS|HOLDINGS|CAPITAL)\b", "entity_owner",
     "Owner of record is a company, not a person",
     "Companies are easier to research and sometimes easier to negotiate with.",
     "MEDIUM"),
    (r"\bTRUST(EE)?\b", "trust_owner",
     "Owner of record is a trust",
     "Trust sales can be straightforward but need the trustee's authority verified.",
     "MEDIUM"),
]


def _year(value: str | None) -> int | None:
    if not value:
        return None
    m = re.match(r"(\d{4})", str(value))
    return int(m.group(1)) if m else None


def analyse(prop: dict) -> list[dict]:
    """Return the distress / opportunity signals supported by current evidence."""
    signals: list[dict] = []

    def add(key, label, why, confidence, verify, weight="distress"):
        signals.append({"key": key, "label": label, "why": why,
                        "confidence": confidence, "verify": verify, "kind": weight})

    owner = (prop.get("owner_name") or "").upper()
    imp = prop.get("imp_value") or 0.0
    land = prop.get("land_value") or 0.0
    total = prop.get("total_value") or 0.0
    acreage = prop.get("acreage") or 0.0
    improved = prop.get("improved")
    pid = prop.get("id")

    # --- owner-based signals ------------------------------------------
    if not owner:
        add("unknown_owner", "No owner name in the record",
            "We do not know who owns this. That has to be answered before anything else.",
            "HIGH", "Pull the deed at the Circuit Clerk.", "risk")
    else:
        for pattern, key, label, why, conf in OWNER_PATTERNS:
            if re.search(pattern, owner):
                add(key, label, why, conf,
                    "Confirm the current owner on the recorded deed - the tax roll "
                    "lags real transfers.")

    # --- value-based signals ------------------------------------------
    if improved == 1 and 0 < imp < 15000:
        add("low_improvement_value",
            f"There is a building here but the county only values it at ${imp:,.0f}",
            "A very low improvement value on an improved parcel usually means the "
            "structure is in poor shape - or the record is out of date.",
            "MEDIUM",
            "Drive by and look at it, then check the Assessor's improvement record.")
    if improved == 1 and imp and land and imp < land * 0.35:
        add("building_worth_less_than_dirt",
            "The building is valued at well under the land it sits on",
            "When the dirt is worth more than the house, the house is often a "
            "teardown or a heavy rehab.",
            "MEDIUM", "Inspect the structure before assuming either way.")
    if improved == 0 or (imp or 0) < 500:
        add("vacant_land", "County record shows no improvement value",
            "This reads as a vacant lot, which opens land plays: storage, a "
            "workshop build, or a seasonal stand.",
            "MEDIUM",
            "Confirm nothing is standing on it - the footprint layer and a drive-by "
            "both help.", "opportunity")

    # --- record staleness ---------------------------------------------
    eff = store.known_value(pid, "parcel_id") and store.latest_evidence(pid, "total_assessed_value")
    eff_date = eff.get("effective_date") if eff else None
    y = _year(eff_date)
    if y and (date.today().year - y) >= 8:
        add("stale_assessment",
            f"The county's record for this parcel was last updated in {y}",
            "A record nobody has touched in years often sits under a property "
            "nobody is looking after.",
            "LOW", "Ask the Assessor when this parcel was last physically reviewed.")

    # --- structure cross-check ----------------------------------------
    fp = store.latest_evidence(pid, "structure_present")
    if fp and "no building footprint" in (fp["value"] or "") and improved == 1:
        add("record_says_building_map_says_none",
            "The tax roll says this parcel is improved, but no building shows in "
            "the mapped footprints",
            "That mismatch can mean the building was demolished, or just that the "
            "footprint data is behind. Either way it is worth a look.",
            "LOW", "Drive by, and compare against recent aerial imagery.", "conflict")
    if fp and "footprint(s) near centroid" in (fp["value"] or "") and improved == 0:
        add("map_says_building_record_says_vacant",
            "A building footprint shows on this parcel but the tax roll calls it vacant",
            "Sometimes this is an unpermitted or unassessed structure - sometimes it "
            "is just a neighbour's shed crossing the line.",
            "LOW", "Verify on the ground and with the Assessor.", "conflict")

    # --- access --------------------------------------------------------
    access = store.latest_evidence(pid, "legal_access")
    if access and "no road mapped" in (access["value"] or ""):
        add("possible_no_access", "No road shows next to this parcel on the map",
            "A lot with no legal way in is worth a fraction of one with frontage - "
            "and it is a classic trap.",
            "LOW", "Check the plat and the deed for a recorded easement.", "risk")

    # --- flood ---------------------------------------------------------
    zone = prop.get("flood_zone") or ""
    if zone[:1] in ("A", "V"):
        add("flood_zone", f"FEMA maps this as flood zone {zone}",
            "Being in a Special Flood Hazard Area means flood insurance and limits on "
            "what you can build.",
            "HIGH", "Pull the FIRMette from FEMA's Map Service Center.", "risk")

    # --- size ----------------------------------------------------------
    if acreage and acreage >= 1.0 and (imp or 0) < 500:
        add("useful_acreage", f"{acreage:.2f} acres of vacant land",
            "Enough room to actually build something on.",
            "MEDIUM", "Check zoning, setbacks, drainage and utilities before you plan.",
            "opportunity")
    if acreage and 0 < acreage < 0.06:
        add("tiny_lot", f"Very small parcel ({acreage:.3f} acres)",
            "Lots this small are often unbuildable remnants, alley strips or "
            "common areas.",
            "MEDIUM", "Check the plat to see what this piece actually is.", "risk")

    if not prop.get("address"):
        add("no_address", "No situs address on the tax roll",
            "Unaddressed parcels are usually raw land, remnants, or land that has "
            "never been built on.",
            "HIGH", "Locate it on the GIS map and confirm what it touches.", "risk")

    return signals


def score_of(signals: list[dict]) -> int:
    return sum(1 for s in signals if s["kind"] == "distress")


def save(prop_id: int, signals: list[dict]) -> None:
    db.ex("UPDATE properties SET distress_json=?, updated_at=datetime('now') WHERE id=?",
          (jdump(signals), prop_id))
    for s in signals:
        store.store_evidence(prop_id, [{
            "field": f"signal:{s['key']}", "value": s["label"],
            "evidence_type": "OBSERVATION", "confidence": s["confidence"],
            "source": "property_hunter.distress",
            "source_name": "Property Hunter distress analysis",
            "raw_ref": s["verify"],
        }])


def refresh(prop: dict) -> list[dict]:
    signals = analyse(prop)
    save(prop["id"], signals)
    return signals
