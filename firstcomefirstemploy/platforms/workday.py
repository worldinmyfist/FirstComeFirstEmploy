"""Workday (myworkdayjobs.com): server-side facets via the public CXS API."""

import re
import threading
import time
from urllib.parse import urljoin, urlparse

import requests

from ..config import (
    BOOTSTRAP_DEBUG,
    BOOTSTRAP_PROBE_TIMEOUT,
    PAGE_WORKERS,
    WORKDAY_PODS,
)
from ..filters import (
    description_filters_requested,
    is_audit,
    matches_description_filters,
    matches_title_filters,
)
from ..utils.http import request_json, retry
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description, normalize
from .base import Platform
from .registry import register

PAGE_SIZE = 20

# Requisition ids: JR12345 (most tenants) or R04711 (e.g. Cohesity).
_REQ_ID = re.compile(r"(?<![A-Za-z0-9])(?:JR|R)\d{4,}(?![A-Za-z0-9])", re.I)
# Multi-location postings come back as "2 Locations" in the listing.
_MULTI_LOC = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)

FACET_ALIASES = {
    "job family": [
        "job family", "job family group", "job category",
        "job family grouping",
    ],
    "country": [
        "country", "location country",
        "country from job posting location extended",
    ],
    "location": ["location", "locations", "primary location"],
}

# search field -> facet name
STANDARD_FIELDS = {
    "country": "country",
    "job_family": "job family",
    "location": "location",
}


def _as_list(value):
    return value if isinstance(value, list) else [value]


# ------------------------------------------------------------
# Facet discovery / resolution
# ------------------------------------------------------------

def _walk(values, key):
    output = []

    for value in values or []:
        if not isinstance(value, dict):
            continue

        # Nested groups (e.g. locationMainGroup) carry their own
        # facetParameter; ids below must use that key.
        here = value.get("facetParameter") or key

        if value.get("id") is not None:
            output.append({
                "descriptor": value.get("descriptor", ""),
                "id": value["id"],
                "count": value.get("count"),
                "key": here,
            })

        output.extend(_walk(value.get("values", []), here))

    return output


def _resolve_facet(metadata, name):
    candidates = FACET_ALIASES.get(normalize(name), [normalize(name)])

    matches = [
        facet for facet in metadata.get("facets", [])
        if normalize(facet.get("descriptor")) in candidates
        or normalize(facet.get("key")) in candidates
    ]

    if len(matches) == 1:
        return matches[0]

    available = [
        f"{f.get('descriptor')} [{f.get('key')}]"
        for f in metadata.get("facets", [])
    ]

    raise ValueError(
        f"Facet '{name}' not uniquely found. Available facets: {available}"
    )


def _resolve_entry(facet, name):
    wanted = normalize(name)

    matches = [
        value for value in facet["values"]
        if normalize(value.get("descriptor")) == wanted
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Value '{name}' not uniquely found in facet '{facet['descriptor']}'"
        )

    return matches[0]


def _apply_values(applied, facet, values):
    for name in values:
        entry = _resolve_entry(facet, name)
        applied.setdefault(entry.get("key") or facet["key"], []).append(entry["id"])


def _country_fallback(metadata, countries):
    """Some tenants have no 'Country' facet; look for the country as a
    value inside any location-like facet."""
    found = {}

    for country in countries:
        wanted = normalize(country)
        hit = False

        for facet in metadata.get("facets", []):
            label = normalize(
                facet.get("descriptor", "") + " " + facet.get("key", "")
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
                f"Country '{country}' not found in any Workday location "
                "facet. Run --discover COMPANY to see the available facets."
            )

    return found


def _build_facets(metadata, search):
    applied = {}

    for field, facet_name in STANDARD_FIELDS.items():
        requested = search.get(field)

        if not requested:
            continue

        values = _as_list(requested)

        try:
            _apply_values(applied, _resolve_facet(metadata, facet_name), values)
        except ValueError:
            if field != "country":
                raise

            applied.update(_country_fallback(metadata, values))

    for facet_name, requested in search.get("extra_facets", {}).items():
        _apply_values(
            applied, _resolve_facet(metadata, facet_name), _as_list(requested)
        )

    return applied


