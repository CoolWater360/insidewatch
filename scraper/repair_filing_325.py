"""
One-time repair for the filing-325 duplicate-current-row incident.

Root cause
----------
PARSER_VERSION 1.2.1 corrected the direction for transaction 2093 from
'unknown' to 'sell'.  Because direction is part of the identity_hash formula,
the corrected direction produced a different hash.  The identity lookup found
nothing, and a second is_current=True row was inserted alongside tx 2093.

The fix in db.upsert_transaction (positional predecessor check) prevents this
going forward, but the two existing current rows must be repaired manually.

This script
-----------
1. Shows all transactions for filing 325 and their current state.
2. Identifies the stale row (direction='unknown') and the corrected row
   (direction='sell') that are both is_current=True at the same position.
3. Supersedes the stale row with the corrected row, preserving a version
   snapshot in transaction_versions.

Usage
-----
    # Dry-run (default — shows what would happen, no DB writes):
    python3 -m scraper.repair_filing_325

    # Apply the fix:
    python3 -m scraper.repair_filing_325 --apply
"""
import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair filing 325 duplicate-current-row.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write the supersede to the database.  Dry-run if omitted.",
    )
    args = parser.parse_args()

    try:
        import dotenv
        dotenv.load_dotenv(".env.local")
    except ImportError:
        pass

    from .db import get_supabase_client, supersede_transaction

    client = get_supabase_client()

    # ── 1. Fetch all transactions for filing 325 ──────────────────────────────
    result = (
        client.table("transactions")
        .select(
            "id,direction,transaction_type,economic_intent,"
            "is_current,superseded_by,source_transaction_index,"
            "parser_version,identity_hash"
        )
        .eq("source_filing_id", 325)
        .order("id")
        .execute()
    )
    rows = result.data or []

    print(f"\n=== Filing 325 — all transactions ({len(rows)} total) ===\n")
    for r in rows:
        print(json.dumps(
            {k: r.get(k) for k in (
                "id", "direction", "transaction_type", "economic_intent",
                "is_current", "superseded_by",
                "source_transaction_index", "parser_version",
            )},
            indent=2,
        ))

    # ── 2. Identify the duplicate pair ───────────────────────────────────────
    current_rows = [r for r in rows if r.get("is_current") is True]
    print(f"\nis_current=True count: {len(current_rows)}")

    if len(current_rows) == 0:
        print("ERROR: no current transactions found for filing 325.")
        return 1

    if len(current_rows) == 1:
        r = current_rows[0]
        print(
            f"  tx {r['id']} direction={r['direction']!r} — "
            "no duplicate; database is clean."
        )
        return 0

    # Expect one stale (direction=unknown) and one corrected (direction=sell).
    stale_rows = [r for r in current_rows if r.get("direction") == "unknown"]
    new_rows   = [r for r in current_rows if r.get("direction") == "sell"]

    if not stale_rows:
        print(
            "\nERROR: expected a direction='unknown' row among current rows "
            "but none found.  Inspect manually."
        )
        return 1
    if not new_rows:
        print(
            "\nERROR: expected a direction='sell' row among current rows "
            "but none found.  Inspect manually."
        )
        return 1

    old_tx = stale_rows[0]
    new_tx = new_rows[0]
    old_id = old_tx["id"]
    new_id = new_tx["id"]

    print(f"\n=== Repair plan ===")
    print(f"  Supersede  tx {old_id:6d}  (direction='unknown', stale — parser <1.2.1)")
    print(f"        with tx {new_id:6d}  (direction='sell',    corrected by parser 1.2.1)")

    if not args.apply:
        print("\nDRY RUN — no database writes.  Pass --apply to execute.")
        return 0

    # ── 3. Apply ──────────────────────────────────────────────────────────────
    ok = supersede_transaction(
        client,
        old_id=old_id,
        new_id=new_id,
        reason=(
            "repair: parser-1.2.1 corrected direction unknown→sell (filing 325); "
            "identity_hash encodes direction so old row was not deduped on reprocess"
        ),
        changed_by="repair_filing_325",
    )

    if not ok:
        print(f"\nERROR: supersede_transaction returned False.", file=sys.stderr)
        return 1

    print(f"\nSuccess: tx {old_id} superseded by tx {new_id}.")

    # ── 4. Verify ─────────────────────────────────────────────────────────────
    verify = (
        client.table("transactions")
        .select("id,direction,transaction_type,economic_intent,is_current,superseded_by")
        .eq("source_filing_id", 325)
        .order("id")
        .execute()
    )
    print("\n=== Post-repair state ===\n")
    for r in verify.data or []:
        print(json.dumps(
            {k: r.get(k) for k in (
                "id", "direction", "transaction_type", "economic_intent",
                "is_current", "superseded_by",
            )},
            indent=2,
        ))

    return 0


if __name__ == "__main__":
    sys.exit(main())
