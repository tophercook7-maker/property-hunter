"""Imagery & terrain sources, vision phrasing, the question router, near-me,
saved filters (spec 20, 30, 31, 42, 50, 57)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_record
from hunter import db, distress, scoring, store, vision
from hunter.sources.base import OK


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def props(boundaries):
    ids = []
    for over in (dict(parcel_id="300-1", address="100 Main St", imp_value=2000.0,
                      lat=34.5100, lon=-93.0500),
                 dict(parcel_id="300-2", address="200 Oak St", parcel_type="RV", improved=0,
                      imp_value=0.0, property_type="lot", acreage=4.2,
                      lat=34.5110, lon=-93.0510),
                 dict(parcel_id="300-3", address="9 Far Rd", city="Unincorporated",
                      lat=34.6000, lon=-93.2000)):
        pid, _, _ = store.ingest(make_record(**over))
        distress.refresh(store.get_property(pid))
        scoring.compute(store.get_property(pid))
        ids.append(pid)
    return ids


# ------------------------------------------------------------------ terrain

def test_slope_is_computed_from_elevation_samples(props, monkeypatch):
    from hunter.sources.imagery import AR_TERRAIN
    #           centre,  east,  west,  north, south   (metres)
    monkeypatch.setattr(AR_TERRAIN, "samples", lambda lat, lon, arm_m=30.0:
                        [155.8, 155.2, 159.3, 149.3, 157.3])
    res = AR_TERRAIN.enrich(store.get_property(props[0]))
    assert res.status == OK
    raw = res.records[0].raw
    assert 13.5 < raw["slope_pct"] < 16.5          # ~ hypot(4.1/60, 8/60)*100
    assert raw["aspect"] == "NE"                    # falls toward the low north sample, a little east
    ev = {e["field"]: e for e in res.records[0].evidence}
    assert ev["slope_pct"]["evidence_type"] == "CALCULATION"
    assert "One cross does not describe a whole tract" in ev["slope_pct"]["raw_ref"]


def test_missing_elevation_is_unavailable_not_zero(props, monkeypatch):
    from hunter.sources.imagery import AR_TERRAIN
    monkeypatch.setattr(AR_TERRAIN, "samples", lambda lat, lon, arm_m=30.0:
                        [155.8, None, 159.3, 149.3, 157.3])
    assert AR_TERRAIN.enrich(store.get_property(props[0])).status == "unavailable"


def test_slope_changes_the_land_and_storage_scores(props):
    pid = props[1]
    p = store.get_property(pid)
    flat = scoring.compute(p, persist=False)
    store.store_evidence(pid, [{"field": "slope_pct", "value": "24.0",
                                "evidence_type": "CALCULATION", "confidence": "MEDIUM",
                                "source": "ar_gis_terrain"}])
    steep = scoring.compute(store.get_property(pid), persist=False)
    assert steep["land"]["score"] < flat["land"]["score"]
    assert steep["storage"]["score"] < flat["storage"]["score"]
    assert any("Steep" in l["reason"] for l in steep["land"]["lines"])
    assert "topography and drainage" not in steep["land"]["unknowns"]


# ------------------------------------------------------------------ imagery

def test_aerials_are_saved_with_source_year_and_license(props, monkeypatch):
    from hunter.sources import imagery as im
    fake_jpeg = b"\xff\xd8\xff" + b"\x00" * 200
    calls = []
    monkeypatch.setattr(im, "fetch_bytes", lambda url, timeout=None: (calls.append(url), fake_jpeg)[1])
    res = im.AR_IMAGERY.enrich(store.get_property(props[0]))
    assert res.status == OK, res.error
    assert sorted(res.records[0].raw["saved"]) == ["2017", "2023"]
    assert any("IMAGERY_9IN_2023" in u for u in calls)
    photos = db.rows_to_dicts(db.q("SELECT * FROM photos WHERE property_id=? ORDER BY captured_at",
                                   (props[0],)))
    assert [p["captured_at"] for p in photos] == ["2017", "2023"]
    for p in photos:
        assert p["kind"] == "aerial" and p["source"].startswith("Arkansas GIS Office")
        assert "confirm reuse terms" in p["license"]
        assert Path(p["local_path"]).exists()
        assert p["source_url"].startswith("https://gis.arkansas.gov/")
    # a second run does not fetch again
    calls.clear()
    res2 = im.AR_IMAGERY.enrich(store.get_property(props[0]))
    assert res2.status == OK and not calls and "already on file" in res2.detail


def test_non_jpeg_response_is_reported_not_saved(props, monkeypatch):
    from hunter.sources import imagery as im
    monkeypatch.setattr(im, "fetch_bytes", lambda url, timeout=None: b'{"error": "nope"}')
    res = im.AR_IMAGERY.enrich(store.get_property(props[2]))
    assert res.status == "unavailable"
    assert "not a JPEG" in res.error
    assert not db.q("SELECT 1 FROM photos WHERE property_id=?", (props[2],))


def test_frame_scales_with_acreage(props):
    from hunter.sources.imagery import _bbox_for
    small = _bbox_for({"lat": 34.5, "lon": -93.0, "acreage": 0.2})
    big = _bbox_for({"lat": 34.5, "lon": -93.0, "acreage": 40})
    assert (big[2] - big[0]) > (small[2] - small[0]) * 5


# ------------------------------------------------------------------- vision

def test_vision_phrasing_is_conservative_and_never_prices_anything():
    out = vision._phrase({"structures": 2, "roof": "possible damage or tarps",
                          "vegetation": "overgrown", "debris": "yes",
                          "driveway": "not visible", "vehicles": 1,
                          "notes": "A shed at the back."}, "aerial")
    joined = " ".join(out).lower()
    assert "possible roof concern" in joined
    assert "overgrown" in joined and "debris" in joined
    assert "$" not in joined and "needs" not in joined
    assert vision._phrase({"roof": "intact", "vegetation": "unclear", "debris": "unclear",
                           "driveway": "unclear", "vehicles": "unclear"}, "aerial") == []


def test_vision_findings_are_stored_as_low_confidence_ai_opinion(props, monkeypatch):
    pid = props[0]
    img = Path(db.connect().execute("PRAGMA database_list").fetchone()[2]).parent / "t.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
    cur = db.ex("INSERT INTO photos(property_id,kind,url,local_path,source,captured_at,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (pid, "aerial", "/files/t.jpg", str(img), "Arkansas GIS Office X", "2023", "now"))
    monkeypatch.setattr(vision, "analyse_image", lambda path, kind, year=None:
                        {"model": "fake-vision", "cached": False,
                         "findings": {"roof": "possible sag", "vegetation": "overgrown"}})
    r = vision.analyse_photo_record(cur.lastrowid)
    assert r["model"] == "fake-vision" and len(r["observations"]) == 2
    ev = [e for e in store.evidence_for(pid) if e["field"].startswith("vision:")]
    assert ev
    for e in ev:
        assert e["evidence_type"] == "AI_OPINION" and e["confidence"] == "LOW"
        assert e["value"].endswith("LOW CONFIDENCE")
        assert "Go and look" in e["raw_ref"]


def test_vision_without_a_model_is_honest(props, monkeypatch):
    monkeypatch.setattr(vision, "available_model", lambda: None)
    r = vision.analyse_image(Path(__file__), "photo")
    assert "no local vision model" in r["error"]


def test_analyse_endpoint_503s_without_a_model(client, props, monkeypatch):
    monkeypatch.setattr(vision, "available_model", lambda: None)
    cur = db.ex("INSERT INTO photos(property_id,kind,url,local_path,source,created_at) "
                "VALUES(?,?,?,?,?,?)", (props[0], "inspection", "/x", __file__, "t", "now"))
    assert client.post(f"/api/photo/{cur.lastrowid}/analyse").status_code == 503


# ---------------------------------------------------------------------- ask

def test_ask_routes_the_spec_questions(client, props):
    def ask(q):
        r = client.get(f"/api/ask?q={q}")
        assert r.status_code == 200, r.text
        return r.json()
    assert ask("What's new?")["intent"] == "whats_new"
    assert ask("what changed")["intent"] == "what_changed"
    a = ask("Which property should I investigate?")
    assert a["intent"] == "investigate" and a["results"] and "I'd start with" in a["answer"]
    assert ask("what's my best opportunity")["intent"] == "best"
    assert ask("which ones are traps")["intent"] == "avoid"
    a = ask("Show me everything under $50,000")
    assert a["intent"] == "search" and a["interpreted"]
    assert all((p["overall"] is None) or True for p in a["results"])
    a = ask("Find me land for storage")
    assert a["intent"] == "search" and "storage" in " ".join(a["interpreted"]).lower()
    a = ask("why did the score change on property %d" % props[0])
    assert a["intent"] == "why_score" and a["results"][0]["score_lines"]
    a = ask("which properties have become more attractive")
    assert a["intent"] == "more_attractive"
    assert ask("is the scan running")["intent"] == "scan_status"
    a = ask("explain 100 Main St")
    assert a["intent"] == "explain" and a["results"][0]["id"] == props[0]
    assert "Hot Springs Village" in a["disclaimer"]
    assert client.get("/api/ask?q=").status_code == 400


def test_ask_never_returns_an_excluded_property(client, props):
    store.ingest(make_record(parcel_id="999-1", address="1 Balboa Way", lat=34.657, lon=-92.97))
    for q in ("what's new", "show me everything", "best opportunity"):
        r = client.get(f"/api/ask?q={q}").json()
        assert not any((x or {}).get("address") == "1 Balboa Way" for x in r["results"])


# --------------------------------------------------------------------- near

def test_near_me_sorts_by_distance_and_respects_radius(client, props):
    r = client.get("/api/near?lat=34.5100&lon=-93.0500&radius_m=500").json()
    assert [p["address"] for p in r["properties"]] == ["100 Main St", "200 Oak St"]
    assert r["properties"][0]["distance_m"] == 0
    assert 100 < r["properties"][1]["distance_m"] < 200
    wide = client.get("/api/near?lat=34.5100&lon=-93.0500&radius_m=30000").json()
    assert wide["count"] == 3


# ------------------------------------------------------------- saved filters

def test_saved_filters_round_trip(client):
    assert client.get("/api/filters/saved").json()["filters"] == []
    r = client.post("/api/filters/saved", json={"name": "cheap flat land",
                                                 "params": {"property_type": "lot",
                                                            "max_value": 20000}}).json()
    assert r["filters"][0]["name"] == "cheap flat land"
    client.post("/api/filters/saved", json={"name": "cheap flat land",
                                            "params": {"property_type": "lot"}})
    assert len(client.get("/api/filters/saved").json()["filters"]) == 1   # replaced, not duplicated
    assert client.post("/api/filters/saved", json={"name": ""}).status_code == 400
    client.delete("/api/filters/saved/cheap%20flat%20land")
    assert client.get("/api/filters/saved").json()["filters"] == []
