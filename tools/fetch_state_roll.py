"""Cache the Arkansas statewide parcel roll to disk, county by county.

    python3 tools/fetch_state_roll.py                 # every county, smallest first
    python3 tools/fetch_state_roll.py --only 05119    # one county
    python3 tools/fetch_state_roll.py --status        # what is cached

Writes data/roll/<fips>.jsonl, one parcel per line, with the centroid. Resumable:
a county already complete is skipped, and a partial county restarts from its own
line count. Nothing touches the database -- this is the slow network half, kept
separate so the load can be re-run without re-fetching 2.1 million parcels.

Centroids are not optional. Hot Springs Village and Diamondhead are excluded by
polygon and by nothing else, and the roll's own city and subdivision fields miss
them; a parcel cached without a coordinate cannot be placed and is not loaded.

Stops if free disk falls under the floor. A full cache is roughly 2 GB.
"""
from __future__ import annotations

import argparse, json, os, shutil, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hunter.http import arcgis_count, arcgis_query          # noqa: E402
from hunter.sources.ar_parcels import SERVICE, LAYER, FIELDS, PAGE  # noqa: E402
from hunter.config import TERRITORIES                        # noqa: E402

OUT = os.path.join(ROOT, "data", "roll")
DISK_FLOOR_GB = 40


def free_gb() -> float:
    return shutil.disk_usage(ROOT).free / 1e9


def counted(fips: str) -> int:
    return arcgis_count(SERVICE, LAYER, f"countyfips='{fips}'")


def cached(fips: str) -> int:
    p = os.path.join(OUT, f"{fips}.jsonl")
    if not os.path.exists(p):
        return 0
    with open(p) as f:
        return sum(1 for _ in f)


def fetch_county(fips: str, county: str) -> dict:
    total = counted(fips)
    have = cached(fips)
    if have >= total and total:
        return {"fips": fips, "county": county, "total": total, "cached": have, "status": "complete"}
    path = os.path.join(OUT, f"{fips}.jsonl")
    mode = "a" if have else "w"
    offset = have
    t0, wrote, nocoord = time.time(), 0, 0
    with open(path, mode) as fh:
        while offset < total:
            if free_gb() < DISK_FLOOR_GB:
                fh.flush()
                return {"fips": fips, "county": county, "total": total,
                        "cached": offset, "status": "stopped: disk floor"}
            try:
                d = arcgis_query(SERVICE, LAYER, where=f"countyfips='{fips}'", out_fields=FIELDS,
                                 geometry=False, result_offset=offset, result_record_count=PAGE,
                                 extra={"returnCentroid": "true"}, timeout=90)
            except Exception as exc:
                time.sleep(4)
                try:
                    d = arcgis_query(SERVICE, LAYER, where=f"countyfips='{fips}'", out_fields=FIELDS,
                                     geometry=False, result_offset=offset, result_record_count=PAGE,
                                     extra={"returnCentroid": "true"}, timeout=90)
                except Exception as exc2:
                    print(f"    {fips} offset {offset}: FAILED {str(exc2)[:70]}", flush=True)
                    offset += PAGE
                    continue
            feats = d.get("features") or []
            if not feats:
                break
            for f in feats:
                a = f.get("attributes") or {}
                c = f.get("centroid") or {}
                if c.get("x") is None:
                    nocoord += 1
                a["_lat"] = round(c["y"], 6) if c.get("y") is not None else None
                a["_lon"] = round(c["x"], 6) if c.get("x") is not None else None
                fh.write(json.dumps(a, separators=(",", ":")) + "\n")
                wrote += 1
            offset += PAGE
            if wrote and wrote % 10000 == 0:
                fh.flush()
                r = wrote / max(time.time() - t0, 1)
                print(f"    {county} {offset:,}/{total:,}  {r:.0f}/s", flush=True)
    return {"fips": fips, "county": county, "total": total, "cached": cached(fips),
            "no_coord": nocoord, "status": "complete" if cached(fips) >= total else "partial"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="comma-separated county FIPS")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    terrs = sorted(TERRITORIES, key=lambda t: t["county"])
    if a.only:
        want = {x.strip() for x in a.only.split(",")}
        terrs = [t for t in terrs if t["county_fips"] in want]

    if a.status:
        tot = done = 0
        for t in terrs:
            have = cached(t["county_fips"])
            tot += have
            if have:
                done += 1
                print(f"  {t['county_fips']}  {t['county']:16s} {have:8,}")
        print(f"\n  {done} counties cached, {tot:,} parcels on disk, {free_gb():.0f} GB free")
        return 0

    print(f"free disk {free_gb():.0f} GB (floor {DISK_FLOOR_GB} GB)", flush=True)
    grand = 0
    for i, t in enumerate(terrs, 1):
        if free_gb() < DISK_FLOOR_GB:
            print(f"STOPPING: free disk {free_gb():.0f} GB is under the {DISK_FLOOR_GB} GB floor", flush=True)
            break
        r = fetch_county(t["county_fips"], t["county"])
        grand += r["cached"]
        print(f"[{i}/{len(terrs)}] {r['county']:16s} {r['cached']:8,}/{r['total']:,}  {r['status']}", flush=True)
    print(f"\ncached {grand:,} parcels across {len(terrs)} counties · {free_gb():.0f} GB free", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
