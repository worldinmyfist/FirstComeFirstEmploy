"""Lever (jobs.lever.co / jobs.eu.lever.co)."""

from urllib.parse import urlparse

from ..utils.http import request_json
from ..utils.text import fmt_ts, join_locations
from .base import Platform, slug_from_company
from .registry import register


@register
class Lever(Platform):
    name = "lever"
    bootstrap_url = "https://jobs.lever.co/{slug}"
    bootstrap_order = 20

    def bootstrap_candidates(self, slugs):
        # US first, then the EU region host, per slug.
        candidates = []

        for slug in slugs:
            candidates.append({
                "slug": slug, "career_url": f"https://jobs.lever.co/{slug}",
            })
            candidates.append({
                "slug": slug, "career_url": f"https://jobs.eu.lever.co/{slug}",
                "region": "eu",
            })

        return candidates

    def detect(self, host, path):
        return host.endswith("lever.co")

    def discover(self, session, company):
        slug = slug_from_company(company)
        host = urlparse(company["career_url"]).netloc.lower()
        eu = ".eu." in host or company.get("region") == "eu"
        base = "https://api.eu.lever.co" if eu else "https://api.lever.co"

        return self.validated(session, company, {
            "slug": slug,
            "api_url": f"{base}/v0/postings/{slug}",
        })

    def fetch_raw(self, session, company, metadata, search):
        data = request_json(
            session, metadata["api_url"], params={"mode": "json"}
        )

        if not isinstance(data, list):
            raise RuntimeError("Unexpected Lever response")

        jobs = {}

        for p in data:
            if not p.get("id"):
                continue

            cats = p.get("categories") or {}
            locations = cats.get("allLocations") or [cats.get("location")]

            jobs[str(p["id"])] = {
                "title": (p.get("text") or "").strip(),
                "location": join_locations(locations),
                "url": p.get("hostedUrl", ""),
                "posted": fmt_ts(p.get("createdAt")),
                "_description": p.get("descriptionPlain", ""),
            }

        return jobs, False
