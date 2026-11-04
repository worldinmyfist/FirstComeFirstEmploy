"""LinkedIn public guest job listings for one company (no login)."""

import html
import re
import time
from urllib.parse import parse_qs, urlparse

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import primary_country
from ..utils.http import request_text
from .base import Platform
from .registry import register

PAGE_SIZE = 25
API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def _company_ids_from_url(url):
    """Company IDs from the f_C URL parameter."""
    raw = parse_qs(urlparse(url).query).get("f_C", [])

    if not raw:
        return []

    return [c.strip() for c in raw[0].split(",") if c.strip().isdigit()]


def _extract(card, pattern):
    match = re.search(pattern, card, re.I | re.S)

    if not match:
        return ""

    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())


def _search_location(search):
    return search.get("location") or primary_country(search) or "India"


@register
class LinkedIn(Platform):
    name = "linkedin"

    def detect(self, host, path):
        return host.endswith("linkedin.com") and "/company/" in path

    def discover(self, session, company):
        ids = (
            company.get("linkedin_company_ids")
            or _company_ids_from_url(company["career_url"])
        )

        if isinstance(ids, str):
            ids = [ids]

        if not ids:
            raise RuntimeError(
                "Could not extract LinkedIn company IDs. "
                "Add linkedin_company_ids to companies.json."
            )

        return {"company_ids": ids, "api_url": API}

    def cache_key(self, metadata, search):
        return (
            self.name,
            tuple(metadata["company_ids"]),
            _search_location(search),
            search.get("search_text", ""),
        )

    def fetch_raw(self, session, company, metadata, search):
        # Descriptions are not fetched for LinkedIn, so description_exclude
        # filters do not apply to these results.
        params = {
            "f_C": ",".join(str(c) for c in metadata["company_ids"]),
            "location": _search_location(search),
            "count": PAGE_SIZE,
        }

        if search.get("search_text"):
            params["keywords"] = search["search_text"]

        jobs = {}

        for page_no in range(MAX_PAGES):
            params["start"] = page_no * PAGE_SIZE
            page = request_text(session, metadata["api_url"], dict(params))

            # Split the response into individual job cards.
            parts = re.split(r'data-entity-urn="urn:li:jobPosting:(\d+)"', page)
            found = 0

            for i in range(1, len(parts), 2):
                jid = parts[i]
                card = parts[i + 1][:10000]

                if jid in jobs:
                    continue

                title = _extract(
                    card, r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</'
                )

                if not title:
                    continue

                url_match = re.search(
                    r'<a[^>]+href="([^"]*linkedin\.com/jobs/view/[^"]+)"',
                    card,
                    re.I,
                )

                jobs[jid] = {
                    "title": title,
                    "location": _extract(
                        card,
                        r'class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)</',
                    ),
                    "url": (
                        html.unescape(url_match.group(1)) if url_match
                        else f"https://www.linkedin.com/jobs/view/{jid}"
                    ),
                    "posted": "",
                    "_description": "",
                }

                found += 1

            print(f"LinkedIn page {page_no + 1}: {found} new jobs")

            if found == 0:
                break

            time.sleep(POLITE_DELAY)

        return jobs, False
