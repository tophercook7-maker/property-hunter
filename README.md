# Property Hunter

Real-estate acquisition research for Garland County, Arkansas, built on public records only.

**Live site:** https://tophercook7-maker.github.io/property-hunter/

- `docs/index.html` — **Arkansas parcel lookup.** Type an address, owner or parcel number for any of the 75 counties, or click a parcel on the map. Owner of record, county appraised and assessed values, acreage, deed reference, FEMA flood zone, and for Hot Springs the vacant-structure register, City liens, 2025 code cases, zoning and mailing address. Everything is queried live from the State and City ArcGIS services in your browser; nothing is stored.
  Each record ends with **Before you buy**: automatic cross-checks (does the City register entry actually name this parcel, do the State and City agree on the owner, deed age, value sanity), a six-item confirmation checklist for the things no feed can do (taxes, State Lands, title, condition, use, money), the concrete routes to buy that parcel (owner-direct, City first, State Lands sale, listing), an offer-prep sheet, and an owner letter that only drafts once the taxes, title, condition and money boxes are ticked. The site never sends anything; you mail the letter.
- `docs/state-lands.html` — **every Arkansas parcel the Commissioner of State Lands is selling for unpaid taxes** (about 1,500 across 37 counties), joined to the county roll for owner, address, appraised value and acreage, with an aerial of each, what is owed, bids, and for Garland the year the taxes stopped. Rebuilt daily by a GitHub Action from the public COSL feed into `docs/data/state_lands.json`. The lookup tool checks every parcel against it.
  Each county on that page also shows **what the State's tax-sale parcels actually sold for** (sold / redeemed counts, median debt and price, who is buying) from COSL's monthly deed reports (`tools/build_cosl_history.py` → `docs/data/history/<COUNTY>.json`, refreshed Mondays), and every listing gets a comparable-sales table.
- `docs/campaign.html` — **owner letter campaign** for the Garland scan: pick who to write to (absentee, vacant register, City lien, estate, land or building, value band), see why each owner might let the property go, print a truthful letter plus reply card per owner addressed to where the tax bill goes, and track status and notes. Excludes Hot Springs Village / Diamondhead (POA dues) and State-certified parcels (a signed deed does not stop the tax sale). Nothing is sent from the page.
- `docs/garland.html` — a snapshot of the Garland County scan: 1,400+ distressed-signal properties with scores and 21 full investigations.
- `hunter/` — the local scanner / investigator app (FastAPI + SQLite). `python3 -m uvicorn hunter.api:app --port 8234`.

## Honesty rules
Every figure is tagged fact / calculation / observation / unknown with its source. Values are the county's **appraised** figures (the assessor's opinion), never sale prices. Delinquent taxes, deeds and State Lands status need a login or a visit, so they are linked, not scraped. Sources are read within their published terms; nothing bypasses a login or a CAPTCHA. Paying someone else's delinquent taxes does not make you the owner.

## Rebuild the snapshot
```
python3 tools/build_share.py
```
