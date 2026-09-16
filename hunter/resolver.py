"""P6 — ARKANSAS ADDRESS → PARCEL RESOLVER.

A person types an address. This module turns it into ONE verified parcel identity only when explicit,
deterministic rules justify it, and otherwise says exactly why not. It never guesses, never fabricates a
parcel, and never turns a fuzzy match into a verified identity.

    input → normalize (rules version recorded) → candidates from the identity sources
          → explicit match rules → one of the match states → evidence + case integration

Identity sources, in order:
  LOCAL_ROLL       the app's own dated copy of the State roll and every other adapter (properties table,
                   address_norm + aliases). A local miss is NOT a statewide miss: the local copy only holds
                   parcels the hunt has already read.
  STATE_ROLL_LIVE  the Arkansas GIS Office parcel layer, read live through the existing ar_gis_parcels
                   adapter and the shared rate-limited ArcGIS client. This is the authoritative identity.

States:
  EXACT_MATCH               one parcel; confirmed on the live State roll; a locality component agreed
  STRONG_MATCH              one parcel; either confirmed live without any locality to corroborate, or found
                            only on the dated local copy because the live roll was unavailable
  AMBIGUOUS                 two or more parcels satisfy every rule → a person must choose
  MANUAL_REVIEW_REQUIRED    near-misses only (house number + street agree, something else disagrees) or the
                            local copy knows an identity the live roll no longer lists at that address
  NO_MATCH                  the sources answered and nothing satisfies house number + street name.
                            NOT "the property does not exist"
  SOURCE_UNAVAILABLE        the live roll failed and the local copy has nothing. NOT a NO_MATCH
  OUT_OF_SCOPE              not an Arkansas address (state token or ZIP outside Arkansas)
  INVALID_INPUT             no house number / street, or unusable text

Provenance: an automated resolution is evidence of origin AUTOMATED_SOURCE (the State roll said so);
a person's choice among candidates is MANUAL_VERIFICATION (HUMAN PROPERTY SELECTION). Nothing here is
an AI opinion, and the Bee has no access to this module.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from . import db, store
from .config import TERRITORIES
from .db import jdump, jload, utcnow
from .http import Blocked, arcgis_query
from .normalize import DIRECTIONS, ORDINALS, STREET_TYPES, UNIT_WORDS

RULES_VERSION = "p6.1"
MAX_INPUT = 200
LIVE_TIMEOUT = 12.0
LIVE_LIMIT = 60
AR_ZIP_RANGE = (71601, 72959)          # every Arkansas 5-digit ZIP falls in 716xx-729xx
STATES = ("EXACT_MATCH", "STRONG_MATCH", "AMBIGUOUS", "NO_MATCH", "SOURCE_UNAVAILABLE",
          "OUT_OF_SCOPE", "INVALID_INPUT", "MANUAL_REVIEW_REQUIRED")
RESOLVED_STATES = ("EXACT_MATCH", "STRONG_MATCH")
SOURCE_LOCAL = "LOCAL_ROLL"
SOURCE_LIVE = "STATE_ROLL_LIVE"
SOURCE_LABEL = {SOURCE_LOCAL: "Local copy of the State roll and prior source reads",
                SOURCE_LIVE: "Arkansas GIS Office parcel layer (live State roll)"}
ADAPTER = "ar_gis_parcels"
EVIDENCE_FIELD = "identity_resolution"
MANUAL_FIELD = "manual:identity"
MANUAL_SOURCE = "manual_verification"

US_STATES = {"AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS", "CA": "CALIFORNIA", "CO": "COLORADO",
             "CT": "CONNECTICUT", "DE": "DELAWARE", "FL": "FLORIDA", "GA": "GEORGIA", "HI": "HAWAII", "ID": "IDAHO",
             "IL": "ILLINOIS", "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY", "LA": "LOUISIANA",
             "ME": "MAINE", "MD": "MARYLAND", "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA",
             "MS": "MISSISSIPPI", "MO": "MISSOURI", "MT": "MONTANA", "NE": "NEBRASKA", "NV": "NEVADA",
             "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK", "NC": "NORTH CAROLINA",
             "ND": "NORTH DAKOTA", "OH": "OHIO", "OK": "OKLAHOMA", "OR": "OREGON", "PA": "PENNSYLVANIA",
             "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA", "TN": "TENNESSEE", "TX": "TEXAS",
             "UT": "UTAH", "VT": "VERMONT", "VA": "VIRGINIA", "WA": "WASHINGTON", "WV": "WEST VIRGINIA",
             "WI": "WISCONSIN", "WY": "WYOMING", "DC": "DISTRICT OF COLUMBIA"}
_STATE_BY_NAME = {v: k for k, v in US_STATES.items()}
_COUNTY_BY_FIPS = {t["county_fips"]: t for t in TERRITORIES}
_COUNTY_BY_NAME = {t["county"].upper(): t for t in TERRITORIES}


# ---------------------------------------------------------------------------------------------------------
# 1. Normalization — deterministic, versioned, keeps every component that could distinguish a parcel
# ---------------------------------------------------------------------------------------------------------
def _tok(s: str) -> list[str]:
    return [t for t in re.sub(r"[^A-Z0-9/#\- ]+", " ", s.upper()).split() if t]


def _parse_street(text: str) -> dict:
    """number / fraction / predir / name / suffix / postdir / unit from one street line. Never drops a
    component: what it cannot classify stays in the name."""
    out = {"number": None, "fraction": None, "predir": None, "name": None, "suffix": None, "postdir": None,
           "unit": None, "unit_word": None}
    toks = _tok(text)
    if not toks:
        return out
    # unit: '#4', 'APT 4', 'UNIT B', 'LOT 12' anywhere after the number; kept verbatim
    for i, t in enumerate(toks):
        low = t.lower()
        if i > 0 and low.startswith("#") and len(t) > 1:
            out["unit"], out["unit_word"] = t[1:], "#"
            toks = toks[:i] + toks[i + 1:]
            break
        if i > 0 and low in UNIT_WORDS and i + 1 < len(toks):
            out["unit"], out["unit_word"] = toks[i + 1], t
            toks = toks[:i] + toks[i + 2:]
            break
    m = re.match(r"^(\d+)([A-Z])?$", toks[0]) if toks else None
    if m:
        out["number"] = int(m.group(1))
        if m.group(2):
            out["unit"] = out["unit"] or m.group(2)
        toks = toks[1:]
        if toks and re.match(r"^\d/\d$", toks[0]):
            out["fraction"] = toks[0]
            toks = toks[1:]
    if not toks:
        return out
    if len(toks) > 1 and toks[0].lower() in DIRECTIONS:
        out["predir"] = DIRECTIONS[toks[0].lower()]
        toks = toks[1:]
    if len(toks) > 1 and toks[-1].lower() in DIRECTIONS:
        out["postdir"] = DIRECTIONS[toks[-1].lower()]
        toks = toks[:-1]
    if len(toks) > 1 and toks[-1].lower() in STREET_TYPES:
        out["suffix"] = STREET_TYPES[toks[-1].lower()]
        toks = toks[:-1]
        if len(toks) > 1 and toks[-1].lower() in DIRECTIONS and not out["postdir"]:
            # "LINCOLN N ST" style is rare; keep the direction as post-directional
            out["postdir"] = DIRECTIONS[toks[-1].lower()]
            toks = toks[:-1]
    name = [ORDINALS.get(t.lower(), t) for t in toks]
    out["name"] = " ".join(name) or None
    return out


def normalize(raw: Any) -> dict:
    """Deterministic. Records the original, the normalized components, the rules version, and any reason
    the input is unusable or outside Arkansas. Nothing is dropped that could tell two parcels apart."""
    n: dict[str, Any] = {"original": raw if isinstance(raw, str) else "", "rules_version": RULES_VERSION,
                         "valid": True, "reason": None, "in_scope": True, "state": None, "zip": None,
                         "city": None, "county_fips": None, "county": None}
    if not isinstance(raw, str):
        n.update(valid=False, reason="The address must be text.")
        return n
    text = " ".join(raw.replace(" ", " ").split())
    if not text:
        n.update(valid=False, reason="Enter an address.")
        return n
    if len(text) > MAX_INPUT:
        n.update(valid=False, reason=f"Addresses are at most {MAX_INPUT} characters.")
        return n
    if re.search(r"https?://|[;<>{}]|--|\bselect\b.*\bfrom\b|\bunion\b", text, re.I):
        n.update(valid=False, reason="That is not a street address.")
        return n
    up = text.upper()
    m = re.search(r"(?:^|[\s,])(\d{5})(?:-\d{4})?\s*$", up)
    if m:
        n["zip"] = m.group(1)
        up = up[:m.start()].rstrip(" ,")
    m = re.search(r"(?:^|[\s,])(" + "|".join(sorted(map(re.escape, list(US_STATES) + list(_STATE_BY_NAME)), key=len, reverse=True)) + r")\.?\s*$", up)
    if m:
        tok = m.group(1)
        n["state"] = tok if tok in US_STATES else _STATE_BY_NAME[tok]
        up = up[:m.start()].rstrip(" ,")
    parts = [p.strip() for p in up.split(",") if p.strip()]
    street_line = parts[0] if parts else ""
    rest = parts[1:]
    if len(parts) == 1:
        # no commas: the street line ends at the suffix (+ directional / unit); what follows is the city
        toks = _tok(street_line)
        i = 1 if toks and re.match(r"^\d+[A-Z]?$", toks[0]) else 0
        if i < len(toks) and re.match(r"^\d/\d$", toks[i]):
            i += 1
        if i + 1 < len(toks) and toks[i].lower() in DIRECTIONS:
            i += 1
        for k in range(i + 1, len(toks)):
            if toks[k].lower() in STREET_TYPES:
                end = k + 1
                if end < len(toks) and toks[end].lower() in DIRECTIONS:
                    end += 1
                if end < len(toks) and toks[end].lower() in UNIT_WORDS:
                    end += 2
                elif end < len(toks) and toks[end].startswith("#"):
                    end += 1
                if end < len(toks):
                    street_line, rest = " ".join(toks[:end]), [" ".join(toks[end:])]
                break
    city_parts = []
    for part in rest:
        cm = re.match(r"^(.+?)\s+(?:COUNTY|CO)$", part.strip())
        if cm and cm.group(1).strip() in _COUNTY_BY_NAME:
            n["county"] = _COUNTY_BY_NAME[cm.group(1).strip()]["county"]
            n["county_fips"] = _COUNTY_BY_NAME[cm.group(1).strip()]["county_fips"]
        else:
            city_parts.append(part)
    if city_parts:
        n["city"] = re.sub(r"[^A-Z0-9 ]+", " ", " ".join(city_parts)).strip() or None
    comp = _parse_street(street_line)
    # route-style names: HIGHWAY 7 / HWY 7 / ROUTE 3 are the same road; canonicalize the route word only
    if comp["name"]:
        nt = comp["name"].split()
        if len(nt) > 1 and nt[0].lower() in STREET_TYPES and re.match(r"^\d", nt[1]):
            nt[0] = STREET_TYPES[nt[0].lower()]
            comp["name"] = " ".join(nt)
    n.update(comp)
    n["normalized"] = " ".join(x for x in [
        f"{comp['number']}" if comp["number"] is not None else None, comp["fraction"], comp["predir"], comp["name"],
        comp["suffix"], comp["postdir"], f"{comp['unit_word'].upper()} {comp['unit']}" if comp["unit"] and comp["unit_word"] else (f"UNIT {comp['unit']}" if comp["unit"] else None)] if x)
    if n["city"]:
        n["normalized"] += f", {n['city']}"
    if n["zip"]:
        n["normalized"] += f" {n['zip']}"
    if n["state"] and n["state"] != "AR":
        n.update(in_scope=False, reason=f"{US_STATES[n['state']].title()} is outside Arkansas. Property Hunter resolves Arkansas parcels only.")
        return n
    if n["zip"] and not (AR_ZIP_RANGE[0] <= int(n["zip"]) <= AR_ZIP_RANGE[1]):
        n.update(in_scope=False, reason=f"ZIP {n['zip']} is not an Arkansas ZIP code. Property Hunter resolves Arkansas parcels only.")
        return n
    if comp["number"] is None:
        n.update(valid=False, reason="Start with the house number (for example 302 Lincoln St, Hot Springs). Route and highway addresses need a number too.")
        return n
    if not comp["name"]:
        n.update(valid=False, reason="Add the street name after the house number.")
        return n
    return n


# ---------------------------------------------------------------------------------------------------------
# 2. Candidates — from the local copy first, then the live State roll through the existing adapter
# ---------------------------------------------------------------------------------------------------------
def _county_name(fips: str | None) -> str | None:
    t = _COUNTY_BY_FIPS.get(fips or "")
    return t["county"] if t else None


def _candidate_from_row(row: dict) -> dict:
    comp = _parse_street(row.get("address") or "")
    return {"property_id": row["id"], "parcel_id": row.get("parcel_id"), "rpid": row.get("rpid"),
            "county_fips": row.get("county_fips"), "county": _county_name(row.get("county_fips")),
            "jurisdiction": row.get("territory"), "situs": row.get("address"), "city": (row.get("city") or "").upper() or None,
            "zip": row.get("zip"), "lat": row.get("lat"), "lon": row.get("lon"), "acreage": row.get("acreage"),
            "components": comp, "sources": [SOURCE_LOCAL], "source_date": row.get("last_seen"),
            "source_url": None, "historical": False}


def _local_candidates(n: dict) -> list[dict]:
    first = n["name"].split()[0]
    rows = db.q("SELECT id, county_fips, parcel_id, rpid, address, city, zip, lat, lon, acreage, last_seen, territory "
                "FROM properties WHERE address_norm LIKE ? AND address IS NOT NULL LIMIT 200",
                (f"{n['number']} %{first}%",))
    return [_candidate_from_row(dict(r)) for r in rows]


def _live_query(n: dict) -> dict:
    """The only network call. Same layer, fields and client as the ar_gis_parcels adapter."""
    from .sources.ar_parcels import FIELDS, LAYER, SERVICE
    service = os.environ.get("PH_RESOLVER_LIVE_SERVICE") or SERVICE     # outage drills only; same client, same layer
    first = re.sub(r"[^A-Z0-9]", "", n["name"].split()[0])
    variants = {first} | {k.upper() for k, v in ORDINALS.items() if v == first}     # FIRST is spelled 1ST on the roll
    name_clause = " OR ".join(f"UPPER(pstrnam) LIKE '{v}%'" for v in sorted(variants) if v)
    where = f"adrnum={int(n['number'])} AND ({name_clause})"
    if n.get("county_fips"):
        where += f" AND countyfips='{n['county_fips']}'"
    return arcgis_query(service, LAYER, where=where, out_fields=FIELDS, geometry=True,
                        result_record_count=LIVE_LIMIT, timeout=LIVE_TIMEOUT)


def _suffix(raw) -> str | None:
    """Roll suffixes are sometimes truncated ('STRE' for STREET): map a 3+ letter prefix of one known word."""
    v = (raw or "").strip().lower()
    if not v:
        return None
    if v in STREET_TYPES:
        return STREET_TYPES[v]
    hits = {STREET_TYPES[k] for k in STREET_TYPES if len(v) >= 3 and k.startswith(v)}
    return hits.pop() if len(hits) == 1 else v.upper()


def _failure_category(exc: Exception) -> str:
    s = str(exc).lower()
    if isinstance(exc, Blocked):
        return "ACCESS_RESTRICTED"
    if "timed out" in s or "timeout" in s:
        return "TIMEOUT"
    if "arcgis error" in s:
        return "SERVICE_ERROR"
    if "connection" in s or "name or service not known" in s or "nodename" in s or "network" in s:
        return "NETWORK"
    return "UNKNOWN_FAILURE"


def _live_candidates(n: dict) -> tuple[list[dict], dict]:
    """Read the live roll; ingest only rows whose house number and street name agree with the input,
    through the adapter's own record builder and the canonical ingest path (AUTOMATED_SOURCE evidence)."""
    from .sources import get_source, register_all
    register_all()
    src = get_source(ADAPTER)
    status = {"source": SOURCE_LIVE, "label": SOURCE_LABEL[SOURCE_LIVE], "adapter": ADAPTER, "status": "OK",
              "read_at": utcnow(), "failure_category": None, "detail": None, "rows": 0}
    try:
        data = _live_query(n)
    except Exception as exc:
        status.update(status="UNAVAILABLE", failure_category=_failure_category(exc), detail=str(exc)[:300])
        return [], status
    feats = (data or {}).get("features") or []
    status["rows"] = len(feats)
    if len(feats) >= LIVE_LIMIT:
        status["detail"] = f"the live roll returned the first {LIVE_LIMIT} rows only"
    out = []
    for f in feats:
        a = f.get("attributes") or {}
        terr = _COUNTY_BY_FIPS.get(str(a.get("countyfips") or ""))
        if not terr:
            continue
        comp = {"number": a.get("adrnum"), "fraction": None,
                "predir": DIRECTIONS.get((a.get("predir") or "").lower()) or ((a.get("predir") or "").upper() or None),
                "name": " ".join(ORDINALS.get(t.lower(), t) for t in _tok(a.get("pstrnam") or "")) or None,
                "suffix": _suffix(a.get("pstrtype")),
                "postdir": DIRECTIONS.get((a.get("psufdir") or "").lower()) or ((a.get("psufdir") or "").upper() or None),
                "unit": None, "unit_word": None}
        if comp["number"] != n["number"] or comp["name"] != n["name"]:
            continue
        rec = src._to_record(f, terr)
        if not rec:
            continue
        pid, _action, _changes = store.ingest(rec)
        row = store.get_property(pid) or {}
        c = _candidate_from_row(row)
        ev0 = rec.evidence[0] if rec.evidence else {}
        c.update(components=comp, sources=[SOURCE_LIVE], source_date=ev0.get("effective_date") or status["read_at"],
                 source_url=ev0.get("source_url"), read_at=status["read_at"],
                 city=((a.get("adrcity") or row.get("city") or "").upper() or None), zip=(str(a.get("adrzip5")) if a.get("adrzip5") else row.get("zip")))
        out.append(c)
    return out, status


