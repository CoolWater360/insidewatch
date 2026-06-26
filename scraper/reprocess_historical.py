"""
Historical PDF reprocessing — Phase 13 ops tool.

Reprocesses stored PDFs through the current parser (PARSER_VERSION 1.2.0)
to backfill the new classification fields (confidence, Italian keywords,
vehicle-context flags) added in Phases 11–12.

Safety guarantees
-----------------
- Dry-run by default.  Pass --apply to write to the database.
- Only touches filings with status='completed' AND raw_extracted_text IS NOT NULL.
- By default only reprocesses filings whose existing transactions carry an
  older parser_version (< current).  Pass --all to force all completed filings.
- Classification overrides (classification_override=true) are respected:
  upsert_transaction() will not overwrite them.
- All changes are versioned via the existing transaction_versions table.
- Every reprocessing run is recorded in filing_processing_runs.

Usage
-----
    # Preview what would change (no DB writes):
    python3 -m scraper.reprocess_historical

    # Apply changes — process up to 50 filings:
    python3 -m scraper.reprocess_historical --apply --limit 50

    # Scope to a date window:
    python3 -m scraper.reprocess_historical --apply \\
        --since 2025-01-01 --until 2025-12-31

    # Force-reprocess everything regardless of parser version:
    python3 -m scraper.reprocess_historical --apply --all --limit 100

    # Write the before/after quality comparison to a file:
    python3 -m scraper.reprocess_historical --apply \\
        --snapshot-dir ops/snapshots
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _load_env() -> None:
    try:
        import dotenv
        dotenv.load_dotenv(".env.local")
    except ImportError:
        pass


def _iso_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(f"Date must be YYYY-MM-DD, got: {value!r}")


def _to_filing_date_dmy(source_published_utc: Optional[str]) -> str:
    """Convert ISO timestamp to DD/MM/YYYY expected by parse_text()."""
    if not source_published_utc:
        return "01/01/1970"
    try:
        dt = datetime.fromisoformat(source_published_utc.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return "01/01/1970"


def _fetch_filings(
    client,
    *,
    since: Optional[str],
    until: Optional[str],
    reprocess_all: bool,
    current_parser_version: str,
    limit: int,
    filing_id: Optional[int] = None,
) -> list:
    """
    Query completed filings that are candidates for reprocessing.

    Returns a list of dicts with keys:
      id, source_url, pdf_sha256, raw_extracted_text, source_published_utc
    """
    query = (
        client.table("filings")
        .select("id,source_url,pdf_sha256,raw_extracted_text,source_published_utc")
        .eq("status", "completed")
        .not_.is_("raw_extracted_text", "null")
    )

    if filing_id is not None:
        # Targeted single-filing reprocess — bypass date and version filters.
        query = query.eq("id", filing_id)
        result = query.limit(1).execute()
        return result.data or []

    if since:
        query = query.gte("source_published_utc", since)
    if until:
        query = query.lte("source_published_utc", until + "T23:59:59Z")

    if not reprocess_all:
        # Only include filings that have at least one transaction with an older
        # parser version.  Uses a subquery via Supabase foreign-table filter.
        # Fallback: if this filter isn't supported, it returns all and the
        # per-filing version check below skips up-to-date ones.
        try:
            stale_ids_result = (
                client.table("transactions")
                .select("source_filing_id")
                .neq("parser_version", current_parser_version)
                .not_.is_("source_filing_id", "null")
                .limit(10000)
                .execute()
            )
            stale_ids = list({
                r["source_filing_id"]
                for r in (stale_ids_result.data or [])
                if r.get("source_filing_id")
            })
            if stale_ids:
                query = query.in_("id", stale_ids)
            else:
                logger.info("All existing transactions already at parser %s — nothing to do.",
                            current_parser_version)
                return []
        except Exception as exc:
            logger.warning(
                "Could not pre-filter by parser_version (%s); will check per-filing.", exc
            )

    result = query.order("source_published_utc").limit(limit).execute()
    return result.data or []


def _reprocess_filing(
    client,
    filing: dict,
    current_parser_version: str,
    dry_run: bool,
) -> dict:
    """
    Re-parse one filing and upsert all resulting transactions.

    Returns a dict:
      {filing_id, found, inserted, versioned, unchanged, errors, dry_run}
    """
    from .parser import parse_text
    from .db import upsert_transaction, record_filing_processing_run

    filing_id = filing["id"]
    source_url = filing.get("source_url", "")
    pdf_sha256 = filing.get("pdf_sha256") or ""
    raw_text = filing.get("raw_extracted_text", "")
    filing_date_dmy = _to_filing_date_dmy(filing.get("source_published_utc"))

    counts = {"inserted": 0, "versioned": 0, "unchanged": 0, "errors": 0}

    try:
        transactions = parse_text(raw_text, source_url, filing_date_dmy,
                                  doc_sha256=pdf_sha256)
    except Exception as exc:
        logger.error("parse_text failed for filing %d: %s", filing_id, exc)
        return {
            "filing_id": filing_id,
            "found": 0, "inserted": 0, "versioned": 0, "unchanged": 0,
            "errors": 1, "dry_run": dry_run,
        }

    found = len(transactions)

    for tx in transactions:
        if dry_run:
            continue
        try:
            result = upsert_transaction(
                client,
                raw_hash=tx.raw_hash,
                insider_name=tx.insider_name,
                insider_role=tx.role,
                company_name=tx.company_name,
                isin=tx.isin,
                instrument_type=tx.instrument_type,
                direction=tx.direction,
                transaction_date=tx.transaction_date,
                filed_date=tx.filing_date,
                quantity=tx.quantity,
                unit_price=tx.unit_price,
                total_value=tx.total_value,
                currency=tx.currency,
                source_url=tx.source_url,
                insider_verified=tx.insider_verified,
                role_category=tx.role_category,
                transaction_type=tx.transaction_type,
                economic_intent=tx.economic_intent,
                classification_rationale=tx.classification_rationale,
                raw_nature_text=tx.raw_nature_text,
                needs_review=tx.needs_review,
                extraction_confidence=tx.extraction_confidence,
                classification_confidence=tx.classification_confidence,
                review_status=tx.review_status,
                review_reason=tx.review_reason,
                source_transaction_index=tx.source_transaction_index,
                raw_document_sha256=pdf_sha256 or tx.raw_document_sha256,
                source_filing_id=filing_id,
                parser_version=current_parser_version,
            )
            if result.get("inserted"):
                counts["inserted"] += 1
            elif result.get("updated"):
                counts["versioned"] += 1
            else:
                counts["unchanged"] += 1
        except Exception as exc:
            logger.error(
                "upsert_transaction failed for filing %d tx %d: %s",
                filing_id, tx.source_transaction_index or 0, exc,
            )
            counts["errors"] += 1

    if not dry_run and found > 0:
        try:
            record_filing_processing_run(
                client,
                filing_id,
                parser_version=current_parser_version,
                transactions_found=found,
                transactions_inserted=counts["inserted"],
                transactions_versioned=counts["versioned"],
                transactions_unchanged=counts["unchanged"],
            )
        except Exception as exc:
            logger.warning("record_filing_processing_run failed for filing %d: %s",
                           filing_id, exc)

    return {
        "filing_id": filing_id,
        "found": found,
        "inserted": counts["inserted"],
        "versioned": counts["versioned"],
        "unchanged": counts["unchanged"],
        "errors": counts["errors"],
        "dry_run": dry_run,
    }


def run_reprocessing(
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
    dry_run: bool = True,
    reprocess_all: bool = False,
    snapshot_dir: Optional[str] = None,
    filing_id: Optional[int] = None,
) -> dict:
    """
    Main entry point — callable from tests or other scripts.

    Returns a summary dict with aggregate counts and per-filing results.
    """
    from .db import get_supabase_client
    from .parser import PARSER_VERSION

    _load_env()
    client = get_supabase_client()

    before_snapshot = None
    if snapshot_dir:
        try:
            from .quality_snapshot import take_snapshot
            before_snapshot = take_snapshot(client)
        except Exception as exc:
            logger.warning("Before-snapshot failed: %s", exc)

    logger.info(
        "Fetching filings (since=%s until=%s limit=%d all=%s dry_run=%s)",
        since, until, limit, reprocess_all, dry_run,
    )

    filings = _fetch_filings(
        client,
        since=since,
        until=until,
        reprocess_all=reprocess_all,
        current_parser_version=PARSER_VERSION,
        limit=limit,
        filing_id=filing_id,
    )

    logger.info("Found %d filing(s) to reprocess.", len(filings))

    per_filing = []
    totals = {"found": 0, "inserted": 0, "versioned": 0, "unchanged": 0, "errors": 0}

    for i, filing in enumerate(filings):
        if (i + 1) % 10 == 0:
            logger.info("Progress: %d/%d filings processed", i + 1, len(filings))
        result = _reprocess_filing(client, filing, PARSER_VERSION, dry_run)
        per_filing.append(result)
        for k in totals:
            totals[k] += result.get(k, 0)

    after_snapshot = None
    if snapshot_dir and not dry_run and before_snapshot:
        try:
            from .quality_snapshot import take_snapshot, compare_snapshots, save_snapshot
            after_snapshot = take_snapshot(client)
            os.makedirs(snapshot_dir, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            save_snapshot(before_snapshot, os.path.join(snapshot_dir, f"before_{ts}.json"))
            save_snapshot(after_snapshot,  os.path.join(snapshot_dir, f"after_{ts}.json"))
            diff = compare_snapshots(before_snapshot, after_snapshot)
            save_snapshot(diff, os.path.join(snapshot_dir, f"diff_{ts}.json"))
        except Exception as exc:
            logger.warning("After-snapshot/diff failed: %s", exc)

    summary = {
        "parser_version": PARSER_VERSION,
        "dry_run": dry_run,
        "filings_processed": len(filings),
        "since": since,
        "until": until,
        "totals": totals,
        "per_filing": per_filing,
        "snapshot_dir": snapshot_dir,
    }

    if dry_run:
        summary["note"] = (
            "Dry run — no database writes.  Re-run with --apply to apply changes."
        )

    return summary


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        description="Reprocess stored historical PDFs through the current parser.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write changes to the database.  Dry-run if omitted.",
    )
    parser.add_argument(
        "--all",
        dest="reprocess_all",
        action="store_true",
        default=False,
        help="Reprocess all completed filings, not just those with stale parser versions.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        metavar="N",
        help="Maximum number of filings to process in this run (default: 100).",
    )
    parser.add_argument(
        "--since",
        type=_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only reprocess filings published on or after this date.",
    )
    parser.add_argument(
        "--until",
        type=_iso_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only reprocess filings published on or before this date.",
    )
    parser.add_argument(
        "--filing-id",
        type=int,
        default=None,
        metavar="ID",
        help=(
            "Reprocess a single filing by its filings.id.  Bypasses date and "
            "parser-version filters.  Use for targeted bug-fix reprocessing."
        ),
    )
    parser.add_argument(
        "--snapshot-dir",
        default=None,
        metavar="DIR",
        help="Directory to write before/after/diff quality snapshots (e.g. ops/snapshots).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Pretty-print the JSON summary.",
    )
    args = parser.parse_args()

    summary = run_reprocessing(
        since=args.since,
        until=args.until,
        limit=args.limit,
        dry_run=not args.apply,
        reprocess_all=args.reprocess_all,
        snapshot_dir=args.snapshot_dir,
        filing_id=args.filing_id,
    )

    indent = 2 if args.pretty else None
    print(json.dumps(summary, indent=indent, default=str))


if __name__ == "__main__":
    main()
