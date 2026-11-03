#!/usr/bin/env python3

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import csv
import json
import html as html_lib
import os
import re
import smtplib
import sys
import time

from pathlib import Path
from urllib.parse import urlparse, urljoin
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "companies.json"
DISCOVERY_DIR = BASE_DIR / "discovery"
STATE_DIR = BASE_DIR / "states"

DISCOVERY_REFRESH_DAYS = 7

PAGE_SIZE = 20
REQUEST_RETRIES = 3
REQUEST_TIMEOUT = 30

SANITY_MIN_RATIO = 0.5

load_dotenv(BASE_DIR / ".env")

EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")

SMTP_SERVER = os.getenv(
    "SMTP_SERVER", "smtp.gmail.com"
)

SMTP_PORT = int(
    os.getenv("SMTP_PORT", "465")
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "en-US"
}

COMPARE_FIELDS = ("title", "location", "url")

COUNTRY_ALIASES = {
    "india": [
        "india", "bengaluru", "bangalore",
        "hyderabad", "mumbai", "pune",
        "chennai", "gurgaon", "gurugram",
        "noida", "new delhi", "delhi",
        "ahmedabad", "kolkata", "kochi"
    ],
    "united states": [
        "united states", "usa", "u.s.",
        "new york", "california", "texas",
        "seattle", "san francisco", "austin",
        "chicago", "boston"
    ],
    "united kingdom": [
        "united kingdom", "uk", "london",
        "manchester", "cambridge", "edinburgh"
    ],
    "canada": [
        "canada", "toronto", "vancouver",
        "montreal", "ottawa", "waterloo"
    ],
    "australia": [
        "australia", "sydney", "melbourne",
        "brisbane", "perth"
    ],
    "singapore": ["singapore"]
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize(value):
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        str(value or "").lower()
    ).strip()



def clean_description(value):
    """Convert an HTML/plain-text description into searchable text."""
    if not value:
        return ""
    # Unescape first: some APIs (Greenhouse) return HTML-escaped HTML.
    value = html_lib.unescape(str(value))
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def description_filters_requested(search):
    return (
        bool(search.get("description_exclude"))
        or bool(search.get("_audit_mode"))
    )


def extract_page_description(session, url):
    """Best-effort description extraction from a public job-detail page."""
    if not url:
        return ""
    try:
        page = request_text(session, url)
    except Exception as exc:
        print(f"  Could not fetch job detail page: {exc}")
        return ""

    # Prefer JSON-LD JobPosting.description where the site exposes it.
    for match in re.finditer(
        r"(?is)<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        page
    ):
        raw = html_lib.unescape(match.group(1)).strip()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            graph = obj.get("@graph") or []
            candidates = [obj] + [x for x in graph if isinstance(x, dict)]
            for candidate in candidates:
                kind = candidate.get("@type", [])
                if isinstance(kind, str):
                    kind = [kind]
                if "JobPosting" in kind and candidate.get("description"):
                    return clean_description(candidate["description"])

    # Fall back to meta description only; do not mistake arbitrary page text for a JD.
    meta = re.search(
        r"(?is)<meta[^>]+(?:name|property)=[\"'](?:description|og:description)[\"'][^>]+content=[\"'](.*?)[\"']",
        page
    )
    return clean_description(meta.group(1)) if meta else ""

def safe_filename(value):
    return re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        value
    )


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = path.with_suffix(".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )

    temp.replace(path)


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def request_json(
    session,
    url,
    params=None,
    payload=None
):
    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            if payload is not None:
                response = session.post(
                    url,
                    json=payload,
                    timeout=REQUEST_TIMEOUT
                )
            else:
                response = session.get(
                    url,
                    params=params,
                    timeout=REQUEST_TIMEOUT
                )

            if response.status_code == 404:
                raise RuntimeError(
                    f"Request failed: HTTP 404 {url}"
                )

            if response.status_code == 400:
                raise RuntimeError(
                    f"HTTP 400 for {url}\n"
                    f"  Request body: {json.dumps(payload or params)}\n"
                    f"  Response: {response.text[:400]}"
                )

            if (
                400 <= response.status_code < 500
                and response.status_code != 429
            ):
                raise RuntimeError(
                    f"Request failed: HTTP {response.status_code} "
                    f"{url} :: {response.text[:300]}"
                )

            response.raise_for_status()
            return response.json()

        except (
            requests.RequestException,
            ValueError
        ) as exc:

            last_error = exc

            if attempt < REQUEST_RETRIES:
                time.sleep(attempt * 2)

    raise RuntimeError(
        f"Request failed: {last_error}"
    )

# ============================================================
# COMPANY CONFIGURATION
# ============================================================

def load_companies():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Missing configuration: {CONFIG_FILE}"
        )

    data = load_json(CONFIG_FILE)
    companies = data.get("companies", [])

    names = set()

    for company in companies:

        if not company.get("name"):
            raise ValueError("Company name missing")

        if not company.get("career_url"):
            raise ValueError(
                f"Career URL missing: {company['name']}"
            )

        key = normalize(company["name"])

        if key in names:
            raise ValueError(
                f"Duplicate company: {company['name']}"
            )

        names.add(key)

    return companies


def select_companies(
    companies,
    requested=None
):
    if not requested or requested == "*":
        return [
            c for c in companies
            if c.get("enabled", True)
        ]

    matches = [
        c for c in companies
        if normalize(c["name"]) == normalize(requested)
    ]

    if not matches:
        raise ValueError(
            f"Company not found: {requested}"
        )

    return matches


def list_companies(companies):
    print("\nCONFIGURED COMPANIES")
    print("=" * 65)

    for company in companies:

        print(
            f"\n{company['name']} | "
            f"{company.get('platform', 'auto')} | "
            f"{'ENABLED' if company.get('enabled', True) else 'DISABLED'}"
        )

        print("URL:", company["career_url"])

        for search in company.get("searches", []):
            print("  -", search["name"])


# ============================================================
# PLATFORM DETECTION
# ============================================================

def detect_platform(company):
    configured = company.get(
        "platform", "auto"
    ).lower()

    if configured != "auto":
        if configured not in ALL_PLATFORMS:
            raise ValueError(
                f"Unsupported platform: {configured}"
            )

        return configured

    parsed = urlparse(company["career_url"])
    host = parsed.netloc.lower()

    if host.endswith("myworkdayjobs.com"):
        return "workday"

    if "greenhouse.io" in host:
        return "greenhouse"

    path = parsed.path.lower()

    if host.endswith("lever.co"):
        return "lever"

    if host.endswith("ashbyhq.com"):
        return "ashby"

    if host.endswith("smartrecruiters.com"):
        return "smartrecruiters"

    if host.endswith("atlassian.com"):
        return "atlassian"

    if host.endswith("amazon.jobs"):
        return "amazon"

    if (
        host.endswith("careers.microsoft.com")
        or host.endswith("eightfold.ai")
    ):
        return "eightfold"

    if host == "careers.google.com" or (
        "google.com" in host and "careers" in path
    ):
        return "google"

    raise ValueError(
        f"Unsupported career URL: {company['career_url']}"
    )


# ============================================================
# DISCOVERY CACHE
# ============================================================

def discovery_path(company):
    return DISCOVERY_DIR / (
        safe_filename(company["name"]) + ".json"
    )


DISCOVERY_SCHEMA = 2


def discovery_is_fresh(metadata, company):
    if metadata.get("schema") != DISCOVERY_SCHEMA:
        return False

    if metadata.get("career_url") != company["career_url"]:
        return False

    if metadata.get("platform") != detect_platform(company):
        return False

    discovered_at = metadata.get("discovered_at")

    if not discovered_at:
        return False

    try:
        discovered = time.mktime(
            time.strptime(
                discovered_at,
                "%Y-%m-%dT%H:%M:%SZ"
            )
        )

        return (
            time.time() - discovered
            < DISCOVERY_REFRESH_DAYS * 86400
        )

    except ValueError:
        return False


