"""--bootstrap CSV / --merge FILE: find public job boards for many companies.

Resolution order for each company (first live hit wins):
  1. KNOWN_BOARDS, then portals built for that one company (google, amazon...)
  2. guessed slugs on every slug-based platform (greenhouse, lever incl. EU,
     ashby, smartrecruiters, rippling, turbohire ...)
  3. Workday: guessed tenant x wdN pod x site (slow, so after step 2)
  4. optional LLM: asks which platform/URL the company uses, then re-checks
     that URL with one of OUR platform integrations
  5. otherwise: companies.unresolved.csv (with the LLM's hint, if any)
"""

import csv
import threading
import time
from pathlib import Path

from ..config import (
    BOOTSTRAP_WORKERS,
    CONFIG_FILE,
    GENERATED_FILE,
    POLITE_DELAY,
    UNRESOLVED_FILE,
)
from ..filters import matches_country, matches_title_filters
from ..platforms import detect_platform, get_platform
from ..platforms.base import Platform
from ..platforms.registry import _REGISTRY
from ..utils.http import create_session
from ..utils.llm import ask_board, clean, llm_available
from ..utils.parallel import map_concurrent
from ..utils.storage import load_json, save_json
from ..utils.text import normalize

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

# A board this small is probably another company's (or a stub): keep looking
# on the remaining platforms before accepting it.
MIN_CONFIDENT_JOBS = 5

_PRINT_LOCK = threading.Lock()

# Keys of a candidate company dict that belong in companies.json.
_ENTRY_KEYS = ("career_url", "slug", "region", "tenant", "site", "org_id")


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


def _guessing_platforms():
    """Registered platforms that can find a board from a name alone, cheapest
    first. A new platform file with `bootstrap_url` joins automatically."""
    platforms = [
        p for p in _REGISTRY.values()
        if p.bootstrap_url or type(p).find_board is not Platform.find_board
    ]
    return sorted(platforms, key=lambda p: (p.bootstrap_order, p.name))


# ------------------------------------------------------------
# Resolution
# ------------------------------------------------------------

def _tag(board, how):
    board["found_via"] = how
    return board


def _resolve_by_name(session, name):
    key = normalize(name)

    if key in KNOWN_BOARDS:
        platform_name, slug = KNOWN_BOARDS[key]
        platform = get_platform(platform_name)

        for company in platform.bootstrap_candidates([slug]):
            board = platform.verify(session, company)

            if board:
                return _tag(board, "known-board")

    for platform in _REGISTRY.values():
        company = platform.known_board(name)

        if company:
            board = platform.verify(session, company)

            if board:
                return _tag(board, "known-portal")

    return None


def _resolve_by_guessing(session, name, slugs):
    words = normalize(name).split()
    platforms = _guessing_platforms()
    cheap = [p for p in platforms if p.bootstrap_order < 90]
    slow = [p for p in platforms if p.bootstrap_order >= 90]

    best = None  # biggest tiny-board seen so far: (total, board)

    # slug-major: every cheap platform for slug 1, then for slug 2 ...
    for slug in slugs:
        for platform in cheap:
            board = platform.find_board(session, [slug], delay=POLITE_DELAY)

            if not board:
                continue

            total = board.get("total")

            if total is None or total >= MIN_CONFIDENT_JOBS:
                weak = len(words) > 1 and slug == words[0]
                return _tag(board, "probe:first-word-slug" if weak else "probe")

            if best is None or total > best[0]:
                best = (total, board)

    if best:
        return _tag(best[1], "probe:small-board")

    for platform in slow:
        board = platform.find_board(session, slugs, delay=POLITE_DELAY)

        if board:
            return _tag(board, "probe")

    return None


def _resolve_by_llm(session, name, location):
    """(board | None, hint dict). Never trusts the LLM: every URL it gives
    must be accepted by one of our platform integrations."""
    answer = ask_board(name, location)
    hint = {
        "llm_platform": clean(answer.get("platform")),
        "llm_career_url": clean(answer.get("career_url")),
        "llm_notes": clean(answer.get("error") or answer.get("notes")),
    }

    if answer.get("error"):
        return None, hint

    url = hint["llm_career_url"]
    slug = clean(answer.get("slug"))
    guess = hint["llm_platform"].lower()
    candidates = []  # (platform name, company dict)

    if url:
        try:
            detected = detect_platform({"career_url": url})
        except ValueError:
            detected = None

        if detected:
            company = {"career_url": url}

            if slug and get_platform(detected).bootstrap_url:
                company["slug"] = slug

            candidates.append((detected, company))

    if slug and guess in _REGISTRY and _REGISTRY[guess].bootstrap_url:
        for company in _REGISTRY[guess].bootstrap_candidates([slug]):
            candidates.append((guess, company))

    seen = set()

    for platform_name, company in candidates:
        if company["career_url"] in seen:
            continue

        seen.add(company["career_url"])
        board = get_platform(platform_name).verify(session, company)
        time.sleep(POLITE_DELAY)

        if board:
            hint["llm_platform"] = platform_name
            return _tag(board, "llm"), hint

    return None, hint


