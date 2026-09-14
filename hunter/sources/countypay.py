"""County Tax Collector balances via CountyPay (countypay.ark.org) - Arkansas.gov.

The Collector's own public payment site. No login. Search by parcel and it lists
every open bill on the taxpayer's account: category (Current or Delinquent),
type (Real Estate or Personal) and the exact amount due. A parcel with no open
bill is simply not listed - which is what "paid" looks like from outside.

Garland's slug is `garland`. When the Collector has the service switched off
the page says "Online payments are currently unavailable"; we report that
verbatim as UNAVAILABLE and never guess.

Read the way a taxpayer would: one search per parcel, polite pacing, nothing
added to a cart, nothing paid. robots.txt allows it.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from html import unescape

from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, Record, SourceResult, register

BASE = "https://countypay.ark.org/index.php"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 PropertyHunter/1.0 (public-records research)")
PAUSE = 0.8
SLUGS = {"05051": "garland", "05125": "saline", "05059": "hotspring"}


def parcel_key(parcel_id: str) -> str:
    """CountyPay keys real-estate parcels as R + the digits of the county parcel id."""
    digits = re.sub(r"\D", "", parcel_id or "")
    return f"R{digits}" if digits else ""


def _text(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))).strip()


def parse_results(html: str) -> dict:
    """{'available': bool, 'message': str, 'bills': [{key, name, address, category, type, amount}]}"""
    t = _text(html)
    if "currently unavailable" in t.lower():
        m = re.search(r"(Online payments are currently unavailable[^.]*\.)", t)
        return {"available": False, "message": m.group(1) if m else "CountyPay unavailable", "bills": []}
    if "No taxpayers matched" in t:
        return {"available": True, "message": "no open bill under that parcel", "bills": []}
    bills = []
    for row in re.finditer(r'<tr class="parcel-row[^"]*"[^>]*>(.*?)</tr>', html, re.S):
        r = row.group(1)
        key = re.search(r'<span class="h6">([^<]+)</span>', r)
        name = re.search(r'parcel-name">\s*<p[^>]*>([^<]*)</p>', r)
        addr = re.search(r"<small>([^<]*)</small>", r)
        for cell in re.finditer(r'<td[^>]*data-parcel="([^"]+)"[^>]*data-category="([^"]+)"[^>]*data-type="([^"]+)"[^>]*>(.*?)</td>', r, re.S):
            amt = re.search(r'name="amount\[[^\]]+\]"[^>]*value="([\d.]+)"', cell.group(4)) or \
                  re.search(r'value="([\d.]+)"[^>]*name="amount\[', cell.group(4))
            bills.append({"key": cell.group(1), "name": unescape(re.sub(r"\s+", " ", name.group(1))).strip() if name else "",
                          "address": unescape(addr.group(1)).strip() if addr else "",
                          "category": cell.group(2), "type": cell.group(3),
                          "amount": float(amt.group(1)) if amt else None})
    return {"available": True, "message": f"{len(bills)} open bill(s)", "bills": bills}


def lookup(slug: str, parcel_id: str) -> dict:
    """One parcel on one county's CountyPay. Returns parse_results() plus checked_at."""
    import httpx
    key = parcel_key(parcel_id)
    out = {"available": False, "message": "no parcel id", "bills": [], "checked_at": _now(), "key": key}
    if not key:
        return out
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=40) as c:
        page = c.get(f"{BASE}/{slug}/search").text
        if "currently unavailable" in page.lower() or 'name="_token"' not in page:
            res = parse_results(page)
            if res["available"]:
                res = {"available": False, "message": "CountyPay search form not offered", "bills": []}
            return {**res, "checked_at": _now(), "key": key}
        tok = re.search(r'name="_token" value="([^"]+)"', page).group(1)
        r = c.post(f"{BASE}/{slug}/results",
                   data={"_token": tok, "parcel": key, "taxid": "", "name": "", "address": ""})
        time.sleep(PAUSE)
    res = parse_results(r.text)
    res["bills"] = [b for b in res["bills"] if b["key"].upper() == key.upper()] or res["bills"]
    return {**res, "checked_at": _now(), "key": key}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CountyPayTaxes(PropertySource):
    name = "county_tax_collector"
    label = "County Tax Collector - open tax bills (CountyPay, Arkansas.gov)"
    kind = "tax"
    url = f"{BASE}/garland"
    access = AUTOMATED
    territory = "garland_ar"

    def health_check(self) -> SourceResult:
        try:
            import httpx
            page = httpx.get(f"{BASE}/garland", headers={"User-Agent": UA}, follow_redirects=True, timeout=30).text
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail="CountyPay did not answer")
        res = parse_results(page)
        if not res["available"]:
            return SourceResult(status=UNAVAILABLE, detail=res["message"])
        return SourceResult(status=OK, detail="Garland County Collector's CountyPay search is open")

    def enrich(self, prop: dict, **kw) -> SourceResult:
        slug = SLUGS.get(prop.get("county_fips") or "05051")
        if not slug or not prop.get("parcel_id"):
            return SourceResult(status=UNAVAILABLE, detail="no CountyPay county or no parcel id")
        try:
            res = lookup(slug, prop["parcel_id"])
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail="CountyPay did not answer")
        if not res["available"]:
            return SourceResult(status=UNAVAILABLE, detail=res["message"])
        src_url = f"{BASE}/{slug}/search"
        real = [b for b in res["bills"] if b["type"].lower().startswith("real")]
        if not real:
            ev = [self.ev("tax_status_check",
                          "no open real-estate tax bill on the Collector's payment site - paid, exempt, or "
                          "not billed there (a parcel certified to the State shows at State Lands, not here)",
                          etype="OBSERVATION", confidence="MEDIUM", source=self.name, source_name=self.label,
                          source_url=src_url, effective_date=res["checked_at"][:10],
                          raw_ref=f"searched {res['key']}")]
            return SourceResult(status=OK, detail="no open tax bill at the Collector",
                                records=[Record(source=self.name, identity={"id": prop["id"]}, evidence=ev)])
        total = sum(b["amount"] or 0 for b in real)
        delinquent = [b for b in real if b["category"].lower().startswith("delinq")]
        cats = ", ".join(f"{b['category'].lower()} {b['type'].lower()} ${b['amount'] or 0:,.2f}" for b in real)
        status = "DELINQUENT" if delinquent else "CURRENT_BILL_OPEN"
        ev = [self.ev("tax_bill", f"${total:,.2f} owed to the County Collector ({cats})", etype="FACT",
                      confidence="HIGH", source=self.name, source_name=self.label, source_url=src_url,
                      effective_date=res["checked_at"][:10],
                      raw_ref=f"searched {res['key']}; taxpayer {real[0]['name']}"),
              self.ev("tax_amount_owed_county", f"{total:.2f}", etype="FACT", confidence="HIGH",
                      source=self.name, source_name=self.label, source_url=src_url)]
        if delinquent:
            ev.append(self.ev("tax_delinquent_county", f"delinquent at the county: ${sum(b['amount'] or 0 for b in delinquent):,.2f}",
                              etype="FACT", confidence="HIGH", source=self.name, source_name=self.label, source_url=src_url))
        return SourceResult(status=OK, detail=f"${total:,.2f} owed ({'DELINQUENT' if delinquent else 'current year, unpaid'})",
                            records=[Record(source=self.name, identity={"id": prop["id"]},
                                            fields={"tax_status": status}, evidence=ev)])


register(CountyPayTaxes())
