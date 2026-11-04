"""Per-company discovery metadata, cached on disk."""

import calendar
import time

from .config import DISCOVERY_DIR, DISCOVERY_REFRESH_DAYS, DISCOVERY_SCHEMA
from .platforms import detect_platform, get_platform
from .utils.storage import load_json, save_json
from .utils.text import safe_filename

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def discovery_path(company):
    return DISCOVERY_DIR / (safe_filename(company["name"]) + ".json")


def _is_fresh(metadata, company, platform):
    if (
        metadata.get("schema") != DISCOVERY_SCHEMA
        or metadata.get("career_url") != company["career_url"]
        or metadata.get("platform") != platform
        or not metadata.get("discovered_at")
    ):
        return False

    try:
        discovered = calendar.timegm(
            time.strptime(metadata["discovered_at"], _TIME_FORMAT)
        )
    except ValueError:
        return False

    return time.time() - discovered < DISCOVERY_REFRESH_DAYS * 86400


def discover_company(session, company, force=False):
    path = discovery_path(company)
    platform = detect_platform(company)

    if path.exists() and not force:
        cached = load_json(path)

        if _is_fresh(cached, company, platform):
            print("Using cached discovery:", company["name"])
            return cached

    print(f"\nDiscovering {company['name']} using {platform}")

    metadata = {
        "company": company["name"],
        "career_url": company["career_url"],
        "platform": platform,
        "schema": DISCOVERY_SCHEMA,
        "discovered_at": time.strftime(_TIME_FORMAT, time.gmtime()),
    }

    metadata.update(get_platform(platform).discover(session, company))

    save_json(path, metadata)
    print("Discovery saved:", path)

    return metadata
