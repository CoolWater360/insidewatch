"""
Fixture-based regression tests for the MAR Art 19 parser.

Each JSON fixture in tests/fixtures/filings/ is loaded and fed through
parse_text().  Tests verify field values, transaction count, and minimum
extraction confidence thresholds.

These tests exercise the full parsing pipeline without PDF binary files,
making them fast, deterministic, and easy to extend with new examples.
"""

import json
import pathlib
from typing import Any

import pytest

from scraper.parser import parse_text, PARSER_VERSION

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "filings"

_CLOSE_ENOUGH_FIELDS = {"quantity", "unit_price", "total_value"}


def _load_fixture(name: str) -> dict:
    path = _FIXTURES_DIR / f"{name}.json"
    with path.open() as f:
        return json.load(f)


def _close_enough(actual: Any, expected: Any, field: str) -> bool:
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False
    if field in _CLOSE_ENOUGH_FIELDS:
        try:
            return abs(float(actual) - float(expected)) < 0.01
        except (TypeError, ValueError):
            return False
    return str(actual).strip().lower() == str(expected).strip().lower()


def _all_fixture_ids() -> list[str]:
    return [p.stem for p in sorted(_FIXTURES_DIR.glob("*.json"))]


# ─── Parametrised: run every fixture ─────────────────────────────────────────

@pytest.mark.parametrize("fixture_id", _all_fixture_ids())
def test_fixture_transaction_count(fixture_id: str) -> None:
    fixture = _load_fixture(fixture_id)
    results = parse_text(
        fixture["raw_text"],
        fixture.get("source_url", "fixture://test"),
        fixture.get("filing_date", "2024-01-01"),
        doc_sha256="",
    )
    expected_count = fixture["expected"]["count"]
    assert len(results) == expected_count, (
        f"{fixture_id}: expected {expected_count} transactions, got {len(results)}"
    )


@pytest.mark.parametrize("fixture_id", _all_fixture_ids())
def test_fixture_fields(fixture_id: str) -> None:
    fixture = _load_fixture(fixture_id)
    results = parse_text(
        fixture["raw_text"],
        fixture.get("source_url", "fixture://test"),
        fixture.get("filing_date", "2024-01-01"),
        doc_sha256="",
    )

    for i, exp_tx in enumerate(fixture["expected"].get("transactions", [])):
        assert i < len(results), f"{fixture_id}: transaction {i} missing from output"
        actual = results[i]

        checked_fields = [
            "insider_name", "company_name", "isin", "direction",
            "transaction_type", "transaction_date", "quantity",
            "unit_price", "currency", "total_value", "needs_review",
        ]
        for field in checked_fields:
            if field not in exp_tx:
                continue
            actual_val = getattr(actual, field, None)
            expected_val = exp_tx[field]
            assert _close_enough(actual_val, expected_val, field), (
                f"{fixture_id} tx[{i}].{field}: "
                f"expected={expected_val!r}, actual={actual_val!r}"
            )


@pytest.mark.parametrize("fixture_id", _all_fixture_ids())
def test_fixture_extraction_confidence(fixture_id: str) -> None:
    fixture = _load_fixture(fixture_id)
    results = parse_text(
        fixture["raw_text"],
        fixture.get("source_url", "fixture://test"),
        fixture.get("filing_date", "2024-01-01"),
        doc_sha256="",
    )

    for i, exp_tx in enumerate(fixture["expected"].get("transactions", [])):
        min_conf = exp_tx.get("min_extraction_confidence")
        if min_conf is None:
            continue
        assert i < len(results), f"{fixture_id}: transaction {i} missing"
        actual_conf = results[i].extraction_confidence
        assert actual_conf >= min_conf, (
            f"{fixture_id} tx[{i}].extraction_confidence: "
            f"expected >= {min_conf}, got {actual_conf}"
        )


# ─── parse_text / parse_pdf contract ─────────────────────────────────────────

class TestParseTextContract:
    def test_empty_text_returns_empty_list(self) -> None:
        results = parse_text("", "fixture://empty", "2024-01-01")
        assert results == []

    def test_no_transaction_blocks_still_returns_one_attempt(self) -> None:
        text = (
            "Mittente del comunicato : Test S.p.A.\n"
            "Nome: Alice Cognome: Smith\n"
            "Ruolo: Persona che esercita funzioni di amministrazione\n"
            "ISIN: IT0001234567\n"
            "ACQUISTO\n"
            "Volume aggregato: 100\nPrezzo: 10.00 EUR\n"
            "Data dell'operazione 2024-06-01\n"
        )
        results = parse_text(text, "fixture://noblock", "2024-06-01")
        # Without "Operazione - N" markers the whole text is treated as one block
        assert len(results) == 1

    def test_doc_sha256_propagated_to_transactions(self) -> None:
        fixture = _load_fixture("layout1_buy")
        sha = "abc123def456" * 4 + "abcd"  # 48 chars — fake but non-empty
        sha = sha[:64]
        results = parse_text(
            fixture["raw_text"],
            fixture["source_url"],
            fixture["filing_date"],
            doc_sha256=sha,
        )
        assert len(results) == 1
        assert results[0].raw_document_sha256 == sha

    def test_parser_version_stamped(self) -> None:
        fixture = _load_fixture("layout1_buy")
        results = parse_text(
            fixture["raw_text"],
            fixture["source_url"],
            fixture["filing_date"],
        )
        assert len(results) == 1
        assert results[0].parser_version == PARSER_VERSION

    def test_source_transaction_index_sequential(self) -> None:
        fixture = _load_fixture("two_transactions")
        results = parse_text(
            fixture["raw_text"],
            fixture["source_url"],
            fixture["filing_date"],
        )
        assert len(results) == 2
        assert results[0].source_transaction_index == 0
        assert results[1].source_transaction_index == 1


# ─── Grant reclassification ────────────────────────────────────────────────────

class TestGrantReclassification:
    def test_zero_price_becomes_grant(self) -> None:
        fixture = _load_fixture("grant_zero_price")
        results = parse_text(
            fixture["raw_text"],
            fixture["source_url"],
            fixture["filing_date"],
        )
        assert len(results) == 1
        tx = results[0]
        assert tx.transaction_type == "grant"
        assert tx.unit_price == 0.0
        assert tx.total_value == 0.0
        assert tx.needs_review is False
