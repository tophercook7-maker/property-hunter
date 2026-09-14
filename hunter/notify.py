"""The morning email: what changed, what to look at, nothing invented.

One plain-text digest per day (or on demand) to the address Topher sets. It is
built from the same tables the UI shows - alerts, changes on watched
properties, State Lands arrivals and departures, the current picks - so the
email never says something the app does not.

Delivery uses the Gmail app password the mailer lane already keeps at
~/.config/mailer/gmail-app-password (never copied into code or the database).
No password file, or notifications switched off, means the digest is written
to the log and the Desktop briefing only - nothing fails loudly, nothing is
faked as sent.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from . import db
from .config import PORT
from .db import setting, set_setting, utcnow

PW_FILE = Path(os.environ.get("PH_GMAIL_PW_FILE") or Path.home() / ".config" / "mailer" / "gmail-app-password")
FROM = os.environ.get("PH_MAIL_FROM", "topher.cook7@gmail.com")
FROM_NAME = "Property Hunter"
SITE = "https://tophercook7-maker.github.io/property-hunter"
DEFAULTS = {"enabled": False, "to": FROM, "only_when_something_changed": True}


def config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (setting("notify", {}) or {}).items() if k in DEFAULTS})
    cfg["last_digest"] = setting("last_digest")
    cfg["can_send"] = PW_FILE.exists()
    cfg["password_file"] = str(PW_FILE)
    return cfg


def update(changes: dict) -> dict:
    cfg = {k: v for k, v in (setting("notify", {}) or {}).items() if k in DEFAULTS}
    for k, v in changes.items():
        if k in DEFAULTS:
            cfg[k] = v
    set_setting("notify", cfg)
    return config()


# ---------------------------------------------------------------- digest ----

def _since() -> str:
    last = setting("last_digest")
    if last:
        return last
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")


def build(since: str | None = None) -> dict:
    """Everything worth an email since `since` (default: the last digest, else 24h)."""
    since = since or _since()
    alerts = db.rows_to_dicts(db.q(
        """SELECT a.*, p.address, p.parcel_id, p.owner_name FROM alerts a
           LEFT JOIN properties p ON p.id=a.property_id
           WHERE a.created_at > ? AND a.kind != 'briefing' ORDER BY a.severity DESC, a.id DESC LIMIT 60""",
        (since,)))
    watched = db.rows_to_dicts(db.q(
        """SELECT c.field, c.old_value, c.new_value, c.severity, c.detected_at, p.id, p.address, p.parcel_id
           FROM changes c JOIN watchlist w ON w.property_id=c.property_id
           JOIN properties p ON p.id=c.property_id
           WHERE c.detected_at > ? AND c.severity IN ('medium','high')
           ORDER BY c.detected_at DESC LIMIT 60""", (since,)))
    arrivals = db.rows_to_dicts(db.q(
        """SELECT p.id, p.address, p.city, p.owner_name, p.total_value, e.value FROM evidence e
           JOIN properties p ON p.id=e.property_id
           WHERE e.field='tax_delinquent' AND e.source='cosl_listings' AND e.created_at > ? AND p.excluded=0
           ORDER BY p.total_value DESC LIMIT 25""", (since,)))
    departures = db.rows_to_dicts(db.q(
        """SELECT p.id, p.address, p.city, e.value FROM evidence e JOIN properties p ON p.id=e.property_id
           WHERE e.field='tax_delinquent_removed' AND e.created_at > ? AND p.excluded=0 LIMIT 25""", (since,)))
    from .analyzers import topher_picks
    try:
        picks = topher_picks(3)
    except Exception:
        picks = []
    last_scan = db.q1("SELECT id, mode, territory, status, finished_at FROM scans ORDER BY id DESC LIMIT 1")
    return {"since": since, "alerts": alerts, "watched_changes": watched, "state_lands_new": arrivals,
            "state_lands_gone": departures, "picks": picks,
            "last_scan": dict(last_scan) if last_scan else None,
            "something_changed": bool(alerts or watched or arrivals or departures)}


def render(d: dict) -> tuple[str, str]:
    """(subject, plain-text body)."""
    n = len(d["alerts"]) + len(d["watched_changes"]) + len(d["state_lands_new"]) + len(d["state_lands_gone"])
    day = datetime.now().strftime("%a %b %-d")
    subject = f"Property Hunter {day}: " + (f"{n} things moved" if n else "quiet")
    L = [f"PROPERTY HUNTER - {datetime.now().strftime('%A, %B %-d, %Y')}",
         f"Covering everything since {d['since'][:16].replace('T', ' ')} UTC.", ""]
    def prop(p):
        return f"{p.get('address') or p.get('parcel_id') or 'parcel'}" + (f" ({p['city'].title()})" if p.get("city") else "")
    if d["watched_changes"]:
        L += ["YOUR WATCHLIST", "-" * 14]
        for c in d["watched_changes"]:
            L.append(f"* {c['address'] or c['parcel_id']}: {c['field']} {c['old_value'] or '(blank)'} -> {c['new_value'] or '(blank)'}"
                     f"  [{c['severity']}]  http://127.0.0.1:{PORT}/#property/{c['id']}")
        L.append("")
    if d["state_lands_new"]:
        L += ["NEW IN THE STATE'S TAX-SALE INVENTORY (outside Hot Springs Village / Diamondhead)", "-" * 40]
        for a in d["state_lands_new"]:
            L.append(f"* {prop(a)} - {a['owner_name'] or 'no owner name'} - appraised ${a['total_value'] or 0:,.0f}")
            L.append(f"    {a['value']}")
        L.append("")
    if d["state_lands_gone"]:
        L += ["LEFT THE STATE'S INVENTORY (sold or redeemed)", "-" * 28]
        for a in d["state_lands_gone"]:
            L.append(f"* {prop(a)} - {a['value']}")
        L.append("")
    if d["alerts"]:
        L += ["ALERTS", "-" * 6]
        for a in d["alerts"]:
            where = f" - {a['address'] or a['parcel_id']}" if a.get("address") or a.get("parcel_id") else ""
            L.append(f"* [{a['severity']}] {a['title']}{where}")
            if a.get("body"):
                L.append(f"    {a['body'][:220]}")
        L.append("")
    if d["picks"]:
        L += ["TODAY'S THREE", "-" * 12]
        for i, p in enumerate(d["picks"], 1):
            L.append(f"{i}. {p.get('address') or p.get('parcel_id')} - score {p.get('score', 0):.0f} - "
                     f"http://127.0.0.1:{PORT}/#property/{p.get('id')}")
        L.append("")
    if not d["something_changed"]:
        L += ["Nothing moved since the last look. The scan ran; the registers and the State's inventory are unchanged.", ""]
    if d.get("last_scan"):
        s = d["last_scan"]
        L.append(f"Last scan: #{s['id']} {s['mode']} {s['territory']} - {s['status']} at {(s.get('finished_at') or '')[:16].replace('T', ' ')}")
    L += ["", f"App: http://127.0.0.1:{PORT}   Site: {SITE}",
          "Every line above comes from a public record the app can show you with its source and date.",
          "Nothing here is legal, tax or investment advice."]
    return subject, "\n".join(L)


# ------------------------------------------------------------------ send ----

def _smtp_send(to: str, subject: str, body: str) -> None:
    pw = PW_FILE.read_text().strip().replace(" ", "")
    msg = EmailMessage()
    msg["From"] = f"{FROM_NAME} <{FROM}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(FROM, pw)
        s.send_message(msg)


def send_digest(force: bool = False, dry: bool = False) -> dict:
    """Build and (if configured) send the digest. Honest about what happened."""
    cfg = config()
    d = build()
    subject, body = render(d)
    out = {"subject": subject, "to": cfg["to"], "items": {k: len(d[k]) for k in
           ("alerts", "watched_changes", "state_lands_new", "state_lands_gone")},
           "sent": False, "reason": None, "body": body}
    if dry:
        out["reason"] = "dry run"
        return out
    if not cfg["enabled"] and not force:
        out["reason"] = "email notifications are switched off"
        return out
    if cfg["only_when_something_changed"] and not d["something_changed"] and not force:
        out["reason"] = "nothing changed, and you asked to only hear when something does"
        set_setting("last_digest", utcnow())
        return out
    if not cfg["can_send"]:
        out["reason"] = f"no Gmail app password at {cfg['password_file']}"
        db.log(None, f"digest not sent: {out['reason']}", level="warn", source="notify")
        return out
    try:
        _smtp_send(cfg["to"], subject, body)
    except Exception as exc:
        out["reason"] = f"send failed: {exc}"
        db.log(None, out["reason"], level="error", source="notify")
        return out
    out["sent"] = True
    set_setting("last_digest", utcnow())
    db.log(None, f"digest emailed to {cfg['to']}: {subject}", source="notify")
    return out
