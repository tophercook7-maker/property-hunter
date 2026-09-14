"""County Collector balances via CountyPay - parsing and honesty, offline."""
from pathlib import Path

from hunter.sources import countypay

FIX = Path(__file__).parent / "fixtures"


def test_parcel_key_is_R_plus_digits():
    assert countypay.parcel_key("013-00002-001") == "R01300002001"
    assert countypay.parcel_key("400-06700-005-000") == "R40006700005000"
    assert countypay.parcel_key("") == ""


def test_parse_results_reads_category_type_and_amount():
    res = countypay.parse_results((FIX / "countypay_results.html").read_text())
    assert res["available"]
    keys = {b["key"]: b for b in res["bills"]}
    assert "R01300002001" in keys and "P2200800" in keys
    real = keys["R01300002001"]
    assert real["category"] == "Current" and real["type"] == "Real Estate" and real["amount"] == 576.62
    assert "Mcdade" in real["name"] and "F G Jones" in real["address"]


def test_parse_unavailable_is_honest():
    res = countypay.parse_results((FIX / "countypay_unavailable.html").read_text())
    assert res["available"] is False and "unavailable" in res["message"].lower() and res["bills"] == []


def test_parse_no_match():
    res = countypay.parse_results("<div>Please correct the following errors: No taxpayers matched your search criteria.</div>")
    assert res["available"] and res["bills"] == []


def test_enrich_reports_open_bill_or_none(monkeypatch):
    src = countypay.CountyPayTaxes()
    prop = {"id": 1, "county_fips": "05051", "parcel_id": "400-06700-005-000"}
    monkeypatch.setattr(countypay, "lookup", lambda slug, pid: {
        "available": True, "message": "1 open bill(s)", "checked_at": "2026-09-14T20:00:00+00:00", "key": "R40006700005000",
        "bills": [{"key": "R40006700005000", "name": "QUEST IRA", "address": "267 Glade", "category": "Delinquent",
                   "type": "Real Estate", "amount": 212.40}]})
    res = src.enrich(prop)
    assert res.status == countypay.OK and "DELINQUENT" in res.detail
    ev = {e["field"]: e for e in res.records[0].evidence}
    assert ev["tax_bill"]["evidence_type"] == "FACT" and "$212.40" in ev["tax_bill"]["value"]
    assert "tax_delinquent_county" in ev and res.records[0].fields["tax_status"] == "DELINQUENT"
    monkeypatch.setattr(countypay, "lookup", lambda slug, pid: {"available": True, "message": "none", "bills": [],
                                                                 "checked_at": "2026-09-14T20:00:00+00:00", "key": "R1"})
    res = src.enrich(prop)
    assert res.status == countypay.OK and "no open" in res.detail
    assert res.records[0].evidence[0]["evidence_type"] == "OBSERVATION"
    monkeypatch.setattr(countypay, "lookup", lambda slug, pid: {"available": False, "message": "Online payments are currently unavailable.",
                                                                 "bills": [], "checked_at": "x", "key": "R1"})
    res = src.enrich(prop)
    assert res.status == countypay.UNAVAILABLE and "unavailable" in res.detail
