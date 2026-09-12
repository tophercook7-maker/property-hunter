"""The AI figure guard, aerial change detection, the CLI, watch fields and
property-aware glossary (spec 13, 35, 41, 50, 52, 86)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from conftest import make_record
from hunter import ai, db, distress, scoring, store
from hunter.imgdiff import compare


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def prop(boundaries):
    pid, _, _ = store.ingest(make_record())
    distress.refresh(store.get_property(pid))
    scoring.compute(store.get_property(pid))
    return store.get_property(pid)


# -------------------------------------------------------------------- guard

EVIDENCE = ("county assessed total: $24,250\n land value: 23200.0\n improvement value: 1050.0\n"
            " as of 2017\n building_footprint_sqft: 1917\n acreage: 2.154")


def test_guard_keeps_figures_that_are_in_the_evidence():
    text = "Assessed at $24,250 (land $23,200, improvements $1,050) as of 2017, about 1,917 sq ft."
    out, removed = ai.guard(text, EVIDENCE)
    assert out == text and removed == []


def test_guard_strips_invented_money_years_and_sizes():
    text = ("The rehab will run $18,000 and the house was built in 1962; it is roughly "
            "2,400 square feet. The assessed value is $24,250.")
    out, removed = ai.guard(text, EVIDENCE)
    assert "$18,000" not in out and "1962" not in out and "2,400" not in out
    assert "$24,250" in out
    assert out.count("[figure not in our evidence - removed]") == 3
    assert set(removed) == {"$18,000", "1962", "2,400 square feet"}


def test_guard_labels_do_not_carry_punctuation():
    _, removed = ai.guard("Maybe $9,000, or $12,000.", EVIDENCE)
    assert removed == ["$9,000", "$12,000"]


def test_explain_applies_the_guard_and_reports_it(prop, monkeypatch):
    from hunter import analyzers
    monkeypatch.setattr(ai, "ask", lambda prompt, **kw:
                        ("Nice lot. I'd budget $45,000 for the roof. Assessed $24,250.", "fake"))
    r = analyzers.explain(prop, use_ai=True)
    assert "$45,000" not in r["text"] and "$24,250" in r["text"]
    assert r["removed_figures"] == ["$45,000"]
    assert "made up were removed" in r["guard_note"]


def test_what_would_you_do_is_guarded_too(prop, monkeypatch):
    from hunter import analyzers
    monkeypatch.setattr(ai, "ask", lambda prompt, **kw:
                        ("Not yet. Offer $30,000 once title is clear.", "fake"))
    r = analyzers.what_would_you_do(prop, use_ai=True)
    assert "$30,000" not in r["text"] and r["removed_figures"] == ["$30,000"]


# ------------------------------------------------------------------ imgdiff

def _img(path: Path, box=None):
    im = Image.new("RGB", (400, 400), (70, 110, 60))
    d = ImageDraw.Draw(im)
    for y in range(0, 400, 25):                       # texture so autocontrast has range
        d.line([(0, y), (400, y + 12)], fill=(60, 95, 50), width=2)
    if box:
        d.rectangle(box, fill=(200, 200, 200))
    im.save(path, "JPEG", quality=90)
    return path


def test_identical_flights_read_as_no_change(tmp_path):
    a = _img(tmp_path / "a.jpg", box=(150, 150, 250, 250))
    b = _img(tmp_path / "b.jpg", box=(150, 150, 250, 250))
    r = compare(a, b)
    assert r["centre_changed_fraction"] < 0.05 and not r["notable"]
    assert r["summary"] == "looks much the same"


def test_a_building_appearing_at_the_centre_is_notable(tmp_path):
    before = _img(tmp_path / "before.jpg")
    after = _img(tmp_path / "after.jpg", box=(140, 140, 260, 260))
    r = compare(before, after)
    assert r["notable"] and r["centre_changed_fraction"] > 0.18
    assert "centre" in r["summary"] or "near the parcel" in r["summary"]


def test_a_neighbours_new_roof_at_the_edge_is_not_the_parcel_changing(tmp_path):
    """A bright new building along one edge (~15% of the frame) must neither be
    normalised away nor be blamed on the parcel in the middle."""
    before = _img(tmp_path / "before.jpg")
    after = _img(tmp_path / "after.jpg", box=(0, 0, 400, 60))   # neighbour, top edge
    r = compare(before, after)
    assert not r["notable"], r
    assert r["centre_changed_fraction"] < 0.05
    assert r["changed_fraction"] > 0.08                          # the change IS seen


def test_then_vs_now_is_recorded_as_a_calculation_and_alerts_when_notable(prop, tmp_path,
                                                                            monkeypatch):
    from hunter.sources import imagery as im
    _img(tmp_path / "2017.jpg")
    _img(tmp_path / "2023.jpg", box=(140, 140, 260, 260))
    for year in ("2017", "2023"):
        db.ex("INSERT INTO photos(property_id,kind,url,local_path,source,captured_at,created_at) "
              "VALUES(?,?,?,?,?,?,?)", (prop["id"], "aerial", f"/x/{year}.jpg",
                                        str(tmp_path / f"{year}.jpg"), "Arkansas GIS Office T",
                                        year, "now"))
    ev = im.AR_IMAGERY._then_vs_now(prop)
    assert ev and ev[0]["field"] == "aerial_change"
    assert ev[0]["evidence_type"] == "CALCULATION" and ev[0]["confidence"] == "LOW"
    assert "not what" in ev[0]["raw_ref"]
    assert db.q1("SELECT 1 FROM alerts WHERE property_id=? AND kind='imagery_changed'", (prop["id"],))
    assert db.q1("SELECT 1 FROM timeline WHERE property_id=? AND kind='imagery'", (prop["id"],))


# ---------------------------------------------------------------------- CLI

def test_cli_works_without_a_server(prop, capsys, monkeypatch):
    from hunter import cli
    monkeypatch.setattr(cli, "_server", lambda: None)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "properties 1" in out and "direct" in out
    assert cli.main(["picks", "--limit", "1"]) == 0
    assert "111 Isabelle St" in capsys.readouterr().out
    assert cli.main(["ask", "what should I investigate"]) == 0
    assert "[investigate]" in capsys.readouterr().out
    assert cli.main(["--json", "ask", "show me everything"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["intent"] == "search" and data["results"][0]["address"] == "111 Isabelle St"
    assert cli.main(["explain", str(prop["id"]), "--no-ai"]) == 0
    assert "simple version" in capsys.readouterr().out
    assert cli.main(["explain", "999999", "--no-ai"]) == 2
    assert cli.main(["briefing"]) == 0
    assert "Good morning" in capsys.readouterr().out


# ------------------------------------------------------- watch fields (35)

def test_watch_fields_round_trip_into_the_list(client, prop):
    r = client.post(f"/api/property/{prop['id']}/watch",
                    json={"priority": "high", "notes": "call the estate", "target_price": 30000,
                          "desired_use": "rental"})
    assert r.status_code == 200
    lst = client.get("/api/properties?watchlist=true").json()["properties"][0]
    assert lst["watch_priority"] == "high" and lst["watch_target_price"] == 30000
    assert lst["watch_desired_use"] == "rental" and lst["watch_notes"] == "call the estate"
    d = client.get(f"/api/property/{prop['id']}").json()
    assert d["watched"] and d["watch"][0]["priority"] == "high"


# ------------------------------------------------------- what's this (41)

def test_glossary_uses_the_current_property_as_the_example(client, prop):
    r = client.get(f"/api/education?term=assessed value&property_id={prop['id']}").json()
    assert r["found"] and "111 Isabelle St" in r["example"] and "$24,250" in r["example"]
    r = client.get(f"/api/education?term=zoning&property_id={prop['id']}").json()
    assert "have not confirmed zoning" in r["example"]
