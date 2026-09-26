"""Import a Garland County assessor NAMELIST workbook into the hunt.

The namelist is the county's own assessment extract: every parcel, its owner,
the deed book and page, a stable Owner_ID, and the human-readable subdivision
name. It carries three things no automated source we have gives us --

  * Book / Page          the recorded deed reference (actDataScout blocks us)
  * Owner_ID             a stable owner key, so one owner's whole portfolio
                         can be seen at once
  * SubdDesc             the real subdivision name, not the V##### code

-- and it covers the entire county rather than the slice we have scanned.

Two rules this importer must never break:

1. THE FILE HAS NO COORDINATES. Hot Springs Village is caught by polygon and
   by nothing else: the county writes these parcels with ordinary city names
   and Spanish subdivision names, so every text fallback misses. Importing
   without coordinates would put ~20,000 Village parcels into the working set.
   We join to cached State-layer centroids first and refuse to run without them.

2. THE FILE IS A MAILING LIST. Owner name plus mailing address for 79,734
   people. The mailing address is kept locally (outreach preparation needs it
   and redacts it on publish) but the published snapshot must never carry it.
   What gets published is the derived fact: which state the owner mails from.
"""
from __future__ import annotations

import argparse, json, os, re, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hunter import db, exclusions, store
from hunter.sources.base import Record

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENTROIDS = os.path.join(ROOT, "data", "garland_centroids.json")
SOURCE = "garland_namelist"
COUNTY_FIPS = "05051"

STATE_RE = re.compile(r"\b([A-Z]{2})\s+\d{5}")
CITY_RE = re.compile(r"^(.*?)\s+[A-Z]{2}\s+\d{5}")


def norm_parcel(v) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def owner_state(csz: str | None) -> str | None:
    m = STATE_RE.search(str(csz or ""))
    return m.group(1) if m else None


def read_rows(path: str):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h is not None else "" for h in next(it)]
    for r in it:
        yield dict(zip(hdr, r))


