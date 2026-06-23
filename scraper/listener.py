"""
Lightweight listener — scans the Borsa Italiana listing for new filings.

Design goals:
  · Terminate in < 2 s if no new filing URLs appear on page 1.
  · Strict 6 s timeout on the listing fetch; 10 s on each PDF download.
  · File-based URL cache (recent_scrapes.json) prevents re-processing.
  · Heavy parse + DB insert + alert only when a genuinely new URL is found.
  · No concurrent threads — processes new PDFs sequentially to stay polite.

Typical run:
  0 new filings → ~1–2 s   (listing fetch only)
  1 new filing  → ~5–15 s  (listing + 1 PDF download + parse + DB insert)
"""

import logging
from typing import Optional

import requests

from . import cache
from .alerts import AlertPayload, dispatch
from .db import get_supabase_client, upsert_transaction
from .fetcher import BlockedError, _make_session, download_pdf, fetch_listing_page
from .models import ListingRow
from .parser import parse_pdf

logger = logging.getLogger(__name__)

# Strict timeouts — never hang in a 1-minute cron window.
_LISTING_TIMEOUT = 6   # seconds
_PDF_TIMEOUT     = 10  # seconds


def run_listener() -> dict:
    """
    Fetch the most recent 25 listings, skip already-seen URLs, process new ones.

    Returns: {"new": int, "skipped": int, "errors": int}
    """
    stats = {"new": 0, "skipped": 0, "errors": 0}

    try:
        client = get_supabase_client()
    except ValueError as exc:
        logger.error("Supabase not configured: %s", exc)
        return stats

    session = _make_session()

    # ── Step 1: fetch listing page 1 (fast path) ──────────────────────────────
    try:
        rows = fetch_listing_page(session, letter="", page=1, timeout=_LISTING_TIMEOUT)
    except BlockedError as exc:
        logger.warning("Listing page blocked: %s", exc)
        return stats
    except requests.Timeout:
        logger.warning("Listing page timed out after %ds", _LISTING_TIMEOUT)
        return stats
    except Exception as exc:
        logger.error("Listing fetch failed: %s", exc)
        return stats

    if not rows:
        logger.info("Listing page returned no rows")
        return stats

    logger.info("Listing page: %d rows", len(rows))

    # ── Step 2: diff against cache ────────────────────────────────────────────
    seen_list = cache.load()
    seen_set  = set(seen_list)

    new_rows  = [r for r in rows if r.pdf_url not in seen_set]
    stats["skipped"] = len(rows) - len(new_rows)
    logger.info("%d new URLs to process, %d already in cache", len(new_rows), stats["skipped"])

    if not new_rows:
        return stats

    # ── Step 3: process new filings ───────────────────────────────────────────
    for row in new_rows:
        try:
            _process_filing(row, client, session)
            stats["new"] += 1
        except Exception as exc:
            logger.error("Failed processing %s: %s", row.pdf_url, exc)
            stats["errors"] += 1
        finally:
            # Mark as seen regardless of outcome to avoid hammering a broken PDF.
            seen_list.append(row.pdf_url)

    # ── Step 4: persist updated cache ─────────────────────────────────────────
    cache.save(seen_list)

    return stats


def _process_filing(row: ListingRow, client, session: requests.Session) -> None:
    """Download, parse, upsert, and alert for one new filing."""
    logger.info("New filing: %s  %s", row.company_name, row.pdf_url)

    try:
        pdf_bytes = download_pdf(session, row.pdf_url, timeout=_PDF_TIMEOUT)
    except (BlockedError, requests.Timeout) as exc:
        logger.warning("Download failed for %s: %s", row.pdf_url, exc)
        raise

    transactions = parse_pdf(pdf_bytes, row.pdf_url, row.filing_date)
    if not transactions:
        logger.info("No transactions parsed from %s", row.pdf_url)
        return

    for tx in transactions:
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
            needs_review=tx.needs_review,
            extraction_confidence=tx.extraction_confidence,
            classification_confidence=tx.classification_confidence,
            review_status=tx.review_status,
            review_reason=tx.review_reason,
            source_transaction_index=tx.source_transaction_index,
            raw_document_sha256=tx.raw_document_sha256,
            parser_version=tx.parser_version,
        )

        if result["inserted"] and tx.insider_verified and not tx.needs_review:
            try:
                tier = _get_company_tier(client, result.get("company_id"))
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
                    company_tier=tier,
                )
            except Exception as alert_exc:
                logger.warning("Alert dispatch failed: %s", alert_exc)


def _get_company_tier(client, company_id: Optional[int]) -> int:
    """Return priority_tier for a company, defaulting to 3 (least urgent)."""
    if not company_id:
        return 3
    try:
        row = (
            client.table("companies")
            .select("priority_tier")
            .eq("id", company_id)
            .single()
            .execute()
        )
        return int(row.data.get("priority_tier", 3)) if row.data else 3
    except Exception:
        return 3
