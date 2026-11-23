"""Paths and tunables shared across the project."""

import os
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

MAX_WORKERS = 6             # companies monitored in parallel
DESCRIPTION_WORKERS = 6     # concurrent per-job description requests (per company)
PAGE_WORKERS = 4            # concurrent listing pages where the total is known (Workday)
HTTP_POOL_SIZE = 32         # connections kept alive per HTTP session
SANITY_MIN_RATIO = 0.5      # refuse to save state if job count collapses

# ---- --bootstrap ---------------------------------------------------------
BOOTSTRAP_WORKERS = 4       # companies resolved in parallel
BOOTSTRAP_PROBE_TIMEOUT = 6 # seconds; short, most guesses are misses
BOOTSTRAP_DEBUG = os.getenv("BOOTSTRAP_DEBUG", "") not in ("", "0")

# Workday needs tenant + datacenter pod (wdN) + site. Pods are probed in this
# order; a host that does not resolve / answer is skipped quickly.
WORKDAY_PODS = (5, 1, 3, 12, 2, 10, 103, 102, 101, 501)

# Optional LLM fallback: Anthropic, OpenAI, or Gemini, configured via .env.
# auto selects the only configured provider, or Anthropic if both are configured
# (preserves existing behavior). Set BOOTSTRAP_LLM_PROVIDER explicitly to override.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
GEMINI_LLM_MODEL = os.getenv("GEMINI_LLM_MODEL", "gemini-3.1-flash-lite")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BOOTSTRAP_LLM_PROVIDER = os.getenv("BOOTSTRAP_LLM_PROVIDER", "auto").strip().lower()
BOOTSTRAP_LLM_MODEL = os.getenv("BOOTSTRAP_LLM_MODEL", "")
ANTHROPIC_LLM_MODEL = os.getenv("ANTHROPIC_LLM_MODEL", "claude-haiku-5-5")
OPENAI_LLM_MODEL = os.getenv("OPENAI_LLM_MODEL", "gpt-4.1-mini")
BOOTSTRAP_LLM_WEB_SEARCH = os.getenv("BOOTSTRAP_LLM_WEB_SEARCH", "1") != "0"
BOOTSTRAP_LLM_TIMEOUT = 90