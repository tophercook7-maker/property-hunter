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