def discover_company(
    session,
    company,
    force=False
):
    path = discovery_path(company)
    platform = detect_platform(company)

    if path.exists() and not force:
        cached = load_json(path)

        if discovery_is_fresh(cached, company):
            print(
                "Using cached discovery:",
                company["name"]
            )
            return cached

    print(
        f"\nDiscovering {company['name']} "
        f"using {platform}"
    )

    metadata = {
        "company": company["name"],
        "career_url": company["career_url"],
        "platform": platform,
        "schema": DISCOVERY_SCHEMA,
        "discovered_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime()
        )
    }

    if platform == "workday":
        metadata.update(
            discover_workday(session, company)
        )

    elif platform == "greenhouse":
        metadata.update(
            discover_greenhouse(session, company)
        )

    else:
        metadata.update(
            discover_simple(session, company, platform)
        )

    save_json(path, metadata)

    print("Discovery saved:", path)

    return metadata


# ============================================================
# WORKDAY DISCOVERY
# ============================================================

def discover_workday(session, company):
    parsed = urlparse(company["career_url"])
    host = parsed.netloc

    match = re.match(
        r"^(.+?)\.wd\d+\.myworkdayjobs\.com$",
        host,
        re.I
    )

    if not match:
        raise ValueError("Unsupported Workday URL")

    tenant = company.get("tenant", match.group(1))

    parts = [
        p for p in parsed.path.split("/") if p
    ]

    if parts and re.fullmatch(
        r"[a-z]{2}-[A-Z]{2}",
        parts[0]
    ):
        parts.pop(0)

    site = company.get(
        "site",
        parts[0] if parts else ""
    )

    if not site:
        raise ValueError("Could not identify Workday site")

    base_url = f"{parsed.scheme}://{host}"

    api_url = (
        f"{base_url}/wday/cxs/"
        f"{tenant}/{site}/jobs"
    )

    job_base_url = f"{base_url}/{site}"

    data = request_json(
        session,
        api_url,
        payload={
            "appliedFacets": {},
            "limit": PAGE_SIZE,
            "offset": 0,
            "searchText": ""
        }
    )

    def walk(values, key):
        output = []

        for value in values or []:
            if not isinstance(value, dict):
                continue

            # Nested groups (e.g. locationMainGroup) carry their
            # own facetParameter; ids below must use that key.
            here = value.get("facetParameter") or key

            if value.get("id") is not None:
                output.append({
                    "descriptor": value.get("descriptor", ""),
                    "id": value["id"],
                    "count": value.get("count"),
                    "key": here
                })

            output.extend(
                walk(value.get("values", []), here)
            )

        return output

    facets = []

    for facet in data.get("facets", []):
        key = facet.get("facetParameter")

        if not key:
            continue

        facets.append({
            "key": key,
            "descriptor": facet.get("descriptor", ""),
            "values": walk(facet.get("values", []), key)
        })

    return {
        "tenant": tenant,
        "site": site,
        "api_url": api_url,
        "job_base_url": job_base_url,
        "total_postings": data.get("total", 0),
        "facets": facets
    }


# ============================================================
# WORKDAY FACET RESOLUTION
# ============================================================

def resolve_workday_facet(metadata, name):
    aliases = {
        "job family": [
            "job family",
            "job family group",
            "job category",
            "job family grouping"
        ],
        "country": [
            "country",
            "location country",
            "country from job posting location extended"
        ],
        "location": [
            "location",
            "locations",
            "primary location"
        ]
    }

    candidates = aliases.get(
        normalize(name),
        [normalize(name)]
    )

    matches = []

    for facet in metadata.get("facets", []):
        descriptor = normalize(
            facet.get("descriptor")
        )

        key = normalize(
            facet.get("key")
        )

        if descriptor in candidates or key in candidates:
            matches.append(facet)

    if len(matches) == 1:
        return matches[0]

    available = [
        f"{f.get('descriptor')} [{f.get('key')}]"
        for f in metadata.get("facets", [])
    ]

    raise ValueError(
        f"Facet '{name}' not uniquely found. "
        f"Available facets: {available}"
    )

def resolve_workday_entry(facet, name):
    wanted = normalize(name)

    matches = [
        value for value in facet["values"]
        if normalize(value.get("descriptor")) == wanted
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Value '{name}' not uniquely found "
            f"in facet '{facet['descriptor']}'"
        )

    return matches[0]


def resolve_workday_value(facet, name):
    return resolve_workday_entry(facet, name)["id"]


def apply_workday_values(applied, facet, values):
    for name in values:
        entry = resolve_workday_entry(facet, name)

        applied.setdefault(
            entry.get("key") or facet["key"], []
        ).append(entry["id"])


def workday_country_fallback(metadata, countries):
    """Some tenants have no 'Country' facet; look for the
    country as a value inside any location-like facet."""
    found = {}

    for country in countries:
        wanted = normalize(country)
        hit = False

        for facet in metadata.get("facets", []):

            label = normalize(
                facet.get("descriptor", "")
                + " " + facet.get("key", "")
            )

            if "location" not in label and "country" not in label:
                continue

            for value in facet["values"]:
                if normalize(value.get("descriptor")) == wanted:
                    found.setdefault(
                        value.get("key") or facet["key"], []
                    ).append(value["id"])
                    hit = True
                    break

            if hit:
                break

        if not hit:
            raise ValueError(
                f"Country '{country}' not found in any "
                "Workday location facet. Run --discover "
                "COMPANY to see the available facets."
            )

    return found


def workday_facets(metadata, search):
    applied = {}

    standard_fields = {
        "country": "country",
        "job_family": "job family",
        "location": "location"
    }

    for field, facet_name in standard_fields.items():

        requested = search.get(field)

        if not requested:
            continue

        values = (
            requested
            if isinstance(requested, list)
            else [requested]
        )

        try:
            facet = resolve_workday_facet(
                metadata,
                facet_name
            )

            apply_workday_values(applied, facet, values)

        except ValueError:

            if field != "country":
                raise

            applied.update(
                workday_country_fallback(metadata, values)
            )

    for facet_name, requested in search.get(
        "extra_facets", {}
    ).items():

        facet = resolve_workday_facet(
            metadata,
            facet_name
        )

        values = (
            requested
            if isinstance(requested, list)
            else [requested]
        )

        apply_workday_values(applied, facet, values)

    return applied


# ============================================================
# WORKDAY JOB FETCH
# ============================================================

def fetch_workday_description(
    session, metadata, external_path, strict=False
):
    """
    Fetch the full description for an individual Workday job.
    strict=True raises after retries so the run keeps the previous
    state instead of making a wrong keep/exclude decision.
    """
    if not external_path:
        return ""

    api_url = metadata["api_url"].rstrip("/")
    cxs_base = re.sub(r"/jobs$", "", api_url)

    path = external_path.lstrip("/")
    if not path.startswith("job/"):
        return ""

    detail_url = f"{cxs_base}/{path}"
    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            response = session.get(detail_url, timeout=20)

            # Job vanished between listing and detail fetch.
            if response.status_code == 404:
                return ""

            response.raise_for_status()
            info = response.json().get("jobPostingInfo") or {}
            return info.get("jobDescription", "") or ""

        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < REQUEST_RETRIES:
                time.sleep(attempt * 2)

    if strict:
        raise RuntimeError(
            f"Workday description fetch failed: {detail_url} :: {last_error}"
        )

    print(f"  Could not fetch Workday description: {last_error}")
    return ""

def fetch_workday(session, metadata, search):
    jobs = {}
    offset = 0
    total = None
    parsed = 0

    applied = workday_facets(metadata, search)

    audit_mode = bool(search.get("_audit_mode"))
    need_description = description_filters_requested(search)

    while True:

        data = request_json(
            session,
            metadata["api_url"],
            payload={
                "appliedFacets": applied,
                "limit": PAGE_SIZE,
                "offset": offset,
                "searchText": search.get("search_text", "")
            }
        )

        if total is None:
            total = data.get("total", 0)
            print("  Matching postings:", total)

        postings = data.get("jobPostings") or []

        if not postings:
            break

        for posting in postings:

            external = posting.get("externalPath", "")
            jid = None

            for field in posting.get("bulletFields") or []:
                match = re.search(
                    r"JR\d+", str(field), re.I
                )

                if match:
                    jid = match.group(0).upper()
                    break

            if not jid:
                match = re.search(
                    r"JR\d+", external, re.I
                )

                jid = (
                    match.group(0).upper()
                    if match else external
                )

            if not jid:
                continue

            parsed += 1

            title = (posting.get("title") or "").strip()

            # Skip the per-job description request for jobs the
            # title filters will reject anyway. In audit mode the
            # title filters are stripped, so everything passes.
            if not matches_title_filters(title, search):
                continue

            description = ""

            if need_description:
                description = clean_description(
                    fetch_workday_description(
                        session,
                        metadata,
                        external,
                        strict=not audit_mode
                    )
                )

            # Normal monitoring: apply description exclusions,
            # same as Greenhouse and finalize() do.
            # Audit mode keeps the job so run_audit can report it.
            if (
                not audit_mode
                and description.strip()
                and not matches_description_filters(
                    description, search
                )
            ):
                continue

            job = {
                "title": title,
                "location": posting.get("locationsText", ""),
                "url": urljoin(
                    metadata["job_base_url"] + "/",
                    external.lstrip("/")
                ),
                "posted": posting.get("postedOn", "")
            }

            if audit_mode:
                job["_description"] = description

            jobs[jid] = job

        offset += len(postings)

        if offset >= total or len(postings) < PAGE_SIZE:
            break

    # Count parsed postings, not kept jobs. Otherwise a search where
    # every job is filtered out would wrongly raise this error.
    if total and not parsed:
        raise RuntimeError(
            "Workday reported postings but none were parsed"
        )

    return jobs


