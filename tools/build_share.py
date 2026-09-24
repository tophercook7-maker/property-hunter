"""Build the shareable, self-contained Property Hunter website from the live database.

    python3 tools/build_share.py

Writes hunter/static/share.html (served at /share) and a copy on the Desktop.
The page carries real owner names from the public tax roll - it is Topher's
call where it gets shared; this script never publishes anything.
"""
import datetime
import json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hunter.db import init_db, q, q1  # noqa: E402

DESKTOP = os.path.expanduser("~/Desktop/🏠 Property Hunter/SHARE - Property Hunter website.html")


def _only(only):
    """SQL fragment restricting a query to some property ids (None = every property)."""
    if not only:
        return "", ()
    ids = tuple(int(x) for x in only)
    return f" AND property_id IN ({','.join('?' * len(ids))})", ids


def _latest(field, only=None):
    out = {}
    frag, ids = _only(only)
    for r in q(f"SELECT property_id, value, raw_ref FROM evidence WHERE field=?{frag} ORDER BY id", (field, *ids)):
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
    # a first reading (nothing -> something) is discovery, not a change and not a conflict: drop it
    out = {r["id"]: "seed" for r in rows if not (r["old_value"] or "").strip() or r["old_value"] in ("None", "not known")}
    out.update({r["id"]: "conflict" for r in rows if r["id"] not in out and r["mins"] is not None and r["mins"] < 10})
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


def _latest_full(field, only=None):
    out = {}
    frag, ids = _only(only)
    for r in q(f"SELECT id, property_id, value, raw_ref, source, effective_date, created_at FROM evidence WHERE field=?{frag} ORDER BY id", (field, *ids)):
        out[r["property_id"]] = dict(r)
    return out


COLLECTOR_SOURCES = ("county_tax_collector", "county_delinquent_list")
STALE_DAYS = 45


def tax_state(pid, p, *, cert, removed, redeemed, sold, bill, chk, delinq, amt_state, amt_county, today):
    """The one tax state a row may carry. Facts only; a missing check stays UNKNOWN.

      TAX_SALE_VERIFIED   State Lands lists it for sale (certification newer than any removal)
      DELINQUENT_VERIFIED the Collector (or an imported county list) says delinquent
      CURRENT_BILL_OPEN   the Collector shows this year's bill open — NOT delinquent
      CURRENT_VERIFIED    the Collector answered and shows no open real-estate bill
      STALE               a Collector answer older than STALE_DAYS (state kept in `was`)
      UNKNOWN             never checked at the Collector; not on the State list
    SOURCE_UNAVAILABLE is composed at display time from status.json (it is a fact about the
    source, not about the parcel). A State Lands 'not held' check never becomes a Collector state."""
    def when(ev):
        return ((ev or {}).get("effective_date") or (ev or {}).get("created_at") or "")[:10]
    def days_old(d):
        try:
            return (today - datetime.date.fromisoformat(d)).days
        except Exception:
            return None
    c = cert.get(pid)
    ended = [e for e in (removed.get(pid), redeemed.get(pid), sold.get(pid)) if e and c and e["id"] > c["id"]]
    if c and not ended and str(p["tax_status"] or "").startswith("CERTIFIED"):
        a = amt_state.get(pid)
        return {"st": "TAX_SALE_VERIFIED", "src": "Commissioner of State Lands", "as_of": when(c),
                "amt": _money(a["value"]) if a else None, "conf": "FACT"}
    d = delinq.get(pid)
    if d:
        a = amt_county.get(pid)
        st = {"st": "DELINQUENT_VERIFIED", "src": "County Collector" if d.get("source") == "county_tax_collector" else "County delinquent list",
              "as_of": when(d), "amt": _money(a["value"]) if a else None, "conf": "FACT"}
    else:
        b = bill.get(pid)
        k = chk.get(pid)
        if b and (p["tax_status"] == "DELINQUENT" or "delinquent" in (b.get("value") or "").lower().split("(")[-1]):
            st = {"st": "DELINQUENT_VERIFIED", "src": "County Collector", "as_of": when(b), "amt": _money_in(b.get("value")), "conf": "FACT"}
        elif b:
            st = {"st": "CURRENT_BILL_OPEN", "src": "County Collector", "as_of": when(b), "amt": _money_in(b.get("value")), "conf": "FACT"}
        elif k and k.get("source") in COLLECTOR_SOURCES:
            st = {"st": "CURRENT_VERIFIED", "src": "County Collector", "as_of": when(k), "amt": None, "conf": "OBSERVATION"}
        else:
            st = {"st": "UNKNOWN", "src": None, "as_of": None, "amt": None, "conf": None}
            if k and k.get("source") == "cosl_listings":
                st["cosl_check"] = when(k)      # the State says it does not hold it; says nothing about the county bill
            return st
    age = days_old(st["as_of"]) if st["as_of"] else None
    if age is not None and age > STALE_DAYS:
        return {"st": "STALE", "was": st["st"], "src": st["src"], "as_of": st["as_of"], "amt": st.get("amt"), "conf": st.get("conf"), "days": age}
    return st


