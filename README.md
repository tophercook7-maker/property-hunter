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
| **OpenStreetMap (Overpass)** | Road class at the parcel (a traffic proxy), and what businesses are nearby. |

### A human has to look

These are **not** broken adapters. They are places where a machine either cannot
lawfully read the data or cannot read it at all. Each one links to the right page and
raises a **MANUAL VERIFICATION REQUIRED** task you can complete and attach evidence to.

| Source | Why |
|---|---|
| **Garland County Assessor (actDataScout)** | Answers automated requests with HTTP 403. We do not work around that. |
| **Garland County Tax Collector** | Delinquency is behind a search form, not a feed. Tax status is too important to guess. |
| **Commissioner of State Lands (COSL)** | Certified-delinquent parcels and auctions live in an interactive catalogue and a separate auction site. |
| **Hot Springs vacant-structure records** | Published through department pages and agendas, not a database. |
| **Hot Springs code enforcement & cleanup liens** | Board agendas and department records. |
| **Garland County Circuit Clerk (deeds, liens)** | Title is the legal truth and reading it is a judgement call. This is exactly where not to guess. |
| **Hot Springs Planning & Zoning** | Zoning is decided by the City, never inferred from a map. |
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
falls back to a plain readout of the evidence and says so. It never invents an owner,
a price, a tax amount, a lien, a zoning district or a source.

---

## Human approval

The system never makes an offer, signs, spends, borrows, contacts a seller, sends a
message, transfers property or publishes anything. Those actions exist only as approval
requests that sit as `pending` until you decide.

---

## Adding a county

Geography is configuration. Add an entry to `TERRITORIES` in `hunter/config.py` with
the county FIPS, centre and bounding box, and an `EXCLUSIONS` entry if there is
somewhere you never want to look. The Arkansas parcel adapter already covers every
Arkansas county; a new state needs a new parcel adapter implementing `PropertySource`.

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