# ============================================================
# GREENHOUSE DISCOVERY
# ============================================================

def greenhouse_board(company):
    if company.get("board_token"):
        return company["board_token"]

    parsed = urlparse(company["career_url"])

    parts = [
        p for p in parsed.path.split("/") if p
    ]

    if not parts:
        raise ValueError(
            "Greenhouse board token missing. "
            "Set board_token in companies.json."
        )

    return parts[0]


def discover_greenhouse(session, company):
    board = greenhouse_board(company)

    api_url = (
        "https://boards-api.greenhouse.io/"
        f"v1/boards/{board}/jobs"
    )

    data = request_json(
        session,
        api_url,
        params={"content": "false"}
    )

    return {
        "board_token": board,
        "api_url": api_url,
        "total_postings": len(data.get("jobs", []))
    }


# ============================================================
# GREENHOUSE FILTERING
# ============================================================

def matches_country(location, country):
    if not country:
        return True

    countries = (
        country if isinstance(country, list) else [country]
    )

    padded = f" {normalize(location)} "

    for item in countries:

        key = normalize(item)

        aliases = COUNTRY_ALIASES.get(key, [key])

        if any(
            f" {normalize(alias)} " in padded
            for alias in aliases
        ):
            return True

    return False


def matches_search(title, description, search):
    text = normalize(title + " " + description)

    keyword = normalize(
        search.get("search_text", "")
    )

    family = normalize(
        search.get("job_family", "")
    )

    if keyword:
        if not all(
            word in text
            for word in keyword.split()
        ):
            return False

    if family:
        if family not in normalize(title):
            return False

    return True


# ============================================================
# GREENHOUSE JOB FETCH
# ============================================================


def fetch_greenhouse(session, metadata, search):
    data = request_json(
        session,
        metadata["api_url"],
        params={"content": "true"}
    )

    jobs = {}
    audit_mode = bool(search.get("_audit_mode"))

    for posting in data.get("jobs", []):

        title = posting.get("title", "")

        location = (
            posting.get("location") or {}
        ).get("name", "")

        description = clean_description(
            posting.get("content", "")
        )

        if not matches_country(
            location,
            search.get("country")
        ):
            continue

        if not matches_search(
            title,
            description,
            search
        ):
            continue

        if (
            not audit_mode
            and not matches_description_filters(
                description, search
            )
        ):
            continue

        jid = str(posting.get("id"))

        if jid == "None":
            continue

        job = {
            "title": title,
            "location": location,
            "url": posting.get("absolute_url", ""),
            "posted": posting.get("updated_at", "")
        }

        if audit_mode:
            job["_description"] = description

        jobs[jid] = job

    return jobs



# ============================================================
# UNIFIED JOB FETCH
# ============================================================

def fetch_jobs(session, company, metadata, search):
    platform = metadata["platform"]

    if platform == "workday":
        jobs = fetch_workday(session, metadata, search)

    elif platform == "greenhouse":
        jobs = fetch_greenhouse(session, metadata, search)

    elif platform in EXTRA_PLATFORMS:
        jobs = fetch_simple(
            session, company, metadata, search
        )

    else:
        raise ValueError(
            f"Unsupported platform: {platform}"
        )

    audit_mode = bool(search.get("_audit_mode"))

    # Optional title filters work on every platform.
    # Safety net: never let _description leak into saved state.
    return {
        jid: (
            job if audit_mode
            else {
                k: v for k, v in job.items()
                if k != "_description"
            }
        )
        for jid, job in jobs.items()
        if matches_title_filters(job.get("title", ""), search)
    }


# ============================================================
# ADDITIONAL PLATFORMS (free, public endpoints only)
# ============================================================

EXTRA_PLATFORMS = (
    "lever", "ashby", "smartrecruiters",
    "atlassian", "amazon", "eightfold", "google"
)

ALL_PLATFORMS = ("workday", "greenhouse") + EXTRA_PLATFORMS

MAX_PAGES = 200
POLITE_DELAY = 0.3

GENERATED_FILE = BASE_DIR / "companies.generated.json"
UNRESOLVED_FILE = BASE_DIR / "companies.unresolved.csv"

HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": None
}

# Edit these lists to tune which titles alert you.
# Matching is on whole words, case-insensitive.
TITLE_PRESETS = {
    "tech_roles": {
        "any": [
            "software engineer", "software developer",
            "software development engineer", "sde",
            "backend engineer", "full stack engineer",
            "machine learning", "ml", "ai engineer",
            "applied scientist", "research scientist",
            "research engineer", "data scientist",
            "forward deployed", "fde",
            "member of technical staff", "member technical staff",
            "quantitative", "quant", "member technical", "mts", "software engineering", "mle", "data scientist",
            "sr engineer", "backend", "engineer", "computer scientist"
        ],
        "exclude": ["intern", "internship", "apprentice", "staff", "principal", "architect", "phd", "fixed term contract", "manager", "lead", "qa", "testing", "test", "verification", "asic", "fpga", "management", "validation", "design engineer", "solutions engineer", "hardware engineer", "modelling engineer", "devops engineer", "system engineer", "systems engineer", "thermal engineer", "dft engineer", "support engineer"]
    }
}

COUNTRY_NAMES = {
    "in": "India", "sg": "Singapore",
    "gb": "United Kingdom", "uk": "United Kingdom",
    "us": "United States", "ca": "Canada",
    "au": "Australia"
}

AMAZON_COUNTRY_CODES = {
    "india": "IND", "singapore": "SGP",
    "united kingdom": "GBR", "united states": "USA",
    "canada": "CAN", "australia": "AUS"
}

_RAW_CACHE = {}
_RAW_CACHE_LOCK = threading.Lock()

def amazon_location_matches_country(location, country):
    if not country:
        return True

    location = (location or "").strip()
    if not location:
        return False

    country = country.strip()
    country_code = AMAZON_COUNTRY_CODES.get(country.lower())

    terms = [country]

    if country_code:
        terms.append(country_code)

    return any(
        re.search(
            rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])",
            location,
            re.IGNORECASE
        )
        for term in terms
    )


