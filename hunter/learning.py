"""Learning from passed deals (spec 80).

When Topher passes on something and says why, that reason is kept. Later lists
can use it - but only in the open: the scoring model is never touched, the
score on the card never moves, and any list this influences says so.

What it does: ranks a property a little lower in a sorted list when it carries
the same kind of problem Topher has already walked away from. What it does not
do: change a score, hide a property, or decide anything.
"""
from __future__ import annotations

from . import db

# pass reason -> distress-signal keys that mean "this is the same problem again"
REASON_SIGNALS = {
    "flood": {"flood_zone"},
    "no access": {"possible_no_access"},
    "bad title": {"estate_owner", "unknown_owner", "trust_owner"},
    "too much rehab": {"low_improvement_value", "building_worth_less_than_dirt"},
    "bad location": set(),                  # handled by the rural test below
    "zoning": set(),
    "too expensive": set(),
    "not enough rent": set(),
    "not my kind of deal": set(),
}


def pass_reasons() -> dict[str, int]:
    rows = db.q("SELECT reason, COUNT(*) n FROM decisions WHERE decision='pass' "
                "GROUP BY reason")
    return {r["reason"]: r["n"] for r in rows}


def enabled() -> bool:
    return bool(db.setting("learning_enabled", True))


def flags_for(prop: dict, reasons: dict[str, int] | None = None) -> list[str]:
    """Which of Topher's past pass-reasons this property resembles."""
    reasons = pass_reasons() if reasons is None else reasons
    if not reasons:
        return []
    keys = {s["key"] for s in (prop.get("distress") or [])}
    hits = []
    for reason, n in reasons.items():
        if n < 1:
            continue
        sig = REASON_SIGNALS.get(reason, set())
        if sig & keys:
            hits.append(reason)
        elif reason == "bad location" and \
                (prop.get("city") or "").lower() in ("", "unincorporated", "rural"):
            hits.append(reason)
    return hits


def apply(properties: list[dict], sort: str) -> dict:
    """Annotate and (for ranked sorts only) gently demote flagged properties.

    Returns {"influenced": bool, "reasons": [...]} for the UI banner.
    """
    reasons = pass_reasons()
    if not reasons or not enabled():
        for p in properties:
            p["preference_flags"] = []
        return {"influenced": False, "reasons": []}
    any_hit = False
    for p in properties:
        p["preference_flags"] = flags_for(p, reasons)
        any_hit = any_hit or bool(p["preference_flags"])
    ranked_sorts = {"overall", "rental", "land", "storage", "business", "workshop",
                    "snowcone", "resale"}
    if any_hit and sort in ranked_sorts:
        # stable: flagged ones sink within the list, order among equals is kept
        properties.sort(key=lambda p: 1 if p["preference_flags"] else 0)
    return {"influenced": any_hit and sort in ranked_sorts,
            "reasons": sorted(reasons, key=lambda r: -reasons[r])}
