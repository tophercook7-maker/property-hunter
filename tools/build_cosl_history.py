"""What tax-sale parcels actually sold for, and who redeemed - from COSL's monthly county files.

    python3 tools/build_cosl_history.py                    # counties with current listings, 2025-2026
    python3 tools/build_cosl_history.py --years 2024 2025 2026 --all-counties

Reads the Commissioner of State Lands' public monthly spreadsheets:
  MONTHLY-SALES        deed issued to a buyer: parcel, deed date, deed type, buyer, the debt
                       (taxes + interest + penalty + costs) the sale cleared
  MONTHLY-REDEMPTIONS  the owner paid up: parcel, date, what they paid
  EXCESS-PROCEEDS      sales where the bid beat the debt: the excess. Sale price = debt + excess.
  TAXTURNBACK-SALES    same sales with the county's appraised Value at certification

Files are cached in data/cosl_reports/ so re-runs only fetch what is new.
Output: docs/data/cosl_history.json (per county: sales, redemptions, summary, top buyers).
"""
import json, os, re, statistics, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.sources import cosl  # noqa: E402

CACHE = os.path.join(ROOT, "data", "cosl_reports")
OUT = os.path.join(ROOT, "docs", "data", "cosl_history.json")
FOLDERS = {"MR": ("MONTHLY-SALES", "MONTHLY-REDEMPTIONS"), "TT": ("TAXTURNBACK-SALES",), "ET": ("EXCESS-PROCEEDS",)}


def list_files(county, folder, year):
    html = cosl._get(f"{cosl.COSL}/Countyfiles/CountyReportsView",
                     params={"county": county.title(), "rpttype": folder, "rptyear": year})
    out = []
    for m in re.finditer(r'href="(/Countyfiles/DownloadFile\?[^"]+\.xls)"', html):
        u = m.group(1).replace("&amp;", "&")
        name = re.search(r"filename=([^&]+)", u).group(1)
        out.append((name, cosl.COSL + u))
    return out


def fetch(name, url):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        import httpx
        with httpx.Client(timeout=60, headers={"User-Agent": cosl.UA}, follow_redirects=True) as c:
            open(path, "wb").write(c.get(url).content)
        time.sleep(cosl.PAUSE)
    return path


def rows_of(path):
    import xlrd
    try:
        wb = xlrd.open_workbook(path)
    except Exception:
        return []
    sh = wb.sheet_by_index(0)
    if sh.nrows < 2:
        return []
    head = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
    out = []
    for r in range(1, sh.nrows):
        row = {head[c]: sh.cell_value(r, c) for c in range(sh.ncols)}
        for k in ("Deed Date", "AppDate"):
            v = row.get(k)
            if isinstance(v, float) and v > 10000:
                row[k] = xlrd.xldate_as_datetime(v, wb.datemode).date().isoformat()
        out.append(row)
    return out


def num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def pk(row):
    return str(row.get("Parcel") or row.get("Parcel #") or "").strip().rstrip(".0") if not isinstance(row.get("Parcel"), float) else str(int(row["Parcel"]))


