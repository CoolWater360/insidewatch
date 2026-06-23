"""Supabase client and database operations."""

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from supabase import create_client, Client

from .insiders import normalize_name

logger = logging.getLogger(__name__)

# 20-second timeout on every PostgREST call — prevents indefinite hangs.
_SUPABASE_TIMEOUT = httpx.Timeout(20.0)


def get_supabase_client() -> Client:
    """
    Initialize and return a Supabase client for backend (write) operations.

    Key selection order:
      1. SUPABASE_SERVICE_ROLE_KEY — bypasses RLS; required once RLS is enabled.
      2. SUPABASE_KEY              — anon key fallback; works only while RLS is disabled.

    IMPORTANT: Never expose SUPABASE_SERVICE_ROLE_KEY to the Next.js frontend.
    Set it only as a GitHub Actions secret and in the local Python scraper env.
    """
    url = os.getenv("SUPABASE_URL")
    # Prefer service-role key; fall back to anon key while RLS is still disabled.
    # Remove the anon-key fallback once RLS is enabled (Phase 1 manual step).
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and either SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY "
            "are required. Set them in .env.local or as GitHub Actions secrets."
        )

    if os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        logger.debug("Supabase client: service_role key")
    else:
        logger.warning(
            "Supabase client using anon key — write operations require RLS to be "
            "disabled. Set SUPABASE_SERVICE_ROLE_KEY before enabling RLS."
        )

    client = create_client(url, key)
    # Patch the PostgREST client timeout (supabase-py default is 120s, too long).
    client.postgrest.timeout = _SUPABASE_TIMEOUT
    return client


def _resolve_company(client: Client, company_name: str, isin: Optional[str]) -> int:
    """
    Find or create a company row, returning its id.

    Lookup order:
      1. By name (case-insensitive) — the common path.
      2. By ISIN — catches renames and duplicate seeds.
      3. Insert — with graceful fallback if a unique constraint fires mid-race.

    Never raises on ISIN constraint violations; logs and continues instead.
    """
    # 1. Lookup by name
    by_name = client.table("companies").select("id, isin").ilike("name", company_name).execute()
    if by_name.data:
        company_id = by_name.data[0]["id"]
        existing_isin = by_name.data[0].get("isin")
        if isin and not existing_isin:
            try:
                client.table("companies").update({"isin": isin}).eq("id", company_id).execute()
            except Exception as exc:
                # ISIN already claimed by another row — not fatal, just skip backfill
                logger.warning(
                    "ISIN backfill skipped for %r (ISIN %s conflicts): %s",
                    company_name, isin, exc,
                )
        return company_id

    # 2. Lookup by ISIN (company may exist under a different name)
    if isin:
        by_isin = client.table("companies").select("id").eq("isin", isin).execute()
        if by_isin.data:
            logger.debug(
                "Company %r not found by name; matched existing row %d via ISIN %s",
                company_name, by_isin.data[0]["id"], isin,
            )
            return by_isin.data[0]["id"]

    # 3. Insert — may still race with a concurrent worker on name or ISIN
    payload: dict = {"name": company_name}
    if isin:
        payload["isin"] = isin

    try:
        comp = client.table("companies").insert(payload).execute()
        return comp.data[0]["id"]
    except Exception as insert_exc:
        logger.warning("Company insert failed for %r: %s — attempting recovery", company_name, insert_exc)

        # Recovery: retry lookups in case a concurrent worker just inserted it
        if isin:
            by_isin2 = client.table("companies").select("id").eq("isin", isin).execute()
            if by_isin2.data:
                return by_isin2.data[0]["id"]

        by_name2 = client.table("companies").select("id").ilike("name", company_name).execute()
        if by_name2.data:
            return by_name2.data[0]["id"]

        # Last resort: insert without ISIN to avoid the unique constraint
        try:
            comp2 = client.table("companies").insert({"name": company_name}).execute()
            return comp2.data[0]["id"]
        except Exception as exc2:
            raise RuntimeError(f"Cannot resolve company {company_name!r}: {exc2}") from exc2


