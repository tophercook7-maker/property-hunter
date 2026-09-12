"""City of Hot Springs GIS adapters, the signals they feed, zoning-aware scoring,
and fuzzy search (spec 16, 18, 19, 21, 56)."""
import pytest
from fastapi.testclient import TestClient

from conftest import make_record
from hunter import db, distress, scoring, store
from hunter.sources import hot_springs as hs


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def prop(boundaries):
    pid, _, _ = store.ingest(make_record())
    return store.get_property(pid)


def _fake_query(table: dict):
    """table: {(svc, lid): [features]} -> a _query replacement."""
    def q(svc, lid, **kw):
        return table.get((svc, lid), [])
    return q


VACANT = {"attributes": {"RPID": 51362, "Street_Number": "111", "Street_Name": "Isabelle",
                         "created_date": 1732114550949, "last_edited_date": 1732114550949},
          "geometry": {"rings": [[[-93.0245, 34.4985], [-93.0245, 34.4993], [-93.0235, 34.4993],
                                  [-93.0235, 34.4985], [-93.0245, 34.4985]]]}}
LIEN = {"attributes": {"Date_of_Lien": 1688256000000, "RPID": 51362, "Address": "111 Isabelle St",
                       "Amount": 4102.4, "Type_of_Lien": "DEMO", "Zoning": "R3", "Water": '6"',
                       "Sewer": "NO", "Vacant": "YES ?", "Comments": None},
        "geometry": VACANT["geometry"]}
CASE_OPEN = {"attributes": {"Enforcement": "2025-00000452", "Address": "111 ISABELLE ST",
                            "Status": "In Progress", "Filed": "2025-06-13", "Closed": None},
             "geometry": {"x": -93.024005, "y": 34.498931}}
ZONE = {"attributes": {"Zoning_Code": "C-G", "Zoning_Description": "General Commercial",
                       "Ordinance": "O6524", "last_edited_date": 1787777361977}}
ADDR_ZONE = {"attributes": {"ADR_LABEL": "111 ISABELLE ST  ", "RPID": "51362", "Zoning_Code": "C-G"}}
METER = {"attributes": {"StreetNum": 111, "Street": "ISABELLE ST", "Status": "ACT",
                        "CustomerTy": "ROC", "InstallDat": 1438300800000}}


# ------------------------------------------------------------- adapters ---

def test_vacant_register_is_a_dated_fact_with_the_disclaimer(prop, monkeypatch):
    monkeypatch.setattr(hs, "_query", _fake_query({("Vacant_Structures_view", 99): [VACANT]}))
    res = hs.HS_VACANT.enrich(prop)
    assert res.status == "ok" and "ON the" in res.detail
    ev = res.records[0].evidence[0]
    assert ev["field"] == "vacant_structure" and ev["evidence_type"] == "FACT"
    assert ev["effective_date"] == "2024-11-20"
    assert "own risk" in ev["raw_ref"]
    assert res.records[0].fields["rpid"] == "51362"
    assert res.records[0].timeline[0]["kind"] == "vacancy"


def test_absence_from_the_register_is_an_observation_not_proof(prop, monkeypatch):
    monkeypatch.setattr(hs, "_query", _fake_query({}))
    res = hs.HS_VACANT.enrich(prop)
    ev = res.records[0].evidence[0]
    assert ev["field"] == "vacant_structure_check" and ev["evidence_type"] == "OBSERVATION"
    assert "not proof of occupancy" in ev["raw_ref"]


def test_lien_record_yields_amount_type_utilities_and_vacancy_hint(prop, monkeypatch):
    monkeypatch.setattr(hs, "_query", _fake_query({("Housing_Lien_Parcels", 90): [LIEN]}))
    res = hs.HS_LIENS.enrich(prop)
    fields = {e["field"]: e for e in res.records[0].evidence}
    assert "demolition lien of $4,102.40" in fields["cleanup_lien"]["value"]
    assert fields["cleanup_lien"]["effective_date"] == "2023-07-02"
    assert fields["cleanup_lien_total"]["value"] == 4102.4
    assert '6"' in fields["city_water"]["value"]
    assert fields["city_sewer"]["value"] == "not available"
    assert fields["vacant_per_lien_record"]["confidence"] == "LOW"     # 'YES ?'
    assert fields["zoning_on_lien_record"]["value"] == "R3"


