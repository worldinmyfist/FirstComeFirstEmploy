"""Atlassian careers (public JSON listing of all openings)."""

from ..utils.http import request_json
from ..utils.text import clean_description, fmt_ts, join_locations
from .base import Platform
from .registry import register


@register
class Atlassian(Platform):
    name = "atlassian"

    def detect(self, host, path):
        return host.endswith("atlassian.com")

    def discover(self, session, company):
        return self.validated(session, company, {
            "api_url": company.get("api_url")
            or "https://www.atlassian.com/endpoint/careers/listings",
        })

    def fetch_raw(self, session, company, metadata, search):
        data = request_json(session, metadata["api_url"])

        if isinstance(data, dict):
            data = data.get("jobs") or data.get("listings") or []

        jobs = {}

        for p in data:
            if p.get("id") is None:
                continue

            jid = str(p["id"])

            jobs[jid] = {
                "title": (p.get("title") or "").strip(),
                "location": join_locations(p.get("locations")),
                "url": p.get("applyUrl")
                or f"https://www.atlassian.com/company/careers/details/{jid}",
                "posted": fmt_ts(
                    (p.get("portalJobPost") or {}).get("updatedDate")
                ),
                "_description": clean_description(
                    p.get("overview")
                    or p.get("description")
                    or p.get("jobDescription")
                    or ""
                ),
            }

        return jobs, False
