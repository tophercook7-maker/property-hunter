"""Tax Sale Radar: what changed in the State's inventory this week, per county.

    python3 tools/build_radar.py

Keeps a daily snapshot of the inventory keys (docs/data/radar/snap-YYYY-MM-DD.json,
tiny), then writes docs/data/radar.json:

  {"built_at", "week_start", "counties": {"GARLAND": {
      "new": [listing...],        arrived in the last 7 days (excluding Village/Diamondhead)
      "gone": [listing...],       left in the last 7 days (sold or redeemed)
      "bids": [listing...],       currently carrying a bid
      "best_value": [listing...], highest appraised value per dollar owed, with a building
      "summary": {...},           counts + median owed + history summary
  }}}

This is the product: the same public data, sorted into "what to look at this week".
"""
import glob, json, os, sys
from datetime import date, datetime, timedelta, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
RADAR = os.path.join(DATA, "radar")


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def slim(x):
    keys = ("rpid", "county", "fips", "owner", "address", "city", "appraised", "improvements", "acreage", "sqm",
            "starting_bid", "current_bid", "bids", "ends", "added", "listing_url", "parcel_id", "delinquent_year",
            "lat", "lon", "extent", "subdivision", "legal", "excluded_area", "sale_type_text")
    return {k: x.get(k) for k in keys}


def main():
    inv = load(os.path.join(DATA, "state_lands.json"), {"listings": []})
    hist = load(os.path.join(DATA, "cosl_history.json"), {"counties": {}})
    os.makedirs(RADAR, exist_ok=True)
    today = date.today()
    keys_today = {}
    for x in inv["listings"]:
        keys_today.setdefault(x["county"], {})[x["rpid"]] = x.get("added")
    json.dump(keys_today, open(os.path.join(RADAR, f"snap-{today.isoformat()}.json"), "w"), separators=(",", ":"))
    # the oldest snapshot within the last 7 days is the baseline; before that, the listing's own 'added' date
    snaps = sorted(glob.glob(os.path.join(RADAR, "snap-*.json")))
    week_ago = (today - timedelta(days=7)).isoformat()
    base, base_date = None, None
    for s in snaps:
        d = os.path.basename(s)[5:15]
        if week_ago <= d < today.isoformat():          # an earlier day this week; today alone is no baseline
            base = load(s, None); base_date = d
            break
    out = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "week_start": week_ago, "counties": {}}
    by_county = {}
    for x in inv["listings"]:
        by_county.setdefault(x["county"], []).append(x)
    for county, rows in by_county.items():
        keep = [x for x in rows if not x.get("excluded_area")]
        base_keys = set((base or {}).get(county, {}).keys()) if base else None
        # P1 classification. WORLD_EVENT only when something outside this site says the record changed this week:
        # the State's own 'added' date, or absence from an earlier snapshot of the State's inventory.
        # Anything else is a FIRST_DISCOVERY (this site read it for the first time; the record may be older).
        if base_keys is not None:
            new = [dict(slim(x), cls="WORLD_EVENT",
                        basis=("state_added_date" if (x.get("added") or "") >= week_ago else "inventory_snapshot:" + base_date))
                   for x in keep if x["rpid"] not in base_keys]
        else:
            new = [dict(slim(x), cls="WORLD_EVENT", basis="state_added_date") for x in keep if (x.get("added") or "") >= week_ago]
        gone = []
        if base_keys is not None:
            now_keys = {x["rpid"] for x in rows}
            gone_keys = [k for k in base_keys if k not in now_keys]
            hrows = load(os.path.join(DATA, "history", f"{county.replace(' ', '_')}.json"), {"sales": [], "redemptions": []})
            sold = {s["parcel"]: s for s in hrows.get("sales", [])}
            red = {r["parcel"]: r for r in hrows.get("redemptions", [])}
            for k in gone_keys:
                how = "sold" if k in sold else "redeemed" if k in red else "left the inventory"
                gone.append({"rpid": k, "county": county, "how": how, "detail": sold.get(k) or red.get(k),
                             # sold / redeemed come from the State's own deed and redemption reports: real-world events.
                             # a bare exit is only observed by this site; the cause is unknown.
                             "cls": "WORLD_EVENT" if how in ("sold", "redeemed") else "OBSERVED",
                             "basis": ("state_deed_report" if how == "sold" else "state_redemption_report" if how == "redeemed" else "inventory_snapshot:" + base_date)})
        bids = sorted([x for x in keep if (x.get("current_bid") or 0) > 0], key=lambda x: -(x["current_bid"] or 0))
        val = sorted([x for x in keep if (x.get("improvements") or 0) > 0 and x.get("starting_bid")],
                     key=lambda x: -((x.get("appraised") or 0) / max(x["starting_bid"], 1)))[:10]
        owed = sorted(x["starting_bid"] for x in keep if x.get("starting_bid"))
        h = (hist.get("counties") or {}).get(county, {})
        out["counties"][county] = {
            "fips": rows[0]["fips"],
            "new": new[:60], "gone": gone[:60], "bids": [slim(x) for x in bids][:40],
            "best_value": [slim(x) for x in val],
            "summary": {"for_sale": len(rows), "outside_village": len(keep), "with_building": sum(1 for x in keep if (x.get("improvements") or 0) > 0),
                        "new_this_week": len(new), "gone_this_week": len(gone), "with_bids": len(bids),
                        "median_owed": owed[len(owed) // 2] if owed else None,
                        "history": h.get("summary"), "top_buyers": (h.get("top_buyers") or [])[:5]}}
    json.dump(out, open(os.path.join(DATA, "radar.json"), "w"), separators=(",", ":"))
    tot = sum(c["summary"]["new_this_week"] for c in out["counties"].values())
    print(json.dumps({"counties": len(out["counties"]), "new_this_week": tot, "baseline": base_date if base else "listing dates"}))


if __name__ == "__main__":
    main()
