"""Build the shareable, self-contained Property Hunter website from the live database.

    python3 tools/build_share.py

Writes hunter/static/share.html (served at /share) and a copy on the Desktop.
The page carries real owner names from the public tax roll - it is Topher's
call where it gets shared; this script never publishes anything.
"""
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q  # noqa: E402

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


def export_rows():
    mail, vac, code = _latest("owner_mailing_address"), _latest("vacant_structure"), _latest("code_case_open")
    liens = {}
    for r in q("SELECT property_id, value, raw_ref FROM evidence WHERE field='cleanup_lien_amount'"):
        liens.setdefault(r["property_id"], {})[r["raw_ref"] or r["value"]] = r["value"]
    sc = {}
    for r in q("SELECT property_id, kind, score FROM scores"):
        sc.setdefault(r["property_id"], {})[r["kind"]] = r["score"]
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
    n_inv = sum(1 for r in rows if r["inv"])
    tpl = tpl.replace("The 21 investigated so far", f"The {n_inv} investigated so far")
    data = json.dumps(rows, separators=(",", ":")).replace("</", "<\\/")
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
        json.dump({"built_at": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds") + "Z",
                   "labels": labels, "rows": [{k: v for k, v in r.items() if k != "inv"} for r in rows]},
                  open(os.path.join(ROOT, "docs", "data", "garland.json"), "w"), separators=(",", ":"))
    return {"properties": len(rows), "investigated": n_inv, "kb": len(full) // 1024, "file": out, "desktop": DESKTOP}


if __name__ == "__main__":
    print(json.dumps(build(), indent=1, ensure_ascii=False))
