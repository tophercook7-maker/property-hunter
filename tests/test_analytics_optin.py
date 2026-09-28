"""Counting visitors must be opt-in, must not identify anyone, and must never
break a page.

The site is static on GitHub Pages, which exposes no access log, so without a
beacon there is no way to know whether anyone has ever opened it. That is worth
fixing and is not worth a tracking script the owner did not choose.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_the_self_hosted_endpoint_must_be_https_or_absent():
    e = (DOCS / "analytics-endpoint.txt").read_text().strip()
    assert e == "" or re.fullmatch(r"https://[^\s\"']+", e), \
        "the counter endpoint is one https URL or nothing; http would leak the referrer in clear"


def test_the_counter_refuses_to_send_anything_identifying():
    js = (DOCS / "analytics.js").read_text()
    for forbidden in ("navigator.userAgent", "screen.width", "canvas", "localStorage",
                      "document.cookie", "navigator.plugins"):
        assert forbidden not in js, f"the beacon must not collect {forbidden}"
    assert "location.pathname" in js and "document.referrer" in js, \
        "path and referrer are the whole payload"


def test_prerenders_are_not_counted():
    js = (DOCS / "analytics.js").read_text()
    assert "prerender" in js, "a page nobody looked at is not a visit"


def test_it_counts_nothing_until_a_token_is_set():
    tok = (DOCS / "analytics-token.txt").read_text().strip()
    assert tok == "" or re.fullmatch(r"[0-9a-f]{20,40}", tok, re.I), \
        "the token file holds one Cloudflare token or nothing at all"


def test_the_loader_refuses_a_placeholder():
    js = (DOCS / "analytics.js").read_text()
    assert re.search(r"\[0-9a-f\]\{20,40\}", js), \
        "the script must validate the token, or a placeholder loads a broken beacon"
    assert ".catch(" in js, "a counting failure must never break the page"


def test_no_other_tracker_crept_in():
    bad = re.compile(r"google-analytics|googletagmanager|gtag\(|facebook\.net|hotjar|fullstory", re.I)
    hits = [p for p in glob.glob(str(DOCS / "*.html")) + glob.glob(str(DOCS / "*.js"))
            if bad.search(Path(p).read_text(errors="replace"))]
    assert not hits, f"an identifying tracker was added: {hits}"


def test_every_page_is_counted_the_same_way():
    pages = [Path(p) for p in glob.glob(str(DOCS / "*.html"))]
    missing = [p.name for p in pages if "analytics.js" not in p.read_text(errors="replace")]
    assert not missing, f"pages that would not be counted: {missing}"
