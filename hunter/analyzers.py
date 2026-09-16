"""Interpretation layers: why is it cheap, deal or trap, what would you do,
what should I do next, and the side-by-side comparison (spec 26/27/33/34/39/59).

Deterministic reasoning does the ranking; AI only writes the English.
"""
from __future__ import annotations

from . import ai, db, finance, scoring, store
from .config import FINANCE_DEFAULTS, LEGAL_DISCLAIMER

# ------------------------------------------------------- why is this cheap --

CHEAP_REASONS = [
    ("condition", "The building is probably rough",
     lambda p, s: "low_improvement_value" in s or "building_worth_less_than_dirt" in s),
    ("title", "Something may be wrong with the title",
     lambda p, s: "estate_owner" in s or "unknown_owner" in s or "trust_owner" in s),
    ("access", "It may be hard or impossible to legally get to",
     lambda p, s: "possible_no_access" in s),
    ("flood", "It floods, or FEMA says it might",
     lambda p, s: "flood_zone" in s),
    ("lot_size", "The lot may be too small or oddly shaped to build on",
     lambda p, s: "tiny_lot" in s),
    ("location", "The location is working against it",
     lambda p, s: (p.get("city") or "").lower() in ("", "unincorporated", "rural")),
    ("zoning", "Zoning may not allow what a buyer would want to do with it",
     lambda p, s: not p.get("zoning")),
    ("taxes", "Back taxes may be riding on it",
     lambda p, s: not p.get("tax_status")),
    ("utilities", "Utilities may not be at the road",
     lambda p, s: (p.get("city") or "").lower() in ("", "unincorporated", "rural")
     and (p.get("imp_value") or 0) < 500),
    ("marketability", "Nobody has been trying very hard to sell it",
     lambda p, s: "stale_assessment" in s),
]


def why_cheap(prop: dict) -> dict:
    sig = {s["key"] for s in (prop.get("distress") or [])}
    hits = [{"key": k, "reason": label} for k, label, test in CHEAP_REASONS
            if test(prop, sig)]
    total = prop.get("total_value") or 0
    is_cheap = bool(total and total < 40000) or bool(prop.get("imp_value") == 0)
    if not hits:
        hits = [{"key": "unknown",
                 "reason": "Nothing in what we hold explains a low price. "
                           "That usually means we are missing something."}]
    biggest = "title" if any(h["key"] == "title" for h in hits) else hits[0]["key"]
    return {
        "looks_cheap": is_cheap,
        "ranked_reasons": hits,
        "most_likely": hits[0]["reason"],
        "biggest_unresolved_concern": {
            "title": "Title. Until somebody reads the deeds and liens, everything "
                     "else is guesswork.",
            "condition": "Condition. Nobody has been inside it.",
            "access": "Legal access. A lot you cannot legally reach is nearly worthless.",
            "flood": "Flood risk, and the insurance that comes with it.",
        }.get(biggest, "Title and condition - the two things we never have on day one."),
        "note": "These are ranked possibilities, not findings. Each one has to be "
                "checked before it means anything.",
    }


# ------------------------------------------------------------ deal or trap --

def deal_or_trap(prop: dict, scores: dict | None = None) -> dict:
    scores = scores or scoring.scores_for(prop["id"])
    sig = {s["key"] for s in (prop.get("distress") or [])}
    overall = (scores.get("overall") or {}).get("score", 50)
    risk = (scores.get("risk") or {}).get("score", 50)

    hard_stops = []
    if "possible_no_access" in sig:
        hard_stops.append("No road shows next to this parcel.")
    if "flood_zone" in sig:
        hard_stops.append("It sits in a FEMA flood hazard area.")
    if "unknown_owner" in sig:
        hard_stops.append("We do not know who owns it.")
    if prop.get("excluded"):
        hard_stops.append(prop.get("exclusion_reason") or "Outside the search area.")

    if len(hard_stops) >= 2:
        verdict, colour = "TRAP", "red"
        why = ("More than one hard problem at once. Any of these alone is survivable; "
               "together they are how people lose money on a cheap lot.")
    elif hard_stops:
        verdict, colour = "PROBLEMATIC", "orange"
        why = ("There is a real problem here. It might be solvable, but it has to be "
               "solved before price even matters.")
    elif overall >= 68 and risk <= 55:
        verdict, colour = "DEAL", "green"
        why = ("The signals point the right way and nothing obvious disqualifies it. "
               "That still means investigate, not buy.")
    else:
        verdict, colour = "MAYBE", "yellow"
        why = ("Not enough here to get excited, not enough to walk away. It comes down "
               "to the things nobody has checked yet.")

    return {
        "verdict": verdict, "colour": colour, "why": why,
        "hard_stops": hard_stops,
        "confidence": (scores.get("overall") or {}).get("confidence", "LOW"),
        "note": "This assessment is deliberately conservative while evidence is "
                "incomplete. It moves as facts arrive.",
    }


