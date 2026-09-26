"""Land leaving a large institutional holder.

Weyerhaeuser holds 1.16 million Arkansas acres, Deltic and Green Bay Packaging
hundreds of thousands more, and they sell parcels off continually. Nobody
watches for it systematically, and a parcel that has just left a timber REIT is
on the market before it is listed anywhere.

The whole difficulty is telling a sale from a spelling change. Every one of the
first 43 "owner changed away from Weyerhaeuser" rows in this database was
Weyerhaeuser to Weyerhaeuser -- the roll dropping a "C/O" line, renaming
WEYERHAEUSER FOREST HOLDINGS INC to WEYERHAEUSER COMPANY. A detector that
counts those is worse than no detector, because it produces a stream of
confident nonsense.

So a divestiture here has to clear all of:

  * the old owner is a holder we actually watch: an entity, 50+ parcels, in
    two or more counties, so one family selling their second lot is not news;
  * the new owner is not the same holder under another spelling -- compared on
    the distinctive stem of the name, not the whole string;
  * the change is a real change and not two sources disagreeing, which is the
    existing SOURCES DISAGREE / first-contact test, reused rather than
    reinvented.

The roll was first loaded on 2026-09-26. There is no history behind that, and
this reports zero rather than inventing a backfill.
"""
from __future__ import annotations

import re

from . import db
from .normalize import normalize_owner

MIN_PARCELS = 50
MIN_COUNTIES = 2

ENTITY_RE = re.compile(
    r"\b(LLC|L L C|INC|CORP|CORPORATION|TRUST|LP|LLP|LTD|COMPANY|PARTNERS|HOLDINGS|"
    r"PROPERTIES|ENTERPRISES|ASSOCIATION|ASSN|POA|BANK|CHURCH|TIMBER|FOREST|PAPER|"
    r"COMMISSION|DEPARTMENT|AUTHORITY|DISTRICT|UNIVERSITY|RAILROAD|ELECTRIC|GAS)\b")

# words that carry no identity: two names sharing only these are not the same owner
NOISE = {"THE", "OF", "AND", "A", "C/O", "CO", "INC", "LLC", "LP", "LLP", "LTD", "CORP",
         "CORPORATION", "COMPANY", "TRUST", "HOLDINGS", "PROPERTIES", "ENTERPRISES",
         "PARTNERS", "GROUP", "ASSOCIATION", "ASSN", "USA", "US", "ATTN", "FOREST",
         "TIMBER", "PAPER", "LAND", "REALTY", "ESTATE", "INVESTMENTS", "INVESTMENT",
         # generic enough that sharing one says nothing: two unrelated companies
         # are both in REAL ESTATE, two unrelated trusts are both a FAMILY trust
         "REAL", "FAMILY", "DEVELOPMENT", "MANAGEMENT", "SERVICES", "SERVICE",
         "RENTAL", "RENTALS", "HOME", "HOMES", "ASSOCIATES", "VENTURES", "CAPITAL"}


def stem(name: str) -> frozenset[str]:
    """The distinctive words in an owner name.

    WEYERHAEUSER FOREST HOLDINGS INC C/O X and WEYERHAEUSER COMPANY both reduce
    to {WEYERHAEUSER}, so a rename between them is visibly not a sale.
    """
    words = re.findall(r"[A-Z0-9&]+", (name or "").upper())
    return frozenset(w for w in words if w not in NOISE and len(w) > 2)


def same_owner(a: str, b: str) -> bool:
    """True when two spellings name the same holder."""
    sa, sb = stem(a), stem(b)
    if not sa or not sb:
        return False
    return bool(sa & sb)


def is_entity(name: str) -> bool:
    return bool(ENTITY_RE.search((name or "").upper()))


def watchlist() -> dict[str, dict]:
    """Holders worth watching, derived from the roll rather than hand-listed."""
    out = {}
    for r in db.q(f"""SELECT owner_norm, MIN(owner_name) nm, COUNT(*) n,
                             COUNT(DISTINCT county_fips) counties,
                             SUM(COALESCE(total_value,0)) val,
                             SUM(COALESCE(acreage,0)) acres
                      FROM properties
                      WHERE owner_norm IS NOT NULL AND owner_norm!=''
                      GROUP BY owner_norm
                      HAVING COUNT(*)>={MIN_PARCELS} AND COUNT(DISTINCT county_fips)>={MIN_COUNTIES}"""):
        nm = (r["nm"] or "").strip()
        if not is_entity(nm):
            continue                       # a common surname in two counties is not an institution
        out[r["owner_norm"]] = {"owner": nm, "parcels": r["n"], "counties": r["counties"],
                                "appraised": r["val"], "acres": r["acres"]}
    return out


def detect(days: int = 90, not_a_change: set | None = None) -> list[dict]:
    """Owner changes that are a holder actually letting go of a parcel."""
    watched = watchlist()
    out = []
    for r in db.q("""SELECT c.id, c.property_id, c.old_value, c.new_value, c.detected_at, c.source,
                            p.address, p.city, p.county_fips, p.acreage, p.total_value, p.parcel_id
                     FROM changes c JOIN properties p ON p.id=c.property_id
                     WHERE c.field='owner_name'
                       AND datetime(replace(c.detected_at,'T',' ')) > datetime('now', ?)
                     ORDER BY c.id DESC""", (f"-{days} days",)):
        if not_a_change and r["id"] in not_a_change:
            continue                                     # two sources disagreeing, not an event
        old, new = r["old_value"] or "", r["new_value"] or ""
        if not old or not new:
            continue
        if same_owner(old, new):
            continue                                     # a rename, which is what all 43 of the first ones were
        # Match the seller EXACTLY against the watchlist key, not by shared words.
        # Stem overlap let "A & J REAL ESTATE HOLDINGS" match any watched holder
        # containing REAL, and a surname match a person -- 15 reported hits, most
        # of them nonsense.
        hit = normalize_owner(old)
        if hit not in watched:
            continue
        out.append({"change_id": r["id"], "property_id": r["property_id"],
                    "parcel_id": r["parcel_id"], "address": r["address"], "city": r["city"],
                    "county_fips": r["county_fips"], "acreage": r["acreage"],
                    "appraised": r["total_value"], "from": old, "to": new,
                    "at": (r["detected_at"] or "")[:10], "holder": watched[hit]["owner"]})
    # The scheduler re-detects the same change every day it runs, so one parcel
    # leaving one holder is one event, not six. Rows arrive newest first.
    seen, unique = set(), []
    for h in out:
        key = (h["property_id"], normalize_owner(h["from"]), normalize_owner(h["to"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    return unique
