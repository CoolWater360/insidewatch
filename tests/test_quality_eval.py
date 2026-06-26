"""
Tests for scraper/quality_eval.py and scraper/create_regression_fixture.py.

All tests are pure: no DB, no network, no filesystem writes (fixture builder
is tested in dry-run mode via build_fixture directly).

Coverage:
  quality_eval.accuracy_from_reviews      — empty, confirmed, corrected, rejected
  quality_eval.calibration_table          — bucket grouping, accuracy per bucket
  quality_eval.unknown_direction_breakdown — filters, groups by rationale prefix
  quality_eval.completeness_stats         — missing-field detection per field
  quality_eval.issuer_resolution_stats    — status counts + false_match
  create_regression_fixture.build_fixture — corrected values, stub fallback
"""

import pytest

from scraper.quality_eval import (
    accuracy_from_reviews,
    calibration_table,
    completeness_stats,
    issuer_resolution_stats,
    unknown_direction_breakdown,
)
from scraper.create_regression_fixture import build_fixture


# ─── Fixture builders ─────────────────────────────────────────────────────────

def _review(
    outcome: str = "confirmed",
    original_direction: str = "buy",
    original_transaction_type: str = "buy",
    original_classification_confidence: float = 0.85,
    corrected_direction: str = None,
    corrected_transaction_type: str = None,
    original_classification_rationale: str = "direction_fallthrough: buy",
) -> dict:
    return {
        "outcome": outcome,
        "original_direction": original_direction,
        "original_transaction_type": original_transaction_type,
        "original_classification_confidence": original_classification_confidence,
        "corrected_direction": corrected_direction,
        "corrected_transaction_type": corrected_transaction_type,
        "original_classification_rationale": original_classification_rationale,
        "fixture_eligible": False,
        "fixture_created": False,
    }


def _tx(**kwargs) -> dict:
    base = {
        "id": 1,
        "company_name": "Acme SpA",
        "insider_name": "Mario Rossi",
        "isin": "IT0001234567",
        "direction": "buy",
        "transaction_type": "buy",
        "economic_intent": "discretionary",
        "transaction_date": "2026-01-15",
        "quantity": 1000.0,
        "unit_price": 9.5,
        "currency": "EUR",
        "source_url": "https://example.com/filing.pdf",
        "filing_date": "2026-01-16",
        "raw_nature_text": "ACQUISTO",
    }
    base.update(kwargs)
    return base


# ─── accuracy_from_reviews ────────────────────────────────────────────────────

class TestAccuracyFromReviews:
    def test_empty_returns_none_accuracy(self):
        result = accuracy_from_reviews([])
        assert result["total_reviewed"] == 0
        assert result["eligible"] == 0
        assert result["direction_accuracy_pct"] is None
        assert result["type_accuracy_pct"] is None

    def test_all_confirmed_is_100_pct(self):
        reviews = [_review("confirmed") for _ in range(5)]
        result = accuracy_from_reviews(reviews)
        assert result["direction_accuracy_pct"] == 100.0
        assert result["type_accuracy_pct"] == 100.0
        assert result["eligible"] == 5
        assert result["rejected"] == 0

    def test_all_rejected_excluded_from_denominator(self):
        reviews = [_review("rejected") for _ in range(3)]
        result = accuracy_from_reviews(reviews)
        assert result["eligible"] == 0
        assert result["rejected"] == 3
        assert result["direction_accuracy_pct"] is None

    def test_corrected_direction_counts_as_wrong(self):
        # Original=buy, corrected=sell → direction was wrong
        r = _review("corrected", original_direction="buy", corrected_direction="sell")
        result = accuracy_from_reviews([r])
        assert result["direction_correct"] == 0
        assert result["direction_accuracy_pct"] == 0.0

    def test_corrected_type_only_direction_still_correct(self):
        # Type was wrong but direction was fine (corrected_direction is None)
        r = _review(
            "corrected",
            original_direction="buy",
            original_transaction_type="buy",
            corrected_direction=None,
            corrected_transaction_type="subscription",
        )
        result = accuracy_from_reviews([r])
        assert result["direction_correct"] == 1
        assert result["direction_accuracy_pct"] == 100.0
        assert result["type_correct"] == 0
        assert result["type_accuracy_pct"] == 0.0

    def test_mixed_reviews(self):
        reviews = [
            _review("confirmed"),                             # dir ok, type ok
            _review("confirmed"),                             # dir ok, type ok
            _review("corrected", corrected_direction="sell"), # dir wrong
            _review("corrected", corrected_transaction_type="grant"),  # type wrong, dir ok
            _review("rejected"),                              # excluded
        ]
        result = accuracy_from_reviews(reviews)
        assert result["total_reviewed"] == 5
        assert result["eligible"] == 4
        assert result["rejected"] == 1
        # 3 out of 4 direction-correct (confirmed ×2, corrected with only type wrong ×1)
        assert result["direction_correct"] == 3
        assert result["direction_accuracy_pct"] == 75.0
        # 3 out of 4 type-correct (confirmed ×2, corrected with only dir wrong ×1)
        assert result["type_correct"] == 3
        assert result["type_accuracy_pct"] == 75.0

    def test_corrected_same_direction_counts_as_correct(self):
        # Outcome=corrected but corrected_direction matches original → direction fine
        r = _review("corrected", original_direction="buy", corrected_direction="buy")
        result = accuracy_from_reviews([r])
        assert result["direction_correct"] == 1

    def test_counts_exclude_rejected_but_include_in_total_reviewed(self):
        reviews = [_review("confirmed"), _review("rejected")]
        result = accuracy_from_reviews(reviews)
        assert result["total_reviewed"] == 2
        assert result["eligible"] == 1
        assert result["rejected"] == 1