# ------------------------------------------------------------
# Job fetch
# ------------------------------------------------------------

def _fetch_detail(session, metadata, external_path, strict=False):
    """jobPostingInfo of one job (description, locations). strict=True raises after retries so the
    run keeps the previous state instead of making a wrong keep/exclude call."""
    path = (external_path or "").lstrip("/")

    if not path.startswith("job/"):
        return {}

    cxs_base = re.sub(r"/jobs$", "", metadata["api_url"].rstrip("/"))
    detail_url = f"{cxs_base}/{path}"

    def attempt():
        response = session.get(detail_url, timeout=20)

        # Job vanished between listing and detail fetch.
        if response.status_code == 404:
            return {}

        response.raise_for_status()
        return response.json().get("jobPostingInfo") or {}

    try:
        return retry(attempt, (requests.RequestException, ValueError))
    except RuntimeError as exc:
        if strict:
            raise RuntimeError(
                f"Workday description fetch failed: {detail_url} :: {exc}"
            ) from exc

        print(f"  Could not fetch Workday description: {exc}")
        return {}


def _fetch_description(session, metadata, external_path, strict=False):
    info = _fetch_detail(session, metadata, external_path, strict=strict)
    return info.get("jobDescription", "") or ""


def _location_from_detail(info, fallback):
    """'2 Locations' -> 'Bangalore - India Office; Pune - Panchshil - India Office'."""
    names = [info.get("location")] + list(info.get("additionalLocations") or [])
    names = [n for n in names if isinstance(n, str) and n.strip()]
    return "; ".join(dict.fromkeys(names)) or fallback

def _job_id(posting):
    for field in posting.get("bulletFields") or []:
        match = _REQ_ID.search(str(field))
        if match:
            return match.group(0).upper()

    external = posting.get("externalPath", "")
    match = _REQ_ID.search(external)
    return match.group(0).upper() if match else external



# ------------------------------------------------------------
# --bootstrap guessing (tenant x wdN pod x site)
# ------------------------------------------------------------

_CONTROL_TENANT = "zzqxnotarealtenant"
# A tenant we expect to exist; used once to check that the gate can tell a
# real tenant from a made-up one. If it moves, we merely fall back to the
# slower blind search.
_CALIBRATION = ("salesforce", 12)
_BLIND_SITES = 6        # sites per pod when the gate cannot discriminate

_GATE_LOCK = threading.Lock()
_CONTROL = {}           # pod -> signature of an unknown tenant's answer
_MODE = {}              # "selective" | "blind"


def _site_guesses(slug):
    cap = slug.capitalize()
    guesses = [
        "External", "External_Career_Site", "Careers", "careers",
        "ExternalCareerSite", f"{slug.upper()}ExternalCareerSite",
        f"{cap}ExternalCareerSite", "external_experienced", slug,
        f"{slug}careers", f"{cap}_Careers", f"{cap}External", "external",
        "jobs",
    ]
    return list(dict.fromkeys(guesses))


def _cxs_post(session, pod, tenant, site):
    url = f"https://{tenant}.wd{pod}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

    return session.post(
        url,
        json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        timeout=BOOTSTRAP_PROBE_TIMEOUT,
    )


def _cxs_total(session, pod, tenant, site):
    """Postings total if tenant+site is a live Workday board, else None.
    One request, short timeout, no retries (most guesses are misses)."""
    try:
        response = _cxs_post(session, pod, tenant, site)

        if response.status_code != 200:
            return None

        data = response.json()
    except (requests.RequestException, ValueError):
        return None

    if not isinstance(data, dict) or "total" not in data:
        return None

    return data["total"]


def _signature(session, pod, tenant):
    """How Workday answers for this tenant with a bogus site."""
    site = "zz-probe-site"

    try:
        response = _cxs_post(session, pod, tenant, site)
    except requests.RequestException as exc:
        return ("ERR", type(exc).__name__)

    body = (response.text or "")[:200]

    for word in (tenant, site):
        body = body.replace(word, "<x>")

    return (response.status_code, body)


def _control(session, pod):
    with _GATE_LOCK:
        known = _CONTROL.get(pod)

    if known is None:
        known = _signature(session, pod, _CONTROL_TENANT)

        with _GATE_LOCK:
            _CONTROL[pod] = known

    return known


