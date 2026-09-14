"""Wait for the Garland Collector's CountyPay search to come back, then fill in tax bills.

    python3 tools/poll_countypay.py --hours 8 --top 80

Every 20 minutes: is the search open? When it is, run the Collector lookup for the
top N scoring Garland properties (plus everything on the watchlist), store the
evidence, rebuild the site snapshot, and stop. Public site, no login, polite pace.
"""
import json, os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q, log  # noqa: E402
from hunter import store  # noqa: E402
from hunter.sources import countypay  # noqa: E402
from hunter.sources.base import OK  # noqa: E402


STATUS = os.path.join(ROOT, "docs", "data", "status.json")


def write_status(ok: bool, detail: str, filled: dict | None = None):
    """docs/data/status.json - the site reads this to say, gently, what it could and could not check."""
    cur = {}
    try:
        cur = json.load(open(STATUS))
    except Exception:
        pass
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    c = cur.get("countypay", {})
    if ok and not c.get("open"):
        c["opened_at"] = now
    if not ok and c.get("open", True):
        c["down_since"] = c.get("down_since") or now
    if ok:
        c["down_since"] = None
    c.update({"open": ok, "detail": detail, "checked_at": now})
    if filled:
        c["last_fill"] = {"at": now, **filled}
    cur["countypay"] = c
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    json.dump(cur, open(STATUS, "w"), indent=1)


def open_now() -> tuple[bool, str]:
    res = countypay.CountyPayTaxes().health_check()
    write_status(res.status == OK, res.detail)
    return res.status == OK, res.detail


def fill(top: int) -> dict:
    src = countypay.CountyPayTaxes()
    ids = [r["id"] for r in q("""SELECT p.id FROM properties p JOIN scores s ON s.property_id=p.id AND s.kind='overall'
                                 WHERE p.excluded=0 AND p.county_fips='05051' AND p.parcel_id IS NOT NULL
                                 ORDER BY s.score DESC LIMIT ?""", (top,))]
    ids += [r["property_id"] for r in q("SELECT property_id FROM watchlist")]
    seen, out = set(), {"checked": 0, "owed": 0, "delinquent": 0, "none": 0, "unavailable": 0}
    for pid in ids:
        if pid in seen:
            continue
        seen.add(pid)
        p = store.get_property(pid)
        if not p:
            continue
        res = src.enrich(p)
        if res.status != OK:
            out["unavailable"] += 1
            if out["unavailable"] >= 3:
                break
            continue
        for rec in res.records:
            store.store_evidence(pid, rec.evidence)
            if rec.fields:
                store.set_fields(pid, rec.fields, src.name)
        out["checked"] += 1
        if "DELINQUENT" in res.detail:
            out["delinquent"] += 1
        elif "owed" in res.detail:
            out["owed"] += 1
        else:
            out["none"] += 1
        time.sleep(countypay.PAUSE)
    return out


def main():
    init_db()
    args = sys.argv[1:]
    hours = float(args[args.index("--hours") + 1]) if "--hours" in args else 8
    top = int(args[args.index("--top") + 1]) if "--top" in args else 80
    deadline = time.time() + hours * 3600
    while time.time() < deadline:
        ok, detail = open_now()
        print(time.strftime("%H:%M"), "CountyPay:", detail, flush=True)
        if ok:
            out = fill(top)
            write_status(True, "open", out)
            print("filled:", out, flush=True)
            log(None, f"CountyPay tax bills filled: {out}", source="countypay")
            subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_share.py")], check=False,
                           stdout=subprocess.DEVNULL)
            return
        time.sleep(20 * 60)
    print("gave up waiting; the scheduled scan will keep trying daily", flush=True)


if __name__ == "__main__":
    main()
