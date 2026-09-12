"""HTTP API + static front end.

Also the integration surface for Daniel / AI Hub (spec 50): every answer is
structured JSON, and nothing here needs the web UI to be useful.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from . import (analyzers, ask as askmod, db, distress, exclusions, files, finance,
               geo, learning, nlsearch, pdf, reports, scanner, scheduler, scoring,
               seeds, store, vision)
from .ai import status as ai_status
from .config import (APP_NAME, APPROVAL_REQUIRED_ACTIONS, DEFAULT_TERRITORY,
                     EXCLUSIONS, FILES_DIR, FINANCE_DEFAULTS, LEGAL_DISCLAIMER,
                     TERRITORIES, VERSION)
from .db import jdump, jload, utcnow
from .sources import all_sources, get_source, register_all

STATIC = Path(__file__).parent / "static"

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    register_all()
    exclusions.sync_rules_to_db()
    exclusions.refresh_cache()
    scanner.reap_interrupted()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan,
              description="Personal real-estate acquisition intelligence. "
                          "Research assistant, not a lawyer.")


# ------------------------------------------------------------------- helpers

def _prop_row(r) -> dict:
    d = dict(r)
    d["distress"] = jload(d.get("distress_json"), []) or []
    d.pop("distress_json", None)
    return d


def _require(prop_id: int) -> dict:
    p = store.get_property(prop_id)
    if not p:
        raise HTTPException(404, "No such property")
    return p


# ---------------------------------------------------------------- dashboard

@app.get("/api/status")
def api_status() -> dict:
    counts = db.q1("""SELECT
        COUNT(*) total,
        SUM(excluded=0) active,
        SUM(excluded=1) excluded,
        SUM(excluded=0 AND data_class='demo') demo,
        SUM(excluded=0 AND first_seen >= datetime('now','-2 day')) fresh,
        SUM(excluded=0 AND property_type='lot') lots,
        SUM(excluded=0 AND property_type='house') houses,
        SUM(excluded=0 AND property_type='commercial') commercial
        FROM properties""")
    distressed = db.q1("SELECT COUNT(*) c FROM properties WHERE excluded=0 "
                       "AND distress_json IS NOT NULL AND distress_json!='[]'")["c"]
    rental = db.q1("SELECT COUNT(*) c FROM scores WHERE kind='rental' AND score>=60")["c"]
    business = db.q1("SELECT COUNT(*) c FROM scores WHERE kind='business' AND score>=60")["c"]
    watch = db.q1("SELECT COUNT(*) c FROM watchlist")["c"]
    alerts = db.q1("SELECT COUNT(*) c FROM alerts WHERE read_at IS NULL")["c"]
    tasks = db.q1("SELECT COUNT(*) c FROM tasks WHERE status='open'")["c"]
    last = scanner.last_scan()
    running = scanner.is_running()
    tax_manual = db.q1("SELECT COUNT(*) c FROM tasks WHERE status='open' AND source IN "
                       "('cosl','garland_tax_collector')")["c"]
    return {
        "app": APP_NAME, "version": VERSION,
        "territory": next(t for t in TERRITORIES if t["key"] == DEFAULT_TERRITORY),
        "counts": {
            "properties": counts["active"] or 0,
            "excluded": counts["excluded"] or 0,
            "demo": counts["demo"] or 0,
            "new": counts["fresh"] or 0,
            "distressed": distressed,
            "tax_opportunities_pending": tax_manual,
            "cheap_lots": counts["lots"] or 0,
            "houses": counts["houses"] or 0,
            "commercial": counts["commercial"] or 0,
            "rental_candidates": rental,
            "business_candidates": business,
            "watchlist": watch,
            "alerts": alerts,
            "open_tasks": tasks,
        },
        "scan": {"running": running,
                 "last": (last or {}).get("finished_at") or (last or {}).get("started_at"),
                 "last_status": (last or {}).get("status"),
                 "last_mode": (last or {}).get("mode"),
                 "next_scheduled": db.setting("next_scan", None),
                 "schedule_enabled": scheduler.config().get("enabled", True)},
        "ai": ai_status(),
        "exclusions": [{"key": e["key"], "label": e["label"]} for e in EXCLUSIONS],
        "disclaimer": LEGAL_DISCLAIMER,
    }


@app.get("/api/briefing")
def api_briefing() -> dict:
    return reports.morning_report()


@app.get("/api/picks")
def api_picks(limit: int = 3) -> dict:
    return {"picks": analyzers.topher_picks(limit)}


# ------------------------------------------------------------------- search

SORTS = {
    "overall": "so.score DESC", "risk": "sr.score ASC", "newest": "p.first_seen DESC",
    "cheapest": "IFNULL(p.total_value, 1e12) ASC", "acreage": "IFNULL(p.acreage,0) DESC",
    "rental": "IFNULL(s_rental.score,0) DESC", "land": "IFNULL(s_land.score,0) DESC",
    "storage": "IFNULL(s_storage.score,0) DESC",
    "business": "IFNULL(s_business.score,0) DESC",
    "workshop": "IFNULL(s_workshop.score,0) DESC",
    "snowcone": "IFNULL(s_snowcone.score,0) DESC",
}


@app.get("/api/properties")
def api_properties(
    q: str | None = None,
    property_type: str | None = None,
    city: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    min_acres: float | None = None,
    max_acres: float | None = None,
    min_score: float | None = None,
    has_distress: bool | None = None,
    no_flood: bool | None = None,
    road_frontage: bool | None = None,
    watchlist: bool | None = None,
    state: str | None = None,
    data_class: str | None = None,
    recommendation: str | None = None,
    sort: str = "overall",
    limit: int = 200,
    offset: int = 0,
    bbox: str | None = None,
) -> dict:
    where = ["p.excluded=0"]
    params: list[Any] = []
    if data_class:
        where.append("p.data_class=?")
        params.append(data_class)
    if property_type:
        where.append("p.property_type=?")
        params.append(property_type)
    if city:
        where.append("LOWER(p.city)=LOWER(?)")
        params.append(city)
    if min_value is not None:
        where.append("IFNULL(p.list_price, p.total_value*5) >= ?")
        params.append(min_value)
    if max_value is not None:
        where.append("IFNULL(p.list_price, p.total_value*5) <= ?")
        params.append(max_value)
    if min_acres is not None:
        where.append("IFNULL(p.acreage,0) >= ?")
        params.append(min_acres)
    if max_acres is not None:
        where.append("IFNULL(p.acreage,1e9) <= ?")
        params.append(max_acres)
    if has_distress:
        where.append("p.distress_json IS NOT NULL AND p.distress_json != '[]'")
    if no_flood:
        where.append("(p.flood_zone IS NULL OR p.flood_zone NOT LIKE 'A%' "
                     "AND p.flood_zone NOT LIKE 'V%')")
    if road_frontage:
        where.append("p.road_class IS NOT NULL AND p.road_class NOT IN ('service','track')")
    if state:
        where.append("p.state=?")
        params.append(state)
    if recommendation:
        where.append("p.recommendation=?")
        params.append(recommendation)
    if min_score is not None:
        where.append("IFNULL(so.score,0) >= ?")
        params.append(min_score)
    if watchlist:
        where.append("w.property_id IS NOT NULL")
    if q:
        like = f"%{q.strip().lower()}%"
        where.append("(LOWER(IFNULL(p.address,'')) LIKE ? OR LOWER(IFNULL(p.owner_name,'')) "
                     "LIKE ? OR LOWER(IFNULL(p.parcel_id,'')) LIKE ? OR "
                     "LOWER(IFNULL(p.legal,'')) LIKE ? OR LOWER(IFNULL(p.subdivision,'')) "
                     "LIKE ? OR LOWER(IFNULL(p.city,'')) LIKE ? OR "
                     "LOWER(IFNULL(p.zip,'')) LIKE ?)")
        params += [like] * 7
    if bbox:
        try:
            w, s, e, n = [float(x) for x in bbox.split(",")]
            where.append("p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ?")
            params += [s, n, w, e]
        except ValueError:
            pass

    order = SORTS.get(sort, SORTS["overall"])
    sql = f"""
      SELECT p.*, so.score AS overall_score, sr.score AS risk_score,
             s_rental.score AS rental_score, s_land.score AS land_score,
             s_storage.score AS storage_score, s_business.score AS business_score,
             s_workshop.score AS workshop_score, s_snowcone.score AS snowcone_score,
             (w.property_id IS NOT NULL) AS watched,
             w.priority AS watch_priority, w.notes AS watch_notes,
             w.target_price AS watch_target_price, w.desired_use AS watch_desired_use
      FROM properties p
      LEFT JOIN scores so ON so.property_id=p.id AND so.kind='overall'
      LEFT JOIN scores sr ON sr.property_id=p.id AND sr.kind='risk'
      LEFT JOIN scores s_rental ON s_rental.property_id=p.id AND s_rental.kind='rental'
      LEFT JOIN scores s_land ON s_land.property_id=p.id AND s_land.kind='land'
      LEFT JOIN scores s_storage ON s_storage.property_id=p.id AND s_storage.kind='storage'
      LEFT JOIN scores s_business ON s_business.property_id=p.id AND s_business.kind='business'
      LEFT JOIN scores s_workshop ON s_workshop.property_id=p.id AND s_workshop.kind='workshop'
      LEFT JOIN scores s_snowcone ON s_snowcone.property_id=p.id AND s_snowcone.kind='snowcone'
      LEFT JOIN watchlist w ON w.property_id=p.id
      WHERE {' AND '.join(where)}
      ORDER BY {order} NULLS LAST
      LIMIT ? OFFSET ?"""
    rows = db.q(sql, (*params, limit, offset))
    total = db.q1(f"""SELECT COUNT(*) c FROM properties p
        LEFT JOIN scores so ON so.property_id=p.id AND so.kind='overall'
        LEFT JOIN watchlist w ON w.property_id=p.id
        WHERE {' AND '.join(where)}""", params)["c"]
    props = [_prop_row(r) for r in rows]
    influence = learning.apply(props, sort)
    return {"total": total, "count": len(props), "properties": props,
            "preferences": {**influence,
                            "note": ("Your preferences have influenced this ranking. "
                                     "Scores are unchanged - properties that look like "
                                     "deals you have passed on before sit lower in the list."
                                     if influence["influenced"] else "")}}


@app.get("/api/search/natural")
def api_natural(q: str, use_ai: bool = False, limit: int = 60) -> dict:
    parsed = nlsearch.parse_with_ai(q) if use_ai else nlsearch.parse(q)
    f = dict(parsed["filters"])
    sort = f.pop("sort", "overall")
    result = api_properties(sort=sort, limit=limit, **f)
    return {"interpretation": parsed, "sort": sort, **result}


@app.get("/api/map")
def api_map(bbox: str | None = None, limit: int = 3000) -> dict:
    rows = db.q("""SELECT p.id,p.address,p.city,p.lat,p.lon,p.property_type,
                          p.total_value,p.acreage,p.recommendation,p.flood_zone,
                          p.data_class,p.distress_json,
                          so.score AS overall_score, sr.score AS risk_score,
                          (w.property_id IS NOT NULL) AS watched
                   FROM properties p
                   LEFT JOIN scores so ON so.property_id=p.id AND so.kind='overall'
                   LEFT JOIN scores sr ON sr.property_id=p.id AND sr.kind='risk'
                   LEFT JOIN watchlist w ON w.property_id=p.id
                   WHERE p.excluded=0 AND p.lat IS NOT NULL LIMIT ?""", (limit,))
    out = []
    for r in rows:
        d = _prop_row(r)
        d["marker"] = _marker(d)
        out.append(d)
    return {"count": len(out), "properties": out,
            "excluded_boundaries": _boundaries()}


def _marker(p: dict) -> str:
    sig = {s["key"] for s in p.get("distress", [])}
    if p.get("watched"):
        return "gold"
    if "possible_no_access" in sig or (p.get("flood_zone") or "")[:1] in ("A", "V"):
        return "red"
    if {"institutional_owner", "estate_owner", "government_owner"} & sig:
        return "blue"
    if (p.get("overall_score") or 0) >= 65:
        return "green"
    if sig:
        return "orange"
    if p.get("property_type") == "lot":
        return "purple"
    return "yellow"


def _boundaries() -> list[dict]:
    out = []
    for row in db.q("SELECT key,name,boundary_json FROM geographies WHERE kind='exclusion'"):
        b = jload(row["boundary_json"], {}) or {}
        out.append({"key": row["key"], "name": row["name"], "rings": b.get("rings", [])})
    return out


# ------------------------------------------------------------------ dossier

@app.get("/api/property/{prop_id}")
def api_property(prop_id: int, ai: bool = False) -> dict:
    _require(prop_id)
    return reports.dossier(prop_id, include_ai=ai)


@app.get("/api/property/{prop_id}/explain")
def api_explain(prop_id: int) -> dict:
    return analyzers.explain(_require(prop_id))


@app.get("/api/property/{prop_id}/what-would-you-do")
def api_wwyd(prop_id: int) -> dict:
    return analyzers.what_would_you_do(_require(prop_id))


@app.get("/api/property/{prop_id}/memo")
def api_memo(prop_id: int, ai: bool = True) -> dict:
    _require(prop_id)
    return reports.deal_memo(prop_id, use_ai=ai)


@app.get("/api/property/{prop_id}/report.html", response_class=HTMLResponse)
def api_report_html(prop_id: int) -> str:
    _require(prop_id)
    return reports.dossier_html(prop_id)


@app.post("/api/property/{prop_id}/financials")
def api_financials(prop_id: int, payload: dict = Body(default={})) -> dict:
    p = _require(prop_id)
    kind = payload.get("kind", "rental")
    o = payload.get("overrides") or {}
    if kind == "rental":
        return finance.rental_analysis(
            purchase_price=float(payload.get("purchase_price") or 0),
            monthly_rent=float(payload.get("monthly_rent") or 0),
            rehab=float(payload.get("rehab") or 0),
            assessed_value=float(p.get("total_value") or 0), overrides=o)
    if kind == "deal":
        return finance.max_purchase_price(
            after_repair_value=float(payload.get("after_repair_value") or 0),
            rehab=float(payload.get("rehab") or 0),
            desired_return=payload.get("desired_return"), overrides=o)
    if kind == "storage":
        return finance.storage_analysis(
            acreage=float(payload.get("acreage") or p.get("acreage") or 0),
            purchase_price=float(payload.get("purchase_price") or 0),
            usable_pct=float(payload.get("usable_pct") or 0.65), overrides=o)
    if kind == "snowcone":
        return finance.snowcone_analysis(
            road_rank=int(payload.get("road_rank") or 3),
            acreage=float(payload.get("acreage") or p.get("acreage") or 0.25),
            competitors=int(payload.get("competitors") or 0), overrides=o)
    if kind == "rehab":
        return finance.rehab_estimate(float(payload.get("sqft") or 0),
                                      payload.get("level", "medium"))
    raise HTTPException(400, f"unknown analysis kind {kind!r}")


@app.get("/api/finance/defaults")
def api_finance_defaults() -> dict:
    return {"defaults": FINANCE_DEFAULTS,
            "note": "Every one of these is an assumption you can change. Nothing here "
                    "is measured from this property."}


@app.post("/api/compare")
def api_compare(payload: dict = Body(...)) -> dict:
    ids = payload.get("ids") or []
    return analyzers.compare([int(i) for i in ids])


# -------------------------------------------------------------- investigate

@app.post("/api/property/{prop_id}/investigate")
def api_investigate(prop_id: int) -> dict:
    from .investigator import investigate
    _require(prop_id)
    return investigate(prop_id)


@app.get("/api/property/{prop_id}/investigation")
def api_investigation(prop_id: int) -> dict:
    row = db.q1("SELECT * FROM investigations WHERE property_id=? ORDER BY id DESC LIMIT 1",
                (prop_id,))
    if not row:
        return {"status": "none"}
    d = dict(row)
    d["stages"] = jload(d.pop("stages_json"), [])
    d["summary"] = jload(d.pop("summary_json"), {})
    return d


# --------------------------------------------------------------- workflow

@app.post("/api/property/{prop_id}/watch")
def api_watch(prop_id: int, payload: dict = Body(default={})) -> dict:
    _require(prop_id)
    db.ex("INSERT INTO watchlist(property_id,priority,notes,target_price,desired_use,added_at) "
          "VALUES(?,?,?,?,?,?) ON CONFLICT(property_id) DO UPDATE SET "
          "priority=excluded.priority, notes=excluded.notes, "
          "target_price=excluded.target_price, desired_use=excluded.desired_use",
          (prop_id, payload.get("priority", "normal"), payload.get("notes"),
           payload.get("target_price"), payload.get("desired_use"), utcnow()))
    store.add_timeline(prop_id, "watch", "Added to the watchlist",
                       payload.get("notes") or "")
    return {"watched": True}


@app.delete("/api/property/{prop_id}/watch")
def api_unwatch(prop_id: int) -> dict:
    db.ex("DELETE FROM watchlist WHERE property_id=?", (prop_id,))
    return {"watched": False}


@app.post("/api/property/{prop_id}/state")
def api_set_state(prop_id: int, payload: dict = Body(...)) -> dict:
    STATES = ["DISCOVERED", "INTERESTING", "INVESTIGATING", "DUE DILIGENCE",
              "OFFER READY", "OFFERED", "UNDER CONTRACT", "ACQUIRED", "REHAB",
              "READY TO RENT", "RENTED", "PORTFOLIO", "PASS", "ARCHIVED"]
    new = (payload.get("state") or "").upper()
    if new not in STATES:
        raise HTTPException(400, f"state must be one of {STATES}")
    p = _require(prop_id)
    db.ex("UPDATE properties SET state=?, updated_at=? WHERE id=?", (new, utcnow(), prop_id))
    store.add_timeline(prop_id, "state", f"Moved to {new}",
                       payload.get("reason") or "")
    if new == "PASS":
        db.ex("INSERT INTO decisions(property_id,decision,reason,detail,created_at) "
              "VALUES(?,'pass',?,?,?)",
              (prop_id, payload.get("reason") or "unspecified",
               payload.get("detail"), utcnow()))
    return {"state": new, "previous": p.get("state")}


@app.get("/api/decisions/learning")
def api_learning() -> dict:
    rows = db.q("SELECT reason, COUNT(*) n FROM decisions WHERE decision='pass' "
                "GROUP BY reason ORDER BY n DESC")
    return {"pass_reasons": db.rows_to_dicts(rows),
            "enabled": learning.enabled(),
            "how_it_is_used": {r: sorted(learning.REASON_SIGNALS.get(r, set()))
                               for r in learning.REASON_SIGNALS},
            "note": ("These are the reasons you have passed before. When a ranked list "
                     "is shown, properties carrying the same kind of problem sit lower "
                     "in it and the list says so. Scores never change and nothing is "
                     "hidden. Turn it off with POST /api/decisions/learning.")}


@app.post("/api/decisions/learning")
def api_learning_toggle(payload: dict = Body(default={})) -> dict:
    db.set_setting("learning_enabled", bool(payload.get("enabled", True)))
    return {"enabled": learning.enabled()}


@app.get("/api/tasks")
def api_tasks(status: str = "open", property_id: int | None = None,
              limit: int = 200) -> dict:
    where, params = ["t.status=?"], [status]
    if property_id:
        where.append("t.property_id=?")
        params.append(property_id)
    rows = db.q(f"""SELECT t.*, p.address FROM tasks t
                    LEFT JOIN properties p ON p.id=t.property_id
                    WHERE {' AND '.join(where)}
                    ORDER BY t.manual DESC, t.priority, t.id LIMIT ?""",
                (*params, limit))
    return {"tasks": db.rows_to_dicts(rows)}


@app.post("/api/task/{task_id}")
def api_task_update(task_id: int, payload: dict = Body(...)) -> dict:
    fields, params = [], []
    for k in ("status", "notes", "evidence", "due_date", "owner", "priority"):
        if k in payload:
            fields.append(f"{k}=?")
            params.append(payload[k])
    if not fields:
        raise HTTPException(400, "nothing to update")
    if payload.get("status") == "done":
        fields.append("completed_at=?")
        params.append(utcnow())
    params.append(task_id)
    db.ex(f"UPDATE tasks SET {','.join(fields)} WHERE id=?", params)
    row = db.q1("SELECT * FROM tasks WHERE id=?", (task_id,))
    if row and row["property_id"] and payload.get("status") == "done":
        store.add_timeline(row["property_id"], "task", f"Completed: {row['title']}",
                           payload.get("evidence") or payload.get("notes") or "")
        if payload.get("evidence"):
            store.store_evidence(row["property_id"], [{
                "field": f"manual:{row['source'] or 'check'}",
                "value": payload["evidence"], "evidence_type": "FACT",
                "confidence": "HIGH", "source": "topher_manual_verification",
                "source_name": row["title"], "source_url": row["source_url"]}])
    return {"ok": True}


@app.post("/api/property/{prop_id}/note")
def api_note(prop_id: int, payload: dict = Body(...)) -> dict:
    _require(prop_id)
    cur = db.ex("INSERT INTO notes(property_id,kind,body,author,confidence,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (prop_id, payload.get("kind", "field_note"), payload.get("body", ""),
                 payload.get("author", "Topher"),
                 payload.get("confidence", "UNVERIFIED"), utcnow()))
    store.add_timeline(prop_id, "note", "Field note added",
                       (payload.get("body") or "")[:200])
    store.store_evidence(prop_id, [{
        "field": "field_observation", "value": payload.get("body", ""),
        "evidence_type": "OBSERVATION", "confidence": "LOW",
        "source": "topher_field_note",
        "source_name": f"{payload.get('author','Topher')} on site",
        "raw_ref": "Unverified first-hand or second-hand observation. A neighbour "
                   "saying nobody has lived there is a lead, not a fact."}])
    return {"id": cur.lastrowid}


@app.get("/api/alerts")
def api_alerts(unread_only: bool = False, limit: int = 100) -> dict:
    sql = ("SELECT a.*, p.address FROM alerts a LEFT JOIN properties p ON p.id=a.property_id "
           + ("WHERE a.read_at IS NULL " if unread_only else "")
           + "ORDER BY a.id DESC LIMIT ?")
    return {"alerts": db.rows_to_dicts(db.q(sql, (limit,)))}


@app.post("/api/alerts/read")
def api_alerts_read(payload: dict = Body(default={})) -> dict:
    if payload.get("id"):
        db.ex("UPDATE alerts SET read_at=? WHERE id=?", (utcnow(), payload["id"]))
    else:
        db.ex("UPDATE alerts SET read_at=? WHERE read_at IS NULL", (utcnow(),))
    return {"ok": True}


@app.get("/api/changes")
def api_changes(limit: int = 100) -> dict:
    rows = db.q("""SELECT c.*, p.address, p.parcel_id FROM changes c
                   JOIN properties p ON p.id=c.property_id
                   WHERE p.excluded=0 ORDER BY c.id DESC LIMIT ?""", (limit,))
    return {"changes": db.rows_to_dicts(rows)}


# ------------------------------------------------------------------- scans

@app.post("/api/scan")
def api_scan(payload: dict = Body(default={})) -> dict:
    if scanner.is_running():
        return {"started": False, "reason": "a scan is already running",
                "scan": scanner.current().as_dict()}
    scan = scanner.start(mode=payload.get("mode", "distress"),
                         territory=payload.get("territory", DEFAULT_TERRITORY),
                         limit=payload.get("limit", 400),
                         enrich_top=payload.get("enrich_top", 10))
    return {"started": True, "scan": scan.as_dict()}


@app.get("/api/scan/current")
def api_scan_current() -> dict:
    cur = scanner.current()
    if cur:
        return {"running": scanner.is_running(), **cur.as_dict()}
    last = scanner.last_scan()
    return {"running": False, "last": last}


@app.get("/api/scan/stream")
async def api_scan_stream(request: Request):
    """Server-sent events carrying the scanner's real state."""
    async def gen():
        last_payload = None
        for _ in range(3600):
            if await request.is_disconnected():
                break
            cur = scanner.current()
            payload = {"running": scanner.is_running(),
                       **(cur.as_dict() if cur else {"status": "idle"})}
            blob = json.dumps(payload, default=str)
            if blob != last_payload:
                yield f"data: {blob}\n\n"
                last_payload = blob
            if not scanner.is_running() and cur and cur.status != "running":
                yield f"data: {json.dumps({'done': True}, default=str)}\n\n"
                break
            await asyncio.sleep(0.6)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/scan/modes")
