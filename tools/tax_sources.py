"""Tax source registry: which public tax records exist for each Arkansas county, what state
each source was last observed in, and how much of the county's roll the hunt has verified.

Every status is OBSERVED or explicitly UNKNOWN. Nothing is inferred from silence:
  AVAILABLE                the source answered a real request (last check)
  TEMPORARILY_UNAVAILABLE  the source itself said it is unavailable (CountyPay's own message)
  BLOCKED                  the operator refuses automation (HTTP 403); never bypassed
  MANUAL_ONLY              a person must ask (FOIA request, login-gated portal)
  NOT_FOUND                the source does not carry this county (HTTP 404 / no page)
  NOT_APPLICABLE           the source does not exist for this county at all
  UNKNOWN                  never checked, or the last check errored for a reason we cannot name

State lives in data/tax_sources.json (history of checks, gitignored dir) and is exported to
docs/data/tax_sources.json for the site. The CountyPay check is the same single page fetch the
poller makes for Garland, once per county, with the adapter's own pause between requests.

Usage: python3 tools/tax_sources.py [--check] [--no-export]
"""
import json, os, sys, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import q, init_db  # noqa: E402
from hunter.config import TERRITORIES  # noqa: E402
from hunter.sources import countypay  # noqa: E402

STATE = os.path.join(ROOT, "data", "tax_sources.json")
EXPORT = os.path.join(ROOT, "docs", "data", "tax_sources.json")
STATUS = os.path.join(ROOT, "docs", "data", "status.json")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"counties": {}}


def check_countypay(fips: str, slug: str) -> dict:
    """One GET of the county's CountyPay search page. Observed outcome only."""
    import httpx
    url = f"{countypay.BASE}/{slug}"
    try:
        r = httpx.get(url, headers={"User-Agent": countypay.UA}, follow_redirects=True, timeout=30)
    except Exception as exc:
        return {"status": "UNKNOWN", "reason": f"request failed: {type(exc).__name__}", "http": None}
    if r.status_code == 404:
        return {"status": "NOT_FOUND", "reason": "CountyPay has no page for this county (HTTP 404)", "http": 404}
    if r.status_code >= 400:
        return {"status": "UNKNOWN", "reason": f"HTTP {r.status_code}; the reason is not stated", "http": r.status_code}
    res = countypay.parse_results(r.text)
    if not res["available"]:
        return {"status": "TEMPORARILY_UNAVAILABLE", "reason": res["message"], "http": r.status_code}
    return {"status": "AVAILABLE", "reason": None, "http": r.status_code}


def county_counts() -> dict:
    """What the hunt has actually verified per county, from the same evidence the exporter uses."""
    out = {}
    for r in q("SELECT county_fips cf, COUNT(*) n, SUM(tax_status LIKE 'CERTIFIED%') cert, SUM(tax_status='DELINQUENT') delinq, "
               "SUM(tax_status='CURRENT_BILL_OPEN') bill FROM properties WHERE excluded=0 GROUP BY county_fips"):
        out[r["cf"]] = {"parcels": r["n"], "tax_sale_verified": r["cert"] or 0, "delinquent_verified": r["delinq"] or 0,
                        "current_bill_open": r["bill"] or 0}
    for r in q("""SELECT p.county_fips cf, COUNT(DISTINCT e.property_id) n, MAX(e.effective_date) last FROM evidence e JOIN properties p ON p.id=e.property_id
                  WHERE e.field='tax_status_check' AND e.source='county_tax_collector' AND p.excluded=0 GROUP BY p.county_fips"""):
        out.setdefault(r["cf"], {})["current_verified"] = r["n"]
        out[r["cf"]]["collector_last_success"] = r["last"]
    for r in q("""SELECT p.county_fips cf, MAX(e.effective_date) last FROM evidence e JOIN properties p ON p.id=e.property_id
                  WHERE e.field='tax_bill' AND p.excluded=0 GROUP BY p.county_fips"""):
        prev = out.setdefault(r["cf"], {}).get("collector_last_success")
        out[r["cf"]]["collector_last_success"] = max(prev or "", r["last"] or "") or None
    for r in q("""SELECT p.county_fips cf, COUNT(*) n FROM scores s JOIN properties p ON p.id=s.property_id
                  WHERE s.kind='overall' AND s.score>=65 AND p.excluded=0 GROUP BY p.county_fips"""):
        out.setdefault(r["cf"], {})["strong"] = r["n"]
    for r in q("""SELECT p.county_fips cf, COUNT(DISTINCT e.property_id) n FROM evidence e JOIN properties p ON p.id=e.property_id
                  WHERE e.field='tax_delinquent_county' AND p.excluded=0 GROUP BY p.county_fips"""):
        out.setdefault(r["cf"], {})["delinquent_verified"] = max(out.get(r["cf"], {}).get("delinquent_verified", 0), r["n"])
    return out


