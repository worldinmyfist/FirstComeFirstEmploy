# JobNotify

**Automated job monitoring and email alerts across company career
portals.**

JobNotify checks configured company career pages, applies your search
and filtering rules, compares current results with previously saved
snapshots, and emails you when tracked jobs are added, removed, or
modified.

It is designed to replace repeatedly checking multiple career pages by
hand.

## Features

-   Monitor multiple companies and multiple searches per company.
-   Support public career-page platforms including Workday, Greenhouse,
    Lever, Ashby, SmartRecruiters, Atlassian, Amazon Jobs, Eightfold,
    and Google Careers.
-   Discover and cache the technical details needed to query each
    company's career portal.
-   Filter by country, location, search keywords, job family, and title
    inclusion/exclusion rules.
-   Apply description-based exclusion rules to remove roles whose
    descriptions contain specified terms, such as excessive experience
    requirements.
-   Retrieve job descriptions where supported by the career platform.
-   Do not reject a job solely because its description is unavailable.
-   Detect added, removed, and modified postings.
-   Send email alerts with job titles, locations, and application links.
-   Save the latest results as JSON state files.
-   Audit title and description filters to see exactly why a job is kept
    or excluded.
-   Export audit results to CSV, including matched rules and description
    availability.
-   Preview live results and test email delivery without running the
    normal monitoring workflow.
-   Bootstrap company configurations from a list of company names and
    merge generated entries into the existing configuration.

## How it works

1.  Read enabled companies and their searches from `companies.json`.
2.  Identify the career portal platform and obtain the metadata needed
    to query it.
3.  Fetch jobs matching each configured search.
4.  Apply the configured title and description filters.
5.  Compare the results with the previous snapshot in `states/`.
6.  If changes are detected, send an email.
7.  Save the latest successful results to `states/`.

The first successful run for a search creates its baseline. Existing
jobs are saved but are not reported as newly added on that first run.

## Repository layout

``` text
JobNotify/
├── notify.py
├── companies.json
├── requirements.txt
├── .env                  # local only; do not commit
├── states/               # local state snapshots; optionally sync via Drive
├── discovery/            # cached career portal metadata
├── audit_results.csv     # generated audit report
└── README.md
```

  -----------------------------------------------------------------------
  Path                                Purpose
  ----------------------------------- -----------------------------------
  `notify.py`                         Monitoring engine and command-line
                                      utilities.

  `companies.json`                    Company list, career portal
                                      configuration, and search filters.

  `.env`                              Email and SMTP credentials. Keep
                                      this private.

  `states/`                           Latest job snapshot for each
                                      company/search monitor.
  
  `discovery/`                        Cached platform metadata used to
                                      query career portals.
  
  `audit_results.csv`                 Appended audit results showing
                                      filter decisions.

  -----------------------------------------------------------------------

## Installation

### 1. Clone the repository

``` bash
git clone https://github.com/worldinmyfist/JobNotify.git
cd JobNotify
```

### 2. Create and activate a virtual environment

``` bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

``` bash
pip install -r requirements.txt
```

### 4. Configure email

Create a `.env` file in the project root:

``` env
EMAIL_FROM=your_email@gmail.com
EMAIL_TO=recipient@example.com
EMAIL_PASSWORD=your_gmail_app_password

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
```

For Gmail, use an App Password where applicable, not your normal account
password.

### 5. Configure companies

Edit `companies.json` as a starting point. Add the companies, career
URLs, platform details (where needed), and search filters you want to
monitor.

A search configuration can include title filters and description
exclusions. Keep your actual company configuration and credentials
private; publish only anonymised examples.

## Filtering jobs

JobNotify applies multiple layers of filtering. These are configured
separately for each search in `companies.json`.

### Title filters

Title filters identify relevant roles based on their job titles.
Depending on the configuration, you can use:

-   `title_any`: Include jobs whose titles match at least one specified
    term.
-   `title_exclude`: Exclude jobs whose titles contain specified terms.
-   `title_preset`: Apply a predefined collection of title rules.

### Description exclusions

Use `description_exclude` to exclude jobs based on their job description
rather than relying only on the title.

This is useful for filtering out roles with experience requirements that
exceed your target. For example, an exclusion list might contain phrases
such as:

``` json
{
  "description_exclude": [
    "6+ years",
    "8 years",
    "10+ years",
    "12+ years",
    "15+ years"
  ]
}
```

The terms above are illustrative; configure the phrases that make sense
for your searches.

Description filtering is applied when a usable job description is
available. If a portal does not provide a description, the job is not
automatically rejected solely because the description could not be
retrieved.

Description retrieval depends on the career platform and the data
exposed by its public endpoints. Some platforms may provide only partial
descriptions.

### Country and location filtering

Searches can be configured with country and location constraints. Some
platforms expose these filters directly through their APIs; others
require JobNotify to evaluate the returned job location.

Amazon Jobs uses country-specific career-site selection based on the
configured search/company settings. Check the relevant company
configuration when monitoring an Amazon country site.

## Command-line reference

Run commands from the repository directory with your virtual environment
activated:

``` bash
python notify.py --help
```

### Normal monitoring

``` bash
python notify.py
```

Runs the enabled monitors. It fetches current jobs, compares them with
saved states, sends an email if changes are found, and then saves the
latest successful results.

The initial successful run creates a baseline for each monitor. It does
not send an alert containing every already-existing job.

### `--dry-run`

``` bash
python notify.py --dry-run
```

Runs the monitoring checks and calculates changes, but does **not** send
emails or save the new state snapshots. Detected changes are printed to
the terminal.

Use this to test search filters and inspect what an email would contain
without changing the baseline.

### `--list-companies`

``` bash
python notify.py --list-companies
```

Lists the companies configured in `companies.json`. Useful for checking
which companies are present and enabled before running monitoring.

### `--test-email`

``` bash
python notify.py --test-email
```

Sends a sample JobNotify email through the configured SMTP settings. Use
it to verify `.env` credentials and email delivery independently of
career-page monitoring.

### `--discover`

``` bash
python notify.py --discover
python notify.py --discover Company_Name
```

Discovers the technical information needed to query the selected
companies' career portals and prints a summary.

Depending on the platform, discovery can identify information such as:

-   **Workday:** tenant, site, and available search facets (for example,
    location or other filter values and their IDs).
-   **Greenhouse:** board token and number of postings.
-   **Other supported platforms:** relevant public API endpoint and,
    where available, posting count.

Discovery metadata is saved under `discovery/` and reused by later
monitoring runs.

You generally do not need to run discovery manually before every
monitoring run: the monitoring engine calls discovery when required.

### `--discover-ids`

``` bash
python notify.py --discover-ids
python notify.py --discover-ids Company_Name
```

Runs the same discovery process and displays the discovered platform
identifiers/details. It is particularly useful when inspecting Workday
facets and their IDs, which can help when configuring or troubleshooting
portal filters.

It is a discovery/inspection command; it does not perform normal change
detection or send job-change alerts.

### `--refresh-discovery`

``` bash
python notify.py --refresh-discovery
python notify.py --discover Company_Name --refresh-discovery
```

Forces discovery metadata to be fetched again instead of using the
cached copy.

Career portal metadata is not necessarily permanent. A company may
change its career-page URL, Workday tenant/site, board token, API
endpoint, or available filter facets. Cached metadata can then become
stale and cause requests to fail or return incomplete results.

JobNotify considers discovery metadata fresh for **7 days**. If the
cache is missing, older than that, has an incompatible schema, or no
longer matches the configured career URL/platform, discovery is
performed again automatically. `--refresh-discovery` bypasses that
freshness check and forces a refresh immediately.

Use it when:

-   A company's career page or job-board URL has changed.
-   Monitoring starts failing for a company even though its career page
    is accessible.
-   A platform's available facets or identifiers appear to have changed.
-   You have updated a company's platform configuration and want to
    rebuild its cached metadata.
-   You want to troubleshoot whether stale discovery data is the cause
    of an issue.

A refresh updates the cached metadata in `discovery/`. It does **not**
reset the job history in `states/` and does not itself send job-change
notifications.

### `--company`

```bash
python notify.py --company Amazon
python notify.py --company Amazon --dry-run
```

Runs monitoring only for the specified company instead of all enabled companies.

- Company names are matched case-insensitively.
- Only the selected company's configured searches are processed.
- `--dry-run` can be combined with `--company` to preview results without sending emails or saving state snapshots.
- Without `--company`, JobNotify monitors all enabled companies as usual.

Useful when testing a newly added company, troubleshooting a career portal, or running a targeted monitoring check.

### `--audit`

``` bash
python notify.py --audit
python notify.py --audit Company_Name
```

Audits the configured title and description exclusion filters for all
enabled companies or a selected company.

For each configured search, the audit fetches a broader set of jobs
using the other search criteria while temporarily omitting the title
inclusion/exclusion rules, title preset, and description exclusion
rules. It then evaluates the configured title and description filters
independently.

The audit reports:

-   **KEPT:** The job passes the audited title and description exclusion
    rules.
-   **EJECTED:** At least one audited filter rejects the job.
-   **Description unavailable:** The description could not be retrieved,
    so description-based exclusion could not be verified. A missing
    description is not treated as a filter match.

#### CSV report

Audit results are written to:

``` text
audit_results.csv
```

The CSV includes fields such as:

  Column                  Description
  ----------------------- ----------------------------------------
  `audit_timestamp`       Time of the audit run.
  `company`               Company being audited.
  `search`                Configured search name.
  `job_id`                Job posting identifier.
  `title`                 Job title.
  `location`              Job location.
  `url`                   Job/application URL.
  `status`                `KEPT` or `EJECTED`.
  `reason`                Why the job was rejected.
  `matched_rule`          Specific filter rule that matched.
  `description_status`    Whether the description was available.
  `description_matches`   Description exclusion terms found.

`reason` and `matched_rule` help identify whether a job was rejected by
a title rule or a description exclusion. `description_status` makes it
possible to distinguish a real non-match from a description that could
not be checked.

#### CSV behavior

Audit results are **appended** to `audit_results.csv` by default.
Repeated runs preserve previous results.

Use `audit_timestamp`, `company`, and `search` to isolate a particular
run. If you change the CSV column schema in the code, use a fresh CSV
file or migrate the old file first; appending rows with a different
column order can misalign the data.

#### Why use audit?

Filters can accidentally exclude relevant roles. For example, a role
with a suitable title may still be rejected because its description
contains an experience requirement, or a title rule may be too
restrictive.

Run audit after changing title or description filters, adding a company,
or when you suspect relevant roles are being missed. Review the CSV and
adjust the relevant rules in `companies.json`.

Audit is diagnostic only: it does not send email alerts or update the
normal `states/` snapshots.

### `--probe`

``` bash
python notify.py --probe
python notify.py --probe Company_Name
```

Fetches live matching results for the selected company or all enabled
companies and prints the number of matching jobs plus a sample of up to
eight job titles and locations per search.

Unlike a normal monitoring run, probe forces fresh discovery metadata
for the selected company and does not touch the normal job state files.
It is useful for checking whether a company endpoint is returning jobs
and whether the configured search filters are matching anything.

If no jobs match, review the country, location, keyword, title, and
description filters.

Probe does not send job-change emails or update `states/`.

### `--bootstrap`

``` bash
python notify.py --bootstrap companies.csv
```

Attempts to find public job boards for company names supplied in a CSV
file. It is a starting point for adding companies; it does not guarantee
that every company or career portal can be identified.

Review generated entries before adding them to your main configuration.

### `--merge`

``` bash
python notify.py --merge companies.generated.json
```

Merges companies from a generated JSON file into the existing
`companies.json`. Companies whose normalized names already exist are
skipped. Before writing the updated configuration, the current
`companies.json` is backed up as `companies.json.bak`.

Review generated company entries before relying on them for monitoring.

## Understanding the two data directories

### `discovery/` --- how to access a career portal

Contains cached technical metadata that helps JobNotify communicate with
a company's career portal. Examples include platform identifiers,
tenant/site information, board tokens, API endpoints, and available
facets.

Discovery data is an implementation cache. It can be regenerated using
`--refresh-discovery` and is automatically refreshed when it is missing
or stale.

### `states/` --- what jobs were seen last time

Contains the latest JSON snapshot for each company/search combination.
JobNotify compares the current results with these snapshots to detect
changes.

These files are the monitoring baseline. If they are deleted, moved, or
replaced with an old copy, JobNotify may treat the next run as a first
run and establish a new baseline rather than detecting changes against
the previous run.

State files are **snapshots, not a historical archive** of every job
ever seen.

## Syncing `states/` with Google Drive

The `states/` directory can be synced to Google Drive so another person
can inspect or download the latest JSON snapshots without running
JobNotify.

Suggested approach:

1.  Create a folder in Google Drive, for example `JobNotify-States`.
2.  Use Google Drive for Desktop to make that folder available locally,
    or configure the local `states/` directory to sync to Drive.
3.  Ensure JobNotify's `STATE_DIR` points to the directory that is
    actually synced.
4.  Share the Drive folder with the intended person. Viewer access is
    sufficient if they only need to inspect the JSON files.

**Important:**

-   Share/sync only `states/` if the other person only needs the job
    snapshots.
-   Do not share `.env` or email credentials.
-   Avoid having two independent JobNotify processes write to the same
    state files at the same time.
-   If the other person only needs to view results, they do not need to
    run the script or have access to the rest of the repository.

## Scheduling automatic checks

On macOS or Linux, use `cron` to run JobNotify on a schedule. For
example, twice daily at 11:30 AM and 9:30 PM:

``` cron
30 11 * * * /absolute/path/to/JobNotify/.venv/bin/python /absolute/path/to/JobNotify/notify.py >> /absolute/path/to/JobNotify/cron.log 2>&1
30 21 * * * /absolute/path/to/JobNotify/.venv/bin/python /absolute/path/to/JobNotify/notify.py >> /absolute/path/to/JobNotify/cron.log 2>&1
```

Replace the example paths with the actual absolute paths on your
machine. Make sure the cron environment can access the project files and
`.env`.

## Troubleshooting checklist

-   **No email received:** Run `python notify.py --test-email` and
    verify `.env`.
-   **No jobs found:** Run `python notify.py --probe Company_Name` and
    inspect the configured search criteria.
-   **Relevant titles may be missing:** Run
    `python notify.py --audit Company_Name` and inspect
    `audit_results.csv`.
-   **A job was unexpectedly excluded:** Filter the audit CSV by
    company/search and inspect `reason`, `matched_rule`, and
    `description_matches`.
-   **Description exclusions do not appear to work:** Check
    `description_status`. Some portals expose incomplete descriptions,
    and unavailable descriptions are not treated as matches.
-   **Career portal requests are failing:** Run
    `python notify.py --discover Company_Name --refresh-discovery`.
-   **Unexpected first-run behavior:** Check whether the relevant JSON
    file exists in `states/` and whether the script is using the
    intended state directory.
-   **Need to inspect configured companies:** Run
    `python notify.py --list-companies`.

## Limitations

-   JobNotify relies on public career-page endpoints and page
    structures. Companies can change these without notice.
-   Platform support and the amount of job information available vary by
    career portal.
-   Some portals may provide incomplete posting dates, location details,
    or job descriptions.
-   Description-based exclusions can only evaluate the description text
    that JobNotify successfully retrieves.
-   A job disappearing from the returned results is treated as removed
    from that monitor's results; it does not prove that the employer has
    permanently closed the position.
-   Search filters determine what is tracked. Review them periodically,
    especially after changing title or description exclusion rules.
-   The first run establishes a baseline and does not generate an alert
    for all existing postings.
