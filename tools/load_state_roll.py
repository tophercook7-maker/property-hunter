"""Load the cached statewide parcel roll into the database, county by county.

    python3 tools/load_state_roll.py --only 05001     # one county
    python3 tools/load_state_roll.py                  # every cached county
    python3 tools/load_state_roll.py --status

Reads data/roll/<fips>.jsonl written by tools/fetch_state_roll.py. Nothing here
touches the network.

WHY THIS EXISTS RATHER THAN store.ingest()
ingest() resolves identity against every existing row, which is what you want
when a second source describes a property you already hold. Loading a county's
first copy of the State roll is the other case: the key is deterministic and
county-scoped (parcel:<fips>:<parcel>), so there is nothing to resolve. The
namelist went through ingest() at 15 parcels/sec; at that rate the remaining
1.9 million parcels is 36 hours. New rows are inserted in batches here, and
anything whose key already exists is handed to ingest() so change detection,
evidence precedence and conflict labelling all still apply to it.

TWO RULES THIS MUST NOT BREAK
1. A parcel with no centroid is not loaded. Hot Springs Village is caught by
   polygon and by nothing else, and the roll's own city and subdivision fields
   miss it. Guessing is the bug that published 462 Village lots as huntable.
2. Parcel numbers are COUNTY-LOCAL. 001-03774-000 exists in Saline and in
   Grant. The canonical key carries the county fips; never key on parcel alone.
"""
from __future__ import annotations

import argparse, json, os, shutil, sys, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hunter import db, exclusions, store                     # noqa: E402
from hunter.config import TERRITORIES                        # noqa: E402
from hunter.normalize import normalize_address, normalize_owner, title_case  # noqa: E402
from hunter.identity import canonical_key                    # noqa: E402
from hunter.sources.base import Record                       # noqa: E402

ROLL = os.path.join(ROOT, "data", "roll")
SOURCE = "ar_gis_parcels"
BATCH = 4000
DISK_FLOOR_GB = 30


def free_gb() -> float:
    return shutil.disk_usage(ROOT).free / 1e9


