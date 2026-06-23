"""
Lightweight listener — scans the Borsa Italiana listing for new filings.

Design goals:
  · Terminate in < 2 s if no new filing URLs appear on page 1.
  · Strict 6 s timeout on the listing fetch; 10 s on each PDF download.
  · Durable filing ledger (Phase 2): each PDF is registered in the filings
    table with full status tracking and exponential-backoff retry.
  · Production safety: if the filings table is unavailable and
    ALLOW_LEGACY_INGESTION is not set, the listener exits with an error
    rather than silently degrading.  This prevents data loss from the
    legacy finally-block cache write, which marks a URL as seen even when
    processing fails, making the failure permanently unrecoverable.
  · Legacy fallback: only permitted when ALLOW_LEGACY_INGESTION=true.
    This environment variable is intended exclusively for local development
    or short-term emergency use.  It MUST NOT be set in production or CI.

Typical run:
  0 new filings → ~1–2 s   (listing fetch only)
  1 new filing  → ~5–15 s  (listing + 1 PDF download + parse + DB insert)
"""

import logging
import os
from typing import Optional

import requests

from . import cache
from . import filings as filing_ledger
from .alerts import AlertPayload, dispatch
from .db import get_supabase_client, upsert_transaction
from .fetcher import BlockedError, _make_session, download_pdf, fetch_listing_page
from .models import ListingRow
from .parser import parse_pdf

logger = logging.getLogger(__name__)

_LISTING_TIMEOUT = 6   # seconds
_PDF_TIMEOUT     = 10  # seconds


# ── Public entry point ────────────────────────────────────────────────────────

def run_listener() -> dict:
    """
    Fetch the most recent listings, process new filings, and return stats.

    Routes to the durable ledger path if the filings table exists.
    If the table is absent:
      · Without ALLOW_LEGACY_INGESTION: returns immediately with errors=1
        so the calling process exits non-zero (CI job fails visibly).
      · With ALLOW_LEGACY_INGESTION=true: runs the legacy file-cache path
        with prominent warnings.  For local dev / emergency only.

    Returns: {"new": int, "skipped": int, "errors": int, "retried": int}
    """
    stats: dict = {"new": 0, "skipped": 0, "errors": 0, "retried": 0}

    try:
        client = get_supabase_client()
    except ValueError as exc:
        logger.error("Supabase not configured: %s", exc)
        return stats

    session = _make_session()

    if filing_ledger.table_exists(client):
        return _run_with_ledger(client, session, stats)

    # ── Filings table not available ───────────────────────────────────────────
    allow_legacy = os.getenv("ALLOW_LEGACY_INGESTION", "").lower() in ("1", "true", "yes")

    if not allow_legacy:
        logger.critical(
            "FATAL: filings table not found in Supabase. "
            "Refusing to run the listener without the durable retry ledger. "
            "Apply db/migrations/002_filings_table.sql and re-run. "
            "For local development or emergency use only, set ALLOW_LEGACY_INGESTION=true "
            "— but note that legacy mode DOES NOT provide retry-safe ingestion."
        )
        stats["errors"] = 1
        return stats

    _warn_legacy_mode()
    return _run_with_cache(client, session, stats)


def _warn_legacy_mode() -> None:
    """Emit a multi-line WARNING that is impossible to miss in any log viewer."""
    for line in [
        "=" * 72,
        "LEGACY INGESTION MODE ACTIVE  (ALLOW_LEGACY_INGESTION=true)",
        "=" * 72,
        "The filings table is not present. Running on the pre-Phase-2 file",
        "cache. This mode has a known data-loss issue: the finally block in",
        "_run_with_cache marks a URL as seen even when processing fails,",
        "making the failure permanently unrecoverable without manual cache",
        "deletion. This mode also does NOT populate source_filing_id on any",
        "transaction and does NOT provide exponential-backoff retry.",
        "",
        "MUST NOT be used for institutional data collection.",
        "Apply db/migrations/002_filings_table.sql as soon as possible,",
        "then unset ALLOW_LEGACY_INGESTION.",
        "=" * 72,
    ]:
        logger.warning(line)


# ── Ledger path (Phase 2) ─────────────────────────────────────────────────────

