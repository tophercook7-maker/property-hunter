"""Report / memo / export generation (spec 37/77/78)."""
from __future__ import annotations

import csv
import io
import json

from . import analyzers, db, finance, scoring, store
from .config import LEGAL_DISCLAIMER
from .db import utcnow


def dossier(prop_id: int, include_ai: bool = False) -> dict:
    p = store.get_property(prop_id)
    if not p:
        return {}
    sc = scoring.scores_for(prop_id)
    ev = store.evidence_for(prop_id)
    out = {
        "property": p,
        "scores": sc,
        "recommendation": p.get("recommendation"),
        "deal_or_trap": analyzers.deal_or_trap(p, sc),
        "why_cheap": analyzers.why_cheap(p),
        "next_steps": analyzers.next_steps(p),
        "business_use": analyzers.business_use_analysis(p),
        "evidence": ev,
        "evidence_count": len(ev),
        "timeline": db.rows_to_dicts(
            db.q("SELECT * FROM timeline WHERE property_id=? ORDER BY "
                 "IFNULL(event_date,created_at) DESC, id DESC", (prop_id,))),
        "changes": db.rows_to_dicts(
            db.q("SELECT * FROM changes WHERE property_id=? ORDER BY id DESC LIMIT 50",
                 (prop_id,))),
        "conflicts": db.rows_to_dicts(
            db.q("SELECT * FROM conflicts WHERE property_id=? ORDER BY id DESC", (prop_id,))),
        "tasks": db.rows_to_dicts(
            db.q("SELECT * FROM tasks WHERE property_id=? ORDER BY status, priority, id",
                 (prop_id,))),
        "notes": db.rows_to_dicts(
            db.q("SELECT * FROM notes WHERE property_id=? ORDER BY id DESC", (prop_id,))),
        "photos": db.rows_to_dicts(
            db.q("SELECT * FROM photos WHERE property_id=? ORDER BY id DESC", (prop_id,))),
        "documents": db.rows_to_dicts(
            db.q("SELECT * FROM documents WHERE property_id=? ORDER BY id DESC", (prop_id,))),
        "watched": bool(db.q1("SELECT 1 FROM watchlist WHERE property_id=?", (prop_id,))),
        "watch": db.rows_to_dicts(db.q("SELECT * FROM watchlist WHERE property_id=?", (prop_id,))),
        "disclaimer": LEGAL_DISCLAIMER,
        "generated_at": utcnow(),
    }
    out["financials"] = default_financials(p)
    if include_ai:
        out["explanation"] = analyzers.explain(p)
    return out


def default_financials(p: dict) -> dict:
    total = p.get("total_value") or 0
    imp = p.get("imp_value") or 0
    sqft = p.get("building_sqft") or 0
    ac = p.get("acreage") or 0
    # The state parcel layer's totalvalue is the county's APPRAISED (full)
    # value; Arkansas taxes on 20% of that. Verified 2026-09-13 against the
    # layer's assessvalue column (0.10-0.20 of totalvalue, caps apply).
    appraised = total
    guess_price = round(appraised * 0.75) if appraised else 0
    out = {
        "appraised_total": total,
        "assessed_total": round(total * 0.2) if total else 0,
        "implied_market_value": appraised,
        "assessed_note": ("The county's total is its APPRAISED value - what the assessor "
                          "thinks it is worth, updated on a schedule; tax is charged on 20% "
                          "of it. It is an opinion, not an appraisal for sale and not a "
                          "sale price. Older records often run under the market."),
        "starting_price_estimate": guess_price,
    }
    if p.get("improved") == 1 and sqft:
        out["rehab"] = finance.rehab_estimate(sqft, "medium")
        rent = round(sqft * finance.FINANCE_DEFAULTS["rent_per_sqft_monthly"])
        out["rental"] = finance.rental_analysis(
            purchase_price=guess_price or max(total * 0.7, 1),
            monthly_rent=rent, rehab=out["rehab"]["estimate"], assessed_value=total)
        out["deal"] = finance.max_purchase_price(
            after_repair_value=appraised or 1, rehab=out["rehab"]["estimate"])
    if ac:
        out["storage"] = finance.storage_analysis(acreage=ac, purchase_price=guess_price)
    road_ev = store.latest_evidence(p["id"], "road_access")
    rank = 3
    if road_ev:
        from .sources.roads import rank_from_label
        rank = rank_from_label(road_ev["value"])
    comp = store.latest_evidence(p["id"], "nearby_food_business")
    out["snowcone"] = finance.snowcone_analysis(
        road_rank=rank, acreage=ac or 0.25,
        competitors=len((comp["value"] or "").split(",")) if comp and comp["value"] else 0)
    return out


