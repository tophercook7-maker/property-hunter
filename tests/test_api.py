"""API surface - the contract Daniel / AI Hub consumes (spec 50, 57, 66)."""
import pytest
from fastapi.testclient import TestClient

from conftest import make_record
from hunter import distress, scoring, store


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded(boundaries):
    ids = []
    for i, over in enumerate([
        dict(parcel_id="300-00100-000", address="100 Main St", imp_value=2000.0),
        dict(parcel_id="300-00101-000", address="200 Oak St", parcel_type="RV",
             improved=0, imp_value=0.0, property_type="lot", acreage=4.2),
        dict(parcel_id="300-00102-000", address="300 Pine St", city="Unincorporated",
             owner_name="SMITH, JOHN ESTATE"),
        dict(parcel_id="999-00001-000", address="1 Balboa Way", lat=34.657, lon=-92.97),
    ]):
        pid, _, _ = store.ingest(make_record(**over))
        p = store.get_property(pid)
        distress.refresh(p)
        scoring.compute(store.get_property(pid))
        ids.append(pid)
    return ids


def test_status_reports_honestly(client, seeded):
    d = client.get("/api/status").json()
    assert d["counts"]["properties"] == 3        # the HSV one is excluded
    assert d["counts"]["excluded"] == 1
    assert d["territory"]["county_fips"] == "05051"
    assert len(d["exclusions"]) == 2
    assert "not legal or financial advice" in d["disclaimer"]


def test_properties_filtering(client, seeded):
    assert client.get("/api/properties?property_type=lot").json()["total"] == 1
    assert client.get("/api/properties?min_acres=4").json()["total"] == 1
    assert client.get("/api/properties?q=oak").json()["total"] == 1
    assert client.get("/api/properties?q=ESTATE").json()["total"] == 1
    assert client.get("/api/properties?has_distress=true").json()["total"] >= 1
    assert client.get("/api/properties?limit=99").json()["total"] == 3


def test_natural_language_search_explains_itself(client, seeded):
    r = client.get("/api/search/natural?q=land over 2 acres").json()
    assert r["interpretation"]["filters"]["min_acres"] == 2.0
    assert r["interpretation"]["interpreted"]
    assert "Hot Springs Village" in r["interpretation"]["note"]
    assert all(p["acreage"] >= 2 for p in r["properties"])


def test_dossier_has_every_section(client, seeded):
    d = client.get(f"/api/property/{seeded[0]}").json()
    for key in ("property", "scores", "deal_or_trap", "why_cheap", "next_steps",
                "business_use", "evidence", "timeline", "changes", "conflicts",
                "tasks", "notes", "photos", "documents", "financials", "disclaimer"):
        assert key in d, f"dossier missing {key}"
    assert d["evidence"], "a dossier with no evidence is not a dossier"


def test_dossier_404s_cleanly(client):
    assert client.get("/api/property/999999").status_code == 404


def test_financial_endpoints(client, seeded):
    pid = seeded[0]
    r = client.post(f"/api/property/{pid}/financials",
                    json={"kind": "rental", "purchase_price": 40000,
                          "monthly_rent": 800, "rehab": 20000}).json()
    assert r["returns"]["cap_rate"] > 0
    d = client.post(f"/api/property/{pid}/financials",
                    json={"kind": "deal", "after_repair_value": 120000,
                          "rehab": 30000}).json()
    assert d["aggressive"] < d["maximum"]
    assert client.post(f"/api/property/{pid}/financials",
                       json={"kind": "nonsense"}).status_code == 400


def test_watchlist_round_trip(client, seeded):
    pid = seeded[0]
    assert client.post(f"/api/property/{pid}/watch", json={"priority": "high"}).json()["watched"]
    assert client.get("/api/properties?watchlist=true").json()["total"] == 1
    client.delete(f"/api/property/{pid}/watch")
    assert client.get("/api/properties?watchlist=true").json()["total"] == 0


def test_state_machine_rejects_nonsense(client, seeded):
    pid = seeded[0]
    assert client.post(f"/api/property/{pid}/state", json={"state": "RENTED"}).status_code == 200
    assert client.post(f"/api/property/{pid}/state", json={"state": "BANANA"}).status_code == 400


def test_pass_records_a_reason_for_learning(client, seeded):
    client.post(f"/api/property/{seeded[0]}/state",
                json={"state": "PASS", "reason": "bad title"})
    learning = client.get("/api/decisions/learning").json()
    assert learning["pass_reasons"][0]["reason"] == "bad title"
    assert "Scores never change" in learning["note"]
    assert learning["enabled"] is True
    assert "bad title" in learning["how_it_is_used"]


