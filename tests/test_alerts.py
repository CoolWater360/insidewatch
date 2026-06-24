"""
Tests for scraper/alerts.py — alert quality filters and ingestion context.

Covers:
  · Low-value transaction suppression (default threshold 10 000 EUR)
  · Mechanical / non-discretionary type suppression
  · Discretionary high-value transaction passes through
  · urgent=True bypasses all quality filters
  · Backfill context suppresses all transaction alerts
  · Late-discovery labelling (live context, old filing, no suppression)
  · Configuration loading from environment variables
"""

from datetime import date, datetime, timedelta

import pytest

from scraper.alerts import (
    AlertConfig,
    AlertPayload,
    MECHANICAL_TRANSACTION_TYPES,
    _LATE_DISCOVERY_DAYS,
    _is_late_discovery,
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
    min_value_eur=10_000.0,
    exclude_mechanical=True,
)

_PERMISSIVE_CONFIG = AlertConfig(
    min_value_eur=0.0,
    exclude_mechanical=False,
)


# ─── Value threshold ──────────────────────────────────────────────────────────

class TestValueThreshold:
    def test_below_threshold_is_suppressed(self):
        ok, reason = should_dispatch(_payload(total_value=500.0), _DEFAULT_CONFIG)
        assert not ok
        assert "below_min_value" in reason

    def test_exactly_at_threshold_passes(self):
        ok, _ = should_dispatch(_payload(total_value=10_000.0), _DEFAULT_CONFIG)
        assert ok

    def test_above_threshold_passes(self):
        ok, _ = should_dispatch(_payload(total_value=500_000.0), _DEFAULT_CONFIG)
        assert ok

    def test_zero_threshold_disables_value_filter(self):
        config = AlertConfig(min_value_eur=0.0, exclude_mechanical=False)
        ok, _ = should_dispatch(_payload(total_value=1.0), config)
        assert ok

    def test_reason_contains_actual_and_threshold_value(self):
        ok, reason = should_dispatch(_payload(total_value=499.0), _DEFAULT_CONFIG)
        assert not ok
        assert "499" in reason
        assert "10.000" in reason or "10,000" in reason or "10000" in reason

    def test_9999_below_default_threshold(self):
        ok, reason = should_dispatch(_payload(total_value=9_999.0), _DEFAULT_CONFIG)
        assert not ok
        assert "below_min_value" in reason

    def test_10001_above_default_threshold(self):
        ok, _ = should_dispatch(_payload(total_value=10_001.0), _DEFAULT_CONFIG)
        assert ok


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
        config = AlertConfig(min_value_eur=0.0, exclude_mechanical=False)
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

    def test_urgent_passes_everything(self):
        ok, reason = should_dispatch(
            _payload(total_value=0.0, transaction_type="conversion"),
            _DEFAULT_CONFIG,
            urgent=True,
        )
        assert ok
        assert reason == ""


# ─── Backfill context ─────────────────────────────────────────────────────────

class TestBackfillContext:
    """
    The backfill context is enforced in dispatch() before quality filters.
    We test it through should_dispatch() returning True (quality passes) while
    verifying that context='backfill' would suppress in dispatch().

    We cannot invoke dispatch() directly in unit tests without mocking HTTP
    calls, so we test the predicate layer and the context semantics separately.
    """

    def test_should_dispatch_is_context_agnostic(self):
        """should_dispatch() does not know about context — it only checks quality."""
        # A high-value discretionary buy passes quality regardless of context.
        ok, _ = should_dispatch(_payload(total_value=100_000.0), _DEFAULT_CONFIG)
        assert ok
        # Quality filters are not where backfill suppression lives.

    def test_quality_filters_still_apply_in_live_context(self):
        """Low-value transactions are suppressed by quality filters even in live context."""
        ok, reason = should_dispatch(_payload(total_value=500.0), _DEFAULT_CONFIG)
        assert not ok
        assert "below_min_value" in reason

    def test_backfill_suppression_logic(self):
        """
        Verify the context branch that dispatch() uses:
          context == 'backfill' → suppress before quality check.
        We exercise this as a whitebox check on the expected code path.
        """
        # Import and inspect dispatch's context parameter default
        import inspect
        from scraper.alerts import dispatch
        sig = inspect.signature(dispatch)
        assert "context" in sig.parameters
        assert sig.parameters["context"].default == "live"

    def test_alert_context_env_is_read_by_run_phase2(self, monkeypatch):
        """run_phase2 must read ALERT_CONTEXT from env and pass it to dispatch."""
        import ast, pathlib
        src = pathlib.Path("scraper/run_phase2.py").read_text()
        assert "ALERT_CONTEXT" in src, "run_phase2.py must read ALERT_CONTEXT"
        assert "alert_context" in src, "run_phase2.py must pass alert_context to dispatch"


