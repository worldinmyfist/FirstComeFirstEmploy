"""Google Careers (plain HTML results page, best effort)."""

import re
import time

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import (
    description_filters_requested,
    passes_prefilters,
    primary_country,
)
from ..utils.http import request_text
from ..utils.page import extract_page_description
from ..utils.parallel import map_concurrent
from .base import Platform
from .registry import register

RESULTS_URL = "https://www.google.com/about/careers/applications/jobs/results/"


def _search_location(search):
    return search.get("location") or primary_country(search).title()


@register
class Google(Platform):
    name = "google"
    per_job_descriptions = True

    def detect(self, host, path):
        return host == "careers.google.com" or (
            "google.com" in host and "careers" in path
        )

    def discover(self, session, company):
        return {"api_url": RESULTS_URL}

    def cache_key(self, metadata, search):
        return (
            self.name,
            _search_location(search),
            search.get("search_text", ""),
            description_filters_requested(search),
        )

    def fetch_raw(self, session, company, metadata, search):
        location = _search_location(search)
        need_description = description_filters_requested(search)
        jobs = {}

        for page_no in range(1, MAX_PAGES + 1):
            params = {"page": page_no}

            if location:
                params["location"] = location

            if search.get("search_text"):
                params["q"] = search["search_text"]

            page = request_text(session, metadata["api_url"], params)

            titles = {}

            for m in re.finditer(r'\["(\d{10,})","([^"\\]{4,200})"', page):
                if not m.group(2).startswith("http"):
                    titles.setdefault(m.group(1), m.group(2))

            found = re.findall(
                r"jobs/results/(\d{10,})-([a-z0-9][a-z0-9\-]*)", page
            )

            new = 0
            page_jobs = {}

            for jid, slug in found:
                if jid in jobs or jid in page_jobs:
                    continue

                new += 1
                url = f"{RESULTS_URL}{jid}-{slug}"

                page_jobs[jid] = {
                    "title": titles.get(jid) or slug.replace("-", " ").title(),
                    "location": location or "",
                    "url": url,
                    "posted": "",
                    "_description": "",
                }

            if need_description:
                wanted = [
                    jid for jid, job in page_jobs.items()
                    if passes_prefilters(job, search, server_country=True)
                ]

                fetched = map_concurrent(
                    lambda jid: extract_page_description(session, page_jobs[jid]["url"]),
                    wanted,
                )

                for jid, text in zip(wanted, fetched):
                    page_jobs[jid]["_description"] = text

            jobs.update(page_jobs)

            if page_no == 1 and not jobs:
                raise RuntimeError(
                    "Google returned no postings. The page format may have "
                    "changed, or the filter matched nothing."
                )

            if new == 0:
                break

            time.sleep(POLITE_DELAY)

        return jobs, True