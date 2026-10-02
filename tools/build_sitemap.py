"""Generate the sitemap from what is actually publishable.

The hand-written one dated from 14 September: nine URLs, missing the home page
and eleven other pages including everything built since, and listing three
pages that carry noindex. Google had indexed the GitHub repository and not the
site, so a person searching found source code instead of the tool.

A page is listed when it exists and does not say noindex. That rule is the
whole file, so a page added next month is in the sitemap without anyone
remembering to add it.

    python3 tools/build_sitemap.py      # -> docs/sitemap.xml
"""
from __future__ import annotations

import datetime, glob, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
OUT = os.path.join(DOCS, "sitemap.xml")
SITE = "https://tophercook7-maker.github.io/property-hunter"

# how often each page genuinely changes, and what matters most
DAILY = {"index.html", "state-lands.html", "discover.html", "foreclosures.html", "weekly.html"}
PRIORITY = {"index.html": "1.0", "lookup.html": "0.9", "state-lands.html": "0.9",
            "foreclosures.html": "0.8", "owners.html": "0.8", "divestitures.html": "0.8",
            "samples.html": "0.8", "get-a-file.html": "0.8", "hunt.html": "0.7",
            "taxes.html": "0.7", "signup.html": "0.7"}


def main() -> int:
    urls, skipped = [], []
    for p in sorted(glob.glob(os.path.join(DOCS, "*.html"))):
        n = os.path.basename(p)
        s = open(p, encoding="utf-8", errors="replace").read()
        if re.search(r'name=["\']robots["\'][^>]*noindex', s):
            skipped.append(n)
            continue
        mod = datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat()
        loc = f"{SITE}/" if n == "index.html" else f"{SITE}/{n}"
        urls.append((loc, mod, "daily" if n in DAILY else "weekly",
                     PRIORITY.get(n, "0.5")))

    body = "\n".join(
        f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{mod}</lastmod>\n"
        f"    <changefreq>{freq}</changefreq>\n    <priority>{pri}</priority>\n  </url>"
        for loc, mod, freq, pri in urls)
    open(OUT, "w").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n")
    print(f"wrote {OUT}: {len(urls)} URLs, {len(skipped)} skipped as noindex")
    print("  skipped:", ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
