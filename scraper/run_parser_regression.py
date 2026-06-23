"""
Parser regression runner.

Loads every JSON fixture from tests/fixtures/filings/, runs parse_text(), and
compares output against the fixture's expected values.  Prints a field-level
accuracy table and exits non-zero if any fixture fails.

Usage:
    python3 -m scraper.run_parser_regression [--fixtures PATH]
"""

import argparse
import json
import pathlib
import sys
from typing import Any, List, Optional

from .parser import parse_text, PARSER_VERSION

_FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "tests" / "fixtures" / "filings"

_CHECKED_FIELDS = [
    "insider_name",
    "company_name",
    "isin",
    "direction",
    "transaction_type",
    "transaction_date",
    "quantity",
    "unit_price",
    "currency",
    "total_value",
    "needs_review",
]


def _load_fixtures(fixtures_dir: pathlib.Path) -> list[dict]:
    fixtures = []
    for path in sorted(fixtures_dir.glob("*.json")):
        with path.open() as f:
            fixtures.append(json.load(f))
    return fixtures


def _close_enough(actual: Any, expected: Any, field: str) -> bool:
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    if field in ("quantity", "unit_price", "total_value"):
        try:
            return abs(float(actual) - float(expected)) < 0.01
        except (TypeError, ValueError):
            return False
    return str(actual).strip().lower() == str(expected).strip().lower()


def _run_fixture(fixture: dict) -> dict:
    meta = fixture["meta"]
    raw_text = fixture["raw_text"]
    source_url = fixture.get("source_url", "fixture://unknown")
    filing_date = fixture.get("filing_date", "2024-01-01")
    expected = fixture["expected"]

    results = parse_text(raw_text, source_url, filing_date, doc_sha256="")

    outcome: dict = {
        "id": meta["id"],
        "description": meta.get("description", ""),
        "expected_count": expected["count"],
        "actual_count": len(results),
        "count_ok": len(results) == expected["count"],
        "transactions": [],
        "passed": True,
    }

    for i, exp_tx in enumerate(expected.get("transactions", [])):
        if i >= len(results):
            outcome["transactions"].append({
                "index": i,
                "error": "transaction missing from output",
                "passed": False,
            })
            outcome["passed"] = False
            continue

        actual_tx = results[i]
        tx_result: dict = {"index": i, "fields": {}, "passed": True}

        for field in _CHECKED_FIELDS:
            if field not in exp_tx:
                continue
            actual_val = getattr(actual_tx, field, None)
            expected_val = exp_tx[field]
            ok = _close_enough(actual_val, expected_val, field)
            tx_result["fields"][field] = {
                "expected": expected_val,
                "actual": actual_val,
                "ok": ok,
            }
            if not ok:
                tx_result["passed"] = False
                outcome["passed"] = False

        # Confidence threshold check
        min_conf = exp_tx.get("min_extraction_confidence")
        if min_conf is not None:
            actual_conf = getattr(actual_tx, "extraction_confidence", 0.0)
            conf_ok = actual_conf >= min_conf
            tx_result["fields"]["extraction_confidence"] = {
                "expected": f">= {min_conf}",
                "actual": actual_conf,
                "ok": conf_ok,
            }
            if not conf_ok:
                tx_result["passed"] = False
                outcome["passed"] = False

        outcome["transactions"].append(tx_result)

    if not outcome["count_ok"]:
        outcome["passed"] = False

    return outcome


def _print_report(outcomes: list[dict]) -> None:
    total = len(outcomes)
    passed = sum(1 for o in outcomes if o["passed"])

    print(f"\nParser regression — version {PARSER_VERSION}")
    print(f"{'─' * 60}")
    print(f"Fixtures: {total}  Passed: {passed}  Failed: {total - passed}")
    print(f"{'─' * 60}\n")

    for outcome in outcomes:
        status = "PASS" if outcome["passed"] else "FAIL"
        count_note = ""
        if not outcome["count_ok"]:
            count_note = f"  [expected {outcome['expected_count']} tx, got {outcome['actual_count']}]"
        print(f"[{status}] {outcome['id']}{count_note}")

        for tx in outcome["transactions"]:
            if tx.get("error"):
                print(f"       tx[{tx['index']}]: {tx['error']}")
                continue
            for field, info in tx.get("fields", {}).items():
                if not info["ok"]:
                    print(
                        f"       tx[{tx['index']}].{field}: "
                        f"expected={info['expected']!r}  actual={info['actual']!r}"
                    )

    print()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run parser regression fixtures")
    parser.add_argument(
        "--fixtures",
        default=str(_FIXTURES_DIR),
        help="Path to fixture directory (default: tests/fixtures/filings)",
    )
    args = parser.parse_args(argv)

    fixtures_dir = pathlib.Path(args.fixtures)
    if not fixtures_dir.is_dir():
        print(f"ERROR: fixtures directory not found: {fixtures_dir}", file=sys.stderr)
        return 2

    fixtures = _load_fixtures(fixtures_dir)
    if not fixtures:
        print("No fixtures found.", file=sys.stderr)
        return 2

    outcomes = [_run_fixture(f) for f in fixtures]
    _print_report(outcomes)

    return 0 if all(o["passed"] for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