# ---------------------------------------------------------------------------------------------------------
# 3. Match rules — explicit, documented, no score
# ---------------------------------------------------------------------------------------------------------
RULES = (
    ("HOUSE_NUMBER", "House number must be identical (required)"),
    ("STREET_NAME", "Street name must be identical after normalization (required)"),
    ("SUFFIX", "Street suffix must agree when both sides state one (ST vs AVE is a different street)"),
    ("DIRECTIONAL", "Pre/post directional must agree when both sides state one"),
    ("CITY", "City must agree when the input names one and the source records one"),
    ("ZIP", "ZIP must agree when the input has one and the source records one"),
    ("COUNTY", "County must agree when the input names one"),
    ("UNIT", "The State roll does not carry unit numbers; a unit in the input is kept and reported, never used to pick a parcel"),
    ("SOURCE_AGREEMENT", "The same parcel found on both the local copy and the live roll corroborates the identity"),
    ("PARCEL_IDENTITY", "Candidates are grouped by county + parcel id; two rows of the same parcel are one candidate"),
)
AGREE, MISMATCH, NOT_STATED = "AGREE", "MISMATCH", "NOT_STATED"


def _rule(rule, a, b, *, required=False):
    if a in (None, "") or b in (None, ""):
        return {"rule": rule, "result": MISMATCH if required else NOT_STATED, "input": a, "candidate": b}
    return {"rule": rule, "result": AGREE if str(a).upper() == str(b).upper() else MISMATCH, "input": a, "candidate": b}


