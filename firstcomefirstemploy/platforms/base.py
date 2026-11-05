"""Base class every job-portal integration extends."""

import threading
from urllib.parse import urlparse

from ..filters import (
    apply_filters,
    description_filters_requested,
    prefilter_signature,
)

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

    def probe(self, session, slug):
        """Return {id: job} if a public board exists for `slug`, else None.
        Used by `--bootstrap`; needs `bootstrap_url`."""
        company = {
            "name": slug,
            "career_url": self.bootstrap_url.format(slug=slug),
            "slug": slug,
        }

        try:
            meta = dict(self.discover(session, company), platform=self.name)
            raw, _ = self.load_raw(session, company, meta, {})
        except Exception:
            return None

        return raw or None