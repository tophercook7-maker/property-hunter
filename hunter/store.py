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
from typing import Any, Iterable

from . import db, exclusions, identity
from .db import jdump, jload, utcnow
from .normalize import normalize_address, normalize_owner
from .sources.base import Record

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
        cur = db.ex(f"INSERT INTO properties({','.join(cols)}) VALUES({placeholders})", vals)
        prop_id = cur.lastrowid
        action = "created"
    else:
        existing = dict(db.q1("SELECT * FROM properties WHERE id=?", (prop_id,)))
        sets, vals = [], []
        for k, v in fields.items():
            old = existing.get(k)
            if v is None:
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
