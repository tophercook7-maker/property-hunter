"""Who owns Arkansas: ownership concentration across all 75 counties.

The Garland view on the same page is grouped by the county's own stable owner
id, so one owner counts once however each deed spells the name. There is no
such id for the rest of the state, so this groups by the normalised name -- and
that is a weaker instrument in a way worth stating on the page rather than
burying:

  * One owner split across spellings counts as several. The federal government
    appears as USA, U S, U S A and US US; the highway department as both
    ARKANSAS STATE HIGHWAY COMMISSION and ARKANSAS STATE HIGHWAY COMM. So every
    concentration figure here is a FLOOR, never a ceiling.
  * Two different people with the same common name count as one. That inflates
    individual names and barely touches company names, which is why the entity
    flag matters when reading the table.

Published: owner name (public record), parcel count, how many counties, the
county appraised total. Never a mailing address -- the same rule as everywhere
else on this site.
"""
from __future__ import annotations

import argparse, json, os, re, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hunter import db                                    # noqa: E402
from hunter.config import TERRITORIES                    # noqa: E402

OUT = os.path.join(ROOT, "docs", "data", "owners_state.json")
TOP = 150
ENTITY_RE = re.compile(
    r"\b(LLC|L L C|INC|CORP|CORPORATION|TRUST|LP|LLP|LTD|COMPANY|CO|PARTNERS|HOLDINGS|"
    r"PROPERTIES|ENTERPRISES|ASSOCIATION|ASSN|POA|BANK|CHURCH|CITY OF|COUNTY|STATE OF|"
    r"USA|U S A|COMMISSION|DEPARTMENT|AUTHORITY|DISTRICT|UNIVERSITY|TIMBER|FOREST)\b")


def main() -> int:
    argparse.ArgumentParser().parse_args()
    db.init_db()
    county = {t["county_fips"]: t["county"] for t in TERRITORIES}

    tot = db.q("SELECT COUNT(*) c FROM properties")[0]["c"]
    names = db.q("SELECT COUNT(DISTINCT owner_norm) c FROM properties "
                 "WHERE owner_norm IS NOT NULL AND owner_norm!=''")[0]["c"]
    val = db.q("SELECT SUM(COALESCE(total_value,0)) v FROM properties")[0]["v"] or 0

    rows = db.q(f"""SELECT owner_norm, COUNT(*) n, COUNT(DISTINCT county_fips) counties,
                           SUM(COALESCE(total_value,0)) val,
                           MIN(owner_name) sample_name,
                           SUM(excluded) excluded
                    FROM properties
                    WHERE owner_norm IS NOT NULL AND owner_norm!=''
                    GROUP BY owner_norm ORDER BY n DESC LIMIT {TOP}""")
    holders = []
    for r in rows:
        nm = (r["sample_name"] or r["owner_norm"] or "").strip()
        holders.append({"owner": nm, "parcels": r["n"], "counties": r["counties"],
                        "appraised": r["val"], "entity": bool(ENTITY_RE.search(nm.upper())),
                        "excluded": r["excluded"]})

    per_county = []
    for r in db.q("""SELECT county_fips, COUNT(*) n, COUNT(DISTINCT owner_norm) owners,
                            SUM(COALESCE(total_value,0)) val
                     FROM properties GROUP BY county_fips ORDER BY n DESC"""):
        per_county.append({"fips": r["county_fips"], "county": county.get(r["county_fips"], r["county_fips"]),
                           "parcels": r["n"], "owners": r["owners"], "appraised": r["val"]})

    multi = db.q("""SELECT COUNT(*) c FROM (SELECT owner_norm FROM properties
                    WHERE owner_norm IS NOT NULL AND owner_norm!=''
                    GROUP BY owner_norm HAVING COUNT(DISTINCT county_fips) > 1)""")[0]["c"]

    doc = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "Arkansas, all 75 counties",
        "source": "Arkansas GIS Office statewide parcel layer (county assessor CAMA data, republished by the State)",
        "method_note": ("Grouped by normalised owner name, because only Garland County supplies a stable "
                        "owner id. One owner spelled two ways counts twice, so every figure here is a "
                        "floor and not a ceiling; two people sharing a common name count as one, which "
                        "inflates individual names and barely affects companies."),
        "privacy_note": "Owner names are public record. Mailing addresses are held locally and never published.",
        "totals": {"parcels": tot, "owner_names": names, "appraised_total": val,
                   "names_in_more_than_one_county": multi, "counties": len(per_county)},
        "top_holders": holders,
        "by_county": per_county,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w"), separators=(",", ":"))
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024:.0f} KB)")
    print(f"  {tot:,} parcels · {names:,} owner names · ${val:,.0f} appraised · "
          f"{multi:,} names in more than one county")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
