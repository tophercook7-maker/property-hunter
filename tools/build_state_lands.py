"""Bake the State Lands (COSL) tax-delinquent inventory into docs/data/ for the site.

    python3 tools/build_state_lands.py                # every county with listings
    python3 tools/build_state_lands.py --details      # + per-listing detail pages (slow, polite)
    python3 tools/build_state_lands.py --county GARLAND

Output: docs/data/state_lands.json
  {"built_at": ..., "counties": {"GARLAND": {"fips": "05051", "count": 475}},
   "listings": [ {rpid, county, fips, owner, acreage, starting_bid, current_bid, bids, sale_type,
                  sale_type_text, ends, added, listing_url, map_url, gis_id,
                  parcel_id, address, city, zip, appraised, assessed, land, improvements,
                  parcel_type, legal, subdivision, deed_ref, deed_date, roll_date, lat, lon, extent,
                  delinquent_year, taxes, liens } ... ]}
Everything is public data read at a polite pace. Nothing here bids or logs in.
"""
import json, os, sys, time
from datetime import datetime, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.sources import cosl  # noqa: E402
from hunter import exclusions  # noqa: E402
from hunter.db import init_db  # noqa: E402

OUT = os.path.join(ROOT, "docs", "data", "state_lands.json")


def ms(v):
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).date().isoformat() if v else None
    except (ValueError, OSError, TypeError):
        return None


def build(counties=None, details=False, detail_counties=("GARLAND",)):
    init_db()
    exclusions.refresh_cache()          # the real HSV / Diamondhead polygons, if cached locally
    counts = cosl.county_counts()
    todo = [c for c in counts if not counties or c in counties]
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = {(x["county"], x["rpid"]): x for x in json.load(open(OUT)).get("listings", [])}
        except Exception:
            prev = {}
    out = []
    for county in todo:
        fips = cosl.COUNTY_FIPS.get(county)
        if not fips:
            print("skip unknown county", county); continue
        rows = cosl.listings(county)
        parcels = cosl.parcels_for_rpids([r["CoSLParcelNumber"] for r in rows], fips,
                                         owners={str(r["CoSLParcelNumber"]).strip(): r.get("Owner") for r in rows})
        joined = 0
        for r in rows:
            rpid = str(r.get("CoSLParcelNumber") or "").strip()
            p = parcels.get(rpid.lstrip("0") or rpid) or {}
            if p:
                joined += 1
            addr = " ".join((p.get("adrlabel") or "").split())
            item = {"rpid": rpid, "county": county, "fips": fips, "owner": r.get("Owner"),
                    "acreage": r.get("Acreage"), "starting_bid": r.get("StartingBid"),
                    "current_bid": r.get("CurrentBid"), "bids": r.get("NumberOfBids"),
                    "sale_type": r.get("SaleType"), "sale_type_text": cosl.SALE_TYPES.get(r.get("SaleType"), r.get("SaleType")),
                    "ends": r.get("End"), "added": (r.get("Added") or "")[:10] or None,
                    "listing_url": f"{cosl.AUCTION}/Auction/Listing/{r.get('ListingToken')}",
                    "map_url": f"{cosl.AUCTION}/auction/get-static-map?gisId={r.get('GisId')}" if r.get("GisId") else None,
                    "gis_id": r.get("GisId"),
                    "parcel_id": p.get("parcelid"), "join": p.get("join"), "address": addr or None,
                    "city": (p.get("adrcity") or "").strip() or None, "zip": p.get("adrzip5") or None,
                    "appraised": p.get("totalvalue"), "assessed": p.get("assessvalue"),
                    "land": p.get("landvalue"), "improvements": p.get("impvalue"),
                    "parcel_type": p.get("parceltype"), "legal": (p.get("parcellgl") or "").strip() or None,
                    "subdivision": (p.get("subdivision") or "").strip() or None,
                    "deed_ref": (p.get("sourceref") or "").strip() or None, "deed_date": ms(p.get("sourcedate")),
                    "roll_date": ms(p.get("camadate")), "sqm": p.get("Shape__Area"),
                    "lat": round(p["lat"], 6) if p.get("lat") else None,
                    "lon": round(p["lon"], 6) if p.get("lon") else None,
                    "extent": [round(v, 6) for v in p["extent"]] if p.get("extent") else None,
                    "delinquent_year": None, "taxes": None, "liens": None, "sale_url": None}
            v = exclusions.check(lat=p.get("lat"), lon=p.get("lon"), subdivision=p.get("subdivision"),
                                 address=addr, city=p.get("adrcity"))
            item["excluded_area"] = v.label if v.excluded else None
            old = prev.get((county, rpid))
            if old and old.get("delinquent_year"):
                for k in ("delinquent_year", "taxes", "liens", "sale_url"):
                    item[k] = old.get(k)
            elif details and county in detail_counties and r.get("ListingToken"):
                try:
                    d = cosl.listing_detail(r["ListingToken"])
                    item.update({k: d.get(k) for k in ("delinquent_year", "taxes", "liens", "sale_url")})
                except Exception as exc:
                    item["detail_error"] = str(exc)[:80]
                time.sleep(cosl.PAUSE)
            out.append(item)
        print(f"{county}: {len(rows)} listings, {joined} joined to a parcel", flush=True)
        time.sleep(cosl.PAUSE)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source": "Arkansas Commissioner of State Lands auction site + Arkansas GIS Office parcel layer",
           "counties": {c: {"fips": cosl.COUNTY_FIPS.get(c), "count": n} for c, n in counts.items()},
           "listings": out}
    json.dump(doc, open(OUT, "w"), separators=(",", ":"))
    return {"counties": len(todo), "listings": len(out), "kb": os.path.getsize(OUT) // 1024, "file": OUT}


if __name__ == "__main__":
    args = sys.argv[1:]
    counties = None
    if "--county" in args:
        counties = [args[args.index("--county") + 1].upper()]
    print(json.dumps(build(counties=counties, details="--details" in args), indent=1))
