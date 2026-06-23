"""
Startup-safety tests — Phase 2 revision.

Verifies that the listener and sweep refuse to run when the filings ledger
is absent (production-safe default) and that legacy mode is permitted only
when ALLOW_LEGACY_INGESTION is explicitly set.

Run with:
    python3 -m pytest tests/test_startup_behavior.py -v
or:
    python3 -m unittest tests.test_startup_behavior -v
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from scraper import listener as _listener_mod
from scraper import run_phase2 as _sweep_mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_client():
    return MagicMock()


def _mock_session():
    return MagicMock()


# ── Listener tests ────────────────────────────────────────────────────────────

class TestListenerStartupBehavior(unittest.TestCase):

    # ── 1. Production: no ledger, no env var → hard fail ─────────────────────

    @patch.dict("os.environ", {}, clear=False)
    @patch("scraper.listener.filing_ledger.table_exists", return_value=False)
    @patch("scraper.listener.get_supabase_client")
    @patch("scraper.listener._make_session")
    def test_production_fail_when_ledger_absent(self, mock_session, mock_client, mock_exists):
        """
        When the filings table is absent and ALLOW_LEGACY_INGESTION is not set,
        run_listener() must return stats with errors > 0 without running any
        ingestion logic.
        """
        # Ensure the env var is absent
        import os
        os.environ.pop("ALLOW_LEGACY_INGESTION", None)

        mock_client.return_value = _mock_client()
        mock_session.return_value = _mock_session()

        stats = _listener_mod.run_listener()

        self.assertGreater(stats["errors"], 0,
            "errors must be > 0 so the GitHub Actions job exits non-zero")
        self.assertEqual(stats["new"], 0,
            "no new transactions must be ingested")
        self.assertEqual(stats["retried"], 0)

    @patch.dict("os.environ", {}, clear=False)
    @patch("scraper.listener.filing_ledger.table_exists", return_value=False)
    @patch("scraper.listener.get_supabase_client")
    @patch("scraper.listener._make_session")
    def test_production_fail_does_not_call_cache_path(self, mock_session, mock_client, mock_exists):
        """
        The legacy _run_with_cache function must never be called in production
        when the ledger is absent and ALLOW_LEGACY_INGESTION is not set.
        """
        import os
        os.environ.pop("ALLOW_LEGACY_INGESTION", None)

        mock_client.return_value = _mock_client()
        mock_session.return_value = _mock_session()

        with patch.object(_listener_mod, "_run_with_cache") as mock_cache:
            _listener_mod.run_listener()
            mock_cache.assert_not_called()

    # ── 2. Legacy mode: ALLOW_LEGACY_INGESTION=true → calls _run_with_cache ──

    @patch.dict("os.environ", {"ALLOW_LEGACY_INGESTION": "true"})
    @patch("scraper.listener.filing_ledger.table_exists", return_value=False)
    @patch("scraper.listener.get_supabase_client")
    @patch("scraper.listener._make_session")
    def test_legacy_mode_proceeds_with_env_var(self, mock_session, mock_client, mock_exists):
        """
        When ALLOW_LEGACY_INGESTION=true and the filings table is absent,
        run_listener() must call _run_with_cache (not fail).
        """
        mock_client.return_value = _mock_client()
        mock_session.return_value = _mock_session()

        sentinel_stats = {"new": 7, "skipped": 0, "errors": 0, "retried": 0}
        with patch.object(_listener_mod, "_run_with_cache", return_value=sentinel_stats) as mock_cache:
            stats = _listener_mod.run_listener()
            mock_cache.assert_called_once()

        self.assertEqual(stats["new"], 7,
            "stats from _run_with_cache must be returned unchanged")

    @patch.dict("os.environ", {"ALLOW_LEGACY_INGESTION": "1"})
    @patch("scraper.listener.filing_ledger.table_exists", return_value=False)
    @patch("scraper.listener.get_supabase_client")
    @patch("scraper.listener._make_session")
    def test_legacy_mode_accepts_numeric_flag(self, mock_session, mock_client, mock_exists):
        """ALLOW_LEGACY_INGESTION=1 must also activate legacy mode."""
        mock_client.return_value = _mock_client()
        mock_session.return_value = _mock_session()

        with patch.object(_listener_mod, "_run_with_cache", return_value={}) as mock_cache:
            _listener_mod.run_listener()
            mock_cache.assert_called_once()

    @patch.dict("os.environ", {"ALLOW_LEGACY_INGESTION": "false"})
    @patch("scraper.listener.filing_ledger.table_exists", return_value=False)
    @patch("scraper.listener.get_supabase_client")
    @patch("scraper.listener._make_session")
    def test_legacy_mode_does_not_activate_on_false(self, mock_session, mock_client, mock_exists):
        """ALLOW_LEGACY_INGESTION=false must be treated as absent (hard fail)."""
        mock_client.return_value = _mock_client()
        mock_session.return_value = _mock_session()

        with patch.object(_listener_mod, "_run_with_cache") as mock_cache:
            stats = _listener_mod.run_listener()
            mock_cache.assert_not_called()

        self.assertGreater(stats["errors"], 0)

    # ── 3. Ledger present → normal path, no env var check needed ─────────────

    @patch.dict("os.environ", {}, clear=False)
    @patch("scraper.listener.filing_ledger.table_exists", return_value=True)
    @patch("scraper.listener.get_supabase_client")
    @patch("scraper.listener._make_session")
    def test_ledger_present_uses_ledger_path(self, mock_session, mock_client, mock_exists):
        """When the filings table exists, _run_with_ledger is called regardless of env vars."""
        import os
        os.environ.pop("ALLOW_LEGACY_INGESTION", None)

        mock_client.return_value = _mock_client()
        mock_session.return_value = _mock_session()

        sentinel = {"new": 3, "skipped": 1, "errors": 0, "retried": 0}
        with patch.object(_listener_mod, "_run_with_ledger", return_value=sentinel) as mock_ledger:
            with patch.object(_listener_mod, "_run_with_cache") as mock_cache:
                stats = _listener_mod.run_listener()
                mock_ledger.assert_called_once()
                mock_cache.assert_not_called()

        self.assertEqual(stats["new"], 3)


# ── Sweep (run_phase2) tests ──────────────────────────────────────────────────

class TestSweepStartupBehavior(unittest.TestCase):

    # ── 4. run_crawl exits when ledger absent and env var not set ─────────────

    @patch.dict("os.environ", {}, clear=False)
    @patch("scraper.run_phase2.filing_ledger.table_exists", return_value=False)
    @patch("scraper.run_phase2.get_supabase_client")
    def test_run_crawl_exits_when_ledger_absent(self, mock_client, mock_exists):
        """
        run_crawl() must call sys.exit(1) when the filings table is absent
        and ALLOW_LEGACY_INGESTION is not set.
        """
        import os
        os.environ.pop("ALLOW_LEGACY_INGESTION", None)

        mock_client.return_value = _mock_client()

        with self.assertRaises(SystemExit) as ctx:
            _sweep_mod.run_crawl(limit=1)

        self.assertEqual(ctx.exception.code, 1)

    @patch.dict("os.environ", {"ALLOW_LEGACY_INGESTION": "true"})
    @patch("scraper.run_phase2.filing_ledger.table_exists", return_value=False)
    @patch("scraper.run_phase2.get_supabase_client")
    def test_run_crawl_proceeds_with_legacy_env_var(self, mock_client, mock_exists):
        """
        run_crawl() must NOT exit when ALLOW_LEGACY_INGESTION=true, even
        without the filings table.  It continues with use_ledger=False.
        """
        client = _mock_client()
        mock_client.return_value = client

        # Stub out the parts that follow the ledger check so we don't need
        # a real DB or seed file.
        with patch("scraper.run_phase2.load_seed_companies", return_value={"inserted": 0, "skipped": 0}):
            client.table.return_value.select.return_value.lte.return_value \
                  .order.return_value.order.return_value.execute.return_value \
                  = MagicMock(data=[])  # no companies → runs no crawl

            # Should not raise SystemExit
            try:
                _sweep_mod.run_crawl(limit=0)
            except SystemExit as exc:
                self.fail(f"run_crawl raised SystemExit({exc.code}) unexpectedly")


if __name__ == "__main__":
    unittest.main()
