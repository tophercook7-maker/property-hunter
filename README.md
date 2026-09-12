# TOPHER PROPERTY HUNTER

A personal real-estate acquisition intelligence system for **Garland County, Arkansas**,
excluding **Hot Springs Village** and **Diamondhead**.

It looks for property that might be worth buying, investigates it, explains it in
ordinary English, ranks it, watches it for changes, and tells you what to check next.

It is a research assistant. It does not buy anything, offer on anything, contact
anybody, or spend a dollar.

```bash
./run.sh                 # http://127.0.0.1:8234
```

---

## The one principle

Everything in here separates:

| | |
|---|---|
| **WHAT WE KNOW** | a source said it, and we kept the source, the date and the link |
| **WHAT WE THINK** | we noticed a pattern and labelled it an observation |
| **WHAT WE ESTIMATE** | arithmetic on assumptions, labelled as such, with a caveat |
| **WHAT WE DON'T KNOW** | said out loud, on the property page, in the score, in the AI answer |
| **WHAT WE NEED TO VERIFY** | a task, pointing at the exact office or web page |

That matters more than any feature here. A source that fails reports that it failed.
It never returns an empty success.

---

## What data is real

Everything in the app is real public data unless a card says `DEMO DATA`. Nothing is
seeded, invented or padded.

### Read automatically

| Source | What it gives us |
|---|---|
| **Arkansas GIS Office — statewide parcels (CAMP)** | The backbone. 76,651 Garland County parcels straight from the county assessor's tax roll: owner of record, situs address, land / improvement / total assessed value, acreage, subdivision, legal description, parcel-type code, parcel polygon, and the date the county data was current. |
| **US Census TIGERweb** | The official Hot Springs Village CDP and Diamondhead city boundary polygons — so the exclusion is point-in-polygon, not a hopeful text match. |
| **FEMA National Flood Hazard Layer** | The mapped flood zone at a parcel's centroid. |
| **Arkansas GIS Office — building footprints** | Whether a building is actually standing there, and roughly how big its footprint is. |
| **Arkansas GIS Office — 911 road centerlines** | Whether a mapped road touches the parcel and what class it is, including highway aliases (Central Ave is also AR 7). ~0.4 s a lookup. |
| **Arkansas GIS Office — aerial imagery** | 2023 9-inch and 2017 1-foot orthoimagery exported per parcel, so every dossier opens on a real then-and-now aerial with its source and year. |
| **Arkansas GIS Office — 1 m elevation model** | Slope and aspect at the parcel, feeding the land and storage scores. |
| **OpenStreetMap (Overpass)** | What businesses and traffic anchors are nearby (competition for a food stand). |
| **City of Hot Springs GIS — vacant-structure register** | The City's own list of vacant structures (~250 parcels), read as polygons with the City's edit date. 19 of the 21 investigation seeds are on it today. |
| **City of Hot Springs GIS — housing / cleanup / demolition liens** | ~263 parcels with lien type, amount, date, and the clerk's notes on water, sewer and vacancy. |
| **City of Hot Springs GIS — 2025 code-enforcement cases** | ~159 addressed cases with status (In Progress / Complied / …) and filed / closed dates. |
| **City of Hot Springs GIS — zoning (2024 update) & overlays** | The zoning district and ordinance at the parcel, plus historic districts, the Malvern overlay, planned-development districts and Opportunity Zones; also the RPID via the address join. |
| **City of Hot Springs GIS — water meters & sewer mains** | Whether a City water meter sits at the address and whether a sewer main runs nearby. |
| **City of Hot Springs GIS — city-owned property** | Parcels the City itself owns, and whether it marks them vacant. |
| **City of Hot Springs GIS — county roll copy with owner mailing address** | Where the tax bill goes. Out of state, out of county or a PO box is recorded as an *absentee owner* observation; a bill addressed to the property itself as probably owner-occupied. |

The City publishes these as open ArcGIS feature services under a use-at-your-own-risk
disclaimer, which is quoted on every piece of evidence taken from them.

### A human has to look

These are **not** broken adapters. They are places where a machine either cannot
lawfully read the data or cannot read it at all. Each one links to the right page and
raises a **MANUAL VERIFICATION REQUIRED** task you can complete and attach evidence to.