def apply_rules(n: dict, c: dict) -> list[dict]:
    comp = c["components"]
    dir_in = " ".join(x for x in (n.get("predir"), n.get("postdir")) if x) or None
    dir_c = " ".join(x for x in (comp.get("predir"), comp.get("postdir")) if x) or None
    checks = [_rule("HOUSE_NUMBER", n["number"], comp.get("number"), required=True),
              _rule("STREET_NAME", n["name"], comp.get("name"), required=True),
              _rule("SUFFIX", n.get("suffix"), comp.get("suffix")),
              _rule("DIRECTIONAL", dir_in, dir_c),
              _rule("CITY", n.get("city"), c.get("city")),
              _rule("ZIP", n.get("zip"), c.get("zip")),
              _rule("COUNTY", n.get("county_fips"), c.get("county_fips")),
              {"rule": "UNIT", "result": NOT_STATED, "input": n.get("unit"), "candidate": None},
              {"rule": "SOURCE_AGREEMENT", "result": AGREE if len(c.get("sources") or []) > 1 else NOT_STATED,
               "input": None, "candidate": ",".join(c.get("sources") or [])}]
    return checks


def _classify(checks: list[dict]) -> str:
    req = [k for k in checks if k["rule"] in ("HOUSE_NUMBER", "STREET_NAME")]
    if any(k["result"] != AGREE for k in req):
        return "REJECTED"
    if any(k["result"] == MISMATCH for k in checks):
        return "NEAR"
    return "QUALIFYING"


