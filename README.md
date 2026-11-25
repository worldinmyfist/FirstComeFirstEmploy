# FirstComeFirstEmploy

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/worldinmyfist/FirstComeFirstEmploy/blob/main/LICENSE) [![Python](https://img.shields.io/badge/python-3.14-blue.svg)](https://img.shields.io/badge/python-3.14-blue.svg)

**A job alert bot that watches company career pages and emails you the moment a matching role is added, removed, or changed.**

Stop refreshing a dozen career portals. Point FirstComeFirstEmploy at the companies you care about, describe the roles you want, and let it run on a schedule. It compares each run against the previous snapshot and emails you only when something actually changed.

## Contents
- [Highlights](#highlights)
- [Quickstart](#quickstart)
- [Email setup](#email-setup)
- [Configuration](#configuration)
- [Customizing presets](#customizing-presets)
- [Adding a company](#adding-a-company)
- [Bulk company bootstrap and LLM fallback](#bulk-company-bootstrap-and-llm-fallback)
- [Usage](#usage)
- [Running on a schedule](#running-on-a-schedule)
- [Data and state](#data-and-state)
- [How it works](#how-it-works)
- [Adding a new platform](#adding-a-new-platform)
- [Fetch caching and `cache_key`](#fetch-caching-and-cache_key)
- [Limitations and responsible use](#limitations-and-responsible-use)
- [License](#license)

## Highlights
- **One tool, many portals:** Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold, plus custom integrations for Amazon, Google, Atlassian, Oracle and LinkedIn.
- **Change detection, not just scraping:** alerts for added, removed and modified jobs, based on snapshots from the previous run.
- **Per-company searches:** each company can have several searches, each with its own title, location and description filters.
- **Auto-discovery:** detects the platform and required metadata from a career URL, and caches it.
- **Bulk bootstrap with optional LLM fallback:** discover boards from a CSV using platform probes, then Anthropic, OpenAI or Gemini for unresolved names; LLM suggestions must pass live ATS verification.
- **Debuggable filtering:** `--audit` shows why each job was kept or dropped; `--probe` tests live fetching; `--dry-run` previews without emailing or saving.
- **Easy to extend:** a new platform is one Python file with a `@register` decorator. No other files need to change.

## Quickstart

**Python 3.14 is required. This release was built using Python 3.14.8.**

Use the same release on macOS, Linux or Windows with Python 3.14.

```bash
# 1. Install
git clone https://github.com/worldinmyfist/FirstComeFirstEmploy.git
cd FirstComeFirstEmploy
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure email (see "Email setup" below) by creating a .env file

# 3. Add a company to companies.json (see "Configuration")

# 4. Check that discovery and fetching work
python notify.py --discover "Example Company"
python notify.py --probe "Example Company"

# 5. Dry run, then the real thing
python notify.py --dry-run
python notify.py
```

The first successful run for a company establishes a **baseline** and sends no alerts for jobs that already exist. You are only notified about changes after that.

On Windows, use `py -3.14 -m venv .venv` to create the environment and `.venv\Scripts\Activate.ps1` to activate it in PowerShell.

## Email setup

Create a `.env` file in the project root:

```dotenv
EMAIL_FROM=your_email@gmail.com
EMAIL_TO=recipient@example.com
EMAIL_PASSWORD=your_gmail_app_password

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) rather than your account password. App Passwords require 2-Step Verification to be enabled.

Verify delivery with:

```bash
python notify.py --test-email
```

## Configuration

Companies and searches live in `companies.json`. Each company has a platform and one or more searches. The file contains a top-level `companies` array:

```json
{
  "companies": [
    {
      "name": "Example Company",
      "career_url": "https://boards.greenhouse.io/example",
      "platform": "greenhouse",
      "enabled": true,
      "searches": [
        {
          "name": "India AI Roles",
          "country": ["india"],
          "title_preset": "tech_roles",
          "title_exclude": ["Senior Manager"],
          "description_exclude": ["Recruitment"]
        }
      ]
    }
  ]
}
```

### Search fields

| Field | Purpose |
|---|---|
| `country` | Country or list of countries to match using location aliases |
| `title_preset` | Optional reusable title rules from `TITLE_PRESETS` |
| `title_any` | Keep jobs whose title matches **any** of these keywords |
| `title_exclude` | Drop jobs whose title matches **any** of these keywords |
| `search_text` | Require all keyword words in the combined title and description |
| `job_family` | Match a job family term in the title |
| `description_exclude` | Drop jobs whose description matches **any** of these keywords |

Reusable title presets and location aliases are defined in `firstcomefirstemploy/presets.py`. Put company-specific keywords in `companies.json`.

Not every portal exposes job descriptions. A missing description does not by itself reject a job under `description_exclude`; use `--audit` to inspect availability and filtering.

## Customizing presets

Edit [`firstcomefirstemploy/presets.py`](firstcomefirstemploy/presets.py) to customize reusable search rules and company discovery. Changes take effect on the next run.

| Setting | Purpose |
|---|---|
| `TITLE_PRESETS` | Reusable title inclusion and exclusion rules |
| `COUNTRY_ALIASES` | Location aliases used to filter returned jobs |
| `BOOTSTRAP_COUNTRY_ALIASES` | Maps CSV locations to countries during bootstrap |
| `BOOTSTRAP_DEFAULT_COUNTRIES` | Fallback countries for blank or unrecognized CSV locations |
| `KNOWN_BOARDS` | Known company-to-platform and board identifier mappings |
| `SKIP_NAMES` | Company names ignored during bootstrap |
| `BOOTSTRAP_NOISE_WORDS` | Words removed when generating candidate board identifiers |
| `MIN_CONFIDENT_JOBS` | Confidence threshold for small career boards |
| `PLATFORM_URL_HINTS` | Career URL patterns used for optional LLM-assisted discovery |

### Title presets

The existing `tech_roles` preset includes software engineering, AI/ML, data science and quantitative roles, with exclusion rules for unwanted titles. Review these rules to suit your preferences.

Add a new preset after the existing dictionary:

```python
TITLE_PRESETS["finance_roles"] = {
    "any": ["financial analyst", "investment analyst", "portfolio analyst"],
    "exclude": ["intern", "internship", "manager"],
}
```

Select it in a search in `companies.json`:

```json
{
  "name": "Finance Roles",
  "country": ["india"],
  "title_preset": "finance_roles"
}
```

Matching uses normalized, case-insensitive whole words and phrases. The `any` list requires at least one match when nonempty; any matching exclusion rejects the title. Search-specific `title_any` and `title_exclude` are combined with the preset lists. If `title_preset` is omitted, no preset is applied. Newly bootstrapped searches use `tech_roles` by default; edit the generated searches if you prefer another preset or none.

### Job locations and bootstrap countries

`COUNTRY_ALIASES` matches locations returned by career sites. For instance, expand the existing India aliases:

```python
COUNTRY_ALIASES["india"].extend(["mumbai", "pune", "chennai", "delhi"])
```

`BOOTSTRAP_COUNTRY_ALIASES` interprets the location column in your bootstrap CSV. India, Singapore, United Kingdom, Remote, United States, Canada, Australia and Germany are recognized by default.

To add another country, append entries to both dictionaries:

```python
BOOTSTRAP_COUNTRY_ALIASES["new zealand"] = {"nz", "auckland", "wellington"}
COUNTRY_ALIASES["new zealand"] = ["new zealand", "auckland", "wellington"]
```

Use lowercase canonical country keys. Bootstrap matches whole normalized words and phrases, recognizes the country key itself, and returns all matched countries in dictionary order without duplicates. A CSV location such as `India / Singapore` can select both countries.

The default fallback is:

```python
BOOTSTRAP_DEFAULT_COUNTRIES = ("india",)
```

Change it to suit your preferences, for example:

```python
BOOTSTRAP_DEFAULT_COUNTRIES = ("united states", "canada")
```

Recognized CSV locations take precedence over fallback countries. Some platform APIs have their own geographic constraints or query only the first country; configure separate searches when necessary. Adding aliases does not add new ATS integrations or guarantee access to country-specific boards.

### Company discovery settings

```python
# Known platform and board identifier for a company
KNOWN_BOARDS["example company"] = ("greenhouse", "examplecompany")

# Ignore a company during bootstrap
SKIP_NAMES.add("example company to skip")

# Additional words to omit when generating candidate board identifiers
BOOTSTRAP_NOISE_WORDS.update({"limited", "corporation", "holdings"})
```

`MIN_CONFIDENT_JOBS` defaults to `5`. Lowering it may help discover small boards but can increase ambiguous matches. Review generated configurations before merging them.

For a custom platform integration, add its career URL pattern if you want it included in LLM-assisted discovery:

```python
PLATFORM_URL_HINTS["example_ats"] = "https://jobs.example-ats.com/<slug>"
```

A working platform integration is still required to verify suggested URLs.

### Runtime settings

See [`firstcomefirstemploy/config.py`](firstcomefirstemploy/config.py) for request timeouts, retries, concurrency and LLM settings. Keep credentials and API keys in `.env`.

## Adding a company

If the company uses an already-supported platform, no code is needed:

1. Find the company's public careers URL and identify the platform from the URL pattern:

| Platform | Typical URL pattern |
|---|---|
| Greenhouse | `boards.greenhouse.io/<name>` or `job-boards.greenhouse.io/<name>` |
| Workday | `<name>.wd<N>.myworkdayjobs.com` |
| Lever | `jobs.lever.co/<name>` |
| Ashby | `jobs.ashbyhq.com/<name>` |
| SmartRecruiters | `jobs.smartrecruiters.com/<name>` |
| Eightfold | Company-specific careers domain (e.g., `apply.careers.microsoft.com/careers`) |
| Oracle Recruiting Cloud | `<name>.fa.<region>.oraclecloud.com/hcmUI/CandidateExperience/` |
| Rippling | `ats.rippling.com/<name>/jobs` |
| TurboHire | `<name>.turbohire.co` |

2. Add an entry to `companies.json` (see [Configuration](#configuration)).
3. Verify it:

   ```bash
   python notify.py --discover "Example Company"
   python notify.py --probe "Example Company"
   python notify.py --audit "Example Company"
   ```

For many companies at once, see [Bulk company bootstrap and LLM fallback](#bulk-company-bootstrap-and-llm-fallback). If the portal uses an unsupported platform, see [Adding a new platform](#adding-a-new-platform).

## Bulk company bootstrap and LLM fallback

`--bootstrap` discovers career boards from company names in a CSV. It first tries **live, non-LLM discovery**, so an API key is not required for ordinary platform probing. An LLM is an optional final fallback for companies that remain unresolved.

### Prepare a CSV

```csv
name,location
Bespoke Labs,India
Proximal,India
Sarvam,India
Google,India
```

```bash
python notify.py --bootstrap companies.csv
```

Bootstrap tries known boards and company-specific portals, then slug-based ATS probes, then Workday tenant/site guesses, and finally an optional LLM hint. It will **verify the suggested URL with a supported platform integration before accepting it**. Unsupported, invalid or empty boards remain unresolved.

### Configure bootstrap countries

See [Customizing presets](#customizing-presets) for country aliases and fallback countries.

### Optional LLM providers: Gemini, OpenAI, Anthropic

Select **one** of the following providers by adding its key and `BOOTSTRAP_LLM_PROVIDER` to the project-root `.env`. You do not need to add keys for all three.

**Gemini (Google AI Studio)**

Get an API key at [Google AI Studio](https://aistudio.google.com/api-keys). Eligible Gemini models/accounts may have a limited free API tier; availability, model support and search-grounding quotas vary.

```dotenv
GEMINI_API_KEY=your-gemini-api-key
BOOTSTRAP_LLM_PROVIDER=gemini
GEMINI_LLM_MODEL=gemini-3.1-flash-lite
BOOTSTRAP_LLM_WEB_SEARCH=0
```

**OpenAI**

Get an API key at [OpenAI API Keys](https://platform.openai.com/api-keys). OpenAI API billing is separate from ChatGPT subscriptions.

```dotenv
OPENAI_API_KEY=your-openai-api-key
BOOTSTRAP_LLM_PROVIDER=openai
OPENAI_LLM_MODEL=gpt-4.1-mini
BOOTSTRAP_LLM_WEB_SEARCH=1
```

**Anthropic (Claude)**

Get an API key from the [Claude Console](https://console.anthropic.com/). API credits/billing are separate from consumer subscriptions.

```dotenv
ANTHROPIC_API_KEY=your-anthropic-api-key
BOOTSTRAP_LLM_PROVIDER=anthropic
ANTHROPIC_LLM_MODEL=claude-haiku-5-5
BOOTSTRAP_LLM_WEB_SEARCH=1
```

These are alternatives, **not** three blocks to paste together. Use the provider and model available to your account.

### Shared LLM settings

| Variable | Meaning |
|---|---|
| `BOOTSTRAP_LLM_PROVIDER` | `gemini`, `openai`, `anthropic`, or `auto` (default) |
| `GEMINI_API_KEY` | Gemini key; `GOOGLE_API_KEY` is also accepted |
| `OPENAI_API_KEY` | OpenAI key |
| `ANTHROPIC_API_KEY` | Anthropic key |
| `GEMINI_LLM_MODEL` | Default: `gemini-3.1-flash-lite` |
| `OPENAI_LLM_MODEL` | Default: `gpt-4.1-mini` |
| `ANTHROPIC_LLM_MODEL` | Default: `claude-haiku-5-5` |
| `BOOTSTRAP_LLM_MODEL` | Optional override of the selected provider's model |
| `BOOTSTRAP_LLM_WEB_SEARCH` | `1` enables provider-supported web search/grounding; `0` disables it |

With `BOOTSTRAP_LLM_PROVIDER=auto`, the current priority is **Anthropic → OpenAI → Gemini** based on which keys are configured. **Set `gemini` explicitly if you want Gemini even when OpenAI or Anthropic keys are present.** The default web-search setting in the code is `1`, so set `0` explicitly to avoid search-grounding usage.

Web search is optional: with `0`, the model can still suggest URLs from its existing knowledge, but it cannot look up current career pages through provider-native search. Provider support, eligibility and billing differ; a free model quota does not necessarily include free search grounding. With `1`, a compatible provider may use web search, subject to provider limits and charges.

The integration uses HTTP requests directly; no extra provider-specific Python SDK is required.

### Results and verification

After bootstrap, review:

- `companies.generated.json`: verified, newly discovered companies for this run.
- `companies.unresolved.csv`: companies that were not verified, including any LLM-suggested platform, URL and notes.

An LLM suggestion **is not proof** that a job board is valid. For example, `jobs.ashbyhq.com/proximal` may be suggested but rejected if the board API does not return usable jobs. Board slugs can be case-sensitive; confirm the exact URL on the company's official careers page when a suggestion fails.

```bash
# Review the generated configuration, then merge verified companies
python notify.py --merge companies.generated.json
```

An empty generated file means no **new verified** companies were produced in that run. Previously configured companies may be skipped; unresolved suggestions do not enter the generated configuration. Keep `.env` outside version control, and remember that provider API quotas, permissions or credits can prevent the LLM fallback from running even while normal ATS probes still work.

## Usage

Run commands from the repository root.

| Command | Description |
|---|---|
| `python notify.py` | Monitor all enabled companies |
| `python notify.py --dry-run` | Preview changes without sending emails or saving state |
| `python notify.py --company Amazon` | Monitor a specific company |
| `python notify.py --discover` | Discover career portal metadata |
| `python notify.py --refresh-discovery` | Refresh cached metadata |
| `python notify.py --probe Amazon` | Test live job fetching |
| `python notify.py --audit Amazon` | Show why each job was kept or filtered out |
| `python notify.py --bootstrap companies.csv` | Generate company configurations from a CSV (optional LLM fallback) |
| `python notify.py --merge companies.generated.json` | Merge generated configurations into `companies.json` |
| `python notify.py --test-email` | Send a test email |

Run `python notify.py --help` for all options.

## Running on a schedule

FirstComeFirstEmploy runs once and exits, so schedule it with whatever you have. Every 30 to 60 minutes is usually plenty and keeps the load on career sites low.

**cron (Linux / macOS):**

```cron
*/30 * * * * cd /path/to/FirstComeFirstEmploy && .venv/bin/python notify.py >> run.log 2>&1
```

On macOS you can use `launchd` instead, and on Windows, Task Scheduler. Note that `states/` must persist between runs, so ephemeral runners (for example, a fresh CI container each time) need that folder cached or synced.

## Data and state

| Path | Contents | In git? |
|---|---|---|
| `companies.json` | Company and search configuration | yes |
| `.env` | Email credentials and optional LLM API keys | **no** |
| `companies.generated.json` | Newly verified bootstrap results (review before merging) | local output |
| `companies.unresolved.csv` | Unresolved bootstrap results and LLM hints | local output |
| `discovery/` | Cached career portal metadata | no |
| `states/` | Job snapshots used for change detection | no |

`discovery/` and `states/` are created on first run. Delete a company's file in `states/` to re-baseline it.

## How it works

Each monitoring run:

1. Loads configured companies and searches.
2. Discovers the platform and required metadata (cached after the first time).
3. Fetches jobs with the platform-specific integration. Searches that would send the same request share one fetch (see [Fetch caching and `cache_key`](#fetch-caching-and-cache_key)).
4. Applies the search filters.
5. Diffs against the previous snapshot.
6. Emails any additions, removals and modifications.
7. Saves the new snapshot.

### Project layout

```text
FirstComeFirstEmploy/
├── notify.py
├── companies.json
├── requirements.txt
├── README.md
└── firstcomefirstemploy/
    ├── config.py
    ├── presets.py
    ├── platforms/
    │   ├── __init__.py
    │   ├── base.py
    │   ├── registry.py
    │   ├── ashby.py
    │   ├── lever.py
    │   └── ...
    ├── commands/
    ├── utils/
    └── notifiers/
```

| Component | Responsibility |
|---|---|
| `notify.py` | Application entry point |
| `companies.json` | Companies and search preferences |
| `config.py` | Runtime settings |
| `presets.py` | Matching and discovery preferences |
| `platforms/base.py` | Common platform interface |
| `platforms/registry.py` | Platform registration and lookup |
| `platforms/ashby.py`, `platforms/lever.py` | Reference integrations |
| `platforms/` | Career platform integrations |
| `commands/` | CLI commands |
| `utils/` | Shared utilities |
| `notifiers/` | Notification services |

## Adding a new platform

A company on an unsupported career platform needs a new integration. Once it exists, every other company on the same platform works through `companies.json` alone.

**1. Inspect the career portal.** Open the careers page with your browser's developer tools (Network tab) and find the public API calls behind the job search. Work out how listings and pagination work, how keyword and location filters are passed, where each job's ID, title, location and application URL live, and whether descriptions are available.

**2. Create a platform module** under `firstcomefirstemploy/platforms/`, for example `example_ats.py`:

```python
from .base import Platform
from .registry import register


@register
class ExampleATS(Platform):

    name = "example_ats"
    per_job_descriptions = False  # True if descriptions need one request per job

    def detect(self, host, path):
        return "example-ats.com" in host

    def discover(self, session, company):
        return {
            "api_url": "https://example-ats.com/api/jobs"
        }

    def cache_key(self, metadata, search):
        # Include every search field that changes the request sent to the API.
        return (self.name, search.get("search_text", ""))

    def fetch_raw(self, session, company, metadata, search):
        # Fetch jobs using the discovered metadata.
        # Handle pagination and normalize results.
        jobs = {}

        return jobs, False
```

| Member | Purpose |
|---|---|
| `name` | Unique platform identifier, used in `companies.json` |
| `detect(host, path)` | Returns `True` if a career URL belongs to this platform |
| `discover(session, company)` | Returns platform metadata, cached in `discovery/` |
| `cache_key(metadata, search)` | Identifies which searches can share one fetch (see below) |
| `per_job_descriptions` | Set to `True` if descriptions need one extra request per job |
| `fetch_raw(session, company, metadata, search)` | Fetches and normalizes jobs; returns `(jobs, server_filtered)` |

Use the `ashby.py` and `lever.py` integrations as references for exact signatures and return format. Choose a unique filename and platform `name` to avoid replacing a built-in integration. For bootstrap support, set `bootstrap_url` and optionally `bootstrap_order`, or override the bootstrap hooks in `base.py`. For LLM-assisted discovery, add the platform URL shape to `PLATFORM_URL_HINTS` in `presets.py`. The `utils.http`, `utils.text` and `filters` modules remain importable from your Python adapter.

**3. Test it.** The platform package imports new modules automatically and `@register` registers the class, so nothing needs to change in the registry or other application modules.

```bash
python notify.py --discover "Example Company"
python notify.py --probe "Example Company"
python notify.py --audit "Example Company"
```

Before enabling regular monitoring, verify job counts, pagination, locations, application URLs and filtering.

## Fetch caching and `cache_key`

A company can have several searches. Many of them differ only in local filters such as title keywords, so refetching the same listings for each one would waste requests. Each platform decides which search fields change the *request* it sends, and `cache_key` returns exactly those fields. Searches with an equal key share a single fetch, and the filters are then applied to the shared results for each search.

```python
def cache_key(self, metadata, search):
    return (
        self.name,
        primary_country(search),                  # sent to the API
        search.get("amazon_country", ""),         # sent to the API
        search.get("search_text", ""),            # sent to the API
        description_filters_requested(search),    # decides whether per-job pages are fetched
    )
```

**Rule of thumb:** include every search field that changes what `fetch_raw` requests or returns, and leave out fields that are only used in local filtering (`title_any`, `title_exclude`, and so on).

- **Too few fields:** searches that need different data share one result, and jobs go missing.
- **Too many fields:** identical requests are repeated, which is slower and less polite to the career site.

### Description fetching

Some portals only return descriptions on each job's own page. Set `per_job_descriptions = True` on the platform class, and fetch those pages only when `description_filters_requested(search)` is true and only for jobs that pass `passes_prefilters`, to keep request counts low. Include `description_filters_requested(search)` in the cache key, because it changes how much work `fetch_raw` does.

### Return value

`fetch_raw` returns `(jobs, server_filtered)`. Return `True` as the second value when the server already applied the country filter (Amazon does this with `country[]`), so it isn't applied again locally.

## Limitations and responsible use

- Relies on publicly accessible career endpoints, which companies can change without notice. When a portal breaks, `--probe` is the fastest way to see why.
- Job data quality varies: some portals return incomplete descriptions or locations.
- New platforms need a custom integration; an LLM suggestion cannot add an unsupported ATS automatically.
- LLM-suggested URLs may be wrong or stale, and provider calls may incur API charges or hit quotas. A suggested URL must pass live verification before it is accepted.
- Be a polite client: keep the schedule modest and do not hammer career sites. Check each site's terms of service before monitoring it. Automated access to some sites, LinkedIn in particular, may be restricted by their terms.

## License

[MIT](https://github.com/worldinmyfist/FirstComeFirstEmploy/blob/main/LICENSE)
