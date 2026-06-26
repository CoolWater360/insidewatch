"""
Regression-fixture creator — Phase 13.

Converts a quality_review record (where fixture_eligible=True) into a JSON
fixture compatible with scraper/run_parser_regression.py.

The fixture captures:
  • raw_text         — from filings.raw_extracted_text (the text the parser
                        actually processed).  If unavailable (pre-migration
                        filing or text not preserved), a stub is written and
                        a warning is printed.
  • expected values  — corrected fields from the quality_review, falling back
                        to the original transaction values where no correction
                        was made.  This means the fixture tests the parser
                        against the ground-truth output, not the old wrong one.

After writing the fixture, the quality_reviews row is updated with
  fixture_created = True
  fixture_id      = <slug>

so the eval report can track which corrections have been converted.

Usage:
    python3 -m scraper.create_regression_fixture --review-id 42
    python3 -m scraper.create_regression_fixture --review-id 42 --fixture-id my_slug
    python3 -m scraper.create_regression_fixture --review-id 42 \\
        --output-dir /tmp/fixtures --dry-run
"""

import argparse
import json
import logging
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_FIXTURES_DIR = (
    pathlib.Path(__file__).parent.parent / "tests" / "fixtures" / "filings"
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


def _fetch_review(client, review_id: int) -> Optional[dict]:
    result = (
        client.table("quality_reviews")
        .select("*")
        .eq("id", review_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _fetch_transaction(client, tx_id: int) -> Optional[dict]:
    result = (
        client.table("transactions")
        .select("*")
        .eq("id", tx_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _fetch_filing(client, filing_id: int) -> Optional[dict]:
    result = (
        client.table("filings")
        .select("raw_extracted_text,pdf_url,filing_date")
        .eq("id", filing_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def build_fixture(
    review: dict,
    tx: dict,
    raw_text: Optional[str],
    filing_meta: Optional[dict],
    fixture_id: str,
) -> dict:
    """
    Build a fixture dict from a quality_review + transaction + optional raw text.

    Corrected values from the review take precedence over the original
    transaction values, so the fixture tests the parser against ground truth.

    This is a pure function and can be tested without a database.
    """
    def _best(corrected_key: str, original_key: str):
        """Return the corrected value if set, otherwise the original."""
        corrected = review.get(corrected_key)
        return corrected if corrected is not None else tx.get(original_key)

    expected_direction = _best("corrected_direction", "direction")
    expected_type      = _best("corrected_transaction_type", "transaction_type")

    stub_warning: Optional[str] = None
    if not raw_text:
        stub_warning = (
            "raw_text unavailable: filings.raw_extracted_text was NULL. "
            "The fixture cannot be used for parser regression until raw_text "
            "is populated manually or the filing is re-processed with text storage."
        )
        raw_text = (
            "[STUB — populate raw_text manually before using this fixture]\n"
            f"company:  {tx.get('company_name', '')}\n"
            f"insider:  {tx.get('insider_name', '')}\n"
            f"nature:   {tx.get('raw_nature_text', '')}\n"
        )

    fixture: dict = {
        "meta": {
            "id":                       fixture_id,
            "description": (
                f"Regression: {review.get('review_category', 'unknown')} "
                f"correction on tx {tx['id']} — "
                f"{review.get('correction_notes') or 'operator correction'}"
            ),
            "source_review_id":         review["id"],
            "source_transaction_id":    tx["id"],
            "parser_version_at_review": review.get("parser_version"),
            "created_at":               datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        },
        "raw_text":   raw_text,
        "source_url": tx.get("source_url") or (filing_meta or {}).get("pdf_url") or "",
        "filing_date": (
            tx.get("filing_date")
            or str((filing_meta or {}).get("filing_date") or "")
            or ""
        ),
        "expected": {
            "count": 1,
            "transactions": [
                {
                    "insider_name":       tx.get("insider_name", ""),
                    "company_name":       tx.get("company_name", ""),
                    "isin":               tx.get("isin"),
                    "direction":          expected_direction,
                    "transaction_type":   expected_type,
                    "transaction_date":   tx.get("transaction_date", ""),
                    "quantity":           tx.get("quantity"),
                    "unit_price":         tx.get("unit_price"),
                    "currency":           tx.get("currency", "EUR"),
                    # A correctly-reviewed fixture should not need further review
                    "needs_review":       False,
                    "min_extraction_confidence": 0.70,
                }
            ],
        },
    }

    if stub_warning:
        fixture["meta"]["stub_warning"] = stub_warning

    return fixture


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a parser regression fixture from a quality_review record"
    )
    parser.add_argument("--review-id", type=int, required=True,
                        help="quality_reviews.id to convert")
    parser.add_argument("--fixture-id", default=None,
                        help="Output slug / filename (default: review_<id>)")
    parser.add_argument("--output-dir", default=str(_DEFAULT_FIXTURES_DIR),
                        help=(
                            "Directory for fixture JSON "
                            "(default: tests/fixtures/filings)"
                        ))
    parser.add_argument("--dry-run", action="store_true",
                        help="Print fixture JSON without writing files or updating DB")
    args = parser.parse_args(argv)

    client = _get_client()

    review = _fetch_review(client, args.review_id)
    if not review:
        print(f"ERROR: quality_review #{args.review_id} not found.", file=sys.stderr)
        return 1

    tx_id = review["transaction_id"]
    tx = _fetch_transaction(client, tx_id)
    if not tx:
        print(f"ERROR: transaction {tx_id} not found.", file=sys.stderr)
        return 1

    filing_meta: Optional[dict] = None
    raw_text:    Optional[str]  = None
    if tx.get("source_filing_id"):
        filing_meta = _fetch_filing(client, tx["source_filing_id"])
        if filing_meta:
            raw_text = filing_meta.get("raw_extracted_text")

    fixture_id = args.fixture_id or f"review_{args.review_id}"
    fixture = build_fixture(review, tx, raw_text, filing_meta, fixture_id)

    if args.dry_run:
        print(json.dumps(fixture, indent=2, ensure_ascii=False))
        return 0

    out_dir  = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{fixture_id}.json"

    if out_path.exists():
        print(f"WARNING: {out_path} already exists — overwriting.")

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Fixture written: {out_path}")

    if fixture.get("meta", {}).get("stub_warning"):
        print(f"WARNING: {fixture['meta']['stub_warning']}")

    # Mark quality_reviews row as converted
    client.table("quality_reviews").update({
        "fixture_created": True,
        "fixture_id":      fixture_id,
    }).eq("id", args.review_id).execute()

    print(f"Marked quality_review #{args.review_id}: fixture_created=True, fixture_id={fixture_id!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
