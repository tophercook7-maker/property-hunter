"""Send the free weekly brief to everyone on the brief list.

    python3 tools/send_brief.py            # dry run: prints the email, sends nothing
    python3 tools/send_brief.py --send     # sends, one message per person
    python3 tools/send_brief.py --send --to someone@example.com   # just that one

The brief itself is built by tools/weekly_brief.py from the published data
files, so the email and https://tophercook7-maker.github.io/property-hunter/weekly.html
say the same thing. This only addresses and delivers it.

One message per person, never a shared To: line -- a BCC slip exposes the list,
and the list is real people's email addresses.

Recipients are plan == "brief" in data/radar_subscribers.json (gitignored).
Paid Radar and Desk subscribers get the county email from tools/send_radar.py
instead, so nobody is sent both.
"""
from __future__ import annotations

import argparse, json, os, re, sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hunter import notify                      # noqa: E402
import tools.weekly_brief as wb                # noqa: E402

SUBS = os.path.join(ROOT, "data", "radar_subscribers.json")


def to_text(md: str) -> str:
    """The brief is written as markdown for the site; email gets it as plain text."""
    out = []
    for line in md.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        if line.startswith("# "):
            t = line[2:].strip()
            out += [t.upper(), "=" * len(t)]
        elif line.startswith("## "):
            t = line[3:].strip()
            out += ["", t.upper(), "-" * len(t)]
        elif line.startswith("- "):
            out.append("  * " + line[2:])
        else:
            out.append(line)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--to", default=None, help="send to one address only")
    a = ap.parse_args()

    b = wb.build()
    body = to_text(wb.markdown(b))
    sale = b.get("sale") or {}
    subject = (f"Arkansas tax sale, week of {sale.get('week_start') or b['built_at'][:10]}"
               f" - {sale.get('new', 0)} in, {sale.get('gone', 0)} out")

    subs = json.load(open(SUBS)) if os.path.exists(SUBS) else []
    people = [s for s in subs if (s.get("plan") or "brief") == "brief"]
    if a.to:
        people = [s for s in people if (s.get("email") or "").lower() == a.to.lower()] or [{"email": a.to}]

    if not people:
        print("nobody on the brief list yet.")
        print("  add someone:  python3 tools/add_subscriber.py them@example.com")
        print(f"\n--- what they would get ---\nSUBJECT: {subject}\n\n{body}")
        return 0

    print(f"{len(people)} recipient(s) · subject: {subject}\n")
    if not a.send:
        print(f"--- DRY RUN, nothing sent ---\n{body}\n")
        print("recipients: " + ", ".join(s["email"] for s in people))
        print("\nsend for real: python3 tools/send_brief.py --send")
        return 0

    sent = 0
    for s in people:
        try:
            notify._smtp_send(s["email"], subject, body)
            sent += 1
            print("  sent ->", s["email"])
        except Exception as exc:
            print("  FAILED ->", s["email"], exc)
    print(f"\n{sent} of {len(people)} sent.")
    return 0 if sent == len(people) else 1


if __name__ == "__main__":
    raise SystemExit(main())