def _pod_has_tenant(session, pod, tenant):
    """True when `tenant` answers differently from a made-up tenant."""
    return _signature(session, pod, tenant) != _control(session, pod)


def _gate_mode(session):
    """'selective' if the pod check separates a real tenant from a fake one
    (calibrated on a known tenant), else 'blind'."""
    with _GATE_LOCK:
        mode = _MODE.get("value")

    if mode is None:
        tenant, pod = _CALIBRATION
        mode = "selective" if _pod_has_tenant(session, pod, tenant) else "blind"

        with _GATE_LOCK:
            _MODE["value"] = mode

    return mode


def _reset_gate():
    with _GATE_LOCK:
        _CONTROL.clear()
        _MODE.clear()


def _sample_jobs(session, metadata):
    """First listing page as {id: job}: enough to prove the board works and
    to show region/title stats without paging through every posting."""
    data = request_json(
        session,
        metadata["api_url"],
        payload={
            "appliedFacets": {}, "limit": PAGE_SIZE,
            "offset": 0, "searchText": "",
        },
    )

    jobs = {}

    for posting in data.get("jobPostings") or []:
        jid = _job_id(posting)

        if not jid:
            continue

        jobs[jid] = {
            "title": (posting.get("title") or "").strip(),
            "location": posting.get("locationsText", ""),
            "url": urljoin(
                metadata["job_base_url"] + "/",
                (posting.get("externalPath") or "").lstrip("/"),
            ),
            "posted": posting.get("postedOn", ""),
        }

    return jobs