| Source | Why |
|---|---|
| **Garland County Assessor (actDataScout)** | Answers automated requests with HTTP 403. We do not work around that. |
| **Garland County Tax Collector** | The Collector's inquiry portal (arkansastaxsearch.com) is a login-gated session application and the assessor portal (ARCountyData) sits behind a browser challenge. Neither is bypassed. Tax status is too important to guess. |
| **Commissioner of State Lands (COSL)** | Certified-delinquent parcels and auctions live in an interactive catalogue and a separate auction site. |
| **Hot Springs — confirm vacancy / condemnation** | The register is read automatically; whether a condemnation or demolition *order* is pending is only known to the office. Low priority. |
| **Hot Springs — lien payoff & pre-2025 code history** | Lien amounts are read automatically; the payoff with interest and older cases are not. Low priority. |
| **Garland County Circuit Clerk (deeds, liens)** | Title is the legal truth and reading it is a judgement call. This is exactly where not to guess. |
| **Hot Springs Planning & Zoning — confirm the use** | The district is read automatically; whether *your* use is permitted by right, conditional, or not is Planning's call. Low priority. |
| **Public listing portals / MLS** | Their terms prohibit automated collection. We don't scrape them. |

We honour `robots.txt`, identify ourselves with a real User-Agent, rate-limit every
host, and never attempt to defeat an access control or a CAPTCHA.

---

## What it does

**Dashboard** — what changed, what's new, what's distressed, and three Topher Picks
with the reason, the risk, the unknowns and the next step for each.

**Scan** — a real pipeline with twelve stages. Every stage is work against a real
source; the progress bar is the backend's actual state. Presets: investigation seeds,
distress sweep, cheap land, rental candidates, business sites, or the whole county.

**Map** — dark, street or aerial. Markers coloured by what they are. The excluded areas
are drawn in red so you can see the rule being enforced.

**Property dossier** — snapshot, distress signals with how to confirm each one, why it
might be cheap, deal-or-trap, what to do next, every piece of evidence with its source
and date, the score breakdown line by line, live money sliders, use analysis, timeline,
changes, tasks, field notes and the AI briefing.

**Money** — rental model (NOI, cap rate, cash-on-cash, DSCR, break-even rent, ten-year
projection, sensitivity), a maximum-purchase-price analyzer that works backwards from
the finished value, a storage build-out sketch and a snow-cone site model.

**Investigate** — eighteen stages per property. Each either does real work or says
plainly that only a human can answer it, and leaves the task behind.

**Keep looking on its own** — a scheduler runs the scan on an interval you set
(default every 24 h), shows the real next-run time on the dashboard, and files the
morning briefing under Alerts when it finishes.

**Field mode** — on the property page: add your own photos (tagged street / inspection /
before / during / after), record a voice note in the browser (transcribed locally by
`whisper` if it is installed, otherwise you type it), attach documents by category,
mark as visited. Everything you bring back is stored as an UNVERIFIED observation with
your name on it — a neighbour's remark never becomes a fact.

**Own it** — once you actually buy one: purchase, loan and value tracking; renovation
projects with budget-vs-actual per task; a money ledger; leases; and before / during /
after photos lined up side by side. "My properties" totals it into a portfolio view.

**Register records find their parcel** — a City register polygon says where a property
is, not which parcel it is. The scan's `parcel_ids` stage does one point-in-polygon lookup
against the City's roll copy per parcel-less property and learns the parcel id, owner,
values and mailing address; when the county record already exists, the register record
is folded onto it with its history intact.

