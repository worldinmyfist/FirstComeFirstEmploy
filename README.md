# FirstComeFirstEmploy

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)

**A job alert bot that watches company career pages and emails you the moment a matching role is added, removed, or changed.**

Stop refreshing a dozen career portals. Point FirstComeFirstEmploy at the companies you care about, describe the roles you want, and let it run on a schedule. It compares each run against the previous snapshot and emails you only when something actually changed.

<!-- TODO: add a screenshot of a real alert email, then uncomment the line below.
![Sample alert email](docs/images/sample-email.png)
-->

## Contents

- [Highlights](#highlights)
- [Quickstart](#quickstart)
- [Email setup](#email-setup)
- [Configuration](#configuration)
- [Adding a company](#adding-a-company)
- [Usage](#usage)
- [Running on a schedule](#running-on-a-schedule)
- [Data and state](#data-and-state)
- [How it works](#how-it-works)
- [Adding a new platform](#adding-a-new-platform)
- [Limitations and responsible use](#limitations-and-responsible-use)
- [License](#license)

## Highlights

- **One tool, many portals:** Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold, plus custom integrations for Amazon, Google, Atlassian, Oracle and LinkedIn.
- **Change detection, not just scraping:** alerts for added, removed and modified jobs, based on snapshots from the previous run.
- **Per-company searches:** each company can have several searches, each with its own title, location and description filters.
- **Auto-discovery:** detects the platform and required metadata from a career URL, and caches it.
- **Debuggable filtering:** `--audit` shows why each job was kept or dropped; `--probe` tests live fetching; `--dry-run` previews without emailing or saving.
- **Easy to extend:** a new platform is one Python file with a `@register` decorator. No other files need to change.

## Quickstart

```bash
# 1. Install
git clone https://github.com/worldinmyfist/FirstComeFirstEmploy.git
cd FirstComeFirstEmploy
python3 -m venv .venv
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

> Requires Python 3.9 or newer.

## Email setup

Create a `.env` file in the project root:

```env
EMAIL_FROM=your_email@gmail.com
EMAIL_TO=recipient@example.com
EMAIL_PASSWORD=your_gmail_app_password

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) rather than your account password. App Passwords require 2-Step Verification to be enabled. Never commit `.env`; make sure it is listed in `.gitignore`.

Verify delivery with:

```bash
python notify.py --test-email
```

## Configuration

Companies and searches live in `companies.json`. Each company has a platform and one or more searches:

```json
{
  "name": "Example Company",
  "career_url": "https://boards.greenhouse.io/example",
  "platform": "greenhouse",
  "enabled": true,
  "searches": [
    {
      "name": "India AI Roles",
      "location": "India",
      "title_any": ["Software Engineer", "Machine Learning Engineer", "Quant Researcher"],
      "title_exclude": ["Intern", "Senior Manager"],
      "description_any": ["Python", "C++", "Machine Learning"],
      "description_exclude": ["Sales", "Recruitment"]
    }
  ]
}
```

### Search fields

| Field                 | Purpose                                                          |
|-----------------------|------------------------------------------------------------------|
| `location`            | Keep only jobs in this location (country/city aliases supported) |
| `title_any`           | Keep jobs whose title matches **any** of these keywords          |
| `title_exclude`       | Drop jobs whose title matches **any** of these keywords          |
| `description_any`     | Keep jobs whose description matches **any** of these keywords    |
| `description_exclude` | Drop jobs whose description matches **any** of these keywords    |

<!-- TODO: state the exact matching rules here, taken from filters.py:
     - case-sensitive or not?  substring, whole-word, or regex?
     - how are the fields combined (AND across fields?)  what happens when a field is empty?
     - what counts as a "modified" job (which fields are compared)? -->

Reusable title presets and location aliases are defined in `firstcomefirstemploy/presets.py`. Put company-specific keywords in `companies.json`.

Not every portal exposes job descriptions. Where a platform returns none, description filters cannot match, so check with `--audit` before relying on them.

## Adding a company

If the company uses an already-supported platform, no code is needed:

1. Find the company's public careers URL and identify the platform from the URL pattern:

   | Platform        | Typical URL pattern               |
   |-----------------|-----------------------------------|
   | Greenhouse      | `boards.greenhouse.io/<name>`     |
   | Lever           | `jobs.lever.co/<name>`            |
   | Ashby           | `jobs.ashbyhq.com/<name>`         |
   | Workday         | `<name>.wd<N>.myworkdayjobs.com`  |
   | SmartRecruiters | `jobs.smartrecruiters.com/<name>` |

2. Add an entry to `companies.json` (see [Configuration](#configuration)).
3. Verify it:

   ```bash
   python notify.py --discover "Example Company"
   python notify.py --probe "Example Company"
   python notify.py --audit "Example Company"
   ```

If the portal uses an unsupported platform, see [Adding a new platform](#adding-a-new-platform).

## Usage

Run commands from the repository root.

| Command                                             | Description                                             |
|-----------------------------------------------------|---------------------------------------------------------|
| `python notify.py`                                  | Monitor all enabled companies                           |
| `python notify.py --dry-run`                        | Preview changes without sending emails or saving state  |
| `python notify.py --company Amazon`                 | Monitor a specific company                              |
| `python notify.py --discover`                       | Discover career portal metadata                         |
| `python notify.py --refresh-discovery`              | Refresh cached metadata                                 |
| `python notify.py --probe Amazon`                   | Test live job fetching                                  |
| `python notify.py --audit Amazon`                   | Show why each job was kept or filtered out              |
| `python notify.py --bootstrap companies.csv`        | Generate company configurations from a CSV              |
| `python notify.py --merge companies.generated.json` | Merge generated configurations into `companies.json`    |
| `python notify.py --test-email`                     | Send a test email                                       |

Run `python notify.py --help` for all options.

<!-- TODO: document the companies.csv columns expected by --bootstrap. -->

## Running on a schedule

FirstComeFirstEmploy runs once and exits, so schedule it with whatever you have. Every 30 to 60 minutes is usually plenty and keeps the load on career sites low.

**cron (Linux / macOS):**

```cron
*/30 * * * * cd /path/to/FirstComeFirstEmploy && .venv/bin/python notify.py >> run.log 2>&1
```

On macOS you can use `launchd` instead, and on Windows, Task Scheduler. Note that `states/` must persist between runs, so ephemeral runners (for example, a fresh CI container each time) need that folder cached or synced.

## Data and state

| Path             | Contents                                | In git? |
|------------------|-----------------------------------------|---------|
| `companies.json` | Company and search configuration        | yes     |
| `.env`           | Email credentials                       | **no**  |
| `discovery/`     | Cached career portal metadata           | no      |
| `states/`        | Job snapshots used for change detection | no      |

`discovery/` and `states/` are created on first run. Delete a company's file in `states/` to re-baseline it.

## How it works

Each run:

1. Loads configured companies and searches.
2. Discovers the platform and required metadata (cached after the first time).
3. Fetches jobs with the platform-specific integration.
4. Applies the search filters.
5. Diffs against the previous snapshot.
6. Emails any additions, removals and modifications.
7. Saves the new snapshot.

### Project layout

```
FirstComeFirstEmploy/
├── notify.py                 # entry point
├── companies.json            # companies and searches
├── requirements.txt
├── .env                      # generated / not in git
├── states/                   # generated / not in git
├── discovery/                # generated / not in git
└── firstcomefirstemploy/
    ├── cli.py
    ├── config.py
    ├── presets.py
    ├── filters.py
    ├── companies.py
    ├── discovery.py
    ├── fetcher.py
    ├── state.py
    ├── platforms/            # one module per ATS / career site
    │   ├── base.py
    │   ├── registry.py
    │   ├── workday.py, greenhouse.py, lever.py, ashby.py, smartrecruiters.py
    │   └── atlassian.py, amazon.py, eightfold.py, google.py, oracle.py, linkedin.py
    ├── notifiers/
    │   └── email_notifier.py
    ├── commands/
    └── utils/
```

| Component               | Responsibility                                              |
|-------------------------|-------------------------------------------------------------|
| `discovery.py`          | Career portal discovery and metadata caching                |
| `fetcher.py`            | Job fetching orchestration                                  |
| `filters.py`            | Search and job filtering                                    |
| `presets.py`            | Default title-matching presets and country/location aliases |
| `state.py`              | Change detection and snapshots                              |
| `platforms/base.py`     | Common platform interface and shared functionality          |
| `platforms/registry.py` | Platform registration and lookup                            |
| `platforms/`            | ATS-specific integrations                                   |
| `notifiers/`            | Email notifications                                         |
| `commands/`             | CLI commands                                                |
| `utils/`                | Shared utilities                                            |

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

    def detect(self, host, path):
        return "example-ats.com" in host

    def discover(self, session, company):
        return {
            "api_url": "https://example-ats.com/api/jobs"
        }

    def fetch_raw(self, session, company, metadata, search):
        # Fetch jobs using the discovered metadata.
        # Handle pagination and normalize results.
        jobs = {}

        return jobs, False
```

| Member                                          | Purpose                                                  |
|-------------------------------------------------|----------------------------------------------------------|
| `name`                                          | Unique platform identifier, used in `companies.json`     |
| `detect(host, path)`                            | Returns `True` if a career URL belongs to this platform  |
| `discover(session, company)`                    | Returns platform metadata, cached in `discovery/`        |
| `fetch_raw(session, company, metadata, search)` | Fetches and normalizes jobs                              |

<!-- TODO: document the normalized job dict shape returned by fetch_raw (keys such as id, title,
     location, url, description) and what the second return value (False above) means.
     Copy both from platforms/base.py or an existing integration. -->

Use an existing integration such as `greenhouse.py` as the reference for exact signatures and return format.

**3. Test it.** The platform package imports new modules automatically and `@register` registers the class, so nothing needs to change in `registry.py`, `__init__.py`, `discovery.py` or `fetcher.py`.

```bash
python notify.py --discover "Example Company"
python notify.py --probe "Example Company"
python notify.py --audit "Example Company"
```

Before enabling regular monitoring, verify job counts, pagination, locations, application URLs and filtering. Pull requests with new platform integrations are welcome.

## Limitations and responsible use

- Relies on publicly accessible career endpoints, which companies can change without notice. When a portal breaks, `--probe` is the fastest way to see why.
- Job data quality varies: some portals return incomplete descriptions or locations.
- New platforms need a custom integration.
- Be a polite client: keep the schedule modest and do not hammer career sites. Check each site's terms of service before monitoring it. Automated access to some sites, LinkedIn in particular, may be restricted by their terms.

## License

[MIT](LICENSE)
