"""Command line for Property Hunter (spec 50 / 82).

Daniel, a cron job, or Topher in a terminal can use the same brain without
touching HTTP:

    python -m hunter.cli ask "what should I investigate"
    python -m hunter.cli status
    python -m hunter.cli picks --limit 5
    python -m hunter.cli scan --mode cheap_land --limit 300
    python -m hunter.cli briefing
    python -m hunter.cli explain 63
    python -m hunter.cli --json ask "find me land for storage"

Everything is the same code path the web app uses. If the web app is running
on this machine the CLI goes through it (so a scan shows up live in the UI);
otherwise it talks to the database directly.
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap

import httpx

from .config import HOST, PORT


def _server() -> str | None:
    base = f"http://{HOST}:{PORT}"
    try:
        httpx.get(f"{base}/api/health", timeout=2)
        return base
    except Exception:
        return None


def _local():
    from . import db
    from .sources import register_all
    db.init_db()
    register_all()


def cmd_ask(a):
    base = _server()
    if base:
        r = httpx.get(f"{base}/api/ask", params={"q": a.question, "use_ai": a.ai},
                      timeout=300).json()
    else:
        _local()
        from .ask import ask
        r = ask(a.question, use_ai=a.ai)
    if a.json:
        return r
    print(f"[{r['intent']}] {r['answer']}")
    for x in (r.get("results") or [])[:8]:
        if isinstance(x, dict) and x.get("address"):
            print(f"  #{x.get('id')}  {x['address']:32} {x.get('recommendation') or '':14} "
                  f"score {x.get('overall') if x.get('overall') is not None else x.get('score')}")
    return None


def cmd_status(a):
    base = _server()
    if base:
        r = httpx.get(f"{base}/api/status", timeout=10).json()
    else:
        _local()
        from .api import api_status
        r = api_status()
    if a.json:
        return r
    c = r["counts"]
    print(f"{r['app']} v{r['version']} - {r['territory']['label']}")
    print(f"  properties {c['properties']}  excluded {c['excluded']}  distressed {c['distressed']}"
          f"  lots {c['cheap_lots']}  rental candidates {c['rental_candidates']}")
    print(f"  watchlist {c['watchlist']}  open tasks {c['open_tasks']}  unread alerts {c['alerts']}")
    print(f"  scan: {'RUNNING' if r['scan']['running'] else 'idle'}; last {r['scan']['last']} "
          f"({r['scan']['last_status']}); next {r['scan']['next_scheduled'] or 'not scheduled'}")
    print(f"  AI: {r['ai']['model'] or 'no local model'}  ({'via ' + base if base else 'direct'})")
    return None


def cmd_picks(a):
    base = _server()
    if base:
        r = httpx.get(f"{base}/api/picks", params={"limit": a.limit}, timeout=30).json()
    else:
        _local()
        from .analyzers import topher_picks
        r = {"picks": topher_picks(a.limit)}
    if a.json:
        return r
    for i, p in enumerate(r["picks"], 1):
        print(f"{i}. {p['address']}  score {p['score']:.0f} / risk {p['risk']:.0f}  "
              f"{p['recommendation']}")
        for w in p["why"][:2]:
            print(f"     + {w}")
        print(f"     ? {', '.join(p['unknown'][:3]) or 'nothing flagged'}")
        print(f"     > {p['next_step']}")
    return None


def cmd_scan(a):
    base = _server()
    if base:
        r = httpx.post(f"{base}/api/scan", json={"mode": a.mode, "limit": a.limit,
                                                 "enrich_top": a.enrich_top}, timeout=30).json()
        if a.json:
            return r
        print("started" if r.get("started") else f"not started: {r.get('reason')}")
        print(f"  watch it at {base}/#scan")
        return None
    _local()
    from .scanner import Scan
    s = Scan(mode=a.mode, limit=a.limit, enrich_top=a.enrich_top)
    s.save()
    out = s.run()
    if a.json:
        return out
    for st in out["stages"]:
        print(f"  {st['status']:12} {st['label']:48} {st['detail'][:60]}")
    for f in out["funnel"]:
        print(f"  {f['value']:>7,}  {f['label']}")
    return None


def cmd_briefing(a):
    base = _server()
    if base:
        r = httpx.get(f"{base}/api/briefing", timeout=30).json()
    else:
        _local()
        from .reports import morning_report
        r = morning_report()
    if a.json:
        return r
    print(f"{r['greeting']} {r['headline']}")
    print(f"  {len(r['new_opportunities'])} new opportunities; "
          f"{len(r['important_changes'])} important changes")
    if r.get("best_deal"):
        b = r["best_deal"]
        print(f"  investigate first: {b['address']} (score {b['score']:.0f})")
    for x in r.get("avoid", [])[:2]:
        print(f"  avoid: {x['address']} (risk {x['score']:.0f})")
    for i, t in enumerate(r["next_three_actions"], 1):
        print(f"  {i}. {t['title']}" + (f" - {t['property']}" if t.get("property") else ""))
    return None


def cmd_investigate(a):
    base = _server()
    payload = {"ids": a.ids} if a.ids else {"top": a.top}
    if a.all:
        payload = {"top": 5000}
    if base:
        r = httpx.post(f"{base}/api/investigate/batch", json=payload, timeout=30).json()
        if a.json:
            return r
        print("started" if r.get("started") else f"not started: {r.get('reason')}")
        if r.get("started"):
            print(f"  {r['count']} properties; PDFs land in {r['folder']}")
            print(f"  progress: {base}/#props  or  python -m hunter.cli status")
        return None
    _local()
    from . import batch
    import time
    r = batch.start(a.ids or [p["id"] for p in __import__("hunter.api", fromlist=["api_properties"])
                    .api_properties(limit=5000 if a.all else a.top)["properties"]])
    while batch.is_running():
        s = batch.status()
        print(f"  {s['done'] + s['failed']}/{s['total']}  {s['current'] or ''}", end="\r")
        time.sleep(2)
    s = batch.status()
    print(f"\ninvestigated {s['done']} ({s['failed']} failed) -> {s['folder']}")
    return None


def cmd_explain(a):
    base = _server()
    if base:
        r = httpx.get(f"{base}/api/property/{a.id}/explain", timeout=600).json()
    else:
        _local()
        from .analyzers import explain
        from .store import get_property
        p = get_property(a.id)
        if not p:
            print("no such property", file=sys.stderr)
            return 2
        r = explain(p, use_ai=not a.no_ai)
    if a.json:
        return r
    print(textwrap.fill(r["text"], 96, replace_whitespace=False))
    if r.get("guard_note"):
        print(f"\n[guard] {r['guard_note']}")
    print(f"\n({r.get('model') or r.get('source')})")
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="property-hunter",
                                 description="Topher Property Hunter - command line")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ask", help="ask a plain-English question")
    s.add_argument("question")
    s.add_argument("--ai", action="store_true", help="let the local model interpret search phrasing")
    s.set_defaults(fn=cmd_ask)
    sub.add_parser("status", help="counts and scan state").set_defaults(fn=cmd_status)
    s = sub.add_parser("picks", help="Topher picks")
    s.add_argument("--limit", type=int, default=3)
    s.set_defaults(fn=cmd_picks)
    s = sub.add_parser("scan", help="run a scan")
    s.add_argument("--mode", default="distress")
    s.add_argument("--limit", type=int, default=400)
    s.add_argument("--enrich-top", type=int, default=10)
    s.set_defaults(fn=cmd_scan)
    sub.add_parser("briefing", help="the morning report").set_defaults(fn=cmd_briefing)
    s = sub.add_parser("investigate", help="run the full investigation on many properties")
    s.add_argument("ids", nargs="*", type=int, help="property ids (default: the top N)")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--all", action="store_true", help="every property (hours)")
    s.set_defaults(fn=cmd_investigate)
    s = sub.add_parser("explain", help="plain-English briefing on one property")
    s.add_argument("id", type=int)
    s.add_argument("--no-ai", action="store_true")
    s.set_defaults(fn=cmd_explain)
    a = ap.parse_args(argv)
    out = a.fn(a)
    if isinstance(out, int):
        return out
    if out is not None:
        print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