def _money_in(text):
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", text or "")
    return _money(m.group(1)) if m else None


def tax_text(pid, taxbill, taxchk, taxcosl):
    """Legacy one-line text, now source-correct: the State never speaks for the Collector."""
    if pid in taxbill:
        return taxbill[pid]["value"]
    if pid in taxcosl:
        return taxcosl[pid]["value"][:80]
    k = taxchk.get(pid)
    if k and k.get("source") in COLLECTOR_SOURCES:
        return "no open bill at the Collector"
    if k and k.get("source") == "cosl_listings":
        return "not held by the State (State Lands check)"
    return None


def build_signals(rows_by_id: dict, county_name: dict) -> dict:
    """OPPORTUNITY SIGNALS for the last seven days: actionable public-record EVENTS, each with
    source, evidence status, why it matters and one next action. Never a field diff. Rules:
      - a State certification counts whether it is 'new this week' or 'first seen by the scanner
        this week' (the user has not seen it either way); the label says which
      - leaving the State inventory counts only when the reason is known from the State's monthly
        report (sold / redeemed); a bare disappearance is reported as 'left the inventory'
      - a City register record counts when its FIRST evidence row is inside the window and the
        property was already known before the window (otherwise it is discovery of a Garland row,
        still listed but labelled as such)
      - a Collector-verified or county-list delinquency counts when first recorded in the window
      - repair artefacts (rows whose property has a county change in the window, or conflict-kind
        changes) never count
      - there is no listing source, so no listing event can exist here."""
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    artefact = {r["property_id"] for r in q("SELECT DISTINCT property_id FROM changes WHERE field IN ('county_fips','territory') AND detected_at > ?", (since,))}
    out = []
    def href(r, kind):
        if kind == "state-lands":
            return f"state-lands.html?county={(r['cn'] or 'GARLAND').upper()}&q={r['pid'] or r['a'] or ''}"
        if kind == "directions" and r.get("lat"):
            return f"https://www.google.com/maps/dir/?api=1&destination={r['lat']},{r['lon']}"
        return f"lookup.html?county={r['cf']}&q={r['pid'] or r['a'] or ''}"
    try:
        sl_urls = {(x.get("fips"), x.get("parcel_id")): x.get("listing_url") for x in json.load(open(os.path.join(ROOT, "docs", "data", "state_lands.json"))).get("listings", [])}
    except Exception:
        sl_urls = {}
    def row(pid, event, label, date, src, status, why, nxt, extra=None, discovery=False, ref=None, src_url=None, discovered_at=None):
        r = rows_by_id.get(pid)
        if not r or pid in artefact:
            return
        url = src_url or (sl_urls.get((r["cf"], r["pid"])) if event.startswith(("NEW_TAX_SALE", "STATE_")) else None)
        out.append({"id": pid, "a": r["a"], "cn": r["cn"], "cf": r["cf"], "pid": r["pid"], "tv": r["tv"], "iv": r["iv"],
                    "event": event, "label": label, "date": (date or "")[:10], "discovered_at": (discovered_at or date or "")[:16],
                    "src": src, "src_url": url or None, "status": status, "kind": "verified" if status == "VERIFIED" else "observed",
                    "evidence_ref": ref, "cls": "FIRST_DISCOVERY" if discovery else "WORLD_EVENT",
                    "why": why, "next": {"label": nxt["label"], "href": href(r, nxt["href"])},
                    "taxs": r.get("taxs"), "sale": r.get("sale"), "conf": r.get("conf"),
                    "discovery": bool(discovery), **(extra or {})})
    # 1. State tax sale: certification recorded in the window (change row, seed or not)
    for c in q("""SELECT c.id cid, c.property_id, c.old_value, c.detected_at, p.first_seen,
                         (SELECT e.effective_date FROM evidence e WHERE e.property_id=c.property_id AND e.field='tax_delinquent' ORDER BY e.id DESC LIMIT 1) listed,
                         (SELECT e.source_url FROM evidence e WHERE e.property_id=c.property_id AND e.field='tax_delinquent' ORDER BY e.id DESC LIMIT 1) surl
                  FROM changes c JOIN properties p ON p.id=c.property_id
                  WHERE c.field='tax_status' AND c.new_value LIKE 'CERTIFIED%' AND c.detected_at > ? AND p.excluded=0""", (since,)):
        first = (c["first_seen"] or "") > since
        row(c["property_id"], "NEW_TAX_SALE",
            "First seen on the State tax-sale list" if first else "Newly certified to the State for unpaid taxes",
            (c["listed"] or c["detected_at"]), "Commissioner of State Lands", "VERIFIED",
            "The State is selling this parcel for unpaid taxes; the amount owed is public and the owner can still redeem until it sells.",
            {"label": "Open sale file", "href": "state-lands"}, discovery=first, ref=f"change:{c['cid']}", src_url=c["surl"], discovered_at=c["detected_at"])
    # 2. Left the State inventory: sold / redeemed from the monthly report, else 'left'
    for e in q("""SELECT e.id eid, e.property_id, e.field, e.value, e.created_at, e.effective_date, e.source_url FROM evidence e JOIN properties p ON p.id=e.property_id
                  WHERE e.field IN ('tax_sale_history','tax_redemption','tax_delinquent_removed') AND e.created_at > ? AND p.excluded=0
                  ORDER BY e.id""", (since,)):
        kind = {"tax_sale_history": ("STATE_SOLD", "Sold at the State tax sale", "The State's monthly sales report lists this parcel as sold; the deed goes to the buyer after the litigation period."),
                "tax_redemption": ("STATE_REDEEMED", "Redeemed by the owner", "The State's monthly report says the owner paid up; it is off the sale list."),
                "tax_delinquent_removed": ("STATE_LEFT", "Left the State tax-sale inventory", "It is no longer listed; the monthly report will say whether it sold or was redeemed.")}[e["field"]]
        row(e["property_id"], kind[0], kind[1], (e["effective_date"] or e["created_at"]), "Commissioner of State Lands",
            "VERIFIED" if e["field"] != "tax_delinquent_removed" else "OBSERVED", kind[2],
            {"label": "Re-check the file", "href": "lookup"}, ref=f"evidence:{e['eid']}", src_url=e["source_url"], discovered_at=e["created_at"])
    # 3. City registers: first evidence row inside the window
    for field, ev_name, label, why, nxt in (
            ("vacant_structure", "NEW_VACANCY_RECORD", "New vacant-structure register record", "The City itself now records this building as vacant.", {"label": "Drive by", "href": "directions"}),
            ("cleanup_lien_amount", "NEW_LIEN", "New City cleanup / demolition lien", "The City spent money here and holds a lien; it is paid at closing or negotiated.", {"label": "Investigate lien", "href": "lookup"}),
            ("code_case_open", "NEW_CODE_CASE", "New code-enforcement case", "The City opened a housing or code case at this address.", {"label": "Review case", "href": "lookup"}),
            ("tax_delinquent_county", "VERIFIED_DELINQUENCY", "Delinquent at the county (verified)", "The county's own record says the taxes are behind.", {"label": "Inspect delinquent record", "href": "lookup"})):
        for e in q(f"""SELECT e.property_id, MIN(e.created_at) first_at, MIN(e.id) eid, MIN(e.effective_date) eff, MIN(e.source_url) surl, p.first_seen
                       FROM evidence e JOIN properties p ON p.id=e.property_id
                       WHERE e.field=? AND p.excluded=0 GROUP BY e.property_id HAVING first_at > ?""", (field, since)):
            first = (e["first_seen"] or "") > since
            row(e["property_id"], ev_name, label + (" (first seen by the scanner)" if first else ""), (e["eff"] or e["first_at"]),
                "City of Hot Springs" if field != "tax_delinquent_county" else "County Collector", "VERIFIED", why, nxt,
                discovery=first, ref=f"evidence:{e['eid']}", src_url=e["surl"], discovered_at=e["first_at"])
    out.sort(key=lambda x: x["date"], reverse=True)
    events = [x for x in out if not x["discovery"]]
    discovered = [x for x in out if x["discovery"]]
    def counts(xs):
        c = {}
        for x in xs:
            c[x["event"]] = c.get(x["event"], 0) + 1
        return c
    return {"schema": 2, "built_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z", "window_days": 7,
            "classes": {"WORLD_EVENT": "the public record itself changed inside the window", "FIRST_DISCOVERY": "Property Hunter read the record for the first time inside the window; the record may be older"},
            # `total` = events on parcels the hunt already knew (a change in the world this week);
            # `discovered` = signals on parcels the scanner met for the first time this week (new to the
            # user too, but not "new this week" in the world). Never merged into one number.
            "total": len(events), "by_event": counts(events),
            "discovered_total": len(discovered), "discovered_by_event": counts(discovered),
            "listing_source": None, "rows": events[:200], "discovered": discovered[:200]}


