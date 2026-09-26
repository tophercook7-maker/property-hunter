"""Who owns Garland County.

Built from the county assessor's own namelist, which carries a stable Owner_ID.
That single field is the thing no other source we have gives us: it links every
parcel one owner holds, so concentration becomes visible instead of being
guessed at from name spellings.

What this publishes, and what it refuses to publish:

  PUBLISHED   owner name (public record), how many parcels they hold, the
              county's appraised total, which STATE the tax bill goes to,
              how many of their parcels sit inside Hot Springs Village.
  NEVER       the owner's street mailing address. The namelist is, in effect,
              a mailing list for 79,734 people; the derived state is the useful
              half and the street line is the half that turns a public record
              into a mail merge.

Hot Springs Village is inside these numbers on purpose. The hunt excludes the
Village because POA dues make those lots bad buys; the ownership picture is a
different question, and the interesting part of the answer is *in* the Village.
Every count here says which side of that line it falls on.
"""
from __future__ import annotations

import argparse, collections, json, os, re, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENTROIDS = os.path.join(ROOT, "data", "garland_centroids.json")
OUT = os.path.join(ROOT, "docs", "data", "owners.json")

STATE_RE = re.compile(r"\b([A-Z]{2})\s+\d{5}")
ENTITY_RE = re.compile(r"\b(LLC|L L C|INC|CORP|TRUST|LP|LTD|COMPANY|CO|PARTNERS|HOLDINGS|PROPERTIES|ENTERPRISES|ASSOCIATION|POA|BANK|CHURCH|CITY OF|COUNTY|STATE OF)\b")


def norm_parcel(v):
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def read_rows(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h is not None else "" for h in next(it)]
    for r in it:
        yield dict(zip(hdr, r))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    a = ap.parse_args()

    village = set()
    coords = {}
    if os.path.exists(CENTROIDS):
        from hunter import exclusions
        exclusions.refresh_cache()
        coords = {norm_parcel(k): v for k, v in json.load(open(CENTROIDS)).items()}
        for pid, (lat, lon) in coords.items():
            v = exclusions.check(lat=lat, lon=lon, subdivision=None, address=None, city=None)
            if v.excluded:
                village.add(pid)
        print(f"centroids {len(coords):,} | inside Village/Diamondhead by polygon: {len(village):,}")
    else:
        print("WARNING: no centroid cache; Village counts will be reported as unknown")

    rows = list(read_rows(a.workbook))
    print(f"namelist rows: {len(rows):,}")

    by = collections.defaultdict(list)
    for r in rows:
        by[r.get("Owner_ID")].append(r)

    def state_of(r):
        m = STATE_RE.search(str(r.get("CSZ") or ""))
        return m.group(1) if m else None

    def val(r):
        v = r.get("Total_Value")
        return v if isinstance(v, (int, float)) else 0

    holders = []
    for oid, v in by.items():
        pids = [norm_parcel(x.get("PARCEL")) for x in v]
        known = [p for p in pids if p in coords]
        inv = sum(1 for p in pids if p in village)
        name = (v[0].get("NAME") or "").strip()
        holders.append({
            "owner": name,
            "parcels": len(v),
            "appraised": sum(val(x) for x in v),
            "state": state_of(v[0]),
            "village": inv if known else None,
            "located": len(known),
            "entity": bool(ENTITY_RE.search(name.upper())),
            "sample": [x.get("PARCEL") for x in v[:3]],
        })
    holders.sort(key=lambda h: (-h["parcels"], -h["appraised"]))

    states = collections.Counter()
    state_val = collections.Counter()
    for r in rows:
        s = state_of(r) or "?"
        states[s] += 1
        state_val[s] += val(r)

    oos = sum(v for k, v in states.items() if k not in ("AR", "?"))
    ent_parcels = sum(h["parcels"] for h in holders if h["entity"])
    located = sum(1 for r in rows if norm_parcel(r.get("PARCEL")) in coords)

    doc = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "county": "Garland",
        "county_fips": "05051",
        "source": "Garland County Assessor revised namelist 26-1543",
        "source_note": ("The county's own assessment extract. Owner of record is not clear title. "
                        "Appraised value is the county's figure, not market value and not a sale price. "
                        "Owner mailing addresses are in the source file and are deliberately not published here; "
                        "only the state the tax bill goes to is shown."),
        "village_note": ("Village and Diamondhead parcels are excluded from the hunt but counted here, because "
                         "who is buying there is the question this page answers. Village membership is decided by "
                         "the Census boundary polygon, never by the subdivision name."),
        "totals": {
            "parcels": len(rows),
            "owners": len(by),
            "appraised_total": sum(val(r) for r in rows),
            "out_of_state_parcels": oos,
            "out_of_state_pct": round(oos / len(rows) * 100, 1),
            "entity_owned_parcels": ent_parcels,
            "with_deed_reference": sum(1 for r in rows if r.get("Book") and r.get("Page")),
            "parcels_located": located,
            "parcels_not_located": len(rows) - located,
            "village_parcels": sum(1 for r in rows if norm_parcel(r.get("PARCEL")) in village) if coords else None,
        },
        "concentration": {
            "owners_1": sum(1 for v in by.values() if len(v) == 1),
            "owners_2_9": sum(1 for v in by.values() if 2 <= len(v) <= 9),
            "owners_10_24": sum(1 for v in by.values() if 10 <= len(v) <= 24),
            "owners_25_99": sum(1 for v in by.values() if 25 <= len(v) <= 99),
            "owners_100plus": sum(1 for v in by.values() if len(v) >= 100),
            "parcels_held_by_10plus": sum(len(v) for v in by.values() if len(v) >= 10),
        },
        "top_holders": holders[:100],
        "by_state": [{"state": s, "parcels": n, "appraised": state_val[s]}
                     for s, n in states.most_common(25)],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w"), separators=(",", ":"))
    print(f"wrote {OUT}")
    t = doc["totals"]
    print(f"  parcels {t['parcels']:,} | owners {t['owners']:,} | "
          f"out-of-state {t['out_of_state_parcels']:,} ({t['out_of_state_pct']}%) | "
          f"village {t['village_parcels']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
