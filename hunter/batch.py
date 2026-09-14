"""Batch investigation: run the full 18-stage investigation over many
properties in the background, write a PDF per property to the Desktop, and
keep an index. Progress is real (one row at a time) and it can be stopped.
"""
from __future__ import annotations

import csv
import threading
from pathlib import Path

from . import db, store
from .db import jdump, setting, set_setting, utcnow

_thread: threading.Thread | None = None
_stop = threading.Event()
_state: dict = {"running": False, "total": 0, "done": 0, "failed": 0, "current": None,
                "started_at": None, "finished_at": None, "folder": None, "results": []}
_lock = threading.Lock()


def status() -> dict:
    with _lock:
        s = dict(_state)
        s["results"] = s["results"][-10:]
        return s


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def stop() -> None:
    _stop.set()


def folder() -> Path:
    from .desktop import DESKTOP_DIR
    f = Path(DESKTOP_DIR) / "Investigations"
    f.mkdir(parents=True, exist_ok=True)
    return f


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -" else "" for c in name).strip()[:60] or "property"


def _write_index(f: Path) -> None:
    rows = db.q("""SELECT i.property_id, p.address, p.owner_name, p.total_value, p.recommendation,
                          i.finished_at, i.summary_json
                   FROM investigations i JOIN properties p ON p.id=i.property_id
                   WHERE i.status='complete' ORDER BY i.finished_at DESC""")
    with (f / "INVESTIGATIONS - index.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Address", "Owner of record", "County appraised", "Our call", "Deal or trap",
                    "Overall score", "Risk score", "Still unknown", "Investigated at", "PDF", "Open in app"])
        seen = set()
        for r in rows:
            if r["property_id"] in seen:
                continue
            seen.add(r["property_id"])
            s = db.jload(r["summary_json"], {}) or {}
            sc = s.get("scores") or {}
            w.writerow([r["address"], r["owner_name"] or "unknown",
                        f"{r['total_value']:,.0f}" if r["total_value"] else "",
                        r["recommendation"] or "", (s.get("deal_or_trap") or {}).get("verdict", ""),
                        sc.get("overall", ""), sc.get("risk", ""),
                        "; ".join(s.get("open_questions") or [])[:120],
                        (r["finished_at"] or "")[:16],
                        f"{_safe(r['address'] or str(r['property_id']))}.pdf",
                        f"http://127.0.0.1:8234/#property/{r['property_id']}"])


def _run(ids: list[int], make_pdf: bool) -> None:
    from . import pdf, reports
    from .investigator import investigate
    f = folder()
    with _lock:
        _state.update(running=True, total=len(ids), done=0, failed=0, current=None,
                      started_at=utcnow(), finished_at=None, folder=str(f), results=[])
    for pid in ids:
        if _stop.is_set():
            break
        p = store.get_property(pid)
        if not p:
            continue
        label = p.get("address") or p.get("parcel_id") or str(pid)
        with _lock:
            _state["current"] = label
        try:
            out = investigate(pid)
            entry = {"id": pid, "address": label, "recommendation": out["summary"]["recommendation"],
                     "verdict": out["summary"]["deal_or_trap"]["verdict"], "pdf": None}
            if make_pdf:
                try:
                    data = pdf.html_to_pdf(reports.dossier_html(pid))
                    path = f / f"{_safe(label)}.pdf"
                    path.write_bytes(data)
                    entry["pdf"] = path.name
                except Exception as exc:
                    entry["pdf_error"] = str(exc)[:120]
            with _lock:
                _state["done"] += 1
                _state["results"].append(entry)
        except Exception as exc:
            with _lock:
                _state["failed"] += 1
                _state["results"].append({"id": pid, "address": label, "error": str(exc)[:160]})
            db.log(None, f"batch investigation failed for #{pid}: {exc}", level="error",
                   source="batch")
    try:
        _write_index(f)
    except Exception as exc:                          # pragma: no cover
        db.log(None, f"batch index failed: {exc}", level="error", source="batch")
    with _lock:
        _state.update(running=False, current=None, finished_at=utcnow())
    set_setting("last_batch", {k: v for k, v in _state.items() if k != "results"})
    store.add_alert(None, "batch_done",
                    f"Investigated {_state['done']} properties"
                    + (f" ({_state['failed']} failed)" if _state["failed"] else ""),
                    f"PDFs and an index are in {f}", "info")


def start(ids: list[int], make_pdf: bool = True) -> dict:
    global _thread
    if is_running():
        return {"started": False, "reason": "a batch is already running", **status()}
    ids = [int(i) for i in dict.fromkeys(ids)]
    if not ids:
        return {"started": False, "reason": "nothing to investigate"}
    _stop.clear()
    _thread = threading.Thread(target=_run, args=(ids, make_pdf), daemon=True,
                               name="property-batch")
    _thread.start()
    return {"started": True, "count": len(ids), "folder": str(folder())}