def test_register_discovery_creates_properties_by_address_and_rpid(boundaries, monkeypatch):
    monkeypatch.setattr(hs, "_query", _fake_query({("Vacant_Structures_view", 99): [VACANT]}))
    res = hs.HS_VACANT.discover()
    assert res.status == "ok" and len(res.records) == 1
    rec = res.records[0]
    assert rec.fields["address"] == "111 Isabelle" and rec.fields["rpid"] == "51362"
    assert rec.fields["lat"] and rec.fields["lon"]
    pid, action, _ = store.ingest(rec)
    assert action == "created"
    # the tax-roll record for the same house then lands on the same property
    pid2, action2, _ = store.ingest(make_record(lat=rec.fields["lat"], lon=rec.fields["lon"]))
    assert pid2 == pid and action2 != "created"
    assert store.get_property(pid)["rpid"] == "51362"


def test_open_and_closed_code_cases_are_told_apart(prop, monkeypatch):
    closed = {"attributes": {**CASE_OPEN["attributes"], "Status": "Complied",
                             "Closed": "2025-07-01"}, "geometry": CASE_OPEN["geometry"]}
    monkeypatch.setattr(hs, "_query", _fake_query(
        {("Addressing_Points_for_Housing_Cases_2025", 33): [CASE_OPEN, closed]}))
    res = hs.HS_CODE.enrich(prop)
    fields = [e["field"] for e in res.records[0].evidence]
    assert fields == ["code_case_open", "code_case"]


def test_zoning_sets_the_district_rpid_and_overlays(prop, monkeypatch):
    monkeypatch.setattr(hs, "_query", _fake_query({
        ("Zoning_", 100): [ZONE], ("Addresses_with_Zoning", 110): [ADDR_ZONE],
        ("Central_Historic_District", 0): [{"attributes": {}}],
        ("Census_Opportunity_Zones", 1): [{"attributes": {}}]}))
    res = hs.HS_ZONING.enrich(prop)
    assert res.records[0].fields == {"zoning": "C-G - General Commercial", "rpid": "51362"}
    fields = {e["field"]: e for e in res.records[0].evidence}
    assert fields["zoning"]["evidence_type"] == "FACT" and "conditional-use" in fields["zoning"]["raw_ref"]
    assert fields["zoning_plain"]["value"] == "general commercial"
    assert fields["historic_district"]["value"] == "Central Historic District"
    assert fields["opportunity_zone"]["value"] == "federal Opportunity Zone"
    assert fields["rpid"]["value"] == "51362"


def test_outside_the_city_map_is_said_plainly(prop, monkeypatch):
    monkeypatch.setattr(hs, "_query", _fake_query({}))
    res = hs.HS_ZONING.enrich(prop)
    assert res.records[0].fields == {}
    ev = res.records[0].evidence[0]
    assert ev["field"] == "zoning_check" and "outside city limits" in ev["value"]
    assert "not the same as 'anything goes'" in ev["raw_ref"]


def test_water_meter_at_the_address_is_a_fact_but_nearby_is_only_an_observation(prop, monkeypatch):
    monkeypatch.setattr(hs, "_query", _fake_query({("Water_Meters", 12): [METER]}))
    res = hs.HS_UTILITIES.enrich(prop)
    ev = {e["field"]: e for e in res.records[0].evidence}
    assert ev["city_water"]["evidence_type"] == "FACT" and "at this address" in ev["city_water"]["value"]
    assert "septic likely" in ev["city_sewer"]["value"]
    other = {"attributes": {**METER["attributes"], "StreetNum": 115}}
    monkeypatch.setattr(hs, "_query", _fake_query({("Water_Meters", 12): [other]}))
    res = hs.HS_UTILITIES.enrich(prop)
    ev = {e["field"]: e for e in res.records[0].evidence}
    assert ev["city_water"]["evidence_type"] == "OBSERVATION" and "on this street" in ev["city_water"]["value"]


