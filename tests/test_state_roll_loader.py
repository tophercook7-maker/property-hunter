"""The statewide roll loader must not undo the two lessons that cost the most.

1. Hot Springs Village is caught by polygon and by nothing else. The roll gives
   ordinary city names and Spanish subdivision names, so every text fallback
   misses. A parcel with no centroid cannot be placed and must not be loaded.
2. Parcel numbers are COUNTY-LOCAL. 001-03774-000 exists in Saline and in Grant.
   The key must carry the county or the two counties merge.
"""
from __future__ import annotations

import pytest

from hunter.identity import canonical_key
import tools.load_state_roll as L


TERR = {"key": "garland_ar", "county_fips": "05051", "county": "Garland"}
SALINE = {"key": "saline_ar", "county_fips": "05125", "county": "Saline"}


def _attrs(**kw):
    a = {"parcelid": "200-17600-055-000", "ownername": "SOMEBODY", "adrlabel": "223 MAZARRON DR",
         "adrcity": "HOT SPRINGS", "totalvalue": "1000.0", "landvalue": "1000.0", "impvalue": "0.0",
         "subdivision": "ESTRELLA", "camakey": "70841.0", "_lat": 34.6871, "_lon": -92.9773}
    a.update(kw)
    return a


def test_parcel_key_is_county_local():
    a = _attrs(parcelid="001-03774-000")
    k1 = canonical_key(L.fields_from(a, SALINE))
    k2 = canonical_key(L.fields_from(a, {"key": "grant_ar", "county_fips": "05053", "county": "Grant"}))
    assert k1 != k2, "the same parcel number in two counties must not share a key"
    assert "05125" in k1 and "05053" in k2


def test_camakey_becomes_an_integer_rpid():
    assert L.fields_from(_attrs(camakey="70841.0"), TERR)["rpid"] == "70841"
    assert L.fields_from(_attrs(camakey=None), TERR)["rpid"] is None


def _in_rings(lon, lat, rings):
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            xi, yi = ring[i]
            xj, yj = ring[i - 1]
            if ((yi > lat) != (yj > lat)) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
    return inside


def test_a_village_parcel_is_inside_the_published_polygon():
    """Against docs/data/exclusions.json, which ships with the site, so this runs
    everywhere rather than skipping wherever the database is a fresh temp file."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / "docs" / "data" / "exclusions.json"
    ex = json.loads(f.read_text())
    assert {"hot_springs_village", "diamondhead"} <= set(ex), "the site ships both polygons"
    a = _attrs()                                   # 223 Mazarron Dr, subdivision ESTRELLA
    assert _in_rings(a["_lon"], a["_lat"], ex["hot_springs_village"]["rings"]), \
        "a real Village parcel must fall inside the Village polygon"
    hs = (34.5037, -93.0552)                       # downtown Hot Springs, a mile outside
    assert not _in_rings(hs[1], hs[0], ex["hot_springs_village"]["rings"])


def test_the_village_is_not_catchable_by_name(monkeypatch):
    """The whole reason coordinates are mandatory: the county writes Village
    parcels with an ordinary city and a Spanish subdivision name. If this ever
    starts passing on text alone, the coordinate requirement looks optional and
    the next bulk import quietly loads 20,000 Village parcels."""
    from hunter import exclusions
    f = L.fields_from(_attrs(), TERR)
    v = exclusions.check(lat=None, lon=None, subdivision=f["subdivision"],
                         address=f["address"], city=f["city"])
    assert not v.excluded


def test_values_and_improved_flag():
    f = L.fields_from(_attrs(impvalue="12000.0", totalvalue="52000.0"), TERR)
    assert f["imp_value"] == 12000.0 and f["total_value"] == 52000.0
    assert f["improved"] == 1 and f["property_type"] == "improved"
    g = L.fields_from(_attrs(impvalue="0.0"), TERR)
    assert g["improved"] == 0 and g["property_type"] == "lot"


def test_placeholder_strings_do_not_become_values():
    f = L.fields_from(_attrs(adrcity="None", subdivision="--", adrzip5="0"), TERR)
    assert f["city"] is None and f["subdivision"] is None and f["zip"] is None
