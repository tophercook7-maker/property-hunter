"""The morning digest: built from real tables, honest about sending."""
import pytest
from fastapi.testclient import TestClient

from hunter import notify, store


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c
from hunter.db import set_setting, setting
from tests.conftest import make_record


def _seed():
    pid, _, _ = store.ingest(make_record())
    store.store_evidence(pid, [{"field": "tax_delinquent", "value": "certified to the State; starting bid $141.47",
                               "evidence_type": "FACT", "confidence": "HIGH", "source": "cosl_listings",
                               "source_name": "COSL", "raw_ref": "[key cosl:1]"}])
    store.add_alert(pid, "register_removed", "111 Isabelle St - came off the vacant register", "why?", "medium")
    return pid


def test_digest_lists_real_events_and_nothing_else():
    pid = _seed()
    d = notify.build(since="2000-01-01T00:00:00")
    assert d["something_changed"]
    assert any(a["title"].startswith("111 Isabelle") for a in d["alerts"])
    assert [a["id"] for a in d["state_lands_new"]] == [pid]
    subject, body = notify.render(d)
    assert "things moved" in subject
    assert "111 Isabelle St" in body and "$141.47" in body
    assert "advice" in body                       # the disclaimer rides along


def test_quiet_day_says_so():
    d = notify.build(since="2999-01-01T00:00:00")
    assert not d["something_changed"]
    subject, body = notify.render(d)
    assert subject.endswith("quiet") and "Nothing moved" in body


def test_send_is_honest_when_off_or_unconfigured(monkeypatch, tmp_path):
    _seed()
    notify.update({"enabled": False, "to": "topher@example.com"})
    out = notify.send_digest()
    assert out["sent"] is False and "switched off" in out["reason"]
    notify.update({"enabled": True})
    monkeypatch.setattr(notify, "PW_FILE", tmp_path / "missing")
    out = notify.send_digest()
    assert out["sent"] is False and "no Gmail app password" in out["reason"]


def test_send_uses_smtp_and_records_the_time(monkeypatch, tmp_path):
    _seed()
    notify.update({"enabled": True, "to": "topher@example.com"})
    pw = tmp_path / "pw"; pw.write_text("abcd efgh")
    monkeypatch.setattr(notify, "PW_FILE", pw)
    sent = {}
    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, user, password): sent["login"] = (user, password)
        def send_message(self, msg): sent["to"] = msg["To"]; sent["subject"] = msg["Subject"]
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    out = notify.send_digest()
    assert out["sent"] and sent["to"] == "topher@example.com" and sent["login"][1] == "abcdefgh"
    assert setting("last_digest")
    # a second run with nothing new is skipped, not re-sent
    out2 = notify.send_digest()
    assert out2["sent"] is False and "nothing changed" in out2["reason"]


def test_api_routes(client, monkeypatch):
    r = client.get("/api/notify").json()
    assert "enabled" in r and "can_send" in r
    assert client.post("/api/notify", json={"to": "nope"}).status_code == 400
    r = client.post("/api/notify", json={"to": "a@b.co", "enabled": True}).json()
    assert r["to"] == "a@b.co" and r["enabled"] is True
    r = client.post("/api/notify/send", json={"dry": True}).json()
    assert r["sent"] is False and r["reason"] == "dry run" and "PROPERTY HUNTER" in r["body"]
