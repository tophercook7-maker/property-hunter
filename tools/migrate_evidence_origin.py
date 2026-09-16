"""P3A: add the canonical ORIGIN to historical evidence rows whose provenance is known from their
source name, field or evidence type. Additive and idempotent: only rows with origin NULL are touched,
nothing is deleted or re-valued, and a row the rules cannot place is left NULL and counted.

    python3 tools/migrate_evidence_origin.py [--dry]
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q, ex, connect  # noqa: E402
from hunter import store  # noqa: E402


def main():
    dry = "--dry" in sys.argv
    init_db()
    before = q("SELECT COUNT(*) n FROM evidence WHERE origin IS NULL")[0]["n"]
    rules = [
        ("MANUAL_VERIFICATION", "source IN (%s) OR field LIKE 'manual:%%' OR field LIKE 'photo:%%'" % ",".join("?" * len(store.MANUAL_SOURCES)), store.MANUAL_SOURCES),
        ("NOTE", "source IN (%s) OR field IN ('field_observation','investigation_seed')" % ",".join("?" * len(store.NOTE_SOURCES)), store.NOTE_SOURCES),
        ("AI_OPINION", "source IN (%s) OR evidence_type='AI_OPINION' OR field LIKE 'vision:%%'" % ",".join("?" * len(store.AI_SOURCES)), store.AI_SOURCES),
        ("DERIVED", "source IN (%s) OR field LIKE 'signal:%%' OR evidence_type IN ('CALCULATION','ESTIMATE')" % ",".join("?" * len(store.DERIVED_SOURCES)), store.DERIVED_SOURCES),
    ]
    known = {r["name"] for r in q("SELECT name FROM sources")} | {"cosl_reports", "county_delinquent_list", "county_tax_collector", "cosl_listings", "ar_gis_parcels"}
    counts = {}
    for origin, where, params in rules:
        n = q(f"SELECT COUNT(*) n FROM evidence WHERE origin IS NULL AND ({where})", tuple(params))[0]["n"]
        counts[origin] = n
        if not dry and n:
            ex(f"UPDATE evidence SET origin=? WHERE origin IS NULL AND ({where})", (origin, *params))
    # automated adapters: only sources this app registered (or its own importers) — never a guess
    marks = ",".join("?" * len(known))
    n = q(f"SELECT COUNT(*) n FROM evidence WHERE origin IS NULL AND source IN ({marks})", tuple(known))[0]["n"]
    counts["AUTOMATED_SOURCE"] = n
    if not dry and n:
        ex(f"UPDATE evidence SET origin='AUTOMATED_SOURCE' WHERE origin IS NULL AND source IN ({marks})", tuple(known))
    connect().commit()
    left = q("SELECT source, COUNT(*) n FROM evidence WHERE origin IS NULL GROUP BY source ORDER BY n DESC")
    print({"rows_without_origin_before": before, "classified": counts, "dry": dry,
           "left_unclassified": [(r["source"], r["n"]) for r in left]})


if __name__ == "__main__":
    main()
