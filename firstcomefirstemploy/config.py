"""Paths and tunables shared across the project."""

from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder holding FirstComeFirstEmploy.py, companies.json, states/, .env
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

CONFIG_FILE = BASE_DIR / "companies.json"
GENERATED_FILE = BASE_DIR / "companies.generated.json"
UNRESOLVED_FILE = BASE_DIR / "companies.unresolved.csv"
AUDIT_CSV_FILE = BASE_DIR / "audit_results.csv"
DISCOVERY_DIR = BASE_DIR / "discovery"
STATE_DIR = BASE_DIR / "states"

DISCOVERY_REFRESH_DAYS = 7
DISCOVERY_SCHEMA = 2

REQUEST_RETRIES = 3
REQUEST_TIMEOUT = 30
POLITE_DELAY = 0.3          # pause between paged requests
MAX_PAGES = 200

MAX_WORKERS = 3             # companies monitored in parallel
SANITY_MIN_RATIO = 0.5      # refuse to save state if job count collapses
