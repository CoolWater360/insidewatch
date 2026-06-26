"""
Database-based parser quality report.

Queries the live transactions table and prints a summary of extraction quality,
confidence distributions, review flags, and recent parser runs.

Usage:
    python3 -m scraper.generate_quality_report [--limit N] [--since YYYY-MM-DD]
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from supabase import create_client


def _get_client():
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


def _fetch_quality_rows(client, since: Optional[str]) -> List[dict]:
    q = (
        client.table("transactions")
        .select(
            "id,insider_name,company_name,isin,direction,transaction_type,"
            "transaction_date,extraction_confidence,classification_confidence,"
            "needs_review,review_status,review_reason,parse_warnings,"
            "parser_version,created_at,is_current,classification_rationale"
        )
        .eq("is_current", True)
        .order("created_at", desc=True)
    )
    if since:
        q = q.gte("created_at", since)
    return q.execute().data or []


def _bucket(conf: float) -> str:
    if conf >= 0.9:
        return "0.9–1.0"
    if conf >= 0.7:
        return "0.7–0.9"
    if conf >= 0.5:
        return "0.5–0.7"
    return "< 0.5"


def _print_report(rows: List[dict], since: Optional[str]) -> None:
    total = len(rows)
    if total == 0:
        print("No transactions found.")
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    since_str = f"since {since}" if since else "all time"
    print(f"\nParser Quality Report — {now_str}  ({since_str})")
    print(f"{'─' * 60}")
    print(f"Total current transactions: {total}")

    # Confidence buckets
    ext_conf_vals = [r.get("extraction_confidence") or 0.0 for r in rows]
    cls_conf_vals = [r.get("classification_confidence") or 0.0 for r in rows]

    ext_buckets: dict[str, int] = {}
    for v in ext_conf_vals:
        b = _bucket(v)
        ext_buckets[b] = ext_buckets.get(b, 0) + 1

    print("\nExtraction confidence distribution:")
    for label in ["0.9–1.0", "0.7–0.9", "0.5–0.7", "< 0.5"]:
        n = ext_buckets.get(label, 0)
        bar = "█" * (n * 40 // max(total, 1))
        print(f"  {label}  {bar}  {n} ({n * 100 // total}%)")

    avg_ext = sum(ext_conf_vals) / total
    avg_cls = sum(cls_conf_vals) / total
    print(f"\n  Avg extraction confidence:      {avg_ext:.3f}")
    print(f"  Avg classification confidence:  {avg_cls:.3f}")

    # Missing key fields
    missing_isin = sum(1 for r in rows if not r.get("isin"))
    missing_name = sum(1 for r in rows if not r.get("insider_name") or r["insider_name"] == "Unknown")
    missing_company = sum(1 for r in rows if not r.get("company_name") or r["company_name"] == "Unknown")
    print(f"\nMissing fields:")
    print(f"  ISIN:         {missing_isin:>5}  ({missing_isin * 100 // total}%)")
    print(f"  insider_name: {missing_name:>5}  ({missing_name * 100 // total}%)")
    print(f"  company_name: {missing_company:>5}  ({missing_company * 100 // total}%)")

    # Review flags
    needs_review = sum(1 for r in rows if r.get("needs_review"))
    print(f"\nReview flags:")
    print(f"  needs_review=true:  {needs_review}  ({needs_review * 100 // total}%)")

    reason_counts: dict[str, int] = {}
    for r in rows:
        reason = r.get("review_reason")
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if reason_counts:
        for reason, n in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason:<30} {n}")

    # Direction breakdown
    print(f"\nDirection breakdown:")
    dir_counts: dict[str, int] = {}
    for r in rows:
        d = r.get("direction") or "unknown"
        dir_counts[d] = dir_counts.get(d, 0) + 1
    for d, n in sorted(dir_counts.items(), key=lambda x: -x[1]):
        print(f"  {d:<10} {n:>5}  ({n * 100 // total}%)")

    # Unknown-direction root-cause breakdown
    unknown_rows = [r for r in rows if (r.get("direction") or "unknown") == "unknown"]
    if unknown_rows:
        print(f"\nUnknown-direction root causes  ({len(unknown_rows)} transactions):")
        cause_counts: dict[str, int] = {}
        for r in unknown_rows:
            rationale = r.get("classification_rationale") or "no_rationale_stored"
            # Use the part before the first colon as the root-cause label
            prefix = rationale.split(":")[0].strip()
            cause_counts[prefix] = cause_counts.get(prefix, 0) + 1
        for cause, n in sorted(cause_counts.items(), key=lambda x: -x[1]):
            pct = n * 100 // len(unknown_rows)
            print(f"  {cause:<48}  {n:>4}  ({pct}%)")
        print(
            "  Note: 'undetermined' means no direction keyword or nature-text signal\n"
            "  was found.  'vague_direction' means direction was inferred from a\n"
            "  weak/fallback signal and downgraded.  Neither should be 'fixed' by\n"
            "  guessing — both represent genuine parsing uncertainty."
        )

    # Transaction type breakdown
    print(f"\nTransaction type breakdown:")
    type_counts: dict[str, int] = {}
    for r in rows:
        t = r.get("transaction_type") or "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t:<18} {n:>5}  ({n * 100 // total}%)")

    # Parser version breakdown
    print(f"\nParser versions:")
    ver_counts: dict[str, int] = {}
    for r in rows:
        v = r.get("parser_version") or "unknown"
        ver_counts[v] = ver_counts.get(v, 0) + 1
    for v, n in sorted(ver_counts.items(), key=lambda x: -x[1]):
        print(f"  {v:<12} {n:>5}")

    print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate parser quality report from DB")
    parser.add_argument("--since", default=None, help="Only include rows created after this date (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    client = _get_client()
    rows = _fetch_quality_rows(client, args.since)
    _print_report(rows, args.since)
    return 0


if __name__ == "__main__":
    sys.exit(main())
