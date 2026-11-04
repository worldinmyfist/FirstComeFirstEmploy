
# FirstComeFirstEmploy

**Automated job monitoring and email alerts across company career portals.**

FirstComeFirstEmploy monitors configured company career pages, filters relevant jobs, tracks changes between runs, and emails you when jobs are added, removed, or modified.

## Why FirstComeFirstEmploy?

- Avoid manually checking multiple company career pages.
- Catch newly posted jobs through automated monitoring.
- Filter irrelevant roles based on keywords, titles, experience, and location.
- Track changes in job postings.
- Monitor multiple companies across different ATS platforms from one place.

## Features

- Monitor multiple companies and searches.
- Support Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Atlassian, Amazon, Eightfold, Google, Oracle, and LinkedIn.
- Automatically discover and cache career portal metadata where supported.
- Apply configurable title, location, and description filters.
- Detect job additions, removals, and modifications.
- Send email alerts for matching changes.
- Audit filtering decisions and export results to CSV.
- Add companies through configuration and extend support for new ATS platforms.

## How it works

1. Load configured companies and searches.
2. Discover the career portal platform and required metadata.
3. Fetch jobs using platform-specific integrations.
4. Apply configured search and filtering rules.
5. Compare results against previously saved snapshots.
6. Send email alerts for detected changes.
7. Save the latest results.

The first successful run establishes a baseline without sending alerts for existing jobs.

## Repository structure

```text
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
    │   ├── workday
    │   ├── greenhouse
    │   ├── lever
    │   ├── ashby
    │   ├── smartrecruiters
    │   ├── atlassian
    │   ├── amazon
    │   ├── eightfold
    │   ├── google
    │   ├── oracle
    │   └── linkedin
    │
    ├── notifiers/
    │   └── email_notifier.py
    │
    ├── commands/
    └── utils/
```

| Component | Responsibility |
|---|---|
| `discovery.py` | Career portal discovery |
| `fetcher.py` | Job fetching orchestration |
| `filters.py` | Search and job filtering |
| `state.py` | Change detection and snapshots |
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

Companies are configured in `companies.json`.

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

This is an illustrative example; use the actual field names and structure expected by the existing configuration parser.

### 3. Discover and test

```bash
python notify.py --discover "Example Company"
python notify.py --probe "Example Company"
python notify.py --audit "Example Company"
```

If the ATS is already supported, no new platform implementation should be necessary.

## Adding a new platform

If a company uses an ATS that is not currently supported, a new platform integration is required.

### 1. Inspect the career portal

Identify the public job-search endpoints and understand how the portal exposes:

- Job listings and pagination.
- Search keywords and location filters.
- Job IDs, titles, locations, and application URLs.
- Job descriptions, where available.

Use the browser's developer tools/network requests to inspect the portal's public API.

### 2. Implement the platform integration

Create a new module under `firstcomefirstemploy/platforms/`.

For example:

```text
firstcomefirstemploy/
└── platforms/
    └── example_ats.py
```

Implement the platform-specific logic for:

- Fetching job postings.
- Handling pagination and search parameters.
- Retrieving descriptions where supported.
- Normalizing results into the common job format used by FirstComeFirstEmploy.

### 3. Add discovery support

Update `firstcomefirstemploy/discovery.py` to identify the new ATS and retrieve the metadata required by its integration.

Cache the discovered metadata under `discovery/` so it can be reused across monitoring runs.

### 4. Register the integration

Connect the new platform module to the existing fetching and discovery orchestration.

Update the relevant platform dispatch logic in `fetcher.py` and any required command or configuration mappings.

### 5. Test the integration

```bash
python notify.py --discover "Example Company"
python notify.py --probe "Example Company"
python notify.py --audit "Example Company"
```

Verify job counts, pagination, locations, application URLs, and filtering before enabling regular monitoring.

Once integrated, other companies using the same ATS can reuse the implementation through configuration.

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
- `states/`: Latest job snapshots used for change detection.

These directories are local by default. The `states/` directory can optionally be synced through Google Drive.

## Limitations

- Depends on publicly accessible career portal endpoints, which may change.
- Platform discovery and job data availability vary.
- Some portals provide incomplete descriptions or location information.
- New ATS platforms may require custom integration work.

---

**FirstComeFirstEmploy** — One place to monitor jobs across multiple company career portals.