def _merge(local: list[dict], live: list[dict]) -> list[dict]:
    by_key: dict[tuple, dict] = {}
    for c in local + live:
        key = (c.get("county_fips"), c.get("parcel_id") or f"pid:{c['property_id']}")
        if key in by_key:
            m = by_key[key]
            m["sources"] = sorted(set(m["sources"]) | set(c["sources"]))
            if SOURCE_LIVE in c["sources"]:
                for k in ("components", "situs", "city", "zip", "lat", "lon", "acreage", "source_date", "source_url", "read_at", "rpid"):
                    if c.get(k) not in (None, ""):
                        m[k] = c[k]
        else:
            by_key[key] = dict(c)
    return list(by_key.values())


# ---------------------------------------------------------------------------------------------------------
# 4. Resolve
# ---------------------------------------------------------------------------------------------------------
def _public_candidate(c: dict) -> dict:
    return {k: c.get(k) for k in ("property_id", "parcel_id", "rpid", "county_fips", "county", "jurisdiction", "situs",
                                  "city", "zip", "lat", "lon", "acreage", "sources", "source_date", "source_url",
                                  "historical", "class", "match_reasons", "mismatches", "checks")} | {
        "normalized_situs": " ".join(x for x in (str(c["components"].get("number") or ""), c["components"].get("fraction"), c["components"].get("predir"),
                                                  c["components"].get("name"), c["components"].get("suffix"), c["components"].get("postdir")) if x)}


