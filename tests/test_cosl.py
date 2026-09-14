"""Commissioner of State Lands adapter - parsing and joins, fully offline."""
from hunter.sources import cosl

SEARCH_HIT = """
<table><tr><th>County Name</th><th>Parcel Number</th><th>Last Name</th><th>First Name</th><th>Actions</th></tr>
<tr><td>GARLAND</td><td>60606</td><td>SERVICES CORPORATION</td><td>AFWC</td>
<td><a href="/WebParcels/GetParcel/839609">View</a></td></tr></table>"""
SEARCH_MISS = "<div>Parcel Not found. Please check and retry.</div>"
PARCEL_PAGE = """<html><body>Parcel Information - 839609 County: GARLAND Year of Delinquency: 2021 Code: 49-9
Parcel Number: 60606 Legal Description: . Section: 02 Township: 01S Range: 19W Lot: 5 Block: 5 Addition: TARRAGONA
Owner: AFWC SERVICES CORPORATION 5205 HAVERILL DR Tax Years Delinquent: 2020 - 2024 Total Due: $131.15
IF YOU DO NOT ALREADY OWN THE PROPERTY, PAYING THE DELINQUENT TAXES WILL NOT GIVE YOU OWNERSHIP</body></html>"""


class _Resp:
    def __init__(self, text, url="https://cosl.org/x"):
        self.text, self.url = text, url
    def raise_for_status(self):
        pass


class _Client:
    """Stand-in for httpx.Client that serves canned pages."""
    pages = {}
    def __init__(self, *a, **k):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get(self, url, params=None):
        for k, v in self.pages.items():
            if k in url:
                return _Resp(v, url)
        return _Resp("", url)
    def post(self, url, data=None, headers=None):
        return _Resp(self.pages.get("POST", ""), url)


def _patch_httpx(monkeypatch, pages):
    import httpx
    _Client.pages = pages
    monkeypatch.setattr(httpx, "Client", _Client)


def test_parcel_status_reads_years_and_total_due(monkeypatch):
    _patch_httpx(monkeypatch, {"SearchByParcel": '<input name="__RequestVerificationToken" type="hidden" value="tok" />',
                               "POST": SEARCH_HIT, "GetParcel": PARCEL_PAGE})
    st = cosl.parcel_status("60606")
    assert st["ok"] and st["certified"]
    assert st["hits"][0]["url"].endswith("/WebParcels/GetParcel/839609")
    assert st["years_delinquent"] == "2020 - 2024"
    assert st["total_due"] == "$131.15"
    assert st["delinquent_year"] == "2021"


def test_parcel_status_not_certified(monkeypatch):
    _patch_httpx(monkeypatch, {"SearchByParcel": '<input name="__RequestVerificationToken" type="hidden" value="tok" />',
                               "POST": SEARCH_MISS})
    st = cosl.parcel_status("35593")
    assert st["ok"] and st["certified"] is False and "hits" not in st


def test_parcels_for_rpids_joins_by_camakey_and_by_parcel_id(monkeypatch):
    calls = []
    def fake_query(service, layer, *, where, out_fields, geometry=False, extra=None, **kw):
        calls.append(where)
        if "camakey" in where:
            return {"features": [{"attributes": {"parcelid": "400-06700-005-000", "camakey": 35593.0,
                                                 "totalvalue": 6050.0},
                                  "centroid": {"x": -93.0567, "y": 34.5267},
                                  "geometry": {"rings": [[[-93.057, 34.526], [-93.056, 34.526],
                                                          [-93.056, 34.527], [-93.057, 34.526]]]}}]}
        return {"features": [{"attributes": {"parcelid": "137-00019-000", "camakey": 293030.0, "totalvalue": 1.0},
                              "centroid": {"x": -93.1, "y": 35.0}, "geometry": {"rings": []}}]}
    monkeypatch.setattr(cosl, "arcgis_query", fake_query)
    monkeypatch.setattr(cosl.time, "sleep", lambda s: None)
    out = cosl.parcels_for_rpids(["35593", "137-00019-000"], "05051")
    assert out["35593"]["parcelid"] == "400-06700-005-000"
    assert out["35593"]["extent"] == [-93.057, 34.526, -93.056, 34.527]
    assert out["137-00019-000"]["lat"] == 35.0
    assert any("camakey IN (35593)" in w for w in calls)
    assert any("parcelid IN ('137-00019-000')" in w for w in calls)


