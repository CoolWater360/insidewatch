"""
Tests for alert context routing in scraper/listener.py.

Proves two invariants:
  1. Filings discovered on the current listing page are processed with
     alert_context="live" so alerts fire immediately on ingestion.
  2. Filings pulled from the retry queue (get_retryable_filings) are
     processed with alert_context="backfill" so they never cause a burst
     of live alerts when old sweep failures are retried in one listener run.
  3. A global ALERT_CONTEXT="backfill" env override silences even
     listing-page filings (for emergency/maintenance runs).
  4. dispatch() is never called by the sweep (run_phase2.py) without
     reading ALERT_CONTEXT from the environment.

These are structural / unit tests — they do not exercise the DB or HTTP stack.
"""

import os
import types
import unittest
from unittest.mock import MagicMock, patch, call

# ── Source inspection tests (no imports of listener needed) ───────────────────

import pathlib

_LISTENER_SRC = pathlib.Path(__file__).parent.parent / "scraper" / "listener.py"
_PHASE2_SRC   = pathlib.Path(__file__).parent.parent / "scraper" / "run_phase2.py"


class TestListenerAlertContextStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = _LISTENER_SRC.read_text()

    def test_listing_page_filings_tagged_live(self):
        """Filings from the listing page must use global_context (default 'live')."""
        self.assertIn("global_context", self.src,
                      "Expected global_context variable for listing-page alert context")
        # The listing-page append must reference global_context, not a hardcoded string
        self.assertIn('to_process.append((row, filing, global_context))', self.src)

    def test_retry_queue_filings_tagged_backfill(self):
        """Retry-queue filings must always use 'backfill' context."""
        self.assertIn('"backfill"', self.src,
                      "Expected literal 'backfill' string for retry-queue context")
        # The retry append must use a backfill context variable, not global_context
        self.assertIn('retry_context = "backfill"', self.src)
        self.assertIn('to_process.append((row, filing, retry_context))', self.src)

    def test_process_filing_accepts_alert_context_param(self):
        """_process_filing_with_ledger must accept alert_context parameter."""
        self.assertIn("alert_context: str", self.src)

    def test_dispatch_called_with_context_kwarg(self):
        """dispatch() in the ledger path must pass context=alert_context."""
        self.assertIn("context=alert_context", self.src)

    def test_dispatch_context_log_present(self):
        """An INFO log must record context= before each dispatch call."""
        self.assertIn("context=%s", self.src,
                      "Expected log line referencing context=% before dispatch")

    def test_global_context_reads_alert_context_env(self):
        """global_context must be read from ALERT_CONTEXT env var."""
        self.assertIn('os.getenv("ALERT_CONTEXT", "live")', self.src)


class TestSweepAlertContextStructure(unittest.TestCase):
    """Confirms the sweep (run_phase2.py) reads ALERT_CONTEXT before dispatching."""

    @classmethod
    def setUpClass(cls):
        cls.src = _PHASE2_SRC.read_text()

    def test_sweep_reads_alert_context_env(self):
        """run_phase2.py must read ALERT_CONTEXT env var before calling dispatch."""
        self.assertIn('os.getenv("ALERT_CONTEXT"', self.src)

    def test_sweep_passes_context_to_dispatch(self):
        """run_phase2.py dispatch call must include context= kwarg."""
        self.assertIn("context=alert_context", self.src)

    def test_sweep_defaults_to_live_not_backfill(self):
        """The default (if env var missing) should be 'live', not hardcoded 'backfill'.
        The workflow YAML sets ALERT_CONTEXT=backfill for sweeps; the code itself
        should not assume it is always a backfill (supports manual live sweep)."""
        self.assertIn('os.getenv("ALERT_CONTEXT", "live")', self.src)


# ── Behavioural unit tests ────────────────────────────────────────────────────

class TestDispatchContextRouting(unittest.TestCase):
    """
    Unit-test the dispatch gate in alerts.py: confirms that context='backfill'
    suppresses the alert and context='live' lets it through (subject to other
    quality filters).
    """

    def _make_payload(self, **kwargs):
        from scraper.alerts import AlertPayload
        defaults = dict(
            company_name="Acme SpA",
            company_id=1,
            insider_name="Mario Rossi",
            insider_role="CEO",
            direction="buy",
            transaction_type="buy",
            quantity=1000,
            unit_price=10.0,
            total_value=10_000.0,
            currency="EUR",
            transaction_date="2026-06-01",
            filed_date="2026-06-01",
            source_url="https://example.com/filing.pdf",
            transaction_id=42,
        )
        defaults.update(kwargs)
        return AlertPayload(**defaults)

    def test_backfill_context_suppresses_alert(self):
        from scraper.alerts import dispatch
        client = MagicMock()
        with patch("scraper.alerts.send_email") as mock_email, \
             patch("scraper.telegram.send_telegram", return_value=False):
            dispatch(
                self._make_payload(total_value=50_000.0),
                client=client,
                company_tier=1,
                context="backfill",
            )
        mock_email.assert_not_called()

    def test_live_context_fires_alert(self):
        from scraper.alerts import dispatch
        client = MagicMock()
        with patch("scraper.alerts.send_email", return_value=True) as mock_email, \
             patch("scraper.telegram.send_telegram", return_value=False), \
             patch("scraper.alerts.check_cluster", return_value=None):
            dispatch(
                self._make_payload(total_value=50_000.0),
                client=client,
                company_tier=1,
                context="live",
            )
        mock_email.assert_called_once()

    def test_backfill_context_logs_suppression(self):
        from scraper.alerts import dispatch
        client = MagicMock()
        with patch("scraper.alerts.send_email") as mock_email, \
             patch("scraper.telegram.send_telegram", return_value=False), \
             self.assertLogs("scraper.alerts", level="INFO") as cm:
            dispatch(
                self._make_payload(total_value=50_000.0),
                client=client,
                company_tier=1,
                context="backfill",
            )
        mock_email.assert_not_called()
        self.assertTrue(
            any("backfill" in line for line in cm.output),
            "Expected a log message mentioning 'backfill' suppression",
        )


if __name__ == "__main__":
    unittest.main()
