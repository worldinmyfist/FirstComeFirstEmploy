
# FirstComeFirstEmploy

**Automated job monitoring and email alerts across company career portals.**

FirstComeFirstEmploy monitors configured company career pages, filters relevant jobs, tracks changes between runs, and emails you when jobs are added, removed, or modified.

## Why FirstComeFirstEmploy?

- Avoid manually checking multiple company career pages.
- Catch newly posted jobs through automated monitoring.
- Filter irrelevant roles by keywords, titles, experience, and location.
- Track changes in job postings.
- Monitor multiple companies across different ATS platforms from one place.

## Features

- Monitor multiple companies and searches.
- Support Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Atlassian, Amazon, Eightfold, Google, Oracle, and LinkedIn.
- Automatically discover and cache career portal metadata where supported.
- Apply configurable title, location, and description filters.
- Built-in title-matching presets and country/location aliases.
- Detect job additions, removals, and modifications.
- Send email alerts for matching changes.
- Audit filtering decisions and export results to CSV.
- Add companies through configuration and extend support for new ATS platforms.

## How it works

1. Load configured companies and searches.
2. Discover the career portal platform and required metadata.
3. Fetch jobs using platform-specific integrations.
4. Apply search and filtering rules.
5. Compare results against previously saved snapshots.
6. Send email alerts for detected changes.
7. Save the latest results.

The first successful run establishes a baseline without sending alerts for existing jobs.

## Repository structure

```text id="h7xq9m"
FirstComeFirstEmploy/
├── notify.py
├── companies.json
├── requirements.txt
├── .env
├── states/
├── discovery/
│
└── firstcomefirstemploy/
    ├── cli.py
    ├── config.py
    ├── presets.py
    ├── filters.py
    ├── companies.py
    ├── discovery.py
    ├── fetcher.py
    ├── state.py
    │
    ├── platforms/
    │   ├── __init__.py
    │   ├── registry.py
    │   ├── workday.py
    │   ├── greenhouse.py
    │   ├── lever.py
    │   ├── ashby.py
    │   ├── smartrecruiters.py
    │   ├── atlassian.py
    │   ├── amazon.py
    │   ├── eightfold.py
    │   ├── google.py
    │   ├── oracle.py
    │   └── linkedin.py
    │
    ├── notifiers/
    │   └── email_notifier.py
    │
    ├── commands/
    └── utils/
```

| Component | Responsibility |
|---|---|
| `discovery.py` | Career portal discovery and metadata caching |
| `fetcher.py` | Job fetching orchestration |
| `filters.py` | Search and job filtering |
| `presets.py` | Default title-matching presets and country/location aliases |
| `state.py` | Change detection and snapshots |
| `platforms/registry.py` | Platform registration and lookup |
| `platforms/` | ATS-specific integrations |
| `notifiers/` | Email notifications |
| `commands/` | CLI commands |
| `utils/` | Shared utilities |

## Installation

```bash
git clone https://github.com/worldinmyfist/FirstComeFirstEmploy.git
cd FirstComeFirstEmploy

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root:

```env
EMAIL_FROM=your_email@gmail.com
EMAIL_TO=recipient@example.com
EMAIL_PASSWORD=your_gmail_app_password

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
```

Configure companies, career URLs, platform details, and search filters in `companies.json`.

Use a Gmail App Password where applicable.

## Adding a company

Companies using an already-supported ATS can be added through `companies.json` without implementing new platform code.

### 1. Find the company's career page

Identify the company's public careers URL and determine which ATS platform it uses.

### 2. Add the company configuration

Add a new company entry to `companies.json`, following the existing configuration format.

For example, a company using an already-supported Greenhouse integration:

```json
{
  "name": "Example Company",
  "career_url": "https://boards.greenhouse.io/example",
  "platform": "greenhouse",
  "enabled": true,
  "searches": [
    {
      "name": "India Tech Roles",
      "location": "India",
      "title_any": [
        "Software Engineer",
        "Machine Learning Engineer"
      ],
      "title_exclude": [
        "Intern"
      ]
    }
  ]
}
```

This is illustrative; use the actual field names and structure expected by the configuration parser.

### 3. Discover and test

```bash
python notify.py --discover "Example Company"
python notify.py --probe "Example Company"
python notify.py --audit "Example Company"
```

If the ATS is already supported, no new platform implementation is required.

## Adding a new platform

A company using an unsupported ATS requires a new platform integration.

### 1. Inspect the career portal

Identify its public job-search endpoints and understand:

- Job listings and pagination.
- Search keywords and location filters.
- Job IDs, titles, locations, and application URLs.
- Job descriptions, where available.

Use browser developer tools and network requests to inspect public API calls.

### 2. Create a platform module

Create a new module under `firstcomefirstemploy/platforms/`.

For example:

```text
firstcomefirstemploy/
└── platforms/
    ├── __init__.py
    ├── registry.py
    └── example_ats.py
