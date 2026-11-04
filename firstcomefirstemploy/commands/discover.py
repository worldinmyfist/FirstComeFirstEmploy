"""--discover: show what was discovered for each company."""

from ..companies import select_companies
from ..discovery import discover_company
from ..platforms import get_platform
from ..utils.http import create_session


def run_discovery(companies, requested=None, refresh=False):
    session = create_session()
    failures = []

    try:
        for company in select_companies(companies, requested):
            try:
                metadata = discover_company(session, company, force=refresh)

                print("\nCompany:", company["name"])
                print("Platform:", metadata["platform"])
                get_platform(metadata["platform"]).describe(metadata)

            except Exception as exc:
                failures.append(company["name"])
                print(f"\nDISCOVERY ERROR: {company['name']}: {exc}")
    finally:
        session.close()

    if failures:
        raise RuntimeError("Discovery failed for: " + ", ".join(failures))
