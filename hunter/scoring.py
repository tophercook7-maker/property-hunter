"""Transparent, multi-purpose scoring (spec 28/29).

No opaque AI number. Every score is a list of named lines with points and a
plain sentence, and the UI shows the lines. Weights live in config.
"""
from __future__ import annotations

from . import db, store
from .config import SCORE_KINDS, SCORING_WEIGHTS
from .db import jdump, utcnow

RECOMMENDATIONS = ["BUY CANDIDATE", "INVESTIGATE", "WATCH", "NEGOTIATE", "PASS",
                   "DO NOT TOUCH"]


class Sheet:
    """Accumulates scoring lines so the reasoning is always visible."""

    def __init__(self, kind: str):
        self.kind = kind
        self.lines: list[dict] = []
        self.unknowns: list[str] = []

    def add(self, points: float, reason: str, evidence: str = "") -> None:
        if points:
            self.lines.append({"points": round(points, 1), "reason": reason,
                               "evidence": evidence})

    def unknown(self, what: str) -> None:
        self.unknowns.append(what)

    @property
    def total(self) -> float:
        return round(sum(l["points"] for l in self.lines), 1)

    def normalized(self) -> float:
        """Map the raw total onto 0-100 without pretending to precision.

        Opportunity sheets start from a neutral 50 and move either way. The risk
        sheet starts at zero and only adds, so "everything is unknown" reads as
        a high-but-not-pegged number instead of slamming into 100 every time.
        """
        if self.kind == "risk":
            return max(0.0, min(100.0, round(self.total * 0.8, 1)))
        return max(0.0, min(100.0, round(50 + self.total, 1)))

    def confidence(self) -> str:
        if len(self.unknowns) >= 5:
            return "LOW"
        if len(self.unknowns) >= 2:
            return "MEDIUM"
        return "HIGH"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "raw": self.total, "score": self.normalized(),
                "lines": self.lines, "unknowns": self.unknowns,
                "confidence": self.confidence()}


def _signals(prop: dict) -> dict[str, dict]:
    return {s["key"]: s for s in (prop.get("distress") or [])}


def _known(prop: dict, field: str):
    ev = store.latest_evidence(prop["id"], field)
    return ev["value"] if ev else None


# ------------------------------------------------------------------ overall

def overall(prop: dict) -> Sheet:
    w = SCORING_WEIGHTS["overall"]
    s = Sheet("overall")
    sig = _signals(prop)
    distress = [k for k, v in sig.items() if v["kind"] == "distress"]

    for key in distress:
        s.add(w["distress_signal"], f"Distress signal: {sig[key]['label']}",
              sig[key]["why"])
    if len(distress) >= 3:
        s.add(w["multiple_distress"],
              f"{len(distress)} separate distress signals stack up on this one",
              "Several independent signals pointing the same way is more meaningful "
              "than any single one.")
    if "vacant_land" in sig:
        s.add(w["vacant_land"], "Vacant land - fewer unknowns than a building",
              "You cannot have a bad roof on an empty lot.")
    if "low_improvement_value" in sig:
        s.add(w["low_improvement_value"], "Building carries a very low assessed value",
              sig["low_improvement_value"]["why"])
    for key in ("institutional_owner", "estate_owner", "government_owner"):
        if key in sig:
            s.add(w[key], sig[key]["label"], sig[key]["why"])
    if "stale_assessment" in sig:
        s.add(w["stale_assessment"], "County record has not been touched in years",
              sig["stale_assessment"]["why"])
    if "useful_acreage" in sig:
        s.add(w["acreage_useful"], "Enough acreage to do something with", "")

    if prop.get("address"):
        s.add(w["has_address"], "Has a real street address", "")
    else:
        s.add(w["no_address"], "No street address on the record", "")
    if (prop.get("city") or "").lower() not in ("", "unincorporated", "rural"):
        s.add(w["in_city"], f"Inside {prop['city']} - services and buyers nearby", "")

    price = prop.get("list_price")
    total = prop.get("total_value") or 0
    if price and total and price < total * 0.8:
        s.add(w["price_below_assessed"],
              f"Asking ${price:,.0f} against an assessed ${total:,.0f}", "")

    # Penalties for what we do NOT know - this is the honest half.
    if not _known(prop, "title_status"):
        s.add(w["title_unknown"], "We have not checked title",
              "Until somebody reads the deeds and liens, the price is a guess.")
        s.unknown("title / liens")
    if not _known(prop, "condition"):
        s.add(w["condition_unknown"], "Nobody has looked at the condition",
              "The single biggest number in any rehab is the one we do not have yet.")
        s.unknown("physical condition")
    if "flood_zone" in sig:
        s.add(w["flood_risk"], "Sits in a FEMA flood hazard area", "")
    if "possible_no_access" in sig:
        s.add(w["no_road_frontage"], "No road mapped at this parcel", "")
    if "tiny_lot" in sig:
        s.add(w["tiny_lot"], "Parcel may be too small to be useful", "")
    if "unknown_owner" in sig:
        s.add(w["unknown_owner"], "Owner unknown", "")
    if not prop.get("tax_status"):
        s.unknown("tax / delinquency status")
    if not prop.get("zoning"):
        s.unknown("zoning")
    return s


