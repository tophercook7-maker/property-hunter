"""Source adapter framework (spec 15).

The scanner never talks to a website. It talks to PropertySource objects.
Each one reports its own health honestly; an unavailable source shows as
unavailable and NEVER as an empty successful scan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .. import db
from ..db import utcnow

# Access modes -----------------------------------------------------------
AUTOMATED = "automated"   # we can lawfully fetch it programmatically
MANUAL = "manual"         # a human has to look; we create a task instead
BLOCKED = "blocked"       # the operator blocks automation; we do not bypass

# Health -----------------------------------------------------------------
OK = "ok"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"
MANUAL_ONLY = "manual"
UNKNOWN = "unknown"


@dataclass
class Record:
    """One normalized observation about one property, from one source."""
    source: str
    identity: dict[str, Any] = field(default_factory=dict)   # parcel_id/address/lat/lon...
    fields: dict[str, Any] = field(default_factory=dict)     # property columns to merge
    evidence: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    status: str
    records: list[Record] = field(default_factory=list)
    detail: str = ""
    error: str = ""
    manual_tasks: list[dict[str, Any]] = field(default_factory=list)


class PropertySource:
    """Base adapter. Subclasses override discover()/fetch() as appropriate."""

    name: str = "unnamed"
    label: str = "Unnamed source"
    kind: str = "other"                 # parcel|boundary|flood|context|tax|code|listing|...
    url: str = ""
    access: str = AUTOMATED
    territory: str = "garland_ar"
    why_manual: str = ""
    what_to_check: str = ""

    # -- lifecycle -------------------------------------------------------
    def register(self) -> None:
        db.ex(
            "INSERT INTO sources(name,label,kind,url,access,enabled,status,updated_at) "
            "VALUES(?,?,?,?,?,1,?,?) "
            "ON CONFLICT(name) DO UPDATE SET label=excluded.label, kind=excluded.kind, "
            "url=excluded.url, access=excluded.access, updated_at=excluded.updated_at",
            (self.name, self.label, self.kind, self.url, self.access,
             MANUAL_ONLY if self.access != AUTOMATED else UNKNOWN, utcnow()),
        )

    def enabled(self) -> bool:
        row = db.q1("SELECT enabled FROM sources WHERE name=?", (self.name,))
        return bool(row["enabled"]) if row else True

    # -- work ------------------------------------------------------------
    def health_check(self) -> SourceResult:
        return SourceResult(status=UNKNOWN, detail="no health check implemented")

    def discover(self, **kwargs) -> SourceResult:
        """Find candidate records. Manual sources return manual tasks instead."""
        if self.access != AUTOMATED:
            return SourceResult(
                status=MANUAL_ONLY,
                detail=self.why_manual or "This source cannot be read automatically.",
                manual_tasks=[self.manual_task()],
            )
        raise NotImplementedError

    def enrich(self, prop: dict, **kwargs) -> SourceResult:
        """Add detail to one already-known property. Optional."""
        return SourceResult(status=UNKNOWN, detail="enrich not implemented")

    # -- honesty ---------------------------------------------------------
    def manual_task(self, prop_id: int | None = None) -> dict[str, Any]:
        return {
            "property_id": prop_id,
            "title": f"MANUAL VERIFICATION REQUIRED - {self.label}",
            "detail": self.what_to_check or f"Check {self.label} by hand.",
            "why": self.why_manual or "This source is not machine readable.",
            "where_to_look": self.url,
            "source": self.name,
            "source_url": self.url,
            "manual": 1,
            "priority": 2,
        }

    def record_attempt(self, result: SourceResult, changed: int = 0) -> None:
        now = utcnow()
        db.ex(
            "UPDATE sources SET status=?, status_detail=?, last_attempt=?, "
            "last_success=COALESCE(?, last_success), last_error=?, "
            "records_found=?, records_changed=?, updated_at=? WHERE name=?",
            (result.status, result.detail, now,
             now if result.status == OK else None,
             result.error or None, len(result.records), changed, now, self.name),
        )

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def ev(field_name: str, value: Any, *, etype: str, confidence: str,
           source: str, source_name: str = "", source_url: str = "",
           retrieved_at: str | None = None, effective_date: str | None = None,
           raw_ref: str | None = None) -> dict:
        return {
            "field": field_name, "value": value, "evidence_type": etype,
            "confidence": confidence, "source": source,
            "source_name": source_name, "source_url": source_url,
            "retrieved_at": retrieved_at or utcnow(),
            "effective_date": effective_date, "raw_ref": raw_ref,
        }


_REGISTRY: dict[str, PropertySource] = {}


def register(src: PropertySource) -> PropertySource:
    _REGISTRY[src.name] = src
    return src


def all_sources() -> list[PropertySource]:
    return list(_REGISTRY.values())


def get_source(name: str) -> PropertySource | None:
    return _REGISTRY.get(name)


def sources_of_kind(kind: str) -> list[PropertySource]:
    return [s for s in _REGISTRY.values() if s.kind == kind]


def register_all() -> None:
    for s in _REGISTRY.values():
        s.register()
