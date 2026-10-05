"""The counter must stay a counter.

It records the day, the path, the referring host and a coarse device word.
It must never grow an IP column, a user-agent column, or anything that follows
a person between visits -- that is the whole reason for hosting it rather than
handing the question to somebody else.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("hitcounter", ROOT / "tools" / "hitcounter.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


def test_the_schema_has_nowhere_to_put_an_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "DB", str(tmp_path / "h.db"))
    c = hc.connect()
    cols = {r[1] for r in c.execute("PRAGMA table_info(hits)")}
    assert cols == {"id", "day", "at", "path", "ref_host", "device"}
    for banned in ("ip", "addr", "ua", "user_agent", "session", "visitor", "uid", "fingerprint"):
        assert banned not in cols
    c.close()


def test_only_the_referring_host_is_kept_not_the_query(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "DB", str(tmp_path / "h.db"))
    hc.record("/lookup.html", "https://www.google.com/search?q=someone+private+searching", "Mozilla/5.0")
    row = sqlite3.connect(str(tmp_path / "h.db")).execute("SELECT ref_host FROM hits").fetchone()
    assert row[0] == "www.google.com"
    assert "private" not in row[0], "a search query is the searcher's business"


@pytest.mark.parametrize("ua,want", [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)", "phone"),
    ("Mozilla/5.0 (iPad; CPU OS 17_0)", "tablet"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "desktop"),
    ("Googlebot/2.1 (+http://www.google.com/bot.html)", "bot"),
    ("curl/8.0", "bot"),
    ("", "unknown"),
])
def test_device_is_a_word_not_a_fingerprint(ua, want):
    assert hc.device_of(ua) == want


def test_a_malformed_referrer_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "DB", str(tmp_path / "h.db"))
    for bad in ("", "not a url", "::::", "javascript:alert(1)"):
        hc.record("/", bad, "Mozilla/5.0")
    n = sqlite3.connect(str(tmp_path / "h.db")).execute("SELECT COUNT(*) FROM hits").fetchone()[0]
    assert n == 4


def test_the_report_says_views_not_people(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "DB", str(tmp_path / "h.db"))
    hc.record("/", "", "Mozilla/5.0")
    s = hc.summary()
    assert s["total_views"] == 1
    assert "not people" in s["note"], "the limit has to be stated, not left to be assumed"


@pytest.mark.parametrize("ua", [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Mozilla/5.0 (compatible; facebookexternalhit/1.1)",
    "WhatsApp/2.23.20.0",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "Twitterbot/1.0",
    "LinkedInBot/1.0",
])
def test_link_preview_fetchers_are_not_visitors(ua):
    """These fetch a URL to build the little card under a post. Counting them
    as people turns "I posted and seven came" into a sentence that is not true,
    which is worse than having no counter."""
    assert hc.device_of(ua) == "bot"


def test_a_person_browsing_from_the_facebook_app_still_counts():
    """The in-app browser is a real person reading. Only the fetchers are bots."""
    assert hc.device_of("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1 [FBAN/FBIOS]") == "phone"
