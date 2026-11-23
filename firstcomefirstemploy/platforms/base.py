"""Base class every job-portal integration extends."""

import threading
import time
from urllib.parse import urlparse

from ..filters import (
    apply_filters,
    description_filters_requested,
    prefilter_signature,
)
from ..utils.text import normalize

_RAW_CACHE = {}
_RAW_CACHE_LOCK = threading.Lock()


def slug_from_company(company):
    """Board slug from `slug`/`board_token`, else the first URL path segment."""
    for key in ("slug", "board_token"):
        if company.get(key):
            return company[key]

    parts = [p for p in urlparse(company["career_url"]).path.split("/") if p]

    if not parts:
        raise ValueError(
            "Board slug missing. Set 'slug' in companies.json."
        )

    return parts[0]


class Platform:
    """A job portal. Subclass, set `name`, implement the hooks, decorate
    with @register (see platforms/registry.py).

    Hooks:
      detect(host, path)  -> True if a career URL belongs to this platform
      discover(...)       -> dict of metadata cached on disk (API urls, ids...)
      fetch_raw(...)      -> ({job_id: {title, location, url, posted,
                              _description}}, server_filtered_country)
    Override `fetch` instead of `fetch_raw` if the portal filters server-side
    and cannot return a reusable raw listing (see workday.py).
    """

    name = ""
    # True if fetch_raw makes one request per job to get its description.
    # Such platforms skip jobs the country/title filters reject (see
    # filters.passes_prefilters), so searches with different rules must not
    # share one raw download.
    per_job_descriptions = False
    # e.g. "https://jobs.lever.co/{slug}" -> platform can be auto-found by
    # `--bootstrap`; leave None otherwise.
    bootstrap_url = None
    # Probe order in `--bootstrap` (lower first). Expensive guessers go last.
    bootstrap_order = 50
    # Single-company portals (google, amazon...): normalized company names
    # served by this platform + the career URL to configure for them.
    bootstrap_names = ()
    bootstrap_career_url = None
    # False for portals where a live check means downloading a huge listing;
    # a name match is then accepted without one.
    bootstrap_validate = True

    # ---- hooks ------------------------------------------------

    def detect(self, host, path):
        return False

    def discover(self, session, company):
        return {}

    def fetch_raw(self, session, company, metadata, search):
        raise NotImplementedError(f"{self.name}: fetch_raw not implemented")

    def cache_key(self, metadata, search):
        """Searches with equal keys share one raw download per run."""
        return (self.name, metadata.get("api_url"))

    def describe(self, metadata):
        """Printed by `--discover`."""
        print("API:", metadata.get("api_url"))

        if "total_postings" in metadata:
            print("Postings:", metadata["total_postings"])

    # ---- shared behaviour -------------------------------------

    def fetch(self, session, company, metadata, search):
        raw, server_country = self.load_raw(session, company, metadata, search)
        return apply_filters(raw, search, server_country)

    def _raw_key(self, metadata, search):
        key = self.cache_key(metadata, search)

        if self.per_job_descriptions and description_filters_requested(search):
            return (key, prefilter_signature(search))

        return key

    def load_raw(self, session, company, metadata, search):
        key = self._raw_key(metadata, search)

        with _RAW_CACHE_LOCK:
            cached = _RAW_CACHE.get(key)

        if cached is not None:
            return cached

        fetched = self.fetch_raw(session, company, metadata, search)

        with _RAW_CACHE_LOCK:
            return _RAW_CACHE.setdefault(key, fetched)

    def validated(self, session, company, meta):
        """Fetch the full board once to prove the config works."""
        raw, _ = self.load_raw(
            session, company, dict(meta, platform=self.name), {}
        )
        return dict(meta, total_postings=len(raw))

    # ---- --bootstrap hooks ------------------------------------

    def known_board(self, name):
        """Company dict if `name` is the company this portal is built for."""
        if self.bootstrap_career_url and normalize(name) in self.bootstrap_names:
            return {"career_url": self.bootstrap_career_url}

        return None

    def bootstrap_candidates(self, slugs):
        """Company dicts to try for these guessed slugs (override to add
        variants, e.g. Lever's EU host)."""
        if not self.bootstrap_url:
            return []

        return [
            {"slug": s, "career_url": self.bootstrap_url.format(slug=s)}
            for s in slugs
        ]

    def probe_company(self, session, company):
        """Return {id: job} if `company` (career_url/slug/...) is a live
        public board on this platform, else None. Never raises."""
        company = dict(company)
        company.setdefault("name", company.get("slug") or company["career_url"])

        try:
            meta = dict(self.discover(session, company), platform=self.name)
            raw, _ = self.load_raw(session, company, meta, {})
        except Exception:
            return None

        return raw or None

    def verify(self, session, company):
        """Board dict {platform, company, jobs, total} or None."""
        if not self.bootstrap_validate:
            return {"platform": self.name, "company": dict(company),
                    "jobs": {}, "total": None, "live_checked": False}

        jobs = self.probe_company(session, company)

        if not jobs:
            return None

        return {"platform": self.name, "company": dict(company),
                "jobs": jobs, "total": len(jobs), "live_checked": True}

    def find_board(self, session, slugs, delay=0.0):
        """Try this platform for the guessed slugs; first live board wins."""
        for company in self.bootstrap_candidates(slugs):
            board = self.verify(session, company)

            if delay:
                time.sleep(delay)

            if board:
                return board

        return None

    def probe(self, session, slug):
        """Back-compat: {id: job} for a slug, else None."""
        return self.probe_company(session, {
            "slug": slug,
            "career_url": self.bootstrap_url.format(slug=slug),
        })