def test_city_adapter_failure_is_unavailable_not_empty(prop, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("service down")
    monkeypatch.setattr(hs, "_query", boom)
    for src in (hs.HS_VACANT, hs.HS_LIENS, hs.HS_CODE, hs.HS_ZONING, hs.HS_UTILITIES):
        assert src.enrich(prop).status == "unavailable"


# ------------------------------------------------------ signals & scores ---

def _with_city_evidence(prop, **fields):
    store.store_evidence(prop["id"], [{"field": k, "value": v, "evidence_type": "FACT",
                                       "confidence": "HIGH", "source": "hs_gis_test"}
                                      for k, v in fields.items()])
    return store.get_property(prop["id"])


def test_city_records_become_distress_signals(prop):
    p = _with_city_evidence(prop, vacant_structure="on the register",
                            cleanup_lien_total=4102.4, code_case_open="case",
                            historic_district="Central Historic District",
                            city_water="City water meter at this address (ACT)",
                            city_sewer="no City sewer main within ~70 m - septic likely")
    keys = {s["key"]: s for s in distress.analyse(p)}
    assert keys["vacant_structure"]["confidence"] == "HIGH"
    assert "$4,102" in keys["cleanup_lien"]["label"]
    assert "code_case_open" in keys and keys["historic_district"]["kind"] == "risk"
    assert keys["city_water"]["kind"] == "opportunity" and keys["septic_likely"]["kind"] == "risk"


def test_vacant_register_moves_the_overall_score_and_risk_sees_the_lien(prop):
    base = scoring.compute(prop, persist=False)
    p = _with_city_evidence(prop, vacant_structure="on the register", cleanup_lien_total=900)
    distress.refresh(p)
    p = store.get_property(p["id"])
    after = scoring.compute(p, persist=False)
    assert after["overall"]["score"] > base["overall"]["score"] + 10
    assert any("vacant-structure register" in l["reason"] for l in after["overall"]["lines"])
    assert after["risk"]["score"] > base["risk"]["score"]


def test_zoning_drives_business_snowcone_storage_and_rental(prop):
    db.ex("UPDATE properties SET zoning='C-G - General Commercial' WHERE id=?", (prop["id"],))
    com = scoring.compute(store.get_property(prop["id"]), persist=False)
    db.ex("UPDATE properties SET zoning='RN-2 - Single Family' WHERE id=?", (prop["id"],))
    res = scoring.compute(store.get_property(prop["id"]), persist=False)
    assert com["business"]["score"] > res["business"]["score"]
    assert com["snowcone"]["score"] > res["snowcone"]["score"]
    assert com["storage"]["score"] > res["storage"]["score"]
    assert res["rental"]["score"] > com["rental"]["score"]
    assert "zoning" not in com["overall"]["unknowns"]
    assert any("conditional-use permit or a rezoning" in l["reason"] for l in res["business"]["lines"])


# ------------------------------------------------------------- fuzzy (56) --

def test_misspelt_street_still_finds_the_house(client, prop):
    store.ingest(make_record(parcel_id="300-2", address="2748 Malvern Ave"))
    r = client.get("/api/properties?q=Isabell").json()
    assert r["total"] >= 1
    r = client.get("/api/properties?q=111 Isabel Street").json()      # exact LIKE fails
    assert r["properties"] and r["properties"][0]["address"] == "111 Isabelle St"
    assert r["did_you_mean"][0]["matched_on"] == "address"
    r = client.get("/api/properties?q=Malvurn").json()
    assert r["properties"][0]["address"] == "2748 Malvern Ave"
    r = client.get("/api/properties?q=Tucker Aquisitions").json()      # owner typo
    assert r["did_you_mean"][0]["matched_on"] == "owner"
    assert client.get("/api/properties?q=zzqqxx").json()["properties"] == []


def test_a_lien_read_from_the_register_is_a_signal_too(prop):
    """Regression: only per-parcel checks wrote cleanup_lien_total, so liens folded
    in from the whole-register read produced no distress signal."""
    p = _with_city_evidence(prop, cleanup_lien_amount=5917.61)
    keys = {s["key"]: s for s in distress.analyse(p)}
    assert "cleanup_lien" in keys and "$5,918" in keys["cleanup_lien"]["label"]


# ---------------------------------------------------- absentee owner (18) --

MAILING = lambda addr: {"attributes": {"ParcelId": "300-06186-000", "OwnerName": "X",
                                       "MailingAdd": addr, "AdrLabel": "111  ISABELLE ST",
                                       "AdrCity": "HOT SPRINGS", "AdrZip5": 71901,
                                       "SourceDate": 1511222400000}}


@pytest.mark.parametrize("mailing, expect", [
    ("300 E OAKLAND PARK BLVD #270  FORT LAUDERDALE FL 33334", "out of state"),
    ("8525 SARAH LN  MABELVALE AR 72103", "out of county"),
    ("PO BOX 272  ROYAL AR 71968", "po box"),
    ("111 ISABELLE ST  HOT SPRINGS AR 71901", "owner occupied"),
    ("100 FOUR OAKS LN  HOT SPRINGS AR 71901", "mailing address on file"),
])
def test_owner_mailing_is_classified_conservatively(prop, monkeypatch, mailing, expect):
    monkeypatch.setattr(hs, "_query", _fake_query({("Housing_Liens_WFL1", 0): [MAILING(mailing)]}))
    res = hs.HS_OWNER_MAILING.enrich(prop)
    assert res.status == "ok" and res.detail == expect
    fields = {e["field"]: e for e in res.records[0].evidence}
    assert fields["owner_mailing_address"]["evidence_type"] == "FACT"
    if expect in ("out of state", "out of county", "po box"):
        assert fields["absentee_owner"]["evidence_type"] == "OBSERVATION"
        assert "not proof" in fields["absentee_owner"]["raw_ref"]
    elif expect == "owner occupied":
        assert "owner_occupancy" in fields and "absentee_owner" not in fields
    else:
        assert "absentee_owner" not in fields and "owner_occupancy" not in fields


def test_absentee_and_owner_occupied_move_the_score_in_opposite_directions(boundaries):
    def scored(parcel, **ev):
        pid, _, _ = store.ingest(make_record(parcel_id=parcel, address=f"{parcel[-2:]} Score St"))
        if ev:
            store.store_evidence(pid, [{"field": k, "value": v, "evidence_type": "FACT",
                                        "confidence": "HIGH", "source": "hs_gis_test"}
                                       for k, v in ev.items()])
        distress.refresh(store.get_property(pid))
        return scoring.compute(store.get_property(pid), persist=False)["overall"]["score"]
    base = scored("300-40")
    away = scored("300-41", absentee_owner="owner gets the tax bill in FL")
    home = scored("300-42", owner_occupancy="tax bill goes to the property itself")
    assert away > base > home


# ------------------------------------------------ register removals (13) --

def test_leaving_the_vacant_register_is_observed_and_alerted(boundaries, monkeypatch):
    from hunter import scanner
    from hunter.sources import get_source
    from hunter.sources.base import SourceResult, Record
    # a house that was on the register last time
    pid, _, _ = store.ingest(make_record(parcel_id="300-21", address="120 Iowa St"))
    store.store_evidence(pid, [{"field": "vacant_structure", "value": "on the register",
                                "evidence_type": "FACT", "confidence": "HIGH",
                                "source": "hs_gis_vacant"}])
    # this run: the register is empty
    for name in scanner.Scan.CITY_REGISTERS:
        monkeypatch.setattr(get_source(name), "discover",
                            lambda **kw: SourceResult(status="ok", records=[], detail="0"))
    s = scanner.Scan(mode="city_registers"); s.save()
    s._city_registers()
    ev = [e for e in store.evidence_for(pid) if e["field"] == "vacant_structure_removed"]
    assert ev and ev[0]["evidence_type"] == "OBSERVATION"
    assert "does not say which" in ev[0]["raw_ref"]
    assert db.q1("SELECT 1 FROM alerts WHERE property_id=? AND kind='register_removed'", (pid,))
    assert "came off the vacant-structure register" in s.stage("city_registers").detail
    # a second run must not report it again
    s2 = scanner.Scan(mode="city_registers"); s2.save(); s2._city_registers()
    assert db.q1("SELECT COUNT(*) c FROM alerts WHERE property_id=? AND kind='register_removed'", (pid,))["c"] == 1


def test_scan_manual_tasks_are_capped_to_first_line_sources(boundaries, monkeypatch):
    from hunter import scanner
    from hunter.sources import get_source
    for i in range(12):
        store.ingest(make_record(parcel_id=f"300-3{i}", address=f"{i} Cap St", imp_value=900.0))
    s = scanner.Scan(mode="distress", enrich_top=40); s.save()
    s.touched = [r["id"] for r in db.q("SELECT id FROM properties")]
    for src in [get_source(n) for n in ("garland_assessor", "garland_tax_collector", "cosl",
                                        "hs_vacant_structures", "hs_code_enforcement",
                                        "garland_recorder", "hs_planning_zoning", "public_listings")]:
        monkeypatch.setattr(src, "health_check", lambda: __import__("hunter.sources.base",
                                                                    fromlist=["SourceResult"]).SourceResult(status="manual", detail="x"))
    s._manual()
    per_property = db.q("SELECT property_id, COUNT(*) n FROM tasks WHERE property_id IS NOT NULL GROUP BY property_id")
    assert 0 < len(per_property) <= 10
    assert all(r["n"] <= 4 for r in per_property)          # assessor, tax, COSL, recorder only
    assert not db.q1("SELECT 1 FROM tasks WHERE source='hs_planning_zoning'")
