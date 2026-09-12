"""SQLite storage. Local-first, no server required (spec 64)."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import DB_PATH

_local = threading.local()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def q(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def q1(sql: str, params: Iterable = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def ex(sql: str, params: Iterable = ()) -> sqlite3.Cursor:
    conn = connect()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def jload(value, default=None):
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def jdump(value) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


SCHEMA = """
-- ------------------------------------------------------------------ sources
CREATE TABLE IF NOT EXISTS sources (
  name TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- parcel|boundary|flood|context|tax|code|listing|...
  url TEXT,
  access TEXT NOT NULL DEFAULT 'automated',  -- automated|manual|blocked
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',    -- ok|degraded|unavailable|manual|unknown
  status_detail TEXT,
  last_attempt TEXT,
  last_success TEXT,
  last_error TEXT,
  records_found INTEGER NOT NULL DEFAULT 0,
  records_changed INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  updated_at TEXT
);

-- --------------------------------------------------------------- geography
CREATE TABLE IF NOT EXISTS geographies (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                 -- county|place|cdp|exclusion
  key TEXT NOT NULL,
  name TEXT NOT NULL,
  state TEXT,
  county_fips TEXT,
  geoid TEXT,
  boundary_json TEXT,                 -- {"rings": [[[lon,lat],...]]}
  bbox_json TEXT,
  source TEXT,
  source_url TEXT,
  retrieved_at TEXT,
  UNIQUE(kind, key)
);

CREATE TABLE IF NOT EXISTS exclusion_rules (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL,
  label TEXT NOT NULL,
  territory TEXT NOT NULL,
  rule_kind TEXT NOT NULL,            -- polygon|city|subdivision|zip
  rule_value TEXT,
  geography_id INTEGER REFERENCES geographies(id),
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  created_at TEXT
);

