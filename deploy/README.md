# Hosting the app somewhere other than the Mac

What runs where today:
- **Public site** (samples, State sale, lookup, brief, signup, feedback): GitHub Pages. Free. Stays there.
- **Licensed app** (Find, Property File, workup, research, outreach, Bee): `python -m uvicorn hunter.api:app --port 8234` on the Mac. Only reachable from the Mac.
- **Daily hunt + publish loop**: `tools/publish_scan.py --loop 90` on the Mac. Reads the State roll, State Lands, the City layers; commits and pushes data files.

## The smallest hosted setup that lets a paying user run their own file

One small Linux VM, one container, one domain name.

| piece | choice | cost |
|---|---|---|
| VM | 2 vCPU / 4 GB / 40 GB (Hetzner CX22, DigitalOcean basic, Linode) | $4–$8 per month |
| domain | the real one you pick | $10–$15 per year |
| TLS | Caddy, automatic | $0 |
| database | SQLite on the VM's disk (the file is ~2 GB with 2.2 M evidence rows; fine) | $0 |
| backups | nightly `sqlite3 .backup` to object storage | $1–$2 per month |
| email for codes/receipts | the mixedmakershop address you already have | $0 |
| Bee (AI) | OFF on the VM. Either keep Bee on the Mac only, or later a GPU box (~$0.50/hour when on) | $0 now |

**About $6–$10 a month all-in** until Bee or traffic grows. A coffee a week.

What does NOT move to the VM in step one: the daily hunt. It reads 75 counties of the State roll and is happy on the Mac. The VM only needs a copy of the database; ship it nightly (`rsync` the SQLite file after a `.backup`) until the hunt itself moves.

## Steps (when the domain exists)
1. `docker compose -f deploy/docker-compose.yml build`
2. Copy `data/property_hunter.db` and the two secret files in `data/` (`license_pepper`, `admin_token`) into the `ph-data` volume.
3. Put the real domain in `deploy/Caddyfile`; point the domain's A record at the VM.
4. `docker compose -f deploy/docker-compose.yml up -d`
5. In `docs/ph-auth.js` and `docs/ph.js`, set `PH_API_BASE` / `PH.API` to `https://app.<domain>` and add that origin to the CORS list in `hunter/api.py`.
6. Generate codes on the VM: `docker compose exec app python -m hunter.licensing generate --type SINGLE_USER`.

## Paywall, when the 25 signups say yes
The gate already exists (P5.5): one code activates one device; every licensed route refuses without a session. What is missing is only the money step: a Stripe Checkout link per plan whose webhook calls `hunter.licensing.create` and emails the code. Stripe is already set up on the mixedmakershop account. Two days of work, no new architecture.
