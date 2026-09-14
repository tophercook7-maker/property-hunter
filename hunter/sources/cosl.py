"""Arkansas Commissioner of State Lands (COSL) - tax-delinquent parcels.

When property taxes go unpaid, the county certifies the parcel to the State
after the statutory period and the Commissioner of State Lands sells it. COSL
publishes, publicly and without login:

  * an auction site (auction.cosl.org) listing every certified parcel that is
    for sale right now, with the owner of record, county parcel/RPID, acreage,
    starting bid (the taxes, interest, penalties and costs owed) and any bids;
  * a per-parcel search (cosl.org) that says whether a parcel is held by COSL;
  * monthly per-county spreadsheets of parcels sold and redeemed, with the
    exact dollars of taxes, interest, penalty and costs.

COSL's "parcel number" for Garland County is the county RPID, which is the
State parcel layer's `camakey` - so every listing joins exactly to one parcel
polygon (verified 2026-09-13).

Everything here is read the way a member of the public would read it, at a
polite pace, with no login and no bidding. Honest limits: a parcel that is NOT
at COSL may still be a year behind at the county; only the county Collector
knows that, and that record is login-only.
"""
from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from typing import Iterator

from .. import geo
from ..http import arcgis_query
from ..normalize import title_case
from .ar_parcels import LAYER as STATE_LAYER, SERVICE as STATE_SERVICE
from .base import AUTOMATED, OK, UNAVAILABLE, PropertySource, Record, SourceResult, register

AUCTION = "https://auction.cosl.org"
COSL = "https://cosl.org"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36 PropertyHunter/1.0 (public-records research; contact topher@mixedmakershop.com)")
PAUSE = 0.6                       # seconds between requests to the same host

SALE_TYPES = {
    "S2": "post-auction sale - buy it online now for the starting bid, or bid if others have",
    "S3": "post-auction sale (negotiated)",
    "S4": "public auction",
}

COUNTY_FIPS = {"ARKANSAS": "05001", "ASHLEY": "05003", "BAXTER": "05005", "BENTON": "05007", "BOONE": "05009",
    "BRADLEY": "05011", "CALHOUN": "05013", "CARROLL": "05015", "CHICOT": "05017", "CLARK": "05019", "CLAY": "05021",
    "CLEBURNE": "05023", "CLEVELAND": "05025", "COLUMBIA": "05027", "CONWAY": "05029", "CRAIGHEAD": "05031",
    "CRAWFORD": "05033", "CRITTENDEN": "05035", "CROSS": "05037", "DALLAS": "05039", "DESHA": "05041", "DREW": "05043",
    "FAULKNER": "05045", "FRANKLIN": "05047", "FULTON": "05049", "GARLAND": "05051", "GRANT": "05053", "GREENE": "05055",
    "HEMPSTEAD": "05057", "HOT SPRING": "05059", "HOWARD": "05061", "INDEPENDENCE": "05063", "IZARD": "05065",
    "JACKSON": "05067", "JEFFERSON": "05069", "JOHNSON": "05071", "LAFAYETTE": "05073", "LAWRENCE": "05075",
    "LEE": "05077", "LINCOLN": "05079", "LITTLE RIVER": "05081", "LOGAN": "05083", "LONOKE": "05085", "MADISON": "05087",
    "MARION": "05089", "MILLER": "05091", "MISSISSIPPI": "05093", "MONROE": "05095", "MONTGOMERY": "05097",
    "NEVADA": "05099", "NEWTON": "05101", "OUACHITA": "05103", "PERRY": "05105", "PHILLIPS": "05107", "PIKE": "05109",
    "POINSETT": "05111", "POLK": "05113", "POPE": "05115", "PRAIRIE": "05117", "PULASKI": "05119", "RANDOLPH": "05121",
    "ST. FRANCIS": "05123", "ST FRANCIS": "05123", "SALINE": "05125", "SCOTT": "05127", "SEARCY": "05129",
    "SEBASTIAN": "05131", "SEVIER": "05133", "SHARP": "05135", "STONE": "05137", "UNION": "05139", "VAN BUREN": "05141",
    "WASHINGTON": "05143", "WHITE": "05145", "WOODRUFF": "05147", "YELL": "05149"}


def _get(url: str, params: dict | None = None, as_json: bool = False):
    import httpx
    with httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as c:
        r = c.get(url, params=params)
        r.raise_for_status()
        return r.json() if as_json else r.text


