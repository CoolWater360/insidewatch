"""
Labelled-review sampling workflow — Phase 13.

Samples transactions from the quality backlog by category, displays them for
human review, and records decisions to the quality_reviews truth table.

Every recorded review preserves a snapshot of the original classifier output
so accuracy and calibration can be measured over time, independently of any
corrections applied to the live transaction row.

Categories
----------
  unknown_direction  — direction = 'unknown'
  unknown_type       — transaction_type = 'unknown'
  low_confidence     — extraction_confidence < LOW_CONF_THRESHOLD (0.65)
  corporate_action   — mechanical/non-discretionary transaction types
  vehicle_entity     — classification via holding vehicle, trust, or nominee
  issuer_resolution  — company not linked to the issuer master
  other              — needs_review = true, not covered by the above

Usage
-----
  # Show backlog counts by category
  python3 -m scraper.sample_review_queue

  # List up to 10 samples from a category
  python3 -m scraper.sample_review_queue --category unknown_direction --limit 10

  # Record a confirmed review (parser was correct)
  python3 -m scraper.sample_review_queue --record TX_ID \\
      --category unknown_direction --outcome confirmed

  # Record a corrected review (parser made an error)
  python3 -m scraper.sample_review_queue --record TX_ID \\
      --category unknown_direction --outcome corrected \\
      --corrected-direction buy --corrected-type buy \\
      --notes "ACQUISTO keyword present in section 4a, missed by parser"

  # Mark a quality_review record as eligible for a regression fixture
  python3 -m scraper.sample_review_queue --mark-fixture REVIEW_ID
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LOW_CONF_THRESHOLD = 0.65

_MECHANICAL_TYPES = [
    "grant", "option_exercise", "sell_to_cover", "conversion", "inheritance",
    "gift_in", "gift_out", "transfer_in", "transfer_out",
    "pledge_or_security", "derivative_transaction",
]

_REVIEW_CATEGORIES = [
    "unknown_direction",
    "unknown_type",
    "low_confidence",
    "corporate_action",
    "vehicle_entity",
    "issuer_resolution",
    "other",
]

_CATEGORY_DESCRIPTIONS = {
    "unknown_direction": "direction = 'unknown'",
    "unknown_type":      "transaction_type = 'unknown'",
    "low_confidence":    f"extraction_confidence < {LOW_CONF_THRESHOLD}",
    "corporate_action":  "mechanical transaction types (grant, transfer, etc.)",
    "vehicle_entity":    "classification via related entity / trust / nominee",
    "issuer_resolution": "company not linked to issuer master (issuer_id IS NULL)",
    "other":             "needs_review=true, not covered by the above categories",
}

# PostgREST embedded-resource syntax: companies(name) and insiders(full_name)
# join the FK columns company_id → companies.name and insider_id → insiders.full_name.
# Do NOT reference company_name or insider_name — those columns do not exist on
# the transactions table; the denormalized names live in the related tables.
_TX_SELECT = (
    "id,direction,transaction_type,transaction_date,total_value,"
    "extraction_confidence,classification_confidence,classification_rationale,"
    "review_reason,needs_review,source_filing_id,parser_version,"
    "economic_intent,raw_nature_text,"
    "companies(name),insiders(full_name,role)"
)


def _company_name(row: dict) -> str:
    """Extract company display name from a PostgREST embedded-resource row."""
    return (row.get("companies") or {}).get("name") or ""


def _insider_name(row: dict) -> str:
    """Extract insider display name from a PostgREST embedded-resource row."""
    return (row.get("insiders") or {}).get("full_name") or ""


def _get_client():
    from supabase import create_client
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print(
            "ERROR: SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) and "
            "SUPABASE_SERVICE_ROLE_KEY must be set.",
            file=sys.stderr,
        )
        sys.exit(1)
    return create_client(url, key)


def _base_query(client):
    """Base transaction query: current rows only, most recent first."""
    return (
        client.table("transactions")
        .select(_TX_SELECT)
        .eq("is_current", True)
        .order("created_at", desc=True)
    )


def _apply_category_filter(q, category: str):
    if category == "unknown_direction":
        return q.eq("direction", "unknown")
    if category == "unknown_type":
        return q.eq("transaction_type", "unknown")
    if category == "low_confidence":
        return q.lt("extraction_confidence", LOW_CONF_THRESHOLD)
    if category == "corporate_action":
        return q.in_("transaction_type", _MECHANICAL_TYPES)
    if category == "vehicle_entity":
        return q.like("classification_rationale", "vehicle_context%")
    if category == "other":
        return q.eq("needs_review", True)
    # issuer_resolution handled separately
    return q


def _count_category(client, category: str) -> int:
    if category == "issuer_resolution":
        r = (
            client.table("companies")
            .select("id", count="exact", head=True)
            .is_("issuer_id", "null")
            .execute()
        )
        return r.count or 0
    q = (
        client.table("transactions")
        .select("id", count="exact", head=True)
        .eq("is_current", True)
    )
    q = _apply_category_filter(q, category)
    r = q.execute()
    return r.count or 0


def _sample_transactions(client, category: str, limit: int) -> list[dict]:
    if category == "issuer_resolution":
        # Return companies without issuer_id instead of transactions
        r = (
            client.table("companies")
            .select("id,name,ticker,isin")
            .is_("issuer_id", "null")
            .order("name")
            .limit(limit)
            .execute()
        )
        return r.data or []
    q = _base_query(client).limit(limit)
    q = _apply_category_filter(q, category)
    return q.execute().data or []


# ─── Commands ─────────────────────────────────────────────────────────────────

def _cmd_counts(client) -> None:
    print("\nReview queue — backlog by quality category\n")
    print(f"  {'Category':<22}  {'Backlog':>7}  Description")
    print("  " + "─" * 75)
    for cat in _REVIEW_CATEGORIES:
        count = _count_category(client, cat)
        desc  = _CATEGORY_DESCRIPTIONS[cat]
        print(f"  {cat:<22}  {count:>7}  {desc}")

    # Truth-set summary
    r = client.table("quality_reviews").select("outcome,fixture_eligible,fixture_created").execute()
    rows = r.data or []
    total     = len(rows)
    confirmed = sum(1 for row in rows if row.get("outcome") == "confirmed")
    corrected = sum(1 for row in rows if row.get("outcome") == "corrected")
    rejected  = sum(1 for row in rows if row.get("outcome") == "rejected")
    eligible  = sum(1 for row in rows if row.get("fixture_eligible"))
    created   = sum(1 for row in rows if row.get("fixture_created"))

    print(f"\n  Truth set: {total} reviews recorded")
    if total:
        print(f"    confirmed={confirmed}  corrected={corrected}  rejected={rejected}")
        print(f"    fixture_eligible={eligible}  fixture_created={created}")
    print()


def _cmd_list(client, category: str, limit: int) -> None:
    rows = _sample_transactions(client, category, limit)
    if not rows:
        print(f"\nNo transactions found for category '{category}'.\n")
        return

    print(f"\n{category} — {len(rows)} sample(s)\n")

    if category == "issuer_resolution":
        print(f"  {'ID':>7}  {'Company name':<35}  {'Ticker':<8}  {'ISIN'}")
        print("  " + "─" * 70)
        for r in rows:
            print(
                f"  {r['id']:>7}  {(r.get('name') or '')[:33]:<35}  "
                f"{(r.get('ticker') or ''):<8}  {r.get('isin') or ''}"
            )
        print()
        print("  Use review_unmatched_issuers to resolve these companies.\n")
        return

    print(
        f"  {'TX_ID':>7}  {'Company':<28}  {'Insider':<22}  "
        f"{'Dir':<6}  {'Type':<20}  {'Conf':>5}  Rationale"
    )
    print("  " + "─" * 130)
    for r in rows:
        rationale = (r.get("classification_rationale") or "")[:38]
        print(
            f"  {r['id']:>7}  {_company_name(r)[:26]:<28}  "
            f"{_insider_name(r)[:20]:<22}  "
            f"{(r.get('direction') or ''):<6}  "
            f"{(r.get('transaction_type') or ''):<20}  "
            f"{(r.get('extraction_confidence') or 0):.2f}  "
            f"{rationale}"
        )

    print()
    print("  To review:")
    print(f"    --record TX_ID --category {category} --outcome confirmed")
    print(
        f"    --record TX_ID --category {category} --outcome corrected "
        f"--corrected-direction buy --corrected-type buy --notes '...'"
    )
    print()


def _cmd_record(
    client,
    tx_id: int,
    category: str,
    outcome: str,
    corrected_direction: Optional[str],
    corrected_type: Optional[str],
    corrected_intent: Optional[str],
    notes: Optional[str],
    reviewed_by: str,
) -> None:
    # Fetch current transaction
    result = (
        client.table("transactions")
        .select(
            "id,direction,transaction_type,economic_intent,"
            "extraction_confidence,classification_confidence,"
            "classification_rationale,needs_review,review_reason,"
            "source_filing_id,parser_version"
        )
        .eq("id", tx_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        print(f"ERROR: transaction {tx_id} not found.", file=sys.stderr)
        sys.exit(1)

    tx = result.data[0]
    now = datetime.now(timezone.utc).isoformat()

    record = {
        "transaction_id":                   tx_id,
        "source_filing_id":                 tx.get("source_filing_id"),
        "parser_version":                   tx.get("parser_version") or "unknown",
        "original_direction":               tx.get("direction"),
        "original_transaction_type":        tx.get("transaction_type"),
        "original_economic_intent":         tx.get("economic_intent"),
        "original_extraction_confidence":   tx.get("extraction_confidence"),
        "original_classification_confidence": tx.get("classification_confidence"),
        "original_classification_rationale":  tx.get("classification_rationale"),
        "original_needs_review":            tx.get("needs_review"),
        "original_review_reason":           tx.get("review_reason"),
        "review_category":                  category,
        "outcome":                          outcome,
        "corrected_direction":              corrected_direction,
        "corrected_transaction_type":       corrected_type,
        "corrected_economic_intent":        corrected_intent,
        "correction_notes":                 notes,
        "reviewed_at":                      now,
        "reviewed_by":                      reviewed_by,
    }

    ins = client.table("quality_reviews").insert(record).execute()
    review_id = ins.data[0]["id"] if ins.data else "?"
    print(f"Recorded quality_review #{review_id} for transaction {tx_id}: {outcome}")

    # Apply classification override when fields were corrected
    if outcome == "corrected" and (corrected_direction or corrected_type or corrected_intent):
        from .classifier import classify_override
        new_type   = corrected_type   or tx.get("transaction_type")
        new_intent = corrected_intent or tx.get("economic_intent")
        classify_override(
            client,
            tx_id,
            transaction_type=new_type,
            economic_intent=new_intent,
            rationale=f"quality_review #{review_id}: {notes or 'operator correction'}",
            changed_by=reviewed_by,
        )
        print(f"Applied classification override to transaction {tx_id}.")

    # Update review_status on the transaction itself
    client.table("transactions").update({
        "review_status": outcome,
        "updated_at":    now,
    }).eq("id", tx_id).execute()


def _cmd_mark_fixture(client, review_id: int) -> None:
    result = client.table("quality_reviews").update({
        "fixture_eligible": True,
    }).eq("id", review_id).execute()
    if result.data:
        print(f"Marked quality_review #{review_id} as fixture_eligible=True.")
        print(f"Run:  python3 -m scraper.create_regression_fixture --review-id {review_id}")
    else:
        print(f"WARNING: quality_review #{review_id} not found.", file=sys.stderr)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Sample quality review queue and record human decisions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--category", choices=_REVIEW_CATEGORIES,
                        help="Quality category to sample or record against")
    parser.add_argument("--limit", type=int, default=10,
                        help="Transactions to list (default: 10)")

    parser.add_argument("--record", type=int, metavar="TX_ID",
                        help="Transaction ID to record a review decision for")
    parser.add_argument("--outcome", choices=["confirmed", "corrected", "rejected"],
                        help="Review outcome (required with --record)")
    parser.add_argument("--corrected-direction", choices=["buy", "sell", "unknown"],
                        help="Corrected direction value (with --outcome corrected)")
    parser.add_argument("--corrected-type",
                        help="Corrected transaction_type value (with --outcome corrected)")
    parser.add_argument("--corrected-intent", choices=["discretionary", "mechanical", "unclear"],
                        help="Corrected economic_intent value (with --outcome corrected)")
    parser.add_argument("--notes",
                        help="Free-text explanation of the correction")
    parser.add_argument("--reviewed-by", default="operator",
                        help="Reviewer identifier (default: operator)")

    parser.add_argument("--mark-fixture", type=int, metavar="REVIEW_ID",
                        help="Mark a quality_review record as fixture_eligible=True")

    args = parser.parse_args(argv)
    client = _get_client()

    if args.mark_fixture is not None:
        _cmd_mark_fixture(client, args.mark_fixture)

    elif args.record is not None:
        if not args.outcome:
            print("ERROR: --record requires --outcome", file=sys.stderr)
            return 2
        if not args.category:
            print("ERROR: --record requires --category", file=sys.stderr)
            return 2
        _cmd_record(
            client,
            tx_id=args.record,
            category=args.category,
            outcome=args.outcome,
            corrected_direction=args.corrected_direction,
            corrected_type=args.corrected_type,
            corrected_intent=args.corrected_intent,
            notes=args.notes,
            reviewed_by=args.reviewed_by,
        )

    elif args.category:
        _cmd_list(client, args.category, args.limit)

    else:
        _cmd_counts(client)

    return 0


if __name__ == "__main__":
    sys.exit(main())
