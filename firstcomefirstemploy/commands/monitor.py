"""Default command: check every enabled company and notify on changes."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import MAX_WORKERS
from ..discovery import discover_company
from ..fetcher import fetch_jobs
from ..notifiers import notify_all
from ..notifiers.email_notifier import build_email
from ..state import (
    count_changes,
    detect_changes,
    load_previous_jobs,
    looks_suspicious,
    save_jobs,
)
from ..utils.http import create_session


def monitor_one_company(company):
    """Check one company with its own HTTP session.

    Results are returned to the main thread so state files and
    notifications are handled centrally.
    """
    session = create_session()
    searches = company.get("searches", [])

    result = {
        "changes": {},
        "pending_states": {},
        "successful": 0,
        "failed": 0,
    }

    try:
        print("\n" + "=" * 70)
        print("COMPANY:", company["name"])

        if not searches:
            print("No searches configured.")
            return result

        try:
            metadata = discover_company(session, company)
        except Exception as exc:
            print("DISCOVERY ERROR:", exc)
            result["failed"] += len(searches)
            return result

        for search in searches:
            monitor_name = f"{company['name']}_{search['name']}"
            print("\nChecking:", monitor_name)

            try:
                current = fetch_jobs(session, company, metadata, search)
                previous = load_previous_jobs(monitor_name)

                print("Jobs found:", len(current))

                if previous is None:
                    print("Initial baseline created.")
                else:
                    if looks_suspicious(previous, current):
                        raise RuntimeError(
                            "Suspiciously low job count. Previous state preserved."
                        )

                    changes = detect_changes(previous, current)
                    count = count_changes(changes)
                    print("Changes:", count)

                    if count:
                        result["changes"][monitor_name] = changes

                result["pending_states"][monitor_name] = current
                result["successful"] += 1

            except Exception as exc:
                result["failed"] += 1
                print("ERROR:", exc)

        return result

    finally:
        session.close()


def run_monitoring(companies, dry_run=False):
    changes_by_monitor = {}
    pending_states = {}
    successful = 0
    failed = 0
    total = sum(len(c.get("searches", [])) for c in companies)

    workers = max(1, min(MAX_WORKERS, len(companies)))
    print(f"\nRunning up to {workers} companies in parallel.")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(monitor_one_company, c): c for c in companies
        }

        for future in as_completed(futures):
            company = futures[future]

            try:
                result = future.result()
            except Exception as exc:
                # One company's unexpected failure must not stop the others.
                failed += len(company.get("searches", []))
                print(f"\nUNEXPECTED ERROR for {company['name']}: {exc}")
                continue

            changes_by_monitor.update(result["changes"])
            pending_states.update(result["pending_states"])
            successful += result["successful"]
            failed += result["failed"]

    if changes_by_monitor:
        if dry_run:
            print("\nDRY RUN: Changes not sent and states not saved.")
            print(build_email(changes_by_monitor))
        else:
            notify_all(changes_by_monitor)
    else:
        print("\nNo changes detected.")

    # Save state only after every worker finished and notifications went out.
    if not dry_run:
        for name, jobs in pending_states.items():
            save_jobs(name, jobs)

    print("\nMONITORING SUMMARY")
    print("=" * 70)
    print("Companies:", len(companies))
    print("Parallel workers:", workers)
    print("Total searches:", total)
    print("Successful:", successful)
    print("Failed:", failed)
    print("Changed monitors:", len(changes_by_monitor))

    if failed:
        sys.exit(1)