def _resolve_insider(
    client: Client,
    full_name: str,
    role: str,
    company_id: int,
    insider_verified: bool,
    role_category: str,
) -> int:
    """
    Find or create an insider row, returning its id.
    Handles concurrent-insert races via try/except + re-lookup.
    """
    # Always normalize before any DB operation so stale ALL-CAPS records
    # don't create new duplicates even if the caller forgot to normalize.
    full_name = normalize_name(full_name)

    # 1. Exact match (fast path — all names in DB are normalized post-migration)
    existing = (
        client.table("insiders")
        .select("id")
        .eq("full_name", full_name)
        .eq("company_id", company_id)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    # 2. Case-insensitive fallback (catches any stale non-normalized record)
    ci_existing = (
        client.table("insiders")
        .select("id")
        .ilike("full_name", full_name)
        .eq("company_id", company_id)
        .execute()
    )
    if ci_existing.data:
        return ci_existing.data[0]["id"]

    try:
        ins = client.table("insiders").insert({
            "full_name": full_name,
            "role": role,
            "company_id": company_id,
            "insider_verified": insider_verified,
            "role_category": role_category,
        }).execute()
        return ins.data[0]["id"]
    except Exception as exc:
        # Race: another thread inserted the same (full_name, company_id) just now
        logger.debug("Insider insert race for %r at company %d: %s — re-querying", full_name, company_id, exc)
        retry = (
            client.table("insiders")
            .select("id")
            .eq("full_name", full_name)
            .eq("company_id", company_id)
            .execute()
        )
        if retry.data:
            return retry.data[0]["id"]
        raise RuntimeError(f"Cannot resolve insider {full_name!r}: {exc}") from exc


# ── Phase 4: Transaction identity and versioning ──────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_identity_hash(
    source_filing_id: int,
    pdf_sha256: str,
    source_transaction_index: int,
    isin: Optional[str],
    direction: str,
) -> str:
    """
    Compute the Phase 4 stable identity hash for a transaction.

    Formula: SHA256(filing_id|pdf_sha256|tx_index|isin|direction)

    Because the hash encodes the transaction's position within its source
    document, two same-day same-price same-insider transactions at different
    indices produce different hashes — both are stored.  Reprocessing the
    same filing with the same parser produces identical hashes — true
    idempotent re-ingest.

    All three positional arguments must be non-None.  Call
    _derive_identity_hash() for the fallback path.
    """
    key = "|".join([
        str(source_filing_id),
        pdf_sha256,
        str(source_transaction_index),
        isin or "no_isin",
        direction or "no_dir",
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def _derive_identity_hash(
    source_filing_id: Optional[int],
    pdf_sha256: Optional[str],
    source_transaction_index: Optional[int],
    isin: Optional[str],
    direction: str,
    raw_hash: str,
) -> str:
    """
    Return the appropriate identity hash for this transaction.

    Uses the new formula when all three positional lineage fields are available
    (all filings processed through Phase 2 + Phase 3 satisfy this).
    Falls back to 'legacy_{raw_hash}' for pre-Phase-2 transactions.
    """
    if (source_filing_id is not None
            and pdf_sha256 is not None
            and source_transaction_index is not None):
        return compute_identity_hash(
            source_filing_id, pdf_sha256, source_transaction_index, isin, direction
        )
    return f"legacy_{raw_hash}"


# Fields compared when deciding whether a re-processed transaction has changed.
_VERSIONED_STR_FIELDS = frozenset([
    "direction", "transaction_date", "transaction_type", "economic_intent",
    "parser_version", "currency", "isin", "instrument_type",
])
_VERSIONED_NUM_FIELDS = frozenset(["quantity", "unit_price", "total_value"])


def _has_changed(existing: dict, new_payload: dict) -> bool:
    """Return True if any semantically significant field differs."""
    for f in _VERSIONED_STR_FIELDS:
        if str(existing.get(f) or "") != str(new_payload.get(f) or ""):
            return True
    for f in _VERSIONED_NUM_FIELDS:
        old, new = existing.get(f), new_payload.get(f)
        if old is None and new is None:
            continue
        try:
            if abs(float(old or 0) - float(new or 0)) > 1e-8:
                return True
        except (TypeError, ValueError):
            if old != new:
                return True
    return False


def _save_version(
    client,
    tx_row: dict,
    *,
    changed_by: str,
    change_reason: Optional[str] = None,
) -> None:
    """Snapshot tx_row into transaction_versions before an update."""
    try:
        client.table("transaction_versions").insert({
            "transaction_id": tx_row["id"],
            "version_number": tx_row.get("version_number", 1),
            "snapshot":        tx_row,
            "changed_by":      changed_by,
            "change_reason":   change_reason,
        }).execute()
    except Exception as exc:
        logger.warning("_save_version failed for tx %s: %s", tx_row.get("id"), exc)


def upsert_transaction(
    client: Client,
    raw_hash: str,
    insider_name: str,
    insider_role: str,
    company_name: str,
    isin: Optional[str],
    instrument_type: str,
    direction: str,
    transaction_date: str,
    filed_date: str,
    quantity: float,
    unit_price: float,
    total_value: float,
    currency: str,
    source_url: str,
    # ── Classification ────────────────────────────────────────────────────────
    insider_verified: bool = True,
    role_category: str = "other",
    transaction_type: str = "buy",
    economic_intent: str = "unclear",
    # ── Quality flags ─────────────────────────────────────────────────────────
    needs_review: bool = False,
    extraction_confidence: Optional[float] = None,
    classification_confidence: Optional[float] = None,
    # ── Review workflow ───────────────────────────────────────────────────────
    review_status: Optional[str] = None,
    review_reason: Optional[str] = None,
    # ── Source lineage ────────────────────────────────────────────────────────
    source_transaction_index: Optional[int] = None,
    raw_document_sha256: Optional[str] = None,
    source_filing_id: Optional[int] = None,
    # ── Parser metadata ───────────────────────────────────────────────────────
    parser_version: Optional[str] = None,
) -> dict:
    """
    Insert or update a transaction using the Phase 4 identity hash.

    Identity is derived from (source_filing_id, pdf_sha256, tx_index, isin,
    direction) — not from (name, date, quantity, price) — so two same-day
    same-price same-insider transactions at different indices are both stored.

    Three outcomes:
      inserted=True,  updated=False  → new transaction, row created
      inserted=False, updated=True   → existing row, fields changed → versioned
      inserted=False, updated=False  → existing row, no change → true dedup

    Returns: {
        'inserted': bool, 'updated': bool,
        'transaction_id': int | None,
        'company_id': int | None,
        'insider_id': int | None,
        'message': str,
    }
    """
    identity_hash = _derive_identity_hash(
        source_filing_id, raw_document_sha256, source_transaction_index,
        isin, direction, raw_hash,
    )
    _review_status = review_status or ("pending_review" if needs_review else "confirmed")
    _parser_version = parser_version or "1.0.0"

    try:
        # Dedup check on the stable identity (not raw_hash).
        existing_result = (
            client.table("transactions")
            .select("*")
            .eq("identity_hash", identity_hash)
            .execute()
        )

        if existing_result.data:
            existing = existing_result.data[0]
            tx_id = existing["id"]

            # Build payload of new values for comparison and potential update.
            comparison_payload = {
                "direction":       direction,
                "transaction_date": transaction_date,
                "transaction_type": transaction_type,
                "economic_intent":  economic_intent,
                "parser_version":   _parser_version,
                "currency":         currency,
                "isin":             isin,
                "instrument_type":  instrument_type,
                "quantity":         quantity,
                "unit_price":       unit_price,
                "total_value":      total_value,
            }

            if not _has_changed(existing, comparison_payload):
                return {
                    "inserted":       False,
                    "updated":        False,
                    "transaction_id": tx_id,
                    "company_id":     existing.get("company_id"),
                    "insider_id":     existing.get("insider_id"),
                    "message":        f"Unchanged (identity: {identity_hash[:16]}…)",
                }

            # Fields changed — save the current state as a version record,
            # then update with the new values.
            _save_version(
                client, existing,
                changed_by=_parser_version,
                change_reason=f"re-processed: {_parser_version}",
            )
            update_payload: dict = {
                **comparison_payload,
                "version_number": existing.get("version_number", 1) + 1,
                "updated_at":     _now_utc(),
            }
            if extraction_confidence is not None:
                update_payload["extraction_confidence"] = extraction_confidence
            if classification_confidence is not None:
                update_payload["classification_confidence"] = classification_confidence
            if review_reason is not None:
                update_payload["review_reason"] = review_reason

            client.table("transactions").update(update_payload).eq("id", tx_id).execute()
            logger.info(
                "Transaction %d versioned (identity: %s…): parser update",
                tx_id, identity_hash[:16],
            )
            return {
                "inserted":       False,
                "updated":        True,
                "transaction_id": tx_id,
                "company_id":     existing.get("company_id"),
                "insider_id":     existing.get("insider_id"),
                "message":        f"Updated transaction {tx_id} (new version)",
            }

        # ── New transaction ───────────────────────────────────────────────────
        company_id = _resolve_company(client, company_name, isin)
        insider_id = _resolve_insider(
            client, insider_name, insider_role, company_id, insider_verified, role_category
        )

        payload: dict = {
            "insider_id":               insider_id,
            "company_id":               company_id,
            "transaction_date":         transaction_date,
            "filed_date":               filed_date,
            "direction":                direction,
            "instrument_type":          instrument_type,
            "isin":                     isin,
            "quantity":                 quantity,
            "unit_price":               unit_price,
            "total_value":              total_value,
            "currency":                 currency,
            "source_url":               source_url,
            "raw_hash":                 raw_hash,
            "identity_hash":            identity_hash,
            "transaction_type":         transaction_type,
            "economic_intent":          economic_intent,
            "needs_review":             needs_review,
            "review_status":            _review_status,
            "parser_version":           _parser_version,
            "is_current":               True,
            "version_number":           1,
        }
        if extraction_confidence is not None:
            payload["extraction_confidence"] = extraction_confidence
        if classification_confidence is not None:
            payload["classification_confidence"] = classification_confidence
        if review_reason is not None:
            payload["review_reason"] = review_reason
        if source_transaction_index is not None:
            payload["source_transaction_index"] = source_transaction_index
        if raw_document_sha256 is not None:
            payload["raw_document_sha256"] = raw_document_sha256
        if source_filing_id is not None:
            payload["source_filing_id"] = source_filing_id

        tx = client.table("transactions").insert(payload).execute()
        tx_id = tx.data[0]["id"]
        logger.debug("Inserted transaction %d (identity: %s…)", tx_id, identity_hash[:16])
        return {
            "inserted":       True,
            "updated":        False,
            "transaction_id": tx_id,
            "company_id":     company_id,
            "insider_id":     insider_id,
            "message":        f"Inserted transaction {tx_id}",
        }

    except Exception as exc:
        logger.error(
            "Failed to upsert transaction (identity: %s…): %s", identity_hash[:16], exc
        )
        return {
            "inserted":       False,
            "updated":        False,
            "transaction_id": None,
            "company_id":     None,
            "insider_id":     None,
            "message":        f"Error: {exc}",
        }


# ── Operator correction ───────────────────────────────────────────────────────

def correct_transaction(
    client: Client,
    transaction_id: int,
    changes: dict,
    *,
    reason: str,
    changed_by: str = "operator",
) -> bool:
    """
    Apply an operator correction, preserving the current state as a version.

    The current row is snapshotted into transaction_versions before being
    updated.  version_number is incremented.  Returns True on success.
    """
    result = client.table("transactions").select("*").eq("id", transaction_id).execute()
    if not result.data:
        logger.warning("correct_transaction: transaction %d not found", transaction_id)
        return False

    current = result.data[0]
    _save_version(client, current, changed_by=changed_by, change_reason=reason)

    update_payload = {
        **changes,
        "version_number": current.get("version_number", 1) + 1,
        "updated_at":     _now_utc(),
    }
    client.table("transactions").update(update_payload).eq("id", transaction_id).execute()
    logger.info(
        "Transaction %d corrected by %r: %s", transaction_id, changed_by, reason
    )
    return True


def supersede_transaction(
    client: Client,
    old_id: int,
    new_id: int,
    *,
    reason: str,
    changed_by: str = "operator",
) -> bool:
    """
    Mark old_id as superseded by new_id.

    Records a version snapshot of old_id, then sets:
      old: is_current=False, superseded_by=new_id, superseded_at=now
    Returns True on success.
    """
    result = client.table("transactions").select("*").eq("id", old_id).execute()
    if not result.data:
        logger.warning("supersede_transaction: transaction %d not found", old_id)
        return False

    current = result.data[0]
    _save_version(client, current, changed_by=changed_by, change_reason=f"superseded: {reason}")

    now = _now_utc()
    client.table("transactions").update({
        "is_current":    False,
        "superseded_by": new_id,
        "superseded_at": now,
        "version_number": current.get("version_number", 1) + 1,
        "updated_at":    now,
    }).eq("id", old_id).execute()
    logger.info("Transaction %d superseded by %d: %s", old_id, new_id, reason)
    return True


# ── Audit retrieval ───────────────────────────────────────────────────────────

def get_transaction_history(client: Client, transaction_id: int) -> dict:
    """
    Return the current transaction row, all previous version snapshots, and
    the source filing (if linked).

    Returns: {
        'current': dict | None,
        'versions': list[dict],
        'filing': dict | None,
    }
    """
    tx_result = client.table("transactions").select("*").eq("id", transaction_id).execute()
    if not tx_result.data:
        return {"current": None, "versions": [], "filing": None}

    current = tx_result.data[0]

    versions_result = (
        client.table("transaction_versions")
        .select("*")
        .eq("transaction_id", transaction_id)
        .order("version_number")
        .execute()
    )

    filing = None
    if current.get("source_filing_id"):
        f_result = (
            client.table("filings")
            .select("id, pdf_url, status, pdf_sha256, storage_path, filing_date, company_name")
            .eq("id", current["source_filing_id"])
            .execute()
        )
        filing = f_result.data[0] if f_result.data else None

    return {
        "current":  current,
        "versions": versions_result.data or [],
        "filing":   filing,
    }


# ── Filing processing run ─────────────────────────────────────────────────────

def record_filing_processing_run(
    client: Client,
    filing_id: int,
    *,
    parser_version: str,
    transactions_found: int,
    transactions_inserted: int,
    transactions_versioned: int,
    transactions_unchanged: int,
) -> None:
    """
    Record one run of the scraper over a specific filing.  Non-fatal.
    """
    try:
        client.table("filing_processing_runs").insert({
            "filing_id":               filing_id,
            "parser_version":          parser_version,
            "transactions_found":      transactions_found,
            "transactions_inserted":   transactions_inserted,
            "transactions_versioned":  transactions_versioned,
            "transactions_unchanged":  transactions_unchanged,
        }).execute()
    except Exception as exc:
        logger.warning("record_filing_processing_run failed for filing %d: %s", filing_id, exc)


def load_seed_companies(client: Client, csv_path: str) -> dict:
    """Load companies from seed CSV (name required; ticker/isin optional) into the database."""
    import csv

    count = {"inserted": 0, "skipped": 0}

    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("name", "").strip()
                if not name:
                    continue

                ticker = row.get("ticker", "").strip() or None
                isin   = row.get("isin",   "").strip() or None
                sector = row.get("sector", "").strip() or None

                existing = client.table("companies").select("id").eq("name", name).execute()
                if existing.data:
                    count["skipped"] += 1
                    continue

                payload: dict = {"name": name}
                if ticker: payload["ticker"] = ticker
                if isin:   payload["isin"]   = isin
                if sector: payload["sector"] = sector

                try:
                    client.table("companies").insert(payload).execute()
                    count["inserted"] += 1
                except Exception as exc:
                    logger.warning("Failed to insert company %s: %s", name, exc)
                    count["skipped"] += 1

    except FileNotFoundError:
        logger.error("Seed file not found: %s", csv_path)

    return count
