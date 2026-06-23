"""
Filing lifecycle tests — Phase 2.

Tests the seven core lifecycle scenarios of the filings ledger without
requiring a real database connection.  All Supabase client calls are mocked.

Run with:
    python3 -m pytest tests/test_filing_lifecycle.py -v
or:
    python3 -m unittest tests.test_filing_lifecycle -v
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

from scraper import filings as fl


# ── Mock builder helpers ──────────────────────────────────────────────────────

def _mock_client():
    """Return a fresh MagicMock that mimics a Supabase client."""
    return MagicMock()


def _make_response(data=None, count=None):
    """Simulate a supabase-py APIResponse."""
    r = MagicMock()
    r.data  = data or []
    r.count = count
    return r


def _wire_select(client, table: str, data: list):
    """Configure client.table(table).select(*).eq(*).execute() → data."""
    resp = _make_response(data)
    (client.table.return_value
           .select.return_value
           .eq.return_value
           .execute.return_value) = resp
    return resp


def _wire_insert(client, returned_row: dict):
    """Configure client.table(*).insert(*).execute() → [returned_row]."""
    resp = _make_response([returned_row])
    (client.table.return_value
           .insert.return_value
           .execute.return_value) = resp
    return resp


def _wire_update(client, data=None):
    """
    Configure client.table(*).update(*).eq(*)[…].execute() → data.

    Uses the 'return self' trick on .eq() so any number of chained .eq()
    calls (e.g. .eq("id", x).eq("claim_token", t).eq("status", s)) all
    resolve to the same mock and return the configured execute() result.

    Default data is [{"id": 999}] — non-empty so that the lease-lost check
    (if not result.data: …) does not fire unless deliberately set to [].
    """
    if data is None:
        data = [{"id": 999}]
    resp = _make_response(data)
    update_mock = client.table.return_value.update.return_value
    update_mock.eq.return_value = update_mock   # chain .eq() on itself
    update_mock.execute.return_value = resp
    return resp


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _past_iso(minutes=90):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _future_iso(minutes=30):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


# ── Test cases ────────────────────────────────────────────────────────────────

class TestFilingLifecycle(unittest.TestCase):

    # ── 1. New filing — happy path ────────────────────────────────────────────

    def test_1_new_filing_registers_and_is_eligible(self):
        """
        A URL not yet in the ledger is registered as 'pending' and is
        immediately eligible for processing.
        """
        client = _mock_client()

        # No existing row
        _wire_select(client, "filings", [])

        new_row = {
            "id": 1, "pdf_url": "https://example.com/a.pdf",
            "status": "pending", "attempt_count": 0, "max_attempts": 3,
            "next_attempt_after": None,
        }
        _wire_insert(client, new_row)

        filing = fl.register_filing(
            client,
            pdf_url="https://example.com/a.pdf",
            filing_date="2024-01-15",
            company_name="Acme SpA",
        )

        self.assertEqual(filing["status"], "pending")
        self.assertTrue(fl.is_eligible(filing))

    def test_1b_complete_filing_stores_stats(self):
        """
        After successful processing, complete_filing sets status=completed
        and records the correct transaction counts.
        """
        client = _mock_client()
        _wire_update(client)

        ok = fl.complete_filing(
            client, filing_id=1,
            tx_inserted=3, tx_dedup=0,
            pdf_sha256="abc123",
            claim_token="tok-1",
        )

        self.assertTrue(ok)
        # Confirm update was called with completed status
        update_call = client.table.return_value.update
        payload = update_call.call_args[0][0]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["transactions_inserted"], 3)
        self.assertEqual(payload["pdf_sha256"], "abc123")
        self.assertIsNone(payload["claim_token"])

    # ── 2. Download failure → failed with backoff ─────────────────────────────

    def test_2_download_failure_marks_failed_with_backoff(self):
        """
        When a download error occurs, fail_filing sets status=failed and
        next_attempt_after to a future timestamp.
        """
        client = _mock_client()
        _wire_update(client)

        fl.fail_filing(
            client, filing_id=2,
            error="Connection timeout",
            attempt_count=1,
            max_attempts=3,
            claim_token="tok-2",
        )

        payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(payload["status"], "failed")
        self.assertIsNotNone(payload["next_attempt_after"])
        self.assertIn("Connection timeout", payload["last_error"])

        # next_attempt_after must be in the future
        naa = datetime.fromisoformat(payload["next_attempt_after"])
        if naa.tzinfo is None:
            naa = naa.replace(tzinfo=timezone.utc)
        self.assertGreater(naa, datetime.now(timezone.utc))

    def test_2b_backoff_grows_exponentially(self):
        """
        Backoff delay doubles with each attempt: attempt 0→1min, 1→2min, 2→4min.
        """
        client = _mock_client()

        delays = []
        for attempt in range(4):
            _wire_update(client)
            before = datetime.now(timezone.utc)
            fl.fail_filing(
                client, filing_id=99,
                error="err", attempt_count=attempt, max_attempts=10,
                claim_token="tok-99",
            )
            payload = client.table.return_value.update.call_args[0][0]
            naa = datetime.fromisoformat(payload["next_attempt_after"])
            if naa.tzinfo is None:
                naa = naa.replace(tzinfo=timezone.utc)
            delays.append((naa - before).total_seconds())

        # Each delay should be roughly double the previous (allow 5 s slack)
        for i in range(1, len(delays)):
            self.assertGreater(delays[i], delays[i - 1] * 1.5,
                               msg=f"delay[{i}]={delays[i]:.0f}s should be > 1.5× delay[{i-1}]={delays[i-1]:.0f}s")

    # ── 3. Empty PDF → skipped ────────────────────────────────────────────────

    def test_3_empty_pdf_marks_skipped(self):
        """
        When parse_pdf returns no transactions, skip_filing sets status=skipped.
        """
        client = _mock_client()
        _wire_update(client)

        ok = fl.skip_filing(
            client, filing_id=3,
            reason="no transactions parsed from PDF",
            claim_token="tok-3",
        )

        self.assertTrue(ok)
        payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(payload["status"], "skipped")
        self.assertIn("no transactions", payload["last_error"])
        self.assertIsNone(payload["claim_token"])

    # ── 4. Failed filing retried successfully after backoff ───────────────────

    def test_4_failed_filing_eligible_after_backoff_elapsed(self):
        """
        A failed filing with next_attempt_after in the past is eligible for retry.
        """
        filing = {
            "id": 4, "status": "failed",
            "attempt_count": 1, "max_attempts": 3,
            "next_attempt_after": _past_iso(minutes=90),  # 90 minutes ago
        }
        self.assertTrue(fl.is_eligible(filing))

    def test_4b_successful_retry_marks_completed(self):
        """After a successful retry, complete_filing marks the filing completed."""
        client = _mock_client()
        _wire_update(client)

        ok = fl.complete_filing(
            client, filing_id=4,
            tx_inserted=2, tx_dedup=1,
            pdf_sha256="def456",
            claim_token="tok-4",
        )

        self.assertTrue(ok)
        payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["transactions_inserted"], 2)

    # ── 5. Max attempts reached — permanently skipped ─────────────────────────

    def test_5_max_attempts_reached_marks_skipped(self):
        """
        When attempt_count >= max_attempts, fail_filing escalates status to
        'skipped' rather than 'failed', and sets no next_attempt_after.
        """
        client = _mock_client()
        _wire_update(client)

        fl.fail_filing(
            client, filing_id=5,
            error="blocked by CAPTCHA",
            attempt_count=3,   # == max_attempts
            max_attempts=3,
            claim_token="tok-5",
        )

        payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(payload["status"], "skipped")
        self.assertIsNone(payload["next_attempt_after"])

    def test_5b_skipped_filing_not_eligible(self):
        """A skipped filing is never eligible for processing."""
        filing = {
            "id": 5, "status": "skipped",
            "attempt_count": 3, "max_attempts": 3,
            "next_attempt_after": None,
        }
        self.assertFalse(fl.is_eligible(filing))

    # ── 6. In-progress filing not re-claimed ──────────────────────────────────

    def test_6_in_progress_filing_not_eligible(self):
        """
        A filing already marked in_progress (e.g. from a crashed concurrent
        run) is not eligible and will not be claimed again.
        """
        filing = {
            "id": 6, "status": "in_progress",
            "attempt_count": 1, "max_attempts": 3,
            "next_attempt_after": None,
        }
        self.assertFalse(fl.is_eligible(filing))

    # ── 7. Completed filing not reprocessed when seen again ───────────────────

    def test_7_completed_filing_already_exists_on_register(self):
        """
        When register_filing finds an existing completed row, it returns it
        without inserting a new row.  The caller checks status==completed
        and skips processing.
        """
        client = _mock_client()

        existing_row = {
            "id": 7, "pdf_url": "https://example.com/b.pdf",
            "status": "completed", "attempt_count": 1, "max_attempts": 3,
            "next_attempt_after": None, "transactions_inserted": 5,
        }
        # SELECT returns the completed row
        _wire_select(client, "filings", [existing_row])

        filing = fl.register_filing(
            client,
            pdf_url="https://example.com/b.pdf",
            filing_date="2024-01-10",
            company_name="Beta SpA",
        )

        # Must return existing row without INSERT
        self.assertEqual(filing["status"], "completed")
        self.assertEqual(filing["id"], 7)
        self.assertFalse(fl.is_eligible(filing))
        # INSERT must not have been called
        client.table.return_value.insert.assert_not_called()

    # ── Backoff window not yet elapsed ────────────────────────────────────────

    def test_failed_filing_not_eligible_before_backoff_window(self):
        """A failed filing whose next_attempt_after is still in the future is not eligible."""
        filing = {
            "id": 8, "status": "failed",
            "attempt_count": 1, "max_attempts": 3,
            "next_attempt_after": _future_iso(minutes=30),  # 30 minutes from now
        }
        self.assertFalse(fl.is_eligible(filing))

    # ── reset_for_retry sets attempt_count to 0 ───────────────────────────────

    def test_reset_for_retry_zeroes_attempt_count(self):
        """reset_for_retry resets status to pending and clears attempt_count."""
        client = _mock_client()

        existing_row = {
            "id": 9, "pdf_url": "https://example.com/c.pdf",
            "status": "failed", "attempt_count": 2,
        }
        # inspect() does a select by id
        _wire_select(client, "filings", [existing_row])
        _wire_update(client)

        ok = fl.reset_for_retry(client, "9")
        self.assertTrue(ok)

        payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["attempt_count"], 0)
        self.assertIsNone(payload["next_attempt_after"])


if __name__ == "__main__":
    unittest.main()
