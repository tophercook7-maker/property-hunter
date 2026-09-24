#!/usr/bin/env python3
"""The weekly brief: what changed on the Arkansas public record in the last seven days, from the site's own
public data files (signals.json, state_lands.json, samples/index.json). Writes:
  docs/weekly.html            the public page (static; the same brief everyone gets)
  ~/Desktop/Property-Hunter-Weekly-Brief.md   the email body to paste and send (BCC the list)
No network, no database writes. Run it Monday: python3 tools/weekly_brief.py
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape as _esc


def esc(v) -> str:
    return _esc(str(v if v is not None else ""))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
SITE = "https://tophercook7-maker.github.io/property-hunter"
EVENT_WORDS = {"NEW_TAX_SALE": "entered the State tax sale", "STATE_SOLD": "sold at the State sale", "STATE_REDEEMED": "redeemed by the owner", "STATE_LEFT": "left the State inventory",
               "NEW_LIEN": "new City lien", "NEW_VACANCY_RECORD": "new vacancy-register record", "NEW_CODE_CASE": "new code case", "VERIFIED_DELINQUENCY": "delinquent at the county (verified)"}


def load(name, default):
    try:
        return json.load(open(os.path.join(DATA, name)))
    except Exception:
        return default


def build() -> dict:
    sig = load("signals.json", {})
    rows = sig.get("rows") or []
    sl = load("state_lands.json", {})
    samples = load(os.path.join("..", "samples", "index.json"), {"samples": []}).get("samples", [])
    tax = load("tax_sources.json", {})
    status = load("status.json", {})
    built = sig.get("built_at") or utc()
    by_event = Counter(r["event"] for r in rows)
    by_county = defaultdict(list)
    for r in rows:
        by_county[r.get("cn") or r.get("cf")].append(r)
    listings = sl.get("listings") or []
    new_listings = sorted([x for x in listings if (x.get("added") or "") >= (datetime.now(timezone.utc).strftime("%Y-%m-%d"))[:8] + "01"], key=lambda x: x.get("starting_bid") or 0)[:10]
    cheapest = sorted([x for x in listings if x.get("starting_bid") and x.get("parcel_id")], key=lambda x: x["starting_bid"])[:8]
    cp = status.get("countypay") or {}
    unavailable = [c["county"] for c in (tax.get("counties") or {}).values() if (c.get("sources") or {}).get("countypay", {}).get("status") in ("TEMPORARILY_UNAVAILABLE", "BLOCKED")]
    return {"built_at": built, "window_days": sig.get("window_days"), "total": len(rows), "by_event": dict(by_event), "by_county": {k: v for k, v in sorted(by_county.items(), key=lambda kv: -len(kv[1]))},
            "cheapest": cheapest, "samples": samples[-5:], "collector": cp, "unavailable_counties": unavailable[:12], "listing_count": len(listings)}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="minutes")


def markdown(b: dict) -> str:
    L = [f"# Property Hunter weekly brief — {b['built_at'][:10]}", "",
         f"What changed on the Arkansas public record in the last {b['window_days'] or 7} days: {b['total']} recorded events. Every line names its source; nothing here is advice.", ""]
    L.append("## By the numbers")
    for ev, n in sorted(b["by_event"].items(), key=lambda kv: -kv[1]):
        L.append(f"- {n} parcels {EVENT_WORDS.get(ev, ev.lower().replace('_', ' '))}")
    L.append(f"- {b['listing_count']:,} parcels in the State tax-sale inventory today")
    L += ["", "## By county (most activity first)"]
    for cn, rs in list(b["by_county"].items())[:10]:
        c = Counter(r["event"] for r in rs)
        L.append(f"- **{cn}**: " + ", ".join(f"{n} {EVENT_WORDS.get(e, e)}" for e, n in c.most_common()))
    L += ["", "## Cheapest State-sale parcels right now (starting bid)"]
    for x in b["cheapest"]:
        L.append(f"- ${x['starting_bid']:,.2f} — {x.get('address') or 'no situs'}, {x.get('city') or ''} ({x.get('county', '').title()} County) · parcel {x['parcel_id']} · {x.get('sale_type_text', '')}")
    L += ["", "## Files published this week"]
    for s in b["samples"]:
        L.append(f"- {s['title']} — {s['headline']} · {SITE}/sample.html?file={s['slug']}")
    L += ["", "## What the tool could not check"]
    if b["collector"].get("open") is False:
        L.append(f"- County Collector portal (CountyPay): unavailable since {str(b['collector'].get('down_since', ''))[:10]} — tax states in those counties are honestly UNKNOWN, not 'current'.")
    if b["unavailable_counties"]:
        L.append("- Collector source unavailable or blocked: " + ", ".join(b["unavailable_counties"]) + (" …" if len(b["unavailable_counties"]) >= 12 else ""))
    L += ["", f"Look up any parcel: {SITE}/lookup.html · The State sale by county: {SITE}/state-lands.html · Get a file on a parcel you are looking at: {SITE}/get-a-file.html", "",
          "Reply 'stop' and you are off the list. Reply with a county and I will add its changes next week."]
    return "\n".join(L)


def html(b: dict) -> str:
    def li(items):
        return "".join(f"<li>{i}</li>" for i in items)
    ev = li(f"<b>{n}</b> parcels {esc(EVENT_WORDS.get(e, e.lower().replace('_', ' ')))}" for e, n in sorted(b["by_event"].items(), key=lambda kv: -kv[1]))
    counties = li(f"<b>{esc(str(cn))}</b>: " + ", ".join(f"{n} {esc(EVENT_WORDS.get(e, e))}" for e, n in Counter(r['event'] for r in rs).most_common()) for cn, rs in list(b["by_county"].items())[:12])
    cheap = li(f"<b>${x['starting_bid']:,.2f}</b> — {esc(x.get('address') or 'no situs')}, {esc(x.get('city') or '')} ({esc(str(x.get('county', '')).title())} County) · parcel <code>{esc(x['parcel_id'])}</code> · {esc(x.get('sale_type_text', ''))}" + (f" · <a href=\"{esc(x['listing_url'])}\" target=\"_blank\" rel=\"noopener\">State listing</a>" if x.get("listing_url") else "") for x in b["cheapest"])
    samples = li(f"<a href=\"sample.html?file={esc(s['slug'])}\">{esc(s['title'])}</a> — {esc(s['headline'])}" for s in b["samples"])
    failures = []
    if b["collector"].get("open") is False:
        failures.append(f"County Collector portal (CountyPay): unavailable since {esc(str(b['collector'].get('down_since', ''))[:10])}. Tax states in those counties are honestly UNKNOWN, not \"current\".")
    if b["unavailable_counties"]:
        failures.append("Collector source unavailable or blocked in: " + esc(", ".join(b["unavailable_counties"])))
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Weekly Brief · Property Hunter</title>
<meta name="description" content="What changed on the Arkansas public record this week: State tax-sale entries, redemptions, sales, City liens, with sources and dates.">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<link rel="stylesheet" href="ph.css">
<style>
main{{max-width:820px;margin:0 auto;padding:16px 16px 70px;display:grid;gap:18px}}
h2{{font-size:28px;text-transform:uppercase;letter-spacing:.02em;margin:0;text-wrap:balance}}
h3{{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:0 0 6px;font-family:var(--body);font-weight:600}}
.box{{background:var(--surface);border:1px solid var(--rule);padding:14px 16px;font-size:14px}}
.box ul{{margin:0;padding-left:18px;display:grid;gap:5px}}
.lead{{font-size:14px;color:var(--muted);max-width:70ch}}
code{{font-family:var(--mono);font-size:12.5px}}
.cta{{display:flex;gap:8px;flex-wrap:wrap}}
</style>
</head>
<body>
<header class="ph-top"><h1><small>What changed on the Arkansas public record · sources and dates on everything · nothing here is advice</small>Weekly brief</h1><nav data-nav aria-label="Sections"></nav></header>
<main>
  <section><h2>Week ending {esc(b['built_at'][:10])}</h2><p class="lead">{b['total']} recorded events in the last {esc(str(b['window_days'] or 7))} days across the State tax-sale inventory and the City of Hot Springs registers. {b['listing_count']:,} parcels are in the State inventory today.</p>
  <div class="cta"><a class="btn" href="signup.html">Get this by email every Monday</a><a class="btn ghost" href="state-lands.html">Browse the State sale by county</a></div></section>
  <section class="box"><h3>By the numbers</h3><ul>{ev or '<li>No recorded changes in the window.</li>'}</ul></section>
  <section class="box"><h3>By county, most activity first</h3><ul>{counties or '<li>None.</li>'}</ul></section>
  <section class="box"><h3>Cheapest State-sale parcels right now, by starting bid</h3><ul>{cheap}</ul><p class="lead" style="margin:8px 0 0">A starting bid is the State's number, not a value. Every one of these has open questions; a Property File lists them.</p></section>
  <section class="box"><h3>Files published</h3><ul>{samples or '<li>None this week.</li>'}</ul></section>
  <section class="box"><h3>What the tool could not check</h3><ul>{li(failures) or '<li>Every automated source answered this week.</li>'}</ul></section>
  <section class="cta"><a class="btn ghost" href="get-a-file.html">Get a file on a parcel you are looking at</a><a class="btn ghost" href="feedback.html">Tell me what to add</a></section>
</main>
<script src="nav.js"></script>
</body>
</html>
'''


if __name__ == "__main__":
    b = build()
    open(os.path.join(ROOT, "docs", "weekly.html"), "w").write(html(b))
    md = markdown(b)
    out = os.path.expanduser("~/Desktop/Property-Hunter-Weekly-Brief.md")
    try:
        open(out, "w").write(md)
    except OSError:
        out = os.path.join(ROOT, "data", "weekly-brief.md"); open(out, "w").write(md)
    print(f"docs/weekly.html written; email body at {out}; {b['total']} events, {len(b['cheapest'])} cheapest listings, {len(b['samples'])} samples")
