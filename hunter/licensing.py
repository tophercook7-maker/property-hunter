"""P5.5 COMMERCIAL ACCESS GATE + ONE-TIME DEVICE-BOUND ACTIVATION.

    ONE ACTIVATION CODE -> ONE LICENSE -> ONE REGISTERED DEVICE KEY -> AUTHENTICATED SESSION -> PROTECTED API

The server decides everything. The browser is untrusted.

  * A code is 80 bits from the OS random source, shown once at creation; the database keeps only a
    peppered HMAC-SHA256 of it (the pepper lives in the data directory, never in the repository or the page).
  * Activation is atomic: one UPDATE ... WHERE status='UNUSED' claims the row; SQLite serialises writers, so a
    second concurrent claim sees zero rows updated and is rejected.
  * The device identity is an ECDSA P-256 key pair the browser generates with Web Crypto and keeps
    non-extractable in IndexedDB. The server stores the public key and its SHA-256 fingerprint. Every
    activation, refresh and re-authentication proves possession by signing a one-time server nonce.
  * The activation code is an enrolment credential only. Sessions use an opaque 15-minute access token and a
    30-day rotating refresh token; both are stored hashed. Every protected request re-checks the license
    and the device binding, so revocation takes effect on the next request.
  * Nothing here can verify physical hardware. A browser profile can be cleared or cloned; what is
    guaranteed is that the code alone is not enough to activate anywhere else, and that a cleared
    device cannot silently re-use its code: an admin rebind is the only path.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db
from .config import DATA_DIR
from .db import jdump, jload, utcnow

TYPES = ("SINGLE_USER", "MONTHLY", "YEARLY", "LIFETIME", "TRIAL", "ADMIN")
STATUSES = ("UNUSED", "ACTIVE", "REVOKED", "EXPIRED", "REBIND_PENDING")
ACCESS_TTL = 15 * 60          # seconds
REFRESH_TTL = 30 * 24 * 3600
CHALLENGE_TTL = 120
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no 0/O/1/I
GENERIC_REJECT = "This access code could not be activated."
ENV = os.environ.get("PH_ENV", "development")
ENFORCED = not (os.environ.get("PH_LICENSE_ENFORCED", "1") in ("0", "false", "no") and ENV == "development")

# ------------------------------------------------------------------ secrets on disk (never in the repo)

def _secret_file(name: str, nbytes: int = 32) -> bytes:
    path = os.path.join(str(DATA_DIR), name)
    os.makedirs(str(DATA_DIR), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(secrets.token_bytes(nbytes))
        os.chmod(path, 0o600)
    return open(path, "rb").read()


def pepper() -> bytes:
    return _secret_file("license_pepper")


def admin_token() -> str:
    """The operator's admin token: created once on disk, shown by `python -m hunter.licensing admin-token`."""
    return base64.urlsafe_b64encode(_secret_file("admin_token")).decode().rstrip("=")


def is_admin(presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(hashlib.sha256(presented.encode()).digest(), hashlib.sha256(admin_token().encode()).digest())


# ------------------------------------------------------------------ codes

def new_code() -> str:
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(16))
    return "PH-" + "-".join(body[i:i + 4] for i in range(0, 16, 4))


def normalize(code: str) -> str | None:
    s = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if s.startswith("PH"):
        s = s[2:]
    if len(s) != 16 or any(ch not in CODE_ALPHABET for ch in s):
        return None
    return s


def code_hash(code: str) -> str | None:
    n = normalize(code)
    if not n:
        return None
    return hmac.new(pepper(), n.encode(), hashlib.sha256).hexdigest()


def _token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


# ------------------------------------------------------------------ audit

EVENTS = ("LICENSE_CREATED", "LICENSE_ACTIVATION_ATTEMPTED", "LICENSE_ACTIVATED", "LICENSE_ACTIVATION_REJECTED", "LICENSE_SESSION_CREATED", "LICENSE_SESSION_REJECTED",
          "LICENSE_REVOKED", "DEVICE_BINDING_CREATED", "DEVICE_BINDING_REJECTED", "DEVICE_REBIND_ADMIN", "LICENSE_EXPIRED", "RATE_LIMITED")


def audit(event: str, *, license_id=None, device=None, actor=None, ok: bool, reason: str = "", ip: str | None = None) -> None:
    assert event in EVENTS
    db.ex("INSERT INTO license_events(at, event, license_id, device_fp, actor, ok, reason, ip) VALUES(?,?,?,?,?,?,?,?)",
          (utcnow(), event, license_id, (device or "")[:16] or None, actor, int(ok), reason[:120], ip))


