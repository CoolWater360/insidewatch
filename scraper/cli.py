#!/usr/bin/env python3
"""
Filing ledger CLI — Phase 2.

Commands:
    inspect   <url-or-id>   Show the full status of a filing.
    retry     <url-or-id>   Reset a specific filing to pending for reprocessing.
    retry-all               Reset ALL failed filings (optionally filtered by
                            --max-age-hours) to pending.  Useful after a scraper
                            bug that caused mass failures.
    list-failed             List all filings in 'failed' or 'skipped' status.

Usage:
    python3 -m scraper.cli inspect https://...pdf
    python3 -m scraper.cli inspect 42
    python3 -m scraper.cli retry   https://...pdf
    python3 -m scraper.cli retry-all
    python3 -m scraper.cli retry-all --max-age-hours 24
    python3 -m scraper.cli list-failed
    python3 -m scraper.cli list-failed --status skipped
"""

import argparse
import logging
import os
import sys

# ── Load .env.local if present (local dev convenience) ───────────────────────
_env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

from .db import get_supabase_client
from . import filings as filing_ledger


logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)-7s  %(message)s",
)
logger = logging.getLogger(__name__)


# ── Command implementations ───────────────────────────────────────────────────

def cmd_inspect(client, args) -> int:
    row = filing_ledger.inspect(client, args.target)
    if not row:
        print(f"No filing found for: {args.target!r}")
        return 1

    width = 30
    print()
    print(f"  Filing #{row['id']}")
    print(f"  {'─' * 50}")
    print(f"  {'URL':<{width}} {row['pdf_url']}")
    print(f"  {'Status':<{width}} {row['status']}")
    print(f"  {'Company':<{width}} {row.get('company_name') or '—'}")
    print(f"  {'Filing date':<{width}} {row.get('filing_date') or '—'}")
    print(f"  {'Attempt count':<{width}} {row.get('attempt_count', 0)}/{row.get('max_attempts', 3)}")
    print(f"  {'First seen':<{width}} {row.get('first_seen_at') or '—'}")
    print(f"  {'Last attempted':<{width}} {row.get('last_attempted_at') or '—'}")
    print(f"  {'Next attempt after':<{width}} {row.get('next_attempt_after') or '—'}")
    print(f"  {'Completed at':<{width}} {row.get('completed_at') or '—'}")
    print(f"  {'Transactions inserted':<{width}} {row.get('transactions_inserted') or 0}")
    print(f"  {'Transactions (dedup)':<{width}} {row.get('transactions_skipped_dedup') or 0}")
    print(f"  {'PDF SHA-256':<{width}} {row.get('pdf_sha256') or '—'}")
    print(f"  {'Scraper version':<{width}} {row.get('scraper_version') or '—'}")
    if row.get("last_error"):
        print(f"  {'Last error':<{width}} {row['last_error'][:120]}")
    print()
    return 0


def cmd_retry(client, args) -> int:
    ok = filing_ledger.reset_for_retry(client, args.target)
    if ok:
        print(f"Filing reset to pending: {args.target!r}")
        return 0
    else:
        print(f"No filing found for: {args.target!r}")
        return 1


def cmd_retry_all(client, args) -> int:
    """Reset all failed filings (optionally capped by age) to pending."""
    from datetime import datetime, timedelta, timezone

    query = client.table("filings").select("id, pdf_url, last_attempted_at")

    if args.status:
        query = query.eq("status", args.status)
    else:
        # Default: retry both 'failed' and 'skipped'
        query = query.in_("status", ["failed", "skipped"])

    result = query.execute()
    rows = result.data or []

    if not rows:
        print("No filings match the filter.")
        return 0

    # Optional age filter
    if args.max_age_hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=args.max_age_hours)).isoformat()
        rows = [
            r for r in rows
            if not r.get("last_attempted_at") or r["last_attempted_at"] >= cutoff
        ]

    if not rows:
        print(f"No filings within the last {args.max_age_hours} hours.")
        return 0

    print(f"Resetting {len(rows)} filing(s) to pending...")
    reset = 0
    for row in rows:
        ok = filing_ledger.reset_for_retry(client, str(row["id"]))
        if ok:
            reset += 1
            print(f"  ✓  #{row['id']}  {row['pdf_url'][:80]}")
        else:
            print(f"  ✗  #{row['id']}  (reset failed)")

    print(f"\n{reset}/{len(rows)} filing(s) reset.")
    return 0 if reset == len(rows) else 1


def cmd_list_failed(client, args) -> int:
    status_filter = args.status or ["failed", "skipped"]
    if isinstance(status_filter, str):
        status_filter = [status_filter]

    result = (
        client.table("filings")
        .select("id, status, attempt_count, max_attempts, company_name, filing_date, last_error, pdf_url")
        .in_("status", status_filter)
        .order("id", desc=True)
        .limit(200)
        .execute()
    )
    rows = result.data or []

    if not rows:
        print("No failed or skipped filings found.")
        return 0

    print(f"\n{'ID':>6}  {'Status':<12}  {'Attempts':>8}  {'Company':<25}  {'Date':<12}  URL")
    print("─" * 110)
    for r in rows:
        attempts = f"{r.get('attempt_count',0)}/{r.get('max_attempts',3)}"
        company  = (r.get("company_name") or "")[:24]
        date     = str(r.get("filing_date") or "")[:10]
        url      = r.get("pdf_url", "")[:50]
        print(f"  {r['id']:>4}  {r['status']:<12}  {attempts:>8}  {company:<25}  {date:<12}  {url}")
        if r.get("last_error"):
            print(f"        └─ {r['last_error'][:100]}")

    print(f"\n{len(rows)} row(s) shown.")
    return 0


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m scraper.cli",
        description="Filing ledger management CLI",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # inspect
    pi = sub.add_parser("inspect", help="Show status of a filing by URL or ID")
    pi.add_argument("target", help="PDF URL or filing integer ID")

    # retry
    pr = sub.add_parser("retry", help="Reset a specific filing to pending")
    pr.add_argument("target", help="PDF URL or filing integer ID")

    # retry-all
    pra = sub.add_parser("retry-all", help="Reset all failed/skipped filings to pending")
    pra.add_argument("--max-age-hours", type=float, metavar="N",
                     help="Only reset filings last attempted within the last N hours")
    pra.add_argument("--status", choices=["failed", "skipped"],
                     help="Restrict to a specific status (default: both failed and skipped)")

    # list-failed
    plf = sub.add_parser("list-failed", help="List failed or skipped filings")
    plf.add_argument("--status", choices=["failed", "skipped"],
                     help="Filter by status (default: both)")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        client = get_supabase_client()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if not filing_ledger.table_exists(client):
        print(
            "ERROR: filings table not found. "
            "Apply db/migrations/002_filings_table.sql first.",
            file=sys.stderr,
        )
        sys.exit(1)

    dispatch = {
        "inspect":     cmd_inspect,
        "retry":       cmd_retry,
        "retry-all":   cmd_retry_all,
        "list-failed": cmd_list_failed,
    }
    sys.exit(dispatch[args.command](client, args))


if __name__ == "__main__":
    main()
