
"""Google Careers (HTML results and full job detail extraction).

Fix:
- Extract minimum and preferred qualifications from job pages.
- Preserve the general job description.
- Fetch each detail page once.
- Keep existing parallel fetching, pagination and filtering.
"""

import html
import re
import time

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import (
    description_filters_requested,
    passes_prefilters,
    primary_country,
)
from ..utils.http import request_text
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description
from .base import Platform
from .registry import register


RESULTS_URL = (
    "https://www.google.com/about/careers/"
    "applications/jobs/results/"
)


def _search_location(search):
    return search.get("location") or primary_country(search).title()


def _extract_section(page, heading):
    """Extract a qualifications list from Google's HTML.

    Handles normal HTML and JSON-embedded escaped HTML.
    """

    pattern = (
        r"<h3\b[^>]*>\s*"
        + re.escape(heading)
        + r"\s*:?\s*</h3>\s*"
        + r"<ul\b[^>]*>(.*?)</ul>"
    )

    match = re.search(
        pattern,
        page,
        flags=re.I | re.S,
    )

    if not match:
        return ""

    content = clean_description(match.group(1))

    if not content:
        return ""

    return heading + ": " + content


def _extract_meta_description(page):
    """Extract the general job description from HTML metadata."""

    patterns = [
        (
            r'<meta[^>]+(?:name|property)=["\']'
            r'(?:description|og:description)["\']'
            r'[^>]+content=["\'](.*?)["\']'
        ),
        (
            r'<meta[^>]+content=["\'](.*?)["\']'
            r'[^>]+(?:name|property)=["\']'
            r'(?:description|og:description)["\']'
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            page,
            flags=re.I | re.S,
        )

        if match:
            return clean_description(match.group(1))

    return ""


def extract_google_description(session, url):
    """Fetch and extract full Google job qualifications and description."""

    try:
        page = request_text(session, url)

    except Exception as exc:
        print(
            f"  Google ERROR: could not fetch job detail "
            f"{url}: {exc}"
        )
        return ""

    # Convert HTML entities and JSON-escaped markup.
    page = html.unescape(page)

    page = (
        page.replace("\\u003c", "<")
            .replace("\\u003e", ">")
            .replace("\\u0026", "&")
            .replace("\\/", "/")
            .replace("\\n", " ")
    )

    sections = []

    # ---------------------------------------------------------
    # 1. Minimum qualifications
    # ---------------------------------------------------------

    minimum = _extract_section(
        page,
        "Minimum qualifications",
    )

    if minimum:
        sections.append(minimum)

    else:
        print(
            "  Google WARNING: minimum qualifications "
            f"not extracted: {url}"
        )

    # ---------------------------------------------------------
    # 2. Preferred qualifications
    # ---------------------------------------------------------

    preferred = _extract_section(
        page,
        "Preferred qualifications",
    )

    if preferred:
        sections.append(preferred)

    # ---------------------------------------------------------
    # 3. General description
    # ---------------------------------------------------------

    general = _extract_meta_description(page)

    if general:
        sections.append(general)

    # ---------------------------------------------------------
    # 4. Combine and clean
    # ---------------------------------------------------------

    description = clean_description(
        " ".join(sections)
    )

    return description


@register
class Google(Platform):

    name = "google"
    per_job_descriptions = True

    def detect(self, host, path):
        return host == "careers.google.com" or (
            "google.com" in host and "careers" in path
        )

    def discover(self, session, company):
        return {
            "api_url": RESULTS_URL
        }

    def cache_key(self, metadata, search):
        return (
            self.name,
            _search_location(search),
            search.get("search_text", ""),
            description_filters_requested(search),
        )

    def fetch_raw(
        self,
        session,
        company,
        metadata,
        search,
    ):
        location = _search_location(search)

        need_description = description_filters_requested(
            search
        )

        jobs = {}

        for page_no in range(1, MAX_PAGES + 1):

            params = {
                "page": page_no
            }

            if location:
                params["location"] = location

            if search.get("search_text"):
                params["q"] = search["search_text"]

            page = request_text(
                session,
                metadata["api_url"],
                params,
            )

            titles = {}

            for match in re.finditer(
                r'\["(\d{10,})","([^"\\]{4,200})"',
                page,
            ):
                if not match.group(2).startswith("http"):
                    titles.setdefault(
                        match.group(1),
                        match.group(2),
                    )

            found = re.findall(
                r"jobs/results/(\d{10,})-([a-z0-9][a-z0-9\-]*)",
                page,
            )

            new = 0
            page_jobs = {}

            for jid, slug in found:

                if jid in jobs or jid in page_jobs:
                    continue

                new += 1

                url = (
                    f"{RESULTS_URL}{jid}-{slug}"
                )

                page_jobs[jid] = {
                    "title": (
                        titles.get(jid)
                        or slug.replace("-", " ").title()
                    ),
                    "location": location or "",
                    "url": url,
                    "posted": "",
                    "_description": "",
                }

            # -------------------------------------------------
            # Fetch descriptions in parallel
            # -------------------------------------------------

            if need_description:

                wanted = [
                    jid
                    for jid, job in page_jobs.items()
                    if passes_prefilters(
                        job,
                        search,
                        server_country=True,
                    )
                ]

                if wanted:

                    fetched = map_concurrent(
                        lambda jid: extract_google_description(
                            session,
                            page_jobs[jid]["url"],
                        ),
                        wanted,
                    )

                    for jid, text in zip(
                        wanted,
                        fetched,
                    ):
                        page_jobs[jid][
                            "_description"
                        ] = text

            jobs.update(page_jobs)

            if page_no == 1 and not jobs:
                raise RuntimeError(
                    "Google returned no postings. "
                    "The page format may have changed, "
                    "or the filter matched nothing."
                )

            if new == 0:
                break

            time.sleep(POLITE_DELAY)

        return jobs, True
