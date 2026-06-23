"""
Durable filing ledger — Phase 2.

One row in the filings table represents one PDF document the scraper has
discovered.  The row tracks the full lifecycle from first discovery to
successful extraction (or permanent failure).

State machine:
    pending → in_progress → completed
                          → failed      (will retry after backoff)
                          → skipped     (empty PDF, blocked, or max retries hit)
    failed  → in_progress              (retry)
    failed  → skipped                  (max retries hit on next fail_filing call)

All public functions are safe to call from multiple threads/workers as long as
the underlying Supabase calls are atomic (they are — each is a single RPC call).
The only race condition is dual-claim on the same filing; this is deliberately
tolerated because transaction-level deduplication via raw_hash is the final
arbiter of uniqueness.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Scraper version stamped on every filing row.
SCRAPER_VERSION = "2.0.0"

# Exponential backoff cap in minutes.
_MAX_BACKOFF_MINUTES = 60


def _now() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _next_retry_after(attempt_count: int) -> str:
    """
    Return the earliest UTC timestamp at which this filing may be retried.
    Backoff: 2^attempt_count minutes, capped at _MAX_BACKOFF_MINUTES.
    attempt_count=0 → 1 min, =1 → 2 min, =2 → 4 min, … =6+ → 60 min.
    """
    minutes = min(2 ** attempt_count, _MAX_BACKOFF_MINUTES)
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def table_exists(client) -> bool:
    """
    Return True if the filings table is present and accessible.
    Used by the listener to decide whether to use the ledger or fall back
    to the file cache.
    """
    try:
        client.table("filings").select("id").limit(0).execute()
        return True
    except Exception:
        return False


def register_filing(
    client,
    pdf_url: str,
    filing_date: Optional[str],
    company_name: Optional[str],
) -> dict:
    """
    Ensure a filing row exists for this PDF URL. Return the existing row if
    already present, or create a new pending row.

    Returns the full filing row dict.  The caller uses the 'status' field to
    decide whether to process this filing.

    Eligible for processing: status in ('pending', 'failed') AND
        (status != 'failed' OR next_attempt_after <= now).
    The caller is responsible for checking eligibility — this function only
    guarantees the row exists.
    """
    existing = (
        client.table("filings")
        .select("*")
        .eq("pdf_url", pdf_url)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    try:
        result = client.table("filings").insert({
            "pdf_url":         pdf_url,
            "filing_date":     filing_date,
            "company_name":    company_name,
            "status":          "pending",
            "scraper_version": SCRAPER_VERSION,
            "first_seen_at":   _now(),
            "updated_at":      _now(),
        }).execute()
        logger.debug("Registered new filing: %s", pdf_url)
        return result.data[0]
    except Exception as exc:
        # Concurrent INSERT race on pdf_url unique constraint: another worker
        # inserted this URL between our SELECT and our INSERT.  Re-query to
        # return their row rather than propagating a constraint error.
        exc_str = str(exc).lower()
        if "unique" in exc_str or "duplicate" in exc_str or "23505" in exc_str:
            logger.debug(
                "register_filing: concurrent INSERT race for %s — re-querying", pdf_url
            )
            retry = (
                client.table("filings").select("*").eq("pdf_url", pdf_url).execute()
            )
            if retry.data:
                return retry.data[0]
        raise


def is_eligible(filing: dict) -> bool:
    """
    Return True if a filing should be processed now.

    Eligible when:
      · status is 'pending' — never attempted, or reset by operator
      · status is 'failed' AND next_attempt_after has elapsed
    """
    status = filing.get("status")
    if status == "pending":
        return True
    if status == "failed":
        naa = filing.get("next_attempt_after")
        if naa is None:
            return True
        now = datetime.now(timezone.utc)
        try:
            window = datetime.fromisoformat(naa)
            if window.tzinfo is None:
                window = window.replace(tzinfo=timezone.utc)
            return now >= window
        except (ValueError, TypeError):
            return True
    return False


def claim_filing(client, filing_id: int, attempt_count: int) -> None:
    """
    Mark a filing as in_progress and increment the attempt counter.
    Should be called immediately before downloading the PDF.
    """
    client.table("filings").update({
        "status":           "in_progress",
        "attempt_count":    attempt_count + 1,
        "last_attempted_at": _now(),
        "next_attempt_after": None,
        "updated_at":       _now(),
    }).eq("id", filing_id).execute()


def complete_filing(
    client,
    filing_id: int,
    *,
    tx_inserted: int,
    tx_dedup: int,
    pdf_sha256: Optional[str],
) -> None:
    """Mark a filing as successfully completed."""
    client.table("filings").update({
        "status":                   "completed",
        "completed_at":             _now(),
        "transactions_inserted":    tx_inserted,
        "transactions_skipped_dedup": tx_dedup,
        "pdf_sha256":               pdf_sha256,
        "last_error":               None,
        "next_attempt_after":       None,
        "updated_at":               _now(),
    }).eq("id", filing_id).execute()
    logger.info(
        "Filing %d completed — %d inserted, %d dedup", filing_id, tx_inserted, tx_dedup
    )


def fail_filing(
    client,
    filing_id: int,
    *,
    error: str,
    attempt_count: int,
    max_attempts: int,
) -> None:
    """
    Mark a filing as failed.  If attempt_count >= max_attempts, permanently
    skip it instead so the retry loop never picks it up again.
    """
    if attempt_count >= max_attempts:
        new_status = "skipped"
        next_retry = None
        logger.warning(
            "Filing %d reached max attempts (%d) — permanently skipped. Error: %s",
            filing_id, max_attempts, error,
        )
    else:
        new_status = "failed"
        next_retry = _next_retry_after(attempt_count)
        logger.warning(
            "Filing %d failed (attempt %d/%d) — retry after %s. Error: %s",
            filing_id, attempt_count, max_attempts, next_retry, error,
        )

    client.table("filings").update({
        "status":             new_status,
        "last_error":         str(error)[:1000],
        "next_attempt_after": next_retry,
        "updated_at":         _now(),
    }).eq("id", filing_id).execute()


def skip_filing(client, filing_id: int, *, reason: str) -> None:
    """Permanently skip a filing (empty PDF, consistently blocked, etc.)."""
    client.table("filings").update({
        "status":             "skipped",
        "last_error":         reason[:1000],
        "next_attempt_after": None,
        "updated_at":         _now(),
    }).eq("id", filing_id).execute()
    logger.info("Filing %d skipped: %s", filing_id, reason)


def get_retryable_filings(client) -> list[dict]:
    """
    Return failed filings whose backoff window has elapsed and which are
    below their max_attempts ceiling.  These should be processed as if new.
    """
    now = _now()
    result = (
        client.table("filings")
        .select("*")
        .eq("status", "failed")
        .lte("next_attempt_after", now)
        .execute()
    )
    rows = result.data or []
    # Filter out rows that have reached their attempt ceiling.
    eligible = [r for r in rows if r.get("attempt_count", 0) < r.get("max_attempts", 3)]
    if eligible:
        logger.info("%d failed filing(s) eligible for retry", len(eligible))
    return eligible


# ── CLI helpers ───────────────────────────────────────────────────────────────

def inspect(client, pdf_url_or_id: str) -> Optional[dict]:
    """Return the filing row for a given PDF URL or integer ID."""
    try:
        filing_id = int(pdf_url_or_id)
        result = client.table("filings").select("*").eq("id", filing_id).execute()
    except ValueError:
        result = client.table("filings").select("*").eq("pdf_url", pdf_url_or_id).execute()
    return result.data[0] if result.data else None


def reset_for_retry(client, pdf_url_or_id: str) -> bool:
    """
    Reset a filing to 'pending' so it will be picked up on the next listener
    run, regardless of its current status or attempt count.  Returns True if
    a row was found and updated.
    """
    row = inspect(client, pdf_url_or_id)
    if not row:
        return False
    client.table("filings").update({
        "status":             "pending",
        "attempt_count":      0,
        "next_attempt_after": None,
        "last_error":         None,
        "updated_at":         _now(),
    }).eq("id", row["id"]).execute()
    logger.info("Filing %d (%s) reset to pending", row["id"], row["pdf_url"])
    return True
