"""Supabase client and database operations."""

import logging
import os
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
    Insert or skip a transaction based on raw_hash deduplication.

    Returns: {'inserted': bool, 'transaction_id': int or None,
              'company_id': int or None, 'insider_id': int or None, 'message': str}
    """
    try:
        # Fast-path dedup check
        existing = client.table("transactions").select("id").eq("raw_hash", raw_hash).execute()
        if existing.data:
            return {
                "inserted": False,
                "transaction_id": existing.data[0]["id"],
                "company_id": None,
                "insider_id": None,
                "message": f"Transaction already exists (hash: {raw_hash[:16]}…)",
            }

        company_id = _resolve_company(client, company_name, isin)
        insider_id = _resolve_insider(client, insider_name, insider_role, company_id, insider_verified, role_category)

        # Derive review_status from needs_review if not explicitly passed
        _review_status = review_status or ("pending_review" if needs_review else "confirmed")

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
            "transaction_type":         transaction_type,
            "economic_intent":          economic_intent,
            "needs_review":             needs_review,
            "review_status":            _review_status,
            "parser_version":           parser_version or "1.0.0",
        }
        # Include optional lineage + quality fields only when set — avoids
        # overwriting DB-level defaults with None for callers that don't compute them.
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

        return {
            "inserted": True,
            "transaction_id": tx.data[0]["id"],
            "company_id": company_id,
            "insider_id": insider_id,
            "message": f"Inserted transaction {tx.data[0]['id']}",
        }

    except Exception as exc:
        logger.error("Failed to upsert transaction (hash: %s): %s", raw_hash[:16], exc)
        return {
            "inserted": False,
            "transaction_id": None,
            "message": f"Error: {exc}",
        }


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