def ms_date(v):
    try:
        return datetime.fromtimestamp(float(v) / 1000, timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def num(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def clean(v):
    s = str(v).strip() if v is not None else ""
    return None if s in ("", "None", "0", "--") else s


def fields_from(a: dict, terr: dict) -> dict:
    addr = clean(a.get("adrlabel")) or " ".join(
        x for x in (clean(a.get("adrnum")), clean(a.get("predir")), clean(a.get("pstrnam")),
                    clean(a.get("pstrtype")), clean(a.get("psufdir"))) if x) or None
    imp = num(a.get("impvalue")) or 0.0
    return {
        "county_fips": terr["county_fips"], "territory": terr["key"], "state": "AR",
        "parcel_id": clean(a.get("parcelid")),
        # camakey arrives as a float string ("3536.0"); it is the RPID other
        # sources join on, so it has to be the integer they use.
        "rpid": (lambda v: None if v is None else (str(int(v)) if float(v).is_integer() else str(v)))(num(a.get("camakey"))),
        "address": title_case(addr) if addr else None,
        "city": title_case(clean(a.get("adrcity"))) if clean(a.get("adrcity")) else None,
        "zip": clean(a.get("adrzip5")),
        "lat": a.get("_lat"), "lon": a.get("_lon"),
        "owner_name": clean(a.get("ownername")),
        "legal": clean(a.get("parcellgl")),
        "subdivision": clean(a.get("subdivision")),
        "acreage": num(a.get("taxarea")),
        "parcel_type": clean(a.get("parceltype")),
        "land_value": num(a.get("landvalue")),
        "imp_value": imp,
        "total_value": num(a.get("totalvalue")),
        "improved": 1 if imp > 500 else 0,
        "property_type": "lot" if imp < 500 else "improved",
        "data_class": "real",
    }


EV = (("owner_name", "owner_name", "Owner of record on the State's republished county roll. Not clear title."),
      ("total_value", "total_value", "County APPRAISED value. Not market value and not a sale price."),
      ("land_value", "land_value", None),
      ("imp_value", "imp_value", None))


def evidence_rows(pid: int, f: dict, a: dict, now: str) -> list[tuple]:
    out = []
    def add(field, value, note=None, etype="FACT", conf="HIGH"):
        if value in (None, ""):
            return
        out.append((pid, field, str(value), etype, conf, SOURCE, "Arkansas GIS Office statewide parcel layer",
                    None, now, ms_date(a.get("camadate")), None, now, "AUTOMATED_SOURCE"))
    for col, field, note in EV:
        v = f.get(col)
        if col.endswith("_value") and v is not None:
            add(field, f"${v:,.0f}", note)
        else:
            add(field, v, note)
    if clean(a.get("sourceref")):
        add("deed_reference", clean(a.get("sourceref")),
            "Deed reference carried on the county roll: a pointer to the recorded instrument, not the instrument.")
    return out


PCOLS = ("canonical_key", "data_class", "territory", "county_fips", "parcel_id", "rpid", "address",
         "address_norm", "city", "zip", "lat", "lon", "subdivision", "legal", "acreage", "owner_name",
         "owner_norm", "parcel_type", "property_type", "improved", "land_value", "imp_value",
         "total_value", "state", "excluded", "exclusion_reason", "first_seen", "last_seen",
         "created_at", "updated_at")
ECOLS = ("property_id", "field", "value", "evidence_type", "confidence", "source", "source_name",
         "source_url", "retrieved_at", "effective_date", "raw_ref", "created_at", "origin")


def load_county(terr: dict, limit: int = 0) -> dict:
    path = os.path.join(ROLL, f"{terr['county_fips']}.jsonl")
    if not os.path.exists(path):
        return {"county": terr["county"], "status": "not cached"}
    have = {r["canonical_key"] for r in db.q(
        "SELECT canonical_key FROM properties WHERE county_fips=?", (terr["county_fips"],))}
    st = {"county": terr["county"], "read": 0, "new": 0, "existing": 0,
          "no_coord": 0, "excluded": 0, "status": "ok"}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pend_p, pend_a, existing_recs = [], [], []
    t0 = time.time()

    def flush():
        if not pend_p:
            return
        con = db.connect() if hasattr(db, "connect") else None
        ids = []
        for row in pend_p:
            cur = db.ex(f"INSERT OR IGNORE INTO properties({','.join(PCOLS)}) "
                        f"VALUES({','.join('?' * len(PCOLS))})", row)
            ids.append(cur.lastrowid)
        for pid, a, f in zip(ids, pend_a, pend_f):
            if not pid:
                continue
            for e in evidence_rows(pid, f, a, now):
                db.ex(f"INSERT INTO evidence({','.join(ECOLS)}) VALUES({','.join('?' * len(ECOLS))})", e)
        pend_p.clear(); pend_a.clear(); pend_f.clear()

    pend_f = []
    with open(path) as fh:
        for line in fh:
            if limit and st["read"] >= limit:
                break
            st["read"] += 1
            a = json.loads(line)
            if a.get("_lat") is None:
                st["no_coord"] += 1
                continue
            f = fields_from(a, terr)
            key = canonical_key(f)
            if not key:
                continue
            if key in have:
                st["existing"] += 1
                existing_recs.append((f, a))
                continue
            have.add(key)
            v = exclusions.check(lat=f["lat"], lon=f["lon"], subdivision=f.get("subdivision"),
                                 address=f.get("address"), city=f.get("city"))
            if v.excluded:
                st["excluded"] += 1
            pend_p.append((key, "real", f["territory"], f["county_fips"], f["parcel_id"], f["rpid"],
                           f["address"], normalize_address(f["address"]) if f["address"] else None,
                           f["city"], f["zip"], f["lat"], f["lon"], f["subdivision"], f["legal"],
                           f["acreage"], f["owner_name"],
                           normalize_owner(f["owner_name"]) if f["owner_name"] else None,
                           f["parcel_type"], f["property_type"], f["improved"], f["land_value"],
                           f["imp_value"], f["total_value"], "AR",
                           1 if v.excluded else 0,
                           f"{v.label}: {v.reason}" if v.excluded else None,
                           now, now, now, now))
            pend_a.append(a); pend_f.append(f)
            st["new"] += 1
            if len(pend_p) >= BATCH:
                flush()
                if free_gb() < DISK_FLOOR_GB:
                    st["status"] = "stopped: disk floor"
                    return st
    flush()
    # Anything already on file goes the honest way: ingest() so change detection,
    # evidence precedence and the SOURCES DISAGREE labelling all still apply.
    for f, a in existing_recs[:20000]:
        store.ingest(Record(source=SOURCE, identity={"parcel_id": f["parcel_id"],
                                                     "county_fips": f["county_fips"]},
                            fields=f, evidence=[], raw={}), data_class="real")
    st["secs"] = round(time.time() - t0, 1)
    st["rate"] = round(st["read"] / max(st["secs"], 1))
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    db.init_db()
    exclusions.refresh_cache()
    boundaries = {b["key"] for b in exclusions._boundaries()}
    if not {"hot_springs_village", "diamondhead"} <= boundaries:
        raise SystemExit("refusing to load: exclusion polygons are not cached; the Village would load as huntable")

    terrs = sorted(TERRITORIES, key=lambda t: t["county"])
    if a.only:
        want = {x.strip() for x in a.only.split(",")}
        terrs = [t for t in terrs if t["county_fips"] in want]

    if a.status:
        for t in terrs:
            n = db.q("SELECT COUNT(*) c FROM properties WHERE county_fips=?", (t["county_fips"],))[0]["c"]
            cached = os.path.join(ROLL, f"{t['county_fips']}.jsonl")
            have = sum(1 for _ in open(cached)) if os.path.exists(cached) else 0
            if have or n:
                print(f"  {t['county_fips']}  {t['county']:16s} db {n:8,}  cached {have:8,}")
        return 0

    for i, t in enumerate(terrs, 1):
        if free_gb() < DISK_FLOOR_GB:
            print(f"STOPPING: {free_gb():.0f} GB free is under the {DISK_FLOOR_GB} GB floor", flush=True)
            break
        r = load_county(t, a.limit)
        print(f"[{i}/{len(terrs)}] {r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
