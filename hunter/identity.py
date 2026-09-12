"""Property identity resolution (spec 54).

Two sources describing "2748 Malvern Ave" and "2748  MALVERN AVENUE" are the
same property. A parcel id is the strongest key; address, coordinates and legal
description back it up. Every key we see is kept as an alias so the next source
that spells it differently still lands on the same record.
"""
from __future__ import annotations

from . import db, geo
from .db import utcnow
from .normalize import (address_number, fuzzy_ratio, normalize_address,
                        normalize_owner, normalize_parcel, squash)

COORD_MATCH_M = 40.0        # centroids this close are treated as the same parcel


def canonical_key(fields: dict) -> str:
    parcel = normalize_parcel(fields.get("parcel_id"))
    if parcel:
        return f"parcel:{fields.get('county_fips') or '?'}:{parcel}"
    rpid = squash(fields.get("rpid"))
    if rpid:
        return f"rpid:{fields.get('county_fips') or '?'}:{rpid}"
    addr = normalize_address(fields.get("address"))
    if addr:
        return f"addr:{fields.get('county_fips') or '?'}:{addr}"
    lat, lon = fields.get("lat"), fields.get("lon")
    if lat is not None and lon is not None:
        return f"geo:{lat:.5f},{lon:.5f}"
    legal = squash(fields.get("legal"))
    if legal:
        return f"legal:{fields.get('county_fips') or '?'}:{legal[:120]}"
    return ""


def _by_alias(alias_type: str, value: str) -> int | None:
    if not value:
        return None
    row = db.q1("SELECT property_id FROM property_aliases WHERE alias_type=? AND alias_value=? "
                "ORDER BY id LIMIT 1", (alias_type, value))
    return row["property_id"] if row else None


def resolve(fields: dict) -> tuple[int | None, str]:
    """Find the existing property this record belongs to.

    Returns (property_id or None, how_we_matched).
    """
    county = fields.get("county_fips")
    parcel = normalize_parcel(fields.get("parcel_id"))
    incoming_num = address_number(fields.get("address"))
    incoming_rpid = squash(fields.get("rpid"))

    def _parcel_conflict(candidate_id: int) -> bool:
        """True when the candidate is provably a different property.

        Three independent tells, any one of which is enough: a different parcel
        id, a different RPID, or a different house number. Adjacent lots share a
        wall, a street and a centroid a few metres apart - the house number is
        what tells 118 Magnolia from 134 Magnolia."""
        row = db.q1("SELECT parcel_id, rpid, address FROM properties WHERE id=?",
                    (candidate_id,))
        if not row:
            return False
        other_parcel = normalize_parcel(row["parcel_id"])
        if parcel and other_parcel and other_parcel != parcel:
            return True
        other_rpid = squash(row["rpid"])
        if incoming_rpid and other_rpid:
            # The City's own parcel identifier settles it either way: the same
            # RPID is the same parcel even if the two sources spell the address
            # with different numbers (that disagreement is recorded as a conflict
            # when the records merge), and a different RPID is a different parcel.
            return other_rpid != incoming_rpid
        other_num = address_number(row["address"])
        if incoming_num and other_num and other_num != incoming_num:
            return True
        return False


    # 1. parcel id - strongest
    if parcel:
        pid = _by_alias("parcel", parcel)
        if pid:
            return pid, "parcel id"
        row = db.q1("SELECT id FROM properties WHERE REPLACE(REPLACE(parcel_id,'-',''),' ','')=? "
                    "AND (county_fips=? OR ? IS NULL) LIMIT 1", (parcel, county, county))
        if row:
            return row["id"], "parcel id"

    # 2. RPID
    rpid = squash(fields.get("rpid"))
    if rpid:
        pid = _by_alias("rpid", rpid)
        if pid and not _parcel_conflict(pid):
            return pid, "RPID"

    # A record that carries its own parcel id must never be folded into a
    # property that already has a *different* parcel id. Two neighbouring lots
    # can share a wall, an address stem and a centroid 5 m apart - they are
    # still two properties.
    # 3. normalized address within the same county
    addr = normalize_address(fields.get("address"))
    if addr and len(addr.split()) > 1 and addr[0].isdigit():
        pid = _by_alias("address", f"{county}|{addr}")
        if pid and not _parcel_conflict(pid):
            return pid, "address"
        for row in db.q("SELECT id FROM properties WHERE address_norm=? AND county_fips=? "
                        "LIMIT 10", (addr, county)):
            if not _parcel_conflict(row["id"]):
                return row["id"], "address"

    # 4. coordinates - same spot on the map
    lat, lon = fields.get("lat"), fields.get("lon")
    if lat is not None and lon is not None:
        d = 0.0006          # ~65 m box, then exact distance
        rows = db.q("SELECT id,lat,lon,address_norm,owner_norm FROM properties "
                    "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                    (lat - d, lat + d, lon - d, lon + d))
        best, best_dist = None, COORD_MATCH_M
        for r in rows:
            if r["lat"] is None or _parcel_conflict(r["id"]):
                continue
            dist = geo.haversine_m(lon, lat, r["lon"], r["lat"])
            if dist < best_dist:
                best, best_dist = r["id"], dist
        if best:
            return best, f"coordinates ({best_dist:.0f} m apart)"

    # 5. legal description + owner, same county - last resort, fuzzy
    legal = squash(fields.get("legal"))
    owner = normalize_owner(fields.get("owner_name"))
    if legal and owner:
        rows = db.q("SELECT id,legal,owner_norm FROM properties WHERE county_fips=? "
                    "AND owner_norm=? LIMIT 25", (county, owner))
        for r in rows:
            if _parcel_conflict(r["id"]):
                continue
            if r["legal"] and fuzzy_ratio(squash(r["legal"]), legal) > 0.8:
                return r["id"], "legal description + owner"
    return None, ""


def record_aliases(property_id: int, fields: dict, source: str) -> None:
    county = fields.get("county_fips")
    pairs = []
    parcel = normalize_parcel(fields.get("parcel_id"))
    if parcel:
        pairs.append(("parcel", parcel))
    rpid = squash(fields.get("rpid"))
    if rpid:
        pairs.append(("rpid", rpid))
    addr = normalize_address(fields.get("address"))
    if addr:
        pairs.append(("address", f"{county}|{addr}"))
    legal = squash(fields.get("legal"))
    if legal:
        pairs.append(("legal", f"{county}|{legal[:150]}"))
    owner = normalize_owner(fields.get("owner_name"))
    if owner:
        pairs.append(("owner", owner))
    lat, lon = fields.get("lat"), fields.get("lon")
    if lat is not None and lon is not None:
        pairs.append(("coord", f"{lat:.5f},{lon:.5f}"))
    for kind, value in pairs:
        db.ex("INSERT OR IGNORE INTO property_aliases(property_id,alias_type,alias_value,"
              "source,created_at) VALUES(?,?,?,?,?)",
              (property_id, kind, value, source, utcnow()))


def merge_duplicates(keep_id: int, drop_id: int) -> None:
    """Fold one property record into another, keeping all history."""
    if keep_id == drop_id:
        return
    for table in ("evidence", "timeline", "changes", "snapshots", "tasks", "notes",
                  "photos", "documents", "scenarios", "decisions", "alerts",
                  "conflicts", "property_aliases", "investigations"):
        db.ex(f"UPDATE OR IGNORE {table} SET property_id=? WHERE property_id=?",
              (keep_id, drop_id))
    db.ex("DELETE FROM properties WHERE id=?", (drop_id,))
