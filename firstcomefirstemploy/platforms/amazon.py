"""Amazon Jobs (amazon.jobs search.json)."""

import re
import time
from urllib.parse import urljoin

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import (
    description_filters_requested,
    passes_prefilters,
    primary_country,
)
from ..utils.http import request_json
from ..utils.page import extract_page_description
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description
from .base import Platform
from .registry import register

COUNTRY_CODES = {
    "india": "IND", "singapore": "SGP",
    "united kingdom": "GBR", "united states": "USA",
    "canada": "CAN", "australia": "AUS",
}


def _location_matches_country(location, country):
    if not country:
        return True

    location = (location or "").strip()

    if not location:
        return False

    terms = [country]
    code = COUNTRY_CODES.get(country.lower())

    if code:
        terms.append(code)

    return any(
        re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", location, re.I)
        for term in terms
    )


@register
class Amazon(Platform):
    name = "amazon"
    per_job_descriptions = True

    def detect(self, host, path):
        return host.endswith("amazon.jobs")

    def discover(self, session, company):
        return {"api_url": "https://www.amazon.jobs/en/search.json"}

    def cache_key(self, metadata, search):
        return (
            self.name,
            primary_country(search),
            search.get("amazon_country", ""),
            search.get("search_text", ""),
            description_filters_requested(search),
        )

    def fetch_raw(self, session, company, metadata, search):
        country = primary_country(search).strip()
        code = search.get("amazon_country") or COUNTRY_CODES.get(country.lower(), "")

        params = {
            "base_query": search.get("search_text", ""),
            "result_limit": 100,
            "sort": "recent",
        }

        if code:
            params["country[]"] = code
        elif country:
            params["loc_query"] = country

        need_description = description_filters_requested(search)
        jobs = {}
        offset = 0

        for _ in range(MAX_PAGES):
            params["offset"] = offset
            data = request_json(session, metadata["api_url"], params=params)

            batch = data.get("jobs") or []
            hits = data.get("hits", 0)

            page_jobs = {}
            needs_page = []

            for p in batch:
                jid = p.get("id_icims") or p.get("id")

                if not jid:
                    continue

                location = (
                    p.get("normalized_location") or p.get("location", "")
                ).strip()

                if not _location_matches_country(location, country):
                    continue

                url = urljoin("https://www.amazon.jobs", p.get("job_path", ""))
                full = p.get("description") or p.get("job_description")
                description = full or p.get("description_short") or ""

                page_jobs[str(jid)] = {
                    "title": (p.get("title") or "").strip(),
                    "location": location,
                    "url": url,
                    "posted": p.get("posted_date", ""),
                    "_description": clean_description(description),
                }

                if need_description and not full:
                    needs_page.append(str(jid))

            # Job-detail pages are only worth fetching for jobs that pass the
            # country/title filters; fetch those concurrently.
            wanted = [
                key for key in needs_page
                if passes_prefilters(page_jobs[key], search, server_country=bool(code))
            ]

            fetched = map_concurrent(
                lambda key: extract_page_description(session, page_jobs[key]["url"]),
                wanted,
            )

            for key, text in zip(wanted, fetched):
                if text:
                    page_jobs[key]["_description"] = clean_description(text)

            jobs.update(page_jobs)

            offset += len(batch)

            if not batch or offset >= hits:
                break

            time.sleep(POLITE_DELAY)

        return jobs, bool(code)