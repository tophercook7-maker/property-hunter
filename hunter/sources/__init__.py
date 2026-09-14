"""Source adapters. Importing this package registers every adapter."""
from .base import (AUTOMATED, BLOCKED, DEGRADED, MANUAL, MANUAL_ONLY, OK,
                   UNAVAILABLE, UNKNOWN, PropertySource, Record, SourceResult,
                   all_sources, get_source, register_all, sources_of_kind)
from . import ar_parcels, boundaries, context, cosl, countypay, flood, hot_springs, imagery, manual, roads  # noqa: F401

__all__ = ["PropertySource", "Record", "SourceResult", "all_sources", "get_source",
           "register_all", "sources_of_kind", "AUTOMATED", "MANUAL", "BLOCKED",
           "OK", "DEGRADED", "UNAVAILABLE", "MANUAL_ONLY", "UNKNOWN"]
