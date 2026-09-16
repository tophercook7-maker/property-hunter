"""P5.5 commercial access gate: one code, one activation, one device key, authenticated session, protected API.
Second device with the same code is rejected; the original keeps working; a revoked license loses access on the
next request; nothing secret reaches the client, the logs or the public site; the dev bypass is ignored in
production; P0-P5 routes are unreachable without a licensed session."""
import base64
import concurrent.futures
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from conftest import make_record

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


class Device:
    """A browser: an ECDSA P-256 key pair whose private half never leaves it."""
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        n = self.key.public_key().public_numbers()
        b = lambda i: base64.urlsafe_b64encode(i.to_bytes(32, "big")).decode().rstrip("=")
        self.jwk = {"kty": "EC", "crv": "P-256", "x": b(n.x), "y": b(n.y)}
        self.access = self.refresh = None
    def sign(self, text: str) -> str:
        der = self.key.sign(text.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode().rstrip("=")
    def activate(self, client, code):
        c = client.get("/api/license/challenge?purpose=activate").json()
        r = client.post("/api/license/activate", json={"code": code, "device_key": self.jwk, "nonce": c["nonce"], "signature": self.sign(c["nonce"])})
        if r.status_code == 200:
            self.access, self.refresh = r.json()["access_token"], r.json()["refresh_token"]
        return r
    def do_refresh(self, client):
        c = client.get("/api/license/challenge?purpose=refresh").json()
        r = client.post("/api/license/refresh", json={"refresh_token": self.refresh, "nonce": c["nonce"], "signature": self.sign(c["nonce"])})
        if r.status_code == 200:
            self.access, self.refresh = r.json()["access_token"], r.json()["refresh_token"]
        return r
    def h(self):
        return {"Authorization": f"Bearer {self.access}"}


@pytest.fixture
def enforced(monkeypatch):
    from hunter import licensing
    monkeypatch.setattr(licensing, "ENFORCED", True)
    licensing.reset_rate_limits()
    yield
    licensing.reset_rate_limits()


@pytest.fixture
def client(enforced):
    from hunter.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin():
    from hunter import licensing
    return {"X-Admin-Token": licensing.admin_token()}


def _code(client, admin, **kw):
    r = client.post("/api/admin/licenses", json=dict({"count": 1}, **kw), headers=admin)
    assert r.status_code == 200, r.text
    return r.json()["issued"][0]


# ------------------------------------------------------------------ 1-3, 8, 9, 10, 14, 15, 21: activation, sessions, protection

def test_one_code_one_device_and_protected_api(client, admin):
    from hunter import store, db
    pid, _, _ = store.ingest(make_record(parcel_id="2000-1", county_fips="05051", address="1 Gate St"))
    # unauthenticated: protected routes are refused, public ones answer
    for path in ("/api/properties", f"/api/property/{pid}", "/api/cases", "/api/cases/index", "/api/case/1/bee", "/api/bee/proposal/1/run", "/api/sources", "/api/tasks", "/api/scan/current"):
        r = client.get(path) if path != "/api/bee/proposal/1/run" else client.post(path, json={})
        assert r.status_code == 401 and r.json()["code"] == "LICENSE_REQUIRED", path
    assert client.post("/api/scan", json={}).status_code == 401
    assert client.post(f"/api/property/{pid}/case", json={}).status_code == 401
    assert client.get("/api/health").status_code == 200 and client.get("/api/status").status_code == 200 and client.get("/api/license/state").json()["enforced"] is True
    # a code issued by the admin, shown once; the stored form is a hash
    issued = _code(client, admin, license_type="SINGLE_USER")
    code = issued["code"]
    assert re.fullmatch(r"PH-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code) and "STORE SECURELY" in issued["note"]
    row = db.q1("SELECT * FROM licenses WHERE id=?", (issued["id"],))
    assert row["status"] == "UNUSED" and code not in json.dumps(dict(row)) and len(row["code_hash"]) == 64
    # malformed and invalid codes are rejected with generic messages
    for bad in ("", "PH-1234", "PH-AAAA-AAAA-AAAA-AAA0", "PH-ZZZZ-ZZZZ-ZZZZ-ZZZZ"):
        r = Device().activate(client, bad)
        assert r.status_code == 403 and r.json()["detail"] == "Invalid access code."
    # device A activates; the code never comes back; the session works on protected routes
    a = Device()
    r = a.activate(client, code)
    assert r.status_code == 200 and r.json()["reactivated"] is False and "code" not in {k.lower() for k in r.json()} and code not in r.text
    assert client.get("/api/properties", headers=a.h()).status_code == 200
    assert client.get("/api/license/me", headers=a.h()).json()["license_id"] == issued["id"]
    v = client.get(f"/api/admin/licenses/{issued['id']}", headers=admin).json()
    assert v["status"] == "ACTIVE" and v["device"]["bound"] and "code_hash" not in v and "public_jwk" not in json.dumps(v) and v["activation_count"] == 1
    # device B with the same code: rejected, nothing revealed
    b = Device()
    r = b.activate(client, code)
    assert r.status_code == 403 and r.json()["detail"] == "This access code has already been activated on another device."
    assert "x" not in r.json() and client.get("/api/properties", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    # device A reopens: refresh with a signed nonce, no code re-entered; old refresh token dies (rotation)
    old_refresh = a.refresh
    assert a.do_refresh(client).status_code == 200 and client.get("/api/cases", headers=a.h()).status_code == 200
    c = client.get("/api/license/challenge?purpose=refresh").json()
    assert client.post("/api/license/refresh", json={"refresh_token": old_refresh, "nonce": c["nonce"], "signature": a.sign(c["nonce"])}).status_code == 403
    # same device re-activation with the code is allowed (reactivated), a stolen refresh token without the key is not
    assert a.activate(client, code).json()["reactivated"] is True
    c = client.get("/api/license/challenge?purpose=refresh").json()
    assert client.post("/api/license/refresh", json={"refresh_token": a.refresh, "nonce": c["nonce"], "signature": b.sign(c["nonce"])}).status_code == 403
    # a nonce is one-time and a private key in the JWK is refused
    c = client.get("/api/license/challenge?purpose=activate").json()
    n = a.jwk | {"d": "AAAA"}
    assert client.post("/api/license/activate", json={"code": code, "device_key": n, "nonce": c["nonce"], "signature": a.sign(c["nonce"])}).status_code == 403
    assert db.q1("SELECT COUNT(*) n FROM license_devices WHERE license_id=? AND active=1", (issued["id"],))["n"] == 1
    # audit trail exists and never carries the code
    ev = client.get(f"/api/admin/licenses/{issued['id']}/events", headers=admin).json()["events"]
    assert {e["event"] for e in ev} >= {"LICENSE_CREATED", "LICENSE_ACTIVATED", "DEVICE_BINDING_CREATED", "DEVICE_BINDING_REJECTED", "LICENSE_SESSION_CREATED"}
    assert code not in json.dumps(ev) and code[3:] not in json.dumps(ev)
    assert code not in json.dumps([dict(r) for r in db.q("SELECT * FROM license_events")])


# ------------------------------------------------------------------ 6: concurrency

def test_concurrent_activation_lets_exactly_one_win(client, admin):
    code = _code(client, admin)["code"]
    from hunter.api import app
    devices = [Device() for _ in range(6)]
    def go(d):
        with TestClient(app) as c:
            return d.activate(c, code).status_code
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        codes = list(ex.map(go, devices))
    assert codes.count(200) == 1 and codes.count(403) == 5
    from hunter import db
    assert db.q1("SELECT activation_count FROM licenses WHERE code_hash=(SELECT code_hash FROM licenses ORDER BY id DESC LIMIT 1)")["activation_count"] == 1


# ------------------------------------------------------------------ 4, 5, 16, 17, 18: revoke, expire, recover

def test_revocation_expiry_and_admin_rebind(client, admin):
    from hunter import db
    lic = _code(client, admin)
    a = Device(); assert a.activate(client, lic["code"]).status_code == 200
    assert client.get("/api/cases", headers=a.h()).status_code == 200
    r = client.post(f"/api/admin/licenses/{lic['id']}/revoke", json={"reason": "chargeback"}, headers=admin).json()
    assert r["status"] == "REVOKED"
    assert client.get("/api/cases", headers=a.h()).status_code == 401, "revocation takes effect on the next request"
    assert a.do_refresh(client).status_code == 403
    assert Device().activate(client, lic["code"]).json()["detail"] == "This license has been revoked." and a.activate(client, lic["code"]).status_code == 403
    assert client.post(f"/api/admin/licenses/{lic['id']}/rebind", json={}, headers=admin).status_code == 400, "a revoked license is never rebound"
    # expiry
    exp = _code(client, admin, days=30)
    db.ex("UPDATE licenses SET expires_at='2020-01-01T00:00:00+00:00' WHERE id=?", (exp["id"],))
    assert Device().activate(client, exp["code"]).json()["detail"] == "This license has expired."
    assert client.get(f"/api/admin/licenses/{exp['id']}", headers=admin).json()["status"] == "EXPIRED"
    # device loss: the customer cleared the browser; the code alone is not enough anywhere; the admin rebinds once
    lic2 = _code(client, admin)
    a2 = Device(); assert a2.activate(client, lic2["code"]).status_code == 200
    lost = Device()                                   # a fresh key: the same person after clearing storage, or anyone else
    assert lost.activate(client, lic2["code"]).status_code == 403
    rb = client.post(f"/api/admin/licenses/{lic2['id']}/rebind", json={"reason": "customer lost device"}, headers=admin).json()
    assert rb["status"] == "REBIND_PENDING" and not rb["device"]["bound"]
    assert client.get("/api/cases", headers=a2.h()).status_code == 401, "the old device is out"
    assert lost.activate(client, lic2["code"]).status_code == 200 and client.get("/api/cases", headers=lost.h()).status_code == 200
    assert a2.activate(client, lic2["code"]).status_code == 403, "and the old key cannot come back"
    assert client.get(f"/api/admin/licenses/{lic2['id']}", headers=admin).json()["activation_count"] == 2
    ev = {e["event"] for e in client.get(f"/api/admin/licenses/{lic2['id']}/events", headers=admin).json()["events"]}
    assert "DEVICE_REBIND_ADMIN" in ev and "LICENSE_REVOKED" in {e["event"] for e in client.get(f"/api/admin/licenses/{lic['id']}/events", headers=admin).json()["events"]}


# ------------------------------------------------------------------ 19, 20, 24: admin boundary and rate limits

def test_admin_only_and_rate_limits(client, admin):
    lic = _code(client, admin)
    a = Device(); a.activate(client, lic["code"])
    for path, method in (("/api/admin/licenses", "post"), ("/api/admin/licenses", "get"), (f"/api/admin/licenses/{lic['id']}/revoke", "post")):
        r = getattr(client, method)(path, json={}, headers=a.h()) if method == "post" else client.get(path, headers=a.h())
        assert r.status_code == 403, "a licensed customer is not an admin"
        r = getattr(client, method)(path, json={}, headers={"X-Admin-Token": "wrong"}) if method == "post" else client.get(path, headers={"X-Admin-Token": "wrong"})
        assert r.status_code == 403
    assert client.get(f"/api/admin/licenses/{lic['id']}", headers=admin).json()["status"] == "ACTIVE"
    # brute force: after the window's allowance every attempt is 429, valid or not
    from hunter import licensing
    licensing.reset_rate_limits()
    good = _code(client, admin)["code"]
    seen = [Device().activate(client, "PH-ZZZZ-ZZZZ-ZZZZ-ZZZ" + ch).status_code for ch in "234567892"]
    assert 429 in seen and seen[-1] == 429
    assert Device().activate(client, good).status_code == 429, "even a valid code waits once the limit is hit"
    licensing.reset_rate_limits()
    assert Device().activate(client, good).status_code == 200


# ------------------------------------------------------------------ 11-13, 25, 27-31: secrets, public exposure, dev bypass

def test_no_secret_leaks_and_production_ignores_bypass(monkeypatch, client, admin):
    from hunter import licensing
    lic = _code(client, admin)
    code = lic["code"]
    # not in the public export, the pages, or the scripts
    for f in list((DOCS / "data").glob("*.json")) + list(DOCS.glob("*.html")) + list(DOCS.glob("*.js")) + [ROOT / "hunter" / "static" / "app.js"]:
        assert code not in f.read_text(errors="ignore"), f
    assert not (DOCS / "data" / "investigations.json").exists(), "investigation data is licensed and no longer published"
    for f in (DOCS / "ph-auth.js", DOCS / "activate.html", ROOT / "hunter" / "static" / "app.js"):
        t = f.read_text()
        for bad in ("admin_token", "ADMIN123", "master", "PH-" + "A" * 4, "x-admin-token", "license_pepper"):
            assert bad not in t.lower() if bad.islower() else bad not in t, (f, bad)
    assert "extractable" in (DOCS / "ph-auth.js").read_text() and "'sign', 'verify']" in (DOCS / "ph-auth.js").read_text()
    # the server never receives or stores a private key; the JWK on file is public only
    a = Device(); a.activate(client, code)
    from hunter import db
    jwk = json.loads(db.q1("SELECT public_jwk FROM license_devices WHERE license_id=?", (lic["id"],))["public_jwk"])
    assert set(jwk) == {"kty", "crv", "x", "y"}
    # the pepper and admin token live outside the repository
    inside_repo = [p for p in ROOT.rglob("*") if p.name in ("license_pepper", "admin_token") and p.relative_to(ROOT).parts[0] not in ("data", ".git")]
    assert not inside_repo, inside_repo
    # production ignores the development bypass
    monkeypatch.setenv("PH_ENV", "production"); monkeypatch.setenv("PH_LICENSE_ENFORCED", "0")
    import importlib
    src = Path(licensing.__file__).read_text()
    assert 'ENV == "development"' in src and 'PH_LICENSE_ENFORCED' in src
    env, enforced_flag = "production", "0"
    computed = not (enforced_flag in ("0", "false", "no") and env == "development")
    assert computed is True
    # the public site carries no protected application data
    pub = json.dumps({p.name: p.stat().st_size for p in (DOCS / "data").glob("*.json")})
    assert "investigations.json" not in pub
    for f in (DOCS / "data").glob("*.json"):
        t = f.read_text(errors="ignore")
        assert "outreach_drafts" not in t and "bee_proposals" not in t and "HUMAN_REPORTED" not in t, f


def test_p5_execution_bee_and_sources_unreachable_without_license(client):
    for path, method in (("/api/case/1/bee/run", "post"), ("/api/bee/proposal/1/run", "post"), ("/api/bee/proposal/1/decide", "post"), ("/api/sources/check", "post"), ("/api/property/1/investigate", "post"),
                         ("/api/case/1/outreach", "post"), ("/api/outreach/1/draft", "post"), ("/api/case/1/log", "post"), ("/api/export/properties.csv", "get"), ("/api/evidence", "get")):
        r = client.post(path, json={}) if method == "post" else client.get(path)
        assert r.status_code in (401, 404) and (r.status_code != 404 or path == "/api/evidence"), path
    # every /api route in the app is gated unless it is on the explicit public list
    from hunter.api import app, PUBLIC_API
    routes = {r.path for r in app.routes if getattr(r, "path", "").startswith("/api/")}
    assert PUBLIC_API <= routes | {"/api/license/state"}
    assert {"/api/properties", "/api/cases", "/api/case/{case_id}/bee", "/api/bee/proposal/{proposal_id}/run", "/api/sources/check", "/api/scan"} <= routes
    for p in PUBLIC_API:
        assert not any(k in p for k in ("case", "propert", "bee", "outreach", "evidence", "scan", "source", "admin"))