def build(counties, years):
    history = {}
    for county in counties:
        sales, redemptions, excess, values = {}, {}, {}, {}
        for folder, kinds in FOLDERS.items():
            for year in years:
                try:
                    files = list_files(county, folder, year)
                except Exception as exc:
                    print(f"{county} {folder} {year}: {exc}", flush=True)
                    continue
                for name, url in files:
                    kind = next((k for k in kinds if name.startswith(k)), None)
                    if not kind:
                        continue
                    try:
                        rows = rows_of(fetch(name, url))
                    except Exception as exc:
                        print(f"  {name}: {exc}", flush=True)
                        continue
                    for row in rows:
                        parcel = pk(row)
                        if not parcel:
                            continue
                        if kind == "MONTHLY-SALES":
                            key = (parcel, str(row.get("Deed #") or "").split(".")[0])
                            sales[key] = {"parcel": parcel, "date": row.get("Deed Date"),
                                          "deed_type": row.get("Deed_Type") or row.get("Deed Type"),
                                          "deed_no": key[1], "buyer": str(row.get("Deed Name") or "").strip(),
                                          "buyer_city": str(row.get("CityStateZip") or "").strip(),
                                          "owed": num(row.get("Total")), "taxes": num(row.get("Taxes")),
                                          "years": str(row.get("Years Paid") or "").strip(),
                                          "legal": str(row.get("Legal") or "").strip()[:80],
                                          "del_year": str(row.get("Year") or "").split(".")[0]}
                        elif kind == "MONTHLY-REDEMPTIONS":
                            key = (parcel, str(row.get("Deed #") or "").split(".")[0])
                            redemptions[key] = {"parcel": parcel, "date": row.get("Deed Date"),
                                                "paid": num(row.get("Total")), "taxes": num(row.get("Taxes")),
                                                "owner": str(row.get("Deed Name") or "").strip(),
                                                "owner_city": str(row.get("CityStateZip") or "").strip(),
                                                "years": str(row.get("Years Paid") or "").strip(),
                                                "del_year": str(row.get("Year") or "").split(".")[0]}
                        elif kind == "EXCESS-PROCEEDS":
                            excess[(parcel, str(row.get("Deed #") or "").split(".")[0])] = num(row.get("Total Excess"))
                            excess.setdefault(("parcel", parcel), num(row.get("Total Excess")))
                        elif kind == "TAXTURNBACK-SALES":
                            values[parcel] = num(row.get("Value"))
        for key, s in sales.items():
            ex = excess.get(key)
            if ex is None:
                ex = excess.get(("parcel", s["parcel"]))
            s["excess"] = ex
            s["price"] = round((s["owed"] or 0) + (ex or 0), 2) if s["owed"] is not None else None
            s["price_known"] = ex is not None          # no excess row = sold at (or below) the debt
            s["value"] = values.get(s["parcel"])
        sl = sorted(sales.values(), key=lambda x: x["date"] or "", reverse=True)
        rl = sorted(redemptions.values(), key=lambda x: x["date"] or "", reverse=True)
        prices = [s["price"] for s in sl if s["price"]]
        owed = [s["owed"] for s in sl if s["owed"]]
        buyers = Counter(s["buyer"] for s in sl if s["buyer"])
        history[county] = {
            "fips": cosl.COUNTY_FIPS.get(county),
            "summary": {"sold": len(sl), "redeemed": len(rl),
                        "median_price": round(statistics.median(prices), 2) if prices else None,
                        "median_owed": round(statistics.median(owed), 2) if owed else None,
                        "sold_above_debt": sum(1 for s in sl if s["excess"]),
                        "max_price": max(prices) if prices else None,
                        "first": min((s["date"] for s in sl if s["date"]), default=None),
                        "last": max((s["date"] for s in sl if s["date"]), default=None)},
            "top_buyers": [{"buyer": b, "n": n, "city": next((s["buyer_city"] for s in sl if s["buyer"] == b), "")}
                           for b, n in buyers.most_common(8)],
            "sales": sl, "redemptions": rl}
        print(f"{county}: {len(sl)} sold, {len(rl)} redeemed, median price {history[county]['summary']['median_price']}", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "years": years,
           "note": "price = debt cleared + excess proceeds where COSL reported excess; otherwise the sale "
                   "cleared exactly the debt (price_known=false means at-or-below-debt).",
           "counties": history}
    json.dump(doc, open(OUT, "w"), separators=(",", ":"))
    return {"counties": len(history), "kb": os.path.getsize(OUT) // 1024}


if __name__ == "__main__":
    args = sys.argv[1:]
    years = [2025, 2026]
    if "--years" in args:
        i = args.index("--years") + 1
        years = []
        while i < len(args) and args[i].isdigit():
            years.append(int(args[i])); i += 1
    if "--all-counties" in args:
        counties = sorted({c for c in cosl.COUNTY_FIPS if c != "ST FRANCIS"})
    elif "--county" in args:
        counties = [args[args.index("--county") + 1].upper()]
    else:
        counties = sorted(cosl.county_counts().keys())
    print(json.dumps(build(counties, years)))
