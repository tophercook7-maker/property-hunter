"""An RSS feed of ready-to-post items, for Make (or anything else) to read.

Make has a built-in RSS trigger: point it at a feed, give it a schedule, and it
posts each new item without anyone touching it. That is the piece that was
missing -- the hunting and the sending were already automatic, but posting
needed a human with a Meta credential.

Each item is a finished post, not a headline to rewrite: the live count of what
the State is selling, the share image, and the link. Numbers come from the same
published data the site shows, so the feed cannot claim something the site does
not. A new item appears when the numbers have actually moved, so Make does not
post the same thing twice.

    python3 tools/build_feed.py      # -> docs/feed.xml
"""
from __future__ import annotations

import html, json, os
from datetime import datetime, timezone
from email.utils import format_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
OUT = os.path.join(DOCS, "feed.xml")
STATE = os.path.join(ROOT, "data", "feed_state.json")
SITE = "https://tophercook7-maker.github.io/property-hunter"
MAX_ITEMS = 20


def load(p, d):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def figures() -> dict:
    sl = load(os.path.join(DOCS, "data", "state_lands.json"), {"listings": []})
    live = [x for x in (sl.get("listings") or [])
            if not x.get("excluded_area") and x.get("starting_bid")]
    radar = load(os.path.join(DOCS, "data", "radar.json"), {})
    rc = radar.get("counties") or {}
    import collections
    cnt = collections.Counter(x.get("county") for x in live)
    return {
        "for_sale": len(live),
        "under_500": sum(1 for x in live if x["starting_bid"] <= 500),
        "with_building": sum(1 for x in live if (x.get("improvements") or 0) > 0),
        "new_this_week": sum(len(c.get("new") or []) for c in rc.values()),
        "left_this_week": sum(len(c.get("gone") or []) for c in rc.values()),
        "top_counties": [k.title() for k, _ in cnt.most_common(4) if k],
    }


def post_text(f: dict) -> str:
    counties = ", ".join(f["top_counties"])
    return (
        f"The State of Arkansas is selling {f['for_sale']:,} lots right now.\n\n"
        f"{f['under_500']:,} of them start under $500. {f['with_building']:,} have a building on them.\n\n"
        "The State's listing tells you the bid. It doesn't tell you about the city lien, the flood "
        "zone, the road that isn't there, or the owner who already redeemed last month.\n\n"
        "I built something that checks all of it and writes down what each source said, with the date "
        "it said it. When a source is down, it says so instead of pretending. All 75 counties — every "
        "parcel in Arkansas.\n\n"
        "Free to look. Type any Arkansas address.\n\n"
        f"{SITE}/\n\n"
        f"Most of these are land, not houses. Heaviest right now in {counties}."
    )


def main() -> int:
    f = figures()
    if not f["for_sale"]:
        raise SystemExit("refusing to publish a post with no listings behind it")

    now = datetime.now(timezone.utc)
    prev = load(STATE, {"items": []})
    items = prev.get("items", [])
    # Only a real move in the numbers earns a new post; otherwise Make would
    # repost the same sentence every time it checked.
    sig = f"{f['for_sale']}-{f['under_500']}-{f['with_building']}"
    if not items or items[0].get("sig") != sig:
        items.insert(0, {"sig": sig, "at": now.isoformat(timespec="seconds"),
                         "title": f"{f['for_sale']:,} Arkansas lots the State is selling",
                         "text": post_text(f)})
        items = items[:MAX_ITEMS]
        json.dump({"items": items}, open(STATE, "w"), indent=1)

    def item_xml(it):
        when = datetime.fromisoformat(it["at"])
        return f"""  <item>
    <title>{html.escape(it['title'])}</title>
    <link>{SITE}/</link>
    <guid isPermaLink="false">ph-{it['sig']}</guid>
    <pubDate>{format_datetime(when)}</pubDate>
    <description>{html.escape(it['text'])}</description>
    <enclosure url="{SITE}/share.png" type="image/png" length="0"/>
  </item>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Property Hunter — ready to post</title>
  <link>{SITE}/</link>
  <description>Finished posts about what the State of Arkansas is selling. Each item is ready to publish as written; the image is in the enclosure.</description>
  <language>en-us</language>
  <lastBuildDate>{format_datetime(now)}</lastBuildDate>
{chr(10).join(item_xml(i) for i in items)}
</channel></rss>
"""
    open(OUT, "w").write(xml)
    print(f"wrote {OUT} ({len(items)} item(s))")
    print(f"  newest: {items[0]['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
