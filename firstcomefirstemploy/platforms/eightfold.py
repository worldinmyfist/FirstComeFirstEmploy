"""Eightfold-powered portals (Microsoft careers and others)."""

import time
from urllib.parse import urljoin, urlparse

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import (
    description_filters_requested,
    passes_prefilters,
    primary_country,
)
from ..utils.http import request_json
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description, fmt_ts, join_locations
from .base import Platform
from .registry import register


def _search_location(search):
    return search.get("location") or primary_country(search).title()


def _data(payload):
    inner = payload.get("data")
    return inner if isinstance(inner, dict) else payload


def _pick(source, *keys):
    for key in keys:
        if source.get(key):
            return source[key]
    return ""


@register
class Eightfold(Platform):
    name = "eightfold"
    per_job_descriptions = True

    def detect(self, host, path):
        return host.endswith("careers.microsoft.com") or host.endswith("eightfold.ai")

    def discover(self, session, company):
        parsed = urlparse(company["career_url"])
        host = parsed.netloc.lower()

        if company.get("api_url"):
            api_url = company["api_url"]
        elif host.endswith("eightfold.ai"):
            api_url = f"https://{host}/api/apply/v2/jobs"
        else:
            api_url = f"{parsed.scheme}://{host}/api/pcsx/search"

        return {
            "api_url": api_url,
            "domain": company.get("domain") or ".".join(host.split(".")[-2:]),
            "site_base": f"{parsed.scheme}://{host}",
        }

    def cache_key(self, metadata, search):
        return (
            self.name,
            metadata["api_url"],
            metadata["domain"],
            _search_location(search),
            search.get("search_text", ""),
            description_filters_requested(search),
        )

    def _detail_description(self, session, metadata, job_id):
        detail = request_json(
            session,
            metadata["site_base"].rstrip("/") + "/api/pcsx/position_details",
            params={"domain": metadata["domain"], "position_id": job_id},
        )

        return clean_description(_pick(
            _data(detail),
            "jobDescription", "job_description", "description", "descriptionText",
        ))

    def fetch_raw(self, session, company, metadata, search):
        location = _search_location(search)
        fetch_descriptions = description_filters_requested(search)

        jobs = {}
        start = 0

        for _ in range(MAX_PAGES):
            params = {
                "domain": metadata["domain"],
                "query": search.get("search_text", ""),
                "start": start,
                "sort_by": "timestamp",
            }

            if location:
                params["location"] = location

            payload = _data(request_json(session, metadata["api_url"], params=params))

            positions = payload.get("positions") or []
            total = payload.get("count") or payload.get("total") or 0

            page_jobs = {}
            raw_ids = {}

            for p in positions:
                jid = p.get("id") or p.get("display_job_id")

                if not jid:
                    continue

                link = (
                    p.get("canonicalPositionUrl")
                    or p.get("positionUrl")
                    or f"/careers/job/{jid}"
                )

                description = clean_description(_pick(
                    p, "jobDescription", "description",
                    "descriptionText", "job_description",
                ))

                page_jobs[str(jid)] = {
                    "title": (p.get("name") or p.get("title") or "").strip(),
                    "location": join_locations(p.get("locations") or p.get("location")),
                    "url": urljoin(metadata["site_base"] + "/", link),
                    "posted": fmt_ts(
                        p.get("postedTs") or p.get("t_create") or p.get("creationTs")
                    ),
                    "_description": description,
                }
                raw_ids[str(jid)] = jid

            if fetch_descriptions:
                # Listing results often lack the full description. Fetch it
                # concurrently, and only for jobs that pass the cheap filters.
                wanted = [
                    key for key, job in page_jobs.items()
                    if not job["_description"]
                    and passes_prefilters(job, search, server_country=bool(location))
                ]

                def describe(key):
                    try:
                        return self._detail_description(
                            session, metadata, raw_ids[key]
                        )
                    except Exception as exc:
                        print(
                            f"  Eightfold description unavailable "
                            f"for {raw_ids[key]}: {exc}"
                        )
                        return ""

                for key, text in zip(wanted, map_concurrent(describe, wanted)):
                    page_jobs[key]["_description"] = text

            jobs.update(page_jobs)

            start += len(positions)

            if not positions or (total and start >= total):
                break

            time.sleep(POLITE_DELAY)

        return jobs, bool(location)