def _identity(c: dict, *, verified_by: str, search_id: int | None, actor: str | None = None) -> dict:
    return {"property_id": c["property_id"], "parcel_id": c.get("parcel_id"), "rpid": c.get("rpid"),
            "county_fips": c.get("county_fips"), "county": c.get("county"), "situs": c.get("situs"),
            "city": c.get("city"), "zip": c.get("zip"), "lat": c.get("lat"), "lon": c.get("lon"),
            "acreage": c.get("acreage"), "verified": True, "verified_by": verified_by, "actor": actor,
            "source": SOURCE_LIVE if SOURCE_LIVE in c.get("sources", []) else SOURCE_LOCAL,
            "source_label": SOURCE_LABEL[SOURCE_LIVE if SOURCE_LIVE in c.get("sources", []) else SOURCE_LOCAL],
            "source_date": c.get("source_date"), "read_at": c.get("read_at") or utcnow(), "search_id": search_id}


def _record_automated(search_id: int, n: dict, c: dict, state: str) -> int | None:
    """AUTOMATED_SOURCE evidence: the State roll (live, or its dated local copy) places this normalized address
    on this parcel. One row per distinct resolution value; a repeat of the same answer reuses the row."""
    pid = c["property_id"]
    value = f"{n['normalized']} = parcel {c.get('parcel_id') or '?'} ({c.get('county') or c.get('county_fips')} County)"
    prev = db.q1("SELECT id FROM evidence WHERE property_id=? AND field=? AND value=? AND superseded_by IS NULL ORDER BY id DESC LIMIT 1",
                 (pid, EVIDENCE_FIELD, value))
    if prev:
        return prev["id"]
    live = SOURCE_LIVE in c.get("sources", [])
    rules = "; ".join(f"{k['rule']}={k['result']}" for k in c["checks"])
    store.store_evidence(pid, [{
        "field": EVIDENCE_FIELD, "value": value, "evidence_type": "OBSERVATION", "confidence": "HIGH" if state == "EXACT_MATCH" else "MEDIUM",
        "origin": "AUTOMATED_SOURCE", "source": ADAPTER,
        "source_name": SOURCE_LABEL[SOURCE_LIVE] if live else SOURCE_LABEL[SOURCE_LOCAL],
        "source_url": c.get("source_url"), "effective_date": (c.get("source_date") or "")[:10] or None,
        "raw_ref": f"ADDRESS RESOLUTION search:{search_id}; {state}; rules {RULES_VERSION}: {rules}"}])
    e = db.q1("SELECT id FROM evidence WHERE property_id=? AND field=? ORDER BY id DESC LIMIT 1", (pid, EVIDENCE_FIELD))
    return e["id"] if e else None