# ------------------------------------------------------- what to do next ----

def next_steps(prop: dict) -> list[dict]:
    sig = {s["key"] for s in (prop.get("distress") or [])}
    steps: list[dict] = []

    def add(priority, title, detail, where="", url=""):
        steps.append({"priority": priority, "title": title, "detail": detail,
                      "where_to_look": where, "source_url": url})

    add(1, "Verify who actually owns it today",
        "The tax roll lags real transfers. The deed is the truth.",
        "Garland County Circuit Clerk / Recorder",
        "https://www.garlandcounty.org/166/Circuit-Clerk")
    add(1, "Check the tax status",
        "Find out whether taxes are current, how many years are behind, how much is "
        "owed, and whether it has been certified to the State.",
        "Garland County Tax Collector",
        "https://www.garlandcounty.org/181/Tax-Collector")
    add(1, "Check title: liens, judgements, easements",
        "This is the step that decides whether anything else matters. "
        "Have an Arkansas real-estate attorney read it.",
        "Garland County Circuit Clerk / Recorder",
        "https://www.garlandcounty.org/166/Circuit-Clerk")
    add(2, "Confirm the zoning and what it lets you do",
        "Ask directly whether your intended use is permitted by right, needs a "
        "conditional-use permit, or is not permitted at all. Never assume approval.",
        "City of Hot Springs Planning & Development",
        "https://www.cityhs.net/162/Planning-Development")
    if "possible_no_access" in sig:
        add(1, "Confirm there is legal access",
            "No road showed on the map next to this parcel. Look for a recorded "
            "easement or platted frontage before anything else.",
            "Plat and deed at the Circuit Clerk")
    add(2, "Drive by and look at it",
        "Photos of the street, the structure, the roof line, the driveway, the "
        "neighbours. Fifteen minutes on site beats an hour of records.",
        "Field mode in this app")
    if prop.get("improved") == 1:
        add(2, "Get a rehab number from somebody who swings a hammer",
            "Our estimate is a per-square-foot rule of thumb. It is not a bid.")
        add(3, "Research what houses on this street actually rent for",
            "Rent is the single number that decides whether a rental works.")
    if prop.get("improved") == 0:
        add(3, "Check utilities at the road",
            "Water, sewer or septic, power, and what it costs to bring them in.")
    add(3, "Work out the most you should pay",
        "Use the deal analyzer once the repair number and the finished value are real "
        "rather than estimated.")
    for i, s in enumerate(steps):
        s["order"] = i + 1
    return steps


# ---------------------------------------------------------------- explain ---

def explain(prop: dict, use_ai: bool = True) -> dict:
    evidence = store.evidence_view(prop["id"]); prop = dict(prop, notes=store.notes_for(prop["id"]))
    block = ai.evidence_block(prop, evidence)
    prompt = f"""{block}

Write a short plain-English briefing for Topher about this property. Use exactly
these five headings, and keep each to two or three sentences:

WHAT IS GOING ON HERE
WHAT LOOKS GOOD
WHAT WORRIES ME
WHAT WE DON'T KNOW
WHAT I'D DO NEXT

Rules: only use the evidence above. If something is not in the evidence, say we
haven't checked it. Do not invent numbers. Do not give legal advice.
"""
    text, model = ("", "")
    removed: list[str] = []
    if use_ai:
        text, model = ai.ask(prompt)
        if text:
            text, removed = ai.guard(text, block)
    return {
        "text": text or ai.fallback_summary(prop),
        "model": model if text else "",
        "source": "local AI reading only the stored evidence" if text
                  else "evidence readout (no AI model running)",
        "removed_figures": removed,
        "guard_note": (f"{len(removed)} figure(s) the model made up were removed: "
                       + ", ".join(removed[:6]) if removed else ""),
        "disclaimer": LEGAL_DISCLAIMER,
    }


