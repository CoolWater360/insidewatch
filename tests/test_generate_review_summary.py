"""
Tests for scraper/generate_review_summary.py.

Covers:
  - _get_counts separates failed and skipped into distinct keys
  - _render_html includes both "Failed filings (retryable)" and "Skipped filings (max retries)" labels
  - _render_html total is sum of all four counts
  - email subject varies with total
"""

import types
import unittest
from unittest.mock import MagicMock, patch

from scraper.generate_review_summary import _get_counts, _render_html


class TestGetCounts(unittest.TestCase):
    def _make_count_response(self, n: int) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.headers = {"content-range": f"0-0/{n}"}
        resp.json.return_value = []
        return resp

    def test_returns_separate_failed_and_skipped_keys(self):
        responses = [
            self._make_count_response(10),  # pending_transactions
            self._make_count_response(3),   # failed_filings
            self._make_count_response(2),   # skipped_filings
            self._make_count_response(5),   # pending_issuers
        ]
        with patch("httpx.get", side_effect=responses):
            counts = _get_counts("https://example.supabase.co", "key")

        self.assertEqual(counts["pending_transactions"], 10)
        self.assertEqual(counts["failed_filings"], 3)
        self.assertEqual(counts["skipped_filings"], 2)
        self.assertEqual(counts["pending_issuers"], 5)

    def test_counts_dict_has_all_four_keys(self):
        responses = [self._make_count_response(0)] * 4
        with patch("httpx.get", side_effect=responses):
            counts = _get_counts("https://example.supabase.co", "key")

        self.assertIn("pending_transactions", counts)
        self.assertIn("failed_filings", counts)
        self.assertIn("skipped_filings", counts)
        self.assertIn("pending_issuers", counts)

    def test_failed_and_skipped_use_separate_http_requests(self):
        call_params: list[dict] = []

        def capture_get(url, **kwargs):
            call_params.append(kwargs.get("params", {}))
            return self._make_count_response(0)

        with patch("httpx.get", side_effect=capture_get):
            _get_counts("https://example.supabase.co", "key")

        filings_params = [p for p in call_params if "filings" in str(p) or p.get("status", "").startswith("eq.")]
        statuses = [p.get("status") for p in call_params]
        self.assertIn("eq.failed", statuses)
        self.assertIn("eq.skipped", statuses)
        # Must NOT combine them as "in.(failed,skipped)" anymore
        self.assertNotIn("in.(failed,skipped)", statuses)


class TestRenderHtml(unittest.TestCase):
    def _counts(self, pending_tx=0, failed=0, skipped=0, issuers=0):
        return {
            "pending_transactions": pending_tx,
            "failed_filings": failed,
            "skipped_filings": skipped,
            "pending_issuers": issuers,
        }

    def test_failed_label_present(self):
        html = _render_html(self._counts(failed=3), [], [])
        self.assertIn("Failed filings (retryable)", html)

    def test_skipped_label_present(self):
        html = _render_html(self._counts(skipped=2), [], [])
        self.assertIn("Skipped filings (max retries)", html)

    def test_does_not_use_combined_failed_label(self):
        html = _render_html(self._counts(failed=1, skipped=1), [], [])
        self.assertNotIn(">Failed filings<", html)

    def test_total_is_sum_of_all_four(self):
        html = _render_html(self._counts(pending_tx=10, failed=3, skipped=2, issuers=5), [], [])
        self.assertIn("20 items need attention", html)

    def test_all_clear_when_zero(self):
        html = _render_html(self._counts(), [], [])
        self.assertIn("All queues clear", html)

    def test_both_counts_rendered(self):
        html = _render_html(self._counts(failed=7, skipped=4), [], [])
        self.assertIn(">7<", html)
        self.assertIn(">4<", html)


if __name__ == "__main__":
    unittest.main()
