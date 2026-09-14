"""Stamp State Lands sales / redemption history onto the properties we hold.

Reads docs/data/cosl_history.json (built by tools/build_cosl_history.py) and, for
every Garland parcel in the database whose RPID appears there, records:

  * a FACT evidence row  'tax_sale_history'  - sold by the State on <date> to <buyer> for $<price>
                         'tax_redemption'    - redeemed by the owner on <date>, $<paid>
  * a timeline event for each, so the property page shows it in order.

Idempotent: keyed on (parcel, deed number) in raw_ref, so re-runs add nothing twice.

    python3 tools/ingest_cosl_history.py
"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q  # noqa: E402
from hunter import store  # noqa: E402

HIST = os.path.join(ROOT, "docs", "data", "cosl_history.json")
SRC = "cosl_reports"
LABEL = "Arkansas Commissioner of State Lands - monthly county deed reports"


def main():
    init_db()
    h = json.load(open(HIST))
    garland = (h.get("counties") or {}).get("GARLAND")
    if not garland:
        print("no Garland history in", HIST); return
    by_rpid = {r["rpid"]: r["id"] for r in q("SELECT id, rpid FROM properties WHERE rpid IS NOT NULL AND county_fips='05051'")}
    have = {(r["property_id"], r["raw_ref"]) for r in q("SELECT property_id, raw_ref FROM evidence WHERE source=?", (SRC,))}
    added = 0
    for s in garland["sales"]:
        pid = by_rpid.get(s["parcel"])
        if not pid:
            continue
        key = f"[key cosl-sale:{s['parcel']}:{s['deed_no']}]"
        if (pid, key) in have:
            continue
        price = s["price"] if s.get("price_known") else s["owed"]
        store.store_evidence(pid, [{
            "field": "tax_sale_history",
            "value": f"sold by the State at tax sale on {s['date']} to {s['buyer']} for "
                     f"${price:,.2f}{'' if s.get('price_known') else ' (the debt; no excess reported)'}",
            "evidence_type": "FACT", "confidence": "HIGH", "source": SRC, "source_name": LABEL,
            "source_url": "https://cosl.org/Countyfiles/CountyReports", "effective_date": s["date"],
            "raw_ref": key}])
        store.add_timeline(pid, "tax", f"Sold by the State for ${price:,.0f}", f"deed to {s['buyer']}",
                           source=SRC, event_date=s["date"])
        added += 1
    for r in garland["redemptions"]:
        pid = by_rpid.get(r["parcel"])
        if not pid:
            continue
        key = f"[key cosl-redeem:{r['parcel']}:{r.get('date')}]"
        if (pid, key) in have:
            continue
        store.store_evidence(pid, [{
            "field": "tax_redemption",
            "value": f"owner redeemed from the State on {r['date']}, paying ${r['paid'] or 0:,.2f} "
                     f"for tax years {r['years'] or '?'}",
            "evidence_type": "FACT", "confidence": "HIGH", "source": SRC, "source_name": LABEL,
            "source_url": "https://cosl.org/Countyfiles/CountyReports", "effective_date": r["date"],
            "raw_ref": key}])
        store.add_timeline(pid, "tax", "Owner redeemed the taxes from the State",
                           f"paid ${r['paid'] or 0:,.0f}", source=SRC, event_date=r["date"])
        added += 1
    print(f"{added} history rows stamped onto Garland properties "
          f"({len(garland['sales'])} sales, {len(garland['redemptions'])} redemptions in the file)")


if __name__ == "__main__":
    main()
