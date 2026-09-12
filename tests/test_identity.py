"""Property identity resolution (spec 54, 66)."""
from conftest import make_record
from hunter import db, identity, store
from hunter.normalize import normalize_address, normalize_parcel


def test_same_parcel_different_address_spelling_is_one_property():
    store.ingest(make_record(address="2748 Malvern Avenue"))
    store.ingest(make_record(address="2748  MALVERN AVE"))
    assert db.q1("SELECT COUNT(*) c FROM properties")["c"] == 1


def test_two_different_parcels_never_merge_even_when_adjacent():
    """Neighbouring lots share a centroid to within metres. They are still two."""
    store.ingest(make_record(parcel_id="300-00001-000", address="100 Main St",
                             lat=34.5100, lon=-93.0500))
    store.ingest(make_record(parcel_id="300-00002-000", address="100 Main St",
                             lat=34.51001, lon=-93.05001))
    assert db.q1("SELECT COUNT(*) c FROM properties")["c"] == 2


def test_reingesting_the_same_record_creates_nothing_new():
    rec = make_record()
    store.ingest(rec)
    ev_before = db.q1("SELECT COUNT(*) c FROM evidence")["c"]
    for _ in range(3):
        pid, action, changes = store.ingest(rec)
        assert action == "seen"
        assert changes == []
    assert db.q1("SELECT COUNT(*) c FROM properties")["c"] == 1
    assert db.q1("SELECT COUNT(*) c FROM evidence")["c"] == ev_before


def test_address_match_only_when_parcel_is_absent():
    store.ingest(make_record(parcel_id="300-00003-000", address="55 Elm St"))
    rec = make_record(parcel_id=None, address="55 ELM STREET", lat=None, lon=None,
                      legal=None, owner_name=None)
    pid, action, _ = store.ingest(rec)
    assert action != "created"
    assert db.q1("SELECT COUNT(*) c FROM properties")["c"] == 1


def test_malformed_and_missing_data_do_not_crash():
    for bad in (
        dict(parcel_id=None, address=None, lat=None, lon=None, legal=None),
        dict(parcel_id="", address="   ", acreage=-5, total_value=-100),
        dict(address="!!! ??? ###", parcel_id="///"),
        dict(acreage=1e9, imp_value=None, land_value=None, total_value=None),
    ):
        pid, action, _ = store.ingest(make_record(**bad))
        assert pid is not None


def test_aliases_are_recorded_for_every_identifier():
    pid, _, _ = store.ingest(make_record())
    kinds = {r["alias_type"] for r in
             db.q("SELECT alias_type FROM property_aliases WHERE property_id=?", (pid,))}
    assert {"parcel", "address", "coord", "owner", "legal"} <= kinds


def test_normalization_is_stable():
    assert normalize_address("2748 Malvern Avenue") == normalize_address("2748  MALVERN AVE")
    assert normalize_address("220 Elizabeth Terrace") == "220 ELIZABETH TER"
    assert normalize_parcel("100-04807-000") == normalize_parcel("10004807000")


def test_merge_duplicates_keeps_history():
    a, _, _ = store.ingest(make_record(parcel_id="300-00010-000", address="1 A St"))
    b, _, _ = store.ingest(make_record(parcel_id="300-00011-000", address="2 B St"))
    store.add_timeline(b, "note", "something happened")
    identity.merge_duplicates(a, b)
    assert db.q1("SELECT COUNT(*) c FROM properties")["c"] == 1
    assert db.q1("SELECT COUNT(*) c FROM timeline WHERE property_id=?", (a,))["c"] >= 1


# --- regressions from the City-registers scan (spec 54/55) -----------------

def test_a_register_polygon_next_door_never_merges_into_the_neighbour():
    """Register records carry no parcel id and their centroid can sit within
    metres of the neighbouring lot. The house number must keep them apart."""
    store.ingest(make_record(parcel_id="300-11", address="118 Magnolia St",
                             lat=34.5100, lon=-93.0500))
    rec = make_record(parcel_id=None, address="134 Magnolia", legal=None, owner_name=None,
                      lat=34.50012, lon=-93.05010, rpid="115601")
    pid, action, _ = store.ingest(rec)
    assert action == "created"
    assert db.q1("SELECT COUNT(*) c FROM properties")["c"] == 2
    assert db.q1("SELECT address FROM properties WHERE parcel_id='300-11'")["address"] == "118 Magnolia St"


