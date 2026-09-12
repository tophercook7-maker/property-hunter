"""Ingest layer: turn source Records into stored properties + evidence.

Rules enforced here, not in the UI:
  * excluded geographies are flagged on write (spec 17)
  * every stored fact carries source / date / confidence (spec 10)
  * conflicting values are recorded, never silently overwritten (spec 55)
  * previous values are snapshotted so change detection has something to
    compare against (spec 11/13) and history never disappears (spec 79)
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Iterable

from . import db, exclusions, identity
from .db import jdump, jload, utcnow
from . import geo
from .normalize import normalize_address, normalize_owner
from .sources.base import Record

# A centroid that moves less than this between sources is the same parcel drawn
# by a different hand - a refinement, not a change.
COORD_REFINEMENT_M = 60.0


def _same_value(old, new) -> bool:
    """'21450.0' and 21450 are the same assessed value. Different sources hand
    numbers back as floats, ints and strings; a formatting difference is not a
    change and must never raise an alert."""
    if old is None or new is None:
        return old is None and new is None
    if str(old) == str(new):
        return True
    try:
        return abs(float(old) - float(new)) < 1e-6
    except (TypeError, ValueError):
        return False


def _less_specific_address(old: str | None, new: str | None) -> bool:
    """'1100 Park' is the same address as '1100 Park Ave' with the suffix missing.
    A source that knows less must not overwrite one that knows more."""
    o, n = normalize_address(old), normalize_address(new)
    if not o or not n or o == n:
        return False
    if o[0].isdigit() and not n[0].isdigit():
        return True                        # 'HOWE ST' must not replace '112 HOWE ST'
    return o.startswith(n + " ")

# Columns a source is allowed to write straight onto the property row.
WRITABLE = {
    "territory", "county_fips", "parcel_id", "rpid", "address", "address_norm",
    "city", "zip", "lat", "lon", "subdivision", "legal", "acreage", "owner_name",
    "parcel_type", "property_type", "improved", "land_value", "imp_value",
    "total_value", "building_sqft", "year_built", "list_price", "listing_status",
    "tax_status", "zoning", "flood_zone", "road_frontage_m", "road_class",
    "data_class",
}

# Changes we shout about (spec 13).
IMPORTANT_FIELDS = {
    "owner_name": ("Owner changed", "high"),
    "total_value": ("Assessed value changed", "medium"),
    "imp_value": ("Improvement value changed", "medium"),
    "land_value": ("Land value changed", "low"),
    "list_price": ("Price changed", "high"),
    "listing_status": ("Listing status changed", "high"),
    "tax_status": ("Tax status changed", "high"),
    "zoning": ("Zoning changed", "high"),
    "flood_zone": ("Flood zone changed", "medium"),
    "acreage": ("Acreage changed", "medium"),
    "property_type": ("Property type changed", "low"),
    "improved": ("Improvement status changed", "medium"),
}

CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}


def _hash(payload: Any) -> str:
    return hashlib.sha256(jdump(payload).encode()).hexdigest()[:32]


def _clean(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def ingest(record: Record, *, data_class: str = "real",
           scan_id: int | None = None) -> tuple[int, str, list[dict]]:
    """Store one record. Returns (property_id, action, detected_changes)."""
    fields = {k: _clean(v) for k, v in record.fields.items() if k in WRITABLE}
    fields.setdefault("data_class", data_class)
    # Always derive the normalized address ourselves. A source that hands us a
    # normalized form that disagrees with its own address would silently break
    # every address-based match downstream.
    if fields.get("address"):
        fields["address_norm"] = normalize_address(fields["address"])
    elif "address_norm" in fields:
        fields.pop("address_norm")

    prop_id, matched_by = identity.resolve(fields)
    now = utcnow()
    verdict = exclusions.check_property(fields)

    changes: list[dict] = []
    extra_rpid = None
    if prop_id is None:
        key = identity.canonical_key(fields)
        if not key:
            key = f"anon:{_hash(record.raw or fields)}"
        cols = ["canonical_key", "first_seen", "last_seen", "created_at", "updated_at",
                "excluded", "exclusion_reason", "owner_norm"]
        vals = [key, now, now, now, now,
                1 if verdict.excluded else 0,
                f"{verdict.label}: {verdict.reason}" if verdict.excluded else None,
                normalize_owner(fields.get("owner_name"))]
        for k, v in fields.items():
            cols.append(k)
            vals.append(v)
        placeholders = ",".join("?" * len(cols))
        # Two genuinely different properties can share an address-only key
        # (units, split lots). Never let that collision lose a record.
        for attempt in range(1, 6):
            try:
                cur = db.ex(f"INSERT INTO properties({','.join(cols)}) VALUES({placeholders})", vals)
                break
            except sqlite3.IntegrityError:
                vals[0] = f"{key}#{attempt}"
        else:                                                    # pragma: no cover
            raise
        prop_id = cur.lastrowid
        action = "created"
    else:
        existing = dict(db.q1("SELECT * FROM properties WHERE id=?", (prop_id,)))
        sets, vals = [], []
        # Coordinates: a small shift is a refinement, never a "change"; and a
        # record with no parcel id (a register polygon, a case point) never
        # overrides coordinates we already hold - the parcel centroid wins.
        if (fields.get("lat") is not None and fields.get("lon") is not None
                and existing.get("lat") is not None and existing.get("lon") is not None):
            shift = geo.haversine_m(fields["lon"], fields["lat"], existing["lon"], existing["lat"])
            if shift < COORD_REFINEMENT_M or record.source != "ar_gis_parcels":
                fields.pop("lat"), fields.pop("lon")        # keep what we have
        # Address: never let a suffix-less form overwrite the fuller one; when
        # only the unit differs (apartments at one building) keep what we have;
        # and when the new form is MORE specific, take it quietly - that is a
        # refinement, not something to alert about.
        if fields.get("address") and existing.get("address"):
            old_n, new_n = normalize_address(existing["address"]), normalize_address(fields["address"])
            if _less_specific_address(existing["address"], fields["address"]) or old_n == new_n:
                fields.pop("address", None)
                fields.pop("address_norm", None)
            elif new_n.startswith(old_n + " "):
                db.ex("UPDATE properties SET address=?, address_norm=? WHERE id=?",
                      (fields["address"], new_n, prop_id))
                fields.pop("address", None)
                fields.pop("address_norm", None)
            else:
                # Two sources, two house numbers, one parcel. Keep what we have,
                # write the disagreement down where it can be seen (spec 55).
                from .normalize import address_number
                if address_number(old_n) and address_number(new_n) and \
                        address_number(old_n) != address_number(new_n):
                    if not db.q1("SELECT 1 FROM conflicts WHERE property_id=? AND field='address' "
                                 "AND status='NEEDS VERIFICATION'", (prop_id,)):
                        db.ex("INSERT INTO conflicts(property_id,field,value_a,source_a,date_a,"
                              "value_b,source_b,date_b,status,created_at) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (prop_id, "address", existing["address"], "earlier source", None,
                               fields["address"], record.source, None,
                               "NEEDS VERIFICATION", utcnow()))
                    fields.pop("address", None)
                    fields.pop("address_norm", None)
        extra_rpid = None
        if fields.get("rpid") and existing.get("rpid") and \
                str(existing["rpid"]) != str(fields["rpid"]):
            extra_rpid = str(fields.pop("rpid"))       # second account on this parcel
        for k, v in fields.items():
            old = existing.get(k)
            if v is None:
                continue
            if k == "address_norm":
                # follows address; recorded through it, not separately
                sets.append("address_norm=?")
                vals.append(v)
                continue
            if _same_value(old, v):
                continue
            if old is None or old == "":
                # A field going from nothing to something is usually just a gap
                # being filled in - but for the fields that change a deal (a price
                # appearing, a tax status appearing, a flood zone appearing) the
                # appearance IS the news, so it is recorded and alerted.
                if k in IMPORTANT_FIELDS:
                    changes.append({"field": k, "old": "not known", "new": v})
                sets.append(f"{k}=?")
                vals.append(v)
            elif str(old) != str(v):
                changes.append({"field": k, "old": old, "new": v})
                sets.append(f"{k}=?")
                vals.append(v)
        if fields.get("owner_name"):
            sets.append("owner_norm=?")
            vals.append(normalize_owner(fields["owner_name"]))
        sets += ["last_seen=?", "updated_at=?", "excluded=?", "exclusion_reason=?"]
        vals += [now, now, 1 if verdict.excluded else 0,
                 f"{verdict.label}: {verdict.reason}" if verdict.excluded else None]
        vals.append(prop_id)
        db.ex(f"UPDATE properties SET {','.join(sets)} WHERE id=?", vals)
        action = "updated" if changes else "seen"

    identity.record_aliases(prop_id, fields, record.source)
    if action != "created" and extra_rpid:
        identity.record_aliases(prop_id, {"rpid": extra_rpid, "county_fips": fields.get("county_fips")},
                                record.source)
        if not db.q1("SELECT 1 FROM evidence WHERE property_id=? AND field='additional_rpid' "
                     "AND value=?", (prop_id, extra_rpid)):
            store_evidence(prop_id, [{
                "field": "additional_rpid", "value": extra_rpid,
                "evidence_type": "FACT", "confidence": "HIGH", "source": record.source,
                "raw_ref": f"a second account ({fields.get('address') or 'no address'}) on "
                           f"the same parcel; the roll can carry several structures per parcel"}])
    store_evidence(prop_id, record.evidence)
    store_timeline(prop_id, record.timeline)
    snapshot(prop_id, record.source, record.raw or fields)
    if changes:
        store_changes(prop_id, record.source, changes)
    return prop_id, action, changes


# ------------------------------------------------------------------ evidence

def store_evidence(prop_id: int, items: Iterable[dict]) -> None:
    for item in items:
        field = item.get("field")
        if not field:
            continue
        value = item.get("value")
        prior = db.q1(
            "SELECT * FROM evidence WHERE property_id=? AND field=? "
            "ORDER BY id DESC LIMIT 1", (prop_id, field))
        if prior and str(prior["value"]) == str(value) and prior["source"] == item.get("source"):
            db.ex("UPDATE evidence SET retrieved_at=? WHERE id=?",
                  (item.get("retrieved_at") or utcnow(), prior["id"]))
            continue
        db.ex(
            "INSERT INTO evidence(property_id,field,value,evidence_type,confidence,source,"
            "source_name,source_url,retrieved_at,effective_date,raw_ref,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (prop_id, field, None if value is None else str(value),
             item.get("evidence_type", "UNKNOWN"), item.get("confidence", "LOW"),
             item.get("source", "unknown"), item.get("source_name"),
             item.get("source_url"), item.get("retrieved_at") or utcnow(),
             item.get("effective_date"), item.get("raw_ref"), utcnow()))
        # A different source disagreeing is a conflict, not an overwrite.
        if (prior and prior["source"] != item.get("source")
                and str(prior["value"]) != str(value)
                and item.get("evidence_type") in ("FACT", "OBSERVATION")
                and prior["evidence_type"] in ("FACT", "OBSERVATION")):
            already = db.q1("SELECT id FROM conflicts WHERE property_id=? AND field=? "
                            "AND status='NEEDS VERIFICATION'", (prop_id, field))
            if not already:
                db.ex("INSERT INTO conflicts(property_id,field,value_a,source_a,date_a,"
                      "value_b,source_b,date_b,status,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?, 'NEEDS VERIFICATION', ?)",
                      (prop_id, field, prior["value"], prior["source"],
                       prior["effective_date"], str(value), item.get("source"),
                       item.get("effective_date"), utcnow()))


def latest_evidence(prop_id: int, field: str) -> dict | None:
    row = db.q1("SELECT * FROM evidence WHERE property_id=? AND field=? "
                "ORDER BY id DESC LIMIT 1", (prop_id, field))
    return dict(row) if row else None


def evidence_for(prop_id: int) -> list[dict]:
    return db.rows_to_dicts(
        db.q("SELECT * FROM evidence WHERE property_id=? ORDER BY field, id DESC", (prop_id,)))


def known_value(prop_id: int, field: str, default=None):
    ev = latest_evidence(prop_id, field)
    return ev["value"] if ev else default


# ------------------------------------------------------------------ timeline

def store_timeline(prop_id: int, events: Iterable[dict]) -> None:
    for e in events:
        dup = db.q1("SELECT id FROM timeline WHERE property_id=? AND kind=? AND title=? "
                    "AND IFNULL(event_date,'')=IFNULL(?,'')",
                    (prop_id, e.get("kind", "event"), e.get("title", ""), e.get("event_date")))
        if dup:
            continue
        db.ex("INSERT INTO timeline(property_id,event_date,kind,title,detail,source,"
              "source_url,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (prop_id, e.get("event_date"), e.get("kind", "event"), e.get("title", ""),
               e.get("detail"), e.get("source"), e.get("source_url"), utcnow()))


def add_timeline(prop_id: int, kind: str, title: str, detail: str = "",
                 event_date: str | None = None, source: str = "property_hunter",
                 source_url: str = "") -> None:
    store_timeline(prop_id, [{"event_date": event_date or utcnow()[:10], "kind": kind,
                              "title": title, "detail": detail, "source": source,
                              "source_url": source_url}])


# ----------------------------------------------------------------- snapshots

def snapshot(prop_id: int, source: str, payload: Any) -> bool:
    """Store the source payload if it differs from the last one. True if new."""
    h = _hash(payload)
    prior = db.q1("SELECT payload_hash FROM snapshots WHERE property_id=? AND source=? "
                  "ORDER BY id DESC LIMIT 1", (prop_id, source))
    if prior and prior["payload_hash"] == h:
        return False
    db.ex("INSERT INTO snapshots(property_id,source,payload_hash,payload_json,captured_at) "
          "VALUES(?,?,?,?,?)", (prop_id, source, h, jdump(payload), utcnow()))
    return True


# ------------------------------------------------------------------- changes

def store_changes(prop_id: int, source: str, changes: list[dict]) -> None:
    prop = db.q1("SELECT address, parcel_id, excluded FROM properties WHERE id=?", (prop_id,))
    label = (prop["address"] if prop and prop["address"] else
             (prop["parcel_id"] if prop else f"property {prop_id}"))
    for ch in changes:
        title, severity = IMPORTANT_FIELDS.get(ch["field"], (None, None))
        db.ex("INSERT INTO changes(property_id,field,old_value,new_value,source,severity,"
              "detected_at) VALUES(?,?,?,?,?,?,?)",
              (prop_id, ch["field"], str(ch["old"]), str(ch["new"]), source,
               severity or "info", utcnow()))
        if title and not (prop and prop["excluded"]):
            add_alert(prop_id, "property_changed", f"{label} - {title.lower()}",
                      f"{ch['field']}: {ch['old']} -> {ch['new']} (source: {source})",
                      severity or "info")
            add_timeline(prop_id, "change", title,
                         f"{ch['old']} -> {ch['new']}", source=source)


def add_alert(prop_id: int | None, kind: str, title: str, body: str = "",
              severity: str = "info") -> int:
    cur = db.ex("INSERT INTO alerts(property_id,kind,title,body,severity,created_at) "
                "VALUES(?,?,?,?,?,?)", (prop_id, kind, title, body, severity, utcnow()))
    return cur.lastrowid


# ---------------------------------------------------------------- retrieval

def get_property(prop_id: int) -> dict | None:
    row = db.q1("SELECT * FROM properties WHERE id=?", (prop_id,))
    if not row:
        return None
    p = dict(row)
    p["distress"] = jload(p.get("distress_json"), []) or []
    return p


def add_task(prop_id: int | None, task: dict) -> int:
    existing = db.q1("SELECT id FROM tasks WHERE IFNULL(property_id,-1)=IFNULL(?,-1) "
                     "AND title=? AND status='open'", (prop_id, task.get("title")))
    if existing:
        return existing["id"]
    cur = db.ex(
        "INSERT INTO tasks(property_id,investigation_id,priority,title,detail,why,"
        "where_to_look,source,source_url,status,owner,manual,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,'open',?,?,?)",
        (prop_id, task.get("investigation_id"), task.get("priority", 3),
         task.get("title", "Task"), task.get("detail"), task.get("why"),
         task.get("where_to_look"), task.get("source"), task.get("source_url"),
         task.get("owner", "Topher"), task.get("manual", 0), utcnow()))
    return cur.lastrowid


def adopt_parcel_id(prop_id: int, parcel_id: str) -> int:
    """A property we only knew by location has learned its parcel id.

    If a property with that parcel id already exists (the county record the
    register polygon should have matched), fold this one INTO it and return the
    survivor's id; otherwise just set the id. Either way, history is kept.
    """
    from . import identity
    from .normalize import normalize_parcel
    norm = normalize_parcel(parcel_id)
    row = db.q1("SELECT id FROM properties WHERE id!=? AND "
                "REPLACE(REPLACE(parcel_id,'-',''),' ','')=?", (prop_id, norm))
    if row:
        keep = row["id"]
        from .normalize import address_number
        mine = db.q1("SELECT address FROM properties WHERE id=?", (prop_id,))
        theirs = db.q1("SELECT address FROM properties WHERE id=?", (keep,))
        a, b = (mine["address"] if mine else None), (theirs["address"] if theirs else None)
        identity.merge_duplicates(keep, prop_id)
        add_timeline(keep, "identity", "Merged a City-register record onto this parcel",
                     f"register record #{prop_id} matched by the parcel polygon")
        na, nb = address_number(a), address_number(b)
        if na and nb and na != nb and not db.q1(
                "SELECT 1 FROM conflicts WHERE property_id=? AND field='address' "
                "AND status='NEEDS VERIFICATION'", (keep,)):
            db.ex("INSERT INTO conflicts(property_id,field,value_a,source_a,date_a,value_b,"
                  "source_b,date_b,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (keep, "address", b, "county tax roll", None, a,
                   "City register (polygon at this location)", None,
                   "NEEDS VERIFICATION", utcnow()))
            add_alert(keep, "conflict", f"{b or a} - the City register and the county roll "
                      f"give different house numbers", f"county says {b!r}, register says {a!r}. "
                      "Same parcel polygon, different address - check which is right.", "medium")
        return keep
    db.ex("UPDATE properties SET parcel_id=?, canonical_key=? WHERE id=?",
          (parcel_id, f"parcel:05051:{norm}", prop_id))
    identity.record_aliases(prop_id, {"parcel_id": parcel_id, "county_fips": "05051"},
                            "hs_gis_owner_mailing")
    return prop_id


def set_fields(prop_id: int, cols: dict, source: str) -> None:
    """Write columns AND keep identity aliases in step.

    An rpid or parcel id written straight to the row without its alias is how
    a later record found a register-only twin instead of the county record."""
    from . import identity
    cols = {k: v for k, v in cols.items() if k in WRITABLE and v is not None}
    if not cols:
        return
    sets = ",".join(f"{k}=?" for k in cols)
    db.ex(f"UPDATE properties SET {sets} WHERE id=?", (*cols.values(), prop_id))
    ident = {k: cols[k] for k in ("parcel_id", "rpid", "address") if k in cols}
    if ident:
        row = db.q1("SELECT county_fips FROM properties WHERE id=?", (prop_id,))
        ident["county_fips"] = (row["county_fips"] if row and row["county_fips"] else "05051")
        identity.record_aliases(prop_id, ident, source)


def merge_rpid_twins() -> int:
    """Two properties, one RPID, only one with a parcel id: the parcel-less one
    is a register record that never found its parcel. Fold it onto the other."""
    from . import identity
    rows = db.q("""SELECT a.id AS keep, b.id AS drop_ FROM properties a JOIN properties b
                   ON a.rpid=b.rpid AND a.id!=b.id
                   WHERE a.rpid IS NOT NULL AND a.parcel_id IS NOT NULL AND b.parcel_id IS NULL
                   AND a.excluded=0 AND b.excluded=0""")
    n = 0
    seen = set()
    for r in rows:
        if r["drop_"] in seen:
            continue
        seen.add(r["drop_"])
        identity.merge_duplicates(r["keep"], r["drop_"])
        add_timeline(r["keep"], "identity", "Merged an RPID twin onto this parcel",
                     f"register record #{r['drop_']} shared this RPID and had no parcel")
        n += 1
    return n


def merge_address_twins() -> int:
    """Two properties at one NUMBERED address where only one came from the
    county roll: the other is a register record that missed its parcel (a
    condo-unit parcel, a boundary centroid). Fold it onto the county record.
    Unnumbered addresses ('E Grand Ave') are many lots and are never merged."""
    from . import identity
    # keep the county-anchored side; when neither side is (two register-created
    # condo-unit parcels at one building), keep the one seen first
    rows = db.q("""SELECT a.id AS keep, b.id AS drop_, a.address FROM properties a
                   JOIN properties b ON a.address_norm=b.address_norm AND a.id!=b.id
                        AND a.county_fips=b.county_fips
                   WHERE a.address_norm IS NOT NULL AND a.excluded=0 AND b.excluded=0
                   AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.property_id=b.id
                                   AND e.source='ar_gis_parcels')
                   AND (EXISTS (SELECT 1 FROM evidence e WHERE e.property_id=a.id
                                AND e.source='ar_gis_parcels') OR a.id < b.id)""")
    n, seen = 0, set()
    for r in rows:
        if r["drop_"] in seen or r["keep"] in seen or not (r["address"] or "")[:1].isdigit():
            continue
        seen.add(r["drop_"])
        identity.merge_duplicates(r["keep"], r["drop_"])
        add_timeline(r["keep"], "identity", "Merged an address twin onto this parcel",
                     f"register record #{r['drop_']} carried the same numbered address")
        n += 1
    return n
