"""
Phase 9 unit tests — latency timestamps, structured logging, health check.

Pure unit tests: no real Supabase connection required.
Uses unittest.mock to fake DB calls.
"""

import json
import logging
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# ── Scraper package import ─────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── 1. Structured JSON logging ───────────────────────────────────────────────

class TestJsonFormatter(unittest.TestCase):
    def setUp(self):
        from scraper.logging_config import JsonFormatter
        self.formatter = JsonFormatter()

    def _make_record(self, msg: str, level=logging.INFO) -> logging.LogRecord:
        return logging.LogRecord(
            name="test", level=level, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )

    def test_produces_valid_json(self):
        record = self._make_record("hello world")
        line = self.formatter.format(record)
        parsed = json.loads(line)  # must not raise
        self.assertIsInstance(parsed, dict)

    def test_required_fields_present(self):
        record = self._make_record("test message")
        parsed = json.loads(self.formatter.format(record))
        for field in ("ts", "level", "logger", "msg"):
            self.assertIn(field, parsed)

    def test_level_name_correct(self):
        record = self._make_record("warn!", level=logging.WARNING)
        parsed = json.loads(self.formatter.format(record))
        self.assertEqual(parsed["level"], "WARNING")

    def test_msg_content_preserved(self):
        record = self._make_record("operazione di internal dealing")
        parsed = json.loads(self.formatter.format(record))
        self.assertEqual(parsed["msg"], "operazione di internal dealing")

    def test_ts_is_parseable_iso8601(self):
        record = self._make_record("ts test")
        parsed = json.loads(self.formatter.format(record))
        # Must be parseable; will raise ValueError if malformed.
        datetime.fromisoformat(parsed["ts"].replace("Z", "+00:00"))

    def test_no_exc_field_when_no_exception(self):
        record = self._make_record("no exc")
        parsed = json.loads(self.formatter.format(record))
        self.assertNotIn("exc", parsed)


class TestConfigureLogging(unittest.TestCase):
    def test_runs_without_error_plain(self):
        from scraper.logging_config import configure_logging
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOG_FORMAT", None)
            configure_logging(verbose=False)  # must not raise

    def test_runs_without_error_json(self):
        from scraper.logging_config import configure_logging
        with patch.dict(os.environ, {"LOG_FORMAT": "json"}):
            configure_logging(verbose=True)  # must not raise

    def test_verbose_sets_debug_level(self):
        from scraper.logging_config import configure_logging
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOG_FORMAT", None)
            configure_logging(verbose=True)
        self.assertEqual(logging.getLogger().level, logging.DEBUG)


# ─── 2. Latency timestamp helpers in scraper.filings ──────────────────────────

class TestSourcePublishedUtc(unittest.TestCase):
    def _call(self, filing_date):
        from scraper.filings import _source_published_utc
        return _source_published_utc(filing_date)

    def test_returns_none_for_none(self):
        self.assertIsNone(self._call(None))

    def test_returns_none_for_empty_string(self):
        self.assertIsNone(self._call(""))

    def test_returns_iso_string(self):
        result = self._call("2026-06-24")
        self.assertIsNotNone(result)
        self.assertIn("2026-06-24", result)

    def test_returns_09h_utc(self):
        result = self._call("2026-06-24")
        dt = datetime.fromisoformat(result)
        self.assertEqual(dt.hour, 9)
        self.assertEqual(dt.minute, 0)

    def test_timezone_aware(self):
        result = self._call("2026-06-24")
        dt = datetime.fromisoformat(result)
        self.assertIsNotNone(dt.tzinfo)

    def test_handles_malformed_date_gracefully(self):
        result = self._call("not-a-date")
        self.assertIsNone(result)