```

Implement a platform class using the existing interface:

```python
from firstcomefirstemploy.platforms.registry import register


@register
class ExampleATS:

    name = "example_ats"

    def detect(self, host, path):
        return "example-ats.com" in host

    def discover(self, session, company):
        return {
            "api_url": "https://example-ats.com/api/jobs"
        }

    def fetch_jobs(self, session, company, metadata, search):
        # Fetch jobs using discovered metadata
        # Handle pagination and normalize job results
        return []
```

The platform implementation should provide:

- `name`: Unique platform identifier.
- `detect(host, path)`: Identifies whether a career URL belongs to this ATS.
- `discover(session, company)`: Retrieves ATS-specific metadata.
- `fetch_jobs(session, company, metadata, search)`: Fetches and normalizes job postings.

Follow the exact method signatures and return format used by existing platform integrations.

### 3. Import the new platform

Add the new module import to `firstcomefirstemploy/platforms/__init__.py`:

```python
from . import example_ats
```

This executes the `@register` decorator and makes the platform available through the registry.

No changes to `registry.py` are required because registration is handled by the decorator.

### 4. Discovery and fetching

The existing `discovery.py` automatically:

- Detects the platform using `detect()`.
- Calls its `discover()` method.
- Caches the returned metadata under `discovery/`.
- Reuses cached metadata on subsequent runs.

The existing `fetcher.py` retrieves the integration through the registry and calls `fetch_jobs()`.

No platform-specific changes to `discovery.py` or `registry.py` are required.

### 5. Test the integration

```bash
python notify.py --discover "Example Company"
python notify.py --probe "Example Company"
python notify.py --audit "Example Company"
```

Verify job counts, pagination, locations, application URLs, and filtering before enabling regular monitoring.

Once integrated, other companies using the same ATS can reuse the implementation through configuration alone.

## Usage

Run commands from the repository root.

| Command | Description |
|---|---|
| `python notify.py` | Monitor all enabled companies |
| `python notify.py --dry-run` | Preview changes without sending emails or saving states |
| `python notify.py --company Amazon` | Monitor a specific company |
| `python notify.py --discover` | Discover career portal metadata |
| `python notify.py --refresh-discovery` | Refresh cached metadata |
| `python notify.py --probe Amazon` | Test live job fetching |
| `python notify.py --audit Amazon` | Audit filtering decisions |
| `python notify.py --bootstrap companies.csv` | Generate company configurations |
| `python notify.py --merge companies.generated.json` | Merge generated configurations |
| `python notify.py --test-email` | Test email delivery |

For all available options:

```bash
python notify.py --help
```

## Data storage

- `companies.json`: Company and search configurations.
- `discovery/`: Cached career portal metadata.
- `states/`: Job snapshots used for change detection.

These directories are local by default. The `states/` directory can optionally be synced through Google Drive.

## Limitations

- Depends on publicly accessible career portal endpoints, which may change.
- Platform discovery and job data availability vary.
- Some portals provide incomplete descriptions or location information.
- New ATS platforms may require custom integration work.

---

**FirstComeFirstEmploy** — One place to monitor jobs across multiple company career portals.
