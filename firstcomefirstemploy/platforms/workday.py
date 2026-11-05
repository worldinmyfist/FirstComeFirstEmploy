"""Workday (myworkdayjobs.com): server-side facets via the public CXS API."""

import re
from urllib.parse import urljoin, urlparse

import requests

from ..config import PAGE_WORKERS
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

def _fetch_description(session, metadata, external_path, strict=False):
    """Full description of one job. strict=True raises after retries so the
    run keeps the previous state instead of making a wrong keep/exclude call."""
    path = (external_path or "").lstrip("/")

    if not path.startswith("job/"):
        return ""

    cxs_base = re.sub(r"/jobs$", "", metadata["api_url"].rstrip("/"))
    detail_url = f"{cxs_base}/{path}"

    def attempt():
        response = session.get(detail_url, timeout=20)

        # Job vanished between listing and detail fetch.
        if response.status_code == 404:
            return ""

        response.raise_for_status()
        info = response.json().get("jobPostingInfo") or {}
        return info.get("jobDescription", "") or ""

    try:
        return retry(attempt, (requests.RequestException, ValueError))
    except RuntimeError as exc:
        if strict:
            raise RuntimeError(
                f"Workday description fetch failed: {detail_url} :: {exc}"
            ) from exc

        print(f"  Could not fetch Workday description: {exc}")
        return ""


def _job_id(posting):
    for field in posting.get("bulletFields") or []:
        match = re.search(r"JR\d+", str(field), re.I)
        if match:
            return match.group(0).upper()

    external = posting.get("externalPath", "")
    match = re.search(r"JR\d+", external, re.I)
    return match.group(0).upper() if match else external


@register
class Workday(Platform):
    name = "workday"

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

        if need_description:
            def describe(jid):
                external = candidates[jid][1]

                return clean_description(
                    _fetch_description(
                        session, metadata, external, strict=not audit
                    )
                )

            order = list(candidates)

            for jid, text in zip(order, map_concurrent(describe, order)):
                descriptions[jid] = text

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
                "location": posting.get("locationsText", ""),
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