def _post_form(url: str, data: dict, headers: dict | None = None):
    import httpx
    h = {"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}
    h.update(headers or {})
    with httpx.Client(timeout=40, follow_redirects=True) as c:
        r = c.post(url, data=data, headers=h)
        r.raise_for_status()
        return r


def _text(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return unescape(re.sub(r"\s+", " ", t)).strip()


# ---------------------------------------------------------------- feeds ----

def county_counts() -> dict[str, int]:
    """{'GARLAND': 475, ...} - how many parcels COSL has for sale per county."""
    r = _get(f"{AUCTION}/auctions/all_filter-menu-customization_counties", as_json=True)
    out = {}
    for row in r or []:
        m = re.match(r"(.+)\((\d+)\)", row.get("DisplayName", ""))
        if m:
            out[m.group(1).strip().upper()] = int(m.group(2))
    return out


def listings(county: str) -> list[dict]:
    """Every parcel COSL currently offers in one county (upper-case name)."""
    out, page, size = [], 1, 500
    while True:
        r = _post_form(f"{AUCTION}/auctions/grid_read", {
            "page": page, "pageSize": size, "skip": (page - 1) * size, "take": size,
            "filter": f"CoSLCountyName~eq~'{county.upper()}'"})
        data = r.json()
        rows = data.get("Data") or []
        out.extend(rows)
        if len(out) >= int(data.get("Total") or 0) or not rows:
            break
        page += 1
        time.sleep(PAUSE)
    return out


def listing_detail(token: str) -> dict:
    """Delinquent year, taxes owed, liens, legal, city, addition, static-map URL."""
    import httpx
    with httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as c:
        r = c.get(f"{AUCTION}/Auction/Listing/{token}")
        r.raise_for_status()
        html = r.text
        url = str(r.url)
    t = _text(html)
    def grab(label, stop):
        m = re.search(re.escape(label) + r"\s+(.+?)\s+(?=" + "|".join(re.escape(s) for s in stop) + ")", t)
        return m.group(1).strip() if m else None
    labels = ["Del Year", "County", "Code", "Parcel #", "Acreage", "Liens", "Owner", "Taxes", "Legal",
              "Prospective", "View on", "Time Remaining", "Current bid", "Starting bid"]
    out = {"sale_url": url,
           "delinquent_year": grab("Del Year", labels[1:]),
           "code": grab("Code", labels[3:]),
           "liens": grab("Liens", labels[6:]),
           "taxes": grab("Taxes", labels[8:]),
           "legal": grab("Legal", labels[9:])}
    m = re.search(r"get-static-map\?gisId=(\d+)", html)
    out["static_map"] = f"{AUCTION}/auction/get-static-map?gisId={m.group(1)}" if m else None
    m = re.search(r'href="(https://datascoutpro\.com/[^"]+)"', html)
    out["datascout_url"] = m.group(1) if m else None
    if out["liens"] and out["liens"].upper() in ("NONE", "N/A", ""):
        out["liens"] = None
    return out


def parcel_status(rpid: str) -> dict:
    """Is this county parcel number / RPID held by COSL? (public per-parcel search)"""
    import httpx
    with httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as c:
        page = c.get(f"{COSL}/Home/SearchByParcel").text
        m = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page)
        if not m:
            return {"ok": False, "error": "COSL search form changed"}
        r = c.post(f"{COSL}/WebParcels/GetByParcelNumber",
                   data={"parcelnumber": str(rpid), "__RequestVerificationToken": m.group(1)})
        t = _text(r.text)
    if "Parcel Not found" in t:
        return {"ok": True, "certified": False, "checked_at": _now()}
    hits = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        cells = [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) >= 4 and cells[1].strip() == str(rpid).strip():
            link = re.search(r'href="(/WebParcels/GetParcel/\d+)"', row)
            hits.append({"county": cells[0], "parcel": cells[1], "name": " ".join(cells[2:4]).strip(),
                         "url": COSL + link.group(1) if link else None})
    out = {"ok": True, "certified": bool(hits), "hits": hits, "checked_at": _now()}
    if hits and hits[0].get("url"):
        # the parcel page says exactly how far behind: years delinquent and total due
        try:
            t = _text(_get(hits[0]["url"]))
            def grab(label, stops):
                m = re.search(re.escape(label) + r"\s*(.+?)\s*(?=" + "|".join(re.escape(x) for x in stops) + ")", t)
                return m.group(1).strip() if m else None
            out["delinquent_year"] = grab("Year of Delinquency:", ["Code:", "Parcel Number:"])
            out["years_delinquent"] = grab("Tax Years Delinquent:", ["Total Due:"])
            out["total_due"] = grab("Total Due:", ["IF YOU", "PAY TAXES"])
            out["owner"] = grab("Owner:", ["Tax Years Delinquent:"])
            out["legal"] = grab("Legal Description:", ["Owner:"])
        except Exception as exc:
            out["detail_error"] = str(exc)[:80]
    return out


def monthly_reports(county: str, year: int) -> list[dict]:
    """Files COSL publishes for a county-year: sales, redemptions, other (xls + pdf)."""
    out = []
    for folder in ("MR", "TT"):
        r = _get(f"{COSL}/Countyfiles/CountyReportsView",
                 params={"county": county.title(), "rpttype": folder, "rptyear": year})
        for m in re.finditer(r'href="(/Countyfiles/DownloadFile\?[^"]+\.xls)"', r or ""):
            u = unescape(m.group(1))
            name = re.search(r"filename=([^&]+)", u).group(1)
            out.append({"folder": folder, "name": name, "url": COSL + u})
        time.sleep(PAUSE)
    return out


def read_report(url: str) -> list[dict]:
    """Rows of a COSL .xls report as dicts (needs xlrd)."""
    import httpx, xlrd
    with httpx.Client(timeout=60, headers={"User-Agent": UA}) as c:
        raw = c.get(url).content
    wb = xlrd.open_workbook(file_contents=raw)
    sh = wb.sheet_by_index(0)
    if sh.nrows < 2:
        return []
    head = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
    rows = []
    for r in range(1, sh.nrows):
        row = {head[c]: sh.cell_value(r, c) for c in range(sh.ncols)}
        dd = row.get("Deed Date")
        if isinstance(dd, float) and dd > 10000:
            row["Deed Date"] = xlrd.xldate_as_datetime(dd, wb.datemode).date().isoformat()
        rows.append(row)
    return rows


def _alnum(v) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def _digit_run(v) -> str:
    runs = re.findall(r"\d{4,}", str(v or ""))
    return max(runs, key=len) if runs else ""


def _owner_tokens(v) -> set:
    return {t for t in re.split(r"[^A-Z]+", str(v or "").upper()) if len(t) > 2 and t not in ("THE", "AND", "LLC", "INC", "TRUST", "ETUX", "ETAL", "JR", "SR")}


_STATE_FIELDS = ("parcelid,camakey,ownername,adrlabel,adrcity,adrzip5,parceltype,assessvalue,impvalue,"
                 "landvalue,totalvalue,subdivision,parcellgl,sourceref,sourcedate,camadate,Shape__Area")


def _state_rows(where: str) -> list[dict]:
    data = arcgis_query(STATE_SERVICE, STATE_LAYER, where=where, out_fields=_STATE_FIELDS,
                        geometry=True, extra={"returnCentroid": "true"})
    out = []
    for f in data.get("features") or []:
        a = f["attributes"]
        rings = (f.get("geometry") or {}).get("rings") or []
        c = f.get("centroid") or {}
        xs = [p[0] for ring in rings for p in ring]
        ys = [p[1] for ring in rings for p in ring]
        out.append({**a, "lat": c.get("y"), "lon": c.get("x"),
                    "extent": [min(xs), min(ys), max(xs), max(ys)] if xs else None, "rings": rings})
    return out


def parcels_for_rpids(rpids: list[str], county_fips: str, owners: dict | None = None) -> dict[str, dict]:
    """COSL number -> State parcel record (attributes + centroid + extent).

    Three passes, honest about which one matched (rec["join"]):
      exact     Garland: camakey == RPID; elsewhere parcelid == COSL number
      normalized  same characters once dashes/dots are removed (Pulaski prints
                  44L0920003001 for the State's 44L-092.00-030.01), or the State id
                  carries a trailing letter the COSL number lacks
      owner     same long digit run inside the county AND the owner names share
                two words (COSL writes FIRST LAST, the roll LAST FIRST)
    Anything else stays unjoined rather than guessed.
    """
    owners = owners or {}
    out: dict[str, dict] = {}
    numeric = [str(int(r)) for r in rpids if str(r).strip().isdigit()]
    textual = [str(r).strip().replace("'", "") for r in rpids if not str(r).strip().isdigit()]

    def run(where):
        try:
            return _state_rows(where)
        except Exception:
            return None

    # pass 1: exact, in batches; a batch the service rejects is retried one id at a time
    batches = [("camakey", numeric[i:i + 100]) for i in range(0, len(numeric), 100)]
    batches += [("parcelid", textual[i:i + 100]) for i in range(0, len(textual), 100)]
    for kind, chunk in batches:
        where = (f"countyfips='{county_fips}' AND camakey IN ({','.join(chunk)})" if kind == "camakey"
                 else f"countyfips='{county_fips}' AND parcelid IN ({','.join(repr(c) for c in chunk)})")
        rows = run(where)
        if rows is None:
            rows = []
            for one in chunk:
                w1 = (f"countyfips='{county_fips}' AND camakey={one}" if kind == "camakey"
                      else f"countyfips='{county_fips}' AND parcelid={one!r}")
                rows += run(w1) or []
                time.sleep(PAUSE / 3)
        for rec in rows:
            rec["join"] = "exact"
            if rec.get("camakey") is not None:
                out[str(int(rec["camakey"]))] = rec
            if rec.get("parcelid"):
                out[str(rec["parcelid"]).strip()] = rec
        time.sleep(PAUSE)

    # passes 2 and 3: the textual ids still unmatched. Counties print their
    # tax-district prefix differently on the two sites (Desha 005- vs 004-,
    # Ouachita 999- vs 001-), Pulaski drops the separators, Miller adds an R.
    def groups(v):
        return [g for g in re.split(r"[^A-Z0-9]+", str(v or "").upper()) if g]
    def core(v):                       # everything after the first group, letters at the end dropped
        g = groups(v)
        return re.sub(r"[A-Z]+$", "", "".join(g[1:] if len(g) > 1 else g))
    def strip_tail(v):
        return re.sub(r"[A-Z]+$", "", _alnum(v))
    for cid in textual:
        if cid in out:
            continue
        g = groups(cid)
        patterns = []
        if len(g) > 1 and len(g[1]) >= 4:
            patterns.append(f"%{g[1]}%")                                  # the parcel number proper
        run_digits = _digit_run(cid)
        if len(run_digits) >= 4 and f"%{run_digits}%" not in patterns:
            patterns.append(f"%{run_digits}%")
        a = _alnum(cid)
        if len(g) == 1 and len(a) >= 6:                                   # no separators (Pulaski)
            patterns.append(f"{a[:3]}-{a[3:6]}%")
        if len(a) >= 5:
            patterns.append(f"{strip_tail(cid)[:5]}%")                    # Miller-style digit strings
        cands, seen = [], set()
        for pat in patterns[:3]:
            for c in run(f"countyfips='{county_fips}' AND parcelid LIKE '{pat}'") or []:
                if c.get("parcelid") not in seen:
                    seen.add(c.get("parcelid")); cands.append(c)
            time.sleep(PAUSE / 2)
            if any(strip_tail(c.get("parcelid")) == strip_tail(cid) for c in cands):
                break
        hit, how = None, None
        for c in cands:
            if strip_tail(c.get("parcelid")) == strip_tail(cid):
                hit, how = c, "normalized"
                break
        if hit is None and cands:
            mine = _owner_tokens(owners.get(cid))
            same_core = [c for c in cands if core(c.get("parcelid")) == core(cid) and core(cid)]
            if len(same_core) == 1 and (not mine or len(mine & _owner_tokens(same_core[0].get("ownername"))) >= 1):
                hit, how = same_core[0], "prefix"
            elif mine:
                scored = sorted(((len(mine & _owner_tokens(c.get("ownername"))), c) for c in cands), key=lambda t: -t[0])
                if scored and scored[0][0] >= 2:
                    hit, how = scored[0][1], "owner"
        if hit is not None:
            out[cid] = dict(hit, join=how)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------- source ----

class StateLandsListings(PropertySource):
    name = "cosl_listings"
    label = "Arkansas Commissioner of State Lands - tax-delinquent parcels for sale"
    kind = "tax"
    url = f"{AUCTION}/Auctions/ListingsView"
    access = AUTOMATED
    territory = "garland_ar"

    def health_check(self) -> SourceResult:
        try:
            n = county_counts().get("GARLAND", 0)
            return SourceResult(status=OK, detail=f"{n} Garland County parcels for sale by the State")
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail="COSL auction site did not answer")

    def discover(self, *, territory: str = "garland_ar", county: str = "GARLAND",
                 progress=None, **kw) -> SourceResult:
        try:
            rows = listings(county)
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail="could not read COSL listings")
        fips = COUNTY_FIPS.get(county.upper(), "05051")
        parcels = parcels_for_rpids([r["CoSLParcelNumber"] for r in rows], fips)
        recs = []
        for r in rows:
            rpid = str(r.get("CoSLParcelNumber") or "").strip()
            p = parcels.get(rpid.lstrip("0") or rpid, {})
            bid = float(r.get("StartingBid") or 0)
            cur = float(r.get("CurrentBid") or 0)
            sale = SALE_TYPES.get(r.get("SaleType"), r.get("SaleType"))
            addr = " ".join((p.get("adrlabel") or "").split())
            fields = {"county_fips": fips, "territory": territory, "rpid": rpid,
                      "parcel_id": p.get("parcelid"), "address": title_case(addr) if addr else None,
                      "city": (p.get("adrcity") or "").upper() or None,
                      "lat": p.get("lat"), "lon": p.get("lon"),
                      "owner_name": p.get("ownername") or r.get("Owner"),
                      "land_value": p.get("landvalue"), "imp_value": p.get("impvalue"),
                      "total_value": p.get("totalvalue"), "legal": p.get("parcellgl"),
                      "parcel_type": p.get("parceltype"), "tax_status": "CERTIFIED_TO_STATE_FOR_SALE"}
            ev = [
                self.ev("tax_delinquent", f"certified to the State for unpaid taxes; for sale by COSL "
                        f"({sale}); starting bid ${bid:,.2f} = taxes, interest, penalties and costs owed",
                        etype="FACT", confidence="HIGH", source=self.name, source_name=self.label,
                        source_url=f"{AUCTION}/Auction/Listing/{r.get('ListingToken')}",
                        effective_date=(r.get("Added") or "")[:10] or None,
                        raw_ref=f"[key cosl:{rpid}] sale type {r.get('SaleType')}; current bid ${cur:,.2f}; "
                                f"bids {r.get('NumberOfBids')}; ends {r.get('End') or 'no active bidding'}"),
                self.ev("tax_amount_owed", f"{bid:.2f}", etype="FACT", confidence="HIGH", source=self.name,
                        source_name=self.label, source_url=f"{AUCTION}/Auction/Listing/{r.get('ListingToken')}",
                        raw_ref="COSL starting bid: the redemption-equivalent amount at listing time"),
            ]
            if cur > 0:
                ev.append(self.ev("tax_sale_bidding", f"someone has already bid ${cur:,.2f} ({r.get('NumberOfBids')} bids)"
                                  f"; bidding ends {r.get('End') or '?'}", etype="FACT", confidence="HIGH",
                                  source=self.name, source_name=self.label,
                                  source_url=f"{AUCTION}/Auction/Listing/{r.get('ListingToken')}"))
            tl = [{"event_date": (r.get("Added") or "")[:10] or None, "kind": "tax",
                   "title": "Listed for sale by the Commissioner of State Lands",
                   "detail": f"starting bid ${bid:,.2f}", "source": self.name,
                   "source_url": f"{AUCTION}/Auction/Listing/{r.get('ListingToken')}"}]
            recs.append(Record(source=self.name,
                               identity={"rpid": rpid, "parcel_id": p.get("parcelid"),
                                         "lat": p.get("lat"), "lon": p.get("lon"), "county_fips": fips},
                               fields=fields, evidence=ev, timeline=tl, raw={"listing": r}))
            if progress:
                progress(len(recs), len(rows))
        return SourceResult(status=OK, records=recs,
                            detail=f"{len(recs)} {county.title()} County parcels for sale by the State "
                                   f"({len(parcels)} joined to a parcel polygon)")

    def enrich(self, prop: dict, **kw) -> SourceResult:
        rpid = (prop.get("rpid") or "").strip()
        if not rpid:
            return SourceResult(status=UNAVAILABLE, detail="no RPID to search COSL with")
        try:
            st = parcel_status(rpid)
        except Exception as exc:
            return SourceResult(status=UNAVAILABLE, error=str(exc), detail="COSL parcel search did not answer")
        if not st.get("ok"):
            return SourceResult(status=UNAVAILABLE, error=st.get("error"), detail=st.get("error"))
        if st["certified"]:
            ev = [self.ev("tax_delinquent", "held by the Commissioner of State Lands (certified for unpaid taxes)",
                          etype="FACT", confidence="HIGH", source=self.name, source_name=self.label,
                          source_url=f"{COSL}/Home/SearchByParcel", effective_date=st["checked_at"][:10])]
            return SourceResult(status=OK, detail="CERTIFIED to the State for unpaid taxes",
                                records=[Record(source=self.name, identity={"id": prop["id"]},
                                                fields={"tax_status": "CERTIFIED_TO_STATE"}, evidence=ev)])
        ev = [self.ev("tax_status_check", "not held by the Commissioner of State Lands as of this check - "
                      "so not 2+ years delinquent; the county Collector alone knows if the current year is paid",
                      etype="OBSERVATION", confidence="HIGH", source=self.name, source_name=self.label,
                      source_url=f"{COSL}/Home/SearchByParcel", effective_date=st["checked_at"][:10])]
        return SourceResult(status=OK, detail="not certified to the State (checked COSL)",
                            records=[Record(source=self.name, identity={"id": prop["id"]}, evidence=ev)])


register(StateLandsListings())
