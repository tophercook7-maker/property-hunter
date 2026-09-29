"""The picture a link shows when somebody shares it.

Every page carried og:title and og:description and no og:image, so a link to
this site pasted into Facebook, a text message or a group post rendered as bare
text -- the same defect the Century 21 audit charged them for.

The card is built from the site's own published data and rendered in the site's
own typeface, so it cannot drift into claiming something the site does not. The
numbers on it are the ones that do not go stale in a week: how many parcels are
covered, how many counties, how many the State is selling. A specific lot at a
specific price belongs in the ad copy where it can be checked the morning it
runs, not baked into an image that outlives it.

    python3 tools/build_share_image.py        # -> docs/share.png, 1200x630

Rendered with the headless Chrome already used for the site audits, so there is
no new dependency.
"""
from __future__ import annotations

import json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
OUT = os.path.join(DOCS, "share.png")
W, H = 1200, 630

CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Chromium.app/Contents/MacOS/Chromium")


def load(name, default):
    try:
        with open(os.path.join(DOCS, "data", name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def figures() -> dict:
    owners = load("owners_state.json", {})
    sl = load("state_lands.json", {"listings": []})
    idx = load("scan_index.json", {"counties": {}})
    div = load("divestitures.json", {})
    counties = idx.get("counties") or {}
    scored = sum(c.get("n") or 0 for c in counties.values())
    listings = [x for x in (sl.get("listings") or []) if not x.get("excluded_area")]
    return {
        "parcels": (owners.get("totals") or {}).get("parcels") or scored,
        "counties": len(counties) or 75,
        "for_sale": len(listings),
        "acres_watched": round((div.get("totals") or {}).get("acres_held") or 0),
    }


def html(f: dict) -> str:
    n = lambda v: f"{v:,}"
    return f"""<!doctype html><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{width:{W}px;height:{H}px;background:#E9ECEF;color:#172029;
       font-family:"IBM Plex Sans",Helvetica,Arial,sans-serif;display:flex;
       align-items:center;justify-content:center}}
  .card{{width:1104px;height:534px;background:#fff;border:1px solid #D3DAE0;
        padding:44px 52px;display:flex;flex-direction:column;justify-content:space-between}}
  .eyebrow{{font-family:"IBM Plex Mono",Menlo,monospace;font-size:17px;letter-spacing:.14em;
           text-transform:uppercase;color:#0E7C73}}
  h1{{font-family:"Barlow Condensed","Arial Narrow",sans-serif;font-weight:700;font-size:92px;
     line-height:.94;letter-spacing:.005em;margin:14px 0 10px}}
  .sub{{font-size:25px;color:#5F6E7B;max-width:62ch;line-height:1.36}}
  .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
  .stat{{border:1px solid #D3DAE0;background:#fff;padding:14px 16px}}
  .stat b{{display:block;font-family:"Barlow Condensed",sans-serif;font-weight:700;
          font-size:47px;line-height:1}}
  .stat span{{display:block;font-size:12.5px;letter-spacing:.07em;text-transform:uppercase;
             color:#5F6E7B;margin-top:5px}}
  .foot{{display:flex;justify-content:space-between;align-items:flex-end;
        border-top:1px solid #D3DAE0;padding-top:16px}}
  .foot .src{{font-family:"IBM Plex Mono",monospace;font-size:14.5px;color:#5F6E7B;max-width:74ch;line-height:1.5}}
  .tag{{font-family:"IBM Plex Mono",monospace;font-size:13px;letter-spacing:.06em;
       text-transform:uppercase;border:1px solid #0E7C73;color:#0E7C73;padding:4px 10px;white-space:nowrap}}
</style>
<div class="card">
  <div>
    <div class="eyebrow">Arkansas · public records</div>
    <h1>What the record says<br>about a parcel.</h1>
    <p class="sub">Taxes, the State&rsquo;s own sale history, flood, road access, liens, vacancy,
      owner of record &mdash; and, plainly, what has not been checked.</p>
  </div>
  <div class="grid">
    <div class="stat"><b>{n(f['parcels'])}</b><span>Parcels covered</span></div>
    <div class="stat"><b>{n(f['counties'])}</b><span>Counties</span></div>
    <div class="stat"><b>{n(f['for_sale'])}</b><span>On the State sale</span></div>
    <div class="stat"><b>{n(f['acres_watched'])}</b><span>Acres watched</span></div>
  </div>
  <div class="foot">
    <div class="src">Every figure carries the source that said it and the date it was said.<br>
      Nothing here is legal, tax, appraisal or investment advice.</div>
    <div class="tag">Property Hunter</div>
  </div>
</div>"""


def main() -> int:
    chrome = next((c for c in CHROME if os.path.exists(c)), None)
    if not chrome:
        raise SystemExit("no Chrome found to render with; install Chrome or render the card by hand")
    f = figures()
    if not f["parcels"]:
        raise SystemExit("refusing to build a card with no data behind it")
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "card.html")
        with open(page, "w") as fh:
            fh.write(html(f))
        shot = os.path.join(tmp, "share.png")
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={W},{H}", f"--screenshot={shot}",
                        "--virtual-time-budget=4000", f"file://{page}"],
                       check=True, capture_output=True, timeout=120)
        if not os.path.exists(shot):
            raise SystemExit("Chrome produced no screenshot")
        shutil.move(shot, OUT)
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024:.0f} KB, {W}x{H})")
    print(f"  {f['parcels']:,} parcels · {f['counties']} counties · "
          f"{f['for_sale']:,} on the State sale · {f['acres_watched']:,} acres watched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
