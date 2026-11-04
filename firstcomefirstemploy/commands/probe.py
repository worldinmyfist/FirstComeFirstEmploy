"""--probe: show live matching jobs without touching saved state."""

from ..companies import select_companies
from ..discovery import discover_company
from ..fetcher import fetch_jobs
from ..utils.http import create_session


def run_probe(companies, requested):
    session = create_session()

    try:
        for company in select_companies(companies, requested):
            print("\n" + "=" * 70)
            print("PROBE:", company["name"])

            try:
                metadata = discover_company(session, company, force=True)

                for search in company.get("searches") or [{"name": "(no filter)"}]:
                    jobs = fetch_jobs(session, company, metadata, search)

                    print(f"\n  [{search['name']}] {len(jobs)} jobs")

                    for job in list(jobs.values())[:8]:
                        print(f"   - {job['title']} | {job.get('location', '')}")

                    if not jobs:
                        print("   (nothing matched; loosen country/title filters)")

            except Exception as exc:
                print("  PROBE ERROR:", exc)
    finally:
        session.close()
