"""Background scheduler: the part that lets the app say "I've been looking".

One daemon thread. It sleeps, wakes, checks whether a scan is due, runs it
through the normal scanner (so the UI sees the same real stages), and when the
scan finishes writes the morning briefing as an alert. Nothing here contacts
anybody or spends anything - it only reads sources the scanner already reads.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from . import db, scanner
from .db import setting, set_setting, utcnow

DEFAULTS = {
    "enabled": True,
    "interval_hours": 24,
    "mode": "distress",
    "limit": 600,
    "enrich_top": 20,
}

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_tick: str | None = None


def config() -> dict:
    stored = setting("schedule", {}) or {}
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
    cfg["next_run"] = setting("next_scan")
    cfg["last_auto_run"] = setting("last_auto_scan")
    cfg["thread_alive"] = bool(_thread and _thread.is_alive())
    cfg["last_tick"] = _last_tick
    return cfg


def update(changes: dict) -> dict:
    cfg = {k: v for k, v in (setting("schedule", {}) or {}).items() if k in DEFAULTS}
    for k, v in changes.items():
        if k in DEFAULTS:
            cfg[k] = v
    set_setting("schedule", cfg)
    if cfg.get("enabled", True):
        _schedule_next(force=("interval_hours" in changes or "enabled" in changes))
    else:
        set_setting("next_scan", None)
    return config()


def _schedule_next(force: bool = False) -> str:
    cfg = config()
    current = setting("next_scan")
    if current and not force:
        return current
    hours = float(cfg.get("interval_hours") or 24)
    nxt = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")
    set_setting("next_scan", nxt)
    return nxt


def due() -> bool:
    cfg = config()
    if not cfg.get("enabled", True):
        return False
    nxt = setting("next_scan")
    if not nxt:
        _schedule_next()
        return False
    try:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(nxt)
    except ValueError:
        _schedule_next(force=True)
        return False


def run_now(reason: str = "scheduled") -> dict | None:
    """Kick a scan through the normal scanner and, when it ends, file the briefing."""
    if scanner.is_running():
        return None
    cfg = config()
    scan = scanner.start(mode=cfg["mode"], limit=int(cfg["limit"]),
                         enrich_top=int(cfg["enrich_top"]))
    set_setting("last_auto_scan", utcnow())
    _schedule_next(force=True)
    db.log(scan.id, f"scan started by the scheduler ({reason})", source="scheduler")
    threading.Thread(target=_wait_and_brief, args=(scan,), daemon=True).start()
    return scan.as_dict()


def _wait_and_brief(scan: scanner.Scan) -> None:
    while scanner.is_running():
        time.sleep(3)
    from .reports import morning_report
    from .store import add_alert
    try:
        r = morning_report()
    except Exception as exc:                    # pragma: no cover
        db.log(scan.id, f"briefing failed: {exc}", level="error", source="scheduler")
        return
    lines = [r["headline"]]
    if r["new_opportunities"]:
        lines.append(f"{len(r['new_opportunities'])} new opportunities.")
    if r["important_changes"]:
        lines.append(f"{len(r['important_changes'])} important property changes.")
    if r.get("best_deal"):
        b = r["best_deal"]
        lines.append(f"I'd investigate {b['address']} first (score {b['score']:.0f}).")
    if r.get("avoid"):
        lines.append(f"{len(r['avoid'])} look cheap but carry serious problems.")
    if not (r["new_opportunities"] or r["important_changes"]):
        lines.append("Nothing moved since the last look.")
    add_alert(None, "briefing", f"{r['greeting']} Here's what I found.",
              " ".join(lines), "info")
    db.log(scan.id, "briefing filed as an alert", source="scheduler")


def _loop() -> None:
    global _last_tick
    while not _stop.is_set():
        _last_tick = utcnow()
        try:
            if due():
                run_now()
        except Exception as exc:                # pragma: no cover
            db.log(None, f"scheduler tick failed: {exc}", level="error", source="scheduler")
        _stop.wait(60)


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    if config().get("enabled", True) and not setting("next_scan"):
        _schedule_next()
    _thread = threading.Thread(target=_loop, daemon=True, name="property-scheduler")
    _thread.start()


def stop() -> None:
    _stop.set()
