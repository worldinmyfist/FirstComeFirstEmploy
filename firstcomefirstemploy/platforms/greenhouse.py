"""Greenhouse boards (boards-api.greenhouse.io)."""

from ..utils.http import request_json
from .base import Platform, slug_from_company
from .registry import register

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def _job(posting, **extra):
    return {
        "title": posting.get("title", ""),
        "location": (posting.get("location") or {}).get("name", ""),
        "url": posting.get("absolute_url", ""),
        **extra,
    }


@register
class Greenhouse(Platform):
    name = "greenhouse"
    bootstrap_url = "https://boards.greenhouse.io/{slug}"

    def detect(self, host, path):
        return "greenhouse.io" in host or (
            host.endswith("databricks.com")
            and path.startswith("/company/careers/")
        )

    def discover(self, session, company):
        board = slug_from_company(company)
        api_url = API.format(board=board)
        data = request_json(session, api_url, params={"content": "false"})

        return {
            "board_token": board,
            "api_url": api_url,
            "total_postings": len(data.get("jobs", [])),
        }

    def describe(self, metadata):
        print("Board token:", metadata["board_token"])
        print("Postings:", metadata["total_postings"])

    def fetch_raw(self, session, company, metadata, search):
        data = request_json(
            session, metadata["api_url"], params={"content": "true"}
        )

        jobs = {}

        for posting in data.get("jobs", []):
            if posting.get("id") is None:
                continue

            jobs[str(posting["id"])] = _job(
                posting,
                posted=posting.get("updated_at", ""),
                _description=posting.get("content", ""),
            )

        return jobs, False

    def probe(self, session, slug):
        # Cheap listing (no job content) is enough to confirm a board.
        try:
            data = request_json(
                session, API.format(board=slug), params={"content": "false"}
            )
        except Exception:
            return None

        jobs = {str(p.get("id")): _job(p) for p in data.get("jobs", [])}
        return jobs or None