def api_scan_modes() -> dict:
    return {"modes": [{"key": k, **{kk: vv for kk, vv in v.items() if kk != "where"}}
                      for k, v in scanner.PRESETS.items()]}


@app.get("/api/scan/logs")
def api_scan_logs(scan_id: int | None = None, limit: int = 200) -> dict:
    if scan_id:
        rows = db.q("SELECT * FROM logs WHERE scan_id=? ORDER BY id DESC LIMIT ?",
                    (scan_id, limit))
    else:
        rows = db.q("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
    return {"logs": db.rows_to_dicts(rows)}


@app.post("/api/seeds/check")
def api_seeds() -> dict:
    return seeds.mark_seeds()


@app.get("/api/seeds")
def api_seed_list() -> dict:
    return {"seeds": seeds.SEED_ADDRESSES, "note": seeds.SEED_NOTE}


# ----------------------------------------------------------------- sources

@app.get("/api/sources")
def api_sources() -> dict:
    rows = db.q("SELECT * FROM sources ORDER BY access, name")
    out = []
    for r in rows:
        d = dict(r)
        src = get_source(d["name"])
        if src:
            d["why_manual"] = src.why_manual
            d["what_to_check"] = src.what_to_check
        out.append(d)
    return {"sources": out}


@app.post("/api/sources/check")
def api_sources_check(payload: dict = Body(default={})) -> dict:
    names = payload.get("names")
    results = []
    for src in all_sources():
        if names and src.name not in names:
            continue
        res = src.health_check()
        src.record_attempt(res)
        results.append({"name": src.name, "label": src.label, "status": res.status,
                        "detail": res.detail, "error": res.error})
    return {"results": results}


@app.post("/api/sources/{name}/toggle")
def api_source_toggle(name: str, payload: dict = Body(default={})) -> dict:
    enabled = 1 if payload.get("enabled", True) else 0
    db.ex("UPDATE sources SET enabled=? WHERE name=?", (enabled, name))
    return {"name": name, "enabled": bool(enabled)}


@app.get("/api/health")
def api_health() -> dict:
    checks = []

    def check(name, fn):
        t = time.time()
        try:
            detail = fn()
            checks.append({"name": name, "status": "GREEN", "detail": detail,
                           "ms": round((time.time() - t) * 1000)})
        except Exception as exc:
            checks.append({"name": name, "status": "RED", "detail": str(exc),
                           "ms": round((time.time() - t) * 1000)})

    check("Database", lambda: f"{db.q1('SELECT COUNT(*) c FROM properties')['c']:,} properties")
    check("Storage", lambda: f"{sum(1 for _ in (STATIC).glob('*'))} static files")
    check("Scheduler", lambda: "background thread ready"
          if not scanner.is_running() else "scan running")
    ai = ai_status()
    checks.append({"name": "AI", "status": "GREEN" if ai["model"] else "YELLOW",
                   "detail": ai["detail"], "ms": 0})
    src_rows = db.q("SELECT name,label,status,access FROM sources")
    ok = sum(1 for r in src_rows if r["status"] == "ok")
    manual = sum(1 for r in src_rows if r["access"] != "automated")
    bad = sum(1 for r in src_rows if r["status"] == "unavailable")
    checks.append({
        "name": "Source adapters",
        "status": "GREEN" if ok and not bad else ("YELLOW" if ok else "RED"),
        "detail": f"{ok} answering, {manual} need a human, {bad} unavailable", "ms": 0})
    checks.append({
        "name": "Exclusions",
        "status": "GREEN" if len(_boundaries()) >= len(EXCLUSIONS) else "RED",
        "detail": f"{len(_boundaries())} boundary polygons cached of "
                  f"{len(EXCLUSIONS)} configured", "ms": 0})
    worst = ("RED" if any(c["status"] == "RED" for c in checks)
             else "YELLOW" if any(c["status"] == "YELLOW" for c in checks) else "GREEN")
    return {"overall": worst, "checks": checks, "generated_at": utcnow()}


@app.get("/api/exclusions")
def api_exclusions() -> dict:
    return {"configured": EXCLUSIONS,
            "rules": db.rows_to_dicts(db.q("SELECT * FROM exclusion_rules ORDER BY key, rule_kind")),
            "stats": exclusions.stats(),
            "boundaries": [{"key": b["key"], "name": b["name"],
                            "points": sum(len(r) for r in b["rings"])}
                           for b in _boundaries()]}


@app.post("/api/exclusions/{rule_id}/toggle")
def api_exclusion_toggle(rule_id: int, payload: dict = Body(default={})) -> dict:
    active = 1 if payload.get("active", True) else 0
    db.ex("UPDATE exclusion_rules SET active=? WHERE id=?", (active, rule_id))
    exclusions.refresh_cache()
    return {"id": rule_id, "active": bool(active)}


# ---------------------------------------------------------------- approvals

@app.post("/api/approvals")
def api_request_approval(payload: dict = Body(...)) -> dict:
    action = payload.get("action")
    if action not in APPROVAL_REQUIRED_ACTIONS:
        raise HTTPException(400, f"action must be one of {APPROVAL_REQUIRED_ACTIONS}")
    cur = db.ex("INSERT INTO approvals(action,property_id,payload_json,status,requested_at) "
                "VALUES(?,?,?,'pending',?)",
                (action, payload.get("property_id"), jdump(payload.get("payload")), utcnow()))
    return {"id": cur.lastrowid, "status": "pending",
            "message": "Nothing happens until you approve this yourself."}


@app.get("/api/approvals")
def api_approvals(status: str = "pending") -> dict:
    return {"approvals": db.rows_to_dicts(
        db.q("SELECT * FROM approvals WHERE status=? ORDER BY id DESC", (status,))),
        "actions_requiring_approval": APPROVAL_REQUIRED_ACTIONS}


@app.post("/api/approvals/{approval_id}")
def api_decide_approval(approval_id: int, payload: dict = Body(...)) -> dict:
    decision = payload.get("decision")
    if decision not in ("approved", "declined"):
        raise HTTPException(400, "decision must be 'approved' or 'declined'")
    db.ex("UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=?",
          (decision, utcnow(), payload.get("by", "Topher"), approval_id))
    return {"id": approval_id, "status": decision}


# ---------------------------------------------------------------- portfolio

@app.get("/api/portfolio")
def api_portfolio() -> dict:
    rows = db.q("""SELECT p.*, pf.* FROM portfolio pf
                   JOIN properties p ON p.id=pf.property_id""")
    props = db.rows_to_dicts(rows)
    monthly_rent = db.q1("SELECT IFNULL(SUM(rent),0) r FROM leases WHERE status='active'")["r"]
    expenses = db.q1("SELECT IFNULL(SUM(amount),0) a FROM ledger WHERE direction='expense'")["a"]
    revenue = db.q1("SELECT IFNULL(SUM(amount),0) a FROM ledger WHERE direction='revenue'")["a"]
    value = sum(p.get("current_value") or 0 for p in props)
    debt = sum(p.get("loan_amount") or 0 for p in props)
    return {"properties": props, "count": len(props),
            "units": sum(p.get("units") or 1 for p in props),
            "monthly_rent": monthly_rent, "annual_rent": monthly_rent * 12,
            "expenses_to_date": expenses, "revenue_to_date": revenue,
            "portfolio_value": value, "debt": debt, "equity": value - debt,
            "noi_estimate": monthly_rent * 12 * 0.55,
            "note": "Portfolio figures come from what you have entered, not from any "
                    "outside source."}


@app.post("/api/portfolio/{prop_id}")
def api_portfolio_add(prop_id: int, payload: dict = Body(...)) -> dict:
    _require(prop_id)
    cols = ["purchase_price", "purchase_date", "closing_costs", "rehab_budget",
            "rehab_actual", "loan_amount", "interest_rate", "term_years",
            "insurance_annual", "taxes_annual", "current_value", "units", "notes"]
    vals = [payload.get(c) for c in cols]
    db.ex(f"INSERT INTO portfolio(property_id,{','.join(cols)},created_at,updated_at) "
          f"VALUES(?,{','.join('?'*len(cols))},?,?) "
          f"ON CONFLICT(property_id) DO UPDATE SET "
          + ",".join(f"{c}=excluded.{c}" for c in cols) + ", updated_at=excluded.updated_at",
          (prop_id, *vals, utcnow(), utcnow()))
    db.ex("UPDATE properties SET state='PORTFOLIO' WHERE id=?", (prop_id,))
    store.add_timeline(prop_id, "portfolio", "Added to the portfolio", "")
    return {"ok": True}


@app.get("/api/rehab/{prop_id}")
def api_rehab(prop_id: int) -> dict:
    projects = db.rows_to_dicts(
        db.q("SELECT * FROM rehab_projects WHERE property_id=? ORDER BY id", (prop_id,)))
    for pr in projects:
        pr["tasks"] = db.rows_to_dicts(
            db.q("SELECT * FROM rehab_tasks WHERE project_id=? ORDER BY id", (pr["id"],)))
        pr["budget_total"] = sum(t.get("budget") or 0 for t in pr["tasks"])
        pr["actual_total"] = sum(t.get("actual") or 0 for t in pr["tasks"])
    photos = db.rows_to_dicts(
        db.q("SELECT * FROM photos WHERE property_id=? AND kind IN "
             "('before','during','after','rehab') ORDER BY id", (prop_id,)))
    return {"projects": projects, "photos": photos}


@app.post("/api/rehab/{prop_id}/project")
def api_rehab_project(prop_id: int, payload: dict = Body(...)) -> dict:
    cur = db.ex("INSERT INTO rehab_projects(property_id,name,status,budget,notes,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (prop_id, payload.get("name", "Rehab"), payload.get("status", "planned"),
                 payload.get("budget"), payload.get("notes"), utcnow()))
    return {"id": cur.lastrowid}


