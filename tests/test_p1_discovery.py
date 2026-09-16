"""P1 opportunity discovery + tax intelligence. Real exporter, real registry builder, real HTML.
A first reading is never called a world event; a source URL is never invented; a missing
record is never a tax state; a source outage is a fact about the source; the newer State
record beats the older Collector bill; the registry reports only what was observed."""
import json, re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CLASSES = {"WORLD_EVENT", "FIRST_DISCOVERY", "INFORMATIONAL", "SOURCE_CHECK", "MANUAL"}
STATUSES = {"AVAILABLE", "TEMPORARILY_UNAVAILABLE", "BLOCKED", "MANUAL_ONLY", "NOT_FOUND", "NOT_APPLICABLE", "UNKNOWN"}


def _ingest(parcel, county, **over):
    from hunter import store
    from tests.conftest import make_record
    pid, _, _ = store.ingest(make_record(parcel_id=parcel, county_fips=county, **over))
    return pid


def _age(pid, days=30):
    from hunter import db
    db.ex("UPDATE properties SET first_seen=datetime('now', ?) WHERE id=?", (f"-{days} days", pid))


def _ev(pid, field, value, **kw):
    from hunter import store
    e = {"field": field, "value": value, "evidence_type": "FACT", "confidence": "HIGH",
         "source": "hot_springs_gis", "source_name": "City of Hot Springs", "effective_date": None, "source_url": None}
    e.update(kw)
    store.store_evidence(pid, [e])


def _signals():
    import tools.build_share as bs
    rows = bs.export_rows()[0]
    by_id = {r["i"]: r for r in rows}
    return bs.build_signals(by_id, {"05051": "Garland", "05069": "Jefferson"}), by_id


# ------------------------------------------------------------------ feed classification

def test_first_reading_is_a_discovery_and_a_record_change_is_an_event():
    known = _ingest("600-1", "05051", address="1 Known St"); _age(known)
    _ev(known, "vacant_structure", "on the City's vacant-structure register", effective_date="2026-09-14",
        source_url="https://services1.arcgis.com/x/FeatureServer/99")
    fresh = _ingest("600-2", "05051", address="2 Fresh St")
    _ev(fresh, "vacant_structure", "on the City's vacant-structure register", effective_date="2024-11-20")
    sig, _ = _signals()
    assert sig["schema"] == 2 and set(sig["classes"]) == {"WORLD_EVENT", "FIRST_DISCOVERY"}
    ev = {x["a"]: x for x in sig["rows"]}; disc = {x["a"]: x for x in sig["discovered"]}
    assert ev["1 Known St"]["cls"] == "WORLD_EVENT" and ev["1 Known St"]["event"] == "NEW_VACANCY_RECORD"
    assert "2 Fresh St" not in ev and disc["2 Fresh St"]["cls"] == "FIRST_DISCOVERY"
    assert disc["2 Fresh St"]["date"] == "2024-11-20", "a discovery keeps the record's own date, not today"
    assert sig["total"] == 1 and sig["discovered_total"] == 1, "never merged into one number"


def test_provenance_is_real_or_absent():
    a = _ingest("601-1", "05051", address="1 Url St"); _age(a)
    _ev(a, "cleanup_lien_amount", "1500", effective_date="2026-09-13", source_url="https://services1.arcgis.com/x/FeatureServer/7")
    b = _ingest("601-2", "05051", address="2 NoUrl St"); _age(b)
    _ev(b, "code_case_open", "open case", effective_date="2026-09-13")
    sig, _ = _signals()
    ev = {x["a"]: x for x in sig["rows"]}
    assert ev["1 Url St"]["src_url"] == "https://services1.arcgis.com/x/FeatureServer/7"
    assert ev["2 NoUrl St"]["src_url"] is None, "no source URL is invented when the evidence carries none"
    for x in sig["rows"]:
        assert re.fullmatch(r"(evidence|change):\d+", x["evidence_ref"])
        assert x["src"] and x["status"] in ("VERIFIED", "OBSERVED") and x["kind"] in ("verified", "observed")
        assert x["next"]["href"].startswith(("lookup.html", "state-lands.html", "https://www.google.com/maps/dir/", "request.html"))
        assert x["sale"] == {"st": "UNKNOWN", "src": None}, "no listing source, so no sale claim"