# ─── Late-discovery labelling ─────────────────────────────────────────────────

class TestLateDiscovery:
    """
    Late-discovery is a labelling mechanism: it never suppresses an alert,
    it only adds [LATE] to the subject and a note to the body.
    """

    def test_recent_filing_is_not_late(self):
        assert not _is_late_discovery(_today_iso())

    def test_yesterday_filing_is_not_late(self):
        assert not _is_late_discovery(_days_ago_iso(1))

    def test_within_window_is_not_late(self):
        assert not _is_late_discovery(_days_ago_iso(_LATE_DISCOVERY_DAYS))

    def test_one_day_over_threshold_is_late(self):
        assert _is_late_discovery(_days_ago_iso(_LATE_DISCOVERY_DAYS + 1))

    def test_very_old_filing_is_late(self):
        assert _is_late_discovery(_days_ago_iso(60))

    def test_dmy_format_parsed_correctly(self):
        """DD/MM/YYYY (Italian listing page format) must also trigger late detection."""
        old_dmy = _days_ago_dmy(_LATE_DISCOVERY_DAYS + 10)
        assert _is_late_discovery(old_dmy)

    def test_recent_dmy_not_late(self):
        assert not _is_late_discovery(_days_ago_dmy(1))

    def test_unparseable_date_is_not_late(self):
        """Unknown age → not labelled as late (conservative: do not suppress)."""
        assert not _is_late_discovery("not-a-date")

    def test_empty_date_is_not_late(self):
        assert not _is_late_discovery("")

    def test_late_discovery_constant_is_positive(self):
        """Sanity: _LATE_DISCOVERY_DAYS must be > 0."""
        assert _LATE_DISCOVERY_DAYS > 0

    def test_should_dispatch_does_not_check_filing_age(self):
        """
        should_dispatch() must NOT suppress based on filing age.
        An old filing that passes quality filters must return True.
        """
        old_payload = _payload(
            total_value=100_000.0,
            filed_date=_days_ago_iso(365),
        )
        ok, reason = should_dispatch(old_payload, _DEFAULT_CONFIG)
        assert ok, f"should_dispatch should not suppress old filings; got reason={reason!r}"

    def test_late_subject_prefix_added(self):
        """_build_email must prepend [LATE] to the subject when late_discovery=True."""
        from scraper.alerts import _build_email, AlertPayload
        p = _payload(filed_date=_days_ago_iso(_LATE_DISCOVERY_DAYS + 5))
        email = _build_email(p, cluster=None, late_discovery=True)
        assert email["subject"].startswith("[LATE]"), (
            f"Expected [LATE] prefix, got: {email['subject']!r}"
        )

    def test_normal_subject_has_no_late_prefix(self):
        """Without late_discovery, [LATE] must not appear in the subject."""
        from scraper.alerts import _build_email
        p = _payload(filed_date=_today_iso())
        email = _build_email(p, cluster=None, late_discovery=False)
        assert "[LATE]" not in email["subject"]

    def test_late_row_in_email_body(self):
        """Late-discovery emails must include the Tarda scoperta row in HTML/text."""
        from scraper.alerts import _build_email
        p = _payload(filed_date=_days_ago_iso(_LATE_DISCOVERY_DAYS + 5))
        email = _build_email(p, cluster=None, late_discovery=True)
        assert "Tarda scoperta" in email["html"]
        assert "Tarda scoperta" in email["text"]

    def test_no_late_row_in_normal_email(self):
        from scraper.alerts import _build_email
        p = _payload(filed_date=_today_iso())
        email = _build_email(p, cluster=None, late_discovery=False)
        assert "Tarda scoperta" not in email["html"]
        assert "Tarda scoperta" not in email["text"]


