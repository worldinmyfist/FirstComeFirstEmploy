
"""D. E. Shaw India careers: listing plus full job descriptions."""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..filters import description_filters_requested, passes_prefilters
from ..utils.http import request_text
from ..utils.parallel import map_concurrent
from ..utils.text import clean_description
from .base import Platform
from .registry import register

BASE = "https://www.deshawindia.com"
LISTING_URL = BASE + "/careers"
DETAIL_WORKERS = 4

_JOB_LINK = re.compile(
    r'<a\b[^>]*?href="([^"]*?/careers/[a-z0-9][a-z0-9\-]*-(\d+)/?)"[^>]*>(.*?)</a>',
    re.I | re.S,
)

_CATEGORIES = sorted([
    "Quality and Test Engineering",
    "Project Management Office",
    "Financial Operations",
    "Financial Research",
    "Product Management",
    "Human Capital",
    "Administration",
    "Core Tech",
    "Systems",
    "Legal",
], key=len, reverse=True)

_CITIES = {
    "HYD": "Hyderabad",
    "BLR": "Bengaluru",
    "GGM": "Gurugram",
}

_LOCS = re.compile(
    r"^((?:HYD|BLR|GGM)(?:\s*/\s*(?:HYD|BLR|GGM))*)\b"
)


class _JobText(HTMLParser):
    """Extract visible text starting at the job heading."""

    SKIP = {"script", "style", "noscript", "svg", "template"}
    BREAKS = {
        "p", "li", "h1", "h2", "h3", "h4",
        "div", "section", "br",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = []
        self.started = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in self.SKIP:
            self.skip.append(tag)

        if self.skip:
            return

        if tag == "h1":
            self.started = True

        if self.started and tag in self.BREAKS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in self.SKIP and tag in self.skip:
            self.skip.remove(tag)
            return

        if self.skip:
            return

        if self.started and tag in self.BREAKS:
            self.parts.append(" ")

    def handle_data(self, data):
        if self.started and not self.skip:
            self.parts.append(data)


def _parse_card(text):
    text = " ".join(text.split())
    category = ""

    for name in _CATEGORIES:
        if text.lower().startswith(name.lower()):
            category = name
            text = text[len(name):].strip()
            break

    codes = []
    match = _LOCS.match(text)

    if match:
        codes = [c.strip() for c in match.group(1).split("/")]
        text = text[match.end():].strip()

    text = re.sub(r"^icon\b\s*", "", text, flags=re.I)
    title, _, blurb = text.partition(" : ")

    location = "; ".join(
        f"{_CITIES[c]}, India" for c in codes
    )

    return category, location, title.strip(), blurb.strip()


def _detail_description(session, url):
    """Fetch full job page, excluding legal/footer text."""

    parser = _JobText()
    parser.feed(request_text(session, url))

    full = clean_description(" ".join(parser.parts))

    for marker in (
        "The members of the D. E. Shaw group do not discriminate",
        "Candidates who are residents of California",
        "CCPA Recruitment Personal Information Privacy Notice",
    ):
        pos = full.lower().find(marker.lower())
        if pos >= 0:
            full = full[:pos].strip()

    if len(full) < 100:
        raise RuntimeError(
            "D. E. Shaw: detail description missing or too short: "
            + url
        )

    return full


@register
class DEShaw(Platform):
    name = "deshaw"
    per_job_descriptions = True

    def detect(self, host, path):
        return host.endswith("deshawindia.com")

    def discover(self, session, company):
        return self.validated(session, company, {
            "api_url": company.get("api_url") or LISTING_URL,
        })

    def cache_key(self, metadata, search):
        return (
            self.name,
            metadata["api_url"],
            description_filters_requested(search),
        )

    def fetch_raw(self, session, company, metadata, search):
        page = request_text(session, metadata["api_url"])
        jobs = {}

        for href, jid, inner in _JOB_LINK.findall(page):
            if jid in jobs:
                continue

            category, location, title, blurb = _parse_card(
                clean_description(inner)
            )

            if not title:
                continue

            jobs[jid] = {
                "title": title,
                "location": location,
                "url": urljoin(BASE, href),
                "posted": "",
                "_description": f"{category}. {blurb}".strip(". "),
            }

        if not jobs:
            raise RuntimeError(
                "D. E. Shaw: no job links parsed; "
                "page layout may have changed."
            )

        if description_filters_requested(search):
            wanted = [
                jid for jid, job in jobs.items()
                if passes_prefilters(
                    job, search, server_country=False
                )
            ]

            print(
                f"  D. E. Shaw: fetching {len(wanted)} "
                f"full descriptions with {DETAIL_WORKERS} workers"
            )

            texts = map_concurrent(
                lambda jid: _detail_description(
                    session, jobs[jid]["url"]
                ),
                wanted,
                workers=DETAIL_WORKERS,
            )

            for jid, text in zip(wanted, texts):
                jobs[jid]["_description"] = text

        return jobs, False