-- -------------------------------------------------------------- properties
CREATE TABLE IF NOT EXISTS properties (
  id INTEGER PRIMARY KEY,
  canonical_key TEXT UNIQUE NOT NULL,
  data_class TEXT NOT NULL DEFAULT 'real',   -- real|demo
  territory TEXT,
  county_fips TEXT,
  parcel_id TEXT,
  rpid TEXT,
  address TEXT,
  address_norm TEXT,
  city TEXT,
  zip TEXT,
  lat REAL, lon REAL,
  subdivision TEXT,
  legal TEXT,
  acreage REAL,
  owner_name TEXT,
  owner_norm TEXT,
  parcel_type TEXT,
  property_type TEXT,                 -- house|vacant_structure|lot|commercial|multifamily|unknown
  improved INTEGER,                   -- 1 improved, 0 vacant, NULL unknown
  land_value REAL, imp_value REAL, total_value REAL,
  building_sqft REAL,
  year_built INTEGER,
  list_price REAL,
  listing_status TEXT,
  tax_status TEXT,
  zoning TEXT,
  flood_zone TEXT,
  road_frontage_m REAL,
  road_class TEXT,
  state TEXT NOT NULL DEFAULT 'DISCOVERED',
  excluded INTEGER NOT NULL DEFAULT 0,
  exclusion_reason TEXT,
  distress_json TEXT,
  recommendation TEXT,
  first_seen TEXT, last_seen TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_prop_parcel ON properties(parcel_id);
CREATE INDEX IF NOT EXISTS idx_prop_excluded ON properties(excluded);
CREATE INDEX IF NOT EXISTS idx_prop_addr ON properties(address_norm);
CREATE INDEX IF NOT EXISTS idx_prop_owner ON properties(owner_norm);
CREATE INDEX IF NOT EXISTS idx_prop_geo ON properties(lat, lon);

CREATE TABLE IF NOT EXISTS property_aliases (
  id INTEGER PRIMARY KEY,
  property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  alias_type TEXT NOT NULL,           -- parcel|address|rpid|legal|coord|owner
  alias_value TEXT NOT NULL,
  source TEXT,
  created_at TEXT,
  UNIQUE(alias_type, alias_value, property_id)
);
CREATE INDEX IF NOT EXISTS idx_alias_val ON property_aliases(alias_type, alias_value);

-- ---------------------------------------------------------------- evidence
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  field TEXT NOT NULL,
  value TEXT,
  evidence_type TEXT NOT NULL,        -- FACT|OBSERVATION|CALCULATION|ESTIMATE|AI_OPINION|UNKNOWN|CONFLICTING
  confidence TEXT NOT NULL,           -- HIGH|MEDIUM|LOW|NONE
  source TEXT NOT NULL,
  source_name TEXT,
  source_url TEXT,
  retrieved_at TEXT,
  effective_date TEXT,
  raw_ref TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ev_prop ON evidence(property_id, field);

CREATE TABLE IF NOT EXISTS conflicts (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  field TEXT NOT NULL,
  value_a TEXT, source_a TEXT, date_a TEXT,
  value_b TEXT, source_b TEXT, date_b TEXT,
  status TEXT NOT NULL DEFAULT 'NEEDS VERIFICATION',
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(property_id, source, captured_at);

CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  field TEXT NOT NULL,
  old_value TEXT, new_value TEXT,
  source TEXT, severity TEXT NOT NULL DEFAULT 'info',
  detected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_change_prop ON changes(property_id, detected_at);

CREATE TABLE IF NOT EXISTS timeline (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  event_date TEXT,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT,
  source TEXT,
  source_url TEXT,
  evidence_id INTEGER REFERENCES evidence(id),
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tl_prop ON timeline(property_id, event_date);

-- ------------------------------------------------------------------ scores
CREATE TABLE IF NOT EXISTS scores (
  property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  score REAL NOT NULL,
  confidence TEXT,
  breakdown_json TEXT,
  computed_at TEXT,
  PRIMARY KEY (property_id, kind)
);

-- --------------------------------------------------------------- workflow
CREATE TABLE IF NOT EXISTS watchlist (
  property_id INTEGER PRIMARY KEY REFERENCES properties(id) ON DELETE CASCADE,
  priority TEXT NOT NULL DEFAULT 'normal',
  notes TEXT, target_price REAL, desired_use TEXT,
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT,
  severity TEXT NOT NULL DEFAULT 'info',
  created_at TEXT NOT NULL,
  read_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_new ON alerts(read_at, created_at);

CREATE TABLE IF NOT EXISTS investigations (
  id INTEGER PRIMARY KEY,
  property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'running',
  stages_json TEXT,
  summary_json TEXT,
  started_at TEXT, finished_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  investigation_id INTEGER REFERENCES investigations(id) ON DELETE SET NULL,
  priority INTEGER NOT NULL DEFAULT 3,
  title TEXT NOT NULL,
  detail TEXT,
  why TEXT,
  where_to_look TEXT,
  source TEXT,
  source_url TEXT,
  status TEXT NOT NULL DEFAULT 'open',   -- open|done|skipped
  owner TEXT DEFAULT 'Topher',
  due_date TEXT,
  notes TEXT,
  evidence TEXT,
  manual INTEGER NOT NULL DEFAULT 0,
  created_at TEXT, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_prop ON tasks(property_id, status);

CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'note',   -- note|field_note|voice_note
  body TEXT NOT NULL,
  author TEXT DEFAULT 'Topher',
  confidence TEXT DEFAULT 'UNVERIFIED',
  audio_path TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                 -- street|aerial|satellite|listing|county|code|owner|inspection|rehab|before|after
  url TEXT, local_path TEXT,
  source TEXT, source_url TEXT, license TEXT,
  captured_at TEXT, confidence TEXT, caption TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  path TEXT, url TEXT, notes TEXT,
  added_at TEXT
);

CREATE TABLE IF NOT EXISTS scenarios (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'rental',
  inputs_json TEXT, outputs_json TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  decision TEXT NOT NULL,             -- pass|pursue|watch
  reason TEXT, detail TEXT,
  created_at TEXT
);

-- -------------------------------------------------------------- portfolio
CREATE TABLE IF NOT EXISTS portfolio (
  property_id INTEGER PRIMARY KEY REFERENCES properties(id) ON DELETE CASCADE,
  purchase_price REAL, purchase_date TEXT, closing_costs REAL,
  rehab_budget REAL, rehab_actual REAL,
  loan_amount REAL, interest_rate REAL, term_years INTEGER,
  insurance_annual REAL, taxes_annual REAL,
  current_value REAL, units INTEGER DEFAULT 1,
  notes TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS leases (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  tenant_name TEXT, unit TEXT, rent REAL, deposit REAL,
  start_date TEXT, end_date TEXT, status TEXT DEFAULT 'active',
  notes TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS rehab_projects (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  name TEXT NOT NULL, status TEXT DEFAULT 'planned',
  budget REAL, started_at TEXT, finished_at TEXT, notes TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS rehab_tasks (
  id INTEGER PRIMARY KEY,
  project_id INTEGER REFERENCES rehab_projects(id) ON DELETE CASCADE,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  category TEXT NOT NULL, title TEXT NOT NULL,
  status TEXT DEFAULT 'todo',
  budget REAL, actual REAL, contractor TEXT,
  started_at TEXT, finished_at TEXT, notes TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  direction TEXT NOT NULL,            -- expense|revenue
  category TEXT, amount REAL NOT NULL,
  occurred_on TEXT, memo TEXT, created_at TEXT
);

-- ------------------------------------------------------------------- ops
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY,
  mode TEXT NOT NULL,
  territory TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  stages_json TEXT,
  stats_json TEXT,
  error TEXT,
  started_at TEXT, finished_at TEXT
);

CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY,
  scan_id INTEGER REFERENCES scans(id) ON DELETE CASCADE,
  level TEXT NOT NULL DEFAULT 'info',
  source TEXT, stage TEXT, message TEXT,
  data_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_scan ON logs(scan_id, id);

CREATE TABLE IF NOT EXISTS ai_cache (
  key TEXT PRIMARY KEY,
  model TEXT, prompt TEXT, response TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY,
  action TEXT NOT NULL,
  property_id INTEGER REFERENCES properties(id) ON DELETE CASCADE,
  payload_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|declined
  requested_at TEXT, decided_at TEXT, decided_by TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


def setting(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    if not row:
        return default
    return jload(row["value"], row["value"])


def set_setting(key: str, value: Any) -> None:
    ex("INSERT INTO settings(key,value) VALUES(?,?) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, jdump(value)))


def log(scan_id: int | None, message: str, level: str = "info",
        source: str | None = None, stage: str | None = None, data: Any = None) -> None:
    ex("INSERT INTO logs(scan_id,level,source,stage,message,data_json,created_at) "
       "VALUES(?,?,?,?,?,?,?)",
       (scan_id, level, source, stage, message, jdump(data) if data else None, utcnow()))