def what_would_you_do(prop: dict, use_ai: bool = True) -> dict:
    scores = scoring.scores_for(prop["id"])
    dot = deal_or_trap(prop, scores)
    evidence = store.evidence_view(prop["id"]); prop = dict(prop, notes=store.notes_for(prop["id"]))
    prompt = f"""{ai.evidence_block(prop, evidence)}

Our own conservative read: {dot['verdict']} - {dot['why']}

Topher asked: "if this were your deal, what would you do?"

Answer in under 150 words, first person, plain English. Start with whether you
would make an offer yet (the honest answer is almost always "not yet"), then say
exactly what has to happen before an offer would make sense. This is your
opinion and analysis, not legal advice - say so in your own words at the end.
"""
    text, model = ("", "")
    removed: list[str] = []
    if use_ai:
        text, model = ai.ask(prompt)
        if text:
            text, removed = ai.guard(text, ai.evidence_block(prop, evidence))
    if not text:
        text = (f"If this were my deal I would not make an offer yet. Our read is "
                f"{dot['verdict']}: {dot['why']} Before an offer would make sense I "
                f"would want the owner confirmed on the deed, the tax status pulled, "
                f"title read by an attorney, the zoning confirmed for whatever you "
                f"intend to do, and eyes on the property. This is opinion, not legal "
                f"advice.")
    return {"text": text, "model": model, "verdict": dot["verdict"],
            "removed_figures": removed,
            "guard_note": (f"{len(removed)} figure(s) the model made up were removed."
                           if removed else ""),
            "disclaimer": LEGAL_DISCLAIMER}


# --------------------------------------------------------------- use fit ----

def business_use_analysis(prop: dict) -> dict:
    """Topher's own uses (spec 21)."""
    scores = scoring.scores_for(prop["id"])
    sqft = prop.get("building_sqft") or 0
    ac = prop.get("acreage") or 0
    road = store.known_value(prop["id"], "road_access") or "unknown"
    uses = [
        {"use": "3D printing / workshop", "score": (scores.get("workshop") or {}).get("score"),
         "needs": ["enough floor space", "adequate electrical panel", "freight access",
                   "zoning that allows light manufacturing or a home occupation"],
         "fit": "good" if sqft >= 800 else ("tight" if sqft else "would have to be built")},
        {"use": "Office / web & AI work", "score": (scores.get("business") or {}).get("score"),
         "needs": ["business internet", "parking", "quiet enough to think"],
         "fit": "good" if (prop.get("city") or "").lower() not in ("", "unincorporated", "rural")
                else "internet is the question"},
        {"use": "Inventory & customer pickup", "score": (scores.get("business") or {}).get("score"),
         "needs": ["visible frontage", "somewhere to park", "a door wide enough"],
         "fit": "good" if "highway" in road else "low visibility"},
        {"use": "Storage development", "score": (scores.get("storage") or {}).get("score"),
         "needs": ["acreage", "road access", "zoning", "drainage"],
         "fit": "worth modelling" if ac >= 1 else "too small"},
        {"use": "Seasonal snow-cone stand", "score": (scores.get("snowcone") or {}).get("score"),
         "needs": ["traffic", "parking", "water and wastewater", "health permit",
                   "a zoning use that allows seasonal food"],
         "fit": "worth looking at" if "highway" in road else "not enough traffic"},
        {"use": "Rental", "score": (scores.get("rental") or {}).get("score"),
         "needs": ["a habitable structure", "a tenant market", "rent that covers the note"],
         "fit": "possible" if prop.get("improved") == 1 else "nothing to rent yet"},
    ]
    uses.sort(key=lambda u: -(u["score"] or 0))
    return {
        "ranked": uses,
        "best_fit": uses[0]["use"] if uses else None,
        "warning": ("None of this means the City would approve the use. Zoning and "
                    "permits are decided by Hot Springs Planning & Development and, for "
                    "any food business, the Arkansas Department of Health. Confirm "
                    "before you spend a dollar."),
    }