def build_record(row: dict, coord: list | None, file_label: str) -> Record:
    parcel = clean(row.get("PARCEL"))
    name = clean(row.get("NAME"))
    name2 = clean(row.get("NAME2"))
    owner = " ".join(x for x in (name, name2) if x) if name2 else name
    situs = clean(row.get("Location"))
    total = row.get("Total_Value")
    assessed = row.get("Total_Assessed")
    acres = row.get("ACRES")
    book, page = clean(row.get("Book")), clean(row.get("Page"))
    ostate = owner_state(row.get("CSZ"))

    fields = {
        "parcel_id": parcel,
        "rpid": clean(row.get("RPID")),
        "owner_name": owner,
        "county_fips": COUNTY_FIPS,
        "state": "AR",
        "legal": clean(row.get("Legal1")),
        "subdivision": clean(row.get("SubdDesc")),
        "total_value": total if isinstance(total, (int, float)) else None,
        "acreage": acres if isinstance(acres, (int, float)) else None,
    }
    if situs:
        fields["address"] = situs
    if coord:
        fields["lat"], fields["lon"] = coord[0], coord[1]

    now = datetime.now(timezone.utc).date().isoformat()
    ev = []

    def add(field, value, etype="FACT", conf="HIGH", note=None):
        if value in (None, ""):
            return
        ev.append({"field": field, "value": str(value), "evidence_type": etype,
                   "confidence": conf, "source": SOURCE, "source_url": None,
                   "observed_at": now, "note": note})

    add("owner_name", owner, note=f"Owner of record on {file_label}. Not clear title.")
    add("parcel_id", parcel)
    if isinstance(total, (int, float)):
        add("total_value", f"${total:,.0f}",
            note="County APPRAISED value from the assessor namelist. Not market value, not a sale price.")
    if isinstance(assessed, (int, float)):
        add("assessed_value", f"${assessed:,.0f}",
            note="Arkansas assesses at 20% of appraised value.")
    if book and page:
        add("deed_reference", f"Book {book} Page {page}",
            note="Recorded deed reference from the county namelist. The instrument itself is at the Circuit Clerk; this is the pointer, not the document.")
    if row.get("Owner_ID"):
        add("owner_id", row["Owner_ID"],
            note="County's stable owner key. Links every parcel this owner holds in Garland County.")
    if ostate:
        add("owner_mailing_state", ostate, etype="FACT",
            note="State the tax bill is mailed to. The street address is held locally and never published.")
    if clean(row.get("STR")):
        add("section_township_range", clean(row.get("STR")))
    if clean(row.get("SUBDIVISION")):
        add("subdivision_code", clean(row.get("SUBDIVISION")))
    blk, lot = clean(row.get("BLOCK")), clean(row.get("LOT"))
    if blk or lot:
        add("block_lot", f"Block {blk or '?'} Lot {lot or '?'}")

    return Record(source=SOURCE,
                  identity={"parcel_id": parcel, "address": situs,
                            "lat": fields.get("lat"), "lon": fields.get("lon"),
                            "owner": owner, "county_fips": COUNTY_FIPS},
                  fields=fields, evidence=ev,
                  raw={"owner_id": row.get("Owner_ID"), "book": book, "page": page,
                       "owner_state": ostate, "subd_code": clean(row.get("SUBDIVISION"))})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(CENTROIDS):
        raise SystemExit(
            f"refusing to import: {CENTROIDS} not found.\n"
            "The namelist has no coordinates, and Hot Springs Village is caught by\n"
            "polygon and nothing else. Run tools/fetch_garland_centroids.py first.")
    coords = json.load(open(CENTROIDS))
    print(f"centroids cached: {len(coords):,}")

    exclusions.refresh_cache()
    have = {b["key"] for b in exclusions._boundaries()}
    if not {"hot_springs_village", "diamondhead"} <= have:
        raise SystemExit(f"refusing to import: exclusion polygons missing {sorted({'hot_springs_village','diamondhead'} - have)}")

    label = os.path.basename(a.workbook)
    by_norm = {norm_parcel(k): v for k, v in coords.items()}

    unlocated: list = []
    stats = {"rows": 0, "with_coords": 0, "no_coords": 0, "excluded": 0,
             "created": 0, "updated": 0, "unchanged": 0}
    t0 = time.time()
    for row in read_rows(a.workbook):
        if a.limit and stats["rows"] >= a.limit:
            break
        stats["rows"] += 1
        pid = norm_parcel(row.get("PARCEL"))
        coord = by_norm.get(pid)
        stats["with_coords" if coord else "no_coords"] += 1
        if not coord:
            # No centroid means no polygon test, and the Village is caught by
            # polygon alone here. Importing it blind is how ~20,000 Village
            # parcels would enter the working set, so it does not get imported.
            # These are parcels the State layer does not carry; they are counted
            # and named, never silently dropped.
            unlocated.append(row.get("PARCEL"))
            continue
        rec = build_record(row, coord, label)
        v = exclusions.check(lat=coord[0], lon=coord[1],
                             subdivision=rec.fields.get("subdivision"),
                             address=rec.fields.get("address"), city=None)
        if v.excluded:
            stats["excluded"] += 1
        if a.dry_run:
            continue
        _, action, _ = store.ingest(rec, data_class="real")
        stats[action if action in stats else "unchanged"] += 1
        if stats["rows"] % 5000 == 0:
            el = time.time() - t0
            print(f"  {stats['rows']:,}  {stats['rows']/max(el,1):.0f}/s  {stats}", flush=True)

    print(f"\nDONE in {time.time()-t0:.0f}s")
    for k, v in stats.items():
        print(f"  {k:12s} {v:,}")
    if unlocated:
        path = os.path.join(ROOT, "data", "namelist_unlocated.json")
        json.dump(unlocated, open(path, "w"))
        print(f"\n  {len(unlocated):,} parcels had no centroid in the State layer and were NOT imported.")
        print(f"  They are listed in {path}. Without a coordinate there is no way to")
        print("  tell whether they sit inside Hot Springs Village, and guessing is the bug.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