@app.post("/api/rehab/task")
def api_rehab_task(payload: dict = Body(...)) -> dict:
    cur = db.ex("INSERT INTO rehab_tasks(project_id,property_id,category,title,status,"
                "budget,actual,contractor,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (payload.get("project_id"), payload.get("property_id"),
                 payload.get("category", "general"), payload.get("title", "Task"),
                 payload.get("status", "todo"), payload.get("budget"),
                 payload.get("actual"), payload.get("contractor"),
                 payload.get("notes"), utcnow()))
    return {"id": cur.lastrowid}


# ------------------------------------------------------------------ exports

@app.get("/api/export/properties.csv", response_class=PlainTextResponse)
def api_export_csv(limit: int = 5000) -> str:
    data = api_properties(limit=limit)
    return reports.properties_csv(data["properties"])


@app.get("/api/education")
def api_education(term: str | None = None, property_id: int | None = None) -> dict:
    from .education import GLOSSARY, explain_term
    if term:
        return explain_term(term, property_id)
    return {"terms": sorted(GLOSSARY.keys())}


# --------------------------------------------------------------------- ask

@app.get("/api/ask")
def api_ask(q: str, use_ai: bool = False) -> dict:
    """Daniel's door: a plain question in, structured results out."""
    if not q.strip():
        raise HTTPException(400, "ask something")
    return askmod.ask(q, use_ai=use_ai)


