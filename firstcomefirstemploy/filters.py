"""Job filtering shared by every platform (country, title, keyword, description)."""

from functools import lru_cache

from .presets import COUNTRY_ALIASES, TITLE_PRESETS
from .utils.text import clean_description, normalize


# ------------------------------------------------------------
# Search-flag helpers
# ------------------------------------------------------------

def is_audit(search):
    return bool(search.get("_audit_mode"))


def description_filters_requested(search):
    """True when job descriptions must be fetched for this search."""
    return bool(search.get("description_exclude")) or is_audit(search)


def needs_description_text(search):
    """True when filtering reads job descriptions (exclusions or search_text)."""
    return description_filters_requested(search) or bool(search.get("search_text"))


def primary_country(search):
    value = search.get("country")

    if isinstance(value, list):
        value = value[0] if value else ""

    return normalize(value)


@lru_cache(maxsize=4096)
def _normalized_term(term):
    """Filter terms repeat for every job, so normalize each one only once."""
    return normalize(term)


def _pad(normalized_text):
    return f" {normalized_text} "


def _in_padded(padded_text, term):
    t = _normalized_term(term)
    return bool(t) and f" {t} " in padded_text


def contains_term(normalized_text, term):
    return _in_padded(_pad(normalized_text), term)


# ------------------------------------------------------------
# Country / keyword
# ------------------------------------------------------------

def matches_country(location, country):
    if not country:
        return True

    countries = country if isinstance(country, list) else [country]
    padded = f" {normalize(location)} "

    for item in countries:
        key = normalize(item)
        aliases = COUNTRY_ALIASES.get(key, [key])

        if any(f" {normalize(alias)} " in padded for alias in aliases):
            return True

    return False


def matches_search(title, description, search):
    text = normalize(title + " " + description)
    keyword = normalize(search.get("search_text", ""))
    family = normalize(search.get("job_family", ""))

    if keyword and not all(word in text for word in keyword.split()):
        return False

    if family and family not in normalize(title):
        return False

    return True


# ------------------------------------------------------------
# Title filters
# ------------------------------------------------------------

def title_terms(search):
    preset = TITLE_PRESETS.get(search.get("title_preset"), {})

    any_terms = list(search.get("title_any") or []) + preset.get("any", [])
    exclude = list(search.get("title_exclude") or []) + preset.get("exclude", [])

    return any_terms, exclude


def prefilter_signature(search):
    """Hashable identity of the checks passes_prefilters() applies."""
    any_terms, exclude = title_terms(search)
    country = search.get("country") or ""
    countries = country if isinstance(country, list) else [country]

    return (tuple(any_terms), tuple(exclude), tuple(str(c) for c in countries))


def matches_title_filters(title, search):
    any_terms, exclude = title_terms(search)
    padded = _pad(normalize(title))

    if any_terms and not any(_in_padded(padded, t) for t in any_terms):
        return False

    return not any(_in_padded(padded, t) for t in exclude)


def get_title_filter_reasons(title, search):
    any_terms, exclude_terms = title_terms(search)
    normalized = normalize(title)

    reasons = []
    rules = []

    if any_terms and not any(contains_term(normalized, t) for t in any_terms):
        reasons.append("title_any_no_match")
        rules.append("Required title terms: " + "; ".join(any_terms))

    matched = [t for t in exclude_terms if contains_term(normalized, t)]

    if matched:
        reasons.append("title_exclude_match")
        rules.append("Excluded title terms: " + "; ".join(matched))

    return reasons, rules


# ------------------------------------------------------------
# Description filters
# ------------------------------------------------------------

def matches_description_filters(description, search):
    padded = _pad(normalize(description))

    return not any(
        _in_padded(padded, term)
        for term in search.get("description_exclude", [])
    )


def get_description_filter_matches(description, search):
    """Return (matched exclusion terms, 'available'|'unavailable').

    A missing description is never treated as a rejection.
    """
    description = clean_description(description)

    if not description.strip():
        return [], "unavailable"

    padded = _pad(normalize(description))

    matched = [
        term for term in search.get("description_exclude", [])
        if _in_padded(padded, term)
    ]

    return matched, "available"


# ------------------------------------------------------------
# Pipeline applied to raw platform results
# ------------------------------------------------------------

def passes_prefilters(job, search, server_country=False):
    """Country + title checks that need no description.

    Platforms call this before paying for a per-job description request: the
    same checks run again in apply_filters, so a job that fails here would be
    dropped anyway and its description is never used.
    """
    if not server_country and not matches_country(
        job.get("location", ""), search.get("country")
    ):
        return False

    return matches_title_filters(job.get("title", ""), search)


def strip_description(job):
    return {k: v for k, v in job.items() if k != "_description"}


def apply_filters(raw_jobs, search, server_country=False):
    """Filter raw jobs ({id: {title, location, url, posted, _description}}).

    In audit mode description exclusions are skipped and `_description` is
    kept so the audit command can report exclusion reasons itself.
    """
    audit = is_audit(search)
    jobs = {}

    for jid, job in raw_jobs.items():
        title = job.get("title", "")
        description = clean_description(job.get("_description", ""))

        if not server_country and not matches_country(
            job.get("location", ""), search.get("country")
        ):
            continue

        if not matches_title_filters(title, search):
            continue

        if not matches_search(title, description, search):
            continue

        if (
            not audit
            and description.strip()
            and not matches_description_filters(description, search)
        ):
            continue

        jobs[jid] = (
            {**job, "_description": description}
            if audit else strip_description(job)
        )

    return jobs