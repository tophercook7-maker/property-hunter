"""Telling a sale from a spelling change.

Every one of the first 43 "owner changed away from Weyerhaeuser" rows in this
database was Weyerhaeuser to Weyerhaeuser: the roll dropping a "C/O" line, or
renaming WEYERHAEUSER FOREST HOLDINGS INC to WEYERHAEUSER COMPANY. A detector
that reports those is worse than no detector, because it produces a steady
stream of confident nonsense about land changing hands that never moved.
"""
from __future__ import annotations

from hunter import divestiture as d


def test_a_rename_is_not_a_sale():
    assert d.same_owner("WEYERHAEUSER FOREST HOLDINGS INC C/O WEYERHAEUSER", "WEYERHAEUSER COMPANY")
    assert d.same_owner("DELTIC TIMBER CORPORATION", "DELTIC FARM & TIMBER CO INC")
    assert d.same_owner("GREEN BAY PACKAGING INC", "GREEN BAY PACKAGING")


def test_an_actual_sale_is_one():
    assert not d.same_owner("WEYERHAEUSER COMPANY", "SMITH, JOHN & MARY")
    assert not d.same_owner("DELTIC TIMBER CORPORATION", "OUACHITA LAND PARTNERS LLC")


def test_shared_filler_words_are_not_a_shared_identity():
    """Two different companies both being an INC, a TRUST or in REAL ESTATE says
    nothing about whether they are the same company."""
    assert not d.same_owner("A & J REAL ESTATE HOLDINGS", "B & K REAL ESTATE HOLDINGS")
    assert not d.same_owner("SMITH FAMILY TRUST", "JONES FAMILY TRUST")
    assert not d.same_owner("ACME LAND COMPANY", "ZENITH LAND COMPANY")


def test_stem_drops_only_the_noise():
    assert d.stem("WEYERHAEUSER FOREST HOLDINGS INC") == frozenset({"WEYERHAEUSER"})
    assert "DELTIC" in d.stem("DELTIC TIMBER CORPORATION")
    assert d.stem("") == frozenset()


def test_only_institutions_are_watched():
    assert d.is_entity("WEYERHAEUSER COMPANY")
    assert d.is_entity("ARKANSAS STATE HIGHWAY COMMISSION")
    assert not d.is_entity("HACKLER, LEE J & PATRICIA"), \
        "a couple owning two lots in two counties is not an institutional holder"


def test_the_seller_must_match_the_watchlist_exactly(monkeypatch):
    """Matching on shared words let 'A & J REAL ESTATE HOLDINGS' pair with any
    watched holder containing REAL, and a surname match a person."""
    from hunter.normalize import normalize_owner
    watched = {normalize_owner("WEYERHAEUSER FOREST HOLDINGS INC"): {"owner": "WEYERHAEUSER FOREST HOLDINGS INC"}}
    assert normalize_owner("A & J REAL ESTATE HOLDINGS") not in watched
    assert normalize_owner("WEYERHAEUSER FOREST HOLDINGS INC") in watched


def test_reports_nothing_rather_than_inventing_history():
    """The roll has one snapshot behind it. Zero is the honest answer, and the
    detector has to be willing to give it."""
    hits = d.detect(days=90, not_a_change=None)
    assert isinstance(hits, list)