def test_map_excludes_and_carries_boundaries(client, seeded):
    d = client.get("/api/map").json()
    assert d["count"] == 3
    assert {b["key"] for b in d["excluded_boundaries"]} == {"hot_springs_village",
                                                            "diamondhead"}
    assert all(p["marker"] for p in d["properties"])


def test_sources_declare_what_they_cannot_do(client):
    d = client.get("/api/sources").json()["sources"]
    by_name = {s["name"]: s for s in d}
    assert by_name["ar_gis_parcels"]["access"] == "automated"
    assert by_name["cosl"]["access"] == "manual"
    assert by_name["garland_assessor"]["access"] == "blocked"
    for s in d:
        if s["access"] != "automated":
            assert s["why_manual"], f"{s['name']} is manual but does not say why"


def test_health_is_traffic_lights(client, seeded):
    d = client.get("/api/health").json()
    assert d["overall"] in ("GREEN", "YELLOW", "RED")
    for c in d["checks"]:
        assert c["status"] in ("GREEN", "YELLOW", "RED")
        assert c["detail"]


def test_approvals_gate_the_dangerous_actions(client, seeded):
    r = client.post("/api/approvals", json={"action": "make_offer",
                                            "property_id": seeded[0]})
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    assert "until you approve" in r.json()["message"]
    assert client.post("/api/approvals", json={"action": "delete_everything"}).status_code == 400
    pend = client.get("/api/approvals").json()
    assert len(pend["approvals"]) == 1
    assert "contact_seller" in pend["actions_requiring_approval"]


def test_completing_a_manual_task_stores_evidence(client, seeded):
    pid = seeded[0]
    tid = store.add_task(pid, {"title": "Check taxes", "source": "garland_tax_collector",
                               "manual": 1})
    client.post(f"/api/task/{tid}", json={"status": "done",
                                          "evidence": "Taxes current through 2025."})
    ev = [e for e in store.evidence_for(pid) if e["field"].startswith("manual:")]
    assert ev and ev[0]["confidence"] == "HIGH"
    assert ev[0]["source"] == "topher_manual_verification"


def test_field_note_is_never_promoted_to_fact(client, seeded):
    pid = seeded[0]
    client.post(f"/api/property/{pid}/note",
                json={"body": "Neighbour says nobody has lived there in years."})
    ev = [e for e in store.evidence_for(pid) if e["field"] == "field_observation"]
    assert ev
    assert ev[0]["evidence_type"] == "OBSERVATION"
    assert ev[0]["confidence"] == "LOW"


def test_csv_export(client, seeded):
    text = client.get("/api/export/properties.csv").text
    assert "address" in text.splitlines()[0]
    assert "1 Balboa Way" not in text          # excluded stays excluded everywhere


def test_education_never_gives_legal_certainty(client):
    d = client.get("/api/education?term=delinquent tax").json()
    assert "does NOT make you the owner" in d["text"]
    d2 = client.get("/api/education?term=adverse possession").json()
    assert "attorney" in d2["text"].lower()


def test_briefing_shape(client, seeded):
    d = client.get("/api/briefing").json()
    for k in ("greeting", "headline", "new_opportunities", "important_changes",
              "next_three_actions", "best_deal", "biggest_risk", "disclaimer"):
        assert k in d


def test_compare_needs_two(client, seeded):
    assert "error" in client.post("/api/compare", json={"ids": [seeded[0]]}).json()
    d = client.post("/api/compare", json={"ids": seeded[:3]}).json()
    assert d["winner"]["why"]
    assert len(d["rows"]) > 10


# ---------------------------------------------------------- learning (spec 80)

def test_passing_influences_ranking_only_in_the_open(client, seeded):
    """A pass for 'bad title' sinks estate-owned parcels in ranked lists - and the
    list says so. Scores do not move and nothing disappears."""
    estate_id = [i for i in seeded
                 if store.get_property(i)["owner_name"] == "SMITH, JOHN ESTATE"][0]
    before = client.get("/api/properties?sort=overall").json()
    assert before["preferences"]["influenced"] is False
    scores_before = {p["id"]: p["overall_score"] for p in before["properties"]}

    client.post(f"/api/property/{seeded[0]}/state",
                json={"state": "PASS", "reason": "bad title"})
    after = client.get("/api/properties?sort=overall").json()
    assert after["preferences"]["influenced"] is True
    assert "influenced this ranking" in after["preferences"]["note"]
    flagged = {p["id"]: p["preference_flags"] for p in after["properties"]}
    assert flagged[estate_id] == ["bad title"]
    assert after["properties"][-1]["id"] == estate_id      # sank to the bottom
    assert {p["id"]: p["overall_score"] for p in after["properties"]} == scores_before
    assert after["total"] == before["total"]               # nothing hidden

    # a non-ranked sort is left alone
    newest = client.get("/api/properties?sort=newest").json()
    assert newest["preferences"]["influenced"] is False

    # and it can be switched off
    client.post("/api/decisions/learning", json={"enabled": False})
    off = client.get("/api/properties?sort=overall").json()
    assert off["preferences"]["influenced"] is False
    client.post("/api/decisions/learning", json={"enabled": True})