@app.post("/api/ask")
def api_ask_post(payload: dict = Body(...)) -> dict:
    return api_ask(payload.get("q", ""), bool(payload.get("use_ai", False)))


# -------------------------------------------------------------------- near

@app.get("/api/near")
def api_near(lat: float, lon: float, radius_m: float = 1500, limit: int = 25) -> dict:
    """Field mode: what is around me, nearest first."""
    d = radius_m / 111000.0
    rows = db.q("""SELECT p.*, so.score AS overall_score, sr.score AS risk_score,
                          (w.property_id IS NOT NULL) AS watched
                   FROM properties p
                   LEFT JOIN scores so ON so.property_id=p.id AND so.kind='overall'
                   LEFT JOIN scores sr ON sr.property_id=p.id AND sr.kind='risk'
                   LEFT JOIN watchlist w ON w.property_id=p.id
                   WHERE p.excluded=0 AND p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ?""",
                (lat - d, lat + d, lon - d * 1.25, lon + d * 1.25))
    out = []
    for r in rows:
        pr = _prop_row(r)
        pr["distance_m"] = round(geo.haversine_m(lon, lat, pr["lon"], pr["lat"]))
        if pr["distance_m"] <= radius_m:
            pr["marker"] = _marker(pr)
            out.append(pr)
    out.sort(key=lambda x: x["distance_m"])
    return {"count": len(out[:limit]), "radius_m": radius_m, "properties": out[:limit]}