**Coming off a register** — every scan re-reads the City's vacancy, lien and code
registers. A property that carried one of those records last time and is not on the
register now gets an observation, a timeline event and an alert ("came off the
vacant-structure register") — phrased as what was observed, because the layer does not
say whether it was resolved, demolished, sold or paid off.

**Fuzzy search** — "111 Isabel Street", "Malvurn" or "Tucker Aquisitions" still find the
right property; the response says what it matched on.

**Look at it** — a local vision model (llava through Ollama) reads the aerials and your
own photos. Every output is stored as AI_OPINION at LOW confidence and phrased as a
possibility — "possible roof concern visible", never a fact, never a dollar figure.
It miscounts buildings often enough to prove the point; it is a prompt to go and look.

**Ask** — `GET /api/ask?q=...` is the door for Daniel. "What changed?", "What should I
investigate?", "Show me everything under $50,000", "Find me land for storage", "Why did
the score change on property 63?" are routed deterministically to the same functions the
UI uses and come back as structured results plus a sentence.

**Near me** — give it your location (or coordinates) and it lists what we track around
you, nearest first, with open / watch / visited / directions on each.

**Watch** — priority, target price, desired use and notes on every watched property,
shown on its card. **❓ What's this?** sits beside every unfamiliar term in the dossier
and money tab and explains it using the property you are looking at.

**Learning from passes** — when you pass on something and say why, ranked lists later
sink properties carrying the same kind of problem, and the list says so in a banner.
Scores never change, nothing is hidden, and it can be switched off.

**Sources & Health** — whether the scanner is actually working, and which parts of the
job a machine is not allowed to do.

**Glossary** — every term in plain English, with this property as the example.

---

## Scoring

Nine separate scores, never one blended number: `overall`, `rental`, `resale`, `land`,
`storage`, `business`, `workshop`, `snowcone`, `risk`.

Every score shows its working — each line with its points and a plain sentence — plus
the list of things nobody has checked, which is what drives the confidence rating.
Weights live in `hunter/config.py`.

---

## The AI

Local only, through Ollama. It is shown the stored evidence and nothing else, it is
told to say "I don't know", and answers are cached. If no model is running, the app
falls back to a plain readout of the evidence and says so.

Asking for honesty is not the same as enforcing it, so there is a **guard**: every
dollar amount, year and square footage in the model's answer is checked against the
evidence it was shown. Anything it was never given is cut and replaced with
`[figure not in our evidence - removed]`, and the page says how many figures were cut.

**Then vs now.** The 2017 and 2023 aerials are compared pixel-wise after a robust
exposure match. The result is a CALCULATION at LOW confidence ("looks much the same",
"noticeable change near the parcel") and, above a threshold, a timeline event and an
alert. It says *that* something changed, never what.

**Command line.** `python -m hunter.cli ask "what should I investigate"`, `status`,
`picks`, `scan`, `briefing`, `explain <id>`, with `--json` for scripts. Goes through the
running app if there is one, otherwise straight to the database.

---

## Human approval

The system never makes an offer, signs, spends, borrows, contacts a seller, sends a
message, transfers property or publishes anything. Those actions exist only as approval
requests that sit as `pending` until you decide.

---

## Adding a county

Geography is configuration. Add an entry to `TERRITORIES` in `hunter/config.py` with
the county FIPS, centre and bounding box, and an `EXCLUSIONS` entry if there is
somewhere you never want to look. Saline County is already there, switched off
(`active: False`), and a live test proves the same parcel adapter reads its ~40,000
parcels and that the Hot Springs Village exclusion — which straddles the county line —
still applies. The API refuses to scan a territory that is not switched on. A new
state needs a new parcel adapter implementing `PropertySource`.

---

## Integration (Daniel / AI Hub)

The API is the product; the web UI only consumes it. Property Hunter runs as its own
service and does not touch the AI Hub repository.

```
GET  /api/status                      counts, scan state, AI state
GET  /api/briefing                    the morning report
GET  /api/picks?limit=3               Topher picks with reasons
GET  /api/properties?...              filter, sort, paginate
GET  /api/search/natural?q=...        "land under $20,000 with road frontage"
GET  /api/map                         markers + exclusion polygons
GET  /api/property/{id}               the full dossier
GET  /api/property/{id}/explain       plain-English briefing
GET  /api/property/{id}/memo          investment memo
POST /api/property/{id}/investigate   the 18-stage investigation
POST /api/property/{id}/financials    rental | deal | storage | snowcone | rehab
POST /api/compare                     which one is better, and why
POST /api/scan                        start a scan
GET  /api/scan/stream                 server-sent events, real stage state
GET  /api/sources                     what is working and what needs a human
GET  /api/health                      green / yellow / red
GET  /api/changes, /api/alerts, /api/tasks
```

```
GET  /api/schedule, POST /api/schedule   interval / mode / on-off; POST /run-now
POST /api/property/{id}/photo|voice|document   multipart uploads (field mode)
GET  /api/property/{id}/report.pdf       via local headless Chrome; 501 if absent
POST /api/property/{id}/ledger|lease     portfolio bookkeeping
GET/POST /api/decisions/learning         pass reasons, how they are used, on/off
GET  /api/ask?q=...                      plain question -> intent + structured answer
GET  /api/near?lat&lon&radius_m          nearest tracked properties (field mode)
POST /api/property/{id}/imagery          pull the 2017/2023 aerials + slope
POST /api/photo/{id}/analyse             local vision read, stored as LOW-confidence opinion
GET/POST/DELETE /api/filters/saved       saved list filters
```

Full interactive docs at `/docs` while the app is running.

---

## Tests

```bash
python3 -m pytest              # offline suite
python3 -m pytest -m live      # hits the real public services
```

The suite includes the adversarial cases: real Diamondhead parcels pulled live and
checked that every one is excluded; "Blacksnake Village Ests" and "Diamond Springs
Estates" checked that they are *not*; two adjacent parcels sharing an address and a
centroid checked that they never merge; a source failure checked that it records as
unavailable rather than as zero results; a grep over the whole codebase checking that
nothing anywhere claims paying somebody's delinquent taxes makes you the owner.

---

## Not legal advice

This is research and opinion. Anything involving title, deeds, tax sales, liens,
contracts or zoning gets verified with the appropriate government office and an
Arkansas real-estate attorney before money moves.
