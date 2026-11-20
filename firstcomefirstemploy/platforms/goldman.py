
"""Goldman Sachs careers (higher.gs.com).

Listing:
    Public GraphQL search API.

Description:
    Full job details from higher.gs.com/roles/<id>.

Optimizations:
- Parallel description fetching (4 workers).
- Fetch details only for jobs passing country/title prefilters.
- Preserve GraphQL listing and pagination.
- Exclude general company footer from job descriptions.
- Raise errors when job descriptions cannot be extracted.
"""

import re
import time
from html.parser import HTMLParser

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import (
    description_filters_requested,
    passes_prefilters,
)
from ..utils.http import request_json, request_text
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description
from .base import Platform
from .registry import register


GRAPHQL_URL = "https://api-higher.gs.com/gateway/api/v1/graphql"
ROLE_URL = "https://higher.gs.com/roles/{}"

PAGE_SIZE = 100
DETAIL_WORKERS = 4

DEFAULT_EXPERIENCES = [
    "PROFESSIONAL",
    "EARLY_CAREER",
    "CAMPUS",
]

QUERY = """
query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items {
      roleId
      jobTitle
      corporateTitle
      division
      jobFunction
      locations { primary city state country }
      externalSource { sourceId }
    }
  }
}
"""


# ============================================================
# LOCATION EXTRACTION
# ============================================================

def _format_location(loc):
    parts = [
        loc.get("city"),
        loc.get("state"),
        loc.get("country"),
    ]

    return ", ".join(p for p in parts if p)


def _location_text(locations):
    locs = [
        l for l in (locations or [])
        if isinstance(l, dict)
    ]

    ordered = (
        [l for l in locs if l.get("primary")]
        + [l for l in locs if not l.get("primary")]
    )

    seen = set()
    out = []

    for loc in ordered:
        text = _format_location(loc)

        if text and text not in seen:
            seen.add(text)
            out.append(text)

    return "; ".join(out)


# ============================================================
# FULL DESCRIPTION EXTRACTION
# ============================================================

