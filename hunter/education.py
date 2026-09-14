"""Education mode (spec 41). Plain language, with the current property as the example."""
from __future__ import annotations

from . import store

GLOSSARY = {
    "lien": ("Somebody else's claim for money attached to the property. If you buy it "
             "without clearing the lien, you can end up paying it. Cleanup liens, tax "
             "liens and contractor liens all work this way."),
    "deed": ("The piece of paper that actually transfers ownership. What the tax roll "
             "says is a bookkeeping entry - the deed is the legal truth."),
    "title": ("The whole history of who owned it and what is attached to it. 'Clear "
              "title' means nothing unexpected is riding along with the property."),
    "assessed value": ("Two numbers live on the county roll. The APPRAISED total is what "
                       "the assessor thinks the property is worth; the ASSESSED value is "
                       "20% of that and is what the tax bill is figured on. The totals "
                       "shown here are the appraised ones - an opinion updated on a "
                       "schedule, not a sale price."),
    "market value": ("What somebody would actually pay for it today. Nobody knows this "
                     "number until a sale happens."),
    "noi": ("Net Operating Income. Rent collected minus everything it costs to run the "
            "place - taxes, insurance, repairs, vacancy, management - but BEFORE the "
            "mortgage payment."),
    "cap rate": ("NOI divided by what the property cost you. A quick way to compare two "
                 "rentals. Higher usually means better return and more risk."),
    "cash-on-cash": ("The cash you get back in a year divided by the cash you actually "
                     "put in. This is the one that tells you whether your own money is "
                     "working."),
    "grm": ("Gross Rent Multiplier - price divided by a year's rent. Rough and fast, "
            "ignores expenses entirely."),
    "dscr": ("Debt Service Coverage Ratio. NOI divided by the annual mortgage payment. "
             "Under 1.0 means the property does not cover its own loan."),
    "foreclosure": ("The lender taking the property back because the loan was not paid. "
                    "A lender-owned house is often for sale by a motivated owner."),
    "delinquent tax": ("Property taxes that were not paid. In Arkansas, after a couple "
                       "of years the county certifies the parcel to the Commissioner of "
                       "State Lands. IMPORTANT: paying somebody else's delinquent taxes "
                       "does NOT make you the owner."),
    "certification": ("When the county hands a tax-delinquent parcel over to the "
                      "Commissioner of State Lands, who may eventually auction it."),
    "cosl": ("The Arkansas Commissioner of State Lands - the state office that handles "
             "tax-delinquent land and the auctions."),
    "redemption": ("The window in which the old owner can pay what is owed and get the "
                   "property back. Until that window closes, what you bought is not "
                   "settled."),
    "easement": ("Somebody else's right to use part of the property - a driveway, a "
                 "power line, a pipe. It rides with the land."),
    "zoning": ("The City's rules about what you may do on a piece of land. Zoning is "
               "why 'I could put a storage building there' is a question, not a plan."),
    "setback": ("How far a building has to sit from each property line. Setbacks are "
                "what turns a one-acre lot into a much smaller buildable area."),
    "flood zone": ("FEMA's map of where water is expected to go. Zones starting with A "
                   "or V are Special Flood Hazard Areas - insurance and building limits."),
    "parcel": ("One piece of land as the county records it, with its own parcel number."),
    "rpid": ("Real Property ID - another identifier some Arkansas systems use for a "
             "parcel alongside the parcel number."),
    "arv": ("After Repair Value - what it would be worth once the work is done. Every "
            "maximum-purchase-price calculation stands on this number, which is an "
            "estimate until an appraiser or a buyer says otherwise."),
    "mao": ("Maximum Allowable Offer - the most you can pay and still hit your target "
            "return after repairs, costs and a margin for being wrong."),
    "adverse possession": ("A legal doctrine where long, open, exclusive use of land can "
                           "sometimes ripen into ownership. It has strict requirements, "
                           "it is fact-specific, and it is never something to assume. "
                           "Talk to an Arkansas attorney."),
    "conditional use permit": ("Permission from the City for a use that is not allowed "
                               "by right in that zoning district. It is a request, not "
                               "a formality."),
    "cleanup lien": ("When the City mows, clears or secures a neglected property and "
                     "bills the owner. An unpaid one attaches to the property - and it "
                     "is a strong hint the owner stopped caring."),
    "condemnation": ("The City declaring a structure unfit. It can lead to a demolition "
                     "order, which becomes the buyer's problem and the buyer's bill."),
    "basis": ("Everything you have into the deal: purchase, repairs, closing, carrying, "
              "financing and the contingency. Compare against value, not against price."),
    "vacancy rate": ("The share of the year you assume the place sits empty. If you "
                     "budget zero vacancy you will be wrong."),
}

ALIASES = {"cap": "cap rate", "coc": "cash-on-cash", "noi ": "noi",
           "taxes": "delinquent tax", "state lands": "cosl",
           "arv ": "arv", "after repair value": "arv"}


def explain_term(term: str, prop_id: int | None = None) -> dict:
    key = term.strip().lower()
    key = ALIASES.get(key, key)
    text = GLOSSARY.get(key)
    if not text:
        matches = [k for k in GLOSSARY if key in k or k in key]
        if matches:
            key = matches[0]
            text = GLOSSARY[key]
    if not text:
        return {"term": term, "found": False,
                "text": "Not in the glossary yet. Ask and it gets added.",
                "related": sorted(GLOSSARY.keys())[:8]}
    out = {"term": key, "found": True, "text": text}
    if prop_id:
        p = store.get_property(prop_id)
        if p:
            out["example"] = _example(key, p)
    return out


def _example(key: str, p: dict) -> str | None:
    addr = p.get("address") or f"parcel {p.get('parcel_id')}"
    total = p.get("total_value") or 0
    if key == "assessed value" and total:
        return (f"On {addr} the county's appraised total is ${total:,.0f}; the tax bill "
                f"is figured on 20% of that, about ${total*0.2:,.0f}.")
    if key == "parcel" and p.get("parcel_id"):
        return f"{addr} is parcel {p['parcel_id']} in Garland County."
    if key == "flood zone":
        return (f"{addr} shows flood zone {p['flood_zone']}."
                if p.get("flood_zone") else
                f"We have not checked the flood zone on {addr} yet.")
    if key == "zoning":
        return (f"We have not confirmed zoning on {addr} - that is on the task list."
                if not p.get("zoning") else f"{addr} is zoned {p['zoning']}.")
    if key == "lien":
        return (f"Nobody has pulled the records on {addr} yet, so we do not know "
                f"whether anything is attached to it.")
    if key in ("noi", "cap rate", "cash-on-cash") and p.get("imp_value"):
        return (f"Run the rental numbers on {addr} in the Money tab to see this one "
                f"with real inputs.")
    return None
