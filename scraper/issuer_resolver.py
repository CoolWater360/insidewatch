"""
Issuer resolution — Phase 6.

Resolves a raw company name and/or ISIN extracted from a filing PDF to a
canonical issuer record in the issuer master.

Resolution priority:
  1. ISIN  → securities.isin exact lookup          (highest confidence)
  2. Alias → issuer_aliases exact case-insensitive  (high confidence)
  3. ilike on issuers.canonical_name               (suggestion only → review queue)
  4. No match                                       (review queue)

Steps 3 and 4 queue an entry in unmatched_issuers and return needs_review=True.
The caller still proceeds with the old companies-table fallback so ingestion is
never blocked by a missing issuer master entry.

Public API
----------
resolve_issuer(client, company_name, isin, *, filing_id=None) -> ResolveResult
link_company_to_issuer(client, company_id, issuer_id) -> None
"""

import logging
from typing import Optional, NamedTuple

from supabase import Client

logger = logging.getLogger(__name__)


class ResolveResult(NamedTuple):
    issuer_id: Optional[int]
    method: str          # 'isin' | 'alias' | 'suggestion' | 'unmatched'
    needs_review: bool


def resolve_issuer(
    client: Client,
    company_name: str,
    isin: Optional[str],
    *,
    filing_id: Optional[int] = None,
) -> ResolveResult:
    """
    Resolve raw name/ISIN to an issuer_id.

    Non-fatal: never raises.  On any DB error, logs and returns
    ResolveResult(None, 'error', needs_review=True).
    """
    try:
        return _resolve(client, company_name, isin, filing_id=filing_id)
    except Exception as exc:
        logger.error("resolve_issuer failed for %r / %s: %s", company_name, isin, exc)
        return ResolveResult(issuer_id=None, method="error", needs_review=True)


def _resolve(
    client: Client,
    company_name: str,
    isin: Optional[str],
    filing_id: Optional[int],
) -> ResolveResult:
    # ── Step 1: ISIN → securities ─────────────────────────────────────────────
    if isin:
        rows = (
            client.table("securities")
            .select("issuer_id")
            .eq("isin", isin)
            .limit(1)
            .execute()
        )
        if rows.data:
            issuer_id = rows.data[0]["issuer_id"]
            logger.debug(
                "Resolved %r via ISIN %s → issuer %d", company_name, isin, issuer_id
            )
            return ResolveResult(issuer_id=issuer_id, method="isin", needs_review=False)

    # ── Step 2: Alias exact lookup (case-insensitive) ─────────────────────────
    # ilike without % wildcards = case-insensitive equality.
    alias_rows = (
        client.table("issuer_aliases")
        .select("issuer_id")
        .ilike("alias", company_name)
        .limit(1)
        .execute()
    )
    if alias_rows.data:
        issuer_id = alias_rows.data[0]["issuer_id"]
        logger.debug(
            "Resolved %r via alias match → issuer %d", company_name, issuer_id
        )
        return ResolveResult(issuer_id=issuer_id, method="alias", needs_review=False)

    # ── Step 3: ilike suggestion on canonical_name (fallback, review only) ────
    suggestion_id: Optional[int] = None
    try:
        # Strip common legal suffixes to improve suggestion hit rate.
        stripped = _strip_legal_suffix(company_name)
        pattern = f"%{stripped}%"
        sugg_rows = (
            client.table("issuers")
            .select("id")
            .ilike("canonical_name", pattern)
            .limit(1)
            .execute()
        )
        if sugg_rows.data:
            suggestion_id = sugg_rows.data[0]["id"]
    except Exception as exc:
        logger.debug("Suggestion lookup failed for %r: %s", company_name, exc)

    # ── Step 4: Queue for review ──────────────────────────────────────────────
    _queue_unmatched(client, company_name, isin, filing_id, suggestion_id)

    method = "suggestion" if suggestion_id else "unmatched"
    logger.debug(
        "No issuer match for %r (ISIN %s) — queued for review (suggestion=%s)",
        company_name, isin, suggestion_id,
    )
    return ResolveResult(issuer_id=None, method=method, needs_review=True)