# ------------------------------------------------------------------ purpose

def rental(prop: dict) -> Sheet:
    s = Sheet("rental")
    sig = _signals(prop)
    if prop.get("property_type") not in ("house", "multifamily"):
        s.add(-25, "Not a house or multifamily - this is not a rental as it sits", "")
    else:
        s.add(12, "Residential structure on the parcel", "")
    if prop.get("improved") == 1:
        s.add(8, "There is already a building to rent", "")
    if (prop.get("city") or "").lower() not in ("", "unincorporated", "rural"):
        s.add(10, "In town - that is where the tenants are", "")
    else:
        s.add(-6, "Rural location - smaller tenant pool, longer vacancies", "")
    imp = prop.get("imp_value") or 0
    if 15000 <= imp <= 120000:
        s.add(12, f"Improvement value ${imp:,.0f} sits in workable rental territory", "")
    elif 0 < imp < 15000:
        s.add(-8, "Improvement value is so low the house may need everything", "")
    if "flood_zone" in sig:
        s.add(-12, "Flood zone - insurance eats the cash flow", "")
    road = (_known(prop, "road_access") or "").lower()
    if any(k in road for k in ("highway", "interstate")):
        s.add(-5, "Fronting a busy highway is worse for a rental than for a business", "")
    s.unknown("actual market rent for this street")
    s.unknown("condition and rehab cost")
    return s


def land(prop: dict) -> Sheet:
    s = Sheet("land")
    sig = _signals(prop)
    ac = prop.get("acreage") or 0
    if prop.get("improved") == 0 or (prop.get("imp_value") or 0) < 500:
        s.add(15, "It is genuinely vacant land", "")
    else:
        s.add(-18, "There is a structure here - this is not a clean land play", "")
    if ac >= 5:
        s.add(14, f"{ac:.1f} acres - room for a real project", "")
    elif ac >= 1:
        s.add(9, f"{ac:.2f} acres - enough for a building and parking", "")
    elif ac >= 0.2:
        s.add(3, f"{ac:.2f} acres - a normal town lot", "")
    else:
        s.add(-8, f"Only {ac:.3f} acres", "")
    if "possible_no_access" in sig:
        s.add(-20, "No mapped road - land you cannot reach is nearly worthless", "")
    if "flood_zone" in sig:
        s.add(-14, "Flood zone limits what can be built", "")
    land_val = prop.get("land_value") or 0
    if ac and land_val:
        per_acre = land_val / ac
        if per_acre < 3000:
            s.add(10, f"County values the dirt at about ${per_acre:,.0f}/acre", "")
    s.unknown("utilities at the road")
    s.unknown("zoning and setbacks")
    slope = _known(prop, "slope_pct")
    if slope is None:
        s.unknown("topography and drainage")
    else:
        try:
            g = float(slope)
        except (TypeError, ValueError):
            g = None
        if g is not None:
            if g < 5:
                s.add(8, f"Nearly flat ({g:.0f}% grade) - cheap to build on", "")
            elif g < 10:
                s.add(3, f"Gentle slope ({g:.0f}%)", "")
            elif g < 20:
                s.add(-6, f"Moderate slope ({g:.0f}%) - grading and drainage cost money", "")
            else:
                s.add(-15, f"Steep ({g:.0f}%) - retaining walls or serious dirt work", "")
    return s


def storage(prop: dict) -> Sheet:
    s = Sheet("storage")
    sig = _signals(prop)
    ac = prop.get("acreage") or 0
    if ac >= 2:
        s.add(18, f"{ac:.1f} acres is a real storage site", "")
    elif ac >= 0.75:
        s.add(9, f"{ac:.2f} acres could hold a small building or two", "")
    else:
        s.add(-12, "Too small for a storage facility worth building", "")
    if prop.get("improved") == 0:
        s.add(8, "Nothing to tear down first", "")
    road = (_known(prop, "road_access") or "").lower()
    if any(k in road for k in ("highway", "major street", "through street")):
        s.add(10, "On a road people actually drive", "")
    elif "no mapped road" in road:
        s.add(-20, "No road access", "")
    if "flood_zone" in sig:
        s.add(-16, "You cannot put customers' belongings in a flood zone", "")
    slope = _known(prop, "slope_pct")
    try:
        g = float(slope) if slope is not None else None
    except (TypeError, ValueError):
        g = None
    if g is not None:
        if g < 5:
            s.add(8, "Flat enough for long storage buildings and drive aisles", "")
        elif g >= 12:
            s.add(-12, f"{g:.0f}% grade - storage rows want flat pads", "")
    if (prop.get("city") or "").lower() not in ("", "unincorporated", "rural"):
        s.add(6, "Close to town where the demand is", "")
    s.unknown("zoning - storage is almost never permitted by right in a residential district")
    s.unknown("utilities and drainage")
    return s