def request_text(session, url, params=None):
    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            response = session.get(
                url,
                params=params,
                headers=HTML_HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 404:
                raise RuntimeError(
                    f"Request failed: HTTP 404 {url}"
                )

            response.raise_for_status()
            return response.text

        except requests.RequestException as exc:
            last_error = exc

            if attempt < REQUEST_RETRIES:
                time.sleep(attempt * 2)

    raise RuntimeError(f"Request failed: {last_error}")


def fmt_ts(value):
    if value in (None, ""):
        return ""

    try:
        number = float(value)

        if number > 1e11:
            number /= 1000.0

        return time.strftime(
            "%Y-%m-%d", time.gmtime(number)
        )

    except (TypeError, ValueError):
        return str(value)


def join_locations(value):
    if isinstance(value, (list, tuple)):
        return "; ".join(
            str(v) for v in value if v
        )

    return str(value or "")


def primary_country(search):
    value = search.get("country")

    if isinstance(value, list):
        value = value[0] if value else ""

    return normalize(value)


def slug_from_company(company):
    for key in ("slug", "board_token"):
        if company.get(key):
            return company[key]

    parts = [
        p for p in
        urlparse(company["career_url"]).path.split("/")
        if p
    ]

    if not parts:
        raise ValueError(
            "Board slug missing. "
            "Set 'slug' in companies.json."
        )

    return parts[0]


# ------------------------------------------------------------
# Filtering shared by all new platforms
# ------------------------------------------------------------

def title_terms(search):
    preset = TITLE_PRESETS.get(
        search.get("title_preset"), {}
    )

    any_terms = list(search.get("title_any") or [])
    any_terms += preset.get("any", [])

    exclude = list(search.get("title_exclude") or [])
    exclude += preset.get("exclude", [])

    return any_terms, exclude


def matches_title_filters(title, search):
    any_terms, exclude = title_terms(search)

    padded = f" {normalize(title)} "

    if any_terms and not any(
        f" {normalize(t)} " in padded
        for t in any_terms
    ):
        return False

    if any(
        f" {normalize(t)} " in padded
        for t in exclude
    ):
        return False

    return True


def get_title_filter_reasons(title, search):
    any_terms, exclude_terms = title_terms(search)
    padded = f" {normalize(title)} "

    reasons = []
    matched_rules = []

    matched_any = [
        term for term in any_terms
        if f" {normalize(term)} " in padded
    ]

    if any_terms and not matched_any:
        reasons.append("title_any_no_match")
        matched_rules.append(
            "Required title terms: "
            + "; ".join(any_terms)
        )

    matched_excludes = [
        term for term in exclude_terms
        if f" {normalize(term)} " in padded
    ]

    if matched_excludes:
        reasons.append("title_exclude_match")
        matched_rules.append(
            "Excluded title terms: "
            + "; ".join(matched_excludes)
        )

    return reasons, matched_rules

def _contains_term(normalized_text, term):
    t = normalize(term)
    return bool(t) and f" {t} " in f" {normalized_text} "


def matches_description_filters(description, search):
    text = normalize(description)

    return not any(
        _contains_term(text, term)
        for term in search.get("description_exclude", [])
    )


def get_description_filter_matches(description, search):
    """
    Return all description exclusion terms found.
    Missing descriptions are not treated as rejection.
    """
    description = clean_description(description)

    if not description.strip():
        return [], "unavailable"

    text = normalize(description)

    matched = [
        term
        for term in search.get("description_exclude", [])
        if _contains_term(text, term)
    ]

    return matched, "available"

def finalize(raw_jobs, search, server_country=False):
    jobs = {}
    audit_mode = bool(search.get("_audit_mode"))

    for jid, job in raw_jobs.items():

        description = clean_description(
            job.get("_description", "")
        )

        if not server_country and not matches_country(
            job.get("location", ""),
            search.get("country")
        ):
            continue

        if not matches_title_filters(
            job.get("title", ""), search
        ):
            continue

        if not matches_search(
            job.get("title", ""),
            description,
            search
        ):
            continue

        # Normal monitoring: apply description exclusions.
        # Audit mode: retain the job so run_audit can
        # independently report the exclusion reason.
        if (
            not audit_mode
            and description.strip()
            and not matches_description_filters(
                description, search
            )
        ):
            continue

        if audit_mode:
            jobs[jid] = {
                **job,
                "_description": description
            }
        else:
            jobs[jid] = {
                k: v for k, v in job.items()
                if k != "_description"
            }

    return jobs


# ------------------------------------------------------------
# Lever
# ------------------------------------------------------------

def lever_api_url(company):
    slug = slug_from_company(company)
    host = urlparse(company["career_url"]).netloc.lower()

    if ".eu." in host or company.get("region") == "eu":
        base = "https://api.eu.lever.co"
    else:
        base = "https://api.lever.co"

    return f"{base}/v0/postings/{slug}"


def fetch_lever(session, metadata):
    data = request_json(
        session,
        metadata["api_url"],
        params={"mode": "json"}
    )

    if not isinstance(data, list):
        raise RuntimeError("Unexpected Lever response")

    jobs = {}

    for p in data:
        jid = p.get("id")

        if not jid:
            continue

        cats = p.get("categories") or {}

        locations = (
            cats.get("allLocations")
            or [cats.get("location")]
        )

        jobs[str(jid)] = {
            "title": (p.get("text") or "").strip(),
            "location": join_locations(locations),
            "url": p.get("hostedUrl", ""),
            "posted": fmt_ts(p.get("createdAt")),
            "_description": p.get("descriptionPlain", "")
        }

    return jobs


# ------------------------------------------------------------
# Ashby
# ------------------------------------------------------------

def fetch_ashby(session, metadata):
    data = request_json(
        session,
        metadata["api_url"],
        params={"includeCompensation": "false"}
    )

    jobs = {}

    for p in data.get("jobs", []):
        jid = p.get("id")

        if not jid:
            continue

        locations = [p.get("location")]

        for extra in p.get("secondaryLocations") or []:
            if isinstance(extra, dict):
                locations.append(extra.get("location"))
            else:
                locations.append(extra)

        jobs[str(jid)] = {
            "title": (p.get("title") or "").strip(),
            "location": join_locations(locations),
            "url": p.get("jobUrl", ""),
            "posted": fmt_ts(p.get("publishedAt")),
            "_description": p.get("descriptionPlain", "")
        }

    return jobs


# ------------------------------------------------------------
# SmartRecruiters
# ------------------------------------------------------------

def fetch_smartrecruiters(session, metadata, search):
    slug = metadata["slug"]
    jobs = {}
    offset = 0

    for _ in range(MAX_PAGES):

        data = request_json(
            session,
            metadata["api_url"],
            params={"limit": 100, "offset": offset}
        )

        content = data.get("content") or []
        total = data.get("totalFound", 0)

        for p in content:
            jid = p.get("id")

            if not jid:
                continue

            loc = p.get("location") or {}

            country = loc.get("country") or ""
            country = COUNTRY_NAMES.get(
                country.lower(), country
            )

            location = loc.get("fullLocation") or ", ".join(
                x for x in (
                    loc.get("city"),
                    loc.get("region"),
                    country
                ) if x
            )

            job_url = (
                f"https://jobs.smartrecruiters.com/{slug}/{jid}"
            )
            description = ""

            # The public SmartRecruiters Posting API exposes full job-ad
            # sections on the individual posting endpoint. Fetch only when
            # description exclusions are configured.
            if description_filters_requested(search):
                detail_url = metadata["api_url"].rstrip("/") + f"/{jid}"
                try:
                    detail = request_json(session, detail_url)
                    job_ad = detail.get("jobAd") or {}
                    sections = job_ad.get("sections") or {}
                    if isinstance(sections, dict):
                        description = "\n".join(
                            str(section.get("text") or "")
                            for section in sections.values()
                            if isinstance(section, dict)
                        )
                    elif isinstance(sections, list):
                        description = "\n".join(
                            str(section.get("text") or "")
                            for section in sections
                            if isinstance(section, dict)
                        )
                    description = clean_description(description)
                except Exception as exc:
                    print(
                        f"  Could not fetch SmartRecruiters description "
                        f"for {jid}: {exc}"
                    )

            jobs[str(jid)] = {
                "title": (p.get("name") or "").strip(),
                "location": location,
                "url": job_url,
                "posted": p.get("releasedDate", ""),
                "_description": description
            }

        offset += len(content)

        if not content or offset >= total:
            break

        time.sleep(POLITE_DELAY)

    return jobs


# ------------------------------------------------------------
# Atlassian (public JSON listing of all openings)
# ------------------------------------------------------------

def fetch_atlassian(session, metadata):
    data = request_json(session, metadata["api_url"])

    if isinstance(data, dict):
        data = data.get("jobs") or data.get("listings") or []

    jobs = {}

    for p in data:
        jid = p.get("id")

        if jid is None:
            continue

        jid = str(jid)

        jobs[jid] = {
            "title": (p.get("title") or "").strip(),
            "location": join_locations(p.get("locations")),
            "url": p.get("applyUrl") or (
                "https://www.atlassian.com/company/"
                f"careers/details/{jid}"
            ),
            "posted": fmt_ts(
                (p.get("portalJobPost") or {}).get(
                    "updatedDate"
                )
            ),
            "_description": clean_description(
                p.get("overview")
                or p.get("description")
                or p.get("jobDescription")
                or ""
            )
        }

    return jobs


# ------------------------------------------------------------
# Amazon (amazon.jobs search.json)
# ------------------------------------------------------------

def fetch_amazon(session, metadata, search):
    country = (primary_country(search) or "").strip()
    code = (
        search.get("amazon_country")
        or AMAZON_COUNTRY_CODES.get(country.lower(), "")
    )

    params = {
        "base_query": search.get("search_text", ""),
        "result_limit": 100,
        "sort": "recent"
    }

    if code:
        params["country[]"] = code
    elif country:
        params["loc_query"] = country

    jobs = {}
    offset = 0

    for _ in range(MAX_PAGES):

        params["offset"] = offset

        data = request_json(
            session, metadata["api_url"], params=params
        )

        batch = data.get("jobs") or []
        hits = data.get("hits", 0)

        for p in batch:
            jid = p.get("id_icims") or p.get("id")

            if not jid:
                continue

            job_location = (
                p.get("normalized_location")
                or p.get("location", "")
            ).strip()

            if not amazon_location_matches_country(job_location, country):
                continue

            job_url = urljoin(
                "https://www.amazon.jobs", p.get("job_path", "")
            )
            description = (
                p.get("description")
                or p.get("job_description")
                or p.get("description_short")
                or ""
            )
            if (
                description_filters_requested(search)
                and not p.get("description")
                and not p.get("job_description")
            ):
                page_description = extract_page_description(
                    session, job_url
                )
                if page_description:
                    description = page_description

            jobs[str(jid)] = {
                "title": (p.get("title") or "").strip(),
                "location": job_location,
                "url": job_url,
                "posted": p.get("posted_date", ""),
                "_description": clean_description(description)
            }

        offset += len(batch)

        if not batch or offset >= hits:
            break

        time.sleep(POLITE_DELAY)

    return jobs, bool(code)


# ------------------------------------------------------------
# Eightfold-powered portals (Microsoft and others)
# ------------------------------------------------------------

def eightfold_defaults(company):
    parsed = urlparse(company["career_url"])
    host = parsed.netloc.lower()

    if company.get("api_url"):
        api_url = company["api_url"]
    elif host.endswith("eightfold.ai"):
        api_url = f"https://{host}/api/apply/v2/jobs"
    else:
        api_url = (
            f"{parsed.scheme}://{host}/api/pcsx/search"
        )

    domain = company.get("domain") or ".".join(
        host.split(".")[-2:]
    )

    return {
        "api_url": api_url,
        "domain": domain,
        "site_base": f"{parsed.scheme}://{host}"
    }


def fetch_eightfold(session, metadata, search):
    location = search.get("location") or primary_country(
        search
    ).title()

    jobs = {}
    start = 0

    fetch_descriptions = description_filters_requested(search)

    for _ in range(MAX_PAGES):

        params = {
            "domain": metadata["domain"],
            "query": search.get("search_text", ""),
            "start": start,
            "sort_by": "timestamp"
        }

        if location:
            params["location"] = location

        data = request_json(
            session, metadata["api_url"], params=params
        )

        payload = (
            data.get("data")
            if isinstance(data.get("data"), dict)
            else data
        )

        positions = payload.get("positions") or []
        total = (
            payload.get("count")
            or payload.get("total")
            or 0
        )

        for p in positions:
            jid = p.get("id") or p.get("display_job_id")

            if not jid:
                continue

            link = (
                p.get("canonicalPositionUrl")
                or p.get("positionUrl")
                or f"/careers/job/{jid}"
            )

            description = clean_description(
                p.get("jobDescription")
                or p.get("description")
                or p.get("descriptionText")
                or p.get("job_description")
                or ""
            )

            # Eightfold listing results often do not contain
            # the full job description. Fetch it separately.
            if fetch_descriptions and not description:

                detail_url = (
                    metadata["site_base"].rstrip("/")
                    + "/api/pcsx/position_details"
                )

                try:
                    detail = request_json(
                        session,
                        detail_url,
                        params={
                            "domain": metadata["domain"],
                            "position_id": jid
                        }
                    )

                    detail_data = (
                        detail.get("data")
                        if isinstance(detail.get("data"), dict)
                        else detail
                    )

                    description = clean_description(
                        detail_data.get("jobDescription")
                        or detail_data.get("job_description")
                        or detail_data.get("description")
                        or detail_data.get("descriptionText")
                        or ""
                    )

                    time.sleep(POLITE_DELAY)

                except Exception as exc:
                    print(
                        f"  Eightfold description unavailable "
                        f"for {jid}: {exc}"
                    )

            jobs[str(jid)] = {
                "title": (
                    p.get("name") or p.get("title") or ""
                ).strip(),
                "location": join_locations(
                    p.get("locations") or p.get("location")
                ),
                "url": urljoin(
                    metadata["site_base"] + "/", link
                ),
                "posted": fmt_ts(
                    p.get("postedTs")
                    or p.get("t_create")
                    or p.get("creationTs")
                ),
                "_description": description
            }

        start += len(positions)

        if not positions or (total and start >= total):
            break

        time.sleep(POLITE_DELAY)

    return jobs, bool(location)


# ------------------------------------------------------------
# Google Careers (plain HTML results page, best effort)
# ------------------------------------------------------------

GOOGLE_RESULTS_URL = (
    "https://www.google.com/about/careers/"
    "applications/jobs/results/"
)


def fetch_google(session, metadata, search):
    location = search.get("location") or primary_country(
        search
    ).title()

    jobs = {}

    for page in range(1, MAX_PAGES + 1):

        params = {"page": page}

        if location:
            params["location"] = location

        if search.get("search_text"):
            params["q"] = search["search_text"]

        html = request_text(
            session, metadata["api_url"], params
        )

        titles = {}

        for m in re.finditer(
            r'\["(\d{10,})","([^"\\]{4,200})"', html
        ):
            if not m.group(2).startswith("http"):
                titles.setdefault(m.group(1), m.group(2))

        found = re.findall(
            r"jobs/results/(\d{10,})-([a-z0-9][a-z0-9\-]*)",
            html
        )

        new = 0

        for jid, slug in found:

            if jid in jobs:
                continue

            new += 1

            job_url = f"{GOOGLE_RESULTS_URL}{jid}-{slug}"
            description = ""
            if description_filters_requested(search):
                description = extract_page_description(
                    session, job_url
                )

            jobs[jid] = {
                "title": titles.get(jid) or (
                    slug.replace("-", " ").title()
                ),
                "location": location or "",
                "url": job_url,
                "posted": "",
                "_description": description
            }

        if page == 1 and not jobs:
            raise RuntimeError(
                "Google returned no postings. The page "
                "format may have changed, or the filter "
                "matched nothing."
            )

        if new == 0:
            break

        time.sleep(POLITE_DELAY)

    return jobs, True


# ------------------------------------------------------------
# Discovery + dispatch for the new platforms
# ------------------------------------------------------------

def discover_simple(session, company, platform):
    meta = {}

    if platform in ("lever", "ashby", "smartrecruiters"):
        slug = slug_from_company(company)
        meta["slug"] = slug

        if platform == "lever":
            meta["api_url"] = lever_api_url(company)

        elif platform == "ashby":
            meta["api_url"] = (
                "https://api.ashbyhq.com/"
                f"posting-api/job-board/{slug}"
            )

        else:
            meta["api_url"] = (
                "https://api.smartrecruiters.com/"
                f"v1/companies/{slug}/postings"
            )

    elif platform == "atlassian":
        meta["api_url"] = company.get("api_url") or (
            "https://www.atlassian.com/"
            "endpoint/careers/listings"
        )

    elif platform == "amazon":
        meta["api_url"] = (
            "https://www.amazon.jobs/en/search.json"
        )

    elif platform == "eightfold":
        meta.update(eightfold_defaults(company))

    elif platform == "google":
        meta["api_url"] = GOOGLE_RESULTS_URL

    # Validate the board right away for platforms whose
    # listing does not depend on the search filters.

    if platform in (
        "lever", "ashby", "smartrecruiters", "atlassian"
    ):
        probe_meta = dict(meta, platform=platform)

        raw, _ = load_raw(
            session, company, probe_meta, {}
        )

        meta["total_postings"] = len(raw)

    return meta


def load_raw(session, company, metadata, search):
    platform = metadata["platform"]

    if platform == "amazon":
        key = (
            platform,
            primary_country(search),
            search.get("amazon_country", ""),
            search.get("search_text", ""),
            description_filters_requested(search)
        )
        fetcher = lambda: fetch_amazon(session, metadata, search)

    elif platform == "eightfold":
        key = (
            platform,
            metadata["api_url"],
            metadata["domain"],
            search.get("location") or primary_country(search),
            search.get("search_text", ""),
            description_filters_requested(search)
        )
        fetcher = lambda: fetch_eightfold(session, metadata, search)

    elif platform == "google":
        key = (
            platform,
            search.get("location") or primary_country(search),
            search.get("search_text", ""),
            description_filters_requested(search)
        )
        fetcher = lambda: fetch_google(session, metadata, search)

    else:
        key = (platform, metadata["api_url"])
        if platform == "smartrecruiters":
            # Description fetching depends on this search's exclusions.
            key += (description_filters_requested(search),)

        def fetcher():
            if platform == "lever":
                raw = fetch_lever(session, metadata)
            elif platform == "ashby":
                raw = fetch_ashby(session, metadata)
            elif platform == "smartrecruiters":
                raw = fetch_smartrecruiters(session, metadata, search)
            elif platform == "atlassian":
                raw = fetch_atlassian(session, metadata)
            else:
                raise ValueError(f"Unsupported platform: {platform}")
            return raw, False

    with _RAW_CACHE_LOCK:
        cached = _RAW_CACHE.get(key)
    if cached is not None:
        return cached

    fetched = fetcher()
    with _RAW_CACHE_LOCK:
        return _RAW_CACHE.setdefault(key, fetched)


def fetch_simple(session, company, metadata, search):
    raw, server_country = load_raw(
        session, company, metadata, search
    )

    return finalize(raw, search, server_country)


# ============================================================
# BOOTSTRAP: find public job boards for companies in a CSV
# ============================================================

BOOTSTRAP_PLATFORMS = (
    "greenhouse", "lever", "ashby", "smartrecruiters"
)

# normalized company name -> (platform, slug)
KNOWN_BOARDS = {
    "glean": ("greenhouse", "gleanwork"),
    "razorpay": (
        "greenhouse", "razorpaysoftwareprivatelimited"
    ),
    "mistral ai": ("lever", "mistral"),
    "openai": ("ashby", "openai"),
    "anthropic": ("greenhouse", "anthropic"),
    "databricks": ("greenhouse", "databricks"),
    "elevenlabs": ("ashby", "elevenlabs"),
    "cohere": ("ashby", "cohere"),
    "stripe": ("greenhouse", "stripe")
}

SKIP_NAMES = {"stealth co"}


def candidate_slugs(name):
    words = normalize(name).split()
    found = []

    def add(value):
        if value and value not in found:
            found.append(value)

    add("".join(words))
    add("-".join(words))

    if len(words) > 1:
        add(words[0])

    trimmed = [
        w for w in words
        if w not in {
            "ai", "labs", "capital", "technologies",
            "inc", "hq"
        }
    ]

    if trimmed and trimmed != words:
        add("".join(trimmed))
        add("-".join(trimmed))

    return found[:5]


def probe_board(session, platform, slug):
    """Return {id: job} if a board exists with jobs, else None."""
    try:
        if platform == "greenhouse":
            data = request_json(
                session,
                "https://boards-api.greenhouse.io/"
                f"v1/boards/{slug}/jobs"
            )

            jobs = {}

            for p in data.get("jobs", []):
                jobs[str(p.get("id"))] = {
                    "title": p.get("title", ""),
                    "location": (
                        p.get("location") or {}
                    ).get("name", ""),
                    "url": p.get("absolute_url", "")
                }

        elif platform == "lever":
            jobs = fetch_lever(session, {
                "api_url": lever_api_url({
                    "career_url": f"https://jobs.lever.co/{slug}",
                    "slug": slug
                })
            })

        elif platform == "ashby":
            jobs = fetch_ashby(session, {
                "api_url": (
                    "https://api.ashbyhq.com/"
                    f"posting-api/job-board/{slug}"
                )
            })

        else:
            jobs = fetch_smartrecruiters(session, {
                "slug": slug,
                "api_url": (
                    "https://api.smartrecruiters.com/"
                    f"v1/companies/{slug}/postings"
                )
            }, {})

    except Exception:
        return None

    return jobs or None


def countries_from_location(text):
    tokens = set(normalize(text).split())
    countries = []

    if tokens & {
        "blr", "hyd", "india", "bangalore", "bengaluru",
        "hyderabad", "pune", "mumbai", "delhi", "noida"
    }:
        countries.append("india")

    if tokens & {"sgp", "singapore"}:
        countries.append("singapore")

    if tokens & {"london", "uk"}:
        countries.append("united kingdom")

    if "remote" in tokens:
        countries.append("remote")

    return countries or ["india"]


def career_url_for(platform, slug):
    return {
        "greenhouse": f"https://boards.greenhouse.io/{slug}",
        "lever": f"https://jobs.lever.co/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/{slug}",
        "smartrecruiters": (
            f"https://jobs.smartrecruiters.com/{slug}"
        )
    }[platform]


def run_bootstrap(csv_path, existing_names):
    csv_path = Path(csv_path)

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    session = create_session()

    generated = []
    unresolved = []

    try:
        for row in rows:

            name = (row.get("Name") or "").strip()
            location = (row.get("Location") or "").strip()
            key = normalize(name)

            if not name or key in SKIP_NAMES:
                continue

            if "/" in name:
                print(f"SKIP (looks like a location): {name}")
                continue

            if key in existing_names:
                print(f"SKIP (already configured): {name}")
                continue

            match = None

            attempts = []

            if key in KNOWN_BOARDS:
                attempts.append(KNOWN_BOARDS[key])

            for slug in candidate_slugs(name):
                for platform in BOOTSTRAP_PLATFORMS:
                    attempts.append((platform, slug))

            for platform, slug in attempts:

                jobs = probe_board(session, platform, slug)

                time.sleep(POLITE_DELAY)

                if jobs:
                    match = (platform, slug, jobs)
                    break

            if not match:
                print(f"NOT FOUND: {name}")
                unresolved.append({
                    "name": name,
                    "location": location,
                    "next_step": (
                        "Open the company careers page and "
                        "check its URL / network calls"
                    )
                })
                continue

            platform, slug, jobs = match
            countries = countries_from_location(location)

            region = [
                j for j in jobs.values()
                if matches_country(
                    j.get("location", ""), countries
                )
            ]

            region_titles = [
                j for j in region
                if matches_title_filters(
                    j.get("title", ""),
                    {"title_preset": "tech_roles"}
                )
            ]

            sample = (
                region_titles[0]["title"]
                if region_titles else "-"
            )

            print(
                f"FOUND: {name} -> {platform}:{slug} | "
                f"total={len(jobs)} "
                f"in-region={len(region)} "
                f"matching-roles={len(region_titles)} | "
                f"e.g. {sample}"
            )

            generated.append({
                "name": name,
                "career_url": career_url_for(platform, slug),
                "platform": platform,
                "slug": slug,
                "enabled": True,
                "csv_location": location,
                "searches": [{
                    "name": "Target roles",
                    "country": countries,
                    "title_preset": "tech_roles"
                }]
            })

    finally:
        session.close()

    save_json(GENERATED_FILE, {"companies": generated})

    with open(
        UNRESOLVED_FILE, "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=["name", "location", "next_step"]
        )
        writer.writeheader()
        writer.writerows(unresolved)

    print(
        f"\nWrote {len(generated)} companies to "
        f"{GENERATED_FILE.name}"
    )

    print(
        f"{len(unresolved)} not found -> "
        f"{UNRESOLVED_FILE.name}"
    )

    print(
        "\nReview the FOUND lines above (a slug can belong to "
        "a different company with the same name), delete "
        "wrong entries, then run:\n"
        f"  python notify.py --merge {GENERATED_FILE.name}"
    )


def run_merge(source_path):
    source = load_json(Path(source_path))
    incoming = source.get("companies", [])

    if CONFIG_FILE.exists():
        current = load_json(CONFIG_FILE)
        backup = CONFIG_FILE.with_suffix(".json.bak")
        save_json(backup, current)
    else:
        current = {"companies": []}

    existing = {
        normalize(c["name"]) for c in current["companies"]
    }

    added = []

    for company in incoming:

        if normalize(company["name"]) in existing:
            print("Skipped (exists):", company["name"])
            continue

        current["companies"].append(company)
        existing.add(normalize(company["name"]))
        added.append(company["name"])

    save_json(CONFIG_FILE, current)

    print(f"Added {len(added)}:", ", ".join(added) or "-")
    print("Backup saved as companies.json.bak")




AUDIT_CSV_FILE = BASE_DIR / "audit_results.csv"

AUDIT_FIELDS = [
    "audit_timestamp",
    "company",
    "search",
    "job_id",
    "title",
    "location",
    "url",
    "status",
    "reason",
    "matched_rule",
    "description_status",
    "description_matches"
]


def run_audit(companies, requested):

    session = create_session()
    audit_rows = []

    timestamp = time.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    try:
        for company in select_companies(companies, requested):

            print("\n" + "=" * 70)
            print("AUDIT:", company["name"])

            try:
                metadata = discover_company(session, company)

                for search in company.get("searches", []):

                    # Remove filters that we want to audit
                    # independently after fetching the jobs.
                    wide = {
                        k: v for k, v in search.items()
                        if k not in (
                            "title_any",
                            "title_exclude",
                            "title_preset",
                            "description_exclude"
                        )
                    }

                    # Internal flag: fetch and preserve descriptions.
                    wide["_audit_mode"] = True

                    everything = fetch_jobs(
                        session,
                        company,
                        metadata,
                        wide
                    )

                    kept = 0
                    skipped = 0
                    unavailable = 0

                    for jid, job in everything.items():

                        title = job.get("title", "")

                        # Check title filters.
                        title_reasons, title_rules = (
                            get_title_filter_reasons(
                                title, search
                            )
                        )

                        # Check description exclusions.
                        description = clean_description(
                            job.get("_description", "")
                        )

                        description_matches, description_status = (
                            get_description_filter_matches(
                                description, search
                            )
                        )

                        reasons = list(title_reasons)
                        matched_rules = list(title_rules)

                        if description_matches:
                            reasons.append(
                                "description_exclude_match"
                            )

                            matched_rules.extend(
                                "description_exclude: " + term
                                for term in description_matches
                            )

                        if (
                            description_status == "unavailable"
                            and search.get("description_exclude")
                        ):
                            unavailable += 1

                        ejected = bool(reasons)

                        if ejected:
                            skipped += 1
                        else:
                            kept += 1

                        audit_rows.append({
                            "audit_timestamp": timestamp,
                            "company": company["name"],
                            "search": search.get("name", ""),
                            "job_id": jid,
                            "title": title,
                            "location": job.get("location", ""),
                            "url": job.get("url", ""),
                            "status": (
                                "EJECTED" if ejected else "KEPT"
                            ),
                            "reason": "; ".join(reasons),
                            "matched_rule": "; ".join(matched_rules),
                            "description_status": description_status,
                            "description_matches": "; ".join(
                                description_matches
                            )
                        })

                    print(
                        f"\n  [{search['name']}] "
                        f"Fetched: {len(everything)} | "
                        f"Kept: {kept} | "
                        f"Ejected: {skipped} | "
                        f"Description unavailable: {unavailable}"
                    )

            except Exception as exc:
                print("  AUDIT ERROR:", exc)

    finally:
        session.close()

    # Append results without deleting previous company audits.
    file_exists = (
        AUDIT_CSV_FILE.exists()
        and AUDIT_CSV_FILE.stat().st_size > 0
    )

    with open(
        AUDIT_CSV_FILE,
        "a",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=AUDIT_FIELDS
        )

        if not file_exists:
            writer.writeheader()

        writer.writerows(audit_rows)

    print(
        f"\nAudit CSV saved: {AUDIT_CSV_FILE}"
    )

    print("Total audited jobs:", len(audit_rows))

    print(
        "Ejected:",
        sum(
            row["status"] == "EJECTED"
            for row in audit_rows
        )
    )


def run_probe(companies, requested):
    """Fetch live results for a company without touching state."""
    session = create_session()

    try:
        for company in select_companies(companies, requested):

            print("\n" + "=" * 70)
            print("PROBE:", company["name"])

            try:
                metadata = discover_company(
                    session, company, force=True
                )

                searches = company.get("searches") or [
                    {"name": "(no filter)"}
                ]

                for search in searches:

                    jobs = fetch_jobs(
                        session, company, metadata, search
                    )

                    print(
                        f"\n  [{search['name']}] "
                        f"{len(jobs)} jobs"
                    )

                    for job in list(jobs.values())[:8]:
                        print(
                            f"   - {job['title']} | "
                            f"{job.get('location', '')}"
                        )

                    if not jobs:
                        print(
                            "   (nothing matched; loosen "
                            "country/title filters)"
                        )

            except Exception as exc:
                print("  PROBE ERROR:", exc)

    finally:
        session.close()


# ============================================================
# STATE MANAGEMENT
# ============================================================

def get_state_file(name):
    return STATE_DIR / (
        safe_filename(name) + ".json"
    )


def load_previous_jobs(name):
    path = get_state_file(name)

    if not path.exists():
        return None

    return load_json(path)


def save_jobs(name, jobs):
    save_json(get_state_file(name), jobs)


# ============================================================
# CHANGE DETECTION
# ============================================================

def job_key(job):
    return tuple(
        job.get(field)
        for field in COMPARE_FIELDS
    )


def detect_changes(previous, current):
    previous_ids = set(previous)
    current_ids = set(current)

    return {
        "added": {
            jid: current[jid]
            for jid in sorted(current_ids - previous_ids)
        },

        "removed": {
            jid: previous[jid]
            for jid in sorted(previous_ids - current_ids)
        },

        "modified": {
            jid: {
                "old": previous[jid],
                "new": current[jid]
            }
            for jid in sorted(previous_ids & current_ids)
            if job_key(previous[jid]) != job_key(current[jid])
        }
    }


def count_changes(changes):
    return sum(
        len(value)
        for value in changes.values()
    )


# ============================================================
# EMAIL GENERATION
# ============================================================

def build_email(all_changes):
    body = "JOB NOTIFY UPDATE\n"
    body += "=" * 70 + "\n"

    for name, changes in all_changes.items():

        body += (
            f"\nMONITOR: {name}\n"
            + "=" * 70
            + "\n"
        )

        if changes["added"]:

            body += (
                f"\nNEW JOBS ({len(changes['added'])})\n"
            )

            for jid, job in changes["added"].items():

                body += (
                    f"\n{job['title']}\n"
                    f"Location: {job.get('location', '')}\n"
                    f"ID: {jid}\n"
                    f"Posted: {job.get('posted', '')}\n"
                    f"URL: {job['url']}\n"
                )

        if changes["removed"]:

            body += (
                f"\nREMOVED JOBS ({len(changes['removed'])})\n"
            )

            for jid, job in changes["removed"].items():

                body += (
                    f"\n{job['title']}\n"
                    f"Location: {job.get('location', '')}\n"
                    f"ID: {jid}\n"
                )

        if changes["modified"]:

            body += (
                f"\nMODIFIED JOBS ({len(changes['modified'])})\n"
            )

            for jid, details in changes["modified"].items():

                old = details["old"]
                new = details["new"]

                body += (
                    f"\nID: {jid}\n"
                    f"Old: {old['title']} | "
                    f"{old.get('location', '')}\n"
                    f"New: {new['title']} | "
                    f"{new.get('location', '')}\n"
                    f"URL: {new['url']}\n"
                )

    return body


# ============================================================
# EMAIL DELIVERY
# ============================================================

def send_email(all_changes):
    if not all([
        EMAIL_FROM,
        EMAIL_TO,
        EMAIL_PASSWORD
    ]):
        raise RuntimeError(
            "Configure EMAIL_FROM, EMAIL_TO "
            "and EMAIL_PASSWORD in .env"
        )

    total = sum(
        count_changes(changes)
        for changes in all_changes.values()
    )

    message = MIMEText(build_email(all_changes))

    message["Subject"] = (
        f"[JobNotify] {total} changes detected "
        f"across {len(all_changes)} monitors"
    )

    message["From"] = EMAIL_FROM
    message["To"] = EMAIL_TO

    with smtplib.SMTP_SSL(
        SMTP_SERVER,
        SMTP_PORT,
        timeout=30
    ) as server:

        server.login(
            EMAIL_FROM,
            EMAIL_PASSWORD
        )

        server.send_message(message)

    print("\nEmail notification sent successfully.")


def send_test_email():
    test_changes = {
        "SMTP TEST": {
            "added": {
                "TEST": {
                    "title": "JobNotify SMTP test",
                    "location": "Test",
                    "url": "https://example.com",
                    "posted": ""
                }
            },
            "removed": {},
            "modified": {}
        }
    }

    send_email(test_changes)

# ============================================================
# DISCOVERY COMMAND
# ============================================================

def run_discovery(
    companies,
    requested=None,
    refresh=False
):
    session = create_session()
    failures = []

    try:
        selected = select_companies(
            companies,
            requested
        )

        for company in selected:

            try:
                metadata = discover_company(
                    session,
                    company,
                    force=refresh
                )

                print("\nCompany:", company["name"])
                print("Platform:", metadata["platform"])

                if metadata["platform"] == "workday":

                    print("Tenant:", metadata["tenant"])
                    print("Site:", metadata["site"])

                    print("\nDISCOVERED FACETS")

                    for facet in metadata["facets"]:

                        print(
                            f"\n{facet['descriptor']} "
                            f"[{facet['key']}]"
                        )

                        for value in facet["values"]:

                            print(
                                f"  {value['descriptor']}: "
                                f"{value['id']}"
                            )

                elif metadata["platform"] == "greenhouse":

                    print(
                        "Board token:",
                        metadata["board_token"]
                    )

                    print(
                        "Postings:",
                        metadata["total_postings"]
                    )

                else:

                    print("API:", metadata.get("api_url"))

                    if "total_postings" in metadata:
                        print(
                            "Postings:",
                            metadata["total_postings"]
                        )

            except Exception as exc:

                failures.append(company["name"])

                print(
                    f"\nDISCOVERY ERROR: "
                    f"{company['name']}: {exc}"
                )

    finally:
        session.close()

    if failures:
        raise RuntimeError(
            "Discovery failed for: "
            + ", ".join(failures)
        )


# ============================================================
# MONITORING ENGINE
# ============================================================

def monitor_one_company(company):
    """Monitor one company using a dedicated HTTP session.

    Each company runs independently. Its results are returned to the main
    thread so state files and email notifications are handled centrally.
    """
    session = create_session()

    result = {
        "company": company["name"],
        "changes": {},
        "pending_states": {},
        "successful": 0,
        "failed": 0,
        "total": 0,
    }

    try:
        print("\n" + "=" * 70)
        print("COMPANY:", company["name"])

        searches = company.get("searches", [])
        result["total"] = len(searches)

        if not searches:
            print("No searches configured.")
            return result

        try:
            metadata = discover_company(session, company)
        except Exception as exc:
            print("DISCOVERY ERROR:", exc)
            result["failed"] += len(searches)
            return result

        for search in searches:
            monitor_name = f"{company['name']}_{search['name']}"
            print("\nChecking:", monitor_name)

            try:
                current = fetch_jobs(
                    session, company, metadata, search
                )
                previous = load_previous_jobs(monitor_name)

                print("Jobs found:", len(current))

                if previous is None:
                    print("Initial baseline created.")
                    result["pending_states"][monitor_name] = current
                    result["successful"] += 1
                    continue

                if previous and (
                    (
                        len(previous) >= 10
                        and len(current) < len(previous) * SANITY_MIN_RATIO
                    )
                    or (len(previous) >= 3 and not current)
                ):
                    raise RuntimeError(
                        "Suspiciously low job count. Previous state preserved."
                    )

                changes = detect_changes(previous, current)
                count = count_changes(changes)
                print("Changes:", count)

                if count:
                    result["changes"][monitor_name] = changes

                result["pending_states"][monitor_name] = current
                result["successful"] += 1

            except Exception as exc:
                result["failed"] += 1
                print("ERROR:", exc)

        return result

    finally:
        session.close()


def run_monitoring(companies, dry_run=False, max_workers=3):
    """Monitor up to three different companies concurrently."""
    changes_by_monitor = {}
    pending_states = {}

    successful = 0
    failed = 0
    total = sum(len(c.get("searches", [])) for c in companies)

    worker_count = max(1, min(int(max_workers), 3, len(companies) or 1))
    print(
        f"\nRunning up to {worker_count} companies in parallel "
        f"(maximum configured: 3)."
    )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(monitor_one_company, company): company
            for company in companies
        }

        for future in as_completed(futures):
            company = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                # A company-level unexpected failure must not stop other workers.
                company_searches = len(company.get("searches", []))
                failed += company_searches
                print(
                    f"\nUNEXPECTED ERROR for {company['name']}: {exc}"
                )
                continue

            changes_by_monitor.update(result["changes"])
            pending_states.update(result["pending_states"])
            successful += result["successful"]
            failed += result["failed"]

    if changes_by_monitor:
        if dry_run:
            print(
                "\nDRY RUN: Changes not emailed and states not saved."
            )
            print(build_email(changes_by_monitor))
        else:
            send_email(changes_by_monitor)
    else:
        print("\nNo changes detected.")

    # State writes happen only after all company workers have completed.
    # This prevents partially completed parallel runs from saving early.
    if not dry_run:
        for name, jobs in pending_states.items():
            save_jobs(name, jobs)

    print("\nMONITORING SUMMARY")
    print("=" * 70)
    print("Companies:", len(companies))
    print("Parallel workers:", worker_count)
    print("Total searches:", total)
    print("Successful:", successful)
    print("Failed:", failed)
    print("Changed monitors:", len(changes_by_monitor))

    if failed:
        sys.exit(1)


