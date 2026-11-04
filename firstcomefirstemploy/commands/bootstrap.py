"""--bootstrap CSV / --merge FILE: find public job boards for many companies."""

import csv
import time
from pathlib import Path

from ..config import (
    CONFIG_FILE,
    GENERATED_FILE,
    POLITE_DELAY,
    UNRESOLVED_FILE,
)
from ..filters import matches_country, matches_title_filters
from ..platforms import get_platform
from ..utils.http import create_session
from ..utils.storage import load_json, save_json
from ..utils.text import normalize

# Probe order when guessing which platform a company uses.
BOOTSTRAP_PLATFORMS = ("greenhouse", "lever", "ashby", "smartrecruiters")

# normalized company name -> (platform, slug)
KNOWN_BOARDS = {
    "glean": ("greenhouse", "gleanwork"),
    "razorpay": ("greenhouse", "razorpaysoftwareprivatelimited"),
    "mistral ai": ("lever", "mistral"),
    "openai": ("ashby", "openai"),
    "anthropic": ("greenhouse", "anthropic"),
    "databricks": ("greenhouse", "databricks"),
    "elevenlabs": ("ashby", "elevenlabs"),
    "cohere": ("ashby", "cohere"),
    "stripe": ("greenhouse", "stripe"),
}

SKIP_NAMES = {"stealth co"}

_NOISE_WORDS = {"ai", "labs", "capital", "technologies", "inc", "hq"}

_INDIA = {
    "blr", "hyd", "india", "bangalore", "bengaluru",
    "hyderabad", "pune", "mumbai", "delhi", "noida",
}


def candidate_slugs(name):
    words = normalize(name).split()
    found = []

    def add(value):
        if value and value not in found:
            found.append(value)

    add("".join(words))
    add("-".join(words))

    if len(words) > 1:
        add(words[0])

    trimmed = [w for w in words if w not in _NOISE_WORDS]

    if trimmed and trimmed != words:
        add("".join(trimmed))
        add("-".join(trimmed))

    return found[:5]


def countries_from_location(text):
    tokens = set(normalize(text).split())
    countries = []

    if tokens & _INDIA:
        countries.append("india")

    if tokens & {"sgp", "singapore"}:
        countries.append("singapore")

    if tokens & {"london", "uk"}:
        countries.append("united kingdom")

    if "remote" in tokens:
        countries.append("remote")

    return countries or ["india"]


def _find_board(session, name):
    attempts = []
    key = normalize(name)

    if key in KNOWN_BOARDS:
        attempts.append(KNOWN_BOARDS[key])

    for slug in candidate_slugs(name):
        for platform in BOOTSTRAP_PLATFORMS:
            attempts.append((platform, slug))

    for platform, slug in attempts:
        jobs = get_platform(platform).probe(session, slug)
        time.sleep(POLITE_DELAY)

        if jobs:
            return platform, slug, jobs

    return None


def run_bootstrap(csv_path):
    with open(Path(csv_path), newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    existing = set()

    if CONFIG_FILE.exists():
        existing = {
            normalize(c["name"])
            for c in load_json(CONFIG_FILE).get("companies", [])
        }

    session = create_session()
    generated = []
    unresolved = []

    try:
        for row in rows:
            name = (row.get("Name") or "").strip()
            location = (row.get("Location") or "").strip()
            key = normalize(name)

            if not name or key in SKIP_NAMES:
                continue

            if "/" in name:
                print(f"SKIP (looks like a location): {name}")
                continue

            if key in existing:
                print(f"SKIP (already configured): {name}")
                continue

            match = _find_board(session, name)

            if not match:
                print(f"NOT FOUND: {name}")
                unresolved.append({
                    "name": name,
                    "location": location,
                    "next_step": (
                        "Open the company careers page and "
                        "check its URL / network calls"
                    ),
                })
                continue

            platform, slug, jobs = match
            countries = countries_from_location(location)

            region = [
                j for j in jobs.values()
                if matches_country(j.get("location", ""), countries)
            ]

            region_titles = [
                j for j in region
                if matches_title_filters(
                    j.get("title", ""), {"title_preset": "tech_roles"}
                )
            ]

            sample = region_titles[0]["title"] if region_titles else "-"

            print(
                f"FOUND: {name} -> {platform}:{slug} | total={len(jobs)} "
                f"in-region={len(region)} matching-roles={len(region_titles)} "
                f"| e.g. {sample}"
            )

            generated.append({
                "name": name,
                "career_url": get_platform(platform).bootstrap_url.format(slug=slug),
                "platform": platform,
                "slug": slug,
                "enabled": True,
                "csv_location": location,
                "searches": [{
                    "name": "Target roles",
                    "country": countries,
                    "title_preset": "tech_roles",
                }],
            })
    finally:
        session.close()

    save_json(GENERATED_FILE, {"companies": generated})

    with open(UNRESOLVED_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "location", "next_step"])
        writer.writeheader()
        writer.writerows(unresolved)

    print(f"\nWrote {len(generated)} companies to {GENERATED_FILE.name}")
    print(f"{len(unresolved)} not found -> {UNRESOLVED_FILE.name}")
    print(
        "\nReview the FOUND lines above (a slug can belong to a different "
        "company with the same name), delete wrong entries, then run:\n"
        f"  python FirstComeFirstEmploy.py --merge {GENERATED_FILE.name}"
    )


def run_merge(source_path):
    incoming = load_json(Path(source_path)).get("companies", [])

    if CONFIG_FILE.exists():
        current = load_json(CONFIG_FILE)
        save_json(CONFIG_FILE.with_suffix(".json.bak"), current)
    else:
        current = {"companies": []}

    existing = {normalize(c["name"]) for c in current["companies"]}
    added = []

    for company in incoming:
        key = normalize(company["name"])

        if key in existing:
            print("Skipped (exists):", company["name"])
            continue

        current["companies"].append(company)
        existing.add(key)
        added.append(company["name"])

    save_json(CONFIG_FILE, current)

    print(f"Added {len(added)}:", ", ".join(added) or "-")
    print("Backup saved as companies.json.bak")
