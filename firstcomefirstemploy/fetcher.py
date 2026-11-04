"""Single entry point to fetch + filter jobs for any platform."""

from .filters import is_audit, matches_title_filters, strip_description
from .platforms import get_platform


def fetch_jobs(session, company, metadata, search):
    """Return {job_id: job} for one search of one company."""
    platform = get_platform(metadata["platform"])
    jobs = platform.fetch(session, company, metadata, search)
    audit = is_audit(search)

    # Title filters apply to every platform. Never let `_description`
    # leak into saved state outside audit mode.
    return {
        jid: job if audit else strip_description(job)
        for jid, job in jobs.items()
        if matches_title_filters(job.get("title", ""), search)
    }
