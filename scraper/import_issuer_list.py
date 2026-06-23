"""
Issuer list importer — Phase 6.

Reads a CSV file and upserts rows into the issuer master tables:
  issuers, issuer_aliases, securities.

CSV columns (all optional except canonical_name):
  canonical_name  — required; the official display name
  short_name      — abbreviated name
  isin            — primary equity ISIN (creates a securities row)
  lei             — Legal Entity Identifier
  ticker          — exchange ticker
  market          — trading venue (MTA, AIM Italy, EXM, …)
  sector          — broad sector
  country         — ISO 3166-1 alpha-2 (default IT)
  aliases         — pipe-separated additional name variants

Usage:
    python3 -m scraper.import_issuer_list --file data/seed_issuers_italy.csv [--dry-run]
"""

import argparse
import csv
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


def _load_csv(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _import_row(client, row: dict, *, dry_run: bool) -> str:
    """Import one CSV row.  Returns 'created' | 'skipped' | 'error'."""
    from .issuer_resolver import create_issuer, _add_alias, _add_security

    canonical_name = row.get("canonical_name", "").strip()
    if not canonical_name:
        return "error:empty_name"

    # Parse aliases column (pipe-separated)
    alias_strs = [a.strip() for a in row.get("aliases", "").split("|") if a.strip()]

    isin = row.get("isin", "").strip() or None
    lei = row.get("lei", "").strip() or None
    ticker = row.get("ticker", "").strip() or None
    market = row.get("market", "").strip() or None
    sector = row.get("sector", "").strip() or None
    short_name = row.get("short_name", "").strip() or None
    country = row.get("country", "IT").strip() or "IT"

    # Check if issuer already exists by canonical_name
    existing = (
        client.table("issuers")
        .select("id")
        .eq("canonical_name", canonical_name)
        .limit(1)
        .execute()
    )
    if existing.data:
        issuer_id = existing.data[0]["id"]
        if dry_run:
            logger.info("DRY-RUN  skip (exists) %r → issuer %d", canonical_name, issuer_id)
        else:
            # Ensure new aliases / securities from CSV are added even for existing issuers
            for alias in alias_strs:
                _add_alias(client, issuer_id, alias, "name")
            if isin:
                _add_security(client, issuer_id, isin)
                _add_alias(client, issuer_id, isin, "isin")
            if ticker:
                _add_alias(client, issuer_id, ticker, "ticker")
        return "skipped"

    if dry_run:
        logger.info("DRY-RUN  would create %r (ISIN=%s aliases=%s)", canonical_name, isin, alias_strs)
        return "created"

    try:
        create_issuer(
            client,
            canonical_name=canonical_name,
            short_name=short_name,
            isin=isin,
            lei=lei,
            ticker=ticker,
            market=market,
            sector=sector,
            country=country,
            aliases=[{"alias": a, "alias_type": "name", "source": "seed_csv"} for a in alias_strs],
        )
        logger.info("Created %r (ISIN=%s)", canonical_name, isin)
        return "created"
    except Exception as exc:
        logger.error("Failed to import %r: %s", canonical_name, exc)
        return "error"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Import issuers from CSV into the issuer master")
    parser.add_argument("--file", required=True, help="Path to CSV file")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be imported without writing")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.file):
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2

    rows = _load_csv(args.file)
    if not rows:
        print("No rows found in CSV.", file=sys.stderr)
        return 2

    client = _get_client()

    counts = {"created": 0, "skipped": 0, "error": 0}
    for row in rows:
        outcome = _import_row(client, row, dry_run=args.dry_run)
        key = outcome.split(":")[0]
        counts[key] = counts.get(key, 0) + 1

    prefix = "DRY-RUN " if args.dry_run else ""
    print(
        f"\n{prefix}Import complete: "
        f"{counts['created']} created, "
        f"{counts['skipped']} skipped (already exist), "
        f"{counts.get('error', 0)} errors."
    )
    return 0 if counts.get("error", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