def build(check: bool) -> dict:
    init_db()
    state = load_state()
    try:
        cp_global = json.load(open(STATUS)).get("countypay", {})
    except Exception:
        cp_global = {}
    try:
        sl = json.load(open(os.path.join(ROOT, "docs", "data", "state_lands.json")))
        sl_counties = {x.get("fips") for x in sl.get("listings", [])}
        sl_built = sl.get("built_at")
    except Exception:
        sl_counties, sl_built = set(), None
    counts = county_counts()
    counties = {}
    for t in TERRITORIES:
        fips, name = t["county_fips"], t["county"]
        slug = countypay.SLUGS.get(fips)
        prev = (state.get("counties") or {}).get(fips, {}).get("countypay", {})
        cpay = {"source_name": "County Collector online search (CountyPay, Arkansas.gov)", "source_type": "collector_portal",
                "coverage_type": "current and delinquent real-estate bills per parcel", "access_method": "automated GET/POST, one parcel at a time, 0.8 s pause",
                "public_url": f"{countypay.BASE}/{slug}" if slug else None,
                "status": prev.get("status", "UNKNOWN"), "last_checked": prev.get("last_checked"), "last_success": prev.get("last_success"),
                "last_failure": prev.get("last_failure"), "failure_reason": prev.get("failure_reason"),
                "notes": "Verified records come only from this source or an imported county list. Unavailable is a fact about the source, never about the parcel."}
        if check and slug:
            res = check_countypay(fips, slug)
            ts = now()
            cpay.update({"status": res["status"], "last_checked": ts, "failure_reason": res["reason"]})
            if res["status"] == "AVAILABLE":
                cpay["last_success"] = ts
            else:
                cpay["last_failure"] = ts
            time.sleep(countypay.PAUSE)
        c = counts.get(fips, {})
        if c.get("collector_last_success"):
            cpay["last_success"] = max(cpay.get("last_success") or "", c["collector_last_success"])
        sources = {
            "countypay": cpay,
            "state_lands": {"source_name": "Commissioner of State Lands tax-sale inventory", "source_type": "state_inventory",
                            "coverage_type": "parcels certified to the State for unpaid taxes (about two years behind)",
                            "access_method": "automated, daily, plus monthly county sale/redemption reports", "public_url": "https://www.cosl.org/",
                            "status": "AVAILABLE" if sl_built else "UNKNOWN", "last_checked": sl_built, "last_success": sl_built,
                            "last_failure": None, "failure_reason": None,
                            "notes": f"{'listings present' if fips in sl_counties else 'no parcels from this county in the current inventory'}; absence from this list says nothing about the county bill"},
            "county_delinquent_list": {"source_name": "County Collector's delinquent list (one year behind)", "source_type": "public_record_request",
                                       "coverage_type": "every parcel delinquent at the county, before certification", "access_method": "FOIA request to the Collector; import with tools/import_delinquent_list.py",
                                       "public_url": f"request.html?county={name}", "status": "MANUAL_ONLY", "last_checked": None,
                                       "last_success": None, "last_failure": None, "failure_reason": None,
                                       "notes": "never automated; a person asks, receives a spreadsheet, and imports it as FACT evidence"},
        }
        if fips == "05051":
            sources["assessor_actdatascout"] = {"source_name": "Garland County Assessor (actDataScout)", "source_type": "assessor_portal",
                                                "coverage_type": "deeds, house photo, current mailing address", "access_method": "browser only",
                                                "public_url": "https://www.actdatascout.com/RealProperty/Arkansas/Garland", "status": "BLOCKED",
                                                "last_checked": None, "last_success": None, "last_failure": None,
                                                "failure_reason": "answers automated requests with HTTP 403; not bypassed", "notes": "manual link on every property file"}
            sources["arkansastaxsearch"] = {"source_name": "arkansastaxsearch.com (Garland)", "source_type": "tax_portal", "coverage_type": "delinquent tax inquiry",
                                            "access_method": "login-gated browser portal", "public_url": "https://www.arkansastaxsearch.com/garland.html",
                                            "status": "MANUAL_ONLY", "last_checked": None, "last_success": None, "last_failure": None, "failure_reason": None,
                                            "notes": "linked, never scraped"}
        counties[fips] = {"county": name, "fips": fips, "sources": sources,
                          "counts": {"parcels": c.get("parcels", 0), "tax_sale_verified": c.get("tax_sale_verified", 0),
                                     "delinquent_verified": c.get("delinquent_verified", 0), "current_bill_open": c.get("current_bill_open", 0),
                                     "current_verified": c.get("current_verified", 0), "strong": c.get("strong", 0),
                                     "unknown": max(0, c.get("parcels", 0) - c.get("tax_sale_verified", 0) - c.get("delinquent_verified", 0)
                                                    - c.get("current_bill_open", 0) - c.get("current_verified", 0))}}
    totals = {k: sum(c["counts"][k] for c in counties.values()) for k in ("parcels", "tax_sale_verified", "delinquent_verified", "current_bill_open", "current_verified", "unknown")}
    totals["collector_unavailable_counties"] = sum(1 for c in counties.values() if c["sources"]["countypay"]["status"] == "TEMPORARILY_UNAVAILABLE")
    totals["collector_available_counties"] = sum(1 for c in counties.values() if c["sources"]["countypay"]["status"] == "AVAILABLE")
    # TAX DATA COVERAGE QUEUE: where tax intelligence is missing and the hunt has the most reason to want it.
    # Ranked by evidence only (signals and State activity and roll size), never by an investment claim.
    queue = []
    for c in counties.values():
        cc, cp = c["counts"], c["sources"]["countypay"]
        if cc["unknown"] == 0:
            continue
        missing = ("Collector search is temporarily unavailable" if cp["status"] == "TEMPORARILY_UNAVAILABLE" else
                   "Collector has no page on CountyPay" if cp["status"] == "NOT_FOUND" else
                   "Collector never successfully checked" if cp["status"] in ("UNKNOWN",) else
                   "Collector open, parcels not yet checked")
        queue.append({"county": c["county"], "fips": c["fips"],
                      "why": f"{cc['strong']:,} parcels with a strong signal stack, {cc['tax_sale_verified']:,} already certified to the State, {cc['parcels']:,} parcels on the roll",
                      "missing": missing, "known": f"State tax-sale list: {cc['tax_sale_verified']:,} · Collector answers: {cc['current_verified'] + cc['current_bill_open'] + cc['delinquent_verified']:,}",
                      "unknown": f"{cc['unknown']:,} parcels with no county tax answer",
                      "next": {"label": "Ask the Collector for the one-year delinquent list (request kit)", "href": f"request.html?county={c['county']}"},
                      "also": [x for x in ([{"label": "Open the county's CountyPay page", "href": cp["public_url"]}] if cp["public_url"] else []) + [{"label": "Open State Lands", "href": "https://www.cosl.org/"}]],
                      "rank_basis": {"strong": cc["strong"], "state_listed": cc["tax_sale_verified"], "parcels": cc["parcels"]}})
    queue.sort(key=lambda x: (-x["rank_basis"]["strong"], -x["rank_basis"]["state_listed"], -x["rank_basis"]["parcels"]))
    out = {"built_at": now(), "statuses": ["AVAILABLE", "TEMPORARILY_UNAVAILABLE", "BLOCKED", "MANUAL_ONLY", "NOT_FOUND", "NOT_APPLICABLE", "UNKNOWN"],
           "countypay_global": {"open": cp_global.get("open"), "down_since": cp_global.get("down_since"), "checked_at": cp_global.get("checked_at")},
           "totals": totals, "counties": counties, "queue": queue}
    return out


def main():
    args = sys.argv[1:]
    out = build(check="--check" in args)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump({"updated_at": out["built_at"], "counties": {f: {"countypay": c["sources"]["countypay"]} for f, c in out["counties"].items()}}, open(STATE, "w"), indent=1)
    if "--no-export" not in args:
        json.dump(out, open(EXPORT, "w"), separators=(",", ":"))
    st = {}
    for c in out["counties"].values():
        st[c["sources"]["countypay"]["status"]] = st.get(c["sources"]["countypay"]["status"], 0) + 1
    print(json.dumps({"countypay_by_status": st, "totals": out["totals"], "queue": len(out["queue"])}))


if __name__ == "__main__":
    main()
