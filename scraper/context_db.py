"""Database helpers for Phase 17A context data layer.

Covers:
  upsert_context_source  — find-or-create by source_url (idempotent)
  supersede_context_event — mark an event row is_current=FALSE with versioning
  assert_source_provenance — validate provenance fields before writing an event

These helpers follow the same patterns as db.py (supersede_transaction,
upsert_transaction) but work across the four context event tables:
  ownership_events, governance_events, buyback_events, corporate_events,
and entity_relationships (which follows the same versioning pattern).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tables that support the versioning pattern used by these helpers.
_VERSIONED_CONTEXT_TABLES = frozenset({
    "ownership_events",
    "governance_events",
    "buyback_events",
    "corporate_events",
    "entity_relationships",
    "context_event_links",
})

# Valid confidence levels (matches CHECK constraints in migrations 017–019).
_VALID_CONFIDENCE = frozenset({
    "parsed_fact",
    "heuristic_suggestion",
    "reviewer_confirmed",
})


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── assert_source_provenance ──────────────────────────────────────────────────

def assert_source_provenance(event: dict, table: str) -> None:
    """
    Validate that a context event dict meets provenance requirements before
    it is written to the database.

    Raises ValueError if any rule is violated:
      1. source_id must be present and non-None.
      2. confidence must be one of the three valid levels.
      3. reviewer_confirmed confidence requires reviewed_by to be set.

    This is an application-level guard, not a substitute for DB constraints.
    Call it before every INSERT/UPDATE on a context event table.

    Args:
        event: dict of column values about to be written.
        table: name of the target context event table (used in error messages).
    """
    source_id = event.get("source_id")
    if source_id is None:
        raise ValueError(
            f"{table}: source_id is required — every context event must be "
            "traceable to a primary source (context_sources row)."
        )

    confidence = event.get("confidence")
    if confidence is not None and confidence not in _VALID_CONFIDENCE:
        raise ValueError(
            f"{table}: invalid confidence {confidence!r}. "
            f"Must be one of: {sorted(_VALID_CONFIDENCE)}"
        )

    if confidence == "reviewer_confirmed":
        reviewed_by = event.get("reviewed_by")
        if not reviewed_by:
            raise ValueError(
                f"{table}: confidence='reviewer_confirmed' requires reviewed_by "
                "to be set to the operator or reviewer identity."
            )


# ── upsert_context_source ─────────────────────────────────────────────────────

def upsert_context_source(
    client: Any,
    *,
    source_url: str,
    source_type: str,
    publisher: Optional[str] = None,
    document_title: Optional[str] = None,
    publication_timestamp: Optional[str] = None,
    document_hash: Optional[str] = None,
    storage_path: Optional[str] = None,
    raw_text: Optional[str] = None,
    issuer_id: Optional[int] = None,
    ingestion_run_id: Optional[str] = None,
) -> int:
    """
    Find or create a context_sources row by source_url, returning its id.

    If a row with matching source_url already exists, the existing id is
    returned and no columns are updated (preserving the original discovery
    metadata).  This is intentional: the first ingestion wins on immutable
    columns (discovered_timestamp, document_hash).

    If the row does not exist, it is inserted and the new id is returned.

    Args:
        client: Supabase client (service-role).
        source_url: canonical URL — the natural identity key.
        source_type: CHECK-constrained vocabulary (see migration 016).
        publisher: who published the document.
        document_title: title as shown on the source site.
        publication_timestamp: ISO 8601 string, UTC.
        document_hash: SHA-256 hex of downloaded bytes.
        storage_path: INTERNAL ONLY path in the storage backend.
        raw_text: INTERNAL ONLY full extracted text.
        issuer_id: optional FK to issuers.
        ingestion_run_id: ingestion batch identifier.

    Returns:
        int: the context_sources.id for this source_url.
    """
    existing = (
        client.table("context_sources")
        .select("id")
        .eq("source_url", source_url)
        .limit(1)
        .execute()
    )
    if existing.data:
        source_id = existing.data[0]["id"]
        logger.debug("context_sources: existing id=%d for url=%r", source_id, source_url)
        return source_id

    payload: dict[str, Any] = {
        "source_url":   source_url,
        "source_type":  source_type,
        "updated_at":   _now_utc(),
    }
    if publisher is not None:
        payload["publisher"] = publisher
    if document_title is not None:
        payload["document_title"] = document_title
    if publication_timestamp is not None:
        payload["publication_timestamp"] = publication_timestamp
    if document_hash is not None:
        payload["document_hash"] = document_hash
    if storage_path is not None:
        payload["storage_path"] = storage_path
    if raw_text is not None:
        payload["raw_text"] = raw_text
    if issuer_id is not None:
        payload["issuer_id"] = issuer_id
    if ingestion_run_id is not None:
        payload["ingestion_run_id"] = ingestion_run_id

    result = client.table("context_sources").insert(payload).execute()
    source_id = result.data[0]["id"]
    logger.info("context_sources: inserted id=%d for url=%r", source_id, source_url)
    return source_id


# ── supersede_context_event ───────────────────────────────────────────────────

def supersede_context_event(
    client: Any,
    table: str,
    old_id: int,
    new_id: int,
    *,
    reason: str,
    changed_by: str = "operator",
) -> bool:
    """
    Mark old_id in the given context event table as superseded by new_id.

    Sets on the old row:
        is_current     = FALSE
        superseded_by  = new_id
        superseded_at  = now()
        version_number = version_number + 1
        updated_at     = now()

    This mirrors supersede_transaction() in db.py but works across the five
    versioned context tables: ownership_events, governance_events,
    buyback_events, corporate_events, entity_relationships,
    and context_event_links.

    Args:
        client:     Supabase client (service-role).
        table:      Target table name (must be in _VERSIONED_CONTEXT_TABLES).
        old_id:     PK of the row being superseded.
        new_id:     PK of the row replacing it.
        reason:     Human-readable description of why the supersession occurred.
        changed_by: Operator id, script name, or parser version string.

    Returns:
        True on success, False if old_id was not found.

    Raises:
        ValueError: if table is not a recognised versioned context table.
    """
    if table not in _VERSIONED_CONTEXT_TABLES:
        raise ValueError(
            f"supersede_context_event: unknown table {table!r}. "
            f"Must be one of: {sorted(_VERSIONED_CONTEXT_TABLES)}"
        )

    result = client.table(table).select("*").eq("id", old_id).execute()
    if not result.data:
        logger.warning(
            "supersede_context_event: %s id=%d not found — cannot supersede",
            table, old_id,
        )
        return False

    current = result.data[0]
    now = _now_utc()

    client.table(table).update({
        "is_current":     False,
        "superseded_by":  new_id,
        "superseded_at":  now,
        "version_number": current.get("version_number", 1) + 1,
        "updated_at":     now,
    }).eq("id", old_id).execute()

    logger.info(
        "supersede_context_event: %s id=%d superseded by id=%d (changed_by=%r, reason=%r)",
        table, old_id, new_id, changed_by, reason,
    )
    return True
