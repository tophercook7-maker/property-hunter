"""P0 trust + opportunity foundation: the site never turns missing information into a
finding, never lets one source speak for another, never keeps a stale State certification
alive as a current signal, never rings a bell for a roll flicker, and never lets silence
mean 'not for sale'. Tests run against the real exporter and the real HTML."""
import json, os, re, shutil, subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


# ---------------------------------------------------------------- exporter: tax wording by source

def _ingest(parcel, county, **over):
    from hunter import store
    from tests.conftest import make_record
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, **over))
    return pid


def test_cosl_check_never_reads_as_collector(tmp_path, monkeypatch):
    from hunter import store, db
    import tools.build_share as bs
    a = _ingest("500-1", "05051", address="1 Cosl St")
    store.store_evidence(a, [{"field": "tax_status_check", "value": "not held by the Commissioner of State Lands",
                             "evidence_type": "OBSERVATION", "confidence": "HIGH", "source": "cosl_listings",
                             "source_name": "Arkansas Commissioner of State Lands", "effective_date": "2026-09-14"}])
    b = _ingest("500-2", "05051", address="2 Collector St")
    store.store_evidence(b, [{"field": "tax_status_check", "value": "no open real-estate tax bill on the Collector's payment site",
                             "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": "county_tax_collector",
                             "source_name": "County Tax Collector", "effective_date": "2026-09-14"}])
    c = _ingest("500-3", "05051", address="3 Unknown St")
    rows = {r["a"]: r for r in bs.export_rows()[0]}
    assert rows["1 Cosl St"]["tax"] == "not held by the State (State Lands check)"
    assert rows["1 Cosl St"]["taxs"]["st"] == "UNKNOWN" and rows["1 Cosl St"]["taxs"]["cosl_check"] == "2026-09-14"
    assert rows["2 Collector St"]["tax"] == "no open bill at the Collector"
    assert rows["2 Collector St"]["taxs"] == {"st": "CURRENT_VERIFIED", "src": "County Collector", "as_of": "2026-09-14", "amt": None, "conf": "OBSERVATION"}
    assert rows["3 Unknown St"]["tax"] is None and rows["3 Unknown St"]["taxs"]["st"] == "UNKNOWN"
    for r in rows.values():
        assert r["sale"] == {"st": "UNKNOWN", "src": None}


def test_current_bill_is_never_delinquent(tmp_path):
    from hunter import store, db
    import tools.build_share as bs
    a = _ingest("501-1", "05069", address="1 Bill St")
    db.ex("UPDATE properties SET tax_status='CURRENT_BILL_OPEN' WHERE id=?", (a,))
    store.store_evidence(a, [{"field": "tax_bill", "value": "$66.65 owed to the County Collector (current real estate $63.49, voluntary real estate $3.16)",
                             "evidence_type": "FACT", "confidence": "HIGH", "source": "county_tax_collector",
                             "source_name": "County Tax Collector", "effective_date": "2026-09-15"}])
    b = _ingest("501-2", "05069", address="2 Delinquent St")
    db.ex("UPDATE properties SET tax_status='DELINQUENT' WHERE id=?", (b,))
    store.store_evidence(b, [{"field": "tax_bill", "value": "$212.40 owed to the County Collector (delinquent real estate $212.40)",
                             "evidence_type": "FACT", "confidence": "HIGH", "source": "county_tax_collector",
                             "source_name": "County Tax Collector", "effective_date": "2026-09-15"}])
    rows = {r["a"]: r for r in bs.export_rows()[0]}
    assert rows["1 Bill St"]["taxs"]["st"] == "CURRENT_BILL_OPEN" and rows["1 Bill St"]["taxs"]["amt"] == 66.65
    assert rows["2 Delinquent St"]["taxs"]["st"] == "DELINQUENT_VERIFIED" and rows["2 Delinquent St"]["taxs"]["amt"] == 212.40


def test_stale_collector_answer_is_marked_stale():
    from hunter import store
    import tools.build_share as bs
    a = _ingest("502-1", "05051", address="1 Old St")
    store.store_evidence(a, [{"field": "tax_status_check", "value": "no open real-estate tax bill", "evidence_type": "OBSERVATION",
                             "confidence": "MEDIUM", "source": "county_tax_collector", "source_name": "County Tax Collector",
                             "effective_date": "2026-01-05"}])
    r = next(x for x in bs.export_rows()[0] if x["a"] == "1 Old St")
    assert r["taxs"]["st"] == "STALE" and r["taxs"]["was"] == "CURRENT_VERIFIED" and r["taxs"]["days"] > 45


# ---------------------------------------------------------------- distress: stale certification

def _certify(pid, when="2026-06-01"):
    from hunter import store
    store.store_evidence(pid, [{"field": "tax_delinquent", "value": f"certified to the State for unpaid taxes; for sale by COSL (listed {when})",
                               "evidence_type": "FACT", "confidence": "HIGH", "source": "cosl_listings",
                               "source_name": "Arkansas Commissioner of State Lands", "effective_date": when,
                               "raw_ref": "[key cosl:x]"}])


