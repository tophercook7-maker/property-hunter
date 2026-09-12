"""Source adapters: honesty about failure is the whole point (spec 15, 52, 76, 87)."""
import pytest

from hunter import db
from hunter.sources import all_sources, get_source, register_all
from hunter.sources.base import (AUTOMATED, BLOCKED, MANUAL, OK, UNAVAILABLE,
                                 PropertySource, SourceResult)


@pytest.fixture(autouse=True)
def registered():
    register_all()


def test_every_source_declares_itself():
    for s in all_sources():
        assert s.name and s.label and s.kind
        assert s.access in (AUTOMATED, MANUAL, BLOCKED)


def test_manual_sources_raise_a_task_instead_of_pretending():
    for s in all_sources():
        if s.access == AUTOMATED:
            continue
        result = s.discover()
        assert result.status == "manual"
        assert result.records == []          # never a fake empty success
        assert result.manual_tasks
        t = result.manual_tasks[0]
        assert "MANUAL VERIFICATION REQUIRED" in t["title"]
        assert t["why"] and t["detail"]


def test_a_failing_source_is_recorded_as_unavailable_not_as_zero_results():
    class Broken(PropertySource):
        name = "broken_test_source"
        label = "Deliberately broken source"
        kind = "parcel"

        def discover(self, **kw):
            return SourceResult(status=UNAVAILABLE, error="connection refused",
                                detail="the service did not answer")

    src = Broken()
    src.register()
    res = src.discover()
    src.record_attempt(res)
    row = db.q1("SELECT * FROM sources WHERE name='broken_test_source'")
    assert row["status"] == "unavailable"
    assert row["last_error"] == "connection refused"
    assert row["last_success"] is None
    assert row["records_found"] == 0


def test_blocked_source_is_not_worked_around():
    s = get_source("garland_assessor")
    assert s.access == BLOCKED
    assert "403" in s.why_manual
    assert "do not work around" in s.why_manual.lower()


def test_cosl_source_carries_the_tax_ownership_warning():
    s = get_source("cosl")
    assert "does NOT make you the owner" in s.what_to_check


def test_parcel_adapter_maps_real_attributes_without_inventing():
    from hunter.sources.ar_parcels import ArkansasParcels
    feature = {
        "attributes": {
            "parcelid": "300-06186-000", "ownername": "TUCKER ACQUISITIONS LLC",
            "adrlabel": "111  ISABELLE ST", "adrcity": "HOT SPRINGS", "adrzip5": 71901,
            "parceltype": "RI", "impvalue": 1050.0, "landvalue": 23200.0,
            "totalvalue": 24250.0, "taxarea": 2.154, "parcellgl": "PT NE SE",
            "subdivision": "UNPLATTED HOT SPRINGS", "countyfips": "05051",
            "sourcedate": 1483250400000, "pubdate": 1775019600000,
        },
        "geometry": {"rings": [[[-93.05, 34.51], [-93.05, 34.511], [-93.049, 34.511],
                                [-93.049, 34.51], [-93.05, 34.51]]]},
    }
    terr = {"key": "garland_ar", "county_fips": "05051"}
    rec = ArkansasParcels()._to_record(feature, terr)
    assert rec.fields["owner_name"] == "TUCKER ACQUISITIONS LLC"
    assert rec.fields["property_type"] == "house"
    assert rec.fields["improved"] == 1
    assert rec.fields["lat"] and rec.fields["lon"]
    types = {e["field"]: e for e in rec.evidence}
    # the class letter is solid; the improvement letter is explicitly an inference
    assert types["property_class"]["evidence_type"] == "FACT"
    assert types["improvement_state"]["evidence_type"] == "ESTIMATE"
    assert types["improvement_state"]["confidence"] == "LOW"
    assert "not yet verified" in types["improvement_state"]["raw_ref"]
    # nothing claims a fact we were not given
    assert "zoning" not in types
    assert "tax_status" not in types


def test_parcel_adapter_skips_records_with_no_parcel_id():
    from hunter.sources.ar_parcels import ArkansasParcels
    assert ArkansasParcels()._to_record({"attributes": {"ownername": "X"}},
                                        {"key": "g", "county_fips": "05051"}) is None


def test_flood_zone_classification():
    from hunter.sources.flood import is_sfha
    assert is_sfha("AE") and is_sfha("A") and is_sfha("VE")
    assert not is_sfha("X") and not is_sfha(None) and not is_sfha("")
