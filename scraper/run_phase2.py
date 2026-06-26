#!/usr/bin/env python3
"""
Phase 2 runner: crawl all FTSE MIB companies, store in Supabase, deduplicate.

Usage:
    python3 -m scraper.run_phase2                         # crawl all companies
    python3 -m scraper.run_phase2 --tier 1 --workers 3   # Tier 1, 3 concurrent
    python3 -m scraper.run_phase2 --limit 5              # crawl first 5
    python3 -m scraper.run_phase2 --company "Eni"        # crawl one company
"""

import argparse
import hashlib
import logging
import os
import requests
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Resolve project root regardless of cwd
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_CSV = os.path.join(_PROJECT_ROOT, "db", "seed_companies.csv")

from .alerts import AlertPayload, dispatch
from .db import get_supabase_client, load_seed_companies, upsert_transaction
from .fetcher import BlockedError, _make_session, iter_company_listings, download_pdf
from . import filings as filing_ledger
from . import storage as doc_storage
from .parser import parse_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _crawl_company(
    company_name: str,
    company_tier: int,
    client,
    polite_delay: float,
    max_pdfs: int,
    use_ledger: bool = False,
    storage_backend=None,
) -> dict:
    """
    Crawl a single company in its own requests.Session (thread-safe).
    Returns a stats dict; never raises — errors are captured in the return value.

    use_ledger: when True, registers each PDF in the filings table and uses
    filing-level dedup (already-completed filings are skipped without downloading).
    When False, falls back to transaction-level dedup only (pre-Phase-2 behaviour).
    """
    stats = {
        "pdfs": 0,
        "parsed": 0,
        "inserted": 0,
        "dedup": 0,
        "filing_skipped": 0,
        "errors": 0,
        "skipped": False,
        "skip_reason": None,
    }

    session = _make_session()

    try:
        first_letter = company_name[0].upper()
        for row in iter_company_listings(
            session,
            letter=first_letter,
            company_name=company_name,
            max_pdfs=max_pdfs,
            polite_delay=polite_delay,
        ):
            stats["pdfs"] += 1
            filing_id: int | None = None
            attempt_count = 0
            max_attempts  = 3

            # ── Ledger: register filing and claim it atomically ───────────────
            if use_ledger:
                try:
                    filing = filing_ledger.register_filing(
                        client,
                        pdf_url=row.pdf_url,
                        filing_date=row.filing_date,
                        company_name=company_name,
                    )
                    filing_id    = filing["id"]
                    max_attempts = filing.get("max_attempts", 3)

                    if filing["status"] == "completed":
                        stats["filing_skipped"] += 1
                        logger.debug("Filing already completed, skipping: %s", row.pdf_url)
                        continue

                    if not filing_ledger.is_eligible(filing):
                        stats["filing_skipped"] += 1
                        continue

                    # Atomic claim: returns None if another worker won the race.
                    claimed = filing_ledger.claim_filing(client, filing_id)
                    if claimed is None:
                        logger.debug("Filing %s: claim lost — skipping", row.pdf_url)
                        stats["filing_skipped"] += 1
                        continue

                    # Use the DB's post-increment values and lease token.
                    attempt_count = claimed["attempt_count"]
                    max_attempts  = claimed.get("max_attempts", max_attempts)
                    claim_token   = claimed["claim_token"]

                except Exception as exc:
                    logger.warning("Ledger registration failed for %s: %s — processing anyway", row.pdf_url, exc)
                    filing_id = None

            # ── Download ──────────────────────────────────────────────────────
            try:
                pdf_bytes = download_pdf(session, row.pdf_url)
            except BlockedError as exc:
                logger.warning("BLOCKED PDF for %s: %s", company_name, exc)
                if use_ledger and filing_id:
                    filing_ledger.fail_filing(
                        client, filing_id,
                        error=f"blocked: {exc}",
                        attempt_count=attempt_count,
                        max_attempts=max_attempts,
                        claim_token=claim_token,
                    )
                stats["errors"] += 1
                continue
            except requests.Timeout:
                logger.warning("TIMEOUT PDF for %s — %s", company_name, row.pdf_url)
                if use_ledger and filing_id:
                    filing_ledger.fail_filing(
                        client, filing_id,
                        error="timeout downloading PDF",
                        attempt_count=attempt_count,
                        max_attempts=max_attempts,
                        claim_token=claim_token,
                    )
                stats["errors"] += 1
                continue
            except Exception as exc:
                logger.error("Failed to download %s: %s", row.pdf_url, exc)
                if use_ledger and filing_id:
                    filing_ledger.fail_filing(
                        client, filing_id,
                        error=str(exc),
                        attempt_count=attempt_count,
                        max_attempts=max_attempts,
                        claim_token=claim_token,
                    )
                stats["errors"] += 1
                continue

            # Phase 9: stamp download time for latency measurement.
            if use_ledger and filing_id:
                filing_ledger.record_downloaded(client, filing_id)

            # ── Store raw PDF — Phase 3 gate ──────────────────────────────────
            pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            raw_text = None  # set inside storage block; used below for skip context
            if use_ledger and filing_id and storage_backend is not None:
                try:
                    storage_path, _ = doc_storage.store_pdf(
                        storage_backend, pdf_bytes, pdf_sha256, row.filing_date
                    )
                    raw_text = doc_storage.extract_raw_text(pdf_bytes)
                    try:
                        filing_ledger.record_storage(
                            client, filing_id,
                            storage_path=storage_path,
                            file_size_bytes=len(pdf_bytes),
                            raw_extracted_text=raw_text or None,
                        )
                    except Exception as exc:
                        logger.error(
                            "Filing %d: record_storage failed — marking failed: %s", filing_id, exc
                        )
                        filing_ledger.fail_filing(
                            client, filing_id,
                            error=f"record_storage failed: {exc}",
                            attempt_count=attempt_count,
                            max_attempts=max_attempts,
                            claim_token=claim_token,
                        )
                        stats["errors"] += 1
                        continue
                except Exception as exc:
                    logger.error("Filing %d: PDF storage failed: %s", filing_id, exc)
                    filing_ledger.fail_filing(
                        client, filing_id,
                        error=f"storage failure: {exc}",
                        attempt_count=attempt_count,
                        max_attempts=max_attempts,
                        claim_token=claim_token,
                    )
                    stats["errors"] += 1
                    continue

            # ── Parse ─────────────────────────────────────────────────────────
            transactions = parse_pdf(pdf_bytes, row.pdf_url, row.filing_date)
            if not transactions:
                if use_ledger and filing_id:
                    if raw_text is None:
                        skip_reason = "no transactions parsed from PDF"
                    elif not raw_text.strip():
                        skip_reason = "no text extracted from PDF — possible download corruption or pdfplumber failure"
                    else:
                        skip_reason = "no transactions in extracted text — possible layout mismatch or genuinely empty filing"
                    filing_ledger.skip_filing(
                        client, filing_id,
                        reason=skip_reason,
                        claim_token=claim_token,
                    )
                continue

            # Phase 9: stamp parse time for latency measurement.
            if use_ledger and filing_id:
                filing_ledger.record_parsed(client, filing_id)

            # ── Upsert transactions ───────────────────────────────────────────
            tx_inserted = 0
            tx_dedup    = 0

            for tx in transactions:
                stats["parsed"] += 1
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
                    raw_document_sha256=tx.raw_document_sha256,
                    source_filing_id=filing_id,   # None when not using ledger
                    parser_version=tx.parser_version,
                )
                if result["inserted"]:
                    stats["inserted"] += 1
                    tx_inserted += 1
                    if tx.insider_verified and not tx.needs_review and result["company_id"]:
                        try:
                            alert_context = os.getenv("ALERT_CONTEXT", "live")
                            dispatch(
                                AlertPayload(
                                    company_name=tx.company_name,
                                    company_id=result["company_id"],
                                    insider_name=tx.insider_name,
                                    insider_role=tx.role,
                                    direction=tx.direction,
                                    transaction_type=tx.transaction_type,
                                    quantity=tx.quantity,
                                    unit_price=tx.unit_price,
                                    total_value=tx.total_value,
                                    currency=tx.currency,
                                    transaction_date=tx.transaction_date,
                                    filed_date=tx.filing_date,
                                    source_url=tx.source_url,
                                    transaction_id=result["transaction_id"],
                                ),
                                client=client,
                                company_tier=company_tier,
                                context=alert_context,
                            )
                        except Exception as alert_exc:
                            logger.warning("Alert dispatch error: %s", alert_exc)
                else:
                    stats["dedup"] += 1
                    tx_dedup += 1

            if use_ledger and filing_id:
                filing_ledger.complete_filing(
                    client, filing_id,
                    tx_inserted=tx_inserted,
                    tx_dedup=tx_dedup,
                    pdf_sha256=pdf_sha256,
                    claim_token=claim_token,
                )

            time.sleep(polite_delay)

    except BlockedError as exc:
        stats["skipped"] = True
        stats["skip_reason"] = f"blocked/CAPTCHA: {exc}"
        logger.warning("BLOCKED crawling %s — %s", company_name, exc)

    except requests.Timeout:
        stats["skipped"] = True
        stats["skip_reason"] = "timeout on listing page"
        logger.warning("TIMEOUT listing page for %s", company_name)

    except Exception as exc:
        stats["skipped"] = True
        stats["skip_reason"] = f"unexpected error: {exc}"
        logger.error("Error crawling %s: %s", company_name, exc, exc_info=True)

    return stats


