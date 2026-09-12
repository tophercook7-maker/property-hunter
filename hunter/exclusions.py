"""Hard geographic exclusion layer (spec 17).

Hot Springs Village and Diamondhead never enter the working set. The check is
multi-signal and runs at write time, so nothing downstream - map, search, API,
scoring, reports - can leak an excluded property through.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from . import db, geo
from .config import EXCLUSIONS
from .db import jload, utcnow
from .normalize import squash


@dataclass
class Verdict:
    excluded: bool
    key: str = ""
    label: str = ""
    reason: str = ""
    signal: str = ""          # polygon|city|subdivision|zip


@lru_cache(maxsize=1)
def _boundaries() -> list[dict]:
    out = []
    for row in db.q("SELECT key,name,boundary_json,bbox_json FROM geographies "
                    "WHERE kind='exclusion'"):
        b = jload(row["boundary_json"], {}) or {}
        rings = b.get("rings") or []
        if rings:
            out.append({"key": row["key"], "name": row["name"], "rings": rings,
                        "bbox": jload(row["bbox_json"], None) or list(geo.rings_bbox(rings))})
    return out


def refresh_cache() -> None:
    _boundaries.cache_clear()


def rule_for(key: str) -> dict | None:
    for r in EXCLUSIONS:
        if r["key"] == key:
            return r
    return None


def active_rules() -> list[dict]:
    """Config rules plus any deactivation stored in the database."""
    disabled = {row["key"] for row in
                db.q("SELECT key FROM exclusion_rules WHERE active=0")}
    return [r for r in EXCLUSIONS if r["key"] not in disabled]


def check(*, lat: float | None = None, lon: float | None = None,
          city: str | None = None, subdivision: str | None = None,
          zip_code: str | None = None, address: str | None = None) -> Verdict:
    """Return the first matching exclusion. Signals are independent."""
    city_n = squash(city)
    sub_n = squash(subdivision)
    addr_n = squash(address)
    zip_n = (zip_code or "").strip()[:5]

    bounds = {b["key"]: b for b in _boundaries()}

    for rule in active_rules():
        # 1. authoritative boundary polygon
        b = bounds.get(rule["key"])
        if b and lat is not None and lon is not None:
            if geo.bbox_contains(b["bbox"], lon, lat) and geo.point_in_rings(lon, lat, b["rings"]):
                return Verdict(True, rule["key"], rule["label"],
                               f"Inside the official {b['name']} boundary "
                               f"(US Census TIGER).", "polygon")
        # 2. city / place name
        for nm in rule.get("city_names", []):
            n = squash(nm)
            if n and (city_n == n or addr_n.endswith(" " + n)):
                return Verdict(True, rule["key"], rule["label"],
                               f"Address city reads '{city}'.", "city")
        # 3. subdivision, anchored patterns only (never bare substrings)
        for pat in rule.get("subdivision_patterns", []):
            if sub_n and re.search(pat, sub_n, re.I):
                return Verdict(True, rule["key"], rule["label"],
                               f"Subdivision '{subdivision}' matches {rule['label']}.",
                               "subdivision")
        # 4. ZIP
        if zip_n and zip_n in rule.get("zips", []):
            return Verdict(True, rule["key"], rule["label"],
                           f"ZIP {zip_n} belongs to {rule['label']}.", "zip")
    return Verdict(False)


def check_property(fields: dict) -> Verdict:
    return check(lat=fields.get("lat"), lon=fields.get("lon"),
                 city=fields.get("city"), subdivision=fields.get("subdivision"),
                 zip_code=fields.get("zip"), address=fields.get("address"))


def sync_rules_to_db() -> None:
    for rule in EXCLUSIONS:
        geo_row = db.q1("SELECT id FROM geographies WHERE kind='exclusion' AND key=?",
                        (rule["key"],))
        existing = db.q1("SELECT id FROM exclusion_rules WHERE key=? AND rule_kind='polygon'",
                         (rule["key"],))
        if existing:
            db.ex("UPDATE exclusion_rules SET geography_id=?, label=? WHERE id=?",
                  (geo_row["id"] if geo_row else None, rule["label"], existing["id"]))
        else:
            db.ex("INSERT INTO exclusion_rules(key,label,territory,rule_kind,rule_value,"
                  "geography_id,active,notes,created_at) VALUES(?,?,?,?,?,?,1,?,?)",
                  (rule["key"], rule["label"], rule["territory"], "polygon",
                   (rule.get("boundary") or {}).get("geoid"),
                   geo_row["id"] if geo_row else None,
                   "Authoritative boundary from US Census TIGERweb.", utcnow()))
        for kind, values in (("city", rule.get("city_names", [])),
                             ("subdivision", rule.get("subdivision_patterns", [])),
                             ("zip", rule.get("zips", []))):
            for v in values:
                if not db.q1("SELECT id FROM exclusion_rules WHERE key=? AND rule_kind=? "
                             "AND rule_value=?", (rule["key"], kind, v)):
                    db.ex("INSERT INTO exclusion_rules(key,label,territory,rule_kind,"
                          "rule_value,active,created_at) VALUES(?,?,?,?,?,1,?)",
                          (rule["key"], rule["label"], rule["territory"], kind, v, utcnow()))


def stats() -> dict:
    rows = db.q("SELECT exclusion_reason, COUNT(*) n FROM properties WHERE excluded=1 "
                "GROUP BY exclusion_reason ORDER BY n DESC")
    return {"total": sum(r["n"] for r in rows),
            "by_reason": [{"reason": r["exclusion_reason"], "count": r["n"]} for r in rows]}