class TestRecordDownloaded(unittest.TestCase):
    def test_calls_filings_update(self):
        from scraper.filings import record_downloaded
        mock_client = MagicMock()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        record_downloaded(mock_client, 42)
        mock_client.table.assert_called_with("filings")
        update_call = mock_client.table.return_value.update.call_args[0][0]
        self.assertIn("downloaded_utc", update_call)

    def test_non_fatal_on_exception(self):
        from scraper.filings import record_downloaded
        mock_client = MagicMock()
        mock_client.table.side_effect = RuntimeError("DB down")
        # Must not raise
        record_downloaded(mock_client, 99)


class TestRecordParsed(unittest.TestCase):
    def test_calls_filings_update_with_both_columns(self):
        from scraper.filings import record_parsed
        mock_client = MagicMock()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = None
        record_parsed(mock_client, 7)
        update_payload = mock_client.table.return_value.update.call_args[0][0]
        self.assertIn("parsed_utc", update_payload)
        self.assertIn("validated_utc", update_payload)

    def test_non_fatal_on_exception(self):
        from scraper.filings import record_parsed
        mock_client = MagicMock()
        mock_client.table.side_effect = RuntimeError("DB down")
        record_parsed(mock_client, 99)  # must not raise


# ─── 3. complete_filing stamps delivered_utc ──────────────────────────────────

class TestCompleteFilingDeliveredUtc(unittest.TestCase):
    def test_delivered_utc_in_update_payload(self):
        from scraper.filings import complete_filing
        mock_update = MagicMock()
        mock_update.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[{"id": 1}])
        mock_client = MagicMock()
        mock_client.table.return_value.update.return_value = mock_update

        complete_filing(mock_client, 1, tx_inserted=3, tx_dedup=0,
                        pdf_sha256="abc", claim_token="tok-1")

        payload = mock_client.table.return_value.update.call_args[0][0]
        self.assertIn("delivered_utc", payload)
        self.assertIsNotNone(payload["delivered_utc"])


# ─── 4. register_filing stamps discovered_utc and source_published_utc ────────

class TestRegisterFilingTimestamps(unittest.TestCase):
    def _make_client(self, existing=None):
        mock_client = MagicMock()
        # Simulate no existing row → triggers INSERT path
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
            MagicMock(data=existing or [])
        mock_client.table.return_value.insert.return_value.execute.return_value = \
            MagicMock(data=[{"id": 1, "status": "pending"}])
        return mock_client

    def test_insert_includes_discovered_utc(self):
        from scraper.filings import register_filing
        client = self._make_client()
        register_filing(client, "https://example.com/filing.pdf", "2026-06-24", "Eni")
        insert_payload = client.table.return_value.insert.call_args[0][0]
        self.assertIn("discovered_utc", insert_payload)
        self.assertIsNotNone(insert_payload["discovered_utc"])

    def test_insert_includes_source_published_utc(self):
        from scraper.filings import register_filing
        client = self._make_client()
        register_filing(client, "https://example.com/filing.pdf", "2026-06-24", "Eni")
        insert_payload = client.table.return_value.insert.call_args[0][0]
        self.assertIn("source_published_utc", insert_payload)
        self.assertIsNotNone(insert_payload["source_published_utc"])

    def test_returns_existing_row_without_insert(self):
        from scraper.filings import register_filing
        existing_row = {"id": 5, "status": "completed", "pdf_url": "https://example.com/x.pdf"}
        client = self._make_client(existing=[existing_row])
        result = register_filing(client, "https://example.com/x.pdf", None, None)
        self.assertEqual(result["id"], 5)
        client.table.return_value.insert.assert_not_called()


# ─── 5. Health check report structure ────────────────────────────────────────

