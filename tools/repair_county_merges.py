"""Undo cross-county parcel merges.

Before 2026-09-14 the parcel-alias match ignored the county, so a Grant County
parcel numbered 001-03774-000 was folded into the Saline County row with the
same number. The row then held Grant's data under Saline's key, and the
"changes" it recorded (owner, values, acreage) were two different properties
being compared, not anything that happened in the world.

This script, for every property that "changed county":
  * re-keys the row to the county it now describes (the newest data wins,
    because that is what the columns hold),
  * deletes the change rows written at the moment of the merge (they are
    artefacts, not changes),
  * deletes evidence retrieved before the merge (it describes the other
    county's parcel),
  * lists the counties whose records were lost, so they can be re-hunted.

Usage: python3 tools/repair_county_merges.py [--apply]
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from collections import Counter

from hunter import db
from hunter.identity import canonical_key


def main() -> int:
    apply = "--apply" in sys.argv
    db.init_db()
    merges = db.q("""SELECT property_id, old_value, new_value, detected_at FROM changes
                     WHERE field='county_fips' ORDER BY detected_at""")
    lost = Counter()
    rekeyed = dropped_changes = dropped_evidence = 0
    for m in merges:
        pid = m["property_id"]
        row = db.q1("SELECT * FROM properties WHERE id=?", (pid,))
        if not row:
            continue
        lost[m["old_value"]] += 1
        key = canonical_key(dict(row)) or row["canonical_key"]
        n_changes = db.q1("SELECT COUNT(*) n FROM changes WHERE property_id=? AND detected_at=?",
                          (pid, m["detected_at"]))["n"]
        n_ev = db.q1("SELECT COUNT(*) n FROM evidence WHERE property_id=? AND created_at<?",
                     (pid, m["detected_at"]))["n"]
        if apply:
            try:
                db.ex("UPDATE properties SET canonical_key=?, first_seen=? WHERE id=?",
                      (key, m["detected_at"], pid))
                rekeyed += 1
            except Exception:
                db.ex("UPDATE properties SET canonical_key=?, first_seen=? WHERE id=?",
                      (f"{key}#repaired", m["detected_at"], pid))
                rekeyed += 1
            db.ex("DELETE FROM changes WHERE property_id=? AND detected_at=?", (pid, m["detected_at"]))
            db.ex("DELETE FROM evidence WHERE property_id=? AND created_at<?", (pid, m["detected_at"]))
            db.ex("DELETE FROM property_aliases WHERE property_id=? AND alias_type='parcel'", (pid,))
        dropped_changes += n_changes
        dropped_evidence += n_ev
    print(f"{'applied' if apply else 'would apply'}: {len(merges)} merged rows re-keyed, "
          f"{dropped_changes} artefact changes removed, {dropped_evidence} stale evidence rows removed")
    print("counties that lost records (re-hunt these):",
          ", ".join(f"{k}:{v}" for k, v in sorted(lost.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