class _VisibleTextParser(HTMLParser):
    """Extract visible text while ignoring scripts and styles."""

    SKIP = {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
    }

    BREAKS = {
        "p",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "div",
        "section",
        "br",
        "article",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if self.skip_depth:
            self.skip_depth += 1
            return

        if tag in self.SKIP:
            self.skip_depth = 1
            return

        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.skip_depth and tag.lower() in self.BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if self.skip_depth:
            self.skip_depth -= 1
            return

        if tag in self.BREAKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            value = data.strip()

            if value:
                self.parts.append(value)


def _extract_full_description(page, title=""):
    """Extract role-specific text from Goldman detail HTML."""

    parser = _VisibleTextParser()
    parser.feed(page)

    text = "\n".join(parser.parts)

    # Goldman job pages usually start their descriptive
    # content with a section such as YOUR IMPACT.
    start_markers = [
        "YOUR IMPACT",
        "ROLE OVERVIEW",
        "JOB DESCRIPTION",
        "ABOUT THE ROLE",
        "RESPONSIBILITIES",
        "QUALIFICATIONS",
    ]

    # Start at the first recognizable job-content heading.
    starts = []

    for marker in start_markers:
        match = re.search(
            r"(?im)^\s*" + re.escape(marker) + r"\s*$",
            text,
        )
        if match:
            starts.append(match.start())

    if starts:
        text = text[min(starts):]

    elif title:
        # Fallback: remove navigation before the role title.
        pos = text.lower().find(title.lower())

        if pos >= 0:
            text = text[pos + len(title):]

    # Remove the generic corporate footer and other
    # non-job-specific text.
    footer_markers = [
        "ABOUT GOLDMAN SACHS",
        "The Goldman Sachs Group, Inc., 2021",
        "Goldman Sachs is an equal employment",
    ]

    for marker in footer_markers:
        pos = text.lower().find(marker.lower())

        if pos >= 0:
            text = text[:pos]

    return clean_description(text)


def _detail_description(session, jid, title=""):
    """Fetch and extract full description for one Goldman role."""

    url = ROLE_URL.format(jid)

    try:
        page = request_text(session, url)

    except Exception as exc:
        raise RuntimeError(
            f"Goldman: detail request failed "
            f"for job {jid}: {exc}"
        ) from exc

    description = _extract_full_description(
        page,
        title=title,
    )

    # Do not silently consider a page with no meaningful
    # job description to be successfully extracted.
    if len(description) < 100:
        raise RuntimeError(
            f"Goldman: description missing or too short "
            f"for job {jid}: {url}"
        )

    return description


# ============================================================
# GOLDMAN PLATFORM
# ============================================================

@register
class Goldman(Platform):

    name = "goldman"
    per_job_descriptions = True

    def detect(self, host, path):
        return host.endswith("higher.gs.com")

    def discover(self, session, company):
        return self.validated(
            session,
            company,
            {
                "api_url": GRAPHQL_URL,
            },
        )

    def cache_key(self, metadata, search):
        return (
            self.name,
            metadata["api_url"],
            tuple(search.get("experiences") or ()),
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
        experiences = [
            str(e).upper()
            for e in (
                search.get("experiences")
                or DEFAULT_EXPERIENCES
            )
        ]

        jobs = {}
        seen_pages = set()

        # ----------------------------------------------------
        # 1. FETCH GRAPHQL JOB LISTINGS
        # ----------------------------------------------------

        for page_no in range(MAX_PAGES):

            payload = {
                "operationName": "GetRoles",
                "query": QUERY,
                "variables": {
                    "searchQueryInput": {
                        "page": {
                            "pageSize": PAGE_SIZE,
                            "pageNumber": page_no,
                        },
                        "sort": {
                            "sortStrategy": "RELEVANCE",
                            "sortOrder": "DESC",
                        },
                        "filters": [],
                        "experiences": experiences,
                        "searchTerm": search.get(
                            "search_text", ""
                        ),
                    }
                },
            }

            data = request_json(
                session,
                metadata["api_url"],
                payload=payload,
            )

            if data.get("errors"):
                raise RuntimeError(
                    "Goldman GraphQL error: "
                    f"{data['errors'][:1]}"
                )

            result = (
                (data.get("data") or {})
                .get("roleSearch") or {}
            )

            items = result.get("items") or []

            # Stop if API returns a repeated page.
            signature = tuple(
                (i.get("externalSource") or {}).get("sourceId")
                or i.get("roleId")
                for i in items
            )

            if not items or signature in seen_pages:
                break

            seen_pages.add(signature)

            for item in items:

                source_id = (
                    (item.get("externalSource") or {})
                    .get("sourceId")
                )

                if (
                    not source_id
                    or not item.get("jobTitle")
                ):
                    continue

                source_id = str(source_id).strip()

                # Listing metadata is only a fallback;
                # it does not contain full qualifications.
                blurb = ". ".join(
                    x for x in (
                        item.get("corporateTitle"),
                        item.get("division"),
                        item.get("jobFunction"),
                    ) if x
                )

                jobs[source_id] = {
                    "title": item["jobTitle"].strip(),
                    "location": _location_text(
                        item.get("locations")
                    ),
                    "url": ROLE_URL.format(source_id),
                    "posted": "",
                    "_description": blurb,
                }

            if len(items) < PAGE_SIZE:
                break

            time.sleep(POLITE_DELAY)

        if not jobs:
            raise RuntimeError(
                "Goldman: no roles returned; "
                "the GraphQL schema may have changed."
            )

        # ----------------------------------------------------
        # 2. FETCH FULL DESCRIPTIONS IF NEEDED
        # ----------------------------------------------------

        if description_filters_requested(search):

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
                f"  Goldman: fetching {len(wanted)} "
                f"full descriptions with "
                f"{DETAIL_WORKERS} workers",
                flush=True,
            )

            if wanted:

                start = time.monotonic()

                details = map_concurrent(
                    lambda jid: _detail_description(
                        session,
                        jid,
                        jobs[jid]["title"],
                    ),
                    wanted,
                    workers=DETAIL_WORKERS,
                )

                for jid, description in zip(
                    wanted,
                    details,
                ):
                    jobs[jid]["_description"] = description

                empty = sum(
                    not jobs[jid]["_description"]
                    for jid in wanted
                )

                elapsed = time.monotonic() - start

                print(
                    f"  Goldman: {len(wanted)} "
                    f"detail requests, "
                    f"{empty} without description, "
                    f"completed in {elapsed:.1f}s",
                    flush=True,
                )

        return jobs, False