# ------------------------------------------------------------------ imagery

@app.post("/api/property/{prop_id}/imagery")
def api_fetch_imagery(prop_id: int, force: bool = False) -> dict:
    p = _require(prop_id)
    results = {}
    for name in ("ar_gis_imagery", "ar_gis_terrain"):
        src = get_source(name)
        if not src:
            continue
        res = src.enrich(p, force=force) if name == "ar_gis_imagery" else src.enrich(p)
        src.record_attempt(res)
        for rec in res.records:
            store.store_evidence(prop_id, rec.evidence)
            store.snapshot(prop_id, name, rec.raw or {})
        results[name] = {"status": res.status, "detail": res.detail, "error": res.error}
    if "ar_gis_terrain" in results and results["ar_gis_terrain"]["status"] == "ok":
        scoring.compute(store.get_property(prop_id))
    return {"results": results,
            "photos": db.rows_to_dicts(db.q(
                "SELECT * FROM photos WHERE property_id=? AND kind='aerial' ORDER BY captured_at",
                (prop_id,)))}


@app.post("/api/photo/{photo_id}/analyse")
def api_analyse_photo(photo_id: int) -> dict:
    res = vision.analyse_photo_record(photo_id)
    if "error" in res:
        raise HTTPException(503, res["error"])
    return res


