#!/usr/bin/env python3
"""Foreclosure sale notices → docs/data/foreclosures.json, with each notice joined to
its parcel on the roll when the address matches, and a dated evidence row on that
parcel so its Property File carries the notice.

    python3 tools/build_foreclosures.py              # read the live notice service
    python3 tools/build_foreclosures.py --fixture F  # parse a saved page (tests)

Facts only. The notice service gives sale date, time, address, courthouse and
auctioneer. It does not give the opening bid (announced at the sale), the lender,
or the recorded notice; those are at the county recorder and in the newspaper,
and the page links to where to look. A notice leaving the list is recorded as
"no longer noticed; outcome unknown", never as "sold".
"""
from __future__ import annotations

import json, os, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter import db, store                                   # noqa: E402
from hunter.sources import foreclosure_notices as src          # noqa: E402

OUT = os.path.join(ROOT, "docs", "data", "foreclosures.json")
HIST = os.path.join(ROOT, "docs", "data", "foreclosures_history.json")


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def join_to_roll(notices: list[dict]) -> int:
    """Exact match on county + normalised street address. One parcel or none; never a guess."""
    joined = 0
    for n in notices:
        n.update({"parcel_id": None, "property_id": None, "owner": None, "appraised": None, "lat": None, "lon": None, "join": None})
        if not (n.get("county_fips") and n.get("address_norm")):
            continue
        rows = db.q("SELECT id, parcel_id, owner_name, total_value, lat, lon FROM properties WHERE county_fips=? AND address_norm=? AND excluded=0 LIMIT 3",
                    (n["county_fips"], n["address_norm"]))
        if len(rows) == 1:
            r = rows[0]
            n.update({"parcel_id": r["parcel_id"], "property_id": r["id"], "owner": r["owner_name"], "appraised": r["total_value"],
                      "lat": r["lat"], "lon": r["lon"], "join": "address"})
            joined += 1
        elif len(rows) > 1:
            n["join"] = f"ambiguous ({len(rows)} parcels share this address)"
    return joined


def write_evidence(notices: list[dict], gone: list[dict], today: str) -> int:
    n_rows = 0
    for n in notices:
        if not n.get("property_id"):
            continue
        when = f"{n['sale_date']}{' ' + n['sale_time'] if n.get('sale_time') else ''}"
        store.store_evidence(n["property_id"], [{
            "field": "foreclosure_notice", "evidence_type": "FACT", "confidence": "HIGH",
            "value": f"Foreclosure sale noticed for {when} at {n.get('location') or 'the county courthouse'}"
                     f"{'; auctioneer ' + n['auctioneer'] if n.get('auctioneer') else ''}"
                     f"{'; prior sale date ' + n['prior_sale_date'] if n.get('prior_sale_date') else ''}",
            "source": src.SOURCE, "source_name": src.SOURCE_NAME, "source_url": src.URL,
            "effective_date": today, "raw_ref": n["key"]}])
        n_rows += 1
    for g in gone:
        if not g.get("property_id"):
            continue
        store.store_evidence(g["property_id"], [{
            "field": "foreclosure_notice_check", "evidence_type": "OBSERVATION", "confidence": "MEDIUM",
            "value": f"No longer on the notice service as of {today} (was noticed for {g['sale_date']}). Outcome unknown: sold, postponed, cancelled or cured are all possible.",
            "source": src.SOURCE, "source_name": src.SOURCE_NAME, "source_url": src.URL,
            "effective_date": today, "raw_ref": g["key"]}])
        n_rows += 1
    return n_rows


def build(fixture: str | None = None, *, today: str | None = None, write: bool = True) -> dict:
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    notices = src.parse(open(fixture).read(), fetched_at=fetched_at) if fixture else src.fetch()
    joined = join_to_roll(notices)
    hist = _load(HIST, {"first_seen": {}, "gone": []})
    first_seen = hist.get("first_seen") or {}
    keys_now = {n["key"] for n in notices}
    new_keys = [k for k in keys_now if k not in first_seen]
    for k in new_keys:
        first_seen[k] = today
    for n in notices:
        n["first_seen"] = first_seen.get(n["key"], today)
        n["new_this_week"] = n["first_seen"] >= _week_ago(today)
    prev = {n["key"]: n for n in (_load(OUT, {}).get("notices") or [])}
    gone = [dict(prev[k], last_seen=today) for k in prev if k not in keys_now]
    hist["gone"] = (gone + (hist.get("gone") or []))[:500]
    hist["first_seen"] = {k: v for k, v in first_seen.items() if k in keys_now or any(g["key"] == k for g in hist["gone"])}
    by_county: dict[str, dict] = {}
    for n in sorted(notices, key=lambda x: (x["sale_date"], x.get("county") or "")):
        c = by_county.setdefault(n.get("county") or "Unknown", {"fips": n.get("county_fips"), "n": 0, "new": 0, "upcoming": 0})
        c["n"] += 1
        c["new"] += int(n["new_this_week"])
        c["upcoming"] += int(n["sale_date"] >= today)
    doc = {"built_at": fetched_at, "today": today, "source": src.SOURCE, "source_name": src.SOURCE_NAME, "source_url": src.URL,
           "what_it_is": "Every Arkansas foreclosure sale currently noticed on an internet foreclosure sale notice service (Ark. Code § 18-50-105), joined to the county roll by address.",
           "what_it_is_not": "Not the opening bid, not the lender, not the recorded notice, and not an outcome. A sale can be postponed, cancelled or cured right up to the hour.",
           "count": len(notices), "joined": joined, "new_this_week": len(new_keys), "upcoming": sum(1 for n in notices if n["sale_date"] >= today),
           "counties": by_county, "notices": notices, "gone_recent": [g for g in hist["gone"] if g.get("last_seen", "") >= _week_ago(today)][:60]}
    n_ev = write_evidence(notices, [g for g in gone], today) if write else 0
    if write:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(doc, open(OUT, "w"), separators=(",", ":"))
        json.dump(hist, open(HIST, "w"), separators=(",", ":"))
    return {"count": len(notices), "joined": joined, "new": len(new_keys), "gone": len(gone), "evidence_rows": n_ev, "file": OUT}


def _week_ago(today: str) -> str:
    from datetime import date, timedelta
    y, m, d = (int(x) for x in today.split("-"))
    return (date(y, m, d) - timedelta(days=7)).isoformat()


if __name__ == "__main__":
    fx = sys.argv[sys.argv.index("--fixture") + 1] if "--fixture" in sys.argv else None
    print(json.dumps(build(fx), indent=1))
