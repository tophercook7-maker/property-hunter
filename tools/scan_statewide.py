"""Run the distress hunt in every Arkansas county, one after another, through the app.

    python3 tools/scan_statewide.py                # all counties not yet scanned, alphabetical
    python3 tools/scan_statewide.py --only pulaski_ar,jefferson_ar
    python3 tools/scan_statewide.py --enrich 5      # deep checks on the top N per county (default 5)

Talks to the running app (http://127.0.0.1:8234) so the Scan view shows real
progress; waits for each county to finish before starting the next. Garland and
Saline are skipped unless named. Polite: one county at a time, the scanner's own
pacing. Expect 5-15 minutes per county.
"""
import json, os, sys, time
import httpx
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.config import TERRITORIES  # noqa: E402
from hunter.db import init_db, q  # noqa: E402

API = "http://127.0.0.1:8234"


def scanned_counties():
    init_db()
    return {r["territory"] for r in q("SELECT DISTINCT territory FROM scans WHERE status='complete' AND mode='distress'")}


def main():
    args = sys.argv[1:]
    enrich = int(args[args.index("--enrich") + 1]) if "--enrich" in args else 5
    only = args[args.index("--only") + 1].split(",") if "--only" in args else None
    done = scanned_counties()
    todo = [t for t in TERRITORIES if (only and t["key"] in only) or
            (not only and t["key"] not in ("garland_ar", "saline_ar") and t["key"] not in done)]
    print(f"{len(todo)} counties to scan", flush=True)
    for t in todo:
        while True:
            try:
                cur = httpx.get(f"{API}/api/scan/current", timeout=20).json()
                s = cur.get("scan") or cur
                if not s or s.get("status") != "running":
                    break
            except Exception:
                pass
            time.sleep(15)
        r = httpx.post(f"{API}/api/scan", json={"mode": "distress", "territory": t["key"], "limit": None, "enrich_top": enrich}, timeout=30).json()
        if not r.get("started"):
            print(t["key"], "not started:", r.get("reason"), flush=True); time.sleep(30); continue
        t0 = time.time()
        while True:
            time.sleep(20)
            try:
                s = httpx.get(f"{API}/api/scan/current", timeout=20).json()
                s = s.get("scan") or s
            except Exception:
                continue
            if s.get("status") in ("complete", "failed", "interrupted"):
                st = {x["key"]: x for x in s.get("stages", [])}
                print(f"{t['key']}: {s['status']} in {(time.time()-t0)/60:.1f} min - "
                      f"{st.get('discovery',{}).get('detail','')[:60]} | {st.get('scoring',{}).get('detail','')}", flush=True)
                break


if __name__ == "__main__":
    main()