# ------------------------------------------------------------------ rate limiting (in-process, sliding window)

_RL: dict[str, list[float]] = {}
_RL_LOCK = threading.Lock()
LIMITS = {"activate": (8, 600), "auth": (60, 600), "admin": (30, 600)}


def rate_limited(bucket: str, key: str) -> bool:
    n, window = LIMITS[bucket]
    now = time.time()
    with _RL_LOCK:
        hits = [t for t in _RL.get(f"{bucket}:{key}", []) if now - t < window]
        if len(hits) >= n:
            _RL[f"{bucket}:{key}"] = hits
            return True
        hits.append(now)
        _RL[f"{bucket}:{key}"] = hits
        return False


def reset_rate_limits() -> None:
    with _RL_LOCK:
        _RL.clear()


# ------------------------------------------------------------------ device keys (ECDSA P-256, Web Crypto compatible)

def _load_public_key(jwk: dict):
    from cryptography.hazmat.primitives.asymmetric import ec
    if not isinstance(jwk, dict) or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or "d" in jwk:
        raise ValueError("device key must be a P-256 public JWK (and never a private one)")
    def b64(s):
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    x, y = int.from_bytes(b64(jwk["x"]), "big"), int.from_bytes(b64(jwk["y"]), "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


def fingerprint(jwk: dict) -> str:
    canon = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def verify_signature(jwk: dict, message: bytes, signature_b64: str) -> bool:
    """Web Crypto ECDSA emits raw r||s (64 bytes); accept that or DER."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from cryptography.exceptions import InvalidSignature
    try:
        key = _load_public_key(jwk)
        sig = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
        if len(sig) == 64:
            sig = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        key.verify(sig, message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def new_challenge(purpose: str) -> dict:
    nonce = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(seconds=CHALLENGE_TTL)).isoformat(timespec="seconds")
    db.ex("INSERT INTO license_challenges(nonce_hash, purpose, expires_at, created_at) VALUES(?,?,?,?)", (_token_hash(nonce), purpose, exp, utcnow()))
    return {"nonce": nonce, "expires_at": exp}


def consume_challenge(nonce: str, purpose: str) -> bool:
    """One-time. Returns True only for an unexpired, unused nonce of this purpose."""
    row = db.q1("SELECT id, expires_at, used_at FROM license_challenges WHERE nonce_hash=? AND purpose=?", (_token_hash(nonce or ""), purpose))
    if not row or row["used_at"] or row["expires_at"] < utcnow():
        return False
    cur = db.ex("UPDATE license_challenges SET used_at=? WHERE id=? AND used_at IS NULL", (utcnow(), row["id"]))
    return cur.rowcount == 1


# ------------------------------------------------------------------ licenses (admin side)

def create(count: int = 1, license_type: str = "SINGLE_USER", days: int | None = None, created_by: str = "admin", metadata: dict | None = None) -> list[dict]:
    """Returns the plaintext codes ONCE. Only the peppered hash is stored."""
    if license_type not in TYPES:
        raise ValueError(f"license_type must be one of {TYPES}")
    out = []
    exp = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds") if days else None
    for _ in range(max(1, min(int(count), 1000))):
        code = new_code()
        cur = db.ex("INSERT INTO licenses(code_hash, status, license_type, expires_at, created_by, metadata_json, activation_count, created_at) VALUES(?,?,?,?,?,?,0,?)",
                    (code_hash(code), "UNUSED", license_type, exp, created_by, jdump(metadata or {}), utcnow()))
        audit("LICENSE_CREATED", license_id=cur.lastrowid, actor=created_by, ok=True, reason=license_type)
        out.append({"id": cur.lastrowid, "code": code, "license_type": license_type, "expires_at": exp, "note": "CODE ISSUED — STORE SECURELY; it will not be shown again"})
    return out


def view(license_id: int) -> dict | None:
    r = db.q1("SELECT * FROM licenses WHERE id=?", (license_id,))
    if not r:
        return None
    d = dict(r)
    d.pop("code_hash", None)
    d["metadata"] = jload(d.pop("metadata_json"), {}) or {}
    dev = db.q1("SELECT fingerprint, created_at, last_seen_at FROM license_devices WHERE license_id=? AND active=1", (license_id,))
    d["device"] = {"bound": bool(dev), "fingerprint_prefix": dev["fingerprint"][:8] if dev else None, "registered_at": dev["created_at"] if dev else None, "last_seen_at": dev["last_seen_at"] if dev else None}
    d["sessions_active"] = db.q1("SELECT COUNT(*) n FROM license_sessions WHERE license_id=? AND revoked_at IS NULL AND refresh_expires_at>?", (license_id, utcnow()))["n"]
    return d


def list_all(status: str | None = None) -> list[dict]:
    rows = db.q("SELECT id FROM licenses" + (" WHERE status=?" if status else "") + " ORDER BY id DESC", (status,) if status else ())
    return [view(r["id"]) for r in rows]


def revoke(license_id: int, actor: str = "admin", reason: str = "") -> dict:
    if not db.q1("SELECT 1 FROM licenses WHERE id=?", (license_id,)):
        raise KeyError("no such license")
    now = utcnow()
    db.ex("UPDATE licenses SET status='REVOKED', revoked_at=? WHERE id=?", (now, license_id))
    db.ex("UPDATE license_sessions SET revoked_at=? WHERE license_id=? AND revoked_at IS NULL", (now, license_id))
    audit("LICENSE_REVOKED", license_id=license_id, actor=actor, ok=True, reason=reason)
    return view(license_id)


def rebind(license_id: int, actor: str = "admin", reason: str = "") -> dict:
    """Admin-controlled device loss path: the old binding and all sessions are ended; the license may be
    activated ONCE more with its original code. Audited. A revoked or expired license is not rebound."""
    r = db.q1("SELECT status FROM licenses WHERE id=?", (license_id,))
    if not r:
        raise KeyError("no such license")
    if r["status"] in ("REVOKED", "EXPIRED"):
        raise ValueError(f"a {r['status']} license cannot be rebound; issue a new one")
    now = utcnow()
    db.ex("UPDATE license_devices SET active=0, unbound_at=? WHERE license_id=? AND active=1", (now, license_id))
    db.ex("UPDATE license_sessions SET revoked_at=? WHERE license_id=? AND revoked_at IS NULL", (now, license_id))
    db.ex("UPDATE licenses SET status='REBIND_PENDING' WHERE id=?", (license_id,))
    audit("DEVICE_REBIND_ADMIN", license_id=license_id, actor=actor, ok=True, reason=reason)
    return view(license_id)


def _expire_if_due(lic) -> bool:
    if lic["expires_at"] and lic["expires_at"] < utcnow() and lic["status"] in ("UNUSED", "ACTIVE", "REBIND_PENDING"):
        db.ex("UPDATE licenses SET status='EXPIRED' WHERE id=?", (lic["id"],))
        audit("LICENSE_EXPIRED", license_id=lic["id"], ok=True)
        return True
    return False


# ------------------------------------------------------------------ activation and sessions (customer side)

def _issue_session(license_id: int, device_id: int, ip: str | None) -> dict:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    db.ex("INSERT INTO license_sessions(license_id, device_id, access_hash, access_expires_at, refresh_hash, refresh_expires_at, created_at, last_used_at, ip) VALUES(?,?,?,?,?,?,?,?,?)",
          (license_id, device_id, _token_hash(access), (now + timedelta(seconds=ACCESS_TTL)).isoformat(timespec="seconds"), _token_hash(refresh),
           (now + timedelta(seconds=REFRESH_TTL)).isoformat(timespec="seconds"), utcnow(), utcnow(), ip))
    audit("LICENSE_SESSION_CREATED", license_id=license_id, ok=True, ip=ip)
    return {"access_token": access, "access_expires_in": ACCESS_TTL, "refresh_token": refresh, "refresh_expires_in": REFRESH_TTL, "token_type": "Bearer"}


def activate(code: str, device_jwk: dict, nonce: str, signature: str, *, ip: str | None = None) -> dict:
    """The one-time claim. Returns a session or raises PermissionError with a customer-safe message."""
    h = code_hash(code)
    if not h:
        audit("LICENSE_ACTIVATION_REJECTED", ok=False, reason="malformed", ip=ip)
        raise PermissionError("Invalid access code.")
    try:
        fp = fingerprint(device_jwk)
        _load_public_key(device_jwk)
    except Exception:
        audit("LICENSE_ACTIVATION_REJECTED", ok=False, reason="bad device key", ip=ip)
        raise PermissionError(GENERIC_REJECT)
    if not consume_challenge(nonce, "activate") or not verify_signature(device_jwk, nonce.encode(), signature):
        audit("DEVICE_BINDING_REJECTED", device=fp, ok=False, reason="device proof failed", ip=ip)
        raise PermissionError(GENERIC_REJECT)
    lic = db.q1("SELECT * FROM licenses WHERE code_hash=?", (h,))
    audit("LICENSE_ACTIVATION_ATTEMPTED", license_id=lic["id"] if lic else None, device=fp, ok=bool(lic), reason="code presented", ip=ip)
    if not lic:
        audit("LICENSE_ACTIVATION_REJECTED", device=fp, ok=False, reason="unknown code", ip=ip)
        raise PermissionError("Invalid access code.")
    if _expire_if_due(lic):
        lic = db.q1("SELECT * FROM licenses WHERE id=?", (lic["id"],))
    if lic["status"] == "REVOKED":
        audit("LICENSE_ACTIVATION_REJECTED", license_id=lic["id"], device=fp, ok=False, reason="revoked", ip=ip)
        raise PermissionError("This license has been revoked.")
    if lic["status"] == "EXPIRED":
        audit("LICENSE_ACTIVATION_REJECTED", license_id=lic["id"], device=fp, ok=False, reason="expired", ip=ip)
        raise PermissionError("This license has expired.")
    if lic["status"] == "ACTIVE":
        dev = db.q1("SELECT * FROM license_devices WHERE license_id=? AND active=1", (lic["id"],))
        if dev and dev["fingerprint"] == fp:
            db.ex("UPDATE license_devices SET last_seen_at=? WHERE id=?", (utcnow(), dev["id"]))
            db.ex("UPDATE licenses SET last_seen_at=? WHERE id=?", (utcnow(), lic["id"]))
            audit("LICENSE_ACTIVATED", license_id=lic["id"], device=fp, ok=True, reason="same device re-activation", ip=ip)
            return {"license_id": lic["id"], "license_type": lic["license_type"], "expires_at": lic["expires_at"], "reactivated": True, **_issue_session(lic["id"], dev["id"], ip)}
        audit("DEVICE_BINDING_REJECTED", license_id=lic["id"], device=fp, ok=False, reason="different device", ip=ip)
        raise PermissionError("This access code has already been activated on another device.")
    # UNUSED or REBIND_PENDING: exactly one caller can claim the row
    now = utcnow()
    cur = db.ex("UPDATE licenses SET status='ACTIVE', activated_at=COALESCE(activated_at, ?), last_seen_at=?, activation_count=activation_count+1 WHERE id=? AND status IN ('UNUSED','REBIND_PENDING')", (now, now, lic["id"]))
    if cur.rowcount != 1:
        audit("LICENSE_ACTIVATION_REJECTED", license_id=lic["id"], device=fp, ok=False, reason="lost the race", ip=ip)
        raise PermissionError("This access code has already been activated on another device.")
    dcur = db.ex("INSERT INTO license_devices(license_id, fingerprint, public_jwk, active, created_at, last_seen_at) VALUES(?,?,?,1,?,?)",
                 (lic["id"], fp, jdump({k: device_jwk[k] for k in ("kty", "crv", "x", "y")}), now, now))
    db.ex("UPDATE licenses SET device_id_hash=? WHERE id=?", (fp, lic["id"]))
    audit("DEVICE_BINDING_CREATED", license_id=lic["id"], device=fp, ok=True, ip=ip)
    audit("LICENSE_ACTIVATED", license_id=lic["id"], device=fp, ok=True, ip=ip)
    return {"license_id": lic["id"], "license_type": lic["license_type"], "expires_at": lic["expires_at"], "reactivated": False, **_issue_session(lic["id"], dcur.lastrowid, ip)}


def refresh(refresh_token: str, nonce: str, signature: str, *, ip: str | None = None) -> dict:
    """Rotate the session. Requires the refresh token AND a fresh signature from the bound device key."""
    s = db.q1("SELECT s.*, d.public_jwk, d.fingerprint, d.active AS device_active FROM license_sessions s JOIN license_devices d ON d.id=s.device_id WHERE s.refresh_hash=?", (_token_hash(refresh_token or ""),))
    if not s or s["revoked_at"] or s["refresh_expires_at"] < utcnow():
        audit("LICENSE_SESSION_REJECTED", license_id=s["license_id"] if s else None, ok=False, reason="refresh invalid", ip=ip)
        raise PermissionError("Sign-in expired. Enter your access code again on this device.")
    jwk = jload(s["public_jwk"], {})
    if not consume_challenge(nonce, "refresh") or not verify_signature(jwk, nonce.encode(), signature):
        audit("LICENSE_SESSION_REJECTED", license_id=s["license_id"], device=s["fingerprint"], ok=False, reason="device proof failed", ip=ip)
        raise PermissionError("This device could not be verified.")
    lic = db.q1("SELECT * FROM licenses WHERE id=?", (s["license_id"],))
    _expire_if_due(lic)
    lic = db.q1("SELECT * FROM licenses WHERE id=?", (s["license_id"],))
    if lic["status"] != "ACTIVE" or not s["device_active"]:
        audit("LICENSE_SESSION_REJECTED", license_id=lic["id"], device=s["fingerprint"], ok=False, reason=f"license {lic['status']}", ip=ip)
        raise PermissionError({"REVOKED": "This license has been revoked.", "EXPIRED": "This license has expired."}.get(lic["status"], "This license is not active."))
    db.ex("UPDATE license_sessions SET revoked_at=? WHERE id=?", (utcnow(), s["id"]))     # rotation: the old pair dies
    db.ex("UPDATE license_devices SET last_seen_at=? WHERE id=?", (utcnow(), s["device_id"]))
    db.ex("UPDATE licenses SET last_seen_at=? WHERE id=?", (utcnow(), lic["id"]))
    return {"license_id": lic["id"], "license_type": lic["license_type"], "expires_at": lic["expires_at"], **_issue_session(lic["id"], s["device_id"], ip)}


def authenticate(access_token: str | None) -> dict | None:
    """Every protected request: token -> live session -> ACTIVE license -> active device. None means no access."""
    if not access_token:
        return None
    s = db.q1("SELECT s.id, s.license_id, s.device_id, s.access_expires_at, s.revoked_at, l.status, l.license_type, l.expires_at, d.active AS device_active FROM license_sessions s JOIN licenses l ON l.id=s.license_id JOIN license_devices d ON d.id=s.device_id WHERE s.access_hash=?",
              (_token_hash(access_token),))
    if not s or s["revoked_at"] or s["access_expires_at"] < utcnow():
        return None
    if s["expires_at"] and s["expires_at"] < utcnow():
        db.ex("UPDATE licenses SET status='EXPIRED' WHERE id=? AND status IN ('UNUSED','ACTIVE','REBIND_PENDING')", (s["license_id"],))
        return None
    if s["status"] != "ACTIVE" or not s["device_active"]:
        return None
    db.ex("UPDATE license_sessions SET last_used_at=? WHERE id=?", (utcnow(), s["id"]))
    return {"license_id": s["license_id"], "license_type": s["license_type"], "session_id": s["id"]}


def logout(access_token: str | None) -> None:
    if access_token:
        db.ex("UPDATE license_sessions SET revoked_at=? WHERE access_hash=? AND revoked_at IS NULL", (utcnow(), _token_hash(access_token)))


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m hunter.licensing", description="Property Hunter licenses (runs on the server; codes are shown once)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="create codes and print them ONCE"); g.add_argument("--count", type=int, default=1); g.add_argument("--type", default="SINGLE_USER", choices=TYPES); g.add_argument("--days", type=int, default=None); g.add_argument("--note", default="")
    l = sub.add_parser("list", help="list licenses (no codes)"); l.add_argument("--status", default=None)
    v = sub.add_parser("view"); v.add_argument("id", type=int)
    r = sub.add_parser("revoke"); r.add_argument("id", type=int); r.add_argument("--reason", default="")
    b = sub.add_parser("rebind", help="admin device-loss recovery: end the old binding, allow one new activation"); b.add_argument("id", type=int); b.add_argument("--reason", default="")
    sub.add_parser("admin-token", help="print the operator's admin token (created on first use)")
    sub.add_parser("init", help="create the pepper and admin token files")
    a = ap.parse_args(argv)
    db.init_db()
    if a.cmd == "generate":
        for x in create(a.count, a.type, a.days, created_by="cli", metadata={"note": a.note} if a.note else None):
            print(f"{x['code']}    id={x['id']}  type={x['license_type']}  expires={x['expires_at'] or 'never'}")
        print("CODE ISSUED — STORE SECURELY. The server keeps only a hash; these codes cannot be shown again.")
    elif a.cmd == "list":
        for x in list_all(a.status):
            print(f"#{x['id']:<5} {x['status']:<15} {x['license_type']:<12} created {x['created_at'][:16]}  activated {(x['activated_at'] or '-')[:16]}  last seen {(x['last_seen_at'] or '-')[:16]}  expires {(x['expires_at'] or 'never')[:10]}  device {'bound' if x['device']['bound'] else 'none'}  sessions {x['sessions_active']}")
    elif a.cmd == "view":
        print(json.dumps(view(a.id), indent=1))
    elif a.cmd == "revoke":
        print(json.dumps(revoke(a.id, "cli", a.reason), indent=1))
    elif a.cmd == "rebind":
        print(json.dumps(rebind(a.id, "cli", a.reason), indent=1))
    elif a.cmd == "admin-token":
        print(admin_token())
    elif a.cmd == "init":
        pepper(); admin_token(); print(f"pepper and admin token are in {DATA_DIR} (mode 600). Show the token with: python -m hunter.licensing admin-token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