def _explain(state, n, sources, cands, qualifying, near) -> dict:
    checked = [f"{s['label']}: {s['status'].lower()}" + (f" ({s['failure_category']})" if s.get("failure_category") else "") for s in sources]
    ex = {"normalized": n["normalized"], "rules_version": RULES_VERSION, "sources_checked": sources,
          "county_assumption": (f"{n['county']} County (named in the input)" if n.get("county") else "no county assumed; every Arkansas county was searched"),
          "unavailable": [s["label"] for s in sources if s["status"] != "OK"], "next_action": None, "summary": None}
    if state == "EXACT_MATCH":
        ex["summary"] = "One parcel satisfies every rule and the live State roll confirms it."
    elif state == "STRONG_MATCH":
        live_ok = any(s["source"] == SOURCE_LIVE and s["status"] == "OK" for s in sources)
        ex["summary"] = ("One parcel satisfies every rule on the live State roll; the input carried no city or ZIP to corroborate it." if live_ok
                         else "One parcel satisfies every rule on the dated local copy of the State roll; the live roll could not be re-read.")
        ex["next_action"] = None if live_ok else "Search again when the State parcel service answers to confirm against the live roll."
    elif state == "AMBIGUOUS":
        ex["summary"] = f"{len(qualifying)} different parcels satisfy every rule. Choose the one you mean; your choice is recorded as a HUMAN PROPERTY SELECTION."
        ex["next_action"] = "Add the city or ZIP, or pick a candidate below."
    elif state == "MANUAL_REVIEW_REQUIRED":
        ex["summary"] = ("The house number and street agree on some records but another component disagrees, or the local copy holds an identity the live roll no longer lists at this address. Nothing was resolved.")
        ex["next_action"] = "Review the near-miss candidates; open the property file of the one you can verify, or search again with the exact suffix, directional, city and ZIP."
    elif state == "NO_MATCH":
        ex["summary"] = "The sources answered and no parcel has this house number and street name. That is not proof the property does not exist."
        ex["next_action"] = "Check the spelling and the road name the county uses (roll names can differ from postal names), try the county Assessor, or look up the parcel by number."
    elif state == "SOURCE_UNAVAILABLE":
        ex["summary"] = "The live State roll did not answer and the local copy has nothing for this address. Nothing was resolved and nothing was ruled out."
        ex["next_action"] = "Try again shortly. If the State parcel service stays down, use the county Assessor's site directly."
    ex["checked"] = checked
    return ex


