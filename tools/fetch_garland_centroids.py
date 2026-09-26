"""Pull parcelid -> centroid for every Garland County parcel from the AR GIS
statewide parcel layer, so a coordinate-less county file (the assessor
namelist) can still be tested against the real Hot Springs Village and
Diamondhead polygons.

Without this the namelist imports with no lat/lon, and every text fallback
(city / subdivision / zip) misses the Village -- the exact failure that
published 462 Village lots as huntable in September.
"""
from __future__ import annotations
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hunter.http import arcgis_count, arcgis_query
from hunter.sources.ar_parcels import SERVICE, LAYER

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "garland_centroids.json")
WHERE = "countyfips='05051'"
PAGE = 200


def main() -> int:
    total = arcgis_count(SERVICE, LAYER, WHERE)
    print(f"Garland parcels in the State layer: {total:,}", flush=True)
    out: dict[str, list[float]] = {}
    if os.path.exists(OUT):
        out = json.load(open(OUT))
        print(f"resuming with {len(out):,} already cached", flush=True)
    offset = 0
    t0 = time.time()
    while offset < total:
        try:
            data = arcgis_query(SERVICE, LAYER, where=WHERE, out_fields="parcelid",
                                geometry=False, result_offset=offset,
                                result_record_count=PAGE,
                                extra={"returnCentroid": "true"}, timeout=60)
        except Exception as exc:
            print(f"  offset {offset}: {exc} -- retrying once", flush=True)
            time.sleep(3)
            try:
                data = arcgis_query(SERVICE, LAYER, where=WHERE, out_fields="parcelid",
                                    geometry=False, result_offset=offset,
                                    result_record_count=PAGE,
                                    extra={"returnCentroid": "true"}, timeout=60)
            except Exception as exc2:
                print(f"  offset {offset}: FAILED {exc2}", flush=True)
                offset += PAGE
                continue
        feats = data.get("features") or []
        if not feats:
            break
        for f in feats:
            pid = (f.get("attributes") or {}).get("parcelid")
            c = f.get("centroid") or {}
            if pid and c.get("x") is not None:
                out[str(pid).strip()] = [round(c["y"], 6), round(c["x"], 6)]
        offset += PAGE
        if offset % 4000 == 0:
            json.dump(out, open(OUT, "w"))
            rate = offset / max(time.time() - t0, 1)
            print(f"  {offset:,}/{total:,}  cached {len(out):,}  "
                  f"{rate:.0f}/s  eta {(total-offset)/max(rate,1)/60:.1f}m", flush=True)
    json.dump(out, open(OUT, "w"))
    print(f"DONE: {len(out):,} parcel centroids -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