def _strip_legal_suffix(name: str) -> str:
    """Remove common Italian and international legal-form suffixes."""
    import re
    return re.sub(
        r"\s+(?:S\.?p\.?A\.?|S\.?r\.?l\.?|S\.?a\.?|N\.?V\.?|PLC|Ltd|Inc|SE|"
        r"SPA|SRL|NV|SA|B\.?V\.?)\.?\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()


def _queue_unmatched(
    client: Client,
    raw_name: str,
    raw_isin: Optional[str],
    filing_id: Optional[int],
    suggestion_issuer_id: Optional[int],
) -> None:
    """
    Insert or ignore an unmatched_issuers row for this raw_name.

    Idempotent: the UNIQUE(raw_name) constraint means a second call for the
    same name is silently ignored via on_conflict=ignore.
    Non-fatal: any DB error is logged, not raised.
    """
    try:
        payload: dict = {
            "raw_name": raw_name,
            "status": "pending",
        }
        if raw_isin:
            payload["raw_isin"] = raw_isin
        if filing_id:
            payload["filing_id"] = filing_id
        if suggestion_issuer_id:
            payload["suggestion_issuer_id"] = suggestion_issuer_id

        client.table("unmatched_issuers").upsert(
            payload,
            on_conflict="raw_name",
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        logger.warning("Failed to queue unmatched issuer %r: %s", raw_name, exc)


def link_company_to_issuer(
    client: Client,
    company_id: int,
    issuer_id: int,
) -> None:
    """
    Set companies.issuer_id = issuer_id if not already set.

    Non-fatal: logs on failure and continues.
    """
    try:
        # Only update if not already linked (avoids unnecessary writes).
        existing = (
            client.table("companies")
            .select("issuer_id")
            .eq("id", company_id)
            .limit(1)
            .execute()
        )
        if existing.data and existing.data[0].get("issuer_id") is None:
            client.table("companies").update({"issuer_id": issuer_id}).eq(
                "id", company_id
            ).execute()
    except Exception as exc:
        logger.warning(
            "link_company_to_issuer failed (company=%d issuer=%d): %s",
            company_id, issuer_id, exc,
        )


def get_unmatched(client: Client, status: str = "pending", limit: int = 50) -> list:
    """Return unmatched_issuers rows for the given status, newest first."""
    rows = (
        client.table("unmatched_issuers")
        .select(
            "id, raw_name, raw_isin, filing_id, suggestion_issuer_id, "
            "discovered_at, status, resolved_to, resolved_at, resolved_by"
        )
        .eq("status", status)
        .order("discovered_at", desc=True)
        .limit(limit)
        .execute()
    )
    return rows.data or []


def mark_resolved(
    client: Client,
    unmatched_id: int,
    issuer_id: int,
    *,
    resolved_by: str = "operator",
) -> None:
    """Mark an unmatched entry as resolved and link it to an issuer."""
    from datetime import datetime, timezone
    client.table("unmatched_issuers").update({
        "status": "resolved",
        "resolved_to": issuer_id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": resolved_by,
    }).eq("id", unmatched_id).execute()


def mark_rejected(
    client: Client,
    unmatched_id: int,
    *,
    resolved_by: str = "operator",
) -> None:
    """Mark an unmatched entry as rejected (not a real issuer)."""
    from datetime import datetime, timezone
    client.table("unmatched_issuers").update({
        "status": "rejected",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_by": resolved_by,
    }).eq("id", unmatched_id).execute()


def create_issuer(
    client: Client,
    *,
    canonical_name: str,
    short_name: Optional[str] = None,
    isin: Optional[str] = None,
    lei: Optional[str] = None,
    ticker: Optional[str] = None,
    market: Optional[str] = None,
    sector: Optional[str] = None,
    country: str = "IT",
    aliases: Optional[list] = None,
) -> int:
    """
    Insert a new issuer with its primary aliases and (optionally) a security.

    Returns the new issuer_id.
    Raises on failure — this is an operator action, not an automated path.
    """
    from datetime import datetime, timezone

    # Insert issuer
    payload: dict = {
        "canonical_name": canonical_name,
        "country": country,
        "status": "active",
    }
    if short_name:
        payload["short_name"] = short_name
    if lei:
        payload["lei"] = lei
    if market:
        payload["market"] = market
    if sector:
        payload["sector"] = sector

    result = client.table("issuers").insert(payload).execute()
    issuer_id: int = result.data[0]["id"]

    # Add canonical_name as primary name alias
    _add_alias(client, issuer_id, canonical_name, "name", is_primary=True)

    # Add supplied aliases
    for alias_entry in (aliases or []):
        if isinstance(alias_entry, str):
            _add_alias(client, issuer_id, alias_entry, "name")
        elif isinstance(alias_entry, dict):
            _add_alias(
                client, issuer_id,
                alias_entry["alias"],
                alias_entry.get("alias_type", "name"),
                source=alias_entry.get("source", "manual"),
            )

    if ticker:
        _add_alias(client, issuer_id, ticker, "ticker")

    if lei:
        _add_alias(client, issuer_id, lei, "lei")

    # Add ISIN as a security row
    if isin:
        _add_security(client, issuer_id, isin)
        _add_alias(client, issuer_id, isin, "isin")

    logger.info("Created issuer %d: %r (ISIN=%s)", issuer_id, canonical_name, isin)
    return issuer_id


def _add_alias(
    client: Client,
    issuer_id: int,
    alias: str,
    alias_type: str,
    *,
    is_primary: bool = False,
    source: str = "manual",
) -> None:
    """Upsert an alias row — silently skips if it already exists for this issuer."""
    try:
        client.table("issuer_aliases").upsert(
            {
                "issuer_id": issuer_id,
                "alias": alias,
                "alias_type": alias_type,
                "source": source,
                "is_primary": is_primary,
            },
            on_conflict="alias,alias_type",
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        logger.warning("Failed to add alias %r (%s) to issuer %d: %s", alias, alias_type, issuer_id, exc)


def _add_security(
    client: Client,
    issuer_id: int,
    isin: str,
    *,
    instrument_type: str = "equity",
    instrument_name: Optional[str] = None,
    currency: str = "EUR",
) -> None:
    try:
        payload: dict = {
            "issuer_id": issuer_id,
            "isin": isin,
            "instrument_type": instrument_type,
            "currency": currency,
        }
        if instrument_name:
            payload["instrument_name"] = instrument_name
        client.table("securities").upsert(payload, on_conflict="isin", ignore_duplicates=True).execute()
    except Exception as exc:
        logger.warning("Failed to add security %s to issuer %d: %s", isin, issuer_id, exc)
