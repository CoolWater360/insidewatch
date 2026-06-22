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
) -> dict:
    """
    Insert or skip transaction based on raw_hash deduplication.

    Returns: {'inserted': bool, 'transaction_id': int or None, 'message': str}
    """
    try:
        # Check if transaction already exists
        existing = client.table("transactions").select("id").eq("raw_hash", raw_hash).execute()
        if existing.data:
            return {
                "inserted": False,
                "transaction_id": existing.data[0]["id"],
                "message": f"Transaction already exists (hash: {raw_hash[:16]}…)",
            }

        # Upsert company — case-insensitive lookup to avoid duplicates from
        # seed (Title Case) vs PDF header (ALL CAPS) name divergence.
        company_result = client.table("companies").select("id, isin").ilike("name", company_name).execute()
        if company_result.data:
            company_id = company_result.data[0]["id"]
            # Backfill ISIN if we now have one and the row lacks it
            if isin and not company_result.data[0].get("isin"):
                client.table("companies").update({"isin": isin}).eq("id", company_id).execute()
        else:
            insert_payload: dict = {"name": company_name}
            if isin:
                insert_payload["isin"] = isin
            comp = client.table("companies").insert(insert_payload).execute()
            company_id = comp.data[0]["id"]

        # Upsert insider
        insider_result = client.table("insiders").select("id").eq("full_name", insider_name).eq(
            "company_id", company_id
        ).execute()
        if insider_result.data:
            insider_id = insider_result.data[0]["id"]
        else:
            ins = client.table("insiders").insert({
                "full_name": insider_name,
                "role": insider_role,
                "company_id": company_id,
                "insider_verified": insider_verified,
                "role_category": role_category,
            }).execute()
            insider_id = ins.data[0]["id"]

        # Insert transaction
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
        }).execute()

        return {
            "inserted": True,
            "transaction_id": tx.data[0]["id"],
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
                isin = row.get("isin", "").strip() or None
                sector = row.get("sector", "").strip() or None

                existing = client.table("companies").select("id").eq("name", name).execute()
                if existing.data:
                    count["skipped"] += 1
                    continue

                payload: dict = {"name": name}
                if ticker:
                    payload["ticker"] = ticker
                if isin:
                    payload["isin"] = isin
                if sector:
                    payload["sector"] = sector

                try:
                    client.table("companies").insert(payload).execute()
                    count["inserted"] += 1
                except Exception as exc:
                    logger.warning("Failed to insert company %s: %s", name, exc)
                    count["skipped"] += 1

    except FileNotFoundError:
        logger.error("Seed file not found: %s", csv_path)

    return count
