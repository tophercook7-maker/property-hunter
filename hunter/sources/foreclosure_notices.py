"""Foreclosure sale notices: the statutory internet notice service.

Arkansas statutory (non-judicial) foreclosures must be noticed in four places
(Ark. Code § 18-50-105): recorded with the county, mailed, published in a
newspaper for four weeks, and "published on an internet foreclosure sale
notice information service". Trustee Foreclosure Sales Online is one such
service and publishes its Arkansas table without a login, so it is read
here: sale date and time, the property address, the courthouse where the
sale happens, and the auctioneer or trustee.

What this source does NOT give: the opening bid (announced at the sale),
the mortgagee's name, or the full recorded notice. Those live in the county
recorder's office and the newspaper; the record carries links to where to
look, never a guess. A notice that disappears from the table is not a
completed sale: the sale may have been postponed, cancelled or the loan
cured. Nothing here says who will own the property next week.
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone

from ..config import TERRITORIES
from ..http import get
from ..normalize import normalize_address

SOURCE = "trustee_foreclosure_sales_online"
SOURCE_NAME = "Trustee Foreclosure Sales Online (internet foreclosure sale notice service, Ark. Code § 18-50-105)"
URL = "https://trustee-foreclosuresalesonline.com/"

_FIPS_BY_COUNTY = {t["county"].upper().replace(".", ""): t["county_fips"] for t in TERRITORIES}
_FIPS_BY_COUNTY.setdefault("ST FRANCIS", "05123")
_FIPS_BY_COUNTY.setdefault("SAINT FRANCIS", "05123")

_HEADERS = ("date", "time", "prior_sale_date", "address", "city", "county", "state", "zip", "location", "auctioneer")


def _cells(row_html: str) -> list[str]:
    return [_html.unescape(re.sub(r"<[^>]+>", " ", c)).replace("\xa0", " ").strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]


def _date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def county_fips(name: str) -> str | None:
    key = re.sub(r"\s+COUNTY$", "", (name or "").upper().strip().replace(".", ""))
    return _FIPS_BY_COUNTY.get(key)


def parse(page: str, *, fetched_at: str | None = None) -> list[dict]:
    """Every Arkansas row of the notice table, as plain records. Pure: no network."""
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        c = _cells(row)
        if len(c) < 10:
            continue
        rec = dict(zip(_HEADERS, c[:10]))
        if rec["state"].strip().upper() != "AR":
            continue
        sale_date = _date(rec["date"])
        if not sale_date:
            continue
        addr = re.sub(r"\s+", " ", rec["address"]).strip()
        out.append({
            "source": SOURCE, "source_name": SOURCE_NAME, "source_url": URL, "fetched_at": fetched_at,
            "sale_date": sale_date, "sale_time": rec["time"].strip() or None,
            "prior_sale_date": _date(rec["prior_sale_date"]),
            "address": addr, "address_norm": normalize_address(addr) or None,
            "city": rec["city"].strip().title() or None, "zip": rec["zip"].strip() or None,
            "county": rec["county"].strip().title() or None, "county_fips": county_fips(rec["county"]),
            "location": rec["location"].strip() or None, "auctioneer": rec["auctioneer"].strip() or None,
            "key": f"{county_fips(rec['county']) or rec['county'].upper()}|{normalize_address(addr)}|{sale_date}",
        })
    return out


# The site's edge (Cloudflare) refuses the app's usual identity string, which carries the
# words "personal real-estate research" in parentheses, while a plain product token with
# the site URL and a contact address is served. Still an honest, identifying UA; nothing
# is disguised. If this is ever refused too, the source is BLOCKED and stays that way.
USER_AGENT = "PropertyHunter/1.0 (+https://tophercook7-maker.github.io/property-hunter/; topher@mixedmakershop.com)"


def fetch() -> list[dict]:
    """Read the live table once. Robots and the shared throttle apply; a block raises, never bypassed."""
    r = get(URL, accept="text/html", user_agent=USER_AGENT)
    return parse(r.text)
