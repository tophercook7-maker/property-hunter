"""The scan pipeline: honest stages, real funnel numbers (spec 14, 75, 76)."""
import pytest

from conftest import make_record
from hunter import db, scanner, store
from hunter.sources.base import OK, SourceResult


def test_every_stage_starts_as_waiting_and_is_named():
    s = scanner.Scan(mode="seeds", limit=1)
    assert len(s.stages) == len(scanner.STAGES)
    for st in s.stages:
        assert st.status == "waiting"
        assert st.label and st.key


def test_funnel_is_counted_never_invented():
    s = scanner.Scan()
    s.stats.update(records_examined=1847, properties_matched=126, excluded=6,
                   candidates=38, strong_candidates=11, picks=3)
    f = {x["label"]: x["value"] for x in s.funnel()}
    assert f["records examined"] == 1847
    assert f["after exclusions"] == 120          # 126 - 6, computed not guessed
    assert f["Topher picks"] == 3


def test_funnel_never_goes_negative():
    s = scanner.Scan()
    s.stats.update(properties_matched=2, excluded=9)
    assert all(x["value"] >= 0 for x in s.funnel())


def test_a_source_failure_marks_the_stage_unavailable_not_done(monkeypatch):
    s = scanner.Scan(mode="distress", limit=5)
    s.save()
    src = scanner.get_source("census_boundaries")
    monkeypatch.setattr(src, "discover",
                        lambda **kw: SourceResult(status="unavailable",
                                                  error="network down",
                                                  detail="TIGERweb did not answer"))
    s._boundaries()
    stage = s.stage("boundaries")
    assert stage.status == "unavailable"
    assert "did not answer" in stage.detail
    assert "cached locally" in stage.detail        # and it says what it fell back to
    assert s.stats["sources_unavailable"] == 1


def test_scan_records_itself_in_the_database():
    s = scanner.Scan(mode="seeds", limit=1)
    s.save()
    row = db.q1("SELECT * FROM scans WHERE id=?", (s.id,))
    assert row["mode"] == "seeds"
    assert row["status"] == "running"
    assert row["started_at"]


def test_presets_are_real_where_clauses():
    for key, preset in scanner.PRESETS.items():
        assert preset["label"] and preset["description"]
        if key not in ("seeds", "full"):
            w = preset["where"]
            assert w and ("parceltype" in w or "ownername" in w or "value" in w)


def test_ingest_stage_counts_new_versus_seen():
    s = scanner.Scan(mode="seeds")
    s.save()
    records = [make_record(parcel_id=f"300-0{i}-000", address=f"{i} Test St")
               for i in range(5)]
    s._ingest(records)
    assert s.stats["new_properties"] == 5
    assert s.stats["properties_matched"] == 5
    s2 = scanner.Scan(mode="seeds")
    s2.save()
    s2._ingest(records)
    assert s2.stats["new_properties"] == 0


def test_exclusion_stage_counts_against_the_real_table(boundaries):
    s = scanner.Scan(mode="seeds")
    s.save()
    s._ingest([make_record(parcel_id="999-1", lat=34.657, lon=-92.97),
               make_record(parcel_id="300-1")])
    s._exclusion()
    assert s.stats["excluded"] == 1
    assert "excluded overall" in s.stage("exclusion").detail


def test_seed_reporting_names_what_it_could_not_match():
    from hunter.seeds import SEED_ADDRESSES, mark_seeds
    store.ingest(make_record(address="100 Edwards Pl", parcel_id="300-EDW"))
    r = mark_seeds()
    assert any(m["seed"] == "100 Edwards Pl" for m in r["matched"])
    assert r["unmatched"]                       # the rest are honestly reported
    assert "not a failure" in r["note"]


def test_seeds_are_stored_as_unverified_leads_not_as_facts():
    from hunter.seeds import mark_seeds
    pid, _, _ = store.ingest(make_record(address="100 Edwards Pl", parcel_id="300-EDW"))
    mark_seeds()
    ev = [e for e in store.evidence_for(pid) if e["field"] == "investigation_seed"]
    assert ev
    assert ev[0]["evidence_type"] == "UNKNOWN"
    assert ev[0]["confidence"] == "NONE"
    assert "nothing about its current condition" in ev[0]["raw_ref"]
    assert "has not been re-confirmed" in ev[0]["raw_ref"]