def test_discover_builds_honest_records(monkeypatch):
    monkeypatch.setattr(cosl, "listings", lambda county: [
        {"Owner": "AFWC SERVICES CORPORATION", "CoSLParcelNumber": "60606", "Acreage": 0.0,
         "StartingBid": 141.47, "CurrentBid": 0.0, "NumberOfBids": 0, "SaleType": "S2",
         "End": None, "Added": "2026-09-05T06:00:06", "ListingToken": "tok", "GisId": 6060605051}])
    monkeypatch.setattr(cosl, "parcels_for_rpids", lambda rpids, fips: {
        "60606": {"parcelid": "200-63950-063-000", "ownername": "AFWC SERVICES CORPORATION",
                  "adrlabel": "  MORA LN", "adrcity": "RURAL", "totalvalue": 1000.0, "landvalue": 1000.0,
                  "impvalue": 0.0, "parcellgl": "LOT 5", "parceltype": "RV", "lat": 34.6, "lon": -93.1}})
    res = cosl.StateLandsListings().discover()
    assert res.status == cosl.OK and len(res.records) == 1
    rec = res.records[0]
    assert rec.fields["parcel_id"] == "200-63950-063-000"
    assert rec.fields["tax_status"] == "CERTIFIED_TO_STATE_FOR_SALE"
    assert rec.fields["address"] == "Mora Ln"
    fields = {e["field"]: e for e in rec.evidence}
    assert "tax_delinquent" in fields and fields["tax_delinquent"]["evidence_type"] == "FACT"
    assert "[key cosl:60606]" in fields["tax_delinquent"]["raw_ref"]
    assert fields["tax_amount_owed"]["value"] == "141.47"
    assert "tax_sale_bidding" not in fields          # nobody has bid, so no claim that somebody has


def test_scanner_removal_detection_keys_on_the_listing(boundaries, monkeypatch):
    """A parcel that leaves the inventory is marked sold-or-redeemed, one that stays is not."""
    from hunter import store
    from hunter.scanner import Scan
    from tests.conftest import make_record
    pid, _, _ = store.ingest(make_record(parcel_id="200-63950-063-000", rpid="60606"))
    store.store_evidence(pid, [{"field": "tax_delinquent", "value": "certified", "evidence_type": "FACT",
                               "confidence": "HIGH", "source": "cosl_listings", "source_name": "COSL",
                               "raw_ref": "[key cosl:60606] sale type S2"}])
    scan = Scan(mode="city_registers")
    assert scan._state_lands_removals({"cosl:60606"}) == 0
    assert scan._state_lands_removals({"cosl:99999"}) == 1
    assert store.latest_evidence(pid, "tax_delinquent_removed")
    assert scan._state_lands_removals({"cosl:99999"}) == 0      # not reported twice


def test_join_falls_back_to_normalized_and_owner_matches(monkeypatch):
    """Pulaski prints 44L0920003001 for the State's 44L-092.00-030.01; Chicot's prefix differs but the owner agrees."""
    def fake_query(service, layer, *, where, out_fields, geometry=False, extra=None, **kw):
        if "parcelid IN" in where:
            return {"features": []}                      # nothing exact
        if "0920003001" in where:
            return {"features": [{"attributes": {"parcelid": "44L-092.00-030.01", "camakey": 2937540.0, "ownername": "MORTON JALETTE"},
                                  "centroid": {"x": -92.3, "y": 34.7}, "geometry": {"rings": []}}]}
        if "04031" in where:
            return {"features": [{"attributes": {"parcelid": "010-04031-000C", "camakey": 1.0, "ownername": "ROARK GIDION THOMAS JR"},
                                  "centroid": {"x": -91.3, "y": 33.3}, "geometry": {"rings": []}},
                                 {"attributes": {"parcelid": "010-04031-000", "camakey": 2.0, "ownername": "SMITH JOHN"},
                                  "centroid": {"x": -91.3, "y": 33.3}, "geometry": {"rings": []}}]}
        return {"features": []}
    monkeypatch.setattr(cosl, "arcgis_query", fake_query)
    monkeypatch.setattr(cosl.time, "sleep", lambda s: None)
    out = cosl.parcels_for_rpids(["44L0920003001", "050-04031-000", "999-00001-000"], "05119",
                                 owners={"050-04031-000": "GIDION THOMAS  ROARK JR"})
    assert out["44L0920003001"]["join"] == "normalized"
    assert out["050-04031-000"]["parcelid"] == "010-04031-000C" and out["050-04031-000"]["join"] == "owner"
    assert "999-00001-000" not in out                  # no evidence, no guess
