"""
Tests for scraper/alerts.py — alert quality filters.

Covers:
  · Low-value transaction suppression
  · Mechanical / non-discretionary type suppression
  · Old-filing suppression
  · Discretionary high-value transaction passes through
  · urgent=True bypasses all quality filters
  · Configuration loading from environment variables
"""

import os
from datetime import date, datetime, timedelta

import pytest

from scraper.alerts import (
    AlertConfig,
    AlertPayload,
    MECHANICAL_TRANSACTION_TYPES,
    _load_alert_config,
    _parse_filed_date,
    should_dispatch,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _today_iso() -> str:
    return datetime.utcnow().date().isoformat()


def _today_dmy() -> str:
    return datetime.utcnow().date().strftime("%d/%m/%Y")


def _days_ago_iso(n: int) -> str:
    return (datetime.utcnow().date() - timedelta(days=n)).isoformat()


def _days_ago_dmy(n: int) -> str:
    return (datetime.utcnow().date() - timedelta(days=n)).strftime("%d/%m/%Y")


def _payload(
    total_value: float = 100_000.0,
    transaction_type: str = "buy",
    direction: str = "buy",
    filed_date: str = None,
) -> AlertPayload:
    return AlertPayload(
        company_name="Acme SpA",
        company_id=1,
        insider_name="Mario Rossi",
        insider_role="CEO",
        direction=direction,
        transaction_type=transaction_type,
        quantity=10_000,
        unit_price=10.0,
        total_value=total_value,
        currency="EUR",
        transaction_date=_today_iso(),
        filed_date=filed_date or _today_iso(),
        source_url="https://example.com/filing.pdf",
        transaction_id=42,
    )


_DEFAULT_CONFIG = AlertConfig(
    min_value_eur=50_000.0,
    exclude_mechanical=True,
    max_filing_age_days=5,
)

_PERMISSIVE_CONFIG = AlertConfig(
    min_value_eur=0.0,
    exclude_mechanical=False,
    max_filing_age_days=0,
)


# ─── Value threshold ──────────────────────────────────────────────────────────

class TestValueThreshold:
    def test_below_threshold_is_suppressed(self):
        ok, reason = should_dispatch(_payload(total_value=500.0), _DEFAULT_CONFIG)
        assert not ok
        assert "below_min_value" in reason

    def test_exactly_at_threshold_passes(self):
        ok, _ = should_dispatch(_payload(total_value=50_000.0), _DEFAULT_CONFIG)
        assert ok

    def test_above_threshold_passes(self):
        ok, _ = should_dispatch(_payload(total_value=500_000.0), _DEFAULT_CONFIG)
        assert ok

    def test_zero_threshold_disables_value_filter(self):
        config = AlertConfig(min_value_eur=0.0, exclude_mechanical=False, max_filing_age_days=0)
        ok, _ = should_dispatch(_payload(total_value=1.0), config)
        assert ok

    def test_reason_contains_actual_value_and_threshold(self):
        ok, reason = should_dispatch(_payload(total_value=499.0), _DEFAULT_CONFIG)
        assert not ok
        assert "499" in reason
        assert "50.000" in reason or "50,000" in reason or "50000" in reason


# ─── Mechanical type exclusion ────────────────────────────────────────────────

class TestMechanicalExclusion:
    @pytest.mark.parametrize("tx_type", sorted(MECHANICAL_TRANSACTION_TYPES))
    def test_mechanical_types_suppressed_by_default(self, tx_type):
        ok, reason = should_dispatch(
            _payload(total_value=200_000.0, transaction_type=tx_type),
            _DEFAULT_CONFIG,
        )
        assert not ok, f"{tx_type} should be suppressed but passed"
        assert "mechanical_type" in reason

    def test_buy_discretionary_passes(self):
        ok, _ = should_dispatch(
            _payload(total_value=200_000.0, transaction_type="buy"),
            _DEFAULT_CONFIG,
        )
        assert ok

    def test_sell_discretionary_passes(self):
        ok, _ = should_dispatch(
            _payload(total_value=200_000.0, transaction_type="sell", direction="sell"),
            _DEFAULT_CONFIG,
        )
        assert ok

    def test_mechanical_passes_when_filter_disabled(self):
        config = AlertConfig(min_value_eur=0.0, exclude_mechanical=False, max_filing_age_days=0)
        ok, _ = should_dispatch(
            _payload(total_value=200_000.0, transaction_type="grant"),
            config,
        )
        assert ok

    def test_grant_suppressed_even_at_high_value(self):
        """Grants should be excluded regardless of value — they are not trading signals."""
        ok, reason = should_dispatch(
            _payload(total_value=5_000_000.0, transaction_type="grant"),
            _DEFAULT_CONFIG,
        )
        assert not ok
        assert "mechanical_type" in reason


# ─── Filing age ───────────────────────────────────────────────────────────────

class TestFilingAge:
    def test_today_filing_passes(self):
        ok, _ = should_dispatch(_payload(filed_date=_today_iso()), _DEFAULT_CONFIG)
        assert ok

    def test_filing_within_window_passes(self):
        ok, _ = should_dispatch(_payload(filed_date=_days_ago_iso(4)), _DEFAULT_CONFIG)
        assert ok

    def test_filing_at_exact_window_passes(self):
        ok, _ = should_dispatch(_payload(filed_date=_days_ago_iso(5)), _DEFAULT_CONFIG)
        assert ok

    def test_old_filing_is_suppressed(self):
        ok, reason = should_dispatch(_payload(filed_date=_days_ago_iso(6)), _DEFAULT_CONFIG)
        assert not ok
        assert "filing_too_old" in reason

    def test_old_filing_dmy_format_is_suppressed(self):
        """DD/MM/YYYY (Italian listing page format) must also be parsed correctly."""
        ok, reason = should_dispatch(_payload(filed_date=_days_ago_dmy(30)), _DEFAULT_CONFIG)
        assert not ok
        assert "filing_too_old" in reason

    def test_age_filter_disabled_at_zero(self):
        config = AlertConfig(min_value_eur=0.0, exclude_mechanical=False, max_filing_age_days=0)
        ok, _ = should_dispatch(_payload(filed_date=_days_ago_iso(365)), config)
        assert ok

    def test_unparseable_filed_date_passes(self):
        """If we can't parse the date, we let it through rather than suppressing."""
        ok, _ = should_dispatch(_payload(filed_date="not-a-date"), _DEFAULT_CONFIG)
        assert ok

    def test_empty_filed_date_passes(self):
        ok, _ = should_dispatch(_payload(filed_date=""), _DEFAULT_CONFIG)
        assert ok


# ─── Urgent bypass ────────────────────────────────────────────────────────────

class TestUrgentBypass:
    def test_urgent_bypasses_value_filter(self):
        ok, _ = should_dispatch(_payload(total_value=1.0), _DEFAULT_CONFIG, urgent=True)
        assert ok

    def test_urgent_bypasses_mechanical_filter(self):
        ok, _ = should_dispatch(
            _payload(total_value=1.0, transaction_type="grant"),
            _DEFAULT_CONFIG,
            urgent=True,
        )
        assert ok

    def test_urgent_bypasses_age_filter(self):
        ok, _ = should_dispatch(_payload(filed_date=_days_ago_iso(365)), _DEFAULT_CONFIG, urgent=True)
        assert ok

    def test_urgent_passes_everything(self):
        ok, reason = should_dispatch(
            _payload(total_value=0.0, transaction_type="conversion", filed_date=_days_ago_iso(999)),
            _DEFAULT_CONFIG,
            urgent=True,
        )
        assert ok
        assert reason == ""


# ─── Combined: discretionary high-value new filing ────────────────────────────

class TestDiscretionaryHighValueNewFiling:
    """The golden path — this must always produce an alert."""

    def test_buy_100k_today(self):
        ok, _ = should_dispatch(
            _payload(total_value=100_000.0, transaction_type="buy", filed_date=_today_iso()),
            _DEFAULT_CONFIG,
        )
        assert ok

    def test_sell_500k_yesterday(self):
        ok, _ = should_dispatch(
            _payload(
                total_value=500_000.0,
                transaction_type="sell",
                direction="sell",
                filed_date=_days_ago_iso(1),
            ),
            _DEFAULT_CONFIG,
        )
        assert ok

    def test_subscription_above_threshold_is_discretionary(self):
        # subscription = discretionary (capital-increase rights issue participation)
        assert "subscription" not in MECHANICAL_TRANSACTION_TYPES
        ok, _ = should_dispatch(
            _payload(total_value=100_000.0, transaction_type="subscription"),
            _DEFAULT_CONFIG,
        )
        assert ok


# ─── Config loading ───────────────────────────────────────────────────────────

class TestConfigLoading:
    def test_defaults_when_no_env(self, monkeypatch):
        monkeypatch.delenv("ALERT_MIN_VALUE_EUR", raising=False)
        monkeypatch.delenv("ALERT_EXCLUDE_MECHANICAL", raising=False)
        monkeypatch.delenv("ALERT_MAX_FILING_AGE_DAYS", raising=False)
        cfg = _load_alert_config()
        assert cfg.min_value_eur == 50_000.0
        assert cfg.exclude_mechanical is True
        assert cfg.max_filing_age_days == 5

    def test_custom_values_loaded(self, monkeypatch):
        monkeypatch.setenv("ALERT_MIN_VALUE_EUR", "100000")
        monkeypatch.setenv("ALERT_EXCLUDE_MECHANICAL", "false")
        monkeypatch.setenv("ALERT_MAX_FILING_AGE_DAYS", "0")
        cfg = _load_alert_config()
        assert cfg.min_value_eur == 100_000.0
        assert cfg.exclude_mechanical is False
        assert cfg.max_filing_age_days == 0

    def test_exclude_mechanical_false_variants(self, monkeypatch):
        for falsy in ("0", "false", "False", "FALSE", "no", "NO"):
            monkeypatch.setenv("ALERT_EXCLUDE_MECHANICAL", falsy)
            cfg = _load_alert_config()
            assert cfg.exclude_mechanical is False, f"Expected False for {falsy!r}"

    def test_exclude_mechanical_true_variants(self, monkeypatch):
        for truthy in ("1", "true", "True", "TRUE", "yes", "YES", "anything"):
            monkeypatch.setenv("ALERT_EXCLUDE_MECHANICAL", truthy)
            cfg = _load_alert_config()
            assert cfg.exclude_mechanical is True, f"Expected True for {truthy!r}"

    def test_invalid_min_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ALERT_MIN_VALUE_EUR", "not-a-number")
        cfg = _load_alert_config()
        assert cfg.min_value_eur == 50_000.0

    def test_invalid_age_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("ALERT_MAX_FILING_AGE_DAYS", "abc")
        cfg = _load_alert_config()
        assert cfg.max_filing_age_days == 5


# ─── Date parsing ─────────────────────────────────────────────────────────────

class TestParseDateHelper:
    def test_iso_format(self):
        assert _parse_filed_date("2026-01-15") == date(2026, 1, 15)

    def test_dmy_format(self):
        assert _parse_filed_date("15/01/2026") == date(2026, 1, 15)

    def test_iso_with_time_suffix(self):
        assert _parse_filed_date("2026-01-15T09:00:00+00:00") == date(2026, 1, 15)

    def test_garbage_returns_none(self):
        assert _parse_filed_date("not-a-date") is None

    def test_empty_returns_none(self):
        assert _parse_filed_date("") is None


# ─── MECHANICAL_TRANSACTION_TYPES completeness ────────────────────────────────

class TestMechanicalSet:
    def test_all_expected_types_present(self):
        expected = {
            "grant", "option_exercise", "sell_to_cover",
            "conversion", "inheritance",
            "gift_in", "gift_out", "transfer_in", "transfer_out",
            "pledge_or_security", "derivative_transaction",
        }
        assert expected.issubset(MECHANICAL_TRANSACTION_TYPES), (
            f"Missing from MECHANICAL_TRANSACTION_TYPES: {expected - MECHANICAL_TRANSACTION_TYPES}"
        )

    def test_discretionary_types_absent(self):
        discretionary = {"buy", "sell", "subscription"}
        overlap = discretionary & MECHANICAL_TRANSACTION_TYPES
        assert not overlap, f"Discretionary types must not be in MECHANICAL_TRANSACTION_TYPES: {overlap}"
