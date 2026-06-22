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

from .db import get_supabase_client, load_seed_companies, upsert_transaction
from .fetcher import BlockedError, _make_session, iter_company_listings, download_pdf
from .parser import parse_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _crawl_company(
    company_name: str,
    client,
    polite_delay: float,
    max_pdfs: int,
) -> dict:
    """
    Crawl a single company in its own requests.Session (thread-safe).
    Returns a stats dict; never raises — errors are captured in the return value.
    """
    stats = {
        "pdfs": 0,
        "parsed": 0,
        "inserted": 0,
        "dedup": 0,
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

            try:
                pdf_bytes = download_pdf(session, row.pdf_url)
            except BlockedError as exc:
                logger.warning("BLOCKED PDF for %s: %s", company_name, exc)
                stats["errors"] += 1
                continue
            except requests.Timeout:
                logger.warning("TIMEOUT PDF for %s — %s", company_name, row.pdf_url)
                stats["errors"] += 1
                continue
            except Exception as exc:
                logger.error("Failed to download %s: %s", row.pdf_url, exc)
                stats["errors"] += 1
                continue

            transactions = parse_pdf(pdf_bytes, row.pdf_url, row.filing_date)
            if not transactions:
                continue

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
                )
                if result["inserted"]:
                    stats["inserted"] += 1
                else:
                    stats["dedup"] += 1

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

    logger.info("Connected to Supabase (max_tier=%d, workers=%d)", max_tier, max_workers)

    seed_result = load_seed_companies(client, SEED_CSV)
    logger.info("Seed load: %d inserted, %d skipped", seed_result["inserted"], seed_result["skipped"])

    companies_query = (
        client.table("companies")
        .select("name")
        .lte("priority_tier", max_tier)
        .order("priority_tier")
        .order("name")
        .execute()
    )
    if not companies_query.data:
        logger.error("No companies found for tier <= %d", max_tier)
        return {}

    all_companies = [c["name"] for c in companies_query.data]
    if company_filter:
        all_companies = [c for c in all_companies if company_filter.lower() in c.lower()]
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
            executor.submit(_crawl_company, name, client, polite_delay, max_pdfs_per_company): name
            for name in all_companies
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
