"""
Unmatched issuer review workflow — Phase 6.

Lists pending entries from unmatched_issuers and allows operators to resolve
or reject each one via a simple CLI.

Usage:
    # List all pending entries
    python3 -m scraper.review_unmatched_issuers

    # Resolve entry #5 to an existing issuer (id=12)
    python3 -m scraper.review_unmatched_issuers --resolve 5 --to 12

    # Create a new issuer and immediately resolve entry #5 to it
    python3 -m scraper.review_unmatched_issuers --create-and-resolve 5 \\
        --canonical-name "Foo S.p.A." --isin IT0001234567 --short-name "Foo"

    # Reject entry #5 (parse error, test data, etc.)
    python3 -m scraper.review_unmatched_issuers --reject 5

    # List resolved / rejected entries
    python3 -m scraper.review_unmatched_issuers --status resolved
    python3 -m scraper.review_unmatched_issuers --status rejected
"""

import argparse
import logging
import os
import sys
from typing import Optional, List

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_client():
    from supabase import create_client
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print(
            "ERROR: SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) and "
            "SUPABASE_SERVICE_ROLE_KEY must be set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return create_client(url, key)


def _cmd_list(client, status: str) -> None:
    from .issuer_resolver import get_unmatched
    rows = get_unmatched(client, status=status, limit=100)

    if not rows:
        print(f"No {status} entries in unmatched_issuers.")
        return

    print(f"\nUnmatched issuers — status: {status}  ({len(rows)} rows)\n")
    print(f"{'ID':>5}  {'raw_name':<40}  {'raw_isin':<14}  {'suggestion':>12}  discovered_at")
    print("─" * 90)
    for r in rows:
        sugg = str(r.get("suggestion_issuer_id") or "")
        print(
            f"{r['id']:>5}  {(r['raw_name'] or '')[:40]:<40}  "
            f"{(r.get('raw_isin') or ''):<14}  {sugg:>12}  "
            f"{(r.get('discovered_at') or '')[:19]}"
        )
    print()
    print("To resolve:  --resolve <id> --to <issuer_id>")
    print("To create:   --create-and-resolve <id> --canonical-name '...' [--isin ...]")
    print("To reject:   --reject <id>")
    print()


def _cmd_resolve(client, unmatched_id: int, issuer_id: int) -> None:
    from .issuer_resolver import mark_resolved
    mark_resolved(client, unmatched_id, issuer_id)
    print(f"Resolved unmatched #{unmatched_id} → issuer {issuer_id}")

    # Also link any companies rows that carry the same raw_name
    row = (
        client.table("unmatched_issuers")
        .select("raw_name")
        .eq("id", unmatched_id)
        .limit(1)
        .execute()
    )
    if row.data:
        raw_name = row.data[0]["raw_name"]
        _backfill_company_link(client, raw_name, issuer_id)


def _cmd_create_and_resolve(
    client,
    unmatched_id: int,
    canonical_name: str,
    isin: Optional[str],
    short_name: Optional[str],
    market: Optional[str],
    sector: Optional[str],
    country: str,
) -> None:
    from .issuer_resolver import create_issuer, mark_resolved

    issuer_id = create_issuer(
        client,
        canonical_name=canonical_name,
        short_name=short_name,
        isin=isin,
        market=market,
        sector=sector,
        country=country,
    )
    print(f"Created issuer {issuer_id}: {canonical_name!r}")

    mark_resolved(client, unmatched_id, issuer_id)
    print(f"Resolved unmatched #{unmatched_id} → issuer {issuer_id}")

    # Backfill: link any companies row with the raw_name to the new issuer
    row = (
        client.table("unmatched_issuers")
        .select("raw_name")
        .eq("id", unmatched_id)
        .limit(1)
        .execute()
    )
    if row.data:
        raw_name = row.data[0]["raw_name"]
        _backfill_company_link(client, raw_name, issuer_id)


def _cmd_reject(client, unmatched_id: int) -> None:
    from .issuer_resolver import mark_rejected
    mark_rejected(client, unmatched_id)
    print(f"Rejected unmatched #{unmatched_id}")


def _backfill_company_link(client, raw_name: str, issuer_id: int) -> None:
    """Link existing companies rows with this name to the resolved issuer."""
    from .issuer_resolver import link_company_to_issuer
    companies = (
        client.table("companies")
        .select("id")
        .ilike("name", raw_name)
        .execute()
    )
    for c in companies.data or []:
        link_company_to_issuer(client, c["id"], issuer_id)
        logger.info("Linked company %d → issuer %d", c["id"], issuer_id)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review and resolve unmatched issuers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("--status", default="pending",
                        choices=["pending", "resolved", "rejected"],
                        help="Filter entries by status (default: pending)")

    parser.add_argument("--resolve", type=int, metavar="ID",
                        help="Resolve unmatched entry ID")
    parser.add_argument("--to", type=int, metavar="ISSUER_ID",
                        help="Issuer ID to resolve to (used with --resolve)")

    parser.add_argument("--create-and-resolve", type=int, metavar="ID",
                        help="Create a new issuer and resolve unmatched entry ID to it")
    parser.add_argument("--canonical-name", help="Canonical name for new issuer")
    parser.add_argument("--isin", help="Primary ISIN for new issuer")
    parser.add_argument("--short-name", help="Short name for new issuer")
    parser.add_argument("--market", help="Market for new issuer")
    parser.add_argument("--sector", help="Sector for new issuer")
    parser.add_argument("--country", default="IT", help="Country for new issuer (default IT)")

    parser.add_argument("--reject", type=int, metavar="ID",
                        help="Reject unmatched entry ID")

    args = parser.parse_args(argv)
    client = _get_client()

    if args.resolve is not None:
        if args.to is None:
            print("ERROR: --resolve requires --to <issuer_id>", file=sys.stderr)
            return 2
        _cmd_resolve(client, args.resolve, args.to)

    elif args.create_and_resolve is not None:
        if not args.canonical_name:
            print("ERROR: --create-and-resolve requires --canonical-name", file=sys.stderr)
            return 2
        _cmd_create_and_resolve(
            client,
            args.create_and_resolve,
            canonical_name=args.canonical_name,
            isin=args.isin,
            short_name=args.short_name,
            market=args.market,
            sector=args.sector,
            country=args.country,
        )

    elif args.reject is not None:
        _cmd_reject(client, args.reject)

    else:
        _cmd_list(client, args.status)

    return 0


if __name__ == "__main__":
    sys.exit(main())