def test_unknown_tax_stays_unknown_on_a_signal():
    a = _ingest("602-1", "05051", address="1 Unknown St"); _age(a)
    _ev(a, "vacant_structure", "on register", effective_date="2026-09-14")
    sig, by_id = _signals()
    x = next(r for r in sig["rows"] if r["a"] == "1 Unknown St")
    assert x["taxs"]["st"] == "UNKNOWN" and x["taxs"]["src"] is None
    assert not any(e["cls"] == "SOURCE_CHECK" for e in _timeline(by_id).get("05051", {}).get(str(a), [])), \
        "no Collector or State check was made, so none is shown"


def test_repair_artefacts_never_become_events():
    from hunter import db
    a = _ingest("603-1", "05051", address="1 Artefact St"); _age(a)
    _ev(a, "vacant_structure", "on register", effective_date="2026-09-14")
    db.ex("INSERT INTO changes(property_id, field, old_value, new_value, source, severity, detected_at) VALUES(?,?,?,?,?,?,datetime('now'))",
          (a, "county_fips", "05119", "05051", "repair", "info"))
    sig, _ = _signals()
    assert not any(x["a"] == "1 Artefact St" for x in sig["rows"] + sig["discovered"])


# ------------------------------------------------------------------ tax truth

def test_state_certification_is_not_overwritten_by_a_collector_bill(monkeypatch):
    from hunter.sources import countypay
    monkeypatch.setattr(countypay, "lookup", lambda slug, pid: {
        "available": True, "message": None, "checked_at": "2026-09-15T00:00:00", "key": pid,
        "bills": [{"key": pid, "name": "X", "type": "Real Estate", "category": "Current", "amount": 66.65}]})
    src = countypay.CountyPayTaxes()
    certified = src.enrich({"id": 1, "parcel_id": "700-1", "county_fips": "05051", "tax_status": "CERTIFIED_TO_STATE_FOR_SALE"})
    plain = src.enrich({"id": 2, "parcel_id": "700-2", "county_fips": "05051", "tax_status": None})
    assert certified.records[0].fields == {}, "a current bill never overwrites a State certification"
    assert plain.records[0].fields == {"tax_status": "CURRENT_BILL_OPEN"}
    assert any(e["field"] == "tax_bill" for e in certified.records[0].evidence), "the bill is still kept as evidence"


def test_newer_state_record_supersedes_older_certification():
    from hunter import db
    a = _ingest("604-1", "05051", address="1 Redeemed St"); _age(a)
    db.ex("UPDATE properties SET tax_status='CERTIFIED_TO_STATE_FOR_SALE' WHERE id=?", (a,))
    _ev(a, "tax_delinquent", "certified", effective_date="2026-06-01", source="cosl_listings", source_name="Arkansas Commissioner of State Lands")
    _ev(a, "tax_redemption", "redeemed", effective_date="2026-09-10", source="cosl_history", source_name="Arkansas Commissioner of State Lands")
    sig, by_id = _signals()
    row = next(r for r in by_id.values() if r["a"] == "1 Redeemed St")
    assert row["taxs"]["st"] != "TAX_SALE_VERIFIED"
    assert any(x["event"] == "STATE_REDEEMED" and x["cls"] == "WORLD_EVENT" for x in sig["rows"])


def _timeline(by_id):
    import tools.build_share as bs
    return bs.build_timelines(by_id)


def test_timeline_classification_and_provenance():
    from hunter import db
    a = _ingest("605-1", "05051", address="1 Timeline St", owner_name="OLD OWNER"); _age(a)
    _ev(a, "vacant_structure", "on register", effective_date="2024-11-20", source_url="https://services1.arcgis.com/x/99")
    _ev(a, "tax_status_check", "no open real-estate tax bill", evidence_type="OBSERVATION", confidence="MEDIUM",
        source="county_tax_collector", source_name="County Tax Collector", effective_date="2026-09-14")
    _ev(a, "tax_delinquent_county", "delinquent on the 2025 list", source="county_delinquent_list", source_name="Garland County Collector list", effective_date="2026-01-15")
    db.ex("INSERT INTO changes(property_id, field, old_value, new_value, source, severity, detected_at) VALUES(?,?,?,?,?,?,datetime('now'))",
          (a, "owner_name", "OLD OWNER", "NEW OWNER", "ar_gis_parcels", "info"))
    db.ex("INSERT INTO changes(property_id, field, old_value, new_value, source, severity, detected_at) VALUES(?,?,?,?,?,?,datetime('now'))",
          (a, "total_value", None, "50000", "ar_gis_parcels", "info"))
    _, by_id = _signals()
    tl = _timeline(by_id)["05051"][str(a)]
    cls = [e["cls"] for e in tl]
    assert set(cls) <= CLASSES
    world = [e for e in tl if e["cls"] == "WORLD_EVENT"]
    assert world and world[0]["date"] == "2024-11-20" and world[0]["url"].startswith("https://services1.arcgis.com/")
    assert any(e["cls"] == "FIRST_DISCOVERY" and e["ref"] == f"property:{a}" for e in tl)
    assert any(e["cls"] == "SOURCE_CHECK" and e["title"].startswith("Collector") for e in tl)
    assert any(e["cls"] == "MANUAL" for e in tl)
    info = [e for e in tl if e["cls"] == "INFORMATIONAL"]
    assert len(info) == 1 and "owner" in info[0]["title"], "a first reading with no old value is not a change"
    assert [e["date"] for e in tl] == sorted(e["date"] for e in tl)
    for e in tl:
        assert e["src"] and e["ref"] and (e["url"] is None or e["url"].startswith("https://"))