@app.get("/api/vision/status")
def api_vision_status() -> dict:
    m = vision.available_model()
    return {"model": m, "ready": bool(m),
            "detail": ("ready" if m else "no local vision model running - pull llava "
                                          "with Ollama to enable image observations")}


# ---------------------------------------------------------------- filters

@app.get("/api/filters/saved")
def api_saved_filters() -> dict:
    return {"filters": db.setting("saved_filters", []) or []}


@app.post("/api/filters/saved")
def api_save_filter(payload: dict = Body(...)) -> dict:
    name = (payload.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "name required")
    saved = [f for f in (db.setting("saved_filters", []) or []) if f.get("name") != name]
    saved.append({"name": name, "params": payload.get("params") or {}, "saved_at": utcnow()})
    db.set_setting("saved_filters", saved)
    return {"filters": saved}


@app.delete("/api/filters/saved/{name}")
def api_delete_filter(name: str) -> dict:
    saved = [f for f in (db.setting("saved_filters", []) or []) if f.get("name") != name]
    db.set_setting("saved_filters", saved)
    return {"filters": saved}


@app.get("/api/cities")
def api_cities() -> dict:
    return {"cities": [r["city"] for r in db.q(
        "SELECT DISTINCT city FROM properties WHERE excluded=0 AND city IS NOT NULL "
        "ORDER BY city")]}


