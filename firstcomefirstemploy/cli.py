"""Command-line interface."""

import argparse
import sys

from .commands.audit import run_audit
from .commands.bootstrap import run_bootstrap, run_merge
from .commands.discover import run_discovery
from .commands.monitor import run_monitoring
from .commands.probe import run_probe
from .commands.test_email import send_test_email
from .companies import list_companies, load_companies, select_companies


def build_parser():
    parser = argparse.ArgumentParser(
        description="FirstComeFirstEmploy - multi-portal job monitoring"
    )

    parser.add_argument("--company", metavar="NAME",
                        help="monitor only this company (case-insensitive)")
    parser.add_argument("--dry-run", action="store_true",
                        help="monitor without notifying or saving state")
    parser.add_argument("--list-companies", action="store_true")
    parser.add_argument("--test-email", action="store_true")

    parser.add_argument("--discover", nargs="?", const="*", metavar="COMPANY",
                        help="show discovered platform metadata")
    parser.add_argument("--refresh-discovery", action="store_true",
                        help="ignore the discovery cache")
    parser.add_argument("--probe", nargs="?", const="*", metavar="COMPANY",
                        help="show live matching jobs without saving state")
    parser.add_argument("--audit", nargs="?", const="*", metavar="COMPANY",
                        help="list jobs your filters keep/exclude, with reasons")

    parser.add_argument("--bootstrap", metavar="CSV",
                        help="find public job boards for companies in a CSV")
    parser.add_argument("--merge", metavar="FILE",
                        help="merge a generated companies file into companies.json")

    return parser


def main():
    args = build_parser().parse_args()

    try:
        if args.bootstrap:
            return run_bootstrap(args.bootstrap)

        if args.merge:
            return run_merge(args.merge)

        if args.test_email:
            return send_test_email()

        companies = load_companies()

        if args.probe is not None:
            return run_probe(companies, args.probe)

        if args.audit is not None:
            return run_audit(companies, args.audit)

        if args.list_companies:
            return list_companies(companies)

        if args.discover is not None or args.refresh_discovery:
            return run_discovery(
                companies,
                requested=args.discover,
                refresh=args.refresh_discovery,
            )

        selected = select_companies(companies, requested=args.company)

        if not selected:
            raise RuntimeError("No enabled companies configured.")

        run_monitoring(selected, dry_run=args.dry_run)

    except Exception as exc:
        print("\nFATAL ERROR:", exc)
        sys.exit(1)