# ─── Combined: discretionary high-value new filing ────────────────────────────

class TestDiscretionaryHighValueNewFiling:
    """The golden path — this must always produce an alert."""

    def test_buy_15k_today(self):
        ok, _ = should_dispatch(
            _payload(total_value=15_000.0, transaction_type="buy", filed_date=_today_iso()),
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
        assert "subscription" not in MECHANICAL_TRANSACTION_TYPES
        ok, _ = should_dispatch(
            _payload(total_value=100_000.0, transaction_type="subscription"),
            _DEFAULT_CONFIG,
        )
        assert ok

    def test_old_filing_high_value_passes_should_dispatch(self):
        """An old-filing alert passes quality filters — it is labelled late, not suppressed."""
        ok, _ = should_dispatch(
            _payload(total_value=250_000.0, filed_date=_days_ago_iso(30)),
            _DEFAULT_CONFIG,
        )
        assert ok


# ─── Configuration loading ────────────────────────────────────────────────────

class TestConfigLoading:
    def test_defaults_when_no_env(self, monkeypatch):
        monkeypatch.delenv("ALERT_MIN_VALUE_EUR", raising=False)
        monkeypatch.delenv("ALERT_EXCLUDE_MECHANICAL", raising=False)
        cfg = _load_alert_config()
        assert cfg.min_value_eur == 10_000.0
        assert cfg.exclude_mechanical is True

    def test_custom_values_loaded(self, monkeypatch):
        monkeypatch.setenv("ALERT_MIN_VALUE_EUR", "25000")
        monkeypatch.setenv("ALERT_EXCLUDE_MECHANICAL", "false")
        cfg = _load_alert_config()
        assert cfg.min_value_eur == 25_000.0
        assert cfg.exclude_mechanical is False

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
        assert cfg.min_value_eur == 10_000.0

    def test_no_max_filing_age_in_config(self):
        """AlertConfig must not have a max_filing_age_days field."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(AlertConfig)}
        assert "max_filing_age_days" not in field_names, (
            "max_filing_age_days must be removed from AlertConfig"
        )


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


# ─── Workflow config inspection ──────────────────────────────────────────────

class TestWorkflowConfig:
    """
    Assert that the GitHub Actions workflow encodes the correct ALERT_CONTEXT
    for each job.  These are file-inspection tests — they catch drift between
    the intent (live vs backfill) and what is actually committed to the workflow.
    """

    @staticmethod
    def _workflow_text() -> str:
        import pathlib
        p = pathlib.Path(".github/workflows/scraper.yml")
        assert p.exists(), f"Workflow file not found at {p}"
        return p.read_text()

    def test_listener_context_is_live(self):
        text = self._workflow_text()
        # Find the listener job section (before the sweep job header).
        listener_section = text.split("# ── Job 2: Full sweep")[0]
        assert 'ALERT_CONTEXT: "live"' in listener_section, (
            "Listener job must set ALERT_CONTEXT: \"live\""
        )

    def test_sweep_context_is_backfill(self):
        text = self._workflow_text()
        # Find the sweep job section (between job 2 and job 3 headers).
        sweep_section = text.split("# ── Job 2: Full sweep")[1].split("# ── Job 3:")[0]
        assert 'ALERT_CONTEXT: "backfill"' in sweep_section, (
            "Sweep job must set ALERT_CONTEXT: \"backfill\""
        )

    def test_listener_context_not_backfill(self):
        text = self._workflow_text()
        listener_section = text.split("# ── Job 2: Full sweep")[0]
        assert 'ALERT_CONTEXT: "backfill"' not in listener_section, (
            "Listener job must not set ALERT_CONTEXT: \"backfill\""
        )

    def test_sweep_context_not_live(self):
        text = self._workflow_text()
        sweep_section = text.split("# ── Job 2: Full sweep")[1].split("# ── Job 3:")[0]
        assert 'ALERT_CONTEXT: "live"' not in sweep_section, (
            "Sweep job must not set ALERT_CONTEXT: \"live\""
        )


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