TIMELINE_FIELDS = {
    "tax_delinquent": ("WORLD_EVENT", "Entered the State tax-sale inventory", "Commissioner of State Lands"),
    "tax_sale_history": ("WORLD_EVENT", "Sold at the State tax sale", "Commissioner of State Lands"),
    "tax_redemption": ("WORLD_EVENT", "Redeemed by the owner (State report)", "Commissioner of State Lands"),
    "tax_delinquent_removed": ("SOURCE_CHECK", "No longer in the State inventory when re-read", "Commissioner of State Lands"),
    "vacant_structure": ("WORLD_EVENT", "On the City vacant-structure register", "City of Hot Springs"),
    "cleanup_lien_amount": ("WORLD_EVENT", "City cleanup / demolition lien recorded", "City of Hot Springs"),
    "code_case_open": ("WORLD_EVENT", "Code-enforcement case opened", "City of Hot Springs"),
    "tax_status_check": ("SOURCE_CHECK", "Source answered: no open bill / not held", None),
    "tax_bill": ("SOURCE_CHECK", "Collector answered with an open bill", "County Collector"),
    "tax_delinquent_county": ("WORLD_EVENT", "Delinquent at the county", None),
}


def build_timelines(rows_by_id: dict) -> dict:
    """Per-property chronological evidence, classified, with provenance. Facts only:
      WORLD_EVENT     the record's own date (effective_date) says something happened
      FIRST_DISCOVERY the moment Property Hunter first read that record (created_at)
      SOURCE_CHECK    a source was asked and answered (incl. 'not held' / 'no open bill')
      INFORMATIONAL   a roll reading changed (owner, value) - information, not opportunity
      MANUAL          a human-imported record (county delinquent list)
    Exported only for properties that have at least one non-roll event, one file per county."""
    out = {}
    def add(pid, ev):
        r = rows_by_id.get(pid)
        if not r:
            return
        out.setdefault(r["cf"], {}).setdefault(str(pid), []).append(ev)
    for e in q("""SELECT e.id, e.property_id, e.field, e.value, e.source, e.source_name, e.source_url, e.effective_date, e.created_at, e.evidence_type, e.confidence, e.origin
                  FROM evidence e JOIN properties p ON p.id=e.property_id
                  WHERE (e.field IN ({}) OR e.field LIKE 'manual:%') AND p.excluded=0 ORDER BY e.id""".format(",".join("?" * len(TIMELINE_FIELDS))), tuple(TIMELINE_FIELDS)):
        cls, title, src = TIMELINE_FIELDS.get(e["field"], ("MANUAL", "Manual verification recorded by a person", None))
        src = src or e["source_name"] or e["source"]
        origin = e["origin"] or __import__("hunter.store", fromlist=["origin_of"]).origin_of(dict(e))
        if e["source"] == "county_delinquent_list":
            title = "Delinquent on the county's list (imported county record)"
        if origin == "MANUAL_VERIFICATION":
            cls = "MANUAL"
        if e["field"].startswith("manual:") or e["field"].startswith("photo:"):
            # P8: a person's research (deed names, mailing addresses, inspection notes, photos) is private to the license.
            # The public timeline keeps the fact that a verification was recorded on that date, never its content.
            add(e["property_id"], {"date": (e["effective_date"] or e["created_at"] or "")[:10], "cls": "MANUAL", "title": "Manual verification recorded by a person",
                                   "detail": "details are private to the license holder", "src": "a person (MANUAL VERIFICATION)", "ref": f"evidence:{e['id']}", "url": None, "etype": e["evidence_type"], "conf": e["confidence"], "origin": origin})
            continue
        if e["field"] == "tax_status_check":
            title = "State Lands: not held by the State" if e["source"] == "cosl_listings" else "Collector: no open real-estate bill"
        eff = (e["effective_date"] or "")[:10]
        seen = (e["created_at"] or "")[:16]
        base = {"src": src, "ref": f"evidence:{e['id']}", "url": e["source_url"] or None, "etype": e["evidence_type"], "conf": e["confidence"], "origin": origin}
        if cls == "WORLD_EVENT" and eff:
            add(e["property_id"], {"date": eff, "cls": "WORLD_EVENT", "title": title, "detail": (e["value"] or "")[:140], **base})
            add(e["property_id"], {"date": seen, "cls": "FIRST_DISCOVERY", "title": f"Property Hunter first read this record", "detail": title, **base})
        elif cls == "WORLD_EVENT":
            add(e["property_id"], {"date": seen, "cls": "FIRST_DISCOVERY", "title": title + " (record undated; this is when it was read)", "detail": (e["value"] or "")[:140], **base})
        else:
            add(e["property_id"], {"date": eff or seen, "cls": cls, "title": title, "detail": (e["value"] or "")[:140], **base})
    # the roll itself: first read, and informational reading changes in the last 90 days
    ids = {int(k) for c in out.values() for k in c}
    if ids:
        marks = ",".join("?" * len(ids))
        for p in q(f"SELECT id, first_seen FROM properties WHERE id IN ({marks})", tuple(ids)):
            add(p["id"], {"date": (p["first_seen"] or "")[:16], "cls": "FIRST_DISCOVERY", "title": "Property Hunter first read this parcel from the county roll", "src": "Arkansas GIS Office (county assessor roll)", "ref": f"property:{p['id']}", "url": None})
        for c in q(f"""SELECT id, property_id, field, old_value, new_value, source, detected_at FROM changes
                       WHERE property_id IN ({marks}) AND field IN ('owner_name','total_value','imp_value','land_value','tax_status')
                       AND detected_at > datetime('now','-90 days') AND old_value IS NOT NULL AND old_value NOT IN ('', 'None', 'not known') ORDER BY id""", tuple(ids)):
            add(c["property_id"], {"date": (c["detected_at"] or "")[:16], "cls": "INFORMATIONAL", "title": f"Roll reading changed: {c['field'].replace('_', ' ')}",
                                   "detail": f"{(c['old_value'] or '')[:40]} -> {(c['new_value'] or '')[:40]}", "src": c["source"], "ref": f"change:{c['id']}", "url": None})
    for cf in out:
        for pid in out[cf]:
            out[cf][pid].sort(key=lambda x: x["date"])
    return out