# ------------------------------------------------------------------ tax source registry: observed only

def test_check_countypay_maps_only_observed_outcomes(monkeypatch):
    import httpx
    import tools.tax_sources as ts
    class R:
        def __init__(self, code, text=""): self.status_code, self.text = code, text
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(404))
    assert ts.check_countypay("05119", "pulaski")["status"] == "NOT_FOUND"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(200, "<html>Online payments are currently unavailable.</html>"))
    r = ts.check_countypay("05051", "garland")
    assert r["status"] == "TEMPORARILY_UNAVAILABLE" and "unavailable" in (r["reason"] or "").lower()
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(503))
    assert ts.check_countypay("05051", "garland")["status"] == "UNKNOWN"
    def boom(*a, **k): raise httpx.ConnectError("x")
    monkeypatch.setattr(httpx, "get", boom)
    assert ts.check_countypay("05051", "garland")["status"] == "UNKNOWN"


def test_registry_never_claims_availability_it_did_not_observe(monkeypatch):
    import tools.tax_sources as ts
    monkeypatch.setattr(ts, "check_countypay", lambda fips, slug: {"status": "AVAILABLE", "reason": None, "http": 200} if fips == "05051"
                        else {"status": "TEMPORARILY_UNAVAILABLE", "reason": "Online payments are currently unavailable.", "http": 200})
    monkeypatch.setattr(ts.time, "sleep", lambda s: None)
    monkeypatch.setattr(ts, "load_state", lambda: {"counties": {}})
    a = _ingest("606-1", "05051", address="1 Reg St")
    out = ts.build(check=True)
    assert set(out["statuses"]) == STATUSES
    for fips, c in out["counties"].items():
        for s in c["sources"].values():
            assert s["status"] in STATUSES
        cp = c["sources"]["countypay"]
        if not cp["public_url"]:
            assert cp["status"] == "UNKNOWN" and cp["last_checked"] is None
        elif fips == "05051":
            assert cp["status"] == "AVAILABLE" and cp["last_success"] == cp["last_checked"]
        else:
            assert cp["status"] == "TEMPORARILY_UNAVAILABLE" and cp["failure_reason"] and cp["last_success"] is None
        assert c["sources"]["county_delinquent_list"]["status"] == "MANUAL_ONLY"
        assert c["counts"]["unknown"] >= 0
    g = out["counties"]["05051"]
    assert g["counts"]["parcels"] >= 1 and g["counts"]["unknown"] == g["counts"]["parcels"], "no tax answer means unknown, not current"
    assert g["sources"]["assessor_actdatascout"]["status"] == "BLOCKED"
    assert out["queue"] and all(x["next"]["href"].startswith("request.html?county=") for x in out["queue"])
    assert out["totals"]["unknown"] == sum(c["counts"]["unknown"] for c in out["counties"].values())


def test_unchecked_registry_keeps_prior_observation_not_a_guess(monkeypatch):
    import tools.tax_sources as ts
    monkeypatch.setattr(ts, "load_state", lambda: {"counties": {"05051": {"countypay": {"status": "TEMPORARILY_UNAVAILABLE", "last_checked": "2026-09-15T01:00:00+00:00",
                                                                                          "last_failure": "2026-09-15T01:00:00+00:00", "failure_reason": "Online payments are currently unavailable."}}}})
    out = ts.build(check=False)
    cp = out["counties"]["05051"]["sources"]["countypay"]
    assert cp["status"] == "TEMPORARILY_UNAVAILABLE" and cp["last_checked"] == "2026-09-15T01:00:00+00:00"
    assert out["counties"]["05069"]["sources"]["countypay"]["status"] == "UNKNOWN"


# ------------------------------------------------------------------ public output agrees with the rules

