"""A rebuild that found nothing new must not rewrite the site.

Every build stamps a fresh built_at. Left alone, that made a no-op rebuild
rewrite all 75 county files, and the publish loop committed roughly 100 MB of
churn for no change: 34 of 40 consecutive auto-commits touched every county.

The subtle half is why the first attempt at this did not work. Comparing the
live objects against the parsed file never matches, because labels are built as
tuples and JSON hands them back as lists. The comparison has to be against what
would actually be written.
"""
from __future__ import annotations

import json

import tools.build_share as bs


def _doc():
    return {"built_at": "2026-01-01T00:00:00+00:00",
            "labels": {"vacant": ("On the vacant register", "distress")},   # tuples, as build() makes them
            "rows": [{"i": 1, "s": 80}], "published": 1}


def test_identical_content_is_not_rewritten(tmp_path):
    p = tmp_path / "c.json"
    assert bs.dump_if_changed(_doc(), str(p)) is True          # first write
    before = p.read_bytes()
    assert bs.dump_if_changed(_doc(), str(p)) is False         # same content
    assert p.read_bytes() == before


def test_a_new_timestamp_alone_is_not_a_change(tmp_path):
    p = tmp_path / "c.json"
    bs.dump_if_changed(_doc(), str(p))
    before = p.read_bytes()
    d = _doc(); d["built_at"] = "2099-12-31T23:59:59+00:00"
    assert bs.dump_if_changed(d, str(p)) is False
    assert p.read_bytes() == before, "the file keeps the stamp from when its data last changed"


def test_tuples_do_not_count_as_a_change(tmp_path):
    """The bug that made the first fix silently do nothing."""
    p = tmp_path / "c.json"
    bs.dump_if_changed(_doc(), str(p))
    d = _doc()
    d["labels"] = {"vacant": ["On the vacant register", "distress"]}   # list, not tuple
    assert bs.dump_if_changed(d, str(p)) is False, \
        "a tuple and the list JSON turns it into are the same published bytes"


def test_a_real_change_is_written(tmp_path):
    p = tmp_path / "c.json"
    bs.dump_if_changed(_doc(), str(p))
    d = _doc(); d["rows"] = [{"i": 1, "s": 81}]
    assert bs.dump_if_changed(d, str(p)) is True
    assert json.loads(p.read_text())["rows"][0]["s"] == 81


def test_a_missing_or_corrupt_file_is_written(tmp_path):
    p = tmp_path / "c.json"
    assert bs.dump_if_changed(_doc(), str(p)) is True
    p.write_text("{not json")
    assert bs.dump_if_changed(_doc(), str(p)) is True
