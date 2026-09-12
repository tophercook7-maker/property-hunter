"""Natural-language search (spec 74).

Deterministic parsing first - it is faster, free and explainable. The AI is only
asked when the rules find nothing, and even then it may only return filters,
never properties. The interpreted filters are always shown back to the user.
"""
from __future__ import annotations

import json
import re

from . import ai

MONEY = r"\$?\s*([\d,]+(?:\.\d+)?)\s*(k|m)?"


def _money(text: str, unit: str | None) -> float:
    v = float(text.replace(",", ""))
    if unit == "k":
        v *= 1_000
    elif unit == "m":
        v *= 1_000_000
    return v


RULES: list[tuple[str, str]] = []


def parse(query: str) -> dict:
    """Return {filters, interpreted, unmatched} - never properties."""
    q = query.lower().strip()
    f: dict = {}
    said: list[str] = []

    m = re.search(rf"(?:under|below|less than|max|up to|cheaper than)\s*{MONEY}(?!\s*acre)", q)
    if m:
        f["max_value"] = _money(m.group(1), m.group(2))
        said.append(f"price or assessed value under ${f['max_value']:,.0f}")
    m = re.search(rf"(?:over|above|more than|at least|min(?:imum)?)\s*{MONEY}(?!\s*acre)", q)
    if m:
        f["min_value"] = _money(m.group(1), m.group(2))
        said.append(f"value over ${f['min_value']:,.0f}")

    m = re.search(r"(?:over|at least|more than)\s*([\d.]+)\s*acre", q)
    if m:
        f["min_acres"] = float(m.group(1))
        said.append(f"at least {f['min_acres']} acres")
    m = re.search(r"(?:under|less than|below)\s*([\d.]+)\s*acre", q)
    if m:
        f["max_acres"] = float(m.group(1))
        said.append(f"under {f['max_acres']} acres")

    if re.search(r"\b(lands?|lots?|acreage|dirt|vacant)\b", q):
        f["property_type"] = "lot"
        said.append("vacant land / lots only")
    if re.search(r"\b(houses?|homes?|rentals?|rent|tenants?|residential)\b", q):
        f["property_type"] = "house"
        said.append("houses only")
    if re.search(r"\b(commercial|business(?:es)?|shops?|storefronts?|retail|offices?)\b", q):
        f["property_type"] = "commercial"
        said.append("commercial parcels only")

    if re.search(r"\b(need(?:s|ing)? work|fixer|rough|run[- ]?down|distress|beat up|"
                 r"neglected|abandoned|vacant house)\b", q):
        f["has_distress"] = True
        said.append("must carry at least one distress signal")
    if re.search(r"\b(cash ?flow|cash-on-cash|rental)\b", q):
        f["sort"] = "rental"
        said.append("ranked by rental score")
    if re.search(r"\bstorage\b", q):
        f["sort"] = "storage"
        said.append("ranked by storage potential")
    if re.search(r"\b(snow ?cone|shaved ice|snowball|stand)\b", q):
        f["sort"] = "snowcone"
        said.append("ranked by snow-cone site score")
    if re.search(r"\b(workshop|3d ?print|shop space|maker)\b", q):
        f["sort"] = "workshop"
        said.append("ranked by workshop / 3D-printing fit")
    if re.search(r"\b(road frontage|frontage|on a highway|main road|busy road)\b", q):
        f["road_frontage"] = True
        said.append("must have a mapped road at the parcel")
    if re.search(r"\bhot springs\b(?!\s+village)", q):
        f["city"] = "Hot Springs"
        said.append("inside Hot Springs")
    if re.search(r"\bflood\b", q) and re.search(r"\b(no|not|without|avoid)\b", q):
        f["no_flood"] = True
        said.append("excluding mapped flood hazard areas")

    return {"query": query, "filters": f, "interpreted": said,
            "understood": bool(said),
            "note": ("Hot Springs Village and Diamondhead are always excluded, "
                     "whatever the search says.")}


def parse_with_ai(query: str) -> dict:
    """Fallback: let a local model map the sentence onto the SAME filter keys."""
    rules = parse(query)
    if rules["understood"]:
        return rules
    schema = {
        "max_value": "number or null", "min_value": "number or null",
        "min_acres": "number or null", "max_acres": "number or null",
        "property_type": "one of house|lot|commercial|multifamily or null",
        "has_distress": "true or null", "no_flood": "true or null",
        "road_frontage": "true or null", "city": "string or null",
        "sort": "one of overall|rental|land|storage|business|workshop|snowcone or null",
    }
    prompt = (f"Turn this property search into JSON filters.\n\n"
              f"Search: {query!r}\n\n"
              f"Allowed keys and value types:\n{json.dumps(schema, indent=2)}\n\n"
              f"Reply with ONLY a JSON object using those keys. Omit any key you are "
              f"not confident about. Never invent a property.")
    text, model = ai.ask(prompt, system="You convert search phrases into JSON filters. "
                                        "You reply with JSON and nothing else.",
                         max_tokens=250)
    if not text:
        return rules
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return rules
    try:
        parsed = {k: v for k, v in json.loads(m.group(0)).items()
                  if k in schema and v not in (None, "", False)}
    except ValueError:
        return rules
    rules["filters"].update(parsed)
    rules["interpreted"] = [f"{k} = {v}" for k, v in parsed.items()]
    rules["understood"] = bool(parsed)
    rules["via"] = f"local model {model}"
    return rules
