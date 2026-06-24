#!/usr/bin/env python3
"""
Filing ledger CLI — Phase 2 / Phase 11.

Commands:
    inspect   <url-or-id>       Show the full status of a filing.
    retry     <url-or-id>       Reset a specific filing to pending for reprocessing.
    retry-all                   Reset ALL failed filings (optionally filtered by
                                --max-age-hours) to pending.  Useful after a scraper
                                bug that caused mass failures.
    list-failed                 List all filings in 'failed' or 'skipped' status.
    reap-stale                  Recover filings stuck in_progress by a crashed worker.
    check-pdf <url-or-id>       Download the PDF and compare its SHA-256 against the
                                stored value. Reports: unchanged | CHANGED | unavailable.
                                NOTE: changed-PDF re-processing is not automatic; use
                                `retry <url-or-id>` if re-parse is needed.
    verify-storage-lineage      Report completed filings missing storage evidence
                                (storage_path, file_size_bytes, or pdf_sha256).
                                Use --fix to reset them to 'failed' for re-processing.
    backfill-latency            Report latency timestamp coverage across all pipeline
                                stages.  Identifies filings that lack one or more of
                                the six latency columns and breaks them down by stage.
                                Historical filings (pre-014) will show NULL stored_utc —
                                this is expected and does not indicate data loss.

Usage:
    python3 -m scraper.cli inspect https://...pdf
    python3 -m scraper.cli inspect 42
    python3 -m scraper.cli retry   https://...pdf
    python3 -m scraper.cli retry-all
    python3 -m scraper.cli retry-all --max-age-hours 24
    python3 -m scraper.cli list-failed
    python3 -m scraper.cli list-failed --status skipped
    python3 -m scraper.cli reap-stale
    python3 -m scraper.cli reap-stale --stale-minutes 60
    python3 -m scraper.cli check-pdf https://...pdf
    python3 -m scraper.cli check-pdf 42
    python3 -m scraper.cli verify-storage-lineage
    python3 -m scraper.cli verify-storage-lineage --fix
    python3 -m scraper.cli backfill-latency
    python3 -m scraper.cli backfill-latency --limit 200
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
from .latency import LATENCY_COLUMNS, PIPELINE_STAGES, aggregate_stage_stats, coverage_report


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


def cmd_reap_stale(client, args) -> int:
    """Recover filings stuck in_progress by a crashed worker."""
    stale_minutes = args.stale_minutes or filing_ledger._stale_minutes()
    print(f"Reaping in_progress filings older than {stale_minutes} minutes...")
    reaped = filing_ledger.reap_stale_filings(client, stale_minutes=stale_minutes)
    if not reaped:
        print("Nothing stale found.")
        return 0

    print(f"\n{'ID':>6}  {'New status':<10}  {'Attempts':>8}  URL")
    print("─" * 90)
    for r in reaped:
        attempts = f"{r.get('attempt_count',0)}/{r.get('max_attempts',3)}"
        print(f"  {r['id']:>4}  {r['status']:<10}  {attempts:>8}  {str(r.get('pdf_url',''))[:60]}")

    print(f"\n{len(reaped)} filing(s) reaped.")
    return 0


def cmd_check_pdf(client, args) -> int:
    """
    Download a filing's PDF and compare its SHA-256 against the stored value.

    Changed-PDF re-processing is NOT automatic. This command is a manual
    diagnostic only. To re-parse a changed PDF: `retry <url-or-id>`.

    Exit codes:
      0 — unchanged
      1 — filing not found or no hash on record
      2 — CHANGED (stored hash ≠ live hash)
      3 — unavailable (download failed)
    """
    import hashlib
    import requests as req

    row = filing_ledger.inspect(client, args.target)
    if not row:
        print(f"No filing found for: {args.target!r}")
        return 1

    stored_sha256 = row.get("pdf_sha256")
    if not stored_sha256:
        print(
            f"Filing #{row['id']}: no pdf_sha256 on record.\n"
            f"  status={row.get('status')}  — filing has not been successfully completed."
        )
        return 1

    url = row["pdf_url"]
    print(f"Checking: {url}")
    try:
        resp = req.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        fresh_sha256 = hashlib.sha256(resp.content).hexdigest()
    except req.Timeout:
        print(f"unavailable  #{row['id']}  (timeout after 30 s)")
        return 3
    except Exception as exc:
        print(f"unavailable  #{row['id']}  ({exc})")
        return 3

    if fresh_sha256 == stored_sha256:
        print(f"unchanged    #{row['id']}  SHA-256: {stored_sha256[:24]}…")
        return 0

    print(f"CHANGED      #{row['id']}")
    print(f"  stored:  {stored_sha256}")
    print(f"  current: {fresh_sha256}")
    print()
    print("NOTE: changed-PDF re-processing is NOT automatic.")
    print(f"  To re-parse: python3 -m scraper.cli retry {args.target!r}")
    return 2


def cmd_verify_storage_lineage(client, args) -> int:
    """
    Report (and optionally fix) completed filings that lack storage evidence.

    Applies to filings processed BEFORE the hardening patch (commit 320f17c),
    when record_storage() was non-fatal and could silently fail.  After the
    patch, no new completed filing can lack storage_path, file_size_bytes, or
    pdf_sha256 — this command verifies and remediates the historical backlog.

    Without --fix:  list every offending filing and exit 1 if any are found.
    With    --fix:  reset each offending filing to 'failed' so the next scraper
                    run re-downloads, re-stores, and re-parses it.  Re-uploading
                    the same PDF to the same SHA-256-derived path is idempotent.

    Exit codes:
      0 — no incomplete completed filings found
      1 — incomplete filings found (--fix: after resetting them to failed)
    """
    result = (
        client.table("filings")
        .select("id, pdf_url, status, storage_path, file_size_bytes, pdf_sha256, completed_at")
        .eq("status", "completed")
        .or_("storage_path.is.null,file_size_bytes.is.null,pdf_sha256.is.null")
        .order("id")
        .execute()
    )
    rows = result.data or []

    if not rows:
        print("Storage-lineage invariant OK: all completed filings have evidence fields.")
        return 0

    print(f"\nFound {len(rows)} completed filing(s) missing storage evidence:\n")
    print(f"  {'ID':>6}  {'storage_path':<8}  {'file_size':>9}  {'sha256':>6}  URL")
    print("  " + "─" * 80)
    for r in rows:
        sp  = "present" if r.get("storage_path") else "MISSING"
        fs  = "present" if r.get("file_size_bytes") is not None else "MISSING"
        sha = "present" if r.get("pdf_sha256") else "MISSING"
        url = str(r.get("pdf_url") or "")[:60]
        print(f"  {r['id']:>6}  {sp:<12}  {fs:>9}  {sha:>6}  {url}")

    if not args.fix:
        print(
            f"\n{len(rows)} filing(s) need remediation. "
            "Re-run with --fix to reset them to 'failed' for re-processing."
        )
        return 1

    print(f"\nResetting {len(rows)} filing(s) to 'failed' for re-processing...")
    fixed = 0
    for r in rows:
        ok = filing_ledger.reset_for_retry(client, str(r["id"]))
        if ok:
            fixed += 1
            print(f"  ✓  #{r['id']}  reset to failed")
        else:
            print(f"  ✗  #{r['id']}  reset failed (already in unexpected state)")

    print(f"\n{fixed}/{len(rows)} filing(s) reset. Run the scraper to re-process them.")
    return 0 if fixed == len(rows) else 1


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


def cmd_backfill_latency(client, args) -> int:
    """
    Report latency-timestamp coverage for completed filings.

    Shows how many filings are missing each timestamp column, which stages
    are computable, and which are blocked by NULL timestamps.  Historical
    filings (before migration 014) will show stored_utc=NULL for the
    download→stored and stored→parsed stages; this is expected.
    """
    limit = getattr(args, "limit", 500) or 500

    result = (
        client.table("filings")
        .select(LATENCY_COLUMNS)
        .eq("status", "completed")
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    rows = result.data or []

    if not rows:
        print("No completed filings found.")
        return 0

    print(f"\nLatency coverage across {len(rows)} most-recent completed filing(s)\n")

    # Per-column coverage
    ts_cols = LATENCY_COLUMNS.split(",")
    print(f"  {'Column':<30}  {'Present':>8}  {'Missing':>8}  {'Coverage':>10}")
    print("  " + "─" * 60)
    for col in ts_cols:
        present = sum(1 for r in rows if r.get(col))
        missing = len(rows) - present
        pct     = 100 * present / len(rows)
        flag    = "  ← expected NULL (pre-014)" if col == "stored_utc" and missing else ""
        print(f"  {col:<30}  {present:>8}  {missing:>8}  {pct:>9.1f}%{flag}")

    # Per-stage coverage
    cov = coverage_report(rows)
    print(f"\n  {'Stage':<30}  {'Coverage':>10}  {'n filings with both ts':>24}")
    print("  " + "─" * 70)
    for name, _, _ in PIPELINE_STAGES:
        frac    = cov.get(name, 0.0)
        n_both  = round(frac * len(rows))
        print(f"  {name:<30}  {frac * 100:>9.1f}%  {n_both:>24}")

    # Stage stats for stages with any data
    stats = aggregate_stage_stats(rows)
    any_data = {k: v for k, v in stats.items() if v["n"] > 0}
    if any_data:
        print(f"\n  Stage latency (seconds)  —  avg / p50 / p95  [n]")
        print("  " + "─" * 60)
        for name, s in any_data.items():
            print(f"  {name:<30}  {s['avg']:>8.1f} / {s['p50']:>8.1f} / {s['p95']:>8.1f}  [{s['n']}]")

    print()
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

    # reap-stale
    prs = sub.add_parser("reap-stale",
                          help="Recover in_progress filings abandoned by a crashed worker")
    prs.add_argument("--stale-minutes", type=int, metavar="N",
                     help=f"Override the stale threshold (default: {filing_ledger._STALE_CLAIM_MINUTES} min)")

    # check-pdf
    pcp = sub.add_parser("check-pdf",
                          help="Compare a filing's live PDF SHA-256 against the stored value")
    pcp.add_argument("target", help="PDF URL or filing integer ID")

    # verify-storage-lineage
    pvsl = sub.add_parser(
        "verify-storage-lineage",
        help="Report completed filings missing storage evidence (storage_path / file_size_bytes / pdf_sha256)",
    )
    pvsl.add_argument(
        "--fix", action="store_true",
        help="Reset offending filings to 'failed' so the scraper re-processes them",
    )

    # backfill-latency
    pbl = sub.add_parser(
        "backfill-latency",
        help="Report latency timestamp coverage across all pipeline stages",
    )
    pbl.add_argument(
        "--limit", type=int, default=500, metavar="N",
        help="Number of most-recent completed filings to analyse (default: 500)",
    )

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
        "inspect":                  cmd_inspect,
        "retry":                    cmd_retry,
        "retry-all":                cmd_retry_all,
        "list-failed":              cmd_list_failed,
        "reap-stale":               cmd_reap_stale,
        "check-pdf":                cmd_check_pdf,
        "verify-storage-lineage":   cmd_verify_storage_lineage,
        "backfill-latency":         cmd_backfill_latency,
    }
    sys.exit(dispatch[args.command](client, args))


if __name__ == "__main__":
    main()
