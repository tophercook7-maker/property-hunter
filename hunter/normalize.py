"""Address / owner / parcel normalization.

Different sources spell the same place differently. Everything that gets
compared or matched goes through here first (spec 54).
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[.,#]+")

STREET_TYPES = {
    "street": "ST", "st": "ST", "str": "ST",
    "avenue": "AVE", "ave": "AVE", "av": "AVE",
    "road": "RD", "rd": "RD",
    "drive": "DR", "dr": "DR",
    "lane": "LN", "ln": "LN",
    "court": "CT", "ct": "CT",
    "circle": "CIR", "cir": "CIR",
    "boulevard": "BLVD", "blvd": "BLVD",
    "place": "PL", "pl": "PL",
    "terrace": "TER", "ter": "TER", "terr": "TER",
    "trail": "TRL", "trl": "TRL", "tr": "TRL",
    "highway": "HWY", "hwy": "HWY",
    "parkway": "PKWY", "pkwy": "PKWY",
    "way": "WAY", "loop": "LOOP", "path": "PATH", "pass": "PASS",
    "square": "SQ", "sq": "SQ",
    "point": "PT", "pt": "PT",
    "ridge": "RDG", "rdg": "RDG",
    "crossing": "XING", "xing": "XING",
    "extension": "EXT", "ext": "EXT",
    "expressway": "EXPY", "expy": "EXPY",
    "cove": "CV", "cv": "CV",
    "run": "RUN", "row": "ROW", "bend": "BND", "bnd": "BND",
}

DIRECTIONS = {
    "north": "N", "n": "N", "south": "S", "s": "S",
    "east": "E", "e": "E", "west": "W", "w": "W",
    "northeast": "NE", "ne": "NE", "northwest": "NW", "nw": "NW",
    "southeast": "SE", "se": "SE", "southwest": "SW", "sw": "SW",
}

UNIT_WORDS = {"apt", "unit", "ste", "suite", "lot", "trlr", "bldg", "#"}


def squash(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", _PUNCT.sub(" ", str(text))).strip().upper()


def normalize_address(raw: str | None) -> str:
    """Return a comparable form: '212 LEISURE TER', '2748 MALVERN AVE'."""
    s = squash(raw)
    if not s:
        return ""
    parts = []
    for tok in s.split(" "):
        low = tok.lower()
        if low in UNIT_WORDS:
            break
        if low in STREET_TYPES:
            parts.append(STREET_TYPES[low])
        elif low in DIRECTIONS and (parts and len(parts) > 1 or not parts):
            parts.append(DIRECTIONS[low])
        else:
            parts.append(tok)
    return " ".join(parts).strip()


def address_number(raw: str | None) -> str:
    s = normalize_address(raw)
    m = re.match(r"^(\d+)\b", s)
    return m.group(1) if m else ""


def street_only(raw: str | None) -> str:
    s = normalize_address(raw)
    return re.sub(r"^\d+\s+", "", s).strip()


def normalize_owner(raw: str | None) -> str:
    s = squash(raw)
    s = re.sub(r"\b(LLC|L L C|INC|CO|CORP|TRUST|TRUSTEE|ET AL|ETAL|JR|SR|III|II|IV)\b", " ", s)
    return _WS.sub(" ", s).strip()


def normalize_parcel(raw: str | None) -> str:
    """Strip formatting so 100-04807-000 == 10004807000."""
    if not raw:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(raw)).upper()


def normalize_subdivision(raw: str | None) -> str:
    return squash(raw)


def slug(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def title_case(text: str | None) -> str:
    """'212  LEISURE TER' -> '212 Leisure Ter' for display."""
    s = _WS.sub(" ", (text or "").strip())
    if not s:
        return ""
    out = []
    for w in s.split(" "):
        if len(w) <= 2 and w.isalpha() and w.upper() in DIRECTIONS.values():
            out.append(w.upper())
        elif w.isdigit():
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def fuzzy_ratio(a: str, b: str) -> float:
    """Cheap token-overlap similarity in [0,1] - no external deps."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
