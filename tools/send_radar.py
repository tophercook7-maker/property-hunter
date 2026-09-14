"""Send the Monday Tax Sale Radar email to subscribers.

    python3 tools/send_radar.py            # dry run: prints each email, sends nothing
    python3 tools/send_radar.py --send     # sends through the Gmail app password on this Mac

Subscribers live in data/radar_subscribers.json (never in the repo):
    [{"email": "someone@example.com", "counties": ["GARLAND", "SALINE"], "plan": "radar"}]
Each email is built from docs/data/radar.json for that person's counties: new
listings, sold or redeemed, bids, best value per dollar owed, plus the county's
sold-price history. Plain text, every figure from the public record.
"""
import json, os, sys
from datetime import date
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter import notify  # noqa: E402

SUBS = os.path.join(ROOT, "data", "radar_subscribers.json")
RADAR = os.path.join(ROOT, "docs", "data", "radar.json")
SITE = "https://tophercook7-maker.github.io/property-hunter"


def money(v):
    return "—" if v is None else f"${v:,.0f}"


def county_block(name, c):
    s = c["summary"]
    L = [f"{name.title()} County", "=" * (len(name) + 7),
         f"{s['outside_village']} for sale outside the Village · {s['with_building']} with a building · "
         f"{s['new_this_week']} new this week · {s['gone_this_week']} sold or redeemed · {s['with_bids']} carrying a bid"]
    if s.get("history"):
        h = s["history"]
        L.append(f"Since Jan 2025 the State sold {h['sold']} here; owners redeemed {h['redeemed']}; median price {money(h.get('median_price'))}, "
                 f"top {money(h.get('max_price'))}.")
    def line(x, tag=""):
        addr = (x.get("address") or (x.get("subdivision") or "") + " lot").strip().title() or "no situs address"
        b = f" · building {money(x['improvements'])}" if (x.get("improvements") or 0) > 0 else " · lot"
        bid = f" · bid {money(x['current_bid'])}" if (x.get("current_bid") or 0) > 0 else ""
        return f"  * {addr} - {money(x.get('appraised'))} appraised{b} - {money(x.get('starting_bid'))} owed{bid}{tag}\n    {x.get('listing_url','')}"
    if c["new"]:
        L += ["", "NEW THIS WEEK"] + [line(x) for x in c["new"][:15]]
    if c["gone"]:
        L += ["", "SOLD OR REDEEMED"] + [f"  * State # {g['rpid']} - {g['how']}" + (f" ({g['detail'].get('buyer') or g['detail'].get('owner','')}, {money(g['detail'].get('price') or g['detail'].get('paid'))})" if g.get("detail") else "") for g in c["gone"][:15]]
    if c["bids"]:
        L += ["", "SOMEBODY IS BIDDING"] + [line(x) for x in c["bids"][:10]]
    if c["best_value"]:
        L += ["", "MOST HOUSE FOR THE DEBT (appraised ÷ owed)"] + [line(x, f" · {round((x.get('appraised') or 0)/max(x.get('starting_bid') or 1,1))}×") for x in c["best_value"][:8]]
    L += ["", f"Full county page with aerials and comps: {SITE}/state-lands.html?county={name}"]
    return "\n".join(L)


def compose(sub, radar):
    counties = [c.upper() for c in sub.get("counties") or ["GARLAND"]]
    blocks = [county_block(c, radar["counties"][c]) for c in counties if c in radar["counties"]]
    subject = f"Tax Sale Radar - {date.today().strftime('%b %-d')} - " + ", ".join(c.title() for c in counties)
    body = "\n\n".join([f"TAX SALE RADAR - week of {radar['week_start']}",
                        "What changed in the State of Arkansas' tax-sale inventory, from the Commissioner of State Lands' "
                        "public feed and the county tax roll. Values are the county's appraised figures, not sale prices. "
                        "You buy on the State's site with your own account; there is a 90-day litigation period after any sale. "
                        "Not legal, tax or investment advice."] + blocks +
                       [(f"Your member link (opens every county, CSV export on): {SITE}/pro.html?key={sub['key']}\n" if sub.get("key") else "") +
                        f"Lookup any parcel: {SITE}/lookup.html\nQuestions or to stop: reply to this email."])
    return subject, body


def main():
    send = "--send" in sys.argv
    subs = json.load(open(SUBS)) if os.path.exists(SUBS) else []
    radar = json.load(open(RADAR))
    if not subs:
        print(f"no subscribers yet ({SUBS} is empty or missing); sample email for Garland follows\n")
        subs = [{"email": "preview@example.com", "counties": ["GARLAND"]}]
        send = False
    for sub in subs:
        subject, body = compose(sub, radar)
        if send:
            notify._smtp_send(sub["email"], subject, body)
            print("sent to", sub["email"], "-", subject)
        else:
            print("TO:", sub["email"]); print("SUBJECT:", subject); print(body); print("-" * 60)


if __name__ == "__main__":
    main()
