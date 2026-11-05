"""SmartRecruiters (jobs.smartrecruiters.com)."""

import time

from ..config import MAX_PAGES, POLITE_DELAY
from ..filters import description_filters_requested, passes_prefilters
from ..utils.http import request_json
from ..utils.parallel import map_concurrent
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
    per_job_descriptions = True
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
        need_description = description_filters_requested(search)

        for _ in range(MAX_PAGES):
            data = request_json(
                session,
                metadata["api_url"],
                params={"limit": 100, "offset": offset},
            )

            content = data.get("content") or []
            total = data.get("totalFound", 0)

            page_jobs = {}

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

                page_jobs[str(jid)] = {
                    "title": (p.get("name") or "").strip(),
                    "location": location,
                    "url": f"https://jobs.smartrecruiters.com/{metadata['slug']}/{jid}",
                    "posted": p.get("releasedDate", ""),
                    "_description": "",
                }

            if need_description:
                # Only jobs that survive the country/title filters need a
                # description; fetch those concurrently.
                wanted = [
                    jid for jid, job in page_jobs.items()
                    if passes_prefilters(job, search)
                ]

                def describe(jid):
                    try:
                        return _fetch_description(session, metadata, jid)
                    except Exception as exc:
                        print(
                            f"  Could not fetch SmartRecruiters description "
                            f"for {jid}: {exc}"
                        )
                        return ""

                for jid, text in zip(wanted, map_concurrent(describe, wanted)):
                    page_jobs[jid]["_description"] = text

            jobs.update(page_jobs)

            offset += len(content)

            if not content or offset >= total:
                break

            time.sleep(POLITE_DELAY)

        return jobs, False