def resolve(address: Any, *, license_id: int = 0, session_id: int = 0, actor: str = "user",
            live: bool = True) -> dict:
    """The resolver. Returns the full resolution and records it in the private search history."""
    t0 = time.perf_counter()
    n = normalize(address)
    sources: list[dict] = []
    cands: list[dict] = []
    state = None
    identity = None
    evidence_id = None
    if not n["valid"]:
        state = "INVALID_INPUT"
    elif not n["in_scope"]:
        state = "OUT_OF_SCOPE"
    else:
        local = _local_candidates(n)
        sources.append({"source": SOURCE_LOCAL, "label": SOURCE_LABEL[SOURCE_LOCAL], "adapter": None, "status": "OK",
                        "read_at": utcnow(), "failure_category": None, "detail": f"{len(local)} rows share the house number and street token", "rows": len(local)})
        if live:
            live_c, live_status = _live_candidates(n)
        else:
            live_c, live_status = [], {"source": SOURCE_LIVE, "label": SOURCE_LABEL[SOURCE_LIVE], "adapter": ADAPTER, "status": "UNAVAILABLE",
                                       "read_at": utcnow(), "failure_category": "DISABLED", "detail": "live read disabled for this request", "rows": 0}
        sources.append(live_status)
        live_ok = live_status["status"] == "OK"
        cands = _merge(local, live_c)
        for c in cands:
            c["checks"] = apply_rules(n, c)
            c["class"] = _classify(c["checks"])
            c["match_reasons"] = [k["rule"] for k in c["checks"] if k["result"] == AGREE]
            c["mismatches"] = [f"{k['rule']}: input {k['input']!r} vs source {k['candidate']!r}" for k in c["checks"] if k["result"] == MISMATCH]
            # local-only identity while the live roll answered = the roll no longer lists this parcel at this address
            c["historical"] = bool(live_ok and SOURCE_LIVE not in c["sources"] and c["class"] != "REJECTED")
            if c["historical"]:
                c["mismatches"].append("HISTORICAL: on the local copy only; not on the current live State roll at this address")
        cands = [c for c in cands if c["class"] != "REJECTED"]
        qualifying = [c for c in cands if c["class"] == "QUALIFYING" and not c["historical"]]
        near = [c for c in cands if c not in qualifying]
        if len(qualifying) == 1:
            c = qualifying[0]
            locality = any(k["rule"] in ("CITY", "ZIP") and k["result"] == AGREE for k in c["checks"])
            state = "EXACT_MATCH" if (SOURCE_LIVE in c["sources"] and locality) else "STRONG_MATCH"
        elif len(qualifying) > 1:
            state = "AMBIGUOUS"
        elif near:
            state = "MANUAL_REVIEW_REQUIRED"
        elif live_ok:
            state = "NO_MATCH"
        else:
            state = "SOURCE_UNAVAILABLE"
    latency = int((time.perf_counter() - t0) * 1000)
    cur = db.ex("INSERT INTO address_searches(license_id, session_id, actor, input_original, input_normalized, normalized_json, state, "
                "candidate_count, candidates_json, selected_property_id, selected_by, sources_json, latency_ms, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (license_id, session_id, actor[:40], n["original"][:MAX_INPUT], n.get("normalized"), jdump(n), state, len(cands),
                 jdump([_public_candidate(c) for c in cands]), None, None, jdump(sources), latency, utcnow()))
    sid = cur.lastrowid
    if state in RESOLVED_STATES:
        c = qualifying[0]
        evidence_id = _record_automated(sid, n, c, state)
        identity = _identity(c, verified_by="AUTOMATED_SOURCE", search_id=sid) | {"evidence_id": evidence_id}
        db.ex("UPDATE address_searches SET selected_property_id=?, selected_by=?, identity_json=? WHERE id=?",
              (c["property_id"], "AUTOMATED_SOURCE", jdump(identity), sid))
    return view(sid)


def view(search_id: int, license_id: int | None = None) -> dict | None:
    r = db.q1("SELECT * FROM address_searches WHERE id=?", (search_id,))
    if not r or (license_id is not None and r["license_id"] != license_id):
        return None
    n = jload(r["normalized_json"], {}) or {}
    cands = jload(r["candidates_json"], []) or []
    sources = jload(r["sources_json"], []) or []
    st = r["state"]
    qualifying = [c for c in cands if c.get("class") == "QUALIFYING" and not c.get("historical")]
    near = [c for c in cands if c not in qualifying]
    ex = _explain(st, n, sources, cands, qualifying, near) if st not in ("INVALID_INPUT", "OUT_OF_SCOPE") else {
        "normalized": n.get("normalized"), "rules_version": RULES_VERSION, "sources_checked": [], "summary": n.get("reason"), "next_action": None, "unavailable": []}
    from . import cases
    case = None
    if r["selected_property_id"]:
        c = cases.case_for_property(r["selected_property_id"])
        case = {"id": c["id"], "status": c["status"], "attached": bool(r["case_id"]) and r["case_id"] == c["id"]} if c else None
    wk = db.q1("SELECT id, status FROM workups WHERE search_id=? ORDER BY id DESC LIMIT 1", (r["id"],))
    return {"search_id": r["id"], "state": st, "workup": ({"id": wk["id"], "status": wk["status"]} if wk else None), "state_label": st.replace("_", " "), "input": {"original": r["input_original"], "normalized": r["input_normalized"]},
            "normalized": n, "candidates": cands, "candidate_count": len(cands), "qualifying": len(qualifying),
            "identity": jload(r["identity_json"], None), "selected_property_id": r["selected_property_id"], "selected_by": r["selected_by"],
            "explanation": ex, "sources": sources, "latency_ms": r["latency_ms"], "created_at": r["created_at"], "actor": r["actor"],
            "case": case, "resolved": st in RESOLVED_STATES or bool(r["selected_property_id"])}