def _run_with_ledger(client, session: requests.Session, stats: dict) -> dict:
    """Process new and retryable filings using the durable DB ledger."""

    # Step 1: fetch listing page
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

    # Step 2: register all rows in the ledger, collect eligible ones
    to_process: list[dict] = []
    for row in rows:
        try:
            filing = filing_ledger.register_filing(
                client,
                pdf_url=row.pdf_url,
                filing_date=row.filing_date,
                company_name=row.company_name,
            )
            if filing["status"] in ("completed", "in_progress"):
                stats["skipped"] += 1
            elif filing_ledger.is_eligible(filing):
                to_process.append((row, filing))
            else:
                stats["skipped"] += 1
        except Exception as exc:
            logger.error("register_filing failed for %s: %s", row.pdf_url, exc)
            stats["errors"] += 1

    # Step 3: also pick up retryable failed filings not on today's listing
    try:
        retryable = filing_ledger.get_retryable_filings(client)
        for filing in retryable:
            row = ListingRow(
                pdf_url=filing["pdf_url"],
                filing_date=filing.get("filing_date"),
                company_name=filing.get("company_name") or "",
            )
            # Avoid adding duplicates already in to_process
            existing_urls = {r.pdf_url for r, _ in to_process}
            if filing["pdf_url"] not in existing_urls:
                to_process.append((row, filing))
    except Exception as exc:
        logger.warning("get_retryable_filings failed: %s", exc)

    stats["skipped"] = len(rows) - sum(1 for r, _ in to_process if r.pdf_url in {lr.pdf_url for lr in rows})
    logger.info("%d filing(s) to process", len(to_process))

    if not to_process:
        return stats

    # Step 4: process eligible filings
    for row, filing in to_process:
        is_retry = filing.get("attempt_count", 0) > 0
        try:
            result = _process_filing_with_ledger(row, filing, client, session)
            if result == "new":
                stats["new"] += 1
            elif result == "retried":
                stats["retried"] += 1
        except Exception as exc:
            logger.error("Failed processing %s: %s", row.pdf_url, exc)
            stats["errors"] += 1

    return stats


def _process_filing_with_ledger(
    row: ListingRow,
    filing: dict,
    client,
    session: requests.Session,
) -> str:
    """
    Download, parse, upsert, and complete/fail one filing using the ledger.
    Returns 'new' for first-time processing, 'retried' for a retry.
    """
    filing_id    = filing["id"]
    attempt_count = filing.get("attempt_count", 0)
    max_attempts  = filing.get("max_attempts", 3)
    is_retry      = attempt_count > 0

    logger.info("Processing filing %d (%s): %s", filing_id, "retry" if is_retry else "new", row.pdf_url)
    filing_ledger.claim_filing(client, filing_id, attempt_count)

    # Download
    try:
        pdf_bytes = download_pdf(session, row.pdf_url, timeout=_PDF_TIMEOUT)
    except (BlockedError, requests.Timeout) as exc:
        filing_ledger.fail_filing(
            client, filing_id,
            error=str(exc),
            attempt_count=attempt_count + 1,
            max_attempts=max_attempts,
        )
        raise
    except Exception as exc:
        filing_ledger.fail_filing(
            client, filing_id,
            error=str(exc),
            attempt_count=attempt_count + 1,
            max_attempts=max_attempts,
        )
        raise

    # Parse
    transactions = parse_pdf(pdf_bytes, row.pdf_url, row.filing_date)
    if not transactions:
        filing_ledger.skip_filing(client, filing_id, reason="no transactions parsed from PDF")
        logger.info("Filing %d: no transactions — skipped", filing_id)
        return "retried" if is_retry else "new"

    # Upsert transactions
    tx_inserted = 0
    tx_dedup    = 0
    pdf_sha256  = transactions[0].raw_document_sha256 if transactions else None

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
            source_filing_id=filing_id,        # ← populated from ledger
            parser_version=tx.parser_version,
        )
        if result["inserted"]:
            tx_inserted += 1
            if tx.insider_verified and not tx.needs_review:
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
        else:
            tx_dedup += 1

    filing_ledger.complete_filing(
        client, filing_id,
        tx_inserted=tx_inserted,
        tx_dedup=tx_dedup,
        pdf_sha256=pdf_sha256,
    )
    return "retried" if is_retry else "new"


# ── File-cache fallback path (pre-Phase-2 behaviour) ─────────────────────────

def _run_with_cache(client, session: requests.Session, stats: dict) -> dict:
    """
    Legacy listener using the JSON file cache.  Used when the filings table
    is not yet available.  Behaviour is identical to the pre-Phase-2 listener.
    """
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

    seen_list = cache.load()
    seen_set  = set(seen_list)
    new_rows  = [r for r in rows if r.pdf_url not in seen_set]
    stats["skipped"] = len(rows) - len(new_rows)
    logger.info("%d new URLs, %d already in cache", len(new_rows), stats["skipped"])

    if not new_rows:
        return stats

    for row in new_rows:
        try:
            _process_filing_cache(row, client, session)
            stats["new"] += 1
        except Exception as exc:
            logger.error("Failed processing %s: %s", row.pdf_url, exc)
            stats["errors"] += 1
        finally:
            seen_list.append(row.pdf_url)

    cache.save(seen_list)
    return stats


def _process_filing_cache(row: ListingRow, client, session: requests.Session) -> None:
    """Download, parse, upsert, and alert — legacy cache path."""
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
            source_filing_id=None,   # not available in cache mode
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


# ── Shared helper ─────────────────────────────────────────────────────────────

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
