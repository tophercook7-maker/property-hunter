"""Foreclosure sale notices: the statutory internet notice service, read honestly.
Offline: the fixture is a saved copy of the service's page."""
import json, os
from pathlib import Path

from hunter import store
from hunter.sources import foreclosure_notices as src
from hunter.cases import sale_state

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
FIX = ROOT / "tests" / "fixtures" / "trustee_foreclosure_sales.html"


def test_parse_reads_every_arkansas_row_with_dates_and_fips():
    rows = src.parse(FIX.read_text(), fetched_at="2026-09-30T20:00:00+00:00")
    assert len(rows) >= 50
    assert all(r["sale_date"] and len(r["sale_date"]) == 10 for r in rows)
    assert all(r["county_fips"] and r["county_fips"].startswith("05") for r in rows), [r["county"] for r in rows if not r["county_fips"]]
    assert all(r["source"] == src.SOURCE and r["source_url"] for r in rows)
    one = next(r for r in rows if r["address"] == "2405 Wolfe Street")
    assert one["county_fips"] == "05119" and one["address_norm"] == "2405 WOLFE ST" and one["location"] == "Pulaski County Courthouse"
    # Tennessee rows never leak in
    assert not any(r.get("state") == "TN" for r in rows)


def test_build_joins_exact_address_only_and_writes_dated_evidence(tmp_path, monkeypatch):
    from tests.conftest import make_record
    import tools.build_foreclosures as bf
    monkeypatch.setattr(bf, "OUT", str(tmp_path / "foreclosures.json"))
    monkeypatch.setattr(bf, "HIST", str(tmp_path / "foreclosures_history.json"))
    pid, _, _ = store.ingest(make_record(county_fips="05119", territory="pulaski_ar", parcel_id="34L0120500100", address="2405 Wolfe St", city="LITTLE ROCK"))
    # a second parcel at a different address must not be matched
    store.ingest(make_record(county_fips="05119", territory="pulaski_ar", parcel_id="34L0120500200", address="2407 Wolfe St", city="LITTLE ROCK"))
    r = bf.build(str(FIX), today="2026-09-30")
    assert r["count"] >= 50 and r["joined"] >= 1 and r["evidence_rows"] >= 1
    doc = json.load(open(bf.OUT))
    n = next(x for x in doc["notices"] if x["address_norm"] == "2405 WOLFE ST")
    assert n["property_id"] == pid and n["parcel_id"] == "34L0120500100" and n["join"] == "address"
    assert n["new_this_week"] is True and n["first_seen"] == "2026-09-30"
    ev = store.latest_evidence(pid, "foreclosure_notice")
    assert ev and "Foreclosure sale noticed for 2026-10-07" in ev["value"] and ev["source"] == src.SOURCE
    assert (ev.get("effective_date") or "")[:10] == "2026-09-30"
    # honesty: the file says what it is not
    assert "opening bid" in doc["what_it_is_not"].lower() and "outcome" in doc["what_it_is_not"].lower()
    # a notice that disappears is "outcome unknown", never "sold"
    r2 = bf.build(str(ROOT / "tests" / "fixtures" / "trustee_foreclosure_sales_empty.html"), today="2026-10-02")
    assert r2["gone"] >= 50
    chk = store.latest_evidence(pid, "foreclosure_notice_check")
    assert chk and "Outcome unknown" in chk["value"] and "sold" not in chk["value"].split("Outcome unknown")[0].lower()
    hist = json.load(open(bf.HIST))
    assert any(g["address_norm"] == "2405 WOLFE ST" for g in hist["gone"])


def test_sale_state_knows_a_noticed_sale_is_not_a_listing_or_an_outcome():
    s = sale_state({"sale": {"st": "FORECLOSURE_SALE_NOTICED", "src": "internet foreclosure sale notice service", "sale_date": "2026-10-07", "location": "Pulaski County Courthouse"}})
    assert s["st"] == "FORECLOSURE_SALE_NOTICED" and "2026-10-07" in s["text"] and "not a listing" in s["text"] and "not an outcome" in s["text"]
    assert sale_state({"sale": {"st": "UNKNOWN", "src": None}})["st"] == "UNKNOWN"


def test_site_wiring_is_honest():
    page = (DOCS / "foreclosures.html").read_text()
    for k in ("data/foreclosures.json", "18-50-105", "no opening bid", "Not an outcome", "arkansaspublicnotices.com", "lookup.html?county="):
        assert k.lower() in page.lower(), k
    for bad in ("guaranteed", "below market", "motivated seller", "hot deal", "steal"):
        assert bad not in page.lower(), bad
    nav = (DOCS / "nav.js").read_text()
    assert '"foreclosures.html"' in nav
    ph = (DOCS / "ph.js").read_text()
    assert "FORECLOSURE_SALE_NOTICED" in ph and "not an outcome" in ph
    look = (DOCS / "lookup.html").read_text()
    assert "data/foreclosures.json" in look and "A notice is not an outcome" in look
    wb = (ROOT / "tools" / "weekly_brief.py").read_text()
    assert "foreclosures.json" in wb and "not an outcome" in wb
    ps = (ROOT / "tools" / "publish_scan.py").read_text()
    assert "build_foreclosures.py" in ps and "docs/data/foreclosures.json" in ps
