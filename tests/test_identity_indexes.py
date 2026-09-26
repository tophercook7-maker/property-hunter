"""Identity resolution must use an index, not scan the table.

identity.resolve() looks a parcel up with the separators stripped. Wrapping the
column in REPLACE() makes a plain index on parcel_id unusable, so every resolve
became a full table scan: 181 ms each at 195,000 rows. A scheduled rescan sat at
99% CPU for an hour and a half and wrote nothing, which reads as a hang rather
than as slowness. At two million rows it would be seconds per resolve.

An expression index fixes it only while it matches the predicate character for
character, so this asserts the plan, not the wording.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from hunter import db

ROOT = Path(__file__).resolve().parent.parent

PARCEL_LOOKUP = ("SELECT id FROM properties WHERE REPLACE(REPLACE(parcel_id,'-',''),' ','')=? "
                 "AND (county_fips=? OR ? IS NULL) LIMIT 1")


def _plan(conn, sql, params):
    return " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_the_parcel_lookup_uses_an_index():
    db.init_db()
    conn = db.connect()
    plan = _plan(conn, PARCEL_LOOKUP, ("00103032000", "05001", "05001"))
    assert "SCAN properties" not in plan, f"full table scan on every identity resolve: {plan}"
    assert "idx_prop_parcel_norm" in plan, plan


def test_county_scoped_reads_use_an_index():
    db.init_db()
    conn = db.connect()
    plan = _plan(conn, "SELECT canonical_key FROM properties WHERE county_fips=?", ("05051",))
    assert "SCAN properties" not in plan, f"county reads scan the whole table: {plan}"


def test_the_index_expression_still_matches_the_query_in_identity():
    """If the predicate in identity.py is reworded, the expression index stops
    being used and nothing fails visibly -- it just gets slow again."""
    src = (ROOT / "hunter" / "identity.py").read_text()
    schema = (ROOT / "hunter" / "db.py").read_text()
    expr = "REPLACE(REPLACE(parcel_id,'-',''),' ','')"
    assert expr in src, "identity.py no longer uses the expression the index was built for"
    assert expr in schema, "db.py no longer declares an index on that expression"


def test_expression_index_actually_answers_the_lookup():
    """A behavioural check on a throwaway database, so this holds even where the
    real one is absent."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE properties(id INTEGER PRIMARY KEY, parcel_id TEXT, county_fips TEXT)")
    c.execute("CREATE INDEX i ON properties(REPLACE(REPLACE(parcel_id,'-',''),' ',''), county_fips)")
    c.executemany("INSERT INTO properties(parcel_id,county_fips) VALUES(?,?)",
                  [(f"{n:03d}-{n:05d}-000", "05001") for n in range(500)])
    target = "001-00001-000"                       # normalises to 00100001000
    normalised = target.replace("-", "").replace(" ", "")
    plan = _plan(c, PARCEL_LOOKUP, (normalised, "05001", "05001"))
    assert "SCAN" not in plan, plan
    row = c.execute(PARCEL_LOOKUP, (normalised, "05001", "05001")).fetchone()
    assert row is not None, "the normalised lookup must still find the row it indexes"
    # and the separators genuinely are what the index is seeing through
    assert c.execute(PARCEL_LOOKUP, (target, "05001", "05001")).fetchone() is None
