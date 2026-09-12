"""Scoring transparency and financial arithmetic (spec 24, 25, 28, 29)."""
import pytest

from conftest import make_record
from hunter import distress, finance, scoring, store


def _scored(**over):
    pid, _, _ = store.ingest(make_record(**over))
    p = store.get_property(pid)
    distress.refresh(p)
    p = store.get_property(pid)
    return p, scoring.compute(p)


def test_every_score_shows_its_working():
    p, out = _scored()
    for kind in ("overall", "rental", "land", "storage", "business", "workshop",
                 "snowcone", "risk", "resale"):
        sheet = out[kind]
        assert 0 <= sheet["score"] <= 100
        assert sheet["lines"], f"{kind} produced a score with no explanation"
        for line in sheet["lines"]:
            assert line["reason"]
            assert isinstance(line["points"], (int, float))
        assert sheet["confidence"] in ("HIGH", "MEDIUM", "LOW")


def test_unknowns_are_declared_not_hidden():
    p, out = _scored()
    unknowns = " ".join(out["overall"]["unknowns"]).lower()
    assert "title" in unknowns
    assert "condition" in unknowns


def test_risk_does_not_peg_at_100_for_an_ordinary_property():
    p, out = _scored()
    assert out["risk"]["score"] < 100


def test_no_access_plus_flood_is_do_not_touch():
    pid, _, _ = store.ingest(make_record(flood_zone="AE"))
    store.store_evidence(pid, [{"field": "legal_access",
                                "value": "no road mapped near this parcel",
                                "evidence_type": "OBSERVATION", "confidence": "LOW",
                                "source": "osm_overpass"}])
    p = store.get_property(pid)
    distress.refresh(p)
    p = store.get_property(pid)
    out = scoring.compute(p)
    assert out["recommendation"] == "DO NOT TOUCH"


def test_excluded_property_is_always_a_pass(boundaries):
    p, out = _scored(lat=34.657, lon=-92.97, parcel_id="999-9")
    assert out["recommendation"] == "PASS"


def test_vacant_land_scores_higher_on_land_than_on_rental():
    p, out = _scored(parcel_type="RV", improved=0, imp_value=0.0,
                     property_type="lot", acreage=3.0)
    assert out["land"]["score"] > out["rental"]["score"]


# ------------------------------------------------------------------ finance

def test_rental_arithmetic_is_internally_consistent():
    r = finance.rental_analysis(purchase_price=45000, monthly_rent=850,
                                rehab=25000, assessed_value=38000)
    inc = r["income"]
    parts = (inc["vacancy"] + inc["taxes"] + inc["insurance"] + inc["maintenance"]
             + inc["reserves"] + inc["management"])
    assert abs(parts - inc["operating_expenses"]) < 0.02
    assert abs((inc["gross_annual"] - inc["operating_expenses"]) - inc["noi"]) < 0.02
    b = r["basis"]
    total = (b["purchase_price"] + b["rehab"] + b["closing_costs"] + b["title_and_legal"]
             + b["carrying_costs"] + b["financing_costs"] + b["contingency"])
    assert abs(total - b["total_basis"]) < 0.02


def test_break_even_rent_actually_breaks_even():
    r = finance.rental_analysis(purchase_price=45000, monthly_rent=850, rehab=25000)
    be = r["returns"]["break_even_rent"]
    at_be = finance.rental_analysis(purchase_price=45000, monthly_rent=be, rehab=25000)
    assert abs(at_be["returns"]["annual_cash_flow"]) < 60


def test_zero_rent_does_not_explode():
    r = finance.rental_analysis(purchase_price=45000, monthly_rent=0, rehab=0)
    assert r["returns"]["annual_cash_flow"] < 0
    assert r["returns"]["gross_rent_multiplier"] == 0


def test_max_price_is_ordered_and_honest_when_impossible():
    m = finance.max_purchase_price(after_repair_value=125000, rehab=35000)
    assert m["aggressive"] < m["reasonable"] < m["maximum"]
    assert m["works_at_any_price"]
    bad = finance.max_purchase_price(after_repair_value=50000, rehab=120000)
    assert bad["works_at_any_price"] is False
    assert "no price that works" in bad["plain_english"][0].lower()


def test_max_price_respects_target_return():
    low = finance.max_purchase_price(after_repair_value=125000, rehab=30000,
                                     desired_return=0.10)
    high = finance.max_purchase_price(after_repair_value=125000, rehab=30000,
                                      desired_return=0.40)
    assert low["aggressive"] > high["aggressive"]


def test_every_analysis_carries_a_caveat():
    for result in (
        finance.rental_analysis(purchase_price=1, monthly_rent=1),
        finance.storage_analysis(acreage=2),
        finance.snowcone_analysis(road_rank=7),
        finance.rehab_estimate(1000),
    ):
        assert result.get("caveat"), "an estimate was returned with no caveat"


def test_storage_scales_with_land():
    small = finance.storage_analysis(acreage=0.5)
    big = finance.storage_analysis(acreage=5.0)
    assert big["estimated_units_10x10"] > small["estimated_units_10x10"]


def test_snowcone_traffic_drives_revenue_and_competition_reduces_it():
    quiet = finance.snowcone_analysis(road_rank=1)
    busy = finance.snowcone_analysis(road_rank=8)
    crowded = finance.snowcone_analysis(road_rank=8, competitors=4)
    assert busy["estimated_season_revenue"] > quiet["estimated_season_revenue"]
    assert crowded["estimated_season_revenue"] < busy["estimated_season_revenue"]