# ============================================================
# COMMAND LINE
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "JobNotify - multi-portal job monitoring"
        )
    )

    parser.add_argument(
        "--discover",
        nargs="?",
        const="*",
        metavar="COMPANY"
    )

    parser.add_argument(
        "--discover-ids",
        nargs="?",
        const="*",
        metavar="COMPANY"
    )

    parser.add_argument(
        "--refresh-discovery",
        action="store_true"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true"
    )

    parser.add_argument(
        "--company",
        metavar="NAME",
        help=(
            "monitor only the specified company; "
            "matches company names case-insensitively"
        )
    )

    parser.add_argument(
        "--list-companies",
        action="store_true"
    )

    parser.add_argument(
        "--test-email",
        action="store_true"
    )

    parser.add_argument(
        "--bootstrap",
        metavar="CSV",
        help="find public job boards for companies in a CSV"
    )

    parser.add_argument(
        "--merge",
        metavar="FILE",
        help="merge a generated companies file into "
             "companies.json"
    )

    parser.add_argument(
        "--audit",
        nargs="?",
        const="*",
        metavar="COMPANY",
        help="list jobs your title filter excludes"
    )

    parser.add_argument(
        "--probe",
        nargs="?",
        const="*",
        metavar="COMPANY",
        help="show live matching jobs without saving state"
    )

    args = parser.parse_args()

    try:
        if args.bootstrap:
            existing = set()

            if CONFIG_FILE.exists():
                existing = {
                    normalize(c["name"])
                    for c in load_json(CONFIG_FILE).get(
                        "companies", []
                    )
                }

            run_bootstrap(args.bootstrap, existing)
            return

        if args.merge:
            run_merge(args.merge)
            return

        companies = load_companies()

        if args.probe is not None:
            run_probe(companies, args.probe)
            return

        if args.audit is not None:
            run_audit(companies, args.audit)
            return

        if args.list_companies:
            list_companies(companies)
            return

        if args.test_email:
            send_test_email()
            return

        if args.discover is not None:

            run_discovery(
                companies,
                requested=args.discover,
                refresh=args.refresh_discovery
            )

            return

        if args.discover_ids is not None:

            run_discovery(
                companies,
                requested=args.discover_ids,
                refresh=args.refresh_discovery
            )

            return

        if args.refresh_discovery:

            run_discovery(
                companies,
                refresh=True
            )

            return

        selected = select_companies(
            companies,
            requested=args.company
        )

        if not selected:
            raise RuntimeError(
                "No enabled companies configured."
            )

        run_monitoring(
            selected,
            dry_run=args.dry_run
        )

    except Exception as exc:

        print("\nFATAL ERROR:", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()