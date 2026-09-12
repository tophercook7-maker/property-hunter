"""The scan pipeline (spec 14/15/75/76).

Every stage in this file corresponds to real work against a real source. The
progress the UI shows is this object's actual state - there is no decorative
animation pretending something is happening. A source that fails says so, and
the funnel numbers at the end are counted, never invented.
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from . import db, distress, exclusions, scoring, store
from .config import DEFAULT_TERRITORY, TERRITORIES
from .db import jdump, utcnow
from .sources import all_sources, get_source, register_all
from .sources.base import AUTOMATED, MANUAL_ONLY, OK

# Discovery presets: real WHERE clauses against the parcel layer.
# Each one is a different way of asking "show me something interesting".
PRESETS = {
    "seeds": {
        "label": "Investigation seeds",
        "description": "Re-verify the known investigation addresses from scratch.",
    },
    "distress": {
        "label": "Distress sweep",
        "description": "Improved parcels the county values very low, plus estate, "
                       "lender and government owners.",
        "where": ("(parceltype LIKE 'R%' AND impvalue > 0 AND impvalue < 20000) "
                  "OR UPPER(ownername) LIKE '%ESTATE%' "
                  "OR UPPER(ownername) LIKE '%HEIRS%' "
                  "OR UPPER(ownername) LIKE '%BANK%' "
                  "OR UPPER(ownername) LIKE '%HUD%' "
                  "OR UPPER(ownername) LIKE 'CITY OF%' "
                  "OR UPPER(ownername) LIKE 'COUNTY OF%'"),
    },
    "cheap_land": {
        "label": "Cheap land",
        "description": "Vacant parcels with a low land value.",
        "where": "parceltype IN ('RV','CV','AV','IV') AND landvalue > 0 AND landvalue < 25000",
    },
    "rental_candidates": {
        "label": "Rental candidates",
        "description": "Improved residential parcels in a workable value band.",
        "where": "parceltype LIKE 'R%' AND impvalue BETWEEN 15000 AND 120000",
    },
    "commercial": {
        "label": "Business sites",
        "description": "Commercial and industrial parcels.",
        "where": "parceltype LIKE 'C%' OR parceltype LIKE 'I%'",
    },
    "city_registers": {
        "label": "City distress registers",
        "description": "Only the parcels the City itself lists as vacant, liened, or "
                       "under a code case - then the county record for each.",
        "where": "",
    },
    "full": {
        "label": "Everything",
        "description": "Every parcel in the county. Slow - thousands of requests.",
        "where": "",
    },
}

STAGES = [
    ("boundaries", "Refreshing the exclusion boundaries"),
    ("discovery", "Reading county parcel records"),
    ("identity", "Matching records to properties we already know"),
    ("exclusion", "Applying the Hot Springs Village / Diamondhead exclusions"),
    ("city_registers", "Reading the City's vacancy, lien and code registers"),
    ("distress", "Looking for distress signals"),
    ("structures", "Checking for buildings on the ground"),
    ("flood", "Checking FEMA flood zones"),
    ("access", "Checking road access"),
    ("city", "Checking City zoning, utilities, liens and vacancy"),
    ("terrain", "Reading the lay of the land"),
    ("imagery", "Pulling aerial photos"),
    ("context", "Checking what is nearby"),
    ("manual", "Reporting the sources a human has to check"),
    ("changes", "Comparing against what we saw last time"),
    ("scoring", "Scoring and ranking"),
    ("report", "Writing it up"),
]


@dataclass
class Stage:
    key: str
    label: str
    status: str = "waiting"        # waiting|running|done|failed|skipped|unavailable
    detail: str = ""
    done: int = 0
    total: int = 0
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status,
                "detail": self.detail, "done": self.done, "total": self.total,
                "started_at": self.started_at, "finished_at": self.finished_at}


class Scan:
    """One run of the pipeline. Thread-safe enough for one background worker."""

    def __init__(self, mode: str = "distress", territory: str = DEFAULT_TERRITORY,
                 limit: int | None = 400, enrich_top: int = 12):
        self.mode = mode
        self.territory = territory
        self.limit = limit
        self.enrich_top = enrich_top
        self.stages = [Stage(k, l) for k, l in STAGES]
        self.stats: dict[str, Any] = {
            "records_examined": 0, "properties_matched": 0, "new_properties": 0,
            "updated_properties": 0, "excluded": 0, "candidates": 0,
            "strong_candidates": 0, "picks": 0, "changes": 0,
            "sources_ok": 0, "sources_unavailable": 0, "manual_tasks": 0,
        }
        self.status = "running"
        self.error = ""
        self.id: int | None = None
        self.touched: list[int] = []
        self._lock = threading.Lock()

    # -- bookkeeping ----------------------------------------------------
    def stage(self, key: str) -> Stage:
        return next(s for s in self.stages if s.key == key)

    def begin(self, key: str, detail: str = "", total: int = 0) -> Stage:
        s = self.stage(key)
        s.status, s.detail, s.total, s.started_at = "running", detail, total, utcnow()
        self.save()
        return s

    def finish(self, key: str, status: str = "done", detail: str = "") -> None:
        s = self.stage(key)
        s.status = status
        if detail:
            s.detail = detail
        s.finished_at = utcnow()
        self.save()

    def tick(self, key: str, done: int, total: int | None = None, detail: str = "") -> None:
        s = self.stage(key)
        s.done = done
        if total is not None:
            s.total = total
        if detail:
            s.detail = detail
        self.save()

    def save(self) -> None:
        with self._lock:
            payload = (jdump([s.as_dict() for s in self.stages]), jdump(self.stats),
                       self.status, self.error or None)
            if self.id is None:
                cur = db.ex("INSERT INTO scans(mode,territory,status,stages_json,stats_json,"
                            "started_at) VALUES(?,?,?,?,?,?)",
                            (self.mode, self.territory, self.status, payload[0],
                             payload[1], utcnow()))
                self.id = cur.lastrowid
            else:
                db.ex("UPDATE scans SET stages_json=?, stats_json=?, status=?, error=?, "
                      "finished_at=? WHERE id=?",
                      (*payload, utcnow() if self.status != "running" else None, self.id))

    def log(self, message: str, level: str = "info", source: str = "",
            stage: str = "", data: Any = None) -> None:
        db.log(self.id, message, level, source or None, stage or None, data)

    def as_dict(self) -> dict:
        return {"id": self.id, "mode": self.mode, "territory": self.territory,
                "status": self.status, "error": self.error,
                "stages": [s.as_dict() for s in self.stages], "stats": self.stats,
                "funnel": self.funnel()}

    def funnel(self) -> list[dict]:
        s = self.stats
        return [
            {"label": "records examined", "value": s["records_examined"]},
            {"label": "properties matched", "value": s["properties_matched"]},
            {"label": "after exclusions", "value": max(0, s["properties_matched"] - s["excluded"])},
            {"label": "investigation candidates", "value": s["candidates"]},
            {"label": "strong candidates", "value": s["strong_candidates"]},
            {"label": "Topher picks", "value": s["picks"]},
        ]

    # -- the pipeline ---------------------------------------------------
    def run(self) -> dict:
        try:
            register_all()
            self._boundaries()
            records = self._discovery()
            self._ingest(records)
            self._exclusion()
            self._city_registers()
            self._distress()
            self._enrich()
            self._manual()
            self._changes()
            self._scoring()
            self._report()
            self.status = "complete"
        except Exception as exc:                      # pragma: no cover - safety net
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            self.log(traceback.format_exc(), level="error")
            for s in self.stages:
                if s.status == "running":
                    s.status = "failed"
                    s.detail = self.error
        self.save()
        return self.as_dict()

    # ------------------------------------------------------------------
    def _boundaries(self) -> None:
        self.begin("boundaries")
        src = get_source("census_boundaries")
        result = src.discover()
        src.record_attempt(result)
        exclusions.refresh_cache()
        exclusions.sync_rules_to_db()
        if result.status == OK:
            self._count_source(True)
            self.finish("boundaries", "done", result.detail)
        else:
            self._count_source(False)
            self.finish("boundaries", "unavailable",
                        f"{result.detail} - falling back to the boundaries already "
                        f"cached locally")

    def _discovery(self) -> list:
        preset = PRESETS.get(self.mode, PRESETS["distress"])
        self.begin("discovery", preset["description"])
        src = get_source("ar_gis_parcels")
        if self.mode == "seeds":
            from .seeds import seed_where
            where = seed_where()
        elif self.mode == "city_registers":
            # The registers are ingested in their own stage; here we only read the
            # tax-roll record for the addresses they name, so nothing is discovered
            # from the roll at large.
            self.finish("discovery", "skipped",
                        "parcel records are matched from the City registers instead")
            return []
        else:
            where = preset.get("where", "")
        try:
            total = src.count(f"countyfips='{self._fips()}'"
                              + (f" AND ({where})" if where else ""))
        except Exception as exc:
            self._count_source(False)
            self.finish("discovery", "failed", f"could not reach the parcel service: {exc}")
            raise
        target = min(total, self.limit) if self.limit else total
        self.tick("discovery", 0, target, f"{total:,} parcels match; reading {target:,}")
        result = src.discover(territory=self.territory, where_extra=where,
                              limit=self.limit,
                              progress=lambda done, tot: self.tick("discovery", done, tot))
        src.record_attempt(result)
        if result.status != OK:
            self._count_source(False)
            self.finish("discovery", "unavailable", result.detail)
        else:
            self._count_source(True)
            self.finish("discovery", "done", result.detail)
        self.stats["records_examined"] = len(result.records)
        self.log(f"discovery read {len(result.records)} parcel records",
                 source="ar_gis_parcels", stage="discovery")
        return result.records

    def _fips(self) -> str:
        return next(t["county_fips"] for t in TERRITORIES if t["key"] == self.territory)

    def _ingest(self, records: list) -> None:
        self.begin("identity", total=len(records))
        new = updated = 0
        changes = 0
        for i, rec in enumerate(records, 1):
            pid, action, ch = store.ingest(rec)
            self.touched.append(pid)
            if action == "created":
                new += 1
            elif action == "updated":
                updated += 1
            changes += len(ch)
            if i % 25 == 0 or i == len(records):
                self.tick("identity", i, len(records),
                          f"{new} new, {updated} updated")
        self.stats.update(properties_matched=len(set(self.touched)),
                          new_properties=new, updated_properties=updated,
                          changes=changes)
        self.finish("identity", "done",
                    f"{len(set(self.touched)):,} distinct properties "
                    f"({new} new, {updated} updated)")

    def _exclusion(self) -> None:
        self.begin("exclusion")
        excluded = 0
        for pid in set(self.touched):
            row = db.q1("SELECT excluded FROM properties WHERE id=?", (pid,))
            if row and row["excluded"]:
                excluded += 1
        self.stats["excluded"] = excluded
        total_excluded = db.q1("SELECT COUNT(*) c FROM properties WHERE excluded=1")["c"]
        self.finish("exclusion", "done",
                    f"{excluded} of this batch excluded "
                    f"({total_excluded} excluded overall)")

    CITY_REGISTERS = ("hs_gis_vacant", "hs_gis_liens", "hs_gis_code_cases")

    REGISTER_FIELDS = {"hs_gis_vacant": ("vacant_structure", "vacant-structure register"),
                       "hs_gis_liens": ("cleanup_lien", "City lien parcels"),
                       "hs_gis_code_cases": ("code_case_open", "2025 open code cases")}

    def _register_removals(self, name: str, present: set[int]) -> int:
        """Properties that carried this register's evidence last time but are not
        on it now. A house coming off the vacant register, or a lien parcel
        dropping off the lien layer, is a change worth telling Topher about -
        phrased as what we observed, not as what it means."""
        field, label = self.REGISTER_FIELDS[name]
        rows = db.q("""SELECT DISTINCT e.property_id, p.address, p.parcel_id
                       FROM evidence e JOIN properties p ON p.id=e.property_id
                       WHERE e.field=? AND e.source=? AND p.excluded=0
                       AND NOT EXISTS (SELECT 1 FROM evidence r WHERE r.property_id=e.property_id
                                       AND r.field=? AND r.id > e.id)""",
                    (field, name, field + "_removed"))
        n = 0
        for r in rows:
            if r["property_id"] in present:
                continue
            store.store_evidence(r["property_id"], [{
                "field": field + "_removed",
                "value": f"no longer on the City's {label} as of this scan",
                "evidence_type": "OBSERVATION", "confidence": "MEDIUM", "source": name,
                "source_name": f"City of Hot Springs GIS ({label})",
                "raw_ref": "It left the layer. That could mean resolved, demolished, sold, "
                           "paid off, or just re-edited - the record does not say which."}])
            store.add_timeline(r["property_id"], "change", f"Came off the City's {label}",
                               "observed by comparing this scan with the last", source=name)
            store.add_alert(r["property_id"], "register_removed",
                            f"{r['address'] or r['parcel_id']} - came off the {label}",
                            "Worth finding out why: resolved, demolished, sold, or paid off.",
                            "medium")
            n += 1
        return n

    def _city_registers(self) -> None:
        """The City's three distress registers are small (a few hundred rows each),
        so every scan reads all of them and folds them onto the properties we know
        - or creates the property if the tax roll had not surfaced it yet."""
        self.begin("city_registers")
        details, total_new, total_seen = [], 0, 0
        for name in self.CITY_REGISTERS:
            src = get_source(name)
            if not src or not src.enabled():
                continue
            res = src.discover()
            src.record_attempt(res)
            if res.status != OK:
                self._count_source(False)
                details.append(f"{src.label.split(' - ')[-1]}: unavailable")
                self.log(f"{name}: {res.detail}", level="warn", source=name, stage="city_registers")
                continue
            self._count_source(True)
            new = seen = 0
            present: set[int] = set()
            for rec in res.records:
                pid, action, _ = store.ingest(rec)
                self.touched.append(pid)
                present.add(pid)
                if action == "created":
                    new += 1
                else:
                    seen += 1
            gone = self._register_removals(name, present)
            if gone:
                details.append(f"{gone} came off the {self.REGISTER_FIELDS[name][1]}")
                self.stats["changes"] += gone
            total_new += new
            total_seen += seen
            # Register rows are records we examined, whatever mode we are in.
            self.stats["records_examined"] += len(res.records)
            details.append(f"{len(res.records)} {src.label.split(' - ')[-1]} ({new} new)")
        self.stats["properties_matched"] = len(set(self.touched))
        self.stats["new_properties"] += total_new
        self.finish("city_registers", "done" if details else "skipped",
                    "; ".join(details) or "no City registers enabled")

    def _distress(self) -> None:
        ids = [i for i in set(self.touched)
               if not (db.q1("SELECT excluded FROM properties WHERE id=?", (i,)) or {})["excluded"]]
        self.begin("distress", total=len(ids))
        candidates = 0
        for n, pid in enumerate(ids, 1):
            p = store.get_property(pid)
            if not p:
                continue
            sigs = distress.refresh(p)
            if distress.score_of(sigs) >= 1:
                candidates += 1
            if n % 25 == 0 or n == len(ids):
                self.tick("distress", n, len(ids), f"{candidates} with at least one signal")
        self.stats["candidates"] = candidates
        self.finish("distress", "done",
                    f"{candidates} of {len(ids)} carry at least one distress signal")

    def _enrich(self) -> None:
        """Enrich only the most promising properties - these calls are expensive."""
        ids = self._top_candidates(self.enrich_top)
        for key, source_name, kwargs, budget in (
                ("structures", "ar_gis_footprints", {}, 180.0),
                ("flood", "fema_nfhl", {}, 180.0),
                ("access", "ar_gis_roads", {}, 180.0),
                ("city", "hs_gis_zoning", {}, 240.0),
                ("terrain", "ar_gis_terrain", {}, 120.0),
                ("imagery", "ar_gis_imagery", {}, 180.0),
                # Overpass is a shared free service and answers in ~45 s, so it
                # only ever looks at a handful of the very best candidates.
                ("context", "osm_overpass", {}, 120.0)):
            src = get_source(source_name)
            if not src or not src.enabled():
                self.finish(key, "skipped", "source disabled")
                continue
            stage_ids = ids[:3] if key == "context" else ids
            self.begin(key, f"checking {len(stage_ids)} strongest candidates",
                       total=len(stage_ids))
            ok = fail = skipped = 0
            last_detail = ""
            deadline = time.monotonic() + budget
            city_srcs = ([get_source(n) for n in ("hs_gis_zoning", "hs_gis_utilities",
                                                   "hs_gis_liens", "hs_gis_vacant",
                                                   "hs_gis_code_cases", "hs_gis_city_property",
                                                   "hs_gis_owner_mailing")]
                         if key == "city" else [src])
            for n, pid in enumerate(stage_ids, 1):
                if time.monotonic() > deadline:
                    skipped = len(stage_ids) - n + 1
                    self.log(f"{source_name}: {budget:.0f}s budget reached, "
                             f"{skipped} candidates not checked",
                             level="warn", source=source_name, stage=key)
                    break
                p = store.get_property(pid)
                if not p:
                    continue
                res = self._enrich_many(city_srcs, p, kwargs) if key == "city" \
                    else src.enrich(p, **kwargs)
                if res.status == OK:
                    ok += 1
                    last_detail = res.detail
                    for rec in res.records:
                        store.store_evidence(pid, rec.evidence)
                        # Only real property columns are written. An adapter that
                        # returns something else must not be able to kill a scan.
                        cols = {k: v for k, v in (rec.fields or {}).items()
                                if k in store.WRITABLE and v is not None}
                        if cols:
                            sets = ",".join(f"{k}=?" for k in cols)
                            db.ex(f"UPDATE properties SET {sets} WHERE id=?",
                                  (*cols.values(), pid))
                        store.snapshot(pid, source_name, rec.raw or rec.fields)
                else:
                    fail += 1
                self.tick(key, n, len(stage_ids), f"{ok} ok, {fail} unavailable")
            src.record_attempt(SimpleResult(OK if ok else "unavailable",
                                            f"{ok} enriched, {fail} unavailable"))
            self._count_source(bool(ok))
            self.finish(key, "done" if ok else "unavailable",
                        f"{ok} checked, {fail} could not be checked"
                        + (f", {skipped} skipped (took too long)" if skipped else "")
                        + (f" - last: {last_detail}" if last_detail else ""))

    @staticmethod
    def _enrich_many(sources, p: dict, kwargs) -> "SimpleResult":
        """Run several adapters on one property and merge their records."""
        merged = SimpleResult(OK, "")
        details = []
        for src in sources:
            if not src:
                continue
            res = src.enrich(p, **kwargs)
            src.record_attempt(res)
            if res.status == OK:
                merged.records += res.records
                if res.detail:
                    details.append(res.detail[:40])
                for rec in res.records:
                    store.store_timeline(p["id"], rec.timeline)
        merged.detail = "; ".join(details[:3])
        if not merged.records:
            merged.status = "unavailable"
        return merged

    def _top_candidates(self, n: int) -> list[int]:
        rows = db.q("""SELECT id FROM properties
                       WHERE excluded=0 AND id IN ({})
                       ORDER BY (LENGTH(IFNULL(distress_json,'')) ) DESC, id
                       LIMIT ?""".format(",".join("?" * len(set(self.touched))) or "NULL"),
                    (*set(self.touched), n)) if self.touched else []
        return [r["id"] for r in rows]

    def _manual(self) -> None:
        self.begin("manual")
        sources = [s for s in all_sources() if s.access != AUTOMATED]
        made = 0
        details = []
        for src in sources:
            if not src.enabled():
                continue
            result = src.health_check()
            src.record_attempt(result)
            details.append(f"{src.label}: {result.status}")
            self.log(f"{src.label} -> {result.status}: {result.detail}",
                     source=src.name, stage="manual")
        # One standing task per strong candidate for the things only a human can
        # do FIRST - title, taxes, the State's tax-sale status, the assessor's own
        # record. The low-priority "confirm with the office" sources are raised
        # by the investigator on demand, or a scan would bury Topher in tasks.
        first_line = [src for src in sources
                      if getattr(src, "priority", 2) <= 2 and src.name != "public_listings"]
        for pid in self._top_candidates(min(10, max(5, self.enrich_top // 2))):
            for src in first_line:
                store.add_task(pid, src.manual_task(pid))
                made += 1
        # And one global task to nail down the parcel-type code table.
        store.add_task(None, {
            "title": "MANUAL VERIFICATION REQUIRED - confirm the Assessor's parcel-type codes",
            "detail": "We read the first letter of the parcel-type code as the class "
                      "(R/C/A/I/E/P) and the second as improved / vacant / manufactured. "
                      "The class letters are solid; the second letter is inferred from "
                      "the improvement-value distribution and has not been confirmed.",
            "why": "Several distress signals key off 'improved vs vacant'. If the code "
                   "table says something different, those signals are wrong.",
            "where_to_look": "Garland County Assessor's office",
            "source": "garland_assessor", "manual": 1, "priority": 2,
        })
        self.stats["manual_tasks"] = made + 1
        self.finish("manual", "done",
                    f"{len(sources)} sources need a human; {made + 1} tasks raised. "
                    + "; ".join(details[:3]))

    def _changes(self) -> None:
        self.begin("changes")
        n = db.q1("SELECT COUNT(*) c FROM changes WHERE detected_at >= "
                  "(SELECT started_at FROM scans WHERE id=?)", (self.id,))["c"]
        self.stats["changes"] = n
        self.finish("changes", "done",
                    f"{n} field-level changes since the last time we looked"
                    if n else "nothing changed since the last scan")

    def _scoring(self) -> None:
        ids = [i for i in set(self.touched)]
        self.begin("scoring", total=len(ids))
        strong = 0
        for n, pid in enumerate(ids, 1):
            p = store.get_property(pid)
            if not p or p.get("excluded"):
                continue
            out = scoring.compute(p)
            if out["overall"]["score"] >= 65:
                strong += 1
            if n % 25 == 0 or n == len(ids):
                self.tick("scoring", n, len(ids), f"{strong} strong so far")
        self.stats["strong_candidates"] = strong
        self.finish("scoring", "done", f"{strong} scored 65 or better")

    def _report(self) -> None:
        from .analyzers import topher_picks
        self.begin("report")
        picks = topher_picks(3)
        self.stats["picks"] = len(picks)
        if self.stats["new_properties"]:
            store.add_alert(None, "scan_complete",
                            f"Scan found {self.stats['new_properties']} new properties",
                            f"{self.stats['candidates']} carry distress signals; "
                            f"{self.stats['strong_candidates']} scored 65 or better.",
                            "info")
        self.finish("report", "done",
                    f"{len(picks)} picks: "
                    + "; ".join(p["address"] or str(p["id"]) for p in picks))

    def _count_source(self, ok: bool) -> None:
        self.stats["sources_ok" if ok else "sources_unavailable"] += 1


class SimpleResult:
    def __init__(self, status, detail):
        self.status, self.detail, self.error, self.records = status, detail, "", []
        self.manual_tasks = []


# ------------------------------------------------------------------ runner --

_current: Scan | None = None
_thread: threading.Thread | None = None


def current() -> Scan | None:
    return _current


def is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def start(mode: str = "distress", territory: str = DEFAULT_TERRITORY,
          limit: int | None = 400, enrich_top: int = 12) -> Scan:
    global _current, _thread
    if is_running():
        return _current
    scan = Scan(mode=mode, territory=territory, limit=limit, enrich_top=enrich_top)
    scan.save()
    _current = scan
    _thread = threading.Thread(target=scan.run, daemon=True, name="property-scan")
    _thread.start()
    return scan


def reap_interrupted() -> int:
    """A scan runs in-process. If the app was stopped mid-scan the row is still
    marked running, which would be a lie the next time anyone looks."""
    rows = db.q("SELECT id FROM scans WHERE status='running'")
    for r in rows:
        db.ex("UPDATE scans SET status='interrupted', error=?, finished_at=? WHERE id=?",
              ("the app stopped while this scan was running", utcnow(), r["id"]))
    return len(rows)


def last_scan() -> dict | None:
    row = db.q1("SELECT * FROM scans ORDER BY id DESC LIMIT 1")
    if not row:
        return None
    d = dict(row)
    d["stages"] = db.jload(d.pop("stages_json"), [])
    d["stats"] = db.jload(d.pop("stats_json"), {})
    return d
