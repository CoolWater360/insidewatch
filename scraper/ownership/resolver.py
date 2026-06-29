"""Exact-only issuer/entity resolution for the ownership pilot.

Resolution is deliberately stricter than scraper/issuer_resolver.py:

  * EXACT matches only — ISIN exact, alias exact, canonical-name exact.
    No ilike/fuzzy "suggestion" tier (that produces heuristic matches which
    must not be auto-applied in this pilot).
  * Read-only in dry-run — no unmatched_issuers queue writes, no entity
    inserts.  Entity creation happens only when apply=True.
  * Graceful degradation — if the Supabase client is None or a query fails,
    resolution returns "unresolved" instead of raising, so the dry-run
    validation report is always produced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class IssuerResolution:
    issuer_id: Optional[int]
    method: str   # 'isin' | 'alias' | 'canonical' | 'unresolved' | 'db_unavailable'


@dataclass
class EntityResolution:
    entity_id: Optional[int]
    method: str   # 'exact_name' | 'lei' | 'created' | 'would_create' |
                  # 'unresolved' | 'db_unavailable'
    legal_name: str = ""


# ── Issuer resolution (read-only) ──────────────────────────────────────────────

def resolve_issuer_exact(
    client: Any,
    issuer_raw_name: str,
    isin: Optional[str] = None,
) -> IssuerResolution:
    """Resolve an issuer by EXACT ISIN, alias, or canonical name only."""
    if client is None:
        return IssuerResolution(None, "db_unavailable")
    try:
        # 1. ISIN exact → securities
        if isin:
            rows = (
                client.table("securities").select("issuer_id")
                .eq("isin", isin).limit(1).execute()
            )
            if rows.data:
                return IssuerResolution(rows.data[0]["issuer_id"], "isin")

        # 2. Alias exact (case-insensitive equality: ilike WITHOUT wildcards)
        if issuer_raw_name:
            arows = (
                client.table("issuer_aliases").select("issuer_id")
                .ilike("alias", issuer_raw_name).limit(1).execute()
            )
            if arows.data:
                return IssuerResolution(arows.data[0]["issuer_id"], "alias")

            # 3. Canonical name exact (case-insensitive equality)
            crows = (
                client.table("issuers").select("id")
                .ilike("canonical_name", issuer_raw_name).limit(1).execute()
            )
            if crows.data:
                return IssuerResolution(crows.data[0]["id"], "canonical")

        return IssuerResolution(None, "unresolved")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("resolve_issuer_exact failed for %r: %s", issuer_raw_name, exc)
        return IssuerResolution(None, "db_unavailable")


# ── Entity resolution / creation ───────────────────────────────────────────────

_LEGAL_SUFFIX_HINTS = (
    ("HOLDING", "holding_company"),
    ("TRUST", "trust"),
    ("FIDUCIAR", "fiduciary"),
    ("FONDAZION", "foundation"),
    ("FOUNDATION", "foundation"),
    ("FUND", "fund"),
    ("SGR", "company"),
    ("SICAV", "fund"),
    ("S.P.A", "company"),
    ("SPA", "company"),
    ("S.R.L", "company"),
    ("SRL", "company"),
    ("S.A.", "company"),
    ("SARL", "company"),
    ("S.À R.L", "company"),
    ("LLC", "company"),
    ("INC", "company"),
    ("PLC", "company"),
    (" LTD", "company"),
    (" NV", "company"),
    (" SE", "company"),
    (" AG", "company"),
)


def infer_entity_type(legal_name: str) -> str:
    """Heuristic entity_type from the legal-name suffix.

    The guess is a *heuristic_suggestion*; the created entity always starts as
    review_status='pending_review' so a human confirms it.
    """
    up = f" {(legal_name or '').upper()} "
    for marker, etype in _LEGAL_SUFFIX_HINTS:
        if marker in up:
            return etype
    # No legal-entity marker → most likely a natural person.
    return "natural_person"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_or_create_entity(
    client: Any,
    legal_name: str,
    *,
    apply: bool,
) -> EntityResolution:
    """Find an entity by exact normalised legal name; optionally create it.

    In dry-run (apply=False) an unseen entity returns method='would_create'
    and entity_id=None.  In apply mode it is inserted with
    review_status='pending_review' and method='created'.
    """
    legal_name = (legal_name or "").strip()
    if not legal_name:
        return EntityResolution(None, "unresolved", "")
    if client is None:
        return EntityResolution(None, "db_unavailable", legal_name)

    try:
        existing = (
            client.table("entities").select("id")
            .ilike("legal_name", legal_name).limit(1).execute()
        )
        if existing.data:
            return EntityResolution(existing.data[0]["id"], "exact_name", legal_name)

        if not apply:
            return EntityResolution(None, "would_create", legal_name)

        payload = {
            "legal_name": legal_name,
            "entity_type": infer_entity_type(legal_name),
            "review_status": "pending_review",
            "updated_at": _now_utc(),
        }
        ins = client.table("entities").insert(payload).execute()
        return EntityResolution(ins.data[0]["id"], "created", legal_name)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("find_or_create_entity failed for %r: %s", legal_name, exc)
        return EntityResolution(None, "db_unavailable", legal_name)