@pytest.mark.parametrize("ended_field", ["tax_delinquent_removed", "tax_redemption", "tax_sale_history"])
def test_certification_stops_being_a_current_signal_after_removal_redemption_or_sale(ended_field):
    from hunter import store, distress
    pid = _ingest("503-" + ended_field[-4:], "05051", address="1 Stale St", lat=34.5, lon=-93.0)
    _certify(pid)
    p = store.get_property(pid)
    assert any(s["key"] == "tax_delinquent" for s in distress.analyse(p)), "certification is a live signal at first"
    store.store_evidence(pid, [{"field": ended_field, "value": "left the inventory / redeemed / sold", "evidence_type": "OBSERVATION",
                               "confidence": "MEDIUM", "source": "cosl_listings", "source_name": "COSL", "effective_date": "2026-09-01"}])
    assert not any(s["key"] == "tax_delinquent" for s in distress.analyse(store.get_property(pid))), ended_field
    # history is intact: the certification evidence still exists
    assert store.latest_evidence(pid, "tax_delinquent") is not None
    # and a NEWER certification revives the signal
    _certify(pid, when="2026-09-10")
    assert any(s["key"] == "tax_delinquent" for s in distress.analyse(store.get_property(pid)))


# ---------------------------------------------------------------- alerts

def test_roll_value_and_owner_changes_do_not_alert_but_tax_status_does():
    from hunter import store, db
    from tests.conftest import make_record
    pid, _, _ = store.ingest(make_record(parcel_id="504-1", county_fips="05051", address="1 Alert St", owner_name="A", total_value=1000.0))
    before = db.q1("SELECT COUNT(*) n FROM alerts WHERE property_id=?", (pid,))["n"]
    store.ingest(make_record(parcel_id="504-1", county_fips="05051", address="1 Alert St", owner_name="B", total_value=9000.0))
    assert db.q1("SELECT COUNT(*) n FROM alerts WHERE property_id=?", (pid,))["n"] == before
    assert db.q1("SELECT COUNT(*) n FROM changes WHERE property_id=? AND field='owner_name'", (pid,))["n"] == 1   # still recorded
    store.ingest(make_record(parcel_id="504-1", county_fips="05051", address="1 Alert St", owner_name="B", total_value=9000.0, tax_status="CERTIFIED_TO_STATE_FOR_SALE"))
    a = db.q("SELECT kind FROM alerts WHERE property_id=? ORDER BY id DESC", (pid,))
    assert a and a[0]["kind"] == "opportunity_signal"


def test_artifact_alerts_are_superseded_not_shown():
    from hunter import store, db
    from hunter.db import utcnow
    import tools.repair_county_merges as rep
    pid = _ingest("505-1", "05051", address="1 Artefact St")
    # an old-style alert whose change row is gone (the repair deleted it)
    aid = store.add_alert(pid, "property_changed", "1 Artefact St - owner changed", "owner_name: X -> Y (source: ar_gis_parcels)", "high")
    n = rep.supersede_artifact_alerts(apply=True)
    assert n >= 1
    row = db.q1("SELECT kind, read_at FROM alerts WHERE id=?", (aid,))
    assert row["kind"] == "repair_artifact" and row["read_at"]
    # a change that still exists keeps its alert
    db.ex("INSERT INTO changes(property_id,field,old_value,new_value,source,severity,detected_at) VALUES(?,?,?,?,?,?,?)",
          (pid, "zoning", "R1", "C2", "hs_gis_zoning", "high", utcnow()))
    keep = store.add_alert(pid, "property_changed", "1 Artefact St - zoning changed", "zoning: R1 -> C2 (source: hs_gis_zoning)", "high")
    rep.supersede_artifact_alerts(apply=True)
    assert db.q1("SELECT kind FROM alerts WHERE id=?", (keep,))["kind"] == "property_changed"


# ---------------------------------------------------------------- signals feed