def deal_memo(prop_id: int, use_ai: bool = True) -> dict:
    d = dossier(prop_id, include_ai=False)
    if not d:
        return {}
    p = d["property"]
    fin = d["financials"]
    sc = d["scores"]
    unknowns = (sc.get("overall") or {}).get("unknowns", [])
    memo = {
        "property": p.get("address") or p.get("parcel_id"),
        "parcel_id": p.get("parcel_id"),
        "generated_at": utcnow(),
        "why_interesting": [l["reason"] for l in
                            sorted((sc.get("overall") or {}).get("lines", []),
                                   key=lambda l: -l["points"])[:5] if l["points"] > 0],
        "what_we_know": [
            f"Owner of record: {p.get('owner_name') or 'unknown'}",
            f"County appraised total: ${p.get('total_value') or 0:,.0f} "
            f"(land ${p.get('land_value') or 0:,.0f}, improvements "
            f"${p.get('imp_value') or 0:,.0f})",
            f"Acreage: {p.get('acreage') if p.get('acreage') is not None else 'unknown'}",
            f"Flood zone: {p.get('flood_zone') or 'not checked'}",
            f"{d['evidence_count']} pieces of evidence on file, each with a source and date",
        ],
        "what_we_dont_know": unknowns or ["title", "condition", "taxes", "zoning"],
        "estimated_numbers": {
            "implied_market_value": fin.get("implied_market_value"),
            "rehab": (fin.get("rehab") or {}).get("estimate"),
            "max_purchase_price": (fin.get("deal") or {}).get("aggressive"),
            "monthly_cash_flow": ((fin.get("rental") or {}).get("returns") or {})
                                 .get("monthly_cash_flow"),
            "cap_rate": ((fin.get("rental") or {}).get("returns") or {}).get("cap_rate"),
        },
        "risks": [h for h in d["deal_or_trap"]["hard_stops"]] +
                 [s["label"] for s in (p.get("distress") or []) if s["kind"] == "risk"],
        "verdict": d["deal_or_trap"]["verdict"],
        "recommendation": p.get("recommendation"),
        "next_steps": [s["title"] for s in d["next_steps"][:6]],
        "confidence": (sc.get("overall") or {}).get("confidence", "LOW"),
        "disclaimer": LEGAL_DISCLAIMER,
    }
    if use_ai:
        memo["narrative"] = analyzers.what_would_you_do(p).get("text", "")
    return memo


# ------------------------------------------------------------------ exports

