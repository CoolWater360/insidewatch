#!/usr/bin/env python3
"""
Phase 1 runner: scrape internal dealing PDFs for ONE company and print
parsed results to the console. No database writes.

Usage:
    python3 -m scraper.run_phase1                    # defaults to ENI
    python3 -m scraper.run_phase1 --company "ENI"
    python3 -m scraper.run_phase1 --company "Emak" --max-pdfs 3
"""

import argparse
import logging
import sys
import time

from .fetcher import _make_session, iter_company_listings, download_pdf
from .models import ParsedTransaction
from .parser import parse_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def print_transaction(tx: ParsedTransaction, pdf_url: str) -> None:
    sep = "─" * 72
    direction_symbol = "▲ BUY " if tx.direction == "buy" else ("▼ SELL" if tx.direction == "sell" else "? ????")

    print(sep)
    print(f"  {direction_symbol}  │  {tx.company_name}  │  {tx.transaction_date}")
    print(sep)
    print(f"  Insider   : {tx.insider_name}")
    print(f"  Role      : {tx.role}")
    print(f"  Instrument: {tx.instrument_type}  (ISIN: {tx.isin or 'n/a'})")
    print(f"  Quantity  : {tx.quantity:,.0f}")
    print(f"  Unit price: {tx.unit_price:,.4f} {tx.currency}")
    print(f"  Total EUR : {tx.total_value:,.2f}")
    print(f"  Filed     : {tx.filing_date}")
    print(f"  Source    : {pdf_url}")
    print(f"  raw_hash  : {tx.raw_hash[:16]}…")
    if tx.parse_warnings:
        print(f"  ⚠ Warnings:")
        for w in tx.parse_warnings:
            print(f"      • {w}")
    print()


def run(company: str, max_pdfs: int = 5, polite_delay: float = 1.5) -> None:
    letter = company[0].upper() if company else ""
    logger.info("Fetching listing for company='%s' (letter '%s')", company, letter)

    session = _make_session()
    found_any = False

    for row in iter_company_listings(
        session,
        letter=letter,
        company_name=company,
        max_pdfs=max_pdfs,
        polite_delay=polite_delay,
    ):
        found_any = True
        logger.info(
            "Downloading PDF: %s  (filed %s)",
            row.pdf_url,
            row.filing_date,
        )

        try:
            pdf_bytes = download_pdf(session, row.pdf_url)
        except Exception as exc:
            logger.error("Failed to download %s: %s", row.pdf_url, exc)
            continue

        transactions = parse_pdf(pdf_bytes, row.pdf_url, row.filing_date)

        if not transactions:
            logger.warning("No transactions parsed from %s", row.pdf_url)
            continue

        logger.info("Parsed %d transaction(s) from %s", len(transactions), row.pdf_url)

        for tx in transactions:
            print_transaction(tx, row.pdf_url)

        time.sleep(polite_delay)

    if not found_any:
        logger.warning(
            "No listing rows found for company='%s'. "
            "Try a different spelling (e.g. 'Eni' not 'ENI').",
            company,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1: scrape one company, print to console.")
    parser.add_argument("--company", default="Eni", help="Exact company name as shown on Borsa Italiana (default: Eni)")
    parser.add_argument("--max-pdfs", type=int, default=5, help="Max PDFs to download (default: 5)")
    parser.add_argument("--delay", type=float, default=1.5, help="Polite delay between requests in seconds (default: 1.5)")
    args = parser.parse_args()

    run(company=args.company, max_pdfs=args.max_pdfs, polite_delay=args.delay)


if __name__ == "__main__":
    main()
