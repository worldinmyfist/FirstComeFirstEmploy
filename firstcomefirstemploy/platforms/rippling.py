
"""Rippling ATS boards (ats.rippling.com), public v2 JSON API, no key.

List:   GET /api/v2/board/<slug>/jobs?page=N&pageSize=100
        -> {items, page, pageSize, totalItems, totalPages}
Detail: GET https://api.rippling.com/platform/api/ats/v1/board/<slug>/jobs/<id>

A job posted in several locations appears once per location with the same
id, so locations are merged per id.
"""

import time

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import description_filters_requested, passes_prefilters
from ..utils.http import request_json
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description
from .base import Platform, slug_from_company
from .registry import register

HOST = "https://ats.rippling.com"
API = HOST + "/api/v2/board/{slug}/jobs"
PAGE_SIZE = 100
DETAIL_API = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{id}"


def _strings(value):
    """Every string inside a nested dict/list (location and description
    shapes vary, so read them defensively)."""
    if isinstance(value, str):
        return [value]

    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]

    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]

    return []


def _one_location(loc):
    """'Bengaluru, India' from {name, country, countryCode, city, ...}."""
    if isinstance(loc, str):
        return loc.strip()

    if not isinstance(loc, dict):
        return ""

    name = (loc.get("name") or loc.get("city") or "").strip()
    country = (loc.get("country") or "").strip()

    # name is usually "City, ST"; add the country so country filters work,
    # unless the name already says it.
    if country and country.lower() not in name.lower():
        name = f"{name}, {country}" if name else country

    return name


def _location(item):
    """Joined locations of one list row (a row normally has one)."""
    value = item.get("locations")

    if value is None:
        value = item.get("location") or item.get("workLocation")

    if isinstance(value, dict):
        value = [value]

    if not isinstance(value, (list, tuple)):
        value = [value] if value else []

    names = [_one_location(v) for v in value]
    return "; ".join(dict.fromkeys(n for n in names if n))


@register
class Rippling(Platform):
    name = "rippling"
    per_job_descriptions = True
    bootstrap_url = HOST + "/{slug}/jobs"
    bootstrap_order = 45

    def detect(self, host, path):
        return host == "ats.rippling.com"

    def discover(self, session, company):
        slug = slug_from_company(company)

        return self.validated(session, company, {
            "slug": slug,
            "api_url": API.format(slug=slug),
        })

    def cache_key(self, metadata, search):
        return (self.name, metadata["api_url"], description_filters_requested(search))

    def _list(self, session, metadata):
        jobs = {}
        seen_pages = set()

        for page_no in range(MAX_PAGES):
            data = request_json(
                session,
                metadata["api_url"],
                params={"page": page_no, "pageSize": PAGE_SIZE},
            )
            items = data.get("items") or []
            signature = tuple(i.get("id") for i in items)

            if not items or signature in seen_pages:
                break

            seen_pages.add(signature)

            for item in items:
                jid = item.get("id")

                if not jid:
                    continue

                jid = str(jid)
                location = _location(item)

                if jid in jobs:
                    # Same job listed once per location: merge them.
                    known = jobs[jid]["location"]

                    if location and location not in known.split("; "):
                        jobs[jid]["location"] = "; ".join(
                            x for x in (known, location) if x
                        )

                    continue

                jobs[jid] = {
                    "title": (item.get("name") or item.get("title") or "").strip(),
                    "location": location,
                    "url": item.get("url") or f"{HOST}/{metadata['slug']}/jobs/{jid}",
                    "posted": item.get("createdOn") or "",
                    "_description": "",
                }

            total_pages = data.get("totalPages")

            if total_pages is not None and page_no + 1 >= total_pages:
                break

            if len(items) < PAGE_SIZE and total_pages is None:
                break

            time.sleep(POLITE_DELAY)

        return jobs

    def _detail_text(self, session, metadata, jid):
        """(description, createdOn, missing). missing=True on HTTP 404."""
        url = DETAIL_API.format(slug=metadata["slug"], id=jid)

        try:
            info = request_json(session, url)
        except RuntimeError as exc:
            # A posting can vanish (or be unlisted) between the list call and
            # its detail call. Any other error still aborts the run.
            if "HTTP 404" in str(exc):
                return "", "", True

            raise

        text = " ".join(
            string
            for key, value in info.items()
            if "descr" in str(key).lower()
            for string in _strings(value)
        )

        if not text.strip():
            # Field name differs: fall back to any long text in the record.
            text = " ".join(
                string for string in _strings(info) if len(string) > 200
            )

        return text, info.get("createdOn") or "", False

    def fetch_raw(self, session, company, metadata, search):
        jobs = self._list(session, metadata)

        if not jobs:
            raise RuntimeError(
                "Rippling: no jobs returned; the board slug may be wrong or "
                "the API changed."
            )

        if description_filters_requested(search):
            # Detail requests only for jobs that survive country + title.
            wanted = [
                jid for jid, job in jobs.items()
                if passes_prefilters(job, search, server_country=False)
            ]

            details = map_concurrent(
                lambda jid: self._detail_text(session, metadata, jid), wanted
            )

            missing = 0

            for jid, (text, created, gone) in zip(wanted, details):
                missing += gone

                if text:
                    jobs[jid]["_description"] = clean_description(text)

                if created and not jobs[jid]["posted"]:
                    jobs[jid]["posted"] = created

            empty = sum(1 for jid in wanted if not jobs[jid]["_description"])
            print(
                f"  Rippling: {len(wanted)} detail pages, "
                f"{missing} returned 404, {empty} without description"
            )

            if wanted and missing == len(wanted):
                raise RuntimeError(
                    "Rippling: every job detail request returned 404; the "
                    "v1 detail endpoint is unavailable; check the board slug and API."
                )

        return jobs, False
