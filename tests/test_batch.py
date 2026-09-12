"""Batch investigation (spec 32, 73): many properties, real progress, a stop,
PDFs and an index on the Desktop."""
import time

import pytest
from fastapi.testclient import TestClient

from conftest import make_record
from hunter import batch, db, distress, scoring, store


@pytest.fixture
def client():
    from hunter.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def props(boundaries):
    ids = []
    for i in range(4):
        pid, _, _ = store.ingest(make_record(parcel_id=f"300-5{i}", address=f"{i+1} Batch St"))
        distress.refresh(store.get_property(pid)); scoring.compute(store.get_property(pid))
        ids.append(pid)
    return ids


@pytest.fixture
def fast(monkeypatch, tmp_path):
    """No network, no Chrome: a fake investigator and a fake PDF, Desktop in tmp."""
    from hunter import investigator, pdf as pdfmod, desktop
    monkeypatch.setattr(desktop, "DESKTOP_DIR", tmp_path)
    calls = []
    def fake_investigate(pid):
        calls.append(pid); time.sleep(0.02)
        p = store.get_property(pid)
        db.ex("INSERT INTO investigations(property_id,status,summary_json,finished_at) VALUES(?,?,?,?)",
              (pid, "complete", db.jdump({"recommendation": "WATCH",
                                          "deal_or_trap": {"verdict": "MAYBE"},
                                          "scores": {"overall": 55, "risk": 40},
                                          "open_questions": ["title"]}), db.utcnow()))
        return {"summary": {"recommendation": "WATCH", "deal_or_trap": {"verdict": "MAYBE"}}}
    monkeypatch.setattr(investigator, "investigate", fake_investigate)
    monkeypatch.setattr(pdfmod, "html_to_pdf", lambda html, timeout=60: b"%PDF-fake")
    return calls, tmp_path


def _wait():
    for _ in range(200):
        if not batch.is_running():
            return
        time.sleep(0.05)
    raise AssertionError("batch did not finish")


def test_batch_runs_every_id_writes_pdfs_and_an_index(props, fast):
    calls, folder = fast
    r = batch.start(props)
    assert r["started"] and r["count"] == 4
    _wait()
    s = batch.status()
    assert s["done"] == 4 and s["failed"] == 0 and not s["running"]
    assert sorted(calls) == sorted(props)
    inv = folder / "Investigations"
    assert len(list(inv.glob("*.pdf"))) == 4
    index = (inv / "INVESTIGATIONS - index.csv").read_text()
    assert "1 Batch St" in index and "WATCH" in index and "MAYBE" in index
    assert db.q1("SELECT 1 FROM alerts WHERE kind='batch_done'")


def test_batch_can_be_stopped_and_refuses_to_double_start(props, fast, monkeypatch):
    from hunter import investigator
    slow_calls = []
    def slow(pid):
        slow_calls.append(pid); time.sleep(0.3)
        return {"summary": {"recommendation": "WATCH", "deal_or_trap": {"verdict": "MAYBE"}}}
    monkeypatch.setattr(investigator, "investigate", slow)
    assert batch.start(props)["started"]
    assert batch.start(props)["started"] is False
    time.sleep(0.1); batch.stop(); _wait()
    assert batch.status()["done"] < 4          # stopped early
    assert len(slow_calls) < 4


def test_a_failing_property_does_not_stop_the_batch(props, fast, monkeypatch):
    from hunter import investigator
    def flaky(pid):
        if pid == props[1]:
            raise RuntimeError("boom")
        return {"summary": {"recommendation": "WATCH", "deal_or_trap": {"verdict": "MAYBE"}}}
    monkeypatch.setattr(investigator, "investigate", flaky)
    batch.start(props); _wait()
    s = batch.status()
    assert s["done"] == 3 and s["failed"] == 1
    assert any(x.get("error") for x in s["results"])


def test_batch_endpoints_accept_ids_top_and_filters(client, props, fast):
    r = client.post("/api/investigate/batch", json={"top": 2}).json()
    assert r["started"] and r["count"] == 2
    _wait()
    r = client.post("/api/investigate/batch", json={"property_type": "house", "pdf": False}).json()
    assert r["started"] and r["count"] == 4
    _wait()
    s = client.get("/api/investigate/batch/status").json()
    assert s["done"] == 4 and not s["running"]
    assert client.post("/api/investigate/batch", json={"ids": []}).json()["started"] is False
    assert client.post("/api/investigate/batch/stop").json()["stopping"]
