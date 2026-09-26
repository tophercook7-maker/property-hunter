#!/usr/bin/env python3
"""Add the owner MAILING ADDRESS from the Garland County namelist to parcels the hunt already holds.

LOCAL ONLY. The address is evidence `owner_mailing_address` (AUTOMATED_SOURCE, source garland_namelist) so the
licensed workup can answer "is there a mailing address of record" and the outreach gate can open. It is never
published: the exporter does not carry the field, the sample publisher strips it, Bee's snapshot redacts it,
and tests/test_published_privacy.py fails the build if a street line from this file reaches docs/.

Matches parcels by county + normalized parcel id only. Never creates a property. Idempotent: an identical
address already on file is not written again (store.store_evidence dedupes by value + source).

    python3 tools/import_namelist_mailing.py ~/Downloads/Garland-County-26-1543-REVISED-NAMELIST.xlsx [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hunter import db, store  # noqa: E402

SOURCE = "garland_namelist"
COUNTY_FIPS = "05051"
RECORD_DATE = "2026-09-24"          # the county fulfilled records request #26-1543 on this date


def norm_parcel(v) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def read_rows(path: str):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    it = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h is not None else "" for h in next(it)]
    for r in it:
        yield dict(zip(hdr, r))


def mailing(row: dict) -> str | None:
    parts = [str(row.get(k) or "").strip() for k in ("ADDRESS1", "ADDRESS2", "CSZ")]
    parts = [p for p in parts if p]
    if len(parts) < 2 or not re.search(r"[A-Z]{2}\s+\d{5}", parts[-1]):
        return None
    return ", ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    ids = {norm_parcel(r["parcel_id"]): r["id"] for r in db.q("SELECT id, parcel_id FROM properties WHERE county_fips=? AND parcel_id IS NOT NULL", (COUNTY_FIPS,))}
    print(f"{len(ids):,} Garland parcels in the DB", flush=True)
    t0 = time.time(); seen = matched = written = skipped = 0
    for row in read_rows(a.xlsx):
        seen += 1
        pid = ids.get(norm_parcel(row.get("PARCEL")))
        addr = mailing(row)
        if not pid or not addr:
            skipped += 1
            continue
        matched += 1
        if not a.dry_run:
            store.store_evidence(pid, [{"field": "owner_mailing_address", "value": addr, "evidence_type": "FACT", "confidence": "HIGH", "source": SOURCE,
                                        "source_name": "Garland County Assessor namelist (records request #26-1543)", "source_url": None, "effective_date": RECORD_DATE,
                                        "raw_ref": "where the county mails the tax bill per the assessor's extract; LOCAL ONLY, never published"}])
            written += 1
        if a.limit and matched >= a.limit:
            break
        if seen % 10000 == 0:
            print(f"  {seen:,} rows · {matched:,} matched · {int(time.time() - t0)} s", flush=True)
    print(f"done: {seen:,} rows read, {matched:,} matched a Garland parcel, {written:,} written, {skipped:,} skipped (no parcel on file or no usable address), {int(time.time() - t0)} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
