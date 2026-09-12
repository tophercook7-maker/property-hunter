"""Configuration for Topher Property Hunter.

Everything geographic, financial or judgemental lives here so that adding a new
county/state is configuration work rather than a rewrite (spec 71/72).
No secrets belong in this file - read those from the environment.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "TOPHER PROPERTY HUNTER"
VERSION = "1.0.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PH_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("PH_DB_PATH", DATA_DIR / "property_hunter.db"))
FILES_DIR = Path(os.environ.get("PH_FILES_DIR", DATA_DIR / "files"))
CACHE_DIR = Path(os.environ.get("PH_CACHE_DIR", DATA_DIR / "cache"))

HOST = os.environ.get("PH_HOST", "127.0.0.1")
PORT = int(os.environ.get("PH_PORT", "8234"))

USER_AGENT = os.environ.get(
    "PH_USER_AGENT",
    "TopherPropertyHunter/1.0 (personal real-estate research; contact topher@mixedmakershop.com)",
)
HTTP_TIMEOUT = float(os.environ.get("PH_HTTP_TIMEOUT", "45"))
# Politeness: minimum seconds between requests to the same host.
RATE_LIMIT_SECONDS = float(os.environ.get("PH_RATE_LIMIT", "0.35"))

# ---------------------------------------------------------------- geography --

# Phase one territory. Add dicts here to add counties/states later.
TERRITORIES = [
    {
        "key": "garland_ar",
        "label": "Garland County, Arkansas",
        "state": "AR",
        "state_fips": "05",
        "county": "Garland",
        "county_fips": "05051",
        "center": [34.5037, -93.0552],
        "bbox": [-93.55, 34.25, -92.75, 34.75],  # minlon, minlat, maxlon, maxlat
        "active": True,
    }
]
DEFAULT_TERRITORY = "garland_ar"

# Hard exclusions (spec 17). Enforced in the data layer, never only in the UI.
# Each rule carries several independent signals so a rename or a typo in one
# source cannot leak a property through.
EXCLUSIONS = [
    {
        "key": "hot_springs_village",
        "label": "Hot Springs Village",
        "territory": "garland_ar",
        # Authoritative boundary: US Census TIGERweb Census Designated Place.
        "boundary": {"service": "tigerweb", "layer": 5, "geoid": "0533482",
                     "name": "Hot Springs Village CDP"},
        "city_names": ["hot springs village", "hsv"],
        # Exact-ish subdivision matches only; substring matching on "village"
        # would wrongly exclude Blacksnake Village Ests, Oaklawn Village, etc.
        "subdivision_patterns": [r"^hot\s+springs\s+village\b", r"^hsv\b"],
        "zips": ["71909", "71910"],
    },
    {
        "key": "diamondhead",
        "label": "Diamondhead",
        "territory": "garland_ar",
        # Diamondhead, AR incorporated 2015; Census place GEOID 0518880.
        "boundary": {"service": "tigerweb", "layer": 4, "geoid": "0518880",
                     "name": "Diamondhead city"},
        "city_names": ["diamondhead"],
        # "DIAMOND SPRINGS ESTATES HPR" must NOT match.
        "subdivision_patterns": [r"^diamondhead\b"],
        "zips": [],
    },
]

# --------------------------------------------------------------- scoring ----

# Transparent, configurable weights (spec 28). Each entry is
# signal -> (points, human sentence). The engine shows every line it applied.
SCORING_WEIGHTS = {
    "overall": {
        "distress_signal": 6,
        "multiple_distress": 8,
        "vacant_land": 4,
        "low_improvement_value": 7,
        "institutional_owner": 9,
        "estate_owner": 7,
        "government_owner": 5,
        "price_below_assessed": 10,
        "stale_assessment": 3,
        "has_address": 2,
        "in_city": 3,
        "acreage_useful": 4,
        "title_unknown": -12,
        "condition_unknown": -8,
        "flood_risk": -10,
        "no_road_frontage": -9,
        "no_address": -4,
        "tiny_lot": -3,
        "unknown_owner": -6,
    }
}

SCORE_KINDS = [
    "overall", "rental", "resale", "land", "storage",
    "business", "workshop", "snowcone", "risk",
]

# ------------------------------------------------------------- financials ---

# Default assumptions. Every one is an ESTIMATE and the UI must say so.
FINANCE_DEFAULTS = {
    "closing_cost_pct": 0.03,
    "title_legal_flat": 1200.0,
    "carrying_months": 6.0,
    "carrying_cost_monthly_pct": 0.008,
    "contingency_pct": 0.12,
    "interest_rate": 0.089,
    "loan_term_years": 25,
    "down_payment_pct": 0.20,
    "vacancy_pct": 0.08,
    "maintenance_pct": 0.08,
    "reserves_pct": 0.05,
    "management_pct": 0.08,
    "insurance_annual_per_100k": 1450.0,
    "tax_rate_of_assessed": 0.0615,      # Garland millage varies by district
    "rehab_per_sqft_light": 28.0,
    "rehab_per_sqft_medium": 55.0,
    "rehab_per_sqft_heavy": 95.0,
    "target_return_pct": 0.22,
    "rent_per_sqft_monthly": 0.95,       # market estimate, must be verified
    "storage_build_cost_per_sqft": 42.0,
    "storage_rent_per_sqft_monthly": 0.95,
    "storage_opex_pct": 0.35,
    "storage_coverage_ratio": 0.32,      # buildable sqft per usable land sqft
    "snowcone_season_days": 150,
    "snowcone_avg_ticket": 4.75,
    "snowcone_daily_customers_per_1k_traffic": 9.0,
}

# --------------------------------------------------------------------- AI ---

AI_ENABLED = os.environ.get("PH_AI_ENABLED", "1") not in ("0", "false", "no")
OLLAMA_URL = os.environ.get("PH_OLLAMA_URL", "http://localhost:11434")
AI_MODEL = os.environ.get("PH_AI_MODEL", "")   # blank = auto-pick a local model
AI_TIMEOUT = float(os.environ.get("PH_AI_TIMEOUT", "420"))
AI_NUM_CTX = int(os.environ.get("PH_AI_NUM_CTX", "8192"))

# --------------------------------------------------------------- policy -----

# Actions that always require an explicit human confirmation (spec 81).
APPROVAL_REQUIRED_ACTIONS = [
    "make_offer", "sign_contract", "spend_money", "borrow_money",
    "contact_seller", "send_message", "transfer_property", "publish",
    "legal_commitment",
]

LEGAL_DISCLAIMER = (
    "This is research and opinion, not legal or financial advice. "
    "Anything involving title, deeds, tax sales, liens, contracts or zoning "
    "should be verified with the appropriate government office and an "
    "Arkansas real-estate attorney before you spend money."
)

for _d in (DATA_DIR, FILES_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