# ─── calibration_table ────────────────────────────────────────────────────────

class TestCalibrationTable:
    def test_empty_returns_empty(self):
        assert calibration_table([]) == []

    def test_all_rejected_returns_empty(self):
        reviews = [
            _review("rejected", original_classification_confidence=0.95),
            _review("rejected", original_classification_confidence=0.80),
        ]
        assert calibration_table(reviews) == []

    def test_single_bucket_high_confidence_perfect_accuracy(self):
        reviews = [
            _review("confirmed", original_classification_confidence=0.95),
            _review("confirmed", original_classification_confidence=0.92),
        ]
        result = calibration_table(reviews)
        assert len(result) == 1
        assert result[0]["bucket"] == "0.9–1.0"
        assert result[0]["n"] == 2
        assert result[0]["accuracy_pct"] == 100.0

    def test_multiple_buckets_grouped_correctly(self):
        reviews = [
            _review("confirmed", original_classification_confidence=0.95),   # 0.9–1.0
            _review("confirmed", original_classification_confidence=0.75),   # 0.7–0.9
            _review("corrected",
                    original_classification_confidence=0.75,
                    corrected_direction="sell"),                               # 0.7–0.9, wrong
            _review("confirmed", original_classification_confidence=0.40),   # <0.5
        ]
        result = calibration_table(reviews)
        buckets = {r["bucket"]: r for r in result}
        assert buckets["0.9–1.0"]["accuracy_pct"] == 100.0
        assert buckets["0.7–0.9"]["n"] == 2
        assert buckets["0.7–0.9"]["accuracy_pct"] == 50.0
        assert buckets["<0.5"]["accuracy_pct"] == 100.0

    def test_missing_confidence_excluded_from_all_buckets(self):
        reviews = [
            _review("confirmed", original_classification_confidence=None),
            _review("confirmed", original_classification_confidence=0.90),
        ]
        result = calibration_table(reviews)
        total_n = sum(r["n"] for r in result)
        assert total_n == 1  # only the one with confidence is counted

    def test_bucket_boundary_0_9_in_high_bucket(self):
        r = _review("confirmed", original_classification_confidence=0.90)
        result = calibration_table([r])
        assert result[0]["bucket"] == "0.9–1.0"

    def test_bucket_boundary_0_7_in_medium_bucket(self):
        r = _review("confirmed", original_classification_confidence=0.70)
        result = calibration_table([r])
        assert result[0]["bucket"] == "0.7–0.9"


# ─── unknown_direction_breakdown ──────────────────────────────────────────────