def test_signals_feed_is_events_only_and_excludes_repair_artifacts():
    from hunter import store, db
    from hunter.db import utcnow
    import tools.build_share as bs
    from tests.conftest import make_record
    # a parcel newly certified this week -> NEW_TAX_SALE
    a = _ingest("506-1", "05051", address="1 Signal St", lat=34.5, lon=-93.0)
    store.ingest(make_record(parcel_id="506-1", county_fips="05051", address="1 Signal St", lat=34.5, lon=-93.0, tax_status="CERTIFIED_TO_STATE_FOR_SALE"))
    # a parcel whose value merely moved -> nothing
    b = _ingest("506-2", "05051", address="2 Flicker St", total_value=5000.0)
    store.ingest(make_record(parcel_id="506-2", county_fips="05051", address="2 Flicker St", total_value=9000.0))
    # a parcel that jumped county this week (repair artefact) -> excluded even if certified
    c = _ingest("506-3", "05051", address="3 Artefact St")
    db.ex("INSERT INTO changes(property_id,field,old_value,new_value,source,severity,detected_at) VALUES(?,?,?,?,?,?,?)", (c, "county_fips", "05013", "05051", "ar_gis_parcels", "info", utcnow()))
    db.ex("INSERT INTO changes(property_id,field,old_value,new_value,source,severity,detected_at) VALUES(?,?,?,?,?,?,?)", (c, "tax_status", None, "CERTIFIED_TO_STATE_FOR_SALE", "cosl_listings", "high", utcnow()))
    rows, _ = bs.export_rows()
    feed = bs.build_signals({r["i"]: r for r in rows}, {})
    ids = {x["id"]: x for x in feed["rows"] + feed["discovered"]}
    assert a in ids and ids[a]["event"] == "NEW_TAX_SALE" and ids[a]["status"] == "VERIFIED" and ids[a]["src"] == "Commissioner of State Lands"
    assert ids[a]["next"]["label"] == "Open sale file" and ids[a]["next"]["href"].startswith("state-lands.html?county=GARLAND")
    assert b not in ids
    assert c not in ids
    assert feed["listing_source"] is None
    for x in feed["rows"] + feed["discovered"]:
        assert x["next"]["label"] and x["next"]["href"] and x["src"] and x["date"] and x["why"]


# ---------------------------------------------------------------- public HTML

def test_public_pages_use_the_honest_vocabulary():
    index = (DOCS / "index.html").read_text()
    assert "New opportunity signals" in index and "What changed this week" not in index
    assert "opportunity signals this week" in index and "'changes this week'" not in index
    assert "No new opportunity signals this week" in index
    hunt = (DOCS / "hunt.html").read_text()
    for label in ("State tax sale", "Collector bill verified", "Verified delinquency", "City lien", "House appraised under $60k", "Land appraised under $25k"):
        assert label in hunt, label
    assert ">Tax distress<" not in hunt and ">Cheap houses<" not in hunt and ">Cheap land<" not in hunt
    assert "Appraised value is the assessor's figure, not market value" in hunt
    lookup = (DOCS / "lookup.html").read_text()
    assert "UNKNOWN — no listing source connected" in lookup and "Signal stack" in lookup
    assert "Research priority" not in lookup
    assert "not certified for sale for unpaid taxes (that takes roughly two years behind)" not in lookup
    ph = (DOCS / "ph.js").read_text()
    for bad in ("guarantee of profit",):   # the old disclaimer text is fine only if the concept stays negative
        pass
    assert "not a probability of profit" in ph and "research priority" not in ph.lower().replace("research-priority", "")
    # every public page that renders a property card mentions sale status
    for page in ("index.html", "hunt.html", "lookup.html", "state-lands.html"):
        t = (DOCS / page).read_text()
        assert re.search(r"sale unknown|no listing source|for sale by the State|PH\.saleStatus|PH\.whyRow", t, re.I), page


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_site_logic_p0_under_node():
    r = subprocess.run(["node", str(ROOT / "tests" / "ui" / "ph.test.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- data safety

def test_cross_county_merge_protection_still_holds():
    from hunter import store, identity
    from tests.conftest import make_record
    a, _, _ = store.ingest(make_record(parcel_id="001-03774-000", address="1 Saline Rd", county_fips="05125", lat=34.65, lon=-92.47))
    b, action, _ = store.ingest(make_record(parcel_id="001-03774-000", address="6723 Moore Ln", county_fips="05053", lat=34.32, lon=-92.37))
    assert a != b and action == "created"


def test_exported_feed_files_have_no_listing_source_and_keep_unknown_unknown():
    """Against the real exports on disk (skipped if not built)."""
    sig = DOCS / "data" / "signals.json"
    if not sig.exists():
        pytest.skip("exports not built")
    d = json.loads(sig.read_text())
    assert d["listing_source"] is None
    for x in d["rows"] + d["discovered"]:
        assert x["event"] in ("NEW_TAX_SALE", "STATE_SOLD", "STATE_REDEEMED", "STATE_LEFT", "NEW_VACANCY_RECORD", "NEW_LIEN", "NEW_CODE_CASE", "VERIFIED_DELINQUENCY")
        assert x["next"]["label"]
    scan = json.loads((DOCS / "data" / "scan" / "05051.json").read_text())
    sts = {r["taxs"]["st"] for r in scan["rows"]}
    assert sts <= {"TAX_SALE_VERIFIED", "DELINQUENT_VERIFIED", "CURRENT_BILL_OPEN", "CURRENT_VERIFIED", "STALE", "UNKNOWN"}
    assert all(r["sale"]["st"] == "UNKNOWN" for r in scan["rows"])
    assert all("amt" in r["taxs"] and (r["taxs"]["amt"] is None or r["taxs"]["st"] != "UNKNOWN") for r in scan["rows"])
