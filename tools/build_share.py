"""Build the shareable, self-contained Property Hunter website from the live database.

    python3 tools/build_share.py

Writes hunter/static/share.html (served at /share) and a copy on the Desktop.
The page carries real owner names from the public tax roll - it is Topher's
call where it gets shared; this script never publishes anything.
"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q, q1  # noqa: E402

DESKTOP = os.path.expanduser("~/Desktop/🏠 Property Hunter/SHARE - Property Hunter website.html")


def _latest(field):
    out = {}
    for r in q("SELECT property_id, value, raw_ref FROM evidence WHERE field=? ORDER BY id", (field,)):
        out[r["property_id"]] = dict(r)
    return out


def _money(v):
    try:
        return float(re.sub(r"[^0-9.]", "", str(v)) or 0)
    except ValueError:
        return 0.0


def conflict_change_ids() -> dict:
    """Change rows that are NOT the world changing. Returns {change_id: kind}.

    'conflict'  the source disagreeing with itself: recorded within ten minutes of the
                parcel's first sighting (the roll returned two records under one parcel
                number and the second overwrote the first), or a flip-flop A -> B -> A
                inside the week (two roll records taking turns).
    'sources'   two sources of different vintage: the old value only ever came from a
                different source than the one that wrote the new value (the City's roll
                copy said X on its date; the State roll says Y on its date).
    Either way the site must say CONFLICTING or SOURCES DISAGREE, never "owner changed"."""
    rows = q("""SELECT c.id, c.property_id, c.field, c.old_value, c.new_value, c.source,
                       (julianday(c.detected_at)-julianday(p.first_seen))*1440 AS mins
                FROM changes c JOIN properties p ON p.id=c.property_id
                WHERE c.detected_at > datetime('now','-7 days') AND c.severity IN ('medium','high')""")
    out = {r["id"]: "conflict" for r in rows if r["mins"] is not None and r["mins"] < 10}
    series = {}
    for r in rows:
        series.setdefault((r["property_id"], r["field"]), []).append(r)
    for rs in series.values():
        pairs = {(r["old_value"], r["new_value"]) for r in rs}
        if any((b, a) in pairs for a, b in pairs):
            out.update({r["id"]: "conflict" for r in rs})
    # Sources name the same fact differently (total_value / total_assessed_value / assessed_total), so match
    # the OLD value itself, in any field, and ask which sources ever reported it for this property.
    for r in rows:
        if r["id"] in out or not r["old_value"]:
            continue
        srcs = {e["source"] for e in q("SELECT DISTINCT source FROM evidence WHERE property_id=? AND value=?",
                                       (r["property_id"], r["old_value"]))}
        if srcs and r["source"] not in srcs:
            out[r["id"]] = "sources"
        elif not srcs and r["field"] in ("owner_name", "total_value", "imp_value", "land_value"):
            # nobody on record ever reported the old value with the writing source: the row was seeded by another
            # source whose evidence used a different name. Not a change we can stand behind.
            other = q1("SELECT 1 FROM evidence WHERE property_id=? AND source<>? LIMIT 1", (r["property_id"], r["source"]))
            if other:
                out[r["id"]] = "sources"
    return out


def export_rows():
    mail, vac, code = _latest("owner_mailing_address"), _latest("vacant_structure"), _latest("code_case_open")
    taxbill, taxchk, taxcosl = _latest("tax_bill"), _latest("tax_status_check"), _latest("tax_delinquent")
    liens = {}
    for r in q("SELECT property_id, value, raw_ref FROM evidence WHERE field='cleanup_lien_amount'"):
        liens.setdefault(r["property_id"], {})[r["raw_ref"] or r["value"]] = r["value"]
    sc, lines, conf = {}, {}, {}
    for r in q("SELECT property_id, kind, score, confidence, breakdown_json FROM scores"):
        sc.setdefault(r["property_id"], {})[r["kind"]] = r["score"]
        if r["kind"] == "overall":
            conf[r["property_id"]] = r["confidence"]
            try:
                b = json.loads(r["breakdown_json"] or "{}")
                lines[r["property_id"]] = [{"p": l.get("points"), "r": (l.get("reason") or "")[:90]}
                                           for l in sorted(b.get("lines", []), key=lambda l: -abs(l.get("points") or 0))[:8]]
            except Exception:
                pass
    chg = {}
    conflicts = conflict_change_ids()
    for r in q("""SELECT id, property_id, field, old_value, new_value, severity, detected_at FROM changes
                  WHERE detected_at > datetime('now','-7 days') AND severity IN ('medium','high')
                  AND field NOT IN ('improved','acreage','property_type','register_attachment','building_sqft') ORDER BY id DESC"""):
        chg.setdefault(r["property_id"], []).append({"f": r["field"], "o": (r["old_value"] or "")[:60], "n": (r["new_value"] or "")[:60],
                                                     "sev": r["severity"], "at": (r["detected_at"] or "")[:10],
                                                     **({"k": conflicts[r["id"]]} if r["id"] in conflicts else {})})
    inv = {}
    for r in q("SELECT * FROM investigations WHERE status='complete' ORDER BY finished_at"):
        s = json.loads(r["summary_json"] or "{}")
        finds, seen = [], set()
        for stage in json.loads(r["stages_json"] or "[]"):
            for f in stage.get("findings", [])[:2]:
                t = f.get("text", "")
                if t and "MANUAL VERIFICATION" not in t and t not in seen:
                    seen.add(t)
                    finds.append({"stage": stage["label"].split(" - ")[0], "text": t, "type": f.get("type"),
                                  "conf": f.get("confidence"), "src": f.get("source_name") or f.get("source")})
        dt = s.get("deal_or_trap", {})
        inv[r["property_id"]] = {"verdict": dt.get("verdict"), "why": dt.get("why"), "stops": dt.get("hard_stops"),
                                 "conf": dt.get("confidence"), "open": s.get("open_questions"),
                                 "next": s.get("next_steps"), "at": (r["finished_at"] or "")[:16],
                                 "findings": finds[:14]}
    rows, labels = [], {}
    county_name = {t["county_fips"]: t["county"] for t in __import__("hunter.config", fromlist=["TERRITORIES"]).TERRITORIES}
    for p in q("SELECT * FROM properties WHERE excluded=0 AND data_class='real'"):
        pid = p["id"]
        dist = json.loads(p["distress_json"] or "[]")
        for x in dist:
            g = re.sub(r"\$[\d,]+", "a sum", x["label"])
            g = re.sub(r"in \d{4}", "in an old year", g)
            g = re.sub(r"\(.*?\)", "", g).strip()
            g = re.sub(r"[\d.]+ acres", "some acres", g)
            labels.setdefault(x["key"], (g, x["kind"]))
        d = [x["key"] for x in dist]
        s = sc.get(pid, {})
        rows.append({
            "i": pid, "a": p["address"], "c": (p["city"] or "").title() or "Unknown", "pid": p["parcel_id"],
            "cf": p["county_fips"], "cn": county_name.get(p["county_fips"], p["county_fips"]),
            "o": p["owner_name"], "m": mail[pid]["value"] if pid in mail else None, "ab": int("absentee_owner" in d),
            "lv": p["land_value"], "iv": p["imp_value"], "tv": p["total_value"], "ac": p["acreage"],
            "z": (p["zoning"] or "").split(" - ")[0] or None, "zf": p["zoning"],
            "f": (p["flood_zone"] or "").split(" (")[0] or None,
            "lat": round(p["lat"], 5) if p["lat"] else None, "lon": round(p["lon"], 5) if p["lon"] else None,
            "yb": p["year_built"], "sq": p["building_sqft"],
            "s": s.get("overall"), "r": s.get("risk"), "rent": s.get("rental"), "land": s.get("land"),
            "stor": s.get("storage"), "biz": s.get("business"), "wk": s.get("workshop"),
            "rec": p["recommendation"] or "UNSCORED", "d": d, "vac": int(pid in vac), "cc": int(pid in code),
            "ts": p["tax_status"], "yb": p["year_built"],
            "conf": conf.get(pid), "lines": lines.get(pid, []), "chg": chg.get(pid, [])[:6],
            "seen": (p["first_seen"] or "")[:10], "upd": (p["last_seen"] or "")[:10],
            "tax": (taxbill[pid]["value"] if pid in taxbill else
                    taxcosl[pid]["value"][:80] if pid in taxcosl else
                    "no open bill at the Collector" if pid in taxchk else None),
            "lien": round(sum(_money(v) for v in liens.get(pid, {}).values()), 2) if pid in liens else 0,
            "inv": inv.get(pid)})
    rows.sort(key=lambda r: -(r["s"] or 0))
    return rows, labels


def write_csv(rows, path):
    """The plain spreadsheet Topher asked for: address, owner, price-ish numbers."""
    import csv
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Address", "City", "Owner of record", "Owner mailing address", "County appraised value",
                    "Assessed for tax (20%)", "City lien $", "On vacant register", "Delinquent taxes",
                    "Score", "Risk", "Our call", "Zoning", "Parcel", "Open in app"])
        for r in rows:
            w.writerow([r["a"], r["c"], r["o"], r["m"] or "", f"{r['tv']:,.0f}" if r["tv"] else "",
                        f"{r['tv']*0.2:,.0f}" if r["tv"] else "", f"{r['lien']:,.2f}" if r["lien"] else "",
                        "YES" if r["vac"] else "", "NOT CHECKED - arkansastaxsearch.com",
                        r["s"] if r["s"] is not None else "", r["r"] if r["r"] is not None else "",
                        r["rec"], r["zf"] or "", r["pid"] or "", f"http://127.0.0.1:8234/#property/{r['i']}"])


def build():
    init_db()
    rows, labels = export_rows()
    tpl = open(os.path.join(ROOT, "tools", "share_template.html")).read()
    n_inv = sum(1 for r in rows if r["inv"] and r.get("cf") in ("05051", "05125"))
    tpl = tpl.replace("The 21 investigated so far", f"The {n_inv} investigated so far")
    # the one-file share and the site's scan page embed Garland + Saline only; every other
    # county is loaded on demand from docs/data/scan/<fips>.json (the statewide export below)
    embed = [r for r in rows if r.get("cf") in ("05051", "05125")]
    data = json.dumps(embed, separators=(",", ":")).replace("</", "<\\/")
    body = tpl.replace("__DATA__", data).replace("__LABELS__", json.dumps(labels))
    head, rest = body.split("<style>", 1)
    css, tail = rest.split("</style>", 1)
    full = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">'
            + head + "<style>" + css + "</style></head><body>" + tail + "</body></html>")
    out = os.path.join(ROOT, "hunter", "static", "share.html")
    open(out, "w").write(full)
    os.makedirs(os.path.dirname(DESKTOP), exist_ok=True)
    open(DESKTOP, "w").write(full)
    write_csv(rows, os.path.join(os.path.dirname(DESKTOP), "PROPERTIES - address owner price.csv"))
    docs = os.path.join(ROOT, "docs", "garland.html")
    if os.path.isdir(os.path.dirname(docs)):
        open(docs, "w").write(full)
        os.makedirs(os.path.join(ROOT, "docs", "data"), exist_ok=True)
        built = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
        slim = [{k: v for k, v in r.items() if k != "inv"} for r in rows]
        home = [r for r in slim if r.get("cf") in ("05051", "05125")]
        json.dump({"built_at": built, "labels": labels, "rows": home},
                  open(os.path.join(ROOT, "docs", "data", "garland.json"), "w"), separators=(",", ":"))
        # every county: its own file, plus a small statewide index (counts + top 25) for the Today page
        sdir = os.path.join(ROOT, "docs", "data", "scan"); os.makedirs(sdir, exist_ok=True)
        by = {}
        for r in slim:
            by.setdefault(r.get("cf") or "?", []).append(r)
        index = {"built_at": built, "counties": {}}
        for cf, rs in by.items():
            rs.sort(key=lambda r: -(r.get("s") or 0))
            json.dump({"built_at": built, "labels": labels, "county": rs[0].get("cn"), "fips": cf, "rows": rs},
                      open(os.path.join(sdir, f"{cf}.json"), "w"), separators=(",", ":"))
            index["counties"][cf] = {"county": rs[0].get("cn"), "n": len(rs), "strong": sum(1 for r in rs if (r.get("s") or 0) >= 65),
                                     "with_building": sum(1 for r in rs if (r.get("iv") or 0) > 0),
                                     "top": [r for r in rs if r.get("rec") != "PASS"][:25]}
        json.dump(index, open(os.path.join(ROOT, "docs", "data", "scan_index.json"), "w"), separators=(",", ":"))
        # what changed this week, and where the hunt stands, for the Today page and the map
        county_name = {t["county_fips"]: t["county"] for t in __import__("hunter.config", fromlist=["TERRITORIES"]).TERRITORIES}
        conflicts = conflict_change_ids()
        chrows = [{"id": r["property_id"], "a": r["address"], "cn": county_name.get(r["county_fips"], r["county_fips"]), "cf": r["county_fips"],
                   "f": r["field"], "o": (r["old_value"] or "")[:60], "n": (r["new_value"] or "")[:60], "sev": r["severity"], "at": (r["detected_at"] or "")[:16],
                   "k": conflicts.get(r["id"], "change")}
                  for r in q("""SELECT c.id, c.property_id, c.field, c.old_value, c.new_value, c.severity, c.detected_at, p.address, p.county_fips
                                FROM changes c JOIN properties p ON p.id=c.property_id
                                WHERE c.detected_at > datetime('now','-7 days') AND c.severity IN ('medium','high') AND p.excluded=0
                                AND c.field NOT IN ('register_attachment','improved','acreage','property_type','building_sqft') ORDER BY c.id DESC LIMIT 300""")]
        from collections import Counter
        real = [r for r in chrows if r["k"] == "change"]
        byfield = Counter(r["f"] for r in real)
        json.dump({"built_at": built, "week": True, "total": len(real), "conflicts": sum(1 for r in chrows if r["k"] == "conflict"),
                   "sources": sum(1 for r in chrows if r["k"] == "sources"), "by_field": byfield.most_common(12),
                   "rows": real[:120] + [r for r in chrows if r["k"] != "change"][:80]},
                  open(os.path.join(ROOT, "docs", "data", "changes.json"), "w"), separators=(",", ":"))
        terr = {t["county_fips"]: t for t in __import__("hunter.config", fromlist=["TERRITORIES"]).TERRITORIES}
        scans = {}
        for r in q("SELECT territory, status, finished_at, started_at, stats_json FROM scans WHERE mode='distress' ORDER BY id"):
            scans[r["territory"]] = r
        hunt = {"built_at": built, "counties": {}}
        for fips, t in terr.items():
            sr = scans.get(t["key"])
            st = json.loads(sr["stats_json"] or "{}") if sr else {}
            hunt["counties"][fips] = {"county": t["county"], "key": t["key"],
                                      "status": ("done" if sr and sr["status"] == "complete" else "scanning" if sr and sr["status"] == "running" else "failed" if sr else "waiting"),
                                      "finished_at": (sr["finished_at"] or "")[:16] if sr else None,
                                      "n": index["counties"].get(fips, {}).get("n", 0), "strong": index["counties"].get(fips, {}).get("strong", 0),
                                      "examined": st.get("records_examined"), "excluded": st.get("excluded")}
        json.dump(hunt, open(os.path.join(ROOT, "docs", "data", "hunt_status.json"), "w"), separators=(",", ":"))
    return {"properties": len(rows), "investigated": n_inv, "kb": len(full) // 1024, "file": out, "desktop": DESKTOP}


if __name__ == "__main__":
    print(json.dumps(build(), indent=1, ensure_ascii=False))
