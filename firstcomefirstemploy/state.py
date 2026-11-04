"""Saved job state per monitor and change detection."""

from .config import SANITY_MIN_RATIO, STATE_DIR
from .utils.storage import load_json, save_json
from .utils.text import safe_filename

COMPARE_FIELDS = ("title", "location", "url")


def _state_file(name):
    return STATE_DIR / (safe_filename(name) + ".json")


def load_previous_jobs(name):
    path = _state_file(name)
    return load_json(path) if path.exists() else None


def save_jobs(name, jobs):
    save_json(_state_file(name), jobs)


def looks_suspicious(previous, current):
    """True if the job count collapsed (likely a scrape/API failure)."""
    if not previous:
        return False

    return (
        (len(previous) >= 10 and len(current) < len(previous) * SANITY_MIN_RATIO)
        or (len(previous) >= 3 and not current)
    )


def _key(job):
    return tuple(job.get(field) for field in COMPARE_FIELDS)


def detect_changes(previous, current):
    previous_ids = set(previous)
    current_ids = set(current)

    return {
        "added": {
            jid: current[jid] for jid in sorted(current_ids - previous_ids)
        },
        "removed": {
            jid: previous[jid] for jid in sorted(previous_ids - current_ids)
        },
        "modified": {
            jid: {"old": previous[jid], "new": current[jid]}
            for jid in sorted(previous_ids & current_ids)
            if _key(previous[jid]) != _key(current[jid])
        },
    }


def count_changes(changes):
    return sum(len(value) for value in changes.values())