def export_rows(only=None):
    """Every exported row, or (only=[ids]) just those properties through the very same code path,
    so the investigation case engine reads the one tax-state model instead of re-implementing it."""
    o = only
    mail, vac, code = _latest("owner_mailing_address", o), _latest("vacant_structure", o), _latest("code_case_open", o)
    taxbill, taxchk, taxcosl = _latest_full("tax_bill", o), _latest_full("tax_status_check", o), _latest_full("tax_delinquent", o)
    removed, redeemed, sold = _latest_full("tax_delinquent_removed", o), _latest_full("tax_redemption", o), _latest_full("tax_sale_history", o)
    delinq, amt_state, amt_county = _latest_full("tax_delinquent_county", o), _latest_full("tax_amount_owed", o), _latest_full("tax_amount_owed_county", o)
    today = datetime.date.today()
    frag, ids = _only(o)
    liens = {}
    for r in q(f"SELECT property_id, value, raw_ref FROM evidence WHERE field='cleanup_lien_amount'{frag}", ids):
        liens.setdefault(r["property_id"], {})[r["raw_ref"] or r["value"]] = r["value"]
    sc, lines, conf = {}, {}, {}
    for r in q(f"SELECT property_id, kind, score, confidence, breakdown_json FROM scores WHERE 1=1{frag}", ids):
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
    for r in q(f"""SELECT id, property_id, field, old_value, new_value, severity, detected_at FROM changes
                  WHERE detected_at > datetime('now','-7 days') AND severity IN ('medium','high')
                  AND field NOT IN ('improved','acreage','property_type','register_attachment','building_sqft'){frag} ORDER BY id DESC""", ids):
        if conflicts.get(r["id"]) == "seed":
            continue
        chg.setdefault(r["property_id"], []).append({"f": r["field"], "o": (r["old_value"] or "")[:60], "n": (r["new_value"] or "")[:60],
                                                     "sev": r["severity"], "at": (r["detected_at"] or "")[:10],
                                                     **({"k": conflicts[r["id"]]} if r["id"] in conflicts else {})})
    inv = {}
    for r in q(f"SELECT * FROM investigations WHERE status='complete'{frag} ORDER BY finished_at", ids):
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
    pfrag = frag.replace("property_id", "id")
    # the public export skips excluded areas; an explicit per-id request (a case file) is not the public export
    excl = "" if only else "excluded=0 AND "
    for p in q(f"SELECT * FROM properties WHERE {excl}data_class='real'{pfrag}", ids):
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
            "o": p["owner_name"], "ab": int("absentee_owner" in d),
            # Owner MAILING ADDRESS is deliberately not published. The absentee
            # signal above is the derived fact the UI needs; the raw address turned
            # this snapshot into a distress-ranked mailing list of 592 named people.
            # Look up still resolves one address live, per parcel, on demand.
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
            "tax": tax_text(pid, taxbill, taxchk, taxcosl),
            "taxs": tax_state(pid, p, cert=taxcosl, removed=removed, redeemed=redeemed, sold=sold, bill=taxbill, chk=taxchk,
                              delinq=delinq, amt_state=amt_state, amt_county=amt_county, today=today),
            "sale": {"st": "UNKNOWN", "src": None},   # no listing source is connected; silence must never read as "not for sale"
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
            w.writerow([r["a"], r["c"], r["o"], r.get("m") or "", f"{r['tv']:,.0f}" if r["tv"] else "",
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
                                     "smax": max((r.get("s") or 0) for r in rs),
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
        chrows = [r for r in chrows if r["k"] != "seed"]
        real = [r for r in chrows if r["k"] == "change"]
        byfield = Counter(r["f"] for r in real)
        json.dump(build_signals({r["i"]: r for r in slim}, county_name),
                  open(os.path.join(ROOT, "docs", "data", "signals.json"), "w"), separators=(",", ":"))
        # P5.5: investigation cases, evidence panels, Bee and outreach metadata are LICENSED application data.
        # They are served only by the licensed local app and are no longer written to the public site.
        tdir = os.path.join(ROOT, "docs", "data", "timeline"); os.makedirs(tdir, exist_ok=True)
        for cf, per in build_timelines({r["i"]: r for r in slim}).items():
            json.dump({"built_at": built, "county": cf, "properties": per}, open(os.path.join(tdir, f"{cf}.json"), "w"), separators=(",", ":"))
        json.dump({"built_at": built, "week": True, "total": len(real), "conflicts": sum(1 for r in chrows if r["k"] == "conflict"),
                   "sources": sum(1 for r in chrows if r["k"] == "sources"), "by_field": byfield.most_common(12),
                   "rows": real[:120] + [r for r in chrows if r["k"] != "change"][:80]},
                  open(os.path.join(ROOT, "docs", "data", "changes.json"), "w"), separators=(",", ":"))
        terr = {t["county_fips"]: t for t in __import__("hunter.config", fromlist=["TERRITORIES"]).TERRITORIES}
        try:
            sl_counties = {x.get("fips") for x in json.load(open(os.path.join(ROOT, "docs", "data", "state_lands.json"))).get("listings", [])}
        except Exception:
            sl_counties = set()
        try:
            cp_open = json.load(open(os.path.join(ROOT, "docs", "data", "status.json"))).get("countypay", {}).get("open")
        except Exception:
            cp_open = None
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
                                      "examined": st.get("records_examined"), "excluded": st.get("excluded"),
                                      # what public-record sources actually exist for this county: an empty filter
                                      # must never read as "there are no such properties"
                                      "sources": {"roll": True, "state_lands": fips in sl_counties,
                                                  "city_registers": fips == "05051",
                                                  "collector": "unavailable" if cp_open is False else ("open" if cp_open else "untested")}}
        json.dump(hunt, open(os.path.join(ROOT, "docs", "data", "hunt_status.json"), "w"), separators=(",", ":"))
    return {"properties": len(rows), "investigated": n_inv, "kb": len(full) // 1024, "file": out, "desktop": DESKTOP}


if __name__ == "__main__":
    print(json.dumps(build(), indent=1, ensure_ascii=False))
