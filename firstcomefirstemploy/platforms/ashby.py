"""Ashby (jobs.ashbyhq.com)."""

from ..utils.http import request_json
from ..utils.text import fmt_ts, join_locations
from .base import Platform, slug_from_company
from .registry import register


@register
class Ashby(Platform):
    name = "ashby"
    bootstrap_url = "https://jobs.ashbyhq.com/{slug}"

    def detect(self, host, path):
        return host.endswith("ashbyhq.com")

    def discover(self, session, company):
        slug = slug_from_company(company)

        return self.validated(session, company, {
            "slug": slug,
            "api_url": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        })

    def fetch_raw(self, session, company, metadata, search):
        data = request_json(
            session, metadata["api_url"], params={"includeCompensation": "false"}
        )

        jobs = {}

        for p in data.get("jobs", []):
            if not p.get("id"):
                continue

            locations = [p.get("location")]

            for extra in p.get("secondaryLocations") or []:
                locations.append(
                    extra.get("location") if isinstance(extra, dict) else extra
                )

            jobs[str(p["id"])] = {
                "title": (p.get("title") or "").strip(),
                "location": join_locations(locations),
                "url": p.get("jobUrl", ""),
                "posted": fmt_ts(p.get("publishedAt")),
                "_description": p.get("descriptionPlain", ""),
            }

        return jobs, False