class TestUnknownDirectionBreakdown:
    def test_empty_returns_empty(self):
        assert unknown_direction_breakdown([]) == []

    def test_non_unknown_directions_excluded(self):
        reviews = [
            _review("confirmed", original_direction="buy"),
            _review("confirmed", original_direction="sell"),
        ]
        assert unknown_direction_breakdown(reviews) == []

    def test_groups_by_rationale_prefix(self):
        reviews = [
            _review(original_direction="unknown",
                    original_classification_rationale="undetermined: direction=unknown, hint=unknown"),
            _review(original_direction="unknown",
                    original_classification_rationale="undetermined: direction=unknown, hint=buy"),
            _review(original_direction="unknown",
                    original_classification_rationale="vague_direction: buy inferred from weak signal only"),
        ]
        result = unknown_direction_breakdown(reviews)
        assert result[0] == {"rationale_prefix": "undetermined", "count": 2}
        assert result[1] == {"rationale_prefix": "vague_direction", "count": 1}

    def test_sorted_by_count_desc(self):
        reviews = [
            _review(original_direction="unknown",
                    original_classification_rationale="vague_direction: buy from weak signal"),
            _review(original_direction="unknown",
                    original_classification_rationale="undetermined: direction=unknown"),
            _review(original_direction="unknown",
                    original_classification_rationale="undetermined: direction=unknown"),
        ]
        result = unknown_direction_breakdown(reviews)
        assert result[0]["rationale_prefix"] == "undetermined"
        assert result[0]["count"] == 2

    def test_no_rationale_uses_sentinel(self):
        r = _review(original_direction="unknown", original_classification_rationale=None)
        result = unknown_direction_breakdown([r])
        assert result[0]["rationale_prefix"] == "no_rationale"

    def test_mixed_direction_only_unknown_counted(self):
        reviews = [
            _review(original_direction="buy",
                    original_classification_rationale="direction_fallthrough: buy"),
            _review(original_direction="unknown",
                    original_classification_rationale="undetermined: direction=unknown"),
        ]
        result = unknown_direction_breakdown(reviews)
        assert len(result) == 1
        assert result[0]["rationale_prefix"] == "undetermined"


# ─── completeness_stats ───────────────────────────────────────────────────────

class TestCompletenessStats:
    def test_empty_returns_zero_total(self):
        result = completeness_stats([])
        assert result == {"total": 0}

    def test_all_present_zero_missing(self):
        tx = {"isin": "IT0001", "insider_name": "Rossi", "company_name": "Acme",
              "unit_price": 9.5, "quantity": 1000.0}
        result = completeness_stats([tx])
        assert result["total"] == 1
        assert result["isin_missing_pct"] == 0.0
        assert result["insider_name_missing_pct"] == 0.0

    def test_none_value_is_missing(self):
        tx = {"isin": None, "insider_name": "Rossi", "company_name": "Acme",
              "unit_price": 9.5, "quantity": 1000.0}
        result = completeness_stats([tx])
        assert result["isin_missing_pct"] == 100.0

    def test_unknown_sentinel_is_missing(self):
        tx = {"isin": "IT0001", "insider_name": "Unknown", "company_name": "Acme",
              "unit_price": 9.5, "quantity": 1000.0}
        result = completeness_stats([tx])
        assert result["insider_name_missing_pct"] == 100.0

    def test_zero_numeric_is_missing(self):
        tx = {"isin": "IT0001", "insider_name": "Rossi", "company_name": "Acme",
              "unit_price": 0.0, "quantity": 1000.0}
        result = completeness_stats([tx])
        assert result["unit_price_missing_pct"] == 100.0

    def test_partial_missing_percentage(self):
        txs = [
            {"isin": "IT0001", "insider_name": "Rossi", "company_name": "Acme",
             "unit_price": 9.5, "quantity": 1000.0},
            {"isin": None, "insider_name": "Bianchi", "company_name": "Beta",
             "unit_price": 5.0, "quantity": 500.0},
        ]
        result = completeness_stats(txs)
        assert result["total"] == 2
        assert result["isin_missing_pct"] == 50.0


# ─── issuer_resolution_stats ──────────────────────────────────────────────────

