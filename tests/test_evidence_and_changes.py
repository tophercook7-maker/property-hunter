"""Evidence, conflicts, snapshots and change detection (spec 10, 11, 13, 55)."""
from conftest import make_record
from hunter import db, store


def test_every_stored_fact_has_a_source_and_a_date():
    pid, _, _ = store.ingest(make_record())
    rows = store.evidence_for(pid)
    assert rows
    for e in rows:
        assert e["source"], f"{e['field']} has no source"
        assert e["confidence"] in ("HIGH", "MEDIUM", "LOW", "NONE")
        assert e["evidence_type"] in ("FACT", "OBSERVATION", "CALCULATION", "ESTIMATE",
                                      "AI_OPINION", "UNKNOWN", "CONFLICTING")
        assert e["retrieved_at"]


def test_conflicting_sources_are_recorded_not_overwritten():
    pid, _, _ = store.ingest(make_record())
    store.store_evidence(pid, [{
        "field": "owner_name", "value": "SOMEBODY ELSE", "evidence_type": "FACT",
        "confidence": "HIGH", "source": "other_source", "effective_date": "2026-06-01"}])
    conflicts = db.q("SELECT * FROM conflicts WHERE property_id=?", (pid,))
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["status"] == "NEEDS VERIFICATION"
    assert {c["value_a"], c["value_b"]} == {"TUCKER ACQUISITIONS LLC", "SOMEBODY ELSE"}
    # both readings survive
    values = {e["value"] for e in store.evidence_for(pid) if e["field"] == "owner_name"}
    assert {"TUCKER ACQUISITIONS LLC", "SOMEBODY ELSE"} <= values


def test_owner_change_is_detected_and_raises_an_alert():
    store.ingest(make_record())
    pid, action, changes = store.ingest(make_record(owner_name="NEW OWNER LLC"))
    assert action == "updated"
    assert any(c["field"] == "owner_name" for c in changes)
    alerts = db.q("SELECT * FROM alerts WHERE property_id=?", (pid,))
    assert any("owner changed" in a["title"].lower() for a in alerts)
    ch = db.q1("SELECT * FROM changes WHERE property_id=? AND field='owner_name'", (pid,))
    assert ch["old_value"] == "TUCKER ACQUISITIONS LLC"
    assert ch["new_value"] == "NEW OWNER LLC"
    assert ch["severity"] == "high"


def test_price_and_tax_changes_are_high_severity():
    store.ingest(make_record())
    store.ingest(make_record(list_price=49000, tax_status="2 years delinquent"))
    sev = {r["field"]: r["severity"] for r in
           db.q("SELECT field,severity FROM changes")}
    assert sev.get("list_price") == "high"
    assert sev.get("tax_status") == "high"


def test_history_is_never_lost():
    store.ingest(make_record(total_value=24250))
    pid, _, _ = store.ingest(make_record(total_value=30000))
    store.ingest(make_record(total_value=41000))
    values = [float(r["new_value"]) for r in
              db.q("SELECT new_value FROM changes WHERE property_id=? AND field='total_value' "
                   "ORDER BY id", (pid,))]
    assert values == [30000.0, 41000.0]
    assert db.q1("SELECT COUNT(*) c FROM snapshots WHERE property_id=?", (pid,))["c"] >= 3


def test_snapshot_only_stored_when_payload_actually_changes():
    pid, _, _ = store.ingest(make_record())
    n = db.q1("SELECT COUNT(*) c FROM snapshots")["c"]
    store.ingest(make_record())
    assert db.q1("SELECT COUNT(*) c FROM snapshots")["c"] == n


def test_a_listing_appearing_is_itself_a_change():
    """Going from "not for sale" to "listed at $49,000" is the news, not a gap fill."""
    store.ingest(make_record())
    pid, _, changes = store.ingest(make_record(list_price=49000))
    fields = {c["field"] for c in changes}
    assert "list_price" in fields
    ch = db.q1("SELECT * FROM changes WHERE field='list_price'")
    assert ch["old_value"] == "not known" and ch["severity"] == "high"
    assert db.q1("SELECT COUNT(*) c FROM alerts WHERE property_id=?", (pid,))["c"] >= 1


def test_excluded_property_changes_do_not_raise_alerts(boundaries):
    store.ingest(make_record(lat=34.657, lon=-92.97, parcel_id="999-1"))
    store.ingest(make_record(lat=34.657, lon=-92.97, parcel_id="999-1",
                             owner_name="SOMEONE NEW"))
    assert db.q1("SELECT COUNT(*) c FROM alerts")["c"] == 0
