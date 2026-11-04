"""SmartRecruiters (jobs.smartrecruiters.com)."""

import time

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import description_filters_requested
from ..utils.http import request_json
from ..utils.text import clean_description
from .base import Platform, slug_from_company
from .registry import register

COUNTRY_NAMES = {
    "in": "India", "sg": "Singapore",
    "gb": "United Kingdom", "uk": "United Kingdom",
    "us": "United States", "ca": "Canada", "au": "Australia",
}


def _fetch_description(session, metadata, job_id):
    """Full job-ad text from the individual posting endpoint."""
    detail = request_json(
        session, metadata["api_url"].rstrip("/") + f"/{job_id}"
    )

    sections = (detail.get("jobAd") or {}).get("sections") or {}
    items = sections.values() if isinstance(sections, dict) else sections

    return clean_description("\n".join(
        str(s.get("text") or "") for s in items if isinstance(s, dict)
    ))


@register
class SmartRecruiters(Platform):
    name = "smartrecruiters"
    bootstrap_url = "https://jobs.smartrecruiters.com/{slug}"

    def detect(self, host, path):
        return host.endswith("smartrecruiters.com")

    def discover(self, session, company):
        slug = slug_from_company(company)

        return self.validated(session, company, {
            "slug": slug,
            "api_url": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
        })

    def cache_key(self, metadata, search):
        # Description fetching depends on this search's exclusions.
        return (self.name, metadata["api_url"], description_filters_requested(search))

    def fetch_raw(self, session, company, metadata, search):
        jobs = {}
        offset = 0

        for _ in range(MAX_PAGES):
            data = request_json(
                session,
                metadata["api_url"],
                params={"limit": 100, "offset": offset},
            )

            content = data.get("content") or []
            total = data.get("totalFound", 0)

            for p in content:
                jid = p.get("id")

                if not jid:
                    continue

                loc = p.get("location") or {}
                country = loc.get("country") or ""
                country = COUNTRY_NAMES.get(country.lower(), country)

                location = loc.get("fullLocation") or ", ".join(
                    x for x in (loc.get("city"), loc.get("region"), country) if x
                )

                description = ""

                if description_filters_requested(search):
                    try:
                        description = _fetch_description(session, metadata, jid)
                    except Exception as exc:
                        print(
                            f"  Could not fetch SmartRecruiters description "
                            f"for {jid}: {exc}"
                        )

                jobs[str(jid)] = {
                    "title": (p.get("name") or "").strip(),
                    "location": location,
                    "url": f"https://jobs.smartrecruiters.com/{metadata['slug']}/{jid}",
                    "posted": p.get("releasedDate", ""),
                    "_description": description,
                }

            offset += len(content)

            if not content or offset >= total:
                break

            time.sleep(POLITE_DELAY)

        return jobs, False