class TestIssuerResolutionStats:
    def test_empty_returns_zeros(self):
        result = issuer_resolution_stats([])
        assert result["total"] == 0
        assert result["pending"] == 0
        assert result["resolved"] == 0
        assert result["rejected"] == 0
        assert result["false_match"] == 0
        assert result["resolution_rate_pct"] == 0.0

    def test_counts_by_status(self):
        rows = [
            {"status": "pending",  "false_match_issuer_id": None},
            {"status": "pending",  "false_match_issuer_id": None},
            {"status": "resolved", "false_match_issuer_id": None},
            {"status": "rejected", "false_match_issuer_id": None},
        ]
        result = issuer_resolution_stats(rows)
        assert result["total"] == 4
        assert result["pending"] == 2
        assert result["resolved"] == 1
        assert result["rejected"] == 1
        assert result["false_match"] == 0

    def test_false_match_counted_independently(self):
        # A resolved entry can also have a false_match_issuer_id
        rows = [
            {"status": "resolved", "false_match_issuer_id": 99},
            {"status": "resolved", "false_match_issuer_id": None},
        ]
        result = issuer_resolution_stats(rows)
        assert result["resolved"] == 2
        assert result["false_match"] == 1

    def test_resolution_rate_pct(self):
        rows = [
            {"status": "resolved", "false_match_issuer_id": None},
            {"status": "resolved", "false_match_issuer_id": None},
            {"status": "pending",  "false_match_issuer_id": None},
        ]
        result = issuer_resolution_stats(rows)
        assert result["resolution_rate_pct"] == pytest.approx(66.7, abs=0.1)


# ─── build_fixture ────────────────────────────────────────────────────────────

class TestBuildFixture:
    def _make_review(self, **kwargs):
        base = {
            "id": 7,
            "transaction_id": 42,
            "review_category": "unknown_direction",
            "outcome": "corrected",
            "corrected_direction": "buy",
            "corrected_transaction_type": "buy",
            "corrected_economic_intent": "discretionary",
            "correction_notes": "ACQUISTO keyword present in section 4a",
            "parser_version": "3.1.0",
        }
        base.update(kwargs)
        return base

    def test_corrected_values_used_in_expected(self):
        review = self._make_review(corrected_direction="buy", corrected_transaction_type="buy")
        tx = _tx(direction="unknown", transaction_type="unknown")
        fixture = build_fixture(review, tx, "some raw text", None, "test_fixture")
        expected_tx = fixture["expected"]["transactions"][0]
        assert expected_tx["direction"] == "buy"
        assert expected_tx["transaction_type"] == "buy"

    def test_original_values_used_when_not_corrected(self):
        # No corrected_direction set → falls back to original
        review = self._make_review(corrected_direction=None, corrected_transaction_type=None)
        tx = _tx(direction="sell", transaction_type="sell")
        fixture = build_fixture(review, tx, "some text", None, "test_fixture")
        expected_tx = fixture["expected"]["transactions"][0]
        assert expected_tx["direction"] == "sell"
        assert expected_tx["transaction_type"] == "sell"

    def test_raw_text_included(self):
        review = self._make_review()
        tx = _tx()
        raw = "AVVISO n.123\nACQUISTO\nPrice: 10 EUR  Volume: 500"
        fixture = build_fixture(review, tx, raw, None, "my_slug")
        assert fixture["raw_text"] == raw

    def test_stub_when_no_raw_text(self):
        review = self._make_review()
        tx = _tx(company_name="Acme SpA", insider_name="Rossi", raw_nature_text="ACQUISTO")
        fixture = build_fixture(review, tx, None, None, "stub_fixture")
        assert "[STUB" in fixture["raw_text"]
        assert "stub_warning" in fixture["meta"]

    def test_fixture_id_in_meta(self):
        review = self._make_review()
        tx = _tx()
        fixture = build_fixture(review, tx, "text", None, "my_custom_id")
        assert fixture["meta"]["id"] == "my_custom_id"

    def test_source_review_id_in_meta(self):
        review = self._make_review(id=99)
        tx = _tx()
        fixture = build_fixture(review, tx, "text", None, "fixture_x")
        assert fixture["meta"]["source_review_id"] == 99

    def test_needs_review_always_false_in_fixture(self):
        review = self._make_review()
        tx = _tx()
        fixture = build_fixture(review, tx, "text", None, "fixture_y")
        assert fixture["expected"]["transactions"][0]["needs_review"] is False

    def test_filing_meta_used_for_source_url_when_tx_has_none(self):
        review = self._make_review()
        tx = _tx(source_url=None)
        filing_meta = {"pdf_url": "https://example.com/doc.pdf", "filing_date": "2026-01-01"}
        fixture = build_fixture(review, tx, "text", filing_meta, "fixture_z")
        assert fixture["source_url"] == "https://example.com/doc.pdf"

    def test_count_is_always_1(self):
        review = self._make_review()
        tx = _tx()
        fixture = build_fixture(review, tx, "text", None, "fixture_count")
        assert fixture["expected"]["count"] == 1
        assert len(fixture["expected"]["transactions"]) == 1
