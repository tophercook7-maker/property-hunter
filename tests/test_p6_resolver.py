"""P6 — ARKANSAS ADDRESS → PARCEL RESOLVER. The 14 address cases and the security set.
The live State roll is simulated with features shaped exactly like the Arkansas GIS parcel layer."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_record
from test_p55_license import Device, _code, admin, client, enforced  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent


def feat(parcel, fips, num, name, stype, city, zip5, *, predir=None, psufdir=None, label=None, lat=34.5, lon=-93.05):
    return {"attributes": {"parcelid": parcel, "countyfips": fips, "adrnum": num, "predir": predir, "pstrnam": name, "pstrtype": stype,
                           "psufdir": psufdir, "adrcity": city, "adrzip5": zip5,
                           "adrlabel": label or " ".join(x for x in (str(num), predir, name, stype, psufdir) if x),
                           "ownername": "OWNER LLC", "parceltype": "RI", "impvalue": 5000.0, "landvalue": 1000.0, "totalvalue": 6000.0,
                           "taxarea": 0.2, "parcellgl": "LOT 1", "subdivision": "SUB", "sourcedate": None, "camadate": None, "pubdate": None},
            "geometry": {"rings": [[[lon, lat], [lon + 0.0005, lat], [lon + 0.0005, lat + 0.0005], [lon, lat + 0.0005], [lon, lat]]]}}


LINCOLN = feat("300-06307-000", "05051", 302, "LINCOLN", "ST", "HOT SPRINGS", 71901)
MAINS = [feat("008-00030-000", "05067", 102, "MAIN", "ST", "JACKSONPORT", 72075, lat=35.6, lon=-91.3),
         feat("06-00124", "05077", 102, "MAIN", "ST", "MORO", 72368, lat=34.8, lon=-90.9),
         feat("900-00012-000", "05053", 102, "MAIN", "ST", "LEOLA", 72084, lat=34.2, lon=-92.6)]


@pytest.fixture
def roll(monkeypatch):
    """A live roll that answers from a fixture; `roll.fail` switches it to an outage."""
    from hunter import resolver
    state = {"features": [LINCOLN] + MAINS, "fail": None, "calls": 0, "where": []}

    def query(n):
        state["calls"] += 1
        if state["fail"]:
            raise state["fail"]
        if n.get("mode") == "PARCEL_ID":
            return {"features": [f for f in state["features"] if f["attributes"]["parcelid"] == n["parcel_id"] and f["attributes"]["countyfips"] == n["county_fips"]]}
        feats = [f for f in state["features"] if f["attributes"]["adrnum"] == n["number"] and f["attributes"]["pstrnam"].startswith(n["name"].split()[0])]
        if n.get("county_fips"):
            feats = [f for f in feats if f["attributes"]["countyfips"] == n["county_fips"]]
        return {"features": feats}
    monkeypatch.setattr(resolver, "_live_query", query)
    return state


def _r(addr, **kw):
    from hunter import resolver
    return resolver.resolve(addr, **kw)


# ------------------------------------------------------------------ 1-4: exact, capitalization, suffix, ZIP
def test_exact_match_writes_automated_evidence_once(roll):
    from hunter import db, resolver
    r = _r("302 Lincoln St, Hot Springs, AR 71901")
    assert r["state"] == "EXACT_MATCH" and r["identity"]["parcel_id"] == "300-06307-000" and r["identity"]["county"] == "Garland"
    assert r["identity"]["verified_by"] == "AUTOMATED_SOURCE" and r["identity"]["source"] == "STATE_ROLL_LIVE"
    assert r["normalized"]["rules_version"] == resolver.RULES_VERSION and r["normalized"]["original"] == "302 Lincoln St, Hot Springs, AR 71901"
    c = r["candidates"][0]
    assert {"HOUSE_NUMBER", "STREET_NAME", "SUFFIX", "CITY", "ZIP"} <= set(c["match_reasons"]) and c["mismatches"] == []
    pid = r["identity"]["property_id"]
    ev = db.q("SELECT * FROM evidence WHERE property_id=? AND field='identity_resolution'", (pid,))
    assert len(ev) == 1 and ev[0]["origin"] == "AUTOMATED_SOURCE" and ev[0]["source"] == "ar_gis_parcels" and "search:" in ev[0]["raw_ref"]
    # 12: a duplicate resolution reuses the evidence row; the history keeps both searches
    r2 = _r("302 lincoln street hot springs ar 71901")
    assert r2["state"] == "EXACT_MATCH" and r2["identity"]["property_id"] == pid and r2["search_id"] != r["search_id"]
    assert len(db.q("SELECT 1 FROM evidence WHERE property_id=? AND field='identity_resolution'", (pid,))) == 1
    assert db.q1("SELECT count(*) c FROM properties")["c"] == 1


def test_capitalization_and_no_locality_is_strong_not_exact(roll):
    r = _r("302 LINCOLN STREET")
    assert r["state"] == "STRONG_MATCH" and r["identity"]["verified_by"] == "AUTOMATED_SOURCE"
    assert "no city or ZIP" in r["explanation"]["summary"]
    r = _r("302 Lincoln St, Hot Springs")            # city only, no ZIP → exact
    assert r["state"] == "EXACT_MATCH"


def test_suffix_variation_is_manual_review_not_a_match(roll):
    from hunter import db
    r = _r("302 Lincoln Ave, Hot Springs")
    assert r["state"] == "MANUAL_REVIEW_REQUIRED" and r["identity"] is None and r["selected_property_id"] is None
    assert any(m.startswith("SUFFIX") for m in r["candidates"][0]["mismatches"])
    assert not db.q("SELECT 1 FROM evidence WHERE field='identity_resolution'")


def test_zip_mismatch_is_manual_review(roll):
    r = _r("302 Lincoln St, Hot Springs, AR 72201")
    assert r["state"] == "MANUAL_REVIEW_REQUIRED" and any(m.startswith("ZIP") for m in r["candidates"][0]["mismatches"])


# ------------------------------------------------------------------ 5, 9: ambiguity and human selection
def test_ambiguous_lists_all_and_city_disambiguates(roll):
    r = _r("102 Main St")
    assert r["state"] == "AMBIGUOUS" and r["qualifying"] == 3 and r["identity"] is None
    assert sorted(c["county"] for c in r["candidates"]) == ["Grant", "Jackson", "Lee"]
    r = _r("102 Main St, Leola")
    assert r["state"] == "EXACT_MATCH" and r["identity"]["county"] == "Grant"
    r = _r("102 Main St, Jackson County")
    assert r["state"] == "STRONG_MATCH" and r["identity"]["county"] == "Jackson" and r["normalized"]["county_fips"] == "05067"


def test_human_selection_is_manual_verification(roll):
    from hunter import db, resolver
    r = _r("102 Main St")
    pid = next(c["property_id"] for c in r["candidates"] if c["county"] == "Lee")
    with pytest.raises(ValueError):
        resolver.select(r["search_id"], pid, actor="Topher", reason="")          # a reason is required
    with pytest.raises(ValueError):
        resolver.select(r["search_id"], 999999, actor="Topher", reason="not a listed candidate")
    s = resolver.select(r["search_id"], pid, actor="Topher", reason="Owner confirmed by phone; Moro address", reference="call 2026-09-16")
    assert s["selected_by"] == "MANUAL_VERIFICATION" and s["identity"]["verified_by"] == "MANUAL_VERIFICATION" and s["identity"]["actor"] == "Topher"
    assert s["identity"]["source"] == "HUMAN_PROPERTY_SELECTION" and s["state"] == "AMBIGUOUS"       # the search state is preserved; the choice is on top
    ev = db.q1("SELECT * FROM evidence WHERE property_id=? AND field='manual:identity'", (pid,))
    assert ev["origin"] == "MANUAL_VERIFICATION" and "HUMAN PROPERTY SELECTION" in ev["raw_ref"] and "reason: Owner confirmed" in ev["raw_ref"] and "reference: call" in ev["raw_ref"]
    with pytest.raises(ValueError):
        resolver.select(r["search_id"], pid, actor="Topher", reason="twice")     # one selection per search
    ex = _r("302 Lincoln St, Hot Springs")
    with pytest.raises(ValueError):
        resolver.select(ex["search_id"], ex["identity"]["property_id"], actor="Topher", reason="nothing to choose on an exact match")


# ------------------------------------------------------------------ 6, 7, 8, 14: no match, outage, out of state, malformed
def test_no_match_is_not_nonexistence(roll):
    r = _r("9999 Nowhere Rd, Hot Springs")
    assert r["state"] == "NO_MATCH" and "not proof" in r["explanation"]["summary"]
    assert [s["status"] for s in r["sources"]] == ["OK", "OK"] and r["explanation"]["next_action"]


def test_source_unavailable_is_not_no_match(roll):
    from hunter import store
    roll["fail"] = TimeoutError("timed out")
    r = _r("302 Lincoln St, Hot Springs")
    assert r["state"] == "SOURCE_UNAVAILABLE" and r["identity"] is None
    live = next(s for s in r["sources"] if s["source"] == "STATE_ROLL_LIVE")
    assert live["status"] == "UNAVAILABLE" and live["failure_category"] == "TIMEOUT" and live["read_at"]
    # the dated local copy still answers when it has the parcel → STRONG, never EXACT, and says the live roll was unavailable
    store.ingest(make_record(parcel_id="300-06307-000", address="302 Lincoln St", city="HOT SPRINGS", zip="71901"))
    r = _r("302 Lincoln St, Hot Springs")
    assert r["state"] == "STRONG_MATCH" and r["identity"]["source"] == "LOCAL_ROLL" and "could not be re-read" in r["explanation"]["summary"]
    assert r["explanation"]["unavailable"] == ["Arkansas GIS Office parcel layer (live State roll)"]
    from hunter.http import Blocked
    roll["fail"] = Blocked("HTTP 403")
    assert next(s for s in _r("1 Nothing St")["sources"] if s["source"] == "STATE_ROLL_LIVE")["failure_category"] == "ACCESS_RESTRICTED"


def test_out_of_scope_and_invalid_input_touch_nothing(roll):
    from hunter import db
    for addr, st in (("123 Main St, Dallas, TX 75201", "OUT_OF_SCOPE"), ("123 Main St, Texas", "OUT_OF_SCOPE"), ("123 Main St 75201", "OUT_OF_SCOPE"),
                     ("", "INVALID_INPUT"), ("Lincoln St", "INVALID_INPUT"), ("x" * 300, "INVALID_INPUT"), ("302; DROP TABLE properties", "INVALID_INPUT"),
                     ("https://evil.example/q", "INVALID_INPUT"), (None, "INVALID_INPUT"), (12345, "INVALID_INPUT")):
        r = _r(addr)
        assert r["state"] == st and r["identity"] is None and r["explanation"]["summary"], addr
    assert roll["calls"] == 0 and db.q1("SELECT count(*) c FROM properties")["c"] == 0 and db.q1("SELECT count(*) c FROM investigation_cases")["c"] == 0


# ------------------------------------------------------------------ 10, 11: investigation reuse
def test_investigation_reuses_existing_case_and_records_origin(roll):
    from hunter import cases, db, resolver
    r = _r("302 Lincoln St, Hot Springs")
    pid = r["identity"]["property_id"]
    existing = cases.open_or_create(pid, None, "Topher")["investigation_id"]   # case exists before the search is attached
    out = resolver.open_investigation(r["search_id"], actor="Topher")
    assert out["case_id"] == existing and out["created"] is False
    out2 = resolver.open_investigation(r["search_id"], actor="Topher")     # idempotent
    assert out2["case_id"] == existing and db.q1("SELECT count(*) c FROM investigation_cases")["c"] == 1
    evs = db.q("SELECT * FROM investigation_events WHERE case_id=? AND cls='INVESTIGATION OPENED FROM ADDRESS SEARCH'", (existing,))
    assert len(evs) == 1 and f"search:{r['search_id']}" in evs[0]["detail"] and "AUTOMATED_SOURCE" in evs[0]["detail"] and evs[0]["actor"] == "Topher"
    assert resolver.view(r["search_id"])["case"]["id"] == existing
    # a fresh property: opened from the search, one case, the human selection named on the event
    r2 = _r("102 Main St")
    lee = next(c["property_id"] for c in r2["candidates"] if c["county"] == "Lee")
    resolver.select(r2["search_id"], lee, actor="Topher", reason="verified with the Lee County assessor")
    o = resolver.open_investigation(r2["search_id"], actor="Topher")
    assert o["created"] is True and "HUMAN PROPERTY SELECTION" in db.q1("SELECT detail FROM investigation_events WHERE case_id=? AND cls='INVESTIGATION OPENED FROM ADDRESS SEARCH'", (o["case_id"],))["detail"]
    with pytest.raises(ValueError):
        resolver.open_investigation(_r("9999 Nowhere Rd")["search_id"], actor="Topher")      # nothing resolved → no case


# ------------------------------------------------------------------ 13: stale / historical identity
def test_historical_local_identity_never_resolves_over_the_live_roll(roll):
    from hunter import store
    store.ingest(make_record(parcel_id="300-OLD-000", address="302 Lincoln St", city="HOT SPRINGS", zip="71901"))
    r = _r("302 Lincoln St, Hot Springs")
    assert r["state"] == "EXACT_MATCH" and r["identity"]["parcel_id"] == "300-06307-000"
    old = next(c for c in r["candidates"] if c["parcel_id"] == "300-OLD-000")
    assert old["historical"] is True and any("HISTORICAL" in m for m in old["mismatches"])
    roll["features"] = MAINS                                                        # the live roll no longer lists Lincoln at all
    r = _r("302 Lincoln St, Hot Springs")
    assert r["state"] == "MANUAL_REVIEW_REQUIRED" and r["identity"] is None


def test_parcel_number_with_county_resolves_and_without_county_is_refused(roll):
    from hunter import resolver
    r = _r("300-06307-000, Garland County")
    assert r["state"] == "EXACT_MATCH" and r["identity"]["parcel_id"] == "300-06307-000" and r["normalized"]["mode"] == "PARCEL_ID" and "parcel number" in r["explanation"]["summary"] or r["state"] == "EXACT_MATCH"
    assert r["candidates"][0]["match_reasons"] == ["PARCEL_IDENTITY", "COUNTY"] and r["normalized"]["normalized"] == "PARCEL 300-06307-000, GARLAND COUNTY"
    r2 = _r("parcel 300-06307-000, Garland, AR")
    assert r2["state"] == "EXACT_MATCH" and r2["identity"]["property_id"] == r["identity"]["property_id"]
    assert _r("300-06307-000")["state"] == "INVALID_INPUT" and "county-local" in _r("300-06307-000")["explanation"]["summary"]
    assert _r("300-06307-000, Lee County")["state"] == "NO_MATCH"                       # same number, wrong county: parcel numbers are county-local
    assert roll["calls"] >= 3


# ------------------------------------------------------------------ normalization keeps what distinguishes parcels
def test_normalization_preserves_components():
    from hunter import resolver
    n = resolver.normalize("1301 1/2 N. Central Avenue Apt 4, Hot Springs, AR 71901")
    assert (n["number"], n["fraction"], n["predir"], n["name"], n["suffix"], n["unit"], n["city"], n["zip"]) == (1301, "1/2", "N", "CENTRAL", "AVE", "4", "HOT SPRINGS", "71901")
    assert n["normalized"] == "1301 1/2 N CENTRAL AVE APT 4, HOT SPRINGS 71901"
    n = resolver.normalize("1200 Highway 7 N, Hot Springs")      # route addresses need the comma: there is no suffix to split on
    assert n["name"] == "HWY 7" and n["postdir"] == "N" and n["number"] == 1200
    assert resolver.normalize("100 W 1st St, Little Rock")["name"] == "FIRST"
    assert resolver._suffix("STRE") == "ST" and resolver._suffix("AVEN") == "AVE" and resolver._suffix("st") == "ST" and resolver._suffix("XQ") == "XQ"
    assert resolver.normalize("412 Hobson Ave, Hot Springs, Garland County")["county_fips"] == "05051"


# ------------------------------------------------------------------ SECURITY
def test_resolver_is_licensed_and_narrow(client, admin, roll):
    from hunter import db, licensing
    for path, method in (("/api/property/resolve", "post"), ("/api/property/resolve/history", "get"), ("/api/property/resolve/1", "get"),
                         ("/api/property/resolve/1/select", "post"), ("/api/property/resolve/1/investigate", "post")):
        r = getattr(client, method)(path, json={"address": "302 Lincoln St"}) if method == "post" else client.get(path)
        assert r.status_code == 401 and r.json()["code"] == "LICENSE_REQUIRED", path
    a, b = Device(), Device()
    assert a.activate(client, _code(client, admin)["code"]).status_code == 200
    assert b.activate(client, _code(client, admin)["code"]).status_code == 200
    # arbitrary sources, URLs, SQL, adapters and commands are refused before anything runs
    for bad in ({"address": "302 Lincoln St", "source": "countypay"}, {"address": "302 Lincoln St", "url": "https://x"}, {"sql": "select 1"},
                {"address": "302 Lincoln St", "adapter": "ar_gis_parcels"}, {"address": "302 Lincoln St", "command": "ls"}, {"address": ["x"]}):
        r = client.post("/api/property/resolve", json=bad, headers=a.h())
        assert r.status_code == 400, bad
    assert roll["calls"] == 0
    ra = client.post("/api/property/resolve", json={"address": "302 Lincoln St, Hot Springs", "actor": "A"}, headers=a.h())
    assert ra.status_code == 200 and ra.json()["state"] == "EXACT_MATCH"
    rb = client.post("/api/property/resolve", json={"address": "102 Main St", "actor": "B"}, headers=b.h())
    assert rb.status_code == 200 and rb.json()["state"] == "AMBIGUOUS"
    sa, sb = ra.json()["search_id"], rb.json()["search_id"]
    # history is isolated per license
    ha = client.get("/api/property/resolve/history", headers=a.h()).json()
    hb = client.get("/api/property/resolve/history", headers=b.h()).json()
    assert [s["id"] for s in ha["searches"]] == [sa] and [s["id"] for s in hb["searches"]] == [sb]
    assert client.get(f"/api/property/resolve/{sb}", headers=a.h()).status_code == 404
    assert client.post(f"/api/property/resolve/{sb}/select", json={"property_id": 1, "actor": "A", "reason": "mine now"}, headers=a.h()).status_code == 404
    assert client.post(f"/api/property/resolve/{sa}/investigate", json={}, headers=b.h()).status_code == 404
    assert client.get(f"/api/property/resolve/{sa}", headers=a.h()).status_code == 200
    # a revoked license loses access at once
    lic_a = client.get("/api/license/me", headers=a.h()).json()["license_id"]
    licensing.revoke(lic_a, "test")
    assert client.post("/api/property/resolve", json={"address": "302 Lincoln St"}, headers=a.h()).status_code == 401
    # no secrets in any resolver response
    blob = json.dumps(ra.json()) + json.dumps(rb.json()) + json.dumps(hb)
    assert not re.search(r"PH-[A-Z2-9]{4}-[A-Z2-9]{4}", blob) and "pepper" not in blob.lower() and "refresh_token" not in blob and "admin" not in blob.lower()
    # P5 execution and Bee stay protected
    assert client.post("/api/bee/proposal/1/run", json={}, headers=a.h()).status_code == 401
    assert client.get("/api/case/1/bee").status_code == 401


def test_rate_limit_reuses_p55_infrastructure(monkeypatch, roll):
    from hunter import licensing
    from hunter.api import app
    monkeypatch.setitem(licensing.LIMITS, "resolve", (3, 600))
    licensing.reset_rate_limits()
    with TestClient(app) as c:
        codes = [c.post("/api/property/resolve", json={"address": "302 Lincoln St"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    licensing.reset_rate_limits()


def test_no_shell_no_unrestricted_http_no_public_resolver_no_export():
    src = (ROOT / "hunter" / "resolver.py").read_text()
    assert not re.search(r"\b(subprocess|os\.system|os\.popen|shlex|pty)\b", src) and "eval(" not in src and "exec(" not in src
    assert not re.search(r"^\s*(import|from)\s+(requests|urllib|httpx|aiohttp|socket)\b", src, re.M)
    assert "from .http import Blocked, arcgis_query" in src                      # the one network path: the shared rate-limited client
    api = (ROOT / "hunter" / "api.py").read_text()
    assert '"/api/property/resolve"' not in api.split("PUBLIC_API = ")[1].split("\n")[0]
    # no public snapshot carries search history, and the exporter does not know the table
    share = (ROOT / "tools" / "build_share.py").read_text() + (ROOT / "tools" / "publish_scan.py").read_text()
    assert "address_searches" not in share
    for f in (ROOT / "docs" / "data").glob("*.json"):
        assert "address_searches" not in f.read_text()[:2_000_000] and "input_original" not in f.read_text()[:2_000_000]
    # the front end has no bypass: every call goes through PH.apiFetch / PHAuth
    ui = (ROOT / "docs" / "find.html").read_text()
    assert "PH.apiFetch(" in ui and "arcgis" not in ui.lower() and "services.arcgis" not in ui and "fetch('http" not in ui
    # Bee has no path into the resolver
    assert "resolver" not in (ROOT / "hunter" / "bee.py").read_text()
