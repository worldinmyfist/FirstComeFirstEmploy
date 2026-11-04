"""Best-effort description extraction from public job-detail pages."""

import html
import json
import re

from .http import request_text
from .text import clean_description

_JSON_LD = re.compile(
    r"(?is)<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>"
)
_META_DESC = re.compile(
    r"(?is)<meta[^>]+(?:name|property)=[\"'](?:description|og:description)[\"']"
    r"[^>]+content=[\"'](.*?)[\"']"
)


def extract_page_description(session, url):
    if not url:
        return ""

    try:
        page = request_text(session, url)
    except Exception as exc:
        print(f"  Could not fetch job detail page: {exc}")
        return ""

    # Prefer JSON-LD JobPosting.description where the site exposes it.
    for match in _JSON_LD.finditer(page):
        try:
            payload = json.loads(html.unescape(match.group(1)).strip())
        except (TypeError, ValueError):
            continue

        objects = payload if isinstance(payload, list) else [payload]

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            graph = obj.get("@graph") or []
            candidates = [obj] + [x for x in graph if isinstance(x, dict)]

            for candidate in candidates:
                kind = candidate.get("@type", [])
                if isinstance(kind, str):
                    kind = [kind]
                if "JobPosting" in kind and candidate.get("description"):
                    return clean_description(candidate["description"])

    # Meta description only; arbitrary page text is not a JD.
    meta = _META_DESC.search(page)
    return clean_description(meta.group(1)) if meta else ""