# ---------------------------------------------------------------- schedule

@app.get("/api/schedule")
def api_schedule() -> dict:
    return scheduler.config()


@app.post("/api/schedule")
def api_schedule_update(payload: dict = Body(default={})) -> dict:
    if "interval_hours" in payload:
        try:
            h = float(payload["interval_hours"])
        except (TypeError, ValueError):
            raise HTTPException(400, "interval_hours must be a number")
        if not (1 <= h <= 24 * 14):
            raise HTTPException(400, "interval_hours must be between 1 and 336")
        payload["interval_hours"] = h
    if "mode" in payload and payload["mode"] not in scanner.PRESETS:
        raise HTTPException(400, f"mode must be one of {list(scanner.PRESETS)}")
    return scheduler.update(payload)


@app.post("/api/schedule/run-now")
def api_schedule_run_now() -> dict:
    out = scheduler.run_now(reason="requested from the UI")
    if out is None:
        return {"started": False, "reason": "a scan is already running"}
    return {"started": True, "scan": out}


# ----------------------------------------------------------------- uploads

@app.post("/api/property/{prop_id}/photo")
async def api_photo(prop_id: int, file: UploadFile = File(...),
                    kind: str = Form("inspection"), caption: str = Form("")) -> dict:
    _require(prop_id)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "photos must be image files")
    try:
        return files.save_photo(prop_id, file.file, file.filename or "", kind, caption)
    except ValueError as exc:
        raise HTTPException(413, str(exc))


@app.post("/api/property/{prop_id}/voice")
async def api_voice(prop_id: int, file: UploadFile = File(...),
                    transcript: str = Form("")) -> dict:
    _require(prop_id)
    ct = file.content_type or ""
    if not (ct.startswith("audio/") or ct.startswith("video/") or ct == "application/octet-stream"):
        raise HTTPException(400, "voice notes must be audio files")
    try:
        return files.save_voice(prop_id, file.file, file.filename or "note.webm", transcript)
    except ValueError as exc:
        raise HTTPException(413, str(exc))


