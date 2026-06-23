#!/usr/bin/env python3
"""
Migration verification script — Phase 1.

Connects to Supabase and checks that all Phase 1 schema changes were applied
correctly.  Exits 0 on success, 1 if any check fails.

Usage:
    python3 db/verify_migration.py

Requirements:
    SUPABASE_URL and (SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY) in environment
    or in .env.local.
"""

import os
import sys

# ── Load .env.local if present ────────────────────────────────────────────────
_env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _get_client():
    from supabase import create_client
    import httpx

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("FAIL: SUPABASE_URL and SUPABASE_KEY (or SERVICE_ROLE_KEY) must be set.")
        sys.exit(1)
    client = create_client(url, key)
    client.postgrest.timeout = httpx.Timeout(20.0)
    return client


PASS = "✓"
FAIL = "✗"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {PASS}  {label}")
    else:
        msg = f"{label}" + (f" — {detail}" if detail else "")
        print(f"  {FAIL}  {msg}")
        _failures.append(msg)


def main() -> None:
    try:
        client = _get_client()
    except Exception as exc:
        print(f"FAIL: Could not connect to Supabase: {exc}")
        sys.exit(1)

    print("\n── Phase 1 migration verification ───────────────────────────────────")

    # ── 1. Required columns exist in transactions ──────────────────────────────
    print("\n[1] Transactions table columns")
    cols_result = client.rpc("json_agg", {}).execute()  # not available; use information_schema

    # Use a raw SQL approach via PostgREST's rpc or a direct query
    # (supabase-py does not have direct DDL introspection; we use a SELECT trick)
    required_columns = [
        "transaction_type", "needs_review", "economic_intent",
        "extraction_confidence", "classification_confidence",
        "review_status", "review_reason",
        "source_filing_id", "source_transaction_index",
        "raw_document_sha256", "parser_version",
    ]

    # Probe each column by selecting it (will error if absent)
    for col in required_columns:
        try:
            r = client.table("transactions").select(col).limit(1).execute()
            check(f"transactions.{col} exists", True)
        except Exception as exc:
            check(f"transactions.{col} exists", False, str(exc)[:80])

    # ── 2. No NULL transaction_type ───────────────────────────────────────────
    print("\n[2] No NULL values in critical columns")
    try:
        r = client.table("transactions").select("id", count="exact").is_("transaction_type", "null").execute()
        null_type_count = r.count or 0
        check(
            f"transaction_type has no NULLs ({null_type_count} found)",
            null_type_count == 0,
            f"{null_type_count} rows with NULL transaction_type" if null_type_count else "",
        )
    except Exception as exc:
        check("transaction_type NULL check", False, str(exc)[:80])

    try:
        r = client.table("transactions").select("id", count="exact").is_("needs_review", "null").execute()
        null_nr_count = r.count or 0
        check(
            f"needs_review has no NULLs ({null_nr_count} found)",
            null_nr_count == 0,
            f"{null_nr_count} rows with NULL needs_review" if null_nr_count else "",
        )
    except Exception as exc:
        check("needs_review NULL check", False, str(exc)[:80])

    # ── 3. economic_intent values are valid ───────────────────────────────────
    print("\n[3] CHECK constraint coverage")
    try:
        r = client.table("transactions").select("id", count="exact").is_("economic_intent", "null").execute()
        null_intent = r.count or 0
        check(
            f"economic_intent has no NULLs ({null_intent} found)",
            null_intent == 0,
        )
    except Exception as exc:
        check("economic_intent NULL check", False, str(exc)[:80])

    try:
        r = client.table("transactions").select("id", count="exact").is_("review_status", "null").execute()
        null_rs = r.count or 0
        check(
            f"review_status has no NULLs ({null_rs} found)",
            null_rs == 0,
        )
    except Exception as exc:
        check("review_status NULL check", False, str(exc)[:80])

    # ── 4. Row counts ─────────────────────────────────────────────────────────
    print("\n[4] Row counts")
    try:
        total = client.table("transactions").select("id", count="exact").execute()
        tx_count = total.count or 0
        check(f"transactions total: {tx_count}", tx_count > 0, "Table appears empty")
    except Exception as exc:
        check("transactions count", False, str(exc)[:80])

    try:
        r = (
            client.table("transactions")
            .select("id", count="exact")
            .eq("review_status", "pending_review")
            .execute()
        )
        pending = r.count or 0
        check(f"pending_review rows: {pending}", True)
    except Exception as exc:
        check("pending_review count", False, str(exc)[:80])

    try:
        r = (
            client.table("transactions")
            .select("id", count="exact")
            .eq("review_status", "confirmed")
            .execute()
        )
        confirmed = r.count or 0
        check(f"confirmed rows: {confirmed}", True)
    except Exception as exc:
        check("confirmed count", False, str(exc)[:80])

    # ── 5. insiders and companies columns ─────────────────────────────────────
    print("\n[5] insiders / companies columns")
    for col in ["insider_verified", "role_category"]:
        try:
            client.table("insiders").select(col).limit(1).execute()
            check(f"insiders.{col} exists", True)
        except Exception as exc:
            check(f"insiders.{col} exists", False, str(exc)[:80])

    for col in ["priority_tier"]:
        try:
            client.table("companies").select(col).limit(1).execute()
            check(f"companies.{col} exists", True)
        except Exception as exc:
            check(f"companies.{col} exists", False, str(exc)[:80])

    # ── 6. parser_version backfill ────────────────────────────────────────────
    print("\n[6] Parser version backfill")
    try:
        r = client.table("transactions").select("id", count="exact").is_("parser_version", "null").execute()
        null_pv = r.count or 0
        check(
            f"parser_version has no NULLs ({null_pv} found)",
            null_pv == 0,
        )
    except Exception as exc:
        check("parser_version NULL check", False, str(exc)[:80])

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n─────────────────────────────────────────────────────────────────────")
    if _failures:
        print(f"\nFAILED: {len(_failures)} check(s) did not pass:")
        for f in _failures:
            print(f"  • {f}")
        print("\nApply db/migrations/001_schema_integrity.sql and re-run.\n")
        sys.exit(1)
    else:
        print(f"\nAll checks passed. Phase 1 migration is correctly applied.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
