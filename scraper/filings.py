"""
Durable filing ledger — Phase 2.

One row in the filings table represents one PDF document the scraper has
discovered.  The row tracks the full lifecycle from first discovery to
successful extraction (or permanent failure).

State machine
─────────────
    pending     → in_progress   claim_filing() won the race
    in_progress → completed     complete_filing()
    in_progress → failed        fail_filing(), attempt_count < max_attempts
    in_progress → skipped       fail_filing(), attempt_count >= max_attempts
    in_progress → failed        reap_stale_filings(), attempt_count < max_attempts
    in_progress → skipped       reap_stale_filings(), attempt_count >= max_attempts
    failed      → in_progress   claim_filing() after backoff window elapsed
    skipped     → pending       reset_for_retry() (operator CLI only)

Concurrency model
─────────────────
    claim_filing() delegates to the claim_filing() Postgres RPC, which executes
    a single UPDATE … WHERE … RETURNING *.  PostgreSQL's row-level locking ensures
    exactly one concurrent caller sees the WHERE clause match and receives a row;
    all others receive 0 rows (race lost).  Callers MUST check the return value.

    raw_hash deduplication in upsert_transaction() is NOT the concurrency control
    mechanism for claims — it is a separate idempotency guard for the insert layer.

Stale in_progress recovery
───────────────────────────
    A worker crash (runner killed, OOM, timeout) leaves the row in_progress
    indefinitely without intervention.  reap_stale_filings() transitions stale
    rows back to failed (or skipped if at max_attempts) and is called at startup
    and before retry selection.

Changed-PDF detection
─────────────────────
    Same-URL content changes are NOT handled automatically. pdf_sha256 is stored
    at completion time for forensic comparison.  Use the CLI command
    `python3 -m scraper.cli check-pdf <url-or-id>` to compare a live download
    against the stored hash.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Scraper version stamped on every filing row.
SCRAPER_VERSION = "2.0.0"

# Exponential backoff cap in minutes.
_MAX_BACKOFF_MINUTES = 60

# How long a filing may remain in_progress before the reaper declares it stale.
# Override per-deployment with the STALE_CLAIM_MINUTES environment variable.
_STALE_CLAIM_MINUTES = 30


def _stale_minutes() -> int:
    """Return the configured stale-claim threshold in minutes."""
    return int(os.getenv("STALE_CLAIM_MINUTES", str(_STALE_CLAIM_MINUTES)))


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


def claim_filing(client, filing_id: int) -> Optional[dict]:
    """
    Atomically claim a filing for processing using the claim_filing Postgres RPC.

    Delegates to a single UPDATE…WHERE…RETURNING * statement, which is atomic
    under PostgreSQL row-level locking.  If two concurrent workers call this for
    the same filing_id, exactly one receives the row; the other receives nothing.

    Claimable states (evaluated by the DB):
      · status = 'pending'
      · status = 'failed' AND next_attempt_after <= now()
      · status = 'in_progress' AND last_attempted_at < now() - stale_threshold
        (stale-claim recovery; the reaper normally handles this at startup)

    Returns the claimed row dict with the post-increment attempt_count, or None
    if the race was lost.  Callers MUST check: None means do not process.
    """
    result = client.rpc("claim_filing", {
        "p_filing_id":    filing_id,
        "p_stale_minutes": _stale_minutes(),
    }).execute()
    if result.data:
        claimed = result.data[0]
        logger.debug(
            "Claimed filing %d (attempt %d/%d)",
            filing_id,
            claimed.get("attempt_count", "?"),
            claimed.get("max_attempts", "?"),
        )
        return claimed
    logger.debug("Lost claim race for filing %d — another worker claimed it", filing_id)
    return None


def reap_stale_filings(client, stale_minutes: int = None) -> list[dict]:
    """
    Recover filings stuck in 'in_progress' longer than stale_minutes.

    A worker crash (OOM, runner timeout, SIGKILL) leaves the row in_progress
    indefinitely: is_eligible() returns False for in_progress, so the filing
    would never be retried without this reaper.

    Transition rules (applied by the DB atomically):
      · attempt_count <  max_attempts → status = 'failed', backoff scheduled
      · attempt_count >= max_attempts → status = 'skipped', permanent

    Should be called:
      1. At listener/sweep startup, before processing any filings.
      2. Before get_retryable_filings() selects failed rows.

    Returns the list of recovered rows (may be empty).
    """
    if stale_minutes is None:
        stale_minutes = _stale_minutes()

    result = client.rpc("reap_stale_filings", {
        "p_stale_minutes": stale_minutes,
    }).execute()
    rows = result.data or []

    if rows:
        logger.warning(
            "Reaped %d stale in_progress filing(s) (threshold: %d min)",
            len(rows), stale_minutes,
        )
        for row in rows:
            logger.warning(
                "  Reaped #%s → %-8s  attempt %s/%s  %s",
                row.get("id"),
                row.get("status"),
                row.get("attempt_count"),
                row.get("max_attempts"),
                str(row.get("pdf_url", ""))[:80],
            )
    return rows


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
    Reap stale in_progress rows, then return failed filings whose backoff
    window has elapsed and which are below their max_attempts ceiling.
    """
    reap_stale_filings(client)

    now = _now()
    result = (
        client.table("filings")
        .select("*")
        .eq("status", "failed")
        .lte("next_attempt_after", now)
        .execute()
    )
    rows = result.data or []
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