def run_crawl(
    limit: int = None,
    company_filter: str = None,
    max_pdfs_per_company: int = 10,
    polite_delay: float = 2.0,
    max_tier: int = 3,
    max_workers: int = 1,
) -> dict:
    """
    Crawl companies and upsert all transactions to Supabase.

    max_tier: 1=FTSE MIB only, 2=+Mid Cap, 3=all
    max_workers: concurrent company threads (use 3 for Tier 1, 1 for Tier 2/3)
    """
    try:
        client = get_supabase_client()
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    use_ledger = filing_ledger.table_exists(client)
    storage_backend = None
    if use_ledger:
        logger.info("Filing ledger available — filing-level dedup and retry tracking enabled")
        filing_ledger.reap_stale_filings(client)   # recover crashed workers before crawl
        storage_backend = doc_storage.get_storage_backend(client)
    else:
        allow_legacy = os.getenv("ALLOW_LEGACY_INGESTION", "").lower() in ("1", "true", "yes")
        if not allow_legacy:
            logger.critical(
                "FATAL: filings table not found in Supabase. "
                "Refusing to run the sweep without the durable retry ledger. "
                "Apply db/migrations/002_filings_table.sql and re-run. "
                "For local development or emergency use only, set ALLOW_LEGACY_INGESTION=true "
                "— but note that legacy mode DOES NOT provide retry-safe ingestion or "
                "source_filing_id population."
            )
            sys.exit(1)
        logger.warning(
            "=" * 72 + "\n"
            "LEGACY SWEEP MODE ACTIVE  (ALLOW_LEGACY_INGESTION=true)\n"
            "filings table absent — running without ledger. source_filing_id will\n"
            "NOT be populated. Failed PDFs have no retry mechanism.\n"
            "Apply db/migrations/002_filings_table.sql as soon as possible.\n"
            + "=" * 72
        )

    logger.info("Connected to Supabase (max_tier=%d, workers=%d)", max_tier, max_workers)

    seed_result = load_seed_companies(client, SEED_CSV)
    logger.info("Seed load: %d inserted, %d skipped", seed_result["inserted"], seed_result["skipped"])

    companies_query = (
        client.table("companies")
        .select("name, priority_tier")
        .lte("priority_tier", max_tier)
        .order("priority_tier")
        .order("name")
        .execute()
    )
    if not companies_query.data:
        logger.error("No companies found for tier <= %d", max_tier)
        return {}

    all_companies = [
        (c["name"], c.get("priority_tier", 3)) for c in companies_query.data
    ]
    if company_filter:
        all_companies = [
            (n, t) for n, t in all_companies if company_filter.lower() in n.lower()
        ]
    if limit:
        all_companies = all_companies[:limit]

    logger.info("Crawling %d companies (max %d PDFs each, %d workers)",
                len(all_companies), max_pdfs_per_company, max_workers)

    run_stats = {
        "companies_attempted": len(all_companies),
        "companies_crawled": 0,
        "pdfs_downloaded": 0,
        "transactions_parsed": 0,
        "transactions_inserted": 0,
        "transactions_skipped_dedup": 0,
        "errors": 0,
    }
    skipped_companies: list[dict] = []

    start_time = time.time()
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {
            executor.submit(
                _crawl_company,
                name, tier, client, polite_delay, max_pdfs_per_company, use_ledger, storage_backend,
            ): name
            for name, tier in all_companies
        }

        for future in as_completed(future_to_name):
            company_name = future_to_name[future]
            completed += 1

            try:
                result = future.result()
            except Exception as exc:
                logger.error("Unexpected future error for %s: %s", company_name, exc)
                skipped_companies.append({"name": company_name, "reason": str(exc)})
                run_stats["errors"] += 1
                continue

            run_stats["pdfs_downloaded"] += result["pdfs"]
            run_stats["transactions_parsed"] += result["parsed"]
            run_stats["transactions_inserted"] += result["inserted"]
            run_stats["transactions_skipped_dedup"] += result["dedup"]
            run_stats["errors"] += result["errors"]

            if result["skipped"]:
                skipped_companies.append({"name": company_name, "reason": result["skip_reason"]})
                run_stats["errors"] += 1
            else:
                run_stats["companies_crawled"] += 1

            logger.info(
                "[%d/%d] %s — %d new, %d dedup, %d errors",
                completed, len(all_companies), company_name,
                result["inserted"], result["dedup"], result["errors"],
            )

    elapsed = time.time() - start_time

    print("\n" + "=" * 70)
    print(f"CRAWL SUMMARY  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"  Companies attempted   : {run_stats['companies_attempted']}")
    print(f"  Companies crawled     : {run_stats['companies_crawled']}")
    print(f"  Companies skipped     : {len(skipped_companies)}")
    print(f"  PDFs downloaded       : {run_stats['pdfs_downloaded']}")
    print(f"  Transactions parsed   : {run_stats['transactions_parsed']}")
    print(f"  Transactions inserted : {run_stats['transactions_inserted']}  (new)")
    print(f"  Transactions dedup'd  : {run_stats['transactions_skipped_dedup']}  (already in DB)")
    print(f"  Errors                : {run_stats['errors']}")
    print(f"  Total time            : {elapsed:.1f}s  ({elapsed/60:.1f}m)")
    if skipped_companies:
        print()
        print("  SKIPPED COMPANIES:")
        for sc in skipped_companies:
            print(f"    • {sc['name']}: {sc['reason']}")
    print("=" * 70 + "\n")

    try:
        client.table("scraper_runs").upsert({
            "tier": max_tier,
            "last_successful_run": datetime.utcnow().isoformat(),
            "companies_crawled": run_stats["companies_crawled"],
            "transactions_inserted": run_stats["transactions_inserted"],
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()
        logger.info("scraper_runs updated for tier %d", max_tier)
    except Exception as exc:
        logger.warning("Could not update scraper_runs: %s", exc)

    return run_stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2: crawl all companies, store in Supabase.")
    parser.add_argument("--limit", type=int, help="Crawl only first N companies (default: all)")
    parser.add_argument("--company", help="Filter by company name substring")
    parser.add_argument("--max-pdfs", type=int, default=10, help="Max PDFs per company (default: 10)")
    parser.add_argument("--delay", type=float, default=2.0, help="Polite delay between requests (default: 2.0s)")
    parser.add_argument("--tier", type=int, default=3, choices=[1, 2, 3],
                        help="Max priority tier: 1=FTSE MIB only, 2=+Mid Cap, 3=all")
    parser.add_argument("--workers", type=int, default=1,
                        help="Concurrent company threads (default: 1; use 3 for Tier 1)")
    args = parser.parse_args()

    run_crawl(
        limit=args.limit,
        company_filter=args.company,
        max_pdfs_per_company=args.max_pdfs,
        polite_delay=args.delay,
        max_tier=args.tier,
        max_workers=args.workers,
    )


if __name__ == "__main__":
    main()
