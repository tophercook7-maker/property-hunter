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
    assert "not changed behind your back" in learning["note"]


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
