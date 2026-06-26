"""
Reproducible quality evaluation report — Phase 13.

Reads quality_reviews truth labels and produces accuracy, calibration, and
breakdown metrics.  Designed for periodic runs; always reflects the current
state of the quality_reviews table.

Unlike generate_quality_report.py (which reports parser self-assessments such
as needs_review flags and confidence distributions), this report is grounded in
ground truth: what did human reviewers actually decide?

Output sections
---------------
  TRUTH SET          counts of confirmed / corrected / rejected reviews
  DIRECTION ACCURACY direction accuracy on the reviewed sample
  TYPE ACCURACY      transaction-type accuracy on the reviewed sample
  CALIBRATION        accuracy by confidence bucket (is high confidence → accurate?)
  UNKNOWN DIRECTION  root-cause breakdown for unknown-direction reviews
  COMPLETENESS       missing-field rates on recent live transactions
  ISSUER RESOLUTION  pending / resolved / rejected / false-match counts
  FIXTURE PIPELINE   how many corrections are queued for regression tests

Usage:
    python3 -m scraper.generate_quality_eval_report
    python3 -m scraper.generate_quality_eval_report --since 2026-01-01
    python3 -m scraper.generate_quality_eval_report --parser-version 3.2.0
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from .quality_eval import (
    accuracy_from_reviews,
    calibration_table,
    completeness_stats,
    issuer_resolution_stats,
    unknown_direction_breakdown,
)


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


def _fetch_reviews(
    client,
    since: Optional[str],
    parser_version: Optional[str],
) -> list[dict]:
    q = client.table("quality_reviews").select("*").order("reviewed_at", desc=True)
    if since:
        q = q.gte("reviewed_at", since)
    if parser_version:
        q = q.eq("parser_version", parser_version)
    return q.execute().data or []


def _fetch_recent_transactions(client, days: int = 30) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    q = (
        client.table("transactions")
        .select("id,isin,insider_name,company_name,unit_price,quantity")
        .eq("is_current", True)
        .gte("created_at", since)
        .limit(2000)
    )
    return q.execute().data or []


def _fetch_unmatched_issuers(client) -> list[dict]:
    return (
        client.table("unmatched_issuers")
        .select("status,false_match_issuer_id")
        .execute()
        .data or []
    )


def _print_report(
    reviews: list[dict],
    tx_rows: list[dict],
    unmatched: list[dict],
    since: Optional[str],
    parser_version: Optional[str],
) -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    scope_parts = []
    if since:
        scope_parts.append(f"since {since}")
    if parser_version:
        scope_parts.append(f"parser_version={parser_version}")
    scope_str = " | ".join(scope_parts) if scope_parts else "all time"

    print(f"\nQuality Evaluation Report — {now_str}  ({scope_str})")
    print("─" * 65)

    # ─── Truth set summary ────────────────────────────────────────────────────
    total = len(reviews)
    if total == 0:
        print(
            "\nNo quality reviews recorded yet.\n"
            "Collect truth labels with:\n"
            "  python3 -m scraper.sample_review_queue --category unknown_direction --limit 10\n"
            "  python3 -m scraper.sample_review_queue --record TX_ID "
            "--category unknown_direction --outcome confirmed\n"
        )
    else:
        confirmed = sum(1 for r in reviews if r.get("outcome") == "confirmed")
        corrected = sum(1 for r in reviews if r.get("outcome") == "corrected")
        rejected  = sum(1 for r in reviews if r.get("outcome") == "rejected")
        print(f"\nTRUTH SET  ({total} reviewed transactions)")
        print(f"  confirmed  {confirmed:>5}  ({confirmed * 100 // total}%)")
        print(f"  corrected  {corrected:>5}  ({corrected * 100 // total}%)")
        print(f"  rejected   {rejected:>5}  ({rejected * 100 // total}%)")

        # ─── Accuracy ─────────────────────────────────────────────────────────
        acc = accuracy_from_reviews(reviews)
        print(f"\nDIRECTION ACCURACY  (N={acc['eligible']}, rejected excluded)")
        if acc["direction_accuracy_pct"] is not None:
            n_wrong = acc["eligible"] - acc["direction_correct"]  # type: ignore[operator]
            print(f"  Correct:   {acc['direction_correct']}/{acc['eligible']} = {acc['direction_accuracy_pct']}%")
            print(f"  Incorrect: {n_wrong}")
        else:
            print("  No eligible reviews.")

        print(f"\nTRANSACTION-TYPE ACCURACY  (N={acc['eligible']})")
        if acc["type_accuracy_pct"] is not None:
            n_wrong = acc["eligible"] - acc["type_correct"]  # type: ignore[operator]
            print(f"  Correct:   {acc['type_correct']}/{acc['eligible']} = {acc['type_accuracy_pct']}%")
            print(f"  Incorrect: {n_wrong}")
        else:
            print("  No eligible reviews.")

        # ─── Calibration ──────────────────────────────────────────────────────
        cal = calibration_table(reviews)
        print("\nCONFIDENCE CALIBRATION  (direction accuracy by classification_confidence)")
        if cal:
            for row in cal:
                filled = int(row["accuracy_pct"] / 5)
                bar    = "█" * filled + "░" * (20 - filled)
                print(f"  {row['bucket']}  {bar}  N={row['n']:>4}  {row['accuracy_pct']:.1f}%")
            # Monotonicity check
            accs = [row["accuracy_pct"] for row in cal]
            if len(accs) >= 2:
                if all(accs[i] >= accs[i + 1] for i in range(len(accs) - 1)):
                    print("  → Well-calibrated: accuracy decreases monotonically with lower confidence.")
                else:
                    print(
                        "  → WARNING: calibration is not monotone. "
                        "Higher confidence does not consistently predict accuracy."
                    )
        else:
            print("  Insufficient reviews for calibration (need confidence values).")

        # ─── Unknown-direction breakdown ───────────────────────────────────────
        unk = unknown_direction_breakdown(reviews)
        if unk:
            n_unk = sum(x["count"] for x in unk)
            print(
                f"\nUNKNOWN DIRECTION BREAKDOWN  "
                f"({n_unk} reviews with original_direction='unknown')"
            )
            for row in unk:
                pct = round(100 * row["count"] / n_unk)
                print(f"  {row['rationale_prefix']:<48}  {row['count']:>4}  ({pct}%)")
        elif any(r.get("original_direction") == "unknown" for r in reviews):
            print("\nUNKNOWN DIRECTION BREAKDOWN  (no rationale recorded)")
        else:
            print("\nUNKNOWN DIRECTION BREAKDOWN  (no unknown-direction reviews in sample)")

        # ─── Fixture pipeline ─────────────────────────────────────────────────
        eligible = sum(1 for r in reviews if r.get("fixture_eligible"))
        created  = sum(1 for r in reviews if r.get("fixture_created"))
        pending  = eligible - created
        print(f"\nFIXTURE PIPELINE")
        print(f"  eligible  {eligible:>5}")
        print(f"  created   {created:>5}")
        print(f"  pending   {pending:>5}", end="")
        if pending:
            print("  ← run: python3 -m scraper.create_regression_fixture --review-id ID")
        else:
            print()

    # ─── Extraction completeness (live transactions, last 30 days) ────────────
    print(f"\nEXTRACTION COMPLETENESS  (last 30d, N={len(tx_rows)})")
    if tx_rows:
        cs = completeness_stats(tx_rows)
        fields = ["isin", "insider_name", "company_name", "unit_price", "quantity"]
        for field in fields:
            pct  = cs.get(f"{field}_missing_pct", 0.0)
            flag = "  ← HIGH" if pct > 20 else ""
            print(f"  {field:<16}  {pct:>5.1f}% missing{flag}")
    else:
        print("  No recent transactions.")

    # ─── Issuer resolution ────────────────────────────────────────────────────
    irs = issuer_resolution_stats(unmatched)
    print(f"\nISSUER RESOLUTION  (unmatched_issuers, all time)")
    print(f"  total        {irs['total']:>6}")
    print(f"  pending      {irs['pending']:>6}")
    print(f"  resolved     {irs['resolved']:>6}  ({irs['resolution_rate_pct']:.1f}% of total)")
    print(f"  rejected     {irs['rejected']:>6}")
    print(f"  false_match  {irs['false_match']:>6}")

    print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate quality evaluation report from human-review truth labels"
    )
    parser.add_argument("--since", default=None,
                        help="Only include reviews after this date (YYYY-MM-DD)")
    parser.add_argument("--parser-version", default=None,
                        help="Filter by parser version (e.g. 3.2.0)")
    args = parser.parse_args(argv)

    client = _get_client()
    reviews  = _fetch_reviews(client, args.since, args.parser_version)
    tx_rows  = _fetch_recent_transactions(client, days=30)
    unmatched = _fetch_unmatched_issuers(client)
    _print_report(reviews, tx_rows, unmatched, args.since, args.parser_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
