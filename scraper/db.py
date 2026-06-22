"""Supabase client and database operations."""

import logging
import os
from typing import Optional

import httpx
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# 20-second timeout on every PostgREST call — prevents indefinite hangs.
_SUPABASE_TIMEOUT = httpx.Timeout(20.0)


def get_supabase_client() -> Client:
    """Initialize and return Supabase client from environment variables."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_KEY environment variables are required. "
            "Set them in .env.local or your shell environment."
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
    existing = (
        client.table("insiders")
        .select("id")
        .eq("full_name", full_name)
        .eq("company_id", company_id)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

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
    insider_verified: bool = True,
    role_category: str = "other",
    transaction_type: str = "buy",
    needs_review: bool = False,
) -> dict:
    """
    Insert or skip a transaction based on raw_hash deduplication.

    Returns: {'inserted': bool, 'transaction_id': int or None, 'message': str}
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

        tx = client.table("transactions").insert({
            "insider_id": insider_id,
            "company_id": company_id,
            "transaction_date": transaction_date,
            "filed_date": filed_date,
            "direction": direction,
            "instrument_type": instrument_type,
            "isin": isin,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_value": total_value,
            "currency": currency,
            "source_url": source_url,
            "raw_hash": raw_hash,
            "transaction_type": transaction_type,
            "needs_review": needs_review,
        }).execute()

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
