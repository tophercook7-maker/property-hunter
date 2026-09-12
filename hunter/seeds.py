"""Investigation seeds (spec 69).

These are addresses that came up during research as worth a look. They are NOT
deals, they are NOT verified, and nothing about them is assumed. The scanner
looks each one up in the live county parcel data and reports what it actually
finds - including "no parcel matched this address", which is itself useful.
"""
from __future__ import annotations

import re

from . import db, store
from .db import utcnow
from .normalize import normalize_address, street_only

SEED_ADDRESSES = [
    "100 Edwards Pl", "220 Elizabeth Ter", "120 Main", "307 Main",
    "2748 Malvern Ave", "249 Magnolia", "115 Magnolia", "119 Maiden",
    "504 Hollywood", "805 Illinois", "809 Illinois", "120 Iowa", "122 Iowa",
    "102 Ira St", "111 Isabelle", "612 Laser", "204 Leawood", "400 Leawood",
    "233 Leonard", "710 Leonard", "302 Lincoln",
]

SEED_NOTE = (
    "This address came from earlier research into Hot Springs distress records "
    "(vacant-structure and cleanup-lien activity). It is an INVESTIGATION SEED "
    "only - nothing about its current condition, ownership, tax status or "
    "availability has been verified, and the original distress record has not "
    "been re-confirmed. Everything shown here was re-read from the live county "
    "parcel data today."
)


def _parts(addr: str) -> tuple[str, str]:
    norm = normalize_address(addr)
    m = re.match(r"^(\d+)\s+(.*)$", norm)
    return (m.group(1), m.group(2)) if m else ("", norm)


def seed_where() -> str:
    """An ArcGIS WHERE clause that finds the seed addresses in the parcel layer."""
    clauses = []
    for addr in SEED_ADDRESSES:
        num, street = _parts(addr)
        if not num:
            continue
        stem = street.split(" ")[0].replace("'", "''")
        clauses.append(f"(adrnum={num} AND UPPER(pstrnam) LIKE '{stem}%')")
    return " OR ".join(clauses)


def mark_seeds() -> dict:
    """Tag whatever the scan actually matched, and report what it did not."""
    matched, unmatched = [], []
    for addr in SEED_ADDRESSES:
        num, street = _parts(addr)
        stem = street.split(" ")[0] if street else ""
        rows = db.q("SELECT id,address,city,excluded FROM properties "
                    "WHERE address_norm LIKE ? AND address_norm LIKE ?",
                    (f"{num} %", f"% {stem}%")) if num else []
        if not rows:
            unmatched.append(addr)
            continue
        for r in rows:
            store.add_timeline(r["id"], "seed", "Flagged as an investigation seed",
                               SEED_NOTE, source="research_notes")
            store.store_evidence(r["id"], [{
                "field": "investigation_seed", "value": f"listed as '{addr}'",
                "evidence_type": "UNKNOWN", "confidence": "NONE",
                "source": "research_notes",
                "source_name": "Earlier Hot Springs distress research (unverified)",
                "raw_ref": SEED_NOTE,
            }])
            store.add_task(r["id"], {
                "title": "Re-confirm why this address was flagged",
                "detail": "It came from earlier research into Hot Springs vacant-structure "
                          "and cleanup-lien activity, but that record has not been "
                          "re-verified. Ask the City whether it is still on any list.",
                "why": "A seed with no confirmed distress record is just an address.",
                "where_to_look": "Hot Springs Neighborhood Services / Code Enforcement",
                "source": "hs_vacant_structures", "manual": 1, "priority": 2,
            })
            matched.append({"id": r["id"], "seed": addr, "matched": r["address"],
                            "city": r["city"], "excluded": bool(r["excluded"])})
    return {"matched": matched, "unmatched": unmatched,
            "note": "An unmatched seed is not a failure - the address may not exist in "
                    "the county parcel layer in that form, may be inside a city-only "
                    "dataset, or may simply have been written down loosely. It is "
                    "reported rather than quietly dropped.",
            "checked_at": utcnow()}
