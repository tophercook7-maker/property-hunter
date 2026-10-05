"""A visitor counter you own, on your own Mac.

    python3 tools/hitcounter.py            # serve on 127.0.0.1:8792
    python3 tools/hitcounter.py --report   # read the counts, no server

The site is static on GitHub Pages, which keeps no access log anyone can read.
Cloudflare Web Analytics would answer that and needs an account token; this
answers it with the tunnel already running on this machine and needs nothing.

WHAT IT RECORDS, AND WHAT IT REFUSES TO
Recorded: the day, the page path, the referring HOST, and a coarse device word
(phone / tablet / desktop / bot). That is enough to answer "is anyone reading
this, what are they reading, and how did they arrive".

Never recorded: the IP address, the full user agent, the full referring URL, or
any identifier. There is no cookie and nothing that follows a person between
visits. That means this counts PAGE VIEWS and cannot count unique people -- a
limit worth having rather than engineering around, and stated on the report
instead of guessed at.

Counting only happens while this Mac is awake. A gap in the data is a gap in
the uptime, not a quiet week, and the report says which days it actually heard
from the world.
"""
from __future__ import annotations

import argparse, json, os, re, sqlite3, sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "hits.db")
PORT = 8792
MAX_BODY = 2048

# facebookexternalhit, Slackbot-LinkExpanding, WhatsApp and the rest fetch a URL
# to build a link preview. They were being counted as desktop visitors, which
# turns "I posted and seven people came" into a sentence that is not true.
BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|curl|wget|python-|headless|preview|fetch|"
    r"facebookexternalhit|facebookcatalog|meta-externalagent|whatsapp|"
    r"slackbot|slack-imgproxy|twitterbot|linkedinbot|discordbot|telegrambot|"
    r"embedly|quora link preview|redditbot|applebot|pinterest|skypeuripreview|"
    r"vkshare|w3c_validator|google-inspectiontool|chrome-lighthouse", re.I)
PHONE_RE = re.compile(r"iphone|android(?!.*tablet)|mobile", re.I)
TABLET_RE = re.compile(r"ipad|tablet", re.I)


def device_of(ua: str) -> str:
    if not ua:
        return "unknown"
    if BOT_RE.search(ua):
        return "bot"
    if TABLET_RE.search(ua):
        return "tablet"
    if PHONE_RE.search(ua):
        return "phone"
    return "desktop"


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS hits(
                   id INTEGER PRIMARY KEY, day TEXT NOT NULL, at TEXT NOT NULL,
                   path TEXT, ref_host TEXT, device TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hits_day ON hits(day)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hits_path ON hits(day, path)")
    c.commit()
    return c


def record(path: str, referrer: str, ua: str) -> None:
    now = datetime.now(timezone.utc)
    host = ""
    if referrer:
        try:
            host = (urlparse(referrer).hostname or "")[:80]
        except ValueError:
            host = ""
    c = connect()
    with c:
        c.execute("INSERT INTO hits(day, at, path, ref_host, device) VALUES(?,?,?,?,?)",
                  (now.date().isoformat(), now.isoformat(timespec="seconds"),
                   (path or "/")[:120], host, device_of(ua)))
    c.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "ph-counter"

    def log_message(self, *a):            # the point is not to keep an access log
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204)

    def do_POST(self):
        if not self.path.startswith("/_c/h"):
            return self._send(404)
        try:
            n = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
            payload = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except (ValueError, TypeError):
            payload = {}
        record(str(payload.get("p") or "/"), str(payload.get("r") or ""),
               self.headers.get("User-Agent") or "")
        self._send(204)

    def do_GET(self):
        if self.path.startswith("/_c/stats"):
            return self._send(200, json.dumps(summary()).encode(), "application/json")
        if self.path.startswith("/_c/health"):
            return self._send(200, b"ok")
        self._send(404)


def summary(days: int = 30) -> dict:
    c = connect()
    q = lambda s, p=(): c.execute(s, p).fetchall()
    total = q("SELECT COUNT(*) FROM hits")[0][0]
    by_day = [{"day": d, "views": n} for d, n in
              q("SELECT day, COUNT(*) FROM hits GROUP BY day ORDER BY day DESC LIMIT ?", (days,))]
    out = {
        "total_views": total,
        "days_with_any_traffic": len(by_day),
        "by_day": by_day,
        "by_path": [{"path": p, "views": n} for p, n in
                    q("SELECT path, COUNT(*) FROM hits GROUP BY path ORDER BY COUNT(*) DESC LIMIT 25")],
        "by_device": [{"device": d, "views": n} for d, n in
                      q("SELECT device, COUNT(*) FROM hits GROUP BY device ORDER BY COUNT(*) DESC")],
        "by_referrer": [{"host": h or "(typed or unknown)", "views": n} for h, n in
                        q("SELECT ref_host, COUNT(*) FROM hits GROUP BY ref_host ORDER BY COUNT(*) DESC LIMIT 15")],
        "note": ("Page views, not people. Nothing here identifies a visitor: no IP, no cookie, "
                 "no identifier, so the same person reading three pages is three views. "
                 "Counting only happens while this Mac is awake."),
    }
    c.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    if a.report:
        s = summary()
        print(f"  {s['total_views']:,} page views across {s['days_with_any_traffic']} day(s) with traffic")
        if not s["total_views"]:
            print("  Nothing recorded yet.")
            return 0
        print("\n  by day:");    [print(f"    {d['day']}  {d['views']:,}") for d in s["by_day"][:10]]
        print("\n  most read:"); [print(f"    {p['views']:5,}  {p['path']}") for p in s["by_path"][:10]]
        print("\n  device:");    [print(f"    {d['views']:5,}  {d['device']}") for d in s["by_device"]]
        print("\n  arrived from:"); [print(f"    {r['views']:5,}  {r['host']}") for r in s["by_referrer"][:8]]
        print(f"\n  {s['note']}")
        return 0
    connect().close()
    print(f"counter on 127.0.0.1:{a.port}  ->  {DB}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