def resolve_company(session, name, location, use_llm=True):
    """-> (board | None, info dict for the unresolved CSV)."""
    slugs = candidate_slugs(name)
    info = {"tried_slugs": " ".join(slugs)}

    board = _resolve_by_name(session, name) or _resolve_by_guessing(
        session, name, slugs
    )

    if board or not use_llm:
        return board, info

    board, hint = _resolve_by_llm(session, name, location)
    info.update(hint)
    return board, info


# ------------------------------------------------------------
# Command
# ------------------------------------------------------------

def _entry(name, location, countries, board):
    company = board["company"]
    entry = {"name": name}

    for key in _ENTRY_KEYS:
        if company.get(key):
            entry[key] = company[key]

    entry.update({
        "platform": board["platform"],
        "enabled": True,
        "csv_location": location,
        "found_via": board["found_via"],
        "searches": [{
            "name": "Target roles",
            "country": countries,
            "title_preset": "tech_roles",
        }],
    })

    return entry


def _found_line(name, board, countries):
    where = f"{board['platform']}:{board['company'].get('slug') or board['company']['career_url']}"
    via = board["found_via"]
    weak = " | WEAK match, verify" if via.endswith(("first-word-slug", "small-board")) else ""

    if not board.get("live_checked", True) or not board["jobs"]:
        return (
            f"FOUND [{via}]: {name} -> {where} "
            f"| not live-checked, run --probe after merge{weak}"
        )

    jobs = board["jobs"]
    region = [
        j for j in jobs.values()
        if matches_country(j.get("location", ""), countries)
    ]
    titles = [
        j for j in region
        if matches_title_filters(j.get("title", ""), {"title_preset": "tech_roles"})
    ]
    sample = titles[0]["title"] if titles else "-"
    scope = "first-page sample" if board.get("sampled") else "all"

    return (
        f"FOUND [{via}]: {name} -> {where} | total={board['total']} "
        f"({scope}: in-region={len(region)} matching-roles={len(titles)}) "
        f"| e.g. {sample}{weak}"
    )


def run_bootstrap(csv_path, use_llm=True):
    with open(Path(csv_path), newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    existing = set()

    if CONFIG_FILE.exists():
        existing = {
            normalize(c["name"])
            for c in load_json(CONFIG_FILE).get("companies", [])
        }

    if use_llm and not llm_available():
        print("LLM fallback off: set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env (and check BOOTSTRAP_LLM_PROVIDER).\n")
        use_llm = False

    todo = []
    seen = set()

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

        if key in seen:
            print(f"SKIP (duplicate row): {name}")
            continue

        seen.add(key)
        todo.append((name, location))

    session = create_session()

    def work(item):
        name, location = item

        try:
            board, info = resolve_company(session, name, location, use_llm)
        except Exception as exc:  # one bad company must not kill the run
            board, info = None, {"llm_notes": f"error: {exc}"[:200]}

        countries = countries_from_location(location)

        with _PRINT_LOCK:
            if board:
                print(_found_line(name, board, countries), flush=True)
            else:
                print(f"NOT FOUND: {name}", flush=True)

        return board, info

    try:
        results = map_concurrent(work, todo, workers=BOOTSTRAP_WORKERS)
    finally:
        session.close()

    generated, unresolved = [], []

    for (name, location), (board, info) in zip(todo, results):
        if board:
            generated.append(
                _entry(name, location, countries_from_location(location), board)
            )
            continue

        unresolved.append({
            "name": name,
            "location": location,
            "tried_slugs": info.get("tried_slugs", ""),
            "llm_platform": info.get("llm_platform", ""),
            "llm_career_url": info.get("llm_career_url", ""),
            "llm_notes": info.get("llm_notes", ""),
            "next_step": (
                "Open the careers page and check its URL / network calls; "
                "if it is a platform we lack, add platforms/<name>.py"
            ),
        })

    save_json(GENERATED_FILE, {"companies": generated})

    with open(UNRESOLVED_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "name", "location", "tried_slugs", "llm_platform",
            "llm_career_url", "llm_notes", "next_step",
        ])
        writer.writeheader()
        writer.writerows(unresolved)

    by_how = {}

    for entry in generated:
        by_how[entry["found_via"]] = by_how.get(entry["found_via"], 0) + 1

    print(f"\nWrote {len(generated)} companies to {GENERATED_FILE.name}", by_how or "")
    print(f"{len(unresolved)} not found -> {UNRESOLVED_FILE.name}")
    print(
        "\nReview the FOUND lines above (a slug can belong to a different "
        "company with the same name; 'llm' and WEAK ones most of all), "
        "delete wrong entries, then run:\n"
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