# ---------------------------------------------------------------------------------------------------------
# 5. Human selection among candidates — MANUAL_VERIFICATION, never a system assertion
# ---------------------------------------------------------------------------------------------------------
def select(search_id: int, property_id: int, *, actor: str, reason: str = "", reference: str = "",
           license_id: int | None = None) -> dict:
    r = db.q1("SELECT * FROM address_searches WHERE id=?", (search_id,))
    if not r or (license_id is not None and r["license_id"] != license_id):
        raise KeyError("no such search")
    if r["state"] not in ("AMBIGUOUS", "MANUAL_REVIEW_REQUIRED"):
        raise ValueError(f"a {r['state'].replace('_', ' ')} result has nothing for a person to choose")
    if r["selected_property_id"]:
        raise ValueError("a property was already selected for this search; search again to choose differently")
    actor = (actor or "").strip()[:40]
    if not actor:
        raise ValueError("name the person making the selection")
    reason = (reason or "").strip()
    if len(reason) < 4:
        raise ValueError("give the reason you know this is the parcel (what you checked, or a reference)")
    cands = jload(r["candidates_json"], []) or []
    c = next((x for x in cands if x.get("property_id") == property_id), None)
    if not c:
        raise ValueError("choose one of the listed candidates")
    n = jload(r["normalized_json"], {}) or {}
    date = utcnow()
    value = f"{n.get('normalized')} = parcel {c.get('parcel_id') or '?'} ({c.get('county') or c.get('county_fips')} County) — chosen by {actor}"
    store.store_evidence(property_id, [{
        "field": MANUAL_FIELD, "value": value, "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "origin": "MANUAL_VERIFICATION",
        "source": MANUAL_SOURCE, "source_name": f"HUMAN PROPERTY SELECTION by {actor} — MANUAL VERIFICATION", "source_url": None,
        "effective_date": date[:10],
        "raw_ref": f"HUMAN PROPERTY SELECTION; search:{search_id}; state {r['state']}; candidate parcel {c.get('parcel_id')} property:{property_id}; "
                   f"reason: {reason}" + (f"; reference: {reference.strip()}" if reference.strip() else "")}])
    e = db.q1("SELECT id FROM evidence WHERE property_id=? AND field=? ORDER BY id DESC LIMIT 1", (property_id, MANUAL_FIELD))
    identity = _identity(dict(c, components=None), verified_by="MANUAL_VERIFICATION", search_id=search_id, actor=actor) | {
        "evidence_id": e["id"] if e else None, "reason": reason, "reference": reference.strip() or None, "selected_at": date}
    identity["source"] = "HUMAN_PROPERTY_SELECTION"
    identity["source_label"] = f"Human property selection by {actor} (candidate from {', '.join(c.get('sources') or [])})"
    db.ex("UPDATE address_searches SET selected_property_id=?, selected_by=?, identity_json=?, selected_at=?, selected_actor=? WHERE id=?",
          (property_id, "MANUAL_VERIFICATION", jdump(identity), date, actor, search_id))
    return view(search_id)


# ---------------------------------------------------------------------------------------------------------
# 6. Investigation — reuse the one case per property; record where it came from
# ---------------------------------------------------------------------------------------------------------
def open_investigation(search_id: int, *, actor: str, license_id: int | None = None) -> dict:
    from . import cases
    r = db.q1("SELECT * FROM address_searches WHERE id=?", (search_id,))
    if not r or (license_id is not None and r["license_id"] != license_id):
        raise KeyError("no such search")
    pid = r["selected_property_id"]
    if not pid:
        raise ValueError("nothing is resolved for this search; a parcel must be verified or chosen first")
    actor = (actor or "user").strip()[:40] or "user"
    existing = cases.case_for_property(pid)
    sig = {"event": "MANUAL", "cls": "MANUAL", "label": "Opened from address search", "src": actor,
           "evidence_ref": f"search:{search_id}", "status": "OBSERVED", "kind": "observed"}
    cases.open_or_create(pid, sig, actor)
    c = cases.case_for_property(pid)
    ref = f"search:{search_id}"
    if not db.q1("SELECT 1 FROM investigation_events WHERE case_id=? AND cls='INVESTIGATION OPENED FROM ADDRESS SEARCH' AND detail LIKE ?", (c["id"], f"{ref};%")):
        how = "HUMAN PROPERTY SELECTION (MANUAL VERIFICATION)" if r["selected_by"] == "MANUAL_VERIFICATION" else f"{r['state'].replace('_', ' ')} (AUTOMATED_SOURCE)"
        cases._event(c["id"], "INVESTIGATION OPENED FROM ADDRESS SEARCH",
                     ("Investigation attached to" if existing else "Investigation opened from") + f" address search by {actor}",
                     f"{ref}; searched {r['input_original']!r} → {r['input_normalized']}; identity by {how}; property:{pid}", f"property:{pid}", actor)
    db.ex("UPDATE address_searches SET case_id=? WHERE id=?", (c["id"], search_id))
    return {"case_id": c["id"], "created": not existing, "property_id": pid, "search": view(search_id)}


def history(license_id: int, limit: int = 50) -> list[dict]:
    """This license's own searches only. Never exported, never public."""
    rows = db.q("SELECT id, actor, input_original, input_normalized, state, candidate_count, selected_property_id, selected_by, case_id, latency_ms, created_at "
                "FROM address_searches WHERE license_id=? ORDER BY id DESC LIMIT ?", (license_id, max(1, min(200, limit))))
    return [dict(r) for r in rows]
