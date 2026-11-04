"""--audit: report which jobs your filters keep or eject, and why."""

import csv
import time

from ..companies import select_companies
from ..config import AUDIT_CSV_FILE
from ..discovery import discover_company
from ..fetcher import fetch_jobs
from ..filters import get_description_filter_matches, get_title_filter_reasons
from ..utils.http import create_session
from ..utils.text import clean_description

AUDIT_FIELDS = [
    "audit_timestamp", "company", "search", "job_id", "title", "location",
    "url", "status", "reason", "matched_rule", "description_status",
    "description_matches",
]

# Filters audited independently after fetching everything.
_AUDITED_FILTERS = ("title_any", "title_exclude", "title_preset", "description_exclude")


def _audit_search(session, company, metadata, search, timestamp):
    wide = {k: v for k, v in search.items() if k not in _AUDITED_FILTERS}
    wide["_audit_mode"] = True  # fetch and preserve descriptions

    everything = fetch_jobs(session, company, metadata, wide)

    rows = []
    unavailable = 0

    for jid, job in everything.items():
        title = job.get("title", "")

        reasons, rules = get_title_filter_reasons(title, search)

        description = clean_description(job.get("_description", ""))
        matches, status = get_description_filter_matches(description, search)

        if matches:
            reasons.append("description_exclude_match")
            rules.extend("description_exclude: " + term for term in matches)

        if status == "unavailable" and search.get("description_exclude"):
            unavailable += 1

        rows.append({
            "audit_timestamp": timestamp,
            "company": company["name"],
            "search": search.get("name", ""),
            "job_id": jid,
            "title": title,
            "location": job.get("location", ""),
            "url": job.get("url", ""),
            "status": "EJECTED" if reasons else "KEPT",
            "reason": "; ".join(reasons),
            "matched_rule": "; ".join(rules),
            "description_status": status,
            "description_matches": "; ".join(matches),
        })

    ejected = sum(r["status"] == "EJECTED" for r in rows)

    print(
        f"\n  [{search['name']}] Fetched: {len(everything)} | "
        f"Kept: {len(rows) - ejected} | Ejected: {ejected} | "
        f"Description unavailable: {unavailable}"
    )

    return rows


def run_audit(companies, requested):
    session = create_session()
    audit_rows = []
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    try:
        for company in select_companies(companies, requested):
            print("\n" + "=" * 70)
            print("AUDIT:", company["name"])

            try:
                metadata = discover_company(session, company)

                for search in company.get("searches", []):
                    audit_rows.extend(
                        _audit_search(session, company, metadata, search, timestamp)
                    )
            except Exception as exc:
                print("  AUDIT ERROR:", exc)
    finally:
        session.close()

    # Append so earlier company audits are kept.
    write_header = not (
        AUDIT_CSV_FILE.exists() and AUDIT_CSV_FILE.stat().st_size > 0
    )

    with open(AUDIT_CSV_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)

        if write_header:
            writer.writeheader()

        writer.writerows(audit_rows)

    print(f"\nAudit CSV saved: {AUDIT_CSV_FILE}")
    print("Total audited jobs:", len(audit_rows))
    print("Ejected:", sum(r["status"] == "EJECTED" for r in audit_rows))