def dossier_html(prop_id: int) -> str:
    d = dossier(prop_id, include_ai=False)
    if not d:
        return "<h1>Not found</h1>"
    p = d["property"]
    esc = lambda s: (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
    rows = "".join(
        f"<tr><td>{esc(e['field'])}</td><td>{esc(e['value'])}</td>"
        f"<td>{esc(e['evidence_type'])}</td><td>{esc(e['confidence'])}</td>"
        f"<td>{esc(e['source'])}</td><td>{esc(e['effective_date'] or e['retrieved_at'])}</td></tr>"
        for e in d["evidence"])
    signals = "".join(f"<li><b>{esc(s['label'])}</b> ({esc(s['confidence'])}) - "
                      f"{esc(s['why'])}<br><i>Verify: {esc(s['verify'])}</i></li>"
                      for s in (p.get("distress") or []))
    steps = "".join(f"<li><b>{esc(s['title'])}</b> - {esc(s['detail'])}</li>"
                    for s in d["next_steps"])
    fin = d["financials"]
    deal = fin.get("deal")
    lien_ev = [e for e in d["evidence"] if e["field"] in ("cleanup_lien_total", "cleanup_lien_amount")]
    lien_total = lien_ev[0]["value"] if lien_ev else None
    lien_text = next((e["value"] for e in d["evidence"] if e["field"] == "cleanup_lien"), "")
    return f"""<!doctype html><meta charset="utf-8">
<title>Property dossier - {esc(p.get('address') or p.get('parcel_id'))}</title>
<style>
 body{{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:960px;
      margin:2rem auto;padding:0 1.2rem;color:#1b1d23}}
 h1{{margin-bottom:.2rem}} .sub{{color:#666}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin:1rem 0}}
 td,th{{border:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}}
 th{{background:#f4f5f7}} .box{{background:#f7f8fa;border-left:4px solid #4a6cf7;
      padding:.8rem 1rem;margin:1rem 0;border-radius:4px}}
 .warn{{border-left-color:#d9822b;background:#fff8f0}}
 .box table{{margin:0}} .box th{{width:190px;background:transparent}} .sub{{color:#777;font-size:12px}}
</style>
<h1>{esc(p.get('address') or 'No street address')}</h1>
<div class="sub">Parcel {esc(p.get('parcel_id'))} &middot; {esc(p.get('city'))} &middot;
 {esc(p.get('acreage'))} acres &middot; generated {esc(d['generated_at'])}</div>
<div class="box"><b>Our call: {esc(p.get('recommendation'))}</b> &mdash;
 {esc(d['deal_or_trap']['verdict'])}. {esc(d['deal_or_trap']['why'])}</div>
<h2>What it would cost</h2>
<div class="box">
<table>
<tr><th>Asking price</th><td>{esc('$%s' % format(p['list_price'], ',.0f')) if p.get('list_price') else 'NOT LISTED FOR SALE that we know of - this came off a City register, not a listing. If you find it listed, enter the price on the property.'}</td></tr>
<tr><th>County appraised total</th><td>{esc('$%s' % format(p['total_value'], ',.0f')) if p.get('total_value') else 'unknown'} <span class="sub">(FACT, county roll)</span></td></tr>
<tr><th>Assessed for tax (20%)</th><td>{esc('$%s' % format(fin.get('assessed_total') or 0, ',.0f')) if fin.get('assessed_total') else 'unknown'} <span class="sub">(CALCULATION: 20% of the appraised total)</span></td></tr>
<tr><th>The most I'd pay</th><td>{('$%s try &middot; $%s marginal &middot; $%s walk away' % (format(deal['aggressive'], ',.0f'), format(deal['reasonable'], ',.0f'), format(deal['maximum'], ',.0f'))) if deal and deal.get('works_at_any_price') else ('No price works on the current repair estimate - get a real contractor number.' if deal else 'Needs a building size to estimate - see the Money tab in the app.')} <span class="sub">(ESTIMATE)</span></td></tr>
<tr><th>City liens on it</th><td>{esc('$%s - ' % format(float(lien_total), ',.2f') + str(lien_text)) if lien_total else 'none on the City lien layer'} <span class="sub">(FACT, City GIS)</span></td></tr>
<tr><th>Delinquent taxes owed</th><td><b>NOT CHECKED - we cannot read this automatically.</b> Look it up here: <a href="https://www.arkansastaxsearch.com/garland.html">arkansastaxsearch.com &rarr; Garland</a> (parcel {esc(p.get('parcel_id') or '?')}). Paying someone's back taxes does NOT make you the owner.</td></tr>
</table>
</div>
<h2>What caught our attention</h2><ul>{signals or '<li>Nothing stood out.</li>'}</ul>
<h2>Why it might be cheap</h2>
<p>{esc(d['why_cheap']['most_likely'])}</p>
<p><b>Biggest unresolved concern:</b> {esc(d['why_cheap']['biggest_unresolved_concern'])}</p>
<h2>What to do next</h2><ol>{steps}</ol>
<h2>Every fact we hold, and where it came from</h2>
<table><tr><th>Field</th><th>Value</th><th>Type</th><th>Confidence</th>
<th>Source</th><th>As of</th></tr>{rows}</table>
<div class="box warn">{esc(d['disclaimer'])}</div>
"""


def properties_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    cols = ["id", "address", "city", "parcel_id", "owner_name", "acreage",
            "total_value", "land_value", "imp_value", "property_type", "flood_zone",
            "recommendation", "overall_score", "risk_score", "data_class", "state"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def morning_report() -> dict:
    """The daily intelligence briefing (spec 37)."""
    new = db.q("""SELECT p.id,p.address,p.parcel_id,s.score FROM properties p
                  JOIN scores s ON s.property_id=p.id AND s.kind='overall'
                  WHERE p.excluded=0 AND p.first_seen >= datetime('now','-1 day')
                  ORDER BY s.score DESC LIMIT 8""")
    changed = db.q("""SELECT c.*, p.address, p.parcel_id FROM changes c
                      JOIN properties p ON p.id=c.property_id
                      WHERE p.excluded=0 AND c.detected_at >= datetime('now','-2 day')
                      AND c.severity IN ('high','medium')
                      ORDER BY c.id DESC LIMIT 8""")
    picks = analyzers.topher_picks(3)
    avoid = db.q("""SELECT p.id,p.address,p.parcel_id,p.recommendation,s.score
                    FROM properties p
                    JOIN scores s ON s.property_id=p.id AND s.kind='risk'
                    WHERE p.excluded=0 AND s.score >= 60
                    ORDER BY s.score DESC LIMIT 3""")
    tasks = db.q("""SELECT t.*, p.address FROM tasks t
                    LEFT JOIN properties p ON p.id=t.property_id
                    WHERE t.status='open' ORDER BY t.priority, t.id LIMIT 3""")
    best = picks[0] if picks else None
    return {
        "greeting": "Good morning, Topher.",
        "headline": ("Here's what changed." if (new or changed)
                     else "Nothing moved since the last look."),
        "new_opportunities": db.rows_to_dicts(new),
        "important_changes": db.rows_to_dicts(changed),
        "investigate": best,
        "avoid": db.rows_to_dicts(avoid),
        "best_deal": best,
        "biggest_risk": db.rows_to_dicts(avoid)[:1],
        "next_three_actions": [
            {"title": t["title"], "property": t["address"], "property_id": t["property_id"]}
            for t in tasks],
        "generated_at": utcnow(),
        "disclaimer": LEGAL_DISCLAIMER,
    }
