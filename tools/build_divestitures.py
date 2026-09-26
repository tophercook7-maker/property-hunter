"""Publish the divestiture watch: land leaving a large institutional holder.

Writes docs/data/divestitures.json -- the watchlist, what has moved, and the
date the baseline starts, so an empty list reads as "nothing has moved since we
started looking" rather than as a broken page.
"""
from __future__ import annotations

import json, os, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hunter import db, divestiture                       # noqa: E402
from hunter.config import TERRITORIES                    # noqa: E402
import tools.build_share as bs                           # noqa: E402

OUT = os.path.join(ROOT, "docs", "data", "divestitures.json")
BASELINE = "2026-09-26"        # the day the statewide roll was first loaded
TOP_HOLDERS = 40


def main() -> int:
    db.init_db()
    county = {t["county_fips"]: t["county"] for t in TERRITORIES}
    watched = divestiture.watchlist()
    hits = divestiture.detect(days=180, not_a_change=set(bs.conflict_change_ids()))
    for h in hits:
        h["county"] = county.get(h["county_fips"], h["county_fips"])

    top = sorted(watched.values(), key=lambda h: -h["parcels"])[:TOP_HOLDERS]
    doc = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline": BASELINE,
        "what": ("Parcels leaving a large institutional holder: timber companies, utilities, "
                 "public agencies and the bigger land companies. A parcel that has just left a "
                 "timber REIT is on the market before it is listed anywhere."),
        "method_note": ("A holder here is an entity with at least 50 parcels across two or more "
                        "counties. A move only counts when the buyer is a different owner, not the "
                        "same holder under another spelling -- the first 43 candidates in this "
                        "database were all Weyerhaeuser to Weyerhaeuser, the roll dropping a C/O "
                        "line. Two sources disagreeing about who owns a parcel is not a sale "
                        "either, and is filtered the same way it is everywhere else on this site."),
        "baseline_note": ("The statewide roll was first read on " + BASELINE + ". There is no "
                          "history behind that date, so an empty list means nothing has moved "
                          "since then, not that nothing ever moves."),
        "totals": {"holders_watched": len(watched),
                   "parcels_held": sum(h["parcels"] for h in watched.values()),
                   "acres_held": round(sum(h.get("acres") or 0 for h in watched.values())),
                   "appraised_held": sum(h.get("appraised") or 0 for h in watched.values()),
                   "moves": len(hits)},
        "top_holders": top,
        "moves": hits[:200],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w"), separators=(",", ":"))
    t = doc["totals"]
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024:.0f} KB)")
    print(f"  watching {t['holders_watched']} holders · {t['parcels_held']:,} parcels · "
          f"{t['acres_held']:,} acres · {t['moves']} moves since {BASELINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
