"""Pull Radar / Desk subscribers from Stripe into data/radar_subscribers.json.

    python3 tools/sync_radar_subscribers.py

Reads completed Checkout Sessions for the two payment links (live mode) through the
Stripe CLI already logged in on this Mac, keeps the ones whose subscription is still
active, and records email, plan, and the "Counties" answer from checkout. Nothing is
charged or changed in Stripe; this only reads.
"""
import hashlib, json, os, re, secrets, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALT_FILE = os.path.join(ROOT, "data", "radar_salt")          # never committed
MEMBERS = os.path.join(ROOT, "docs", "data", "members.json")  # hashes only, committed


def salt():
    if not os.path.exists(SALT_FILE):
        open(SALT_FILE, "w").write(secrets.token_hex(24))
    return open(SALT_FILE).read().strip()


def key_for(email):
    """A subscriber's personal link key: stable per email, unguessable without the salt."""
    return hashlib.sha256((salt() + "|" + email.lower()).encode()).hexdigest()[:24]
LINKS = os.path.join(ROOT, "data", "stripe_links.json")
OUT = os.path.join(ROOT, "data", "radar_subscribers.json")
KNOWN = {"ARKANSAS","ASHLEY","BAXTER","BENTON","BOONE","BRADLEY","CALHOUN","CARROLL","CHICOT","CLARK","CLAY","CLEBURNE","CLEVELAND","COLUMBIA","CONWAY","CRAIGHEAD","CRAWFORD","CRITTENDEN","CROSS","DALLAS","DESHA","DREW","FAULKNER","FRANKLIN","FULTON","GARLAND","GRANT","GREENE","HEMPSTEAD","HOT SPRING","HOWARD","INDEPENDENCE","IZARD","JACKSON","JEFFERSON","JOHNSON","LAFAYETTE","LAWRENCE","LEE","LINCOLN","LITTLE RIVER","LOGAN","LONOKE","MADISON","MARION","MILLER","MISSISSIPPI","MONROE","MONTGOMERY","NEVADA","NEWTON","OUACHITA","PERRY","PHILLIPS","PIKE","POINSETT","POLK","POPE","PRAIRIE","PULASKI","RANDOLPH","ST FRANCIS","SALINE","SCOTT","SEARCY","SEBASTIAN","SEVIER","SHARP","STONE","UNION","VAN BUREN","WASHINGTON","WHITE","WOODRUFF","YELL"}


def st(*args):
    out = subprocess.run(["stripe", *args, "--live"], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(out.stderr[:300])
    return json.loads(out.stdout)


def counties_from(text):
    t = (text or "").upper().replace("COUNTY", "")
    found = [c for c in KNOWN if re.search(r"\b" + re.escape(c) + r"\b", t)]
    return sorted(found) or ["GARLAND"]


def main():
    links = json.load(open(LINKS)) if os.path.exists(LINKS) else {}
    plinks = {}
    for pl in st("payment_links", "list", "--limit", "100")["data"]:
        for name, url in links.items():
            if pl["url"] == url:
                plinks[pl["id"]] = name
    subs = {}
    for plid, name in plinks.items():
        sessions = st("checkout", "sessions", "list", "-d", f"payment_link={plid}", "--limit", "100")["data"]
        for s in sessions:
            if s.get("status") != "complete" or not s.get("subscription"):
                continue
            sub = st("subscriptions", "retrieve", s["subscription"])
            if sub.get("status") not in ("active", "trialing"):
                continue
            email = ((s.get("customer_details") or {}).get("email") or "").lower()
            if not email:
                continue
            answer = next((cf.get("text", {}).get("value") for cf in s.get("custom_fields", []) if cf.get("key") == "counties"), "")
            subs[email] = {"email": email, "plan": "desk" if "Desk" in name else "radar",
                           "counties": counties_from(answer), "counties_raw": answer,
                           "since": s.get("created"), "subscription": sub["id"]}
    existing = {}
    if os.path.exists(OUT):
        try:
            existing = {x["email"]: x for x in json.load(open(OUT))}
        except Exception:
            existing = {}
    for e, x in existing.items():           # hand-added subscribers stay
        if x.get("manual") and e not in subs:
            subs[e] = x
    for x in subs.values():
        x["key"] = key_for(x["email"])
    json.dump(sorted(subs.values(), key=lambda x: x["email"]), open(OUT, "w"), indent=1)
    members = [{"hash": hashlib.sha256(x["key"].encode()).hexdigest(), "plan": x["plan"], "counties": x.get("counties", [])}
               for x in subs.values()]
    json.dump({"members": members, "count": len(members)}, open(MEMBERS, "w"), separators=(",", ":"))
    print(f"{len(subs)} active subscriber(s) -> {OUT}; hashes -> {MEMBERS}")


if __name__ == "__main__":
    main()