def business(prop: dict) -> Sheet:
    s = Sheet("business")
    road = (_known(prop, "road_access") or "").lower()
    if any(k in road for k in ("interstate", "us highway", "state highway")):
        s.add(16, "Visible from a main road", "")
    elif any(k in road for k in ("major street", "through street")):
        s.add(8, "On a through street", "")
    elif "no mapped road" in road:
        s.add(-22, "No road access - not a business site", "")
    if prop.get("property_type") == "commercial":
        s.add(14, "Already a commercial parcel", "")
    elif prop.get("property_type") == "house":
        s.add(-6, "Residential parcel - a business use likely needs a zoning change", "")
    ac = prop.get("acreage") or 0
    if ac >= 0.3:
        s.add(7, "Room for parking", "")
    if (prop.get("city") or "").lower() not in ("", "unincorporated", "rural"):
        s.add(8, "In the city - customers, utilities, internet", "")
    s.unknown("zoning and permitted use")
    s.unknown("electrical service size")
    s.unknown("internet availability")
    return s


def workshop(prop: dict) -> Sheet:
    """3D printing / computer work / inventory / customer pickup."""
    s = Sheet("workshop")
    sqft = prop.get("building_sqft") or 0
    if sqft >= 1200:
        s.add(15, f"About {sqft:,.0f} sqft of building footprint - room to work", "")
    elif sqft >= 400:
        s.add(8, f"About {sqft:,.0f} sqft footprint - workable for a small shop", "")
    elif prop.get("improved") == 0:
        s.add(-10, "No building - you would be building one", "")
    if prop.get("property_type") in ("commercial",):
        s.add(12, "Commercial parcel - a workshop use fits more easily", "")
    ac = prop.get("acreage") or 0
    if ac >= 0.25:
        s.add(6, "Space for deliveries and customer pickup", "")
    road = (_known(prop, "road_access") or "").lower()
    if "no mapped road" in road:
        s.add(-20, "No access for freight or customers", "")
    if (prop.get("city") or "").lower() not in ("", "unincorporated", "rural"):
        s.add(7, "In town - better odds on business internet", "")
    s.unknown("electrical panel size - 3D printers and tools add up")
    s.unknown("zoning for light manufacturing / home occupation")
    s.unknown("internet service at this address")
    return s


def snowcone(prop: dict) -> Sheet:
    s = Sheet("snowcone")
    sig = _signals(prop)
    road = (_known(prop, "road_access") or "").lower()
    if any(k in road for k in ("interstate", "us highway")):
        s.add(20, "On a main road - a seasonal stand lives on passing traffic", "")
    elif "state highway" in road:
        s.add(16, "On a state highway - decent traffic", "")
    elif "major street" in road:
        s.add(10, "On a major street", "")
    elif "through street" in road:
        s.add(6, "On a through street", "")
    else:
        s.add(-18, "Not enough passing traffic for a stand", "")
    ac = prop.get("acreage") or 0
    if 0.12 <= ac <= 2.0:
        s.add(10, f"{ac:.2f} acres - right size for a stand and a few parking spaces", "")
    elif ac > 2.0:
        s.add(4, "Plenty of room, more land than a stand needs", "")
    else:
        s.add(-8, "Probably too tight for parking and a drive-up", "")
    if prop.get("improved") == 0:
        s.add(8, "Clear lot - easy to place a stand", "")
    anchors = _known(prop, "nearby_traffic_anchors") or ""
    if anchors:
        s.add(8, f"Traffic generators nearby: {anchors}", "")
    competitors = _known(prop, "nearby_food_business") or ""
    if competitors:
        s.add(-6, f"Existing food businesses nearby: {competitors}", "")
    if "flood_zone" in sig:
        s.add(-8, "Flood zone - a seasonal setup in a flood area is a bad idea", "")
    s.unknown("zoning for a seasonal food use")
    s.unknown("health department permit requirements")
    s.unknown("water and wastewater at the site")
    s.unknown("real traffic counts")
    return s