def test_a_register_record_for_the_same_house_does_merge_and_keeps_the_fuller_address():
    store.ingest(make_record(parcel_id="300-12", address="1100 Park Ave",
                             lat=34.5200, lon=-93.0600))
    rec = make_record(parcel_id=None, address="1100 Park", legal=None, owner_name=None,
                      lat=34.52005, lon=-93.06004, rpid="50201")
    pid, action, changes = store.ingest(rec)
    assert action != "created"
    p = store.get_property(pid)
    assert p["address"] == "1100 Park Ave" and p["address_norm"] == "1100 PARK AVE"
    assert p["rpid"] == "50201"
    assert changes == []                                   # nothing worth alerting
    assert db.q1("SELECT COUNT(*) c FROM changes")["c"] == 0


def test_different_rpid_is_a_different_property():
    store.ingest(make_record(parcel_id=None, address="10 Elm St", rpid="1", legal=None,
                             owner_name=None, lat=34.53, lon=-93.07))
    pid, action, _ = store.ingest(make_record(parcel_id=None, address="10 Elm St", rpid="2",
                                              legal=None, owner_name=None,
                                              lat=34.53001, lon=-93.07001))
    assert action == "created"


def test_a_real_relocation_is_still_a_change():
    store.ingest(make_record(parcel_id="300-13", lat=34.5100, lon=-93.0500))
    pid, action, changes = store.ingest(make_record(parcel_id="300-13", lat=34.5200, lon=-93.0500))
    assert {c["field"] for c in changes} == {"lat"}


def test_rpid_alias_hit_with_a_conflicting_house_number_does_not_merge():
    """Regression: the guard was defined below the RPID branch that used it."""
    store.ingest(make_record(parcel_id=None, address="20 Oak St", rpid="77", legal=None,
                             owner_name=None, lat=34.54, lon=-93.08))
    # same RPID reused by a source for a different house number - a data error
    # upstream, but it must not fold two houses together
    pid, action, _ = store.ingest(make_record(parcel_id=None, address="22 Oak St", rpid="77",
                                              legal=None, owner_name=None,
                                              lat=34.54001, lon=-93.08001))
    assert action == "created"


def test_more_specific_address_is_taken_quietly_and_units_do_not_flip_flop():
    pid, _, _ = store.ingest(make_record(parcel_id=None, address="1100 Park", rpid="9", legal=None,
                                         owner_name=None, lat=34.55, lon=-93.09))
    _, _, changes = store.ingest(make_record(parcel_id=None, address="1100 Park Ave", rpid="9",
                                             legal=None, owner_name=None, lat=34.55, lon=-93.09))
    assert store.get_property(pid)["address"] == "1100 Park Ave" and changes == []
    for unit in ("1100 Park Ave Apt 3", "1100 Park Ave Apt 7"):
        _, _, changes = store.ingest(make_record(parcel_id=None, address=unit, rpid="9",
                                                 legal=None, owner_name=None, lat=34.55, lon=-93.09))
        assert changes == []
    assert store.get_property(pid)["address"] == "1100 Park Ave"
    assert db.q1("SELECT COUNT(*) c FROM changes WHERE field='address'")["c"] == 0


def test_hash_unit_tokens_are_units_and_case_points_never_move_a_parcel():
    from hunter.normalize import normalize_address
    assert normalize_address("121 Ward St, #6") == normalize_address("121 Ward St, #14") == "121 WARD ST"
    pid, _, _ = store.ingest(make_record(parcel_id="300-14", address="121 Ward St",
                                         lat=34.4744, lon=-93.0492))
    for unit, lat, lon in (("121 Ward St, #6", 34.4749, -93.0505), ("121 Ward St, #14", 34.4744, -93.0492)):
        _, action, changes = store.ingest(make_record(parcel_id=None, address=unit, legal=None,
                                                      owner_name=None, lat=lat, lon=lon))
        assert action != "created" and changes == []
    p = store.get_property(pid)
    assert (p["lat"], p["lon"]) == (34.4744, -93.0492) and p["address"] == "121 Ward St"
    assert db.q1("SELECT COUNT(*) c FROM changes")["c"] == 0
