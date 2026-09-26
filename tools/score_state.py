"""Score every parcel in the state, and keep the detail where it earns its room.

    python3 tools/score_state.py --only 05119     # one county
    python3 tools/score_state.py                  # every county with unscored rows
    python3 tools/score_state.py --status

WHY NOT JUST compute(persist=True) ON EVERYTHING
Computing is cheap: 12,800 parcels/sec. Storing is not. There are nine score
kinds, so persisting all of them for 2.1 million parcels is 19 million rows and
about 8.6 GB of breakdown JSON on top of an 11 GB roll -- most of it describing
ordinary occupied houses that will never be looked at.

So: every parcel gets its overall score and its recommendation, which is what
ranking, counting and filtering need, and what makes "top 2,000 of 180,264"
a true sentence. The eight use-case sheets -- rental, resale, land, storage,
business, workshop, snowcone, risk -- are kept only for the parcels that rank
inside a county's DETAIL_KEEP, because they only matter once somebody is
looking at one property, and Look up can recompute them in under a millisecond
for anything else.

Nothing is hidden by this. A parcel outside the detail slice still carries a
real overall score and a real recommendation; it is the breakdown sheets that
are recomputed on demand instead of stored.
"""
from __future__ import annotations

import argparse, os, shutil, sys, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hunter import db, scoring                               # noqa: E402
from hunter.config import TERRITORIES                        # noqa: E402
from hunter.db import jdump, utcnow                          # noqa: E402

DETAIL_KEEP = 5000        # per county: 2.5x the publish cap, so filters have room
CHUNK = 20000
DISK_FLOOR_GB = 25


def free_gb() -> float:
    return shutil.disk_usage(ROOT).free / 1e9


def score_county(fips: str, county: str, redo: bool = False) -> dict:
    where = "county_fips=?" if redo else \
            "county_fips=? AND id NOT IN (SELECT property_id FROM scores WHERE kind='overall')"
    todo = db.q(f"SELECT COUNT(*) c FROM properties WHERE {where}", (fips,))[0]["c"]
    if not todo:
        return {"county": county, "status": "nothing to score"}
    st = {"county": county, "scored": 0, "detail": 0, "secs": 0.0}
    t0 = time.time()
    ranked: list[tuple[float, int, dict]] = []
    now = utcnow()
    last_id = 0
    while True:
        if free_gb() < DISK_FLOOR_GB:
            st["status"] = "stopped: disk floor"
            return st
        # Keyset paging on id. OFFSET would rescan the whole county each chunk,
        # and when redo is off the rows just scored drop out of the predicate,
        # which makes a growing OFFSET skip work silently.
        rows = db.q(f"SELECT * FROM properties WHERE {where} AND id > ? ORDER BY id LIMIT ?",
                    (fips, last_id, CHUNK))
        if not rows:
            break
        last_id = rows[-1]["id"]
        overall_rows, rec_rows = [], []
        for r in rows:
            p = dict(r)
            try:
                out = scoring.compute(p, persist=False)
            except Exception:
                continue
            ov = out.get("overall") or {}
            sc = ov.get("score") or 0
            overall_rows.append((p["id"], "overall", sc, ov.get("confidence"), jdump(ov), now))
            rec_rows.append((out.get("recommendation"), p["id"]))
            ranked.append((sc, p["id"], out))
            st["scored"] += 1
        db.many("INSERT INTO scores(property_id,kind,score,confidence,breakdown_json,computed_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(property_id,kind) DO UPDATE SET score=excluded.score, "
                "confidence=excluded.confidence, breakdown_json=excluded.breakdown_json, "
                "computed_at=excluded.computed_at", overall_rows)
        db.many("UPDATE properties SET recommendation=? WHERE id=?", rec_rows)
        # Only the top slice keeps its detail sheets, so the rest can be dropped
        # instead of held in memory for a 180,000-parcel county.
        if len(ranked) > DETAIL_KEEP * 3:
            ranked.sort(key=lambda x: -x[0])
            del ranked[DETAIL_KEEP:]

    # the detail sheets, only for the slice that ranks
    ranked.sort(key=lambda x: -x[0])
    detail = []
    for sc, pid, out in ranked[:DETAIL_KEEP]:
        for kind in scoring.SCORE_KINDS:
            if kind == "overall" or kind not in out:
                continue
            d = out[kind]
            detail.append((pid, kind, d.get("score"), d.get("confidence"), jdump(d), now))
    if detail:
        db.many("INSERT INTO scores(property_id,kind,score,confidence,breakdown_json,computed_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(property_id,kind) DO UPDATE SET score=excluded.score, "
                "confidence=excluded.confidence, breakdown_json=excluded.breakdown_json, "
                "computed_at=excluded.computed_at", detail)
        st["detail"] = len(detail)
    st["secs"] = round(time.time() - t0, 1)
    st["rate"] = round(st["scored"] / max(st["secs"], 1))
    st["status"] = "ok"
    return st


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--redo", action="store_true", help="rescore rows that already have a score")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    db.init_db()
    terrs = sorted(TERRITORIES, key=lambda t: t["county"])
    if a.only:
        want = {x.strip() for x in a.only.split(",")}
        terrs = [t for t in terrs if t["county_fips"] in want]

    if a.status:
        tot_p = tot_s = 0
        for t in terrs:
            f = t["county_fips"]
            p = db.q("SELECT COUNT(*) c FROM properties WHERE county_fips=?", (f,))[0]["c"]
            s = db.q("SELECT COUNT(*) c FROM scores s JOIN properties p ON p.id=s.property_id "
                     "WHERE p.county_fips=? AND s.kind='overall'", (f,))[0]["c"]
            tot_p += p; tot_s += s
            if p:
                print(f"  {f}  {t['county']:16s} {s:8,}/{p:8,} scored")
        print(f"\n  {tot_s:,}/{tot_p:,} scored statewide · {free_gb():.0f} GB free")
        return 0

    for i, t in enumerate(terrs, 1):
        if free_gb() < DISK_FLOOR_GB:
            print(f"STOPPING: {free_gb():.0f} GB free is under the {DISK_FLOOR_GB} GB floor", flush=True)
            break
        r = score_county(t["county_fips"], t["county"], a.redo)
        if r.get("status") != "nothing to score":
            print(f"[{i}/{len(terrs)}] {r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
