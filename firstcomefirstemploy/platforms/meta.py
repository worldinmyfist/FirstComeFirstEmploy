
"""Meta Careers public GraphQL job listings.

Uses Meta's own careers API.

Optimizations:
- Parallel description requests (4 workers)
- No fixed 0.7-second delay
- Retry with backoff on HTTP 429 / transient errors
- Prefilter jobs before fetching descriptions
- Compatible with existing Platform caching and audit
"""

import json
import random
import time

import requests

from ..config import REQUEST_TIMEOUT
from ..filters import (
    description_filters_requested,
    passes_prefilters,
)
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description
from .base import Platform
from .registry import register


BASE = "https://www.metacareers.com"

SEARCH_DOC_ID = "27506805582236862"
DETAIL_DOC_ID = "27371134039243725"

# Performance settings
META_DETAIL_WORKERS = 4
META_MAX_RETRIES = 3
META_RETRY_BASE_DELAY = 2.0


def _graphql(session, doc_id, variables):
    """Execute Meta GraphQL request with retry/backoff."""

    token = "job-monitor"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-FB-LSD": token,
        "Origin": BASE,
        "Referer": BASE + "/jobsearch/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    payload = {
        "lsd": token,
        "variables": json.dumps(variables),
        "doc_id": doc_id,
    }

    for attempt in range(META_MAX_RETRIES + 1):
        try:
            response = session.post(
                BASE + "/graphql",
                data=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            # Rate limiting or temporary server errors
            if response.status_code in (
                429, 500, 502, 503, 504
            ):
                if attempt < META_MAX_RETRIES:
                    retry_after = response.headers.get(
                        "Retry-After"
                    )

                    try:
                        delay = float(retry_after)
                    except (ValueError, TypeError):
                        delay = (
                            META_RETRY_BASE_DELAY
                            * (2 ** attempt)
                            + random.uniform(0, 0.5)
                        )

                    print(
                        f"  Meta: HTTP {response.status_code}, "
                        f"retrying in {delay:.1f}s"
                    )

                    time.sleep(max(0, delay))
                    continue

                raise RuntimeError(
                    f"Meta: HTTP {response.status_code} "
                    f"after {META_MAX_RETRIES} retries"
                )

            response.raise_for_status()

            try:
                obj = json.loads(
                    response.text.splitlines()[0]
                )
            except (ValueError, IndexError) as exc:
                raise RuntimeError(
                    "Meta: unexpected GraphQL response"
                ) from exc

            if obj.get("errors"):
                raise RuntimeError(
                    "Meta GraphQL error: "
                    + str(obj["errors"])[:350]
                )

            if not isinstance(obj.get("data"), dict):
                raise RuntimeError(
                    "Meta: GraphQL returned no data"
                )

            return obj["data"]

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as exc:

            if attempt >= META_MAX_RETRIES:
                raise RuntimeError(
                    "Meta: network request failed "
                    f"after retries: {exc}"
                ) from exc

            delay = (
                META_RETRY_BASE_DELAY
                * (2 ** attempt)
                + random.uniform(0, 0.5)
            )

            print(
                f"  Meta: network error, "
                f"retrying in {delay:.1f}s"
            )
            time.sleep(delay)

    raise RuntimeError("Meta: GraphQL retries exhausted")


def _flatten(value):
    """Extract strings from nested description structures."""

    if isinstance(value, str):
        try:
            parsed = json.loads(value)

            if isinstance(parsed, (list, dict)):
                return _flatten(parsed)

        except (ValueError, TypeError):
            pass

        return [value]

    if isinstance(value, list):
        return [
            s
            for v in value
            for s in _flatten(v)
        ]

    if isinstance(value, dict):
        return [
            s
            for v in value.values()
            for s in _flatten(v)
        ]

    return []


def _description(info):
    """Extract job description and experience requirements."""

    wanted = {
        "description",
        "job_description",
        "minimum_qualifications",
        "preferred_qualifications",
        "responsibilities",
        "description_html",
        "job_description_summary",
        "role_description",
        "minimumQualifications",
    }

    fragments = []

    def walk(node):

        if isinstance(node, dict):
            for key, value in node.items():

                if key in wanted:
                    fragments.extend(_flatten(value))

                elif isinstance(value, (dict, list)):
                    walk(value)

        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(info)

    return clean_description(
        " ".join(dict.fromkeys(fragments))
    )


@register
class Meta(Platform):

    name = "meta"
    per_job_descriptions = True

    def detect(self, host, path):
        return host in (
            "metacareers.com",
            "www.metacareers.com",
        )

    def discover(self, session, company):

        return self.validated(
            session,
            company,
            {
                "api_url": BASE + "/graphql"
            },
        )

    def cache_key(self, metadata, search):
        """Reuse raw downloads for identical searches."""

        return (
            self.name,
            metadata["api_url"],
            description_filters_requested(search),
        )

    def _list(self, session):
        """Fetch Meta job inventory."""

        variables = {
            "isLoggedIn": False,
            "search_input": {
                "q": None,
                "divisions": [],
                "offices": [],
                "roles": [],
                "leadership_levels": [],
                "saved_jobs": [],
                "saved_searches": [],
                "sub_teams": [],
                "teams": [],
                "is_leadership": False,
                "is_remote_only": False,
                "sort_by_new": True,
                "page": 1,
                "results_per_page": None,
            },
            "viewasUserID": None,
        }

        data = _graphql(
            session,
            SEARCH_DOC_ID,
            variables,
        )

        container = data.get(
            "job_search_with_featured_jobs"
        )

        if (
            not isinstance(container, dict)
            or not isinstance(
                container.get("all_jobs"), list
            )
        ):
            raise RuntimeError(
                "Meta: unexpected job-search response"
            )

        jobs = {}

        for item in container["all_jobs"]:

            jid = str(
                item.get("id") or ""
            ).strip()

            if not jid:
                continue

            locs = item.get("locations") or []

            if isinstance(locs, str):
                locs = [locs]

            location = "; ".join(
                str(x) for x in locs if x
            )

            jobs[jid] = {
                "title": item.get("title") or "",
                "location": location,
                "url": f"{BASE}/jobs/{jid}/",
                "posted": "",
                "_description": "",
            }

        return jobs

    def _detail(self, session, jid):
        """Fetch description for one job."""

        data = _graphql(
            session,
            DETAIL_DOC_ID,
            {
                "renderLoggedInView": False,
                "requisitionID": jid,
                "viewasUserID": None,
            },
        )

        info = data.get(
            "xcp_requisition_job_description"
        )

        if info is None:
            return ""

        return _description(info)

    def fetch_raw(
        self,
        session,
        company,
        metadata,
        search,
    ):
        """Fetch jobs and parallelize description retrieval."""

        jobs = self._list(session)

        if not description_filters_requested(search):
            return jobs, False

        # Only fetch descriptions for jobs that survive
        # country and title filters.
        wanted = [
            jid
            for jid, job in jobs.items()
            if passes_prefilters(
                job,
                search,
                server_country=False,
            )
        ]

        print(
            f"  Meta: fetching {len(wanted)} "
            f"descriptions with "
            f"{META_DETAIL_WORKERS} workers"
        )

        if not wanted:
            return jobs, False

        start = time.monotonic()

        # Parallel requests.
        # map_concurrent preserves the input order.
        details = map_concurrent(
            lambda jid: self._detail(session, jid),
            wanted,
            workers=META_DETAIL_WORKERS,
        )

        for jid, description in zip(
            wanted, details
        ):
            jobs[jid]["_description"] = description

        empty = sum(
            not jobs[jid]["_description"]
            for jid in wanted
        )

        elapsed = time.monotonic() - start

        print(
            f"  Meta: {len(wanted)} detail requests, "
            f"{empty} without description, "
            f"completed in {elapsed:.1f}s"
        )

        return jobs, False