# --------------------------------------------------------- schedule (spec 5/37)

def test_schedule_is_configurable_and_reports_next_run(client):
    cfg = client.get("/api/schedule").json()
    assert cfg["enabled"] is True and cfg["interval_hours"] == 24
    cfg = client.post("/api/schedule", json={"interval_hours": 6, "mode": "cheap_land"}).json()
    assert cfg["interval_hours"] == 6 and cfg["mode"] == "cheap_land"
    assert cfg["next_run"]
    assert client.get("/api/status").json()["scan"]["next_scheduled"] == cfg["next_run"]
    assert client.post("/api/schedule", json={"interval_hours": 0}).status_code == 400
    assert client.post("/api/schedule", json={"mode": "banana"}).status_code == 400
    off = client.post("/api/schedule", json={"enabled": False}).json()
    assert off["next_run"] is None
    client.post("/api/schedule", json={"enabled": True, "interval_hours": 24, "mode": "distress"})


# ------------------------------------------------- uploads (spec 30/42/43/44)

def test_photo_upload_is_stored_as_an_owner_observation(client, seeded):
    pid = seeded[0]
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    r = client.post(f"/api/property/{pid}/photo",
                    files={"file": ("roof.png", png, "image/png")},
                    data={"kind": "inspection", "caption": "Roof from the street"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "inspection" and body["url"].startswith("/files/")
    assert client.get(body["url"]).status_code == 200
    d = client.get(f"/api/property/{pid}").json()
    assert d["photos"][0]["source"].endswith("own photo")
    assert d["photos"][0]["license"] == "owner"
    ev = [e for e in d["evidence"] if e["field"] == "photo:inspection"]
    assert ev and ev[0]["evidence_type"] == "OBSERVATION"
    assert "behind the wall" in ev[0]["raw_ref"]


def test_photo_upload_rejects_non_images(client, seeded):
    r = client.post(f"/api/property/{seeded[0]}/photo",
                    files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_voice_note_with_typed_transcript_is_unverified_hearsay(client, seeded):
    pid = seeded[0]
    r = client.post(f"/api/property/{pid}/voice",
                    files={"file": ("n.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 32,
                                    "audio/webm")},
                    data={"transcript": "Neighbour says nobody has lived here in years."})
    assert r.status_code == 200, r.text
    assert r.json()["auto_transcribed"] is False
    d = client.get(f"/api/property/{pid}").json()
    note = [n for n in d["notes"] if n["kind"] == "voice_note"][0]
    assert note["confidence"] == "UNVERIFIED" and note["audio_path"].startswith("/files/")
    ev = [e for e in d["evidence"] if e["field"] == "field_observation"]
    assert ev and ev[0]["confidence"] == "LOW" and ev[0]["evidence_type"] == "OBSERVATION"
    assert "hearsay" in ev[0]["raw_ref"]


def test_document_upload_is_categorised(client, seeded):
    pid = seeded[0]
    r = client.post(f"/api/property/{pid}/document",
                    files={"file": ("deed.pdf", b"%PDF-1.4 fake", "application/pdf")},
                    data={"category": "deed", "title": "Warranty deed 2019"})
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "deed"
    d = client.get(f"/api/property/{pid}").json()
    assert d["documents"][0]["title"] == "Warranty deed 2019"
    bad = client.post(f"/api/property/{pid}/document",
                      files={"file": ("x.bin", b"x", "application/octet-stream")},
                      data={"category": "not-a-category"})
    assert bad.json()["category"] == "other"


def test_upload_size_limit_is_enforced(client, seeded, monkeypatch):
    from hunter import files as f
    monkeypatch.setattr(f, "MAX_BYTES", 1024)
    r = client.post(f"/api/property/{seeded[0]}/photo",
                    files={"file": ("big.png", b"\x89PNG" + b"\x00" * 5000, "image/png")})
    assert r.status_code == 413


# --------------------------------------------------------- portfolio (46-49)

def test_portfolio_ledger_and_lease_round_trip(client, seeded):
    pid = seeded[0]
    client.post(f"/api/portfolio/{pid}", json={"purchase_price": 40000,
                                               "purchase_date": "2026-09-01",
                                               "current_value": 90000,
                                               "loan_amount": 30000})
    assert store.get_property(pid)["state"] == "PORTFOLIO"
    client.post(f"/api/property/{pid}/ledger",
                json={"direction": "expense", "category": "roof", "amount": 6500})
    client.post(f"/api/property/{pid}/ledger",
                json={"direction": "revenue", "category": "rent", "amount": 900})
    assert client.post(f"/api/property/{pid}/ledger",
                       json={"direction": "sideways", "amount": 1}).status_code == 400
    led = client.get(f"/api/property/{pid}/ledger").json()
    assert led["expenses"] == 6500 and led["revenue"] == 900 and led["net"] == -5600
    client.post(f"/api/property/{pid}/lease",
                json={"tenant_name": "A. Tenant", "rent": 900, "start_date": "2026-10-01"})
    assert store.get_property(pid)["state"] == "RENTED"
    pf = client.get("/api/portfolio").json()
    assert pf["count"] == 1 and pf["monthly_rent"] == 900
    assert pf["equity"] == 60000
    proj = client.post(f"/api/rehab/{pid}/project", json={"name": "Make-ready", "budget": 12000}).json()
    task = client.post("/api/rehab/task", json={"project_id": proj["id"], "property_id": pid,
                                                "category": "roof", "title": "Replace shingles",
                                                "budget": 6000}).json()
    client.post(f"/api/rehab/task/{task['id']}", json={"actual": 6500, "status": "done"})
    rehab = client.get(f"/api/rehab/{pid}").json()
    assert rehab["projects"][0]["budget_total"] == 6000
    assert rehab["projects"][0]["actual_total"] == 6500


def test_pdf_export_is_honest_when_chrome_is_missing(client, seeded, monkeypatch):
    from hunter import pdf as pdfmod
    monkeypatch.setattr(pdfmod, "chrome_path", lambda: None)
    r = client.get(f"/api/property/{seeded[0]}/report.pdf")
    assert r.status_code == 501
    assert "PDF export unavailable" in r.json()["detail"]


# ------------------------------------------------------ territories (71/72)

def test_inactive_territory_is_listed_but_refused(client, monkeypatch):
    from hunter import api, config
    st = client.get("/api/status").json()
    keys = {t["key"]: t["active"] for t in st["territories"]}
    assert keys == {"garland_ar": True, "saline_ar": True}
    # a territory that is configured but switched off must be listed and refused
    parked = [dict(t, active=(t["key"] != "saline_ar")) for t in config.TERRITORIES]
    monkeypatch.setattr(api, "TERRITORIES", parked)
    r = client.post("/api/scan", json={"territory": "saline_ar", "mode": "seeds", "limit": 1})
    assert r.status_code == 400 and "not switched on" in r.json()["detail"]
    assert client.post("/api/scan", json={"territory": "mars"}).status_code == 400
    assert client.post("/api/scan", json={"mode": "banana"}).status_code == 400


# --------------------------------------------------------- desktop folder

def test_desktop_refresh_writes_briefing_and_picks_and_removes_stale_ones(client, seeded, tmp_path, monkeypatch):
    from hunter import desktop, pdf as pdfmod
    monkeypatch.setattr(pdfmod, "html_to_pdf", lambda html, timeout=60: b"%PDF-fake " + html[:20].encode())
    (tmp_path / "PICK 9 - Old House.pdf").write_bytes(b"stale")
    (tmp_path / "READ ME FIRST.txt").write_text("keep me")
    out = desktop.refresh(tmp_path)
    assert "THIS MORNING.txt" in out["written"]
    assert not (tmp_path / "PICK 9 - Old House.pdf").exists()          # stale pick gone
    assert (tmp_path / "READ ME FIRST.txt").read_text() == "keep me"     # untouched
    picks = sorted(p.name for p in tmp_path.glob("PICK *.pdf"))
    assert picks and picks[0].startswith("PICK 1 - ")
    text = (tmp_path / "THIS MORNING.txt").read_text()
    assert "INVESTIGATE FIRST" in text and "NEXT THREE ACTIONS" in text and "not legal" in text
    assert desktop.refresh(tmp_path / "missing")["skipped"]