# ------------------------------------------------------------- comparison ---

COMPARE_FIELDS = [
    ("address", "Address"), ("city", "City"), ("acreage", "Acres"),
    ("total_value", "Assessed value"), ("land_value", "Land value"),
    ("imp_value", "Improvement value"), ("property_type", "Type"),
    ("flood_zone", "Flood zone"), ("zoning", "Zoning"), ("tax_status", "Tax status"),
    ("owner_name", "Owner of record"), ("recommendation", "Our call"),
]


def compare(prop_ids: list[int]) -> dict:
    props = []
    for pid in prop_ids:
        p = store.get_property(pid)
        if p:
            p["scores"] = scoring.scores_for(pid)
            p["deal_or_trap"] = deal_or_trap(p, p["scores"])
            props.append(p)
    if len(props) < 2:
        return {"error": "Pick at least two properties to compare."}

    rows = []
    for key, label in COMPARE_FIELDS:
        rows.append({"field": label,
                     "values": [p.get(key) for p in props]})
    for kind in ("overall", "risk", "rental", "land", "storage", "business",
                 "workshop", "snowcone"):
        rows.append({"field": f"{kind} score",
                     "values": [(p["scores"].get(kind) or {}).get("score") for p in props]})

    best = max(props, key=lambda p: ((p["scores"].get("overall") or {}).get("score", 0)
                                     - (p["scores"].get("risk") or {}).get("score", 0) * 0.35))
    reasons = []
    b = best["scores"]
    reasons.append(f"Highest opportunity score once risk is taken off the top "
                   f"({(b.get('overall') or {}).get('score')} opportunity against "
                   f"{(b.get('risk') or {}).get('score')} risk).")
    if best.get("distress"):
        reasons.append("It carries the distress signals that make a property "
                       "negotiable: " +
                       ", ".join(s["label"].lower() for s in best["distress"][:3]) + ".")
    reasons.append("That said, the comparison is only as good as the evidence - and on "
                   "every one of these, title and condition are still unchecked.")
    return {
        "properties": [{"id": p["id"], "address": p.get("address") or p.get("parcel_id"),
                        "recommendation": p.get("recommendation")} for p in props],
        "rows": rows,
        "winner": {"id": best["id"],
                   "address": best.get("address") or best.get("parcel_id"),
                   "why": reasons},
    }


# ------------------------------------------------------------------ picks ---

def topher_picks(limit: int = 3, data_class: str = "real") -> list[dict]:
    rows = db.q("""
        SELECT p.*, so.score AS overall, sr.score AS risk
        FROM properties p
        JOIN scores so ON so.property_id=p.id AND so.kind='overall'
        LEFT JOIN scores sr ON sr.property_id=p.id AND sr.kind='risk'
        WHERE p.excluded=0 AND p.data_class=? AND p.state NOT IN ('PASS','ARCHIVED')
        ORDER BY (so.score - IFNULL(sr.score,50)*0.35) DESC
        LIMIT ?""", (data_class, limit))
    picks = []
    for r in rows:
        p = store.get_property(r["id"])
        sc = scoring.scores_for(r["id"])
        dot = deal_or_trap(p, sc)
        top_lines = sorted((sc.get("overall") or {}).get("lines", []),
                           key=lambda l: -l["points"])[:3]
        picks.append({
            "id": p["id"],
            "address": p.get("address") or p.get("parcel_id"),
            "score": r["overall"], "risk": r["risk"],
            "recommendation": p.get("recommendation"),
            "why": [l["reason"] for l in top_lines],
            "risk_note": dot["why"],
            "unknown": (sc.get("overall") or {}).get("unknowns", [])[:3],
            "next_step": next_steps(p)[0]["title"],
        })
    return picks
