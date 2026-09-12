"""Live checks against the real public services.

Marked `live` so the main suite stays offline-safe:
    pytest -m live          run only these
    pytest -m "not live"    skip them
These exist because the honest claim "this reads real Garland County data" has
to be verifiable, not asserted.
"""
import pytest

pytestmark = pytest.mark.live

GARLAND = "05051"


def test_arkansas_parcel_service_is_answering():
    from hunter.sources.ar_parcels import ARKANSAS_PARCELS
    r = ARKANSAS_PARCELS.health_check()
    assert r.status == "ok", r.error
    assert "parcels published" in r.detail


def test_real_parcels_come_back_with_real_fields():
    from hunter.sources.ar_parcels import ARKANSAS_PARCELS
    res = ARKANSAS_PARCELS.discover(limit=5)
    assert res.status == "ok"
    assert len(res.records) == 5
    for rec in res.records:
        f = rec.fields
        assert f["parcel_id"] and f["county_fips"] == GARLAND
        assert f["lat"] and f["lon"]
        assert 34.0 < f["lat"] < 35.0 and -94.0 < f["lon"] < -92.0
        assert rec.evidence
        assert all(e["source"] == "ar_gis_parcels" for e in rec.evidence)


def test_census_boundaries_download_and_contain_their_own_centroid():
    from hunter import geo
    from hunter.sources.boundaries import CENSUS_BOUNDARIES
    for layer, geoid, name in ((5, "0533482", "Hot Springs Village"),
                               (4, "0518880", "Diamondhead")):
        b = CENSUS_BOUNDARIES.fetch_boundary(layer, geoid, name)
        assert b, f"no boundary returned for {name}"
        c = geo.centroid(b["rings"])
        assert geo.point_in_rings(c[0], c[1], b["rings"])


def test_real_diamondhead_parcels_are_actually_excluded():
    """The adversarial case: pull real parcels from inside the excluded area."""
    from hunter import exclusions
    from hunter.sources.ar_parcels import ARKANSAS_PARCELS
    from hunter.sources.boundaries import CENSUS_BOUNDARIES
    CENSUS_BOUNDARIES.discover()
    exclusions.refresh_cache()
    res = ARKANSAS_PARCELS.discover(
        where_extra="UPPER(subdivision) LIKE 'DIAMONDHEAD %'", limit=25)
    assert res.records, "expected real Diamondhead parcels"
    for rec in res.records:
        v = exclusions.check_property(rec.fields)
        assert v.excluded, (f"{rec.fields.get('address')} "
                            f"({rec.fields.get('subdivision')}) leaked through")


def test_fema_flood_answers_for_a_real_point():
    from hunter.sources.flood import FEMA_FLOOD
    assert FEMA_FLOOD.health_check().status == "ok"
    attrs = FEMA_FLOOD.zone_at(34.5133, -93.0538)
    assert attrs and attrs.get("FLD_ZONE")


def test_building_footprints_answer_for_downtown_hot_springs():
    from hunter.sources.context import AR_FOOTPRINTS
    res = AR_FOOTPRINTS.enrich({"id": 1, "lat": 34.5133, "lon": -93.0538})
    assert res.status == "ok"
    assert res.records


@pytest.mark.slow
def test_openstreetmap_reports_nearby_businesses_and_not_roads():
    """OSM's job is who is nearby - competition for a food stand, and the anchors
    that generate traffic. Road access moved to the 911 centerline file, so this
    adapter must no longer claim to answer it."""
    from hunter.sources.context import OSM_CONTEXT
    res = OSM_CONTEXT.enrich({"id": 1, "lat": 34.5133, "lon": -93.0538})
    assert res.status == "ok", res.detail
    raw = res.records[0].raw
    assert set(raw) == {"food", "shops", "anchors"}
    assert "road_rank" not in raw and "road_class" not in raw
    # downtown Hot Springs genuinely has mapped food businesses
    assert raw["food"], "expected mapped food businesses on Central Ave"
    fields = {e["field"] for e in res.records[0].evidence}
    assert not (fields & {"road_access", "legal_access"}), \
        f"OSM should no longer write road evidence, wrote {fields}"
    assert res.records[0].fields == {}


def test_road_centerlines_classify_a_known_highway_and_a_lake():
    """Central Ave in Hot Springs is also AR 7. The middle of Lake Ouachita is not
    a road. If either of those comes back wrong, access scoring is wrong."""
    from hunter.sources.roads import AR_ROADS
    assert AR_ROADS.health_check().status == "ok"

    downtown = AR_ROADS.enrich({"id": 1, "lat": 34.5133, "lon": -93.0538})
    assert downtown.status == "ok"
    assert downtown.records[0].raw["road_rank"] >= 7, downtown.detail

    lake = AR_ROADS.enrich({"id": 2, "lat": 34.5700, "lon": -93.4000})
    assert lake.status == "ok"
    assert lake.records[0].raw["road_rank"] == 0
    fields = {e["field"] for e in lake.records[0].evidence}
    assert "legal_access" in fields
    note = next(e for e in lake.records[0].evidence if e["field"] == "legal_access")
    assert "not proof" in note["raw_ref"]