class TestHealthCheckStructure(unittest.TestCase):
    def test_run_health_check_returns_required_keys(self):
        from scraper.run_health_check import run_health_check

        # Patch all individual check functions so no real HTTP calls are made.
        with patch("scraper.run_health_check.check_db",
                   return_value={"ok": True, "message": "ok"}), \
             patch("scraper.run_health_check.check_recent_activity",
                   return_value={"ok": True, "message": "ok"}), \
             patch("scraper.run_health_check.check_failed_filings",
                   return_value={"ok": True, "message": "ok"}), \
             patch("scraper.run_health_check.check_review_queue",
                   return_value={"ok": True, "message": "ok"}):
            result = run_health_check("https://example.supabase.co", "fake-key")

        for key in ("status", "exit_code", "generated_at", "checks"):
            self.assertIn(key, result)

    def test_healthy_when_all_checks_pass(self):
        from scraper.run_health_check import run_health_check
        ok = {"ok": True, "message": "ok"}
        with patch("scraper.run_health_check.check_db", return_value=ok), \
             patch("scraper.run_health_check.check_recent_activity", return_value=ok), \
             patch("scraper.run_health_check.check_failed_filings", return_value=ok), \
             patch("scraper.run_health_check.check_review_queue", return_value=ok):
            result = run_health_check("u", "k")
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["exit_code"], 0)

    def test_critical_when_db_unreachable(self):
        from scraper.run_health_check import run_health_check
        ok   = {"ok": True,  "message": "ok"}
        fail = {"ok": False, "message": "DB unreachable"}
        with patch("scraper.run_health_check.check_db", return_value=fail), \
             patch("scraper.run_health_check.check_recent_activity", return_value=ok), \
             patch("scraper.run_health_check.check_failed_filings", return_value=ok), \
             patch("scraper.run_health_check.check_review_queue", return_value=ok):
            result = run_health_check("u", "k")
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["exit_code"], 2)

    def test_degraded_when_only_non_critical_check_fails(self):
        from scraper.run_health_check import run_health_check
        ok   = {"ok": True,  "message": "ok"}
        warn = {"ok": False, "message": "too many skipped filings"}
        with patch("scraper.run_health_check.check_db", return_value=ok), \
             patch("scraper.run_health_check.check_recent_activity", return_value=ok), \
             patch("scraper.run_health_check.check_failed_filings", return_value=warn), \
             patch("scraper.run_health_check.check_review_queue", return_value=ok):
            result = run_health_check("u", "k")
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["exit_code"], 1)


# ─── 6. Operations report structure ───────────────────────────────────────────

class TestOpsReportStructure(unittest.TestCase):
    def _mock_ops_calls(self):
        """Patch both _count and _select used by build_report."""
        import scraper.generate_daily_operations_report as mod
        count_patch  = patch.object(mod, "_count",  return_value=0)
        select_patch = patch.object(mod, "_select", return_value=[])
        return count_patch, select_patch

    def test_build_report_returns_required_keys(self):
        from scraper.generate_daily_operations_report import build_report
        import scraper.generate_daily_operations_report as mod
        with patch.object(mod, "_count", return_value=0), \
             patch.object(mod, "_select", return_value=[]):
            report = build_report("https://example.supabase.co", "fake-key")

        for key in ("generated_at", "filings", "latency_last_7d", "transactions", "review_queue"):
            self.assertIn(key, report)

    def test_build_report_latency_stats_structure(self):
        from scraper.generate_daily_operations_report import build_report
        import scraper.generate_daily_operations_report as mod
        with patch.object(mod, "_count", return_value=5), \
             patch.object(mod, "_select", return_value=[]):
            report = build_report("u", "k")

        lat = report["latency_last_7d"]
        for section in ("end_to_end_seconds", "scraper_seconds", "download_seconds", "parse_seconds"):
            self.assertIn(section, lat)
            self.assertIn("n", lat[section])

    def test_latency_percentile_calculation(self):
        from scraper.generate_daily_operations_report import _percentile, _latency_stats
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(_percentile(vals, 50), 30.0)
        stats = _latency_stats(vals)
        self.assertEqual(stats["n"], 5)
        self.assertEqual(stats["min"], 10.0)
        self.assertEqual(stats["max"], 50.0)

    def test_latency_stats_empty_returns_none_fields(self):
        from scraper.generate_daily_operations_report import _latency_stats
        stats = _latency_stats([])
        self.assertEqual(stats["n"], 0)
        self.assertIsNone(stats["avg"])
        self.assertIsNone(stats["p95"])


if __name__ == "__main__":
    unittest.main()
