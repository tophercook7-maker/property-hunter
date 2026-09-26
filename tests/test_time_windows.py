"""A time window must not depend on how a timestamp was spelled.

db.utcnow() writes ISO 8601 with a "T" and a UTC offset. SQLite's
datetime('now') returns "YYYY-MM-DD HH:MM:SS" with a space. Compared as raw
strings, "T" (0x54) sorts above " " (0x20), so a row written earlier today
reads as NEWER than now and lands inside every short window.

The date part usually dominates, which is why this hid: a seven-day window is
only wrong on its boundary day. A one-day window is half wrong, and a
two-minute window matched 2.9 million rows.
"""
from __future__ import annotations

import sqlite3

import pytest


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, at TEXT)")
    return c


def test_raw_comparison_is_wrong_within_the_same_day():
    c = _db()
    # written an hour ago, in the format db.utcnow() produces
    c.execute("INSERT INTO t(at) VALUES (strftime('%Y-%m-%dT%H:%M:%S','now','-1 hour') || '+00:00')")
    naive = c.execute("SELECT COUNT(*) FROM t WHERE at > datetime('now','-2 minutes')").fetchone()[0]
    assert naive == 1, "this is the bug: an hour-old row inside a two-minute window"


def test_normalised_comparison_is_right():
    c = _db()
    c.execute("INSERT INTO t(at) VALUES (strftime('%Y-%m-%dT%H:%M:%S','now','-1 hour') || '+00:00')")
    fixed = c.execute("SELECT COUNT(*) FROM t WHERE datetime(replace(at,'T',' ')) > datetime('now','-2 minutes')").fetchone()[0]
    assert fixed == 0


def test_normalisation_handles_both_spellings_and_the_offset():
    c = _db()
    c.execute("INSERT INTO t(at) VALUES (strftime('%Y-%m-%dT%H:%M:%S','now','-10 minutes') || '+00:00')")
    c.execute("INSERT INTO t(at) VALUES (datetime('now','-10 minutes'))")
    c.execute("INSERT INTO t(at) VALUES (strftime('%Y-%m-%dT%H:%M:%S','now','-3 hours') || '+00:00')")
    n = c.execute("SELECT COUNT(*) FROM t WHERE datetime(replace(at,'T',' ')) > datetime('now','-1 hour')").fetchone()[0]
    assert n == 2, "both spellings of ten-minutes-ago are in the hour; three-hours-ago is not"


@pytest.mark.parametrize("path,needle", [
    ("tools/build_share.py", "detected_at"),
    ("hunter/reports.py", "first_seen"),
    ("hunter/api.py", "first_seen"),
    ("hunter/ask.py", "first_seen"),
])
def test_no_raw_window_comparisons_remain(path, needle):
    """Any future `col > datetime('now', ...)` on a timestamp column has to be
    normalised, or it silently over-matches inside the current day."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / path).read_text()
    bad = re.findall(rf"(?<!replace\()\b[\w.]*{needle}\s*>=?\s*datetime\('now'", src)
    assert not bad, f"{path}: un-normalised window comparison on {needle}: {bad}"