@app.post("/api/property/{prop_id}/document")
async def api_document(prop_id: int, file: UploadFile = File(...),
                       category: str = Form("other"), title: str = Form(""),
                       notes: str = Form("")) -> dict:
    _require(prop_id)
    try:
        return files.save_document(prop_id, file.file, file.filename or "", category,
                                   title, notes)
    except ValueError as exc:
        raise HTTPException(413, str(exc))


@app.post("/api/note/{note_id}")
def api_note_update(note_id: int, payload: dict = Body(...)) -> dict:
    row = db.q1("SELECT * FROM notes WHERE id=?", (note_id,))
    if not row:
        raise HTTPException(404, "no such note")
    if "body" in payload:
        db.ex("UPDATE notes SET body=? WHERE id=?", (payload["body"], note_id))
        if row["kind"] == "voice_note" and payload["body"].strip():
            store.store_evidence(row["property_id"], [{
                "field": "field_observation", "value": payload["body"],
                "evidence_type": "OBSERVATION", "confidence": "LOW",
                "source": "topher_voice_note", "source_name": "Topher on site (voice)",
                "source_url": row["audio_path"]}])
    return {"ok": True}


@app.post("/api/photo/{photo_id}")
def api_photo_update(photo_id: int, payload: dict = Body(...)) -> dict:
    sets, vals = [], []
    if "kind" in payload and payload["kind"] in files.PHOTO_KINDS:
        sets.append("kind=?"); vals.append(payload["kind"])
    if "caption" in payload:
        sets.append("caption=?"); vals.append(payload["caption"])
    if not sets:
        raise HTTPException(400, "nothing to update")
    vals.append(photo_id)
    db.ex(f"UPDATE photos SET {','.join(sets)} WHERE id=?", vals)
    return {"ok": True}


# --------------------------------------------------------------------- pdf

@app.get("/api/property/{prop_id}/report.pdf")
def api_report_pdf(prop_id: int):
    p = _require(prop_id)
    html = reports.dossier_html(prop_id)
    try:
        data = pdf.html_to_pdf(html)
    except Exception as exc:
        raise HTTPException(501, f"PDF export unavailable: {exc}")
    name = (p.get("address") or p.get("parcel_id") or f"property-{prop_id}")
    name = "".join(c if c.isalnum() else "-" for c in name).strip("-")[:60]
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


# ------------------------------------------------------- portfolio ledger

@app.post("/api/property/{prop_id}/ledger")
def api_ledger_add(prop_id: int, payload: dict = Body(...)) -> dict:
    _require(prop_id)
    direction = payload.get("direction")
    if direction not in ("expense", "revenue"):
        raise HTTPException(400, "direction must be expense or revenue")
    try:
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(400, "amount must be a number")
    cur = db.ex("INSERT INTO ledger(property_id,direction,category,amount,occurred_on,"
                "memo,created_at) VALUES(?,?,?,?,?,?,?)",
                (prop_id, direction, payload.get("category") or "general", amount,
                 payload.get("occurred_on") or utcnow()[:10], payload.get("memo"), utcnow()))
    return {"id": cur.lastrowid}


@app.get("/api/property/{prop_id}/ledger")
def api_ledger(prop_id: int) -> dict:
    rows = db.rows_to_dicts(db.q("SELECT * FROM ledger WHERE property_id=? "
                                 "ORDER BY occurred_on DESC, id DESC", (prop_id,)))
    exp = sum(r["amount"] for r in rows if r["direction"] == "expense")
    rev = sum(r["amount"] for r in rows if r["direction"] == "revenue")
    return {"entries": rows, "expenses": exp, "revenue": rev, "net": rev - exp}


@app.post("/api/property/{prop_id}/lease")
def api_lease_add(prop_id: int, payload: dict = Body(...)) -> dict:
    _require(prop_id)
    cur = db.ex("INSERT INTO leases(property_id,tenant_name,unit,rent,deposit,start_date,"
                "end_date,status,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (prop_id, payload.get("tenant_name"), payload.get("unit"),
                 payload.get("rent"), payload.get("deposit"), payload.get("start_date"),
                 payload.get("end_date"), payload.get("status", "active"),
                 payload.get("notes"), utcnow()))
    if payload.get("status", "active") == "active":
        db.ex("UPDATE properties SET state='RENTED' WHERE id=? AND state IN "
              "('READY TO RENT','REHAB','ACQUIRED','PORTFOLIO')", (prop_id,))
    return {"id": cur.lastrowid}


@app.get("/api/property/{prop_id}/leases")
def api_leases(prop_id: int) -> dict:
    return {"leases": db.rows_to_dicts(
        db.q("SELECT * FROM leases WHERE property_id=? ORDER BY id DESC", (prop_id,)))}


@app.post("/api/rehab/task/{task_id}")
def api_rehab_task_update(task_id: int, payload: dict = Body(...)) -> dict:
    sets, vals = [], []
    for k in ("status", "budget", "actual", "contractor", "notes", "started_at",
              "finished_at", "title", "category"):
        if k in payload:
            sets.append(f"{k}=?"); vals.append(payload[k])
    if not sets:
        raise HTTPException(400, "nothing to update")
    vals.append(task_id)
    db.ex(f"UPDATE rehab_tasks SET {','.join(sets)} WHERE id=?", vals)
    return {"ok": True}


# ------------------------------------------------------------------ ui

FILES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # Cache-bust the assets on their own mtimes so a reload always gets the
    # front end that matches the running backend.
    stamp = str(int(max((STATIC / "app.js").stat().st_mtime,
                        (STATIC / "styles.css").stat().st_mtime)))
    html = (STATIC / "index.html").read_text().replace("__V__", stamp)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon():
    p = STATIC / "favicon.svg"
    return FileResponse(p) if p.exists() else JSONResponse({}, status_code=404)