def test_public_signals_feed_is_consistent():
    d = json.loads((DOCS / "data" / "signals.json").read_text())
    assert d["schema"] == 2 and d["listing_source"] is None
    assert d["total"] >= len(d["rows"]) and d["discovered_total"] >= len(d["discovered"])
    need = {"id", "a", "cn", "cf", "pid", "event", "label", "date", "discovered_at", "src", "src_url", "status", "kind", "evidence_ref", "cls", "why", "next", "taxs", "sale"}
    for x in d["rows"]:
        assert need <= set(x) and x["cls"] == "WORLD_EVENT"
    for x in d["discovered"]:
        assert need <= set(x) and x["cls"] == "FIRST_DISCOVERY"
    for x in d["rows"] + d["discovered"]:
        assert x["src_url"] is None or re.match(r"https://(www\.|auction\.)?cosl\.org/|https://services1\.arcgis\.com/|https://countypay\.ark\.org/", x["src_url"]), x["src_url"]
        assert x["sale"]["st"] in ("UNKNOWN", "FOR_SALE_BY_STATE")
        if x["sale"]["st"] == "FOR_SALE_BY_STATE":
            assert x["sale"]["src"]
        assert x["taxs"]["st"] in ("TAX_SALE_VERIFIED", "DELINQUENT_VERIFIED", "CURRENT_BILL_OPEN", "CURRENT_VERIFIED", "STALE", "UNKNOWN")
        if x["taxs"]["st"] != "UNKNOWN":
            assert x["taxs"]["src"] and x["taxs"]["as_of"], "a tax state needs a source and a date"
        assert x["event"] != "NEW_TAX_SALE" or x["status"] == "VERIFIED"


def test_public_timelines_and_registry_are_well_formed():
    files = list((DOCS / "data" / "timeline").glob("*.json"))
    assert files
    for f in files[:10]:
        t = json.loads(f.read_text())
        for pid, evs in t["properties"].items():
            assert [e["date"] for e in evs] == sorted(e["date"] for e in evs)
            for e in evs:
                assert e["cls"] in CLASSES and e["src"] and e["ref"]
    d = json.loads((DOCS / "data" / "tax_sources.json").read_text())
    assert len(d["counties"]) == 75 and set(d["statuses"]) == STATUSES
    for c in d["counties"].values():
        for s in c["sources"].values():
            assert s["status"] in STATUSES
            if s["status"] in ("AVAILABLE",):
                assert s["last_success"], "AVAILABLE without an observed success is a guess"
    r = json.loads((DOCS / "data" / "radar.json").read_text())
    for c in r["counties"].values():
        assert all(x["cls"] in ("WORLD_EVENT", "FIRST_DISCOVERY") and x["basis"] for x in c["new"])
        assert all(g["cls"] == ("WORLD_EVENT" if g["how"] in ("sold", "redeemed") else "OBSERVED") for g in c["gone"])


def test_pages_carry_the_p1_surfaces_and_no_obsolete_language():
    disc = (DOCS / "discover.html").read_text()
    for k in ('id="events"', 'id="discoveries"', 'id="f-county"', 'id="f-event"', 'id="f-cls"', 'id="f-kind"', 'id="f-tax"', 'id="f-sale"', 'id="f-since"', 'PH.filterSignals', 'PH.sigCard'):
        assert k in disc
    assert "opportunity score" not in disc.lower().replace("no opportunity score", "")
    tax = (DOCS / "taxes.html").read_text()
    for k in ('id="cov"', 'id="queue"', "data/tax_sources.json", "request.html", "cosl.org", "import_delinquent_list"):
        assert k in tax
    look = (DOCS / "lookup.html").read_text()
    for k in ('id="knowsec"', 'id="tlsec"', "PH.timelineHtml", "PH.knowBlock", "data/timeline/"):
        assert k in look
    assert "Collector's search was unavailable" not in look, "a reason for an unknown is never hard-coded"
    nav = (DOCS / "nav.js").read_text()
    assert "discover.html" in nav and "taxes.html" in nav
    idx = (DOCS / "index.html").read_text()
    assert "discover.html" in idx and "taxes.html" in idx
    watch = (DOCS / "watch.html").read_text()
    assert "data/signals.json" in watch and "PH.CLASS" in watch
    for f in ("discover.html", "taxes.html", "lookup.html", "index.html", "watch.html", "pro.html"):
        s = (DOCS / f).read_text().lower()
        for bad in ("guaranteed", "hot deal", "motivated seller", "below market"):
            assert bad not in s, (f, bad)