def resale(prop: dict) -> Sheet:
    s = Sheet("resale")
    sig = _signals(prop)
    if prop.get("improved") == 1:
        s.add(10, "A structure gives you something to improve and resell", "")
    if "low_improvement_value" in sig:
        s.add(12, "Cheap on paper relative to a finished house", "")
    if (prop.get("city") or "").lower() not in ("", "unincorporated", "rural"):
        s.add(10, "In town - a resale market exists here", "")
    else:
        s.add(-6, "Rural - resale can be slow", "")
    if "flood_zone" in sig:
        s.add(-12, "Flood zone narrows your buyer pool", "")
    s.unknown("what finished houses on this street actually sell for")
    s.unknown("rehab cost")
    return s


def risk(prop: dict) -> Sheet:
    """Higher = riskier. Displayed separately, never blended into opportunity."""
    s = Sheet("risk")
    sig = _signals(prop)
    s.add(18, "Title has not been examined", "Every unexamined title is a risk.")
    s.unknown("title")
    s.add(14, "Condition has not been inspected", "")
    s.unknown("condition")
    if not prop.get("tax_status"):
        s.add(12, "Tax and delinquency status unknown", "")
        s.unknown("taxes")
    if not prop.get("zoning"):
        s.add(10, "Zoning unconfirmed", "")
        s.unknown("zoning")
    if "flood_zone" in sig:
        s.add(14, "Mapped flood hazard", "")
    if "possible_no_access" in sig:
        s.add(20, "Possible lack of legal access", "")
    if "unknown_owner" in sig:
        s.add(16, "Owner of record unclear", "")
    for key in ("record_says_building_map_says_none", "map_says_building_record_says_vacant"):
        if key in sig:
            s.add(8, "Sources disagree about what is standing here", "")
    return s


BUILDERS = {
    "overall": overall, "rental": rental, "resale": resale, "land": land,
    "storage": storage, "business": business, "workshop": workshop,
    "snowcone": snowcone, "risk": risk,
}


def recommend(prop: dict, sheets: dict[str, Sheet]) -> tuple[str, str]:
    """Conservative recommendation + a plain-English reason."""
    o = sheets["overall"].normalized()
    r = sheets["risk"].normalized()
    sig = _signals(prop)

    if "possible_no_access" in sig and "flood_zone" in sig:
        return ("DO NOT TOUCH",
                "No mapped road access and a flood hazard on the same parcel. That is "
                "two of the hardest problems to fix, stacked on one piece of dirt.")
    if prop.get("excluded"):
        return ("PASS", prop.get("exclusion_reason") or "Outside the search area.")
    if o >= 72 and r < 60:
        return ("INVESTIGATE",
                "The numbers and the signals both look interesting, and nothing "
                "obvious disqualifies it. Worth spending real time on - but not "
                "money, not yet.")
    if o >= 72:
        return ("INVESTIGATE",
                "This one is interesting, but the unknowns are stacked high enough "
                "that I would not put money near it until they are answered.")
    if o >= 58:
        return ("WATCH",
                "Something here is worth keeping an eye on, but not enough to spend "
                "your week on it today.")
    if o >= 45:
        return ("WATCH", "Nothing wrong with it, nothing exciting either. Park it.")
    return ("PASS",
            "Not enough upside showing to justify the work. If something changes - "
            "price, owner, tax status - it comes back.")


def compute(prop: dict, persist: bool = True) -> dict:
    sheets = {kind: BUILDERS[kind](prop) for kind in SCORE_KINDS if kind in BUILDERS}
    rec, why = recommend(prop, sheets)
    out = {k: v.as_dict() for k, v in sheets.items()}
    out["recommendation"] = rec
    out["recommendation_reason"] = why
    if persist:
        now = utcnow()
        for kind, sheet in sheets.items():
            d = sheet.as_dict()
            db.ex("INSERT INTO scores(property_id,kind,score,confidence,breakdown_json,"
                  "computed_at) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(property_id,kind) DO UPDATE SET score=excluded.score, "
                  "confidence=excluded.confidence, breakdown_json=excluded.breakdown_json, "
                  "computed_at=excluded.computed_at",
                  (prop["id"], kind, d["score"], d["confidence"], jdump(d), now))
        db.ex("UPDATE properties SET recommendation=? WHERE id=?", (rec, prop["id"]))
    return out


def scores_for(prop_id: int) -> dict:
    rows = db.q("SELECT kind,score,confidence,breakdown_json FROM scores WHERE property_id=?",
                (prop_id,))
    return {r["kind"]: {**db.jload(r["breakdown_json"], {}), "score": r["score"],
                        "confidence": r["confidence"]} for r in rows}