@register
class Workday(Platform):
    name = "workday"
    bootstrap_order = 90  # many requests per guess: after the cheap platforms

    def detect(self, host, path):
        return host.endswith("myworkdayjobs.com")

    def discover(self, session, company):
        parsed = urlparse(company["career_url"])
        host = parsed.netloc

        match = re.match(r"^(.+?)\.wd\d+\.myworkdayjobs\.com$", host, re.I)

        if not match:
            raise ValueError("Unsupported Workday URL")

        tenant = company.get("tenant", match.group(1))

        parts = [p for p in parsed.path.split("/") if p]

        if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
            parts.pop(0)

        site = company.get("site", parts[0] if parts else "")

        if not site:
            raise ValueError("Could not identify Workday site")

        base_url = f"{parsed.scheme}://{host}"
        api_url = f"{base_url}/wday/cxs/{tenant}/{site}/jobs"

        data = request_json(
            session,
            api_url,
            payload={
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": 0,
                "searchText": "",
            },
        )

        facets = []

        for facet in data.get("facets", []):
            key = facet.get("facetParameter")

            if not key:
                continue

            facets.append({
                "key": key,
                "descriptor": facet.get("descriptor", ""),
                "values": _walk(facet.get("values", []), key),
            })

        return {
            "tenant": tenant,
            "site": site,
            "api_url": api_url,
            "job_base_url": f"{base_url}/{site}",
            "total_postings": data.get("total", 0),
            "facets": facets,
        }

    # ---- --bootstrap -------------------------------------------

    def probe_company(self, session, company):
        board = self.verify(session, company)
        return board["jobs"] if board else None

    def verify(self, session, company):
        """Works for a full Workday URL (guessed or given by an LLM)."""
        try:
            meta = self.discover(session, dict(company))
            jobs = _sample_jobs(session, meta)
        except Exception:
            return None

        if not jobs:
            return None

        return {
            "platform": self.name,
            "company": dict(company),
            "jobs": jobs,
            "total": meta.get("total_postings") or len(jobs),
            "live_checked": True,
            "sampled": True,
        }

    def find_board(self, session, slugs, delay=0.0):
        """Guess tenant (= slug), then wdN pod, then site; every hit is
        confirmed by the real CXS API before it is returned."""
        for slug in slugs:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
                continue

            mode = _gate_mode(session)

            if mode == "selective":
                flags = map_concurrent(
                    lambda n: _pod_has_tenant(session, n, slug),
                    WORKDAY_PODS, workers=5,
                )
                pods = [n for n, ok in zip(WORKDAY_PODS, flags) if ok]
                sites = _site_guesses(slug)
            else:
                pods = list(WORKDAY_PODS)
                sites = _site_guesses(slug)[:_BLIND_SITES]

            if BOOTSTRAP_DEBUG:
                print(f"  [workday] {slug}: mode={mode} pods={pods}")

            tasks = [(n, site) for n in pods for site in sites]
            totals = map_concurrent(
                lambda t: _cxs_total(session, t[0], slug, t[1]),
                tasks, workers=6,
            )

            for (pod, site), total in zip(tasks, totals):
                if total is None:
                    continue

                company = {
                    "career_url": f"https://{slug}.wd{pod}.myworkdayjobs.com/{site}",
                    "tenant": slug,
                    "site": site,
                }
                board = self.verify(session, company)

                if delay:
                    time.sleep(delay)

                if board:
                    return board

        return None

    def describe(self, metadata):
        print("Tenant:", metadata["tenant"])
        print("Site:", metadata["site"])
        print("\nDISCOVERED FACETS")

        for facet in metadata["facets"]:
            print(f"\n{facet['descriptor']} [{facet['key']}]")

            for value in facet["values"]:
                print(f"  {value['descriptor']}: {value['id']}")

    def fetch(self, session, company, metadata, search):
        applied = _build_facets(metadata, search)
        audit = is_audit(search)
        need_description = description_filters_requested(search)

        def load_page(offset):
            return request_json(
                session,
                metadata["api_url"],
                payload={
                    "appliedFacets": applied,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "searchText": search.get("search_text", ""),
                },
            )

        first = load_page(0)

        # Workday only reports `total` on the first page; once it is known
        # the remaining pages are independent, so fetch them concurrently.
        total = first.get("total", 0)
        print("  Matching postings:", total)

        pages = [first.get("jobPostings") or []]

        if pages[0] and len(pages[0]) >= PAGE_SIZE:
            offsets = list(range(len(pages[0]), total, PAGE_SIZE))

            pages.extend(
                data.get("jobPostings") or []
                for data in map_concurrent(load_page, offsets, workers=PAGE_WORKERS)
            )

        parsed = 0
        candidates = {}

        for postings in pages:
            for posting in postings:
                external = posting.get("externalPath", "")
                jid = _job_id(posting)

                if not jid:
                    continue

                parsed += 1
                title = (posting.get("title") or "").strip()

                # Skip the per-job description request for jobs the title
                # filters will reject anyway.
                if not matches_title_filters(title, search):
                    continue

                candidates[jid] = (title, external, posting)

        # Description requests are independent too. In strict (non-audit)
        # mode a failure aborts the run so the previous state is kept.
        descriptions = {}
        details = {}

        multi = [
            jid for jid, (_, _, posting) in candidates.items()
            if _MULTI_LOC.match(posting.get("locationsText") or "")
        ]
        targets = list(candidates) if need_description else multi

        if targets:
            def load_detail(jid):
                # Only a description-filter failure aborts the run; a missing
                # location detail just keeps "2 Locations".
                return _fetch_detail(
                    session, metadata, candidates[jid][1],
                    strict=need_description and not audit,
                )

            for jid, info in zip(targets, map_concurrent(load_detail, targets)):
                details[jid] = info
                descriptions[jid] = clean_description(
                    info.get("jobDescription", "") or ""
                ) if need_description else ""

        jobs = {}

        for jid, (title, external, posting) in candidates.items():
            description = descriptions.get(jid, "")

            # Audit mode keeps the job so the audit can report it.
            if (
                not audit
                and description.strip()
                and not matches_description_filters(description, search)
            ):
                continue

            job = {
                "title": title,
                "location": (
                    _location_from_detail(
                        details.get(jid) or {}, posting.get("locationsText", "")
                    )
                    if jid in multi
                    else posting.get("locationsText", "")
                ),
                "url": urljoin(
                    metadata["job_base_url"] + "/", external.lstrip("/")
                ),
                "posted": posting.get("postedOn", ""),
            }

            if audit:
                job["_description"] = description

            jobs[jid] = job

        # Count parsed postings, not kept jobs, so a search where every job
        # is filtered out doesn't raise.
        if total and not parsed:
            raise RuntimeError("Workday reported postings but none were parsed")

        return jobs