def test_road_lookup_is_fast_enough_to_scan_with():
    """Overpass takes ~45 s a call; this has to be two orders faster or the
    scanner stalls. If this regresses, the scan experience regresses."""
    import time
    from hunter.sources.roads import AR_ROADS
    t = time.monotonic()
    AR_ROADS.enrich({"id": 1, "lat": 34.5133, "lon": -93.0538})
    assert time.monotonic() - t < 5.0


def test_blocked_source_is_still_blocked_and_we_still_do_not_bypass_it():
    from hunter.sources import get_source
    r = get_source("garland_assessor").health_check()
    assert r.status == "manual"
    assert "not bypassed" in r.detail.lower() or "not machine readable" in r.detail.lower()


def test_seed_addresses_resolve_against_the_live_parcel_layer():
    from hunter.http import arcgis_count
    from hunter.seeds import SEED_ADDRESSES, seed_where
    from hunter.sources.ar_parcels import SERVICE, LAYER
    n = arcgis_count(SERVICE, LAYER, f"countyfips='{GARLAND}' AND ({seed_where()})")
    assert n > 0, "none of the investigation seeds matched a real parcel"
    assert n <= len(SEED_ADDRESSES) * 4


def test_state_imagery_exports_a_real_jpeg_and_terrain_reads_elevation(tmp_path, monkeypatch):
    """The dossier hero image and the slope score both rest on these two services."""
    from conftest import make_record
    from hunter import db, store
    from hunter.sources import imagery as im
    monkeypatch.setattr(im, "FILES_DIR", tmp_path)
    db.init_db()
    pid, _, _ = store.ingest(make_record(parcel_id="300-06186-000", lat=34.498931,
                                         lon=-93.024005, acreage=2.154))
    prop = store.get_property(pid)
    res = im.AR_IMAGERY.enrich(prop, force=True)
    assert res.status == "ok", res.error
    assert sorted(res.records[0].raw["saved"]) == ["2017", "2023"]
    files = list(tmp_path.rglob("*.jpg"))
    assert len(files) == 2 and all(f.read_bytes()[:3] == b"\xff\xd8\xff" for f in files)
    assert all(f.stat().st_size > 20_000 for f in files), "suspiciously small aerial"

    terr = im.AR_TERRAIN.enrich(prop)
    assert terr.status == "ok", terr.error
    raw = terr.records[0].raw
    assert 0 <= raw["slope_pct"] < 60 and 100 < raw["elevation_m"] < 400


def test_city_of_hot_springs_gis_answers_and_knows_111_isabelle():
    """The City registers are real and 111 Isabelle St is inside the zoning map."""
    from hunter.sources import hot_springs as hs
    for src, low in ((hs.HS_VACANT, 100), (hs.HS_LIENS, 100), (hs.HS_CODE, 50),
                     (hs.HS_ZONING, 10000)):
        h = src.health_check()
        assert h.status == "ok", h.error
        n = int(h.detail.split(" ")[0].replace(",", ""))
        assert n >= low, f"{src.name}: only {n} records"
    p = {"id": 1, "lat": 34.498931, "lon": -93.024005, "address_norm": "111 ISABELLE ST",
         "rpid": None}
    z = hs.HS_ZONING.enrich(p)
    assert z.status == "ok" and z.records[0].fields.get("zoning", "").startswith("R-R")
    assert z.records[0].fields.get("rpid") == "51362"
    u = hs.HS_UTILITIES.enrich(p)
    assert u.status == "ok"
    assert any(e["field"] == "city_water" and "at this address" in e["value"]
               for e in u.records[0].evidence)


def test_a_second_county_is_configuration_not_code():
    """Spec 71/72: adding Saline County is a dict. The same parcel adapter must
    read it, and the HSV exclusion polygon (which straddles the county line)
    must still bite there."""
    from hunter import exclusions
    from hunter.config import TERRITORIES
    from hunter.sources.ar_parcels import ARKANSAS_PARCELS
    saline = next(t for t in TERRITORIES if t["key"] == "saline_ar")
    assert saline["active"] is False
    n = ARKANSAS_PARCELS.count(f"countyfips='{saline['county_fips']}'")
    assert n > 20000, f"Saline County should have tens of thousands of parcels, got {n}"
    res = ARKANSAS_PARCELS.discover(territory="saline_ar", limit=3)
    assert res.status == "ok" and len(res.records) == 3
    assert all(r.fields["territory"] == "saline_ar" and r.fields["county_fips"] == "05125"
               for r in res.records)
    # the HSV polygon extends into Saline County: an HSV point there is still excluded
    from hunter.sources.boundaries import CENSUS_BOUNDARIES
    CENSUS_BOUNDARIES.discover(); exclusions.refresh_cache()
    assert exclusions.check(lat=34.68, lon=-92.90).excluded
