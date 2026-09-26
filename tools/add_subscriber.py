"""Put a person on the brief list.

    python3 tools/add_subscriber.py --list
    python3 tools/add_subscriber.py someone@example.com --counties GARLAND,SALINE --name "Jane" --area "Hot Springs" --note "from the site"
    python3 tools/add_subscriber.py someone@example.com --plan radar
    python3 tools/add_subscriber.py someone@example.com --remove

signup.html posts to FormSubmit, which lands in topher@mixedmakershop.com --
it does not reach this machine. This is the step in between: read the email,
run this, and the person is on the list that tools/send_radar.py sends to.

data/radar_subscribers.json is gitignored and stays that way. It holds real
people's email addresses and belongs on this Mac only.

Default plan is "brief": the free weekly one. "radar" and "desk" are the paid
tiers and are only set by hand or by tools/sync_radar_subscribers.py.
"""
from __future__ import annotations

import argparse, json, os, re, sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBS = os.path.join(ROOT, "data", "radar_subscribers.json")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PLANS = ("brief", "radar", "desk")


def load() -> list:
    if not os.path.exists(SUBS):
        return []
    try:
        d = json.load(open(SUBS))
        return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        raise SystemExit(f"{SUBS} is not valid JSON; fix or delete it before adding anyone")


def save(rows: list) -> None:
    os.makedirs(os.path.dirname(SUBS), exist_ok=True)
    json.dump(rows, open(SUBS, "w"), indent=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("email", nargs="?")
    ap.add_argument("--counties", default="GARLAND")
    ap.add_argument("--plan", default="brief", choices=PLANS)
    ap.add_argument("--name", default=None)
    ap.add_argument("--area", default=None, help="what they told you they cover")
    ap.add_argument("--note", default=None, help="where they came from, what they asked for")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    rows = load()

    if a.list or not a.email:
        if not rows:
            print("nobody on the list yet.")
            print(f"  file: {SUBS}")
            print("  add someone:  python3 tools/add_subscriber.py them@example.com --counties GARLAND")
            return 0
        print(f"{len(rows)} on the list ({SUBS})\n")
        for r in rows:
            print(f"  {r.get('email','?'):38s} {r.get('plan','brief'):6s} "
                  f"{','.join(r.get('counties') or []):24s} {r.get('name') or ''} {('· ' + r['note']) if r.get('note') else ''}")
        return 0

    email = a.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise SystemExit(f"that does not look like an email address: {email!r}")

    idx = next((i for i, r in enumerate(rows) if (r.get("email") or "").lower() == email), None)

    if a.remove:
        if idx is None:
            print(f"{email} was not on the list; nothing removed.")
            return 0
        rows.pop(idx)
        save(rows)
        print(f"removed {email}. {len(rows)} left.")
        return 0

    counties = [c.strip().upper() for c in a.counties.split(",") if c.strip()]
    rec = {"email": email, "plan": a.plan, "counties": counties,
           "added_at": datetime.now(timezone.utc).date().isoformat()}
    for k, v in (("name", a.name), ("area", a.area), ("note", a.note)):
        if v:
            rec[k] = v

    if idx is None:
        rows.append(rec)
        print(f"added {email} ({a.plan}, {', '.join(counties)}). {len(rows)} on the list.")
    else:
        rec["added_at"] = rows[idx].get("added_at", rec["added_at"])
        rows[idx] = {**rows[idx], **rec}
        print(f"updated {email} ({a.plan}, {', '.join(counties)}). {len(rows)} on the list.")
    save(rows)
    print("\npreview what they will get:  python3 tools/send_radar.py")
    print("send for real:               python3 tools/send_radar.py --send")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
