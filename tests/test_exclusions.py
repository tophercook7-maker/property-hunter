"""The exclusion layer is the one rule that must never fail (spec 17, 66)."""
import pytest

from conftest import make_record
from hunter import exclusions, store


def test_hsv_centroid_is_excluded_by_polygon(boundaries):
    v = exclusions.check(lat=34.657, lon=-92.97)
    assert v.excluded and v.signal == "polygon"
    assert "Hot Springs Village" in v.label


def test_diamondhead_polygon_excludes_interior_points(boundaries):
    from hunter import geo
    rings = boundaries["diamondhead"]["rings"]
    inside = [p for p in _grid(boundaries["diamondhead"]["bbox"])
              if geo.point_in_rings(p[0], p[1], rings)]
    assert inside, "fixture polygon should contain some points"
    for lon, lat in inside[:25]:
        v = exclusions.check(lat=lat, lon=lon)
        assert v.excluded, f"{lat},{lon} is inside Diamondhead but was not excluded"


def _grid(bbox, n=30):
    w, s, e, nn = bbox
    return [(w + (e - w) * i / (n - 1), s + (nn - s) * j / (n - 1))
            for i in range(n) for j in range(n)]


def test_downtown_hot_springs_is_not_excluded(boundaries):
    assert not exclusions.check(lat=34.5133, lon=-93.0538).excluded


@pytest.mark.parametrize("subdivision", [
    "BLACKSNAKE VILLAGE ESTS", "OAKLAWN VILLAGE HPR", "WESTWOOD VILLAGE",
    "LAKE HAMILTON VILLAGE", "DIAMOND SPRINGS ESTATES HPR", "VILLAGE SOUTH II",
])
def test_similar_names_are_not_excluded(subdivision, boundaries):
    """A bare substring match on 'village' or 'diamond' would wrongly kill these."""
    v = exclusions.check(subdivision=subdivision, lat=34.5133, lon=-93.0538)
    assert not v.excluded, f"{subdivision} must not be excluded ({v.reason})"


@pytest.mark.parametrize("subdivision", [
    "DIAMONDHEAD A", "DIAMONDHEAD L1", "DIAMONDHEAD MANOR", "diamondhead q",
])
def test_real_diamondhead_subdivisions_are_excluded(subdivision, boundaries):
    assert exclusions.check(subdivision=subdivision).excluded


def test_city_and_zip_signals(boundaries):
    assert exclusions.check(city="Hot Springs Village").excluded
    assert exclusions.check(zip_code="71909").excluded
    assert not exclusions.check(city="Hot Springs").excluded
    assert not exclusions.check(zip_code="71901").excluded


def test_excluded_property_is_flagged_on_write(boundaries):
    """Exclusion happens at the data layer, not in the UI."""
    rec = make_record(lat=34.657, lon=-92.97, parcel_id="999-00001-000",
                      address="1 Balboa Way", city="Hot Springs Village")
    pid, action, _ = store.ingest(rec)
    p = store.get_property(pid)
    assert p["excluded"] == 1
    assert "Hot Springs Village" in p["exclusion_reason"]


def test_excluded_property_never_appears_in_api_results(boundaries):
    from hunter.api import api_map, api_properties
    store.ingest(make_record(lat=34.657, lon=-92.97, parcel_id="999-00002-000",
                             address="2 Balboa Way"))
    store.ingest(make_record())          # a normal Hot Springs property
    ids = {p["id"] for p in api_properties(limit=500)["properties"]}
    map_ids = {p["id"] for p in api_map()["properties"]}
    excluded = {r["id"] for r in
                __import__("hunter.db", fromlist=["db"]).q(
                    "SELECT id FROM properties WHERE excluded=1")}
    assert excluded, "test setup should have produced an excluded property"
    assert not (ids & excluded)
    assert not (map_ids & excluded)


def test_toggling_a_rule_off_is_respected(boundaries):
    from hunter import db
    from hunter.config import EXCLUSIONS
    exclusions.sync_rules_to_db()
    assert exclusions.check(lat=34.657, lon=-92.97).excluded
    db.ex("UPDATE exclusion_rules SET active=0 WHERE key='hot_springs_village'")
    # active_rules() reads the DB, so the config rule drops out
    keys = {r["key"] for r in exclusions.active_rules()}
    assert "hot_springs_village" not in keys
    db.ex("UPDATE exclusion_rules SET active=1 WHERE key='hot_springs_village'")
