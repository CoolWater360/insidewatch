"""
Concurrency and stale-claim tests — Phase 2 revision.

Verifies:
  1. Two workers attempting to claim the same pending filing: exactly one succeeds.
  2. A stale in_progress filing is recovered to 'failed' by the reaper.
  3. A non-stale in_progress filing is not reclaimed via claim_filing.
  4. A recovered filing with attempt_count >= max_attempts goes to 'skipped', not 'failed'.
  5. Failed retry logic still works after stale recovery.

Run with:
    python3 -m unittest tests.test_concurrency_and_stale -v
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

from scraper import filings as fl


# ── Mock builder helpers ──────────────────────────────────────────────────────

def _mock_client():
    return MagicMock()


def _rpc_response(data):
    """Simulate supabase-py response from client.rpc(...).execute()."""
    r = MagicMock()
    r.data = data
    return r


def _wire_rpc(client, data):
    """Make client.rpc(*).execute() return data."""
    client.rpc.return_value.execute.return_value = _rpc_response(data)


def _wire_rpc_sequence(client, data_sequence):
    """Make successive client.rpc(*).execute() calls return items from data_sequence."""
    client.rpc.return_value.execute.side_effect = [
        _rpc_response(d) for d in data_sequence
    ]


def _select_response(data):
    r = MagicMock()
    r.data = data
    return r


def _wire_select(client, data):
    """Make client.table(*).select(*).eq(*).lte(*).execute() return data."""
    (client.table.return_value
           .select.return_value
           .eq.return_value
           .lte.return_value
           .execute.return_value) = _select_response(data)


def _wire_update(client, data=None):
    """
    Configure client.table(*).update(*).eq(*)[…].execute() → data.
    Uses the 'return self' trick on .eq() for any chain length.
    Default data is non-empty so lease-lost checks pass by default.
    """
    if data is None:
        data = [{"id": 999}]
    resp = MagicMock()
    resp.data = data
    update_mock = client.table.return_value.update.return_value
    update_mock.eq.return_value = update_mock
    update_mock.execute.return_value = resp
    return resp


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _future_iso(minutes=30):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


# ── Test cases ────────────────────────────────────────────────────────────────

class TestAtomicClaim(unittest.TestCase):

    # ── 1. Dual-claim: exactly one worker wins ────────────────────────────────

    def test_dual_claim_exactly_one_succeeds(self):
        """
        When two workers attempt to claim the same pending filing, the Postgres
        RPC guarantees only one UPDATE matches. The Python layer must:
          · return the row to the winner (non-None)
          · return None to the loser
        so that only the winner proceeds with download/parse.
        """
        claimed_row = {
            "id": 1, "pdf_url": "https://example.com/a.pdf",
            "status": "in_progress",
            "attempt_count": 1, "max_attempts": 3,
            "next_attempt_after": None,
        }

        # Worker 1: DB returns the row (claim succeeded)
        client_w1 = _mock_client()
        _wire_rpc(client_w1, [claimed_row])

        # Worker 2: DB returns empty (row already claimed by worker 1)
        client_w2 = _mock_client()
        _wire_rpc(client_w2, [])

        result_w1 = fl.claim_filing(client_w1, filing_id=1)
        result_w2 = fl.claim_filing(client_w2, filing_id=1)

        self.assertIsNotNone(result_w1, "Worker 1 should win the claim")
        self.assertIsNone(result_w2, "Worker 2 should lose the race")

    def test_winner_receives_db_attempt_count(self):
        """
        The returned row's attempt_count reflects the DB post-increment value.
        Callers must use this, not a locally-tracked counter.
        """
        claimed_row = {
            "id": 2, "pdf_url": "https://example.com/b.pdf",
            "status": "in_progress",
            "attempt_count": 2,   # DB incremented from 1 → 2
            "max_attempts": 3,
        }
        client = _mock_client()
        _wire_rpc(client, [claimed_row])

        result = fl.claim_filing(client, filing_id=2)

        self.assertIsNotNone(result)
        self.assertEqual(result["attempt_count"], 2,
            "Caller must use the DB's post-increment attempt_count")

    def test_claim_passes_filing_id_and_stale_minutes_to_rpc(self):
        """claim_filing must call the claim_filing RPC with the correct parameters."""
        client = _mock_client()
        _wire_rpc(client, [])  # result irrelevant

        fl.claim_filing(client, filing_id=42)

        client.rpc.assert_called_once()
        rpc_name, rpc_params = client.rpc.call_args[0]
        self.assertEqual(rpc_name, "claim_filing")
        self.assertEqual(rpc_params["p_filing_id"], 42)
        self.assertIn("p_stale_minutes", rpc_params)
        self.assertIsInstance(rpc_params["p_stale_minutes"], int)


class TestStaleReaper(unittest.TestCase):

    # ── 2. Stale in_progress becomes retryable ────────────────────────────────

    def test_stale_in_progress_reaped_to_failed(self):
        """
        A filing in_progress for longer than the stale threshold must be
        transitioned to 'failed' with a future next_attempt_after.
        """
        reaped_row = {
            "id": 5, "pdf_url": "https://example.com/stale.pdf",
            "status": "failed",
            "attempt_count": 1, "max_attempts": 3,
            "next_attempt_after": _future_iso(minutes=2),
            "last_error": "Stale in_progress: no completion signal for 30 min",
        }
        client = _mock_client()
        _wire_rpc(client, [reaped_row])

        reaped = fl.reap_stale_filings(client, stale_minutes=30)

        self.assertEqual(len(reaped), 1)
        self.assertEqual(reaped[0]["status"], "failed")
        self.assertIsNotNone(reaped[0]["next_attempt_after"],
            "Reaped filing must have a future retry window")
        self.assertIn("Stale in_progress", reaped[0]["last_error"])

    def test_reaper_calls_rpc_with_stale_minutes(self):
        """reap_stale_filings must call the reap_stale_filings RPC."""
        client = _mock_client()
        _wire_rpc(client, [])

        fl.reap_stale_filings(client, stale_minutes=45)

        client.rpc.assert_called_once()
        rpc_name, rpc_params = client.rpc.call_args[0]
        self.assertEqual(rpc_name, "reap_stale_filings")
        self.assertEqual(rpc_params["p_stale_minutes"], 45)

    def test_reaper_returns_empty_when_nothing_stale(self):
        """reap_stale_filings must return [] when no filings are stale."""
        client = _mock_client()
        _wire_rpc(client, [])

        reaped = fl.reap_stale_filings(client, stale_minutes=30)
        self.assertEqual(reaped, [])

    # ── 3. Non-stale in_progress filing is not reclaimed ─────────────────────

    def test_non_stale_in_progress_not_reclaimed(self):
        """
        A filing in_progress for less than the stale threshold must not be
        reclaimed. The RPC WHERE clause filters it out; the Python layer
        receives 0 rows and returns None.
        """
        client = _mock_client()
        _wire_rpc(client, [])  # DB returns empty: not stale, not claimable

        result = fl.claim_filing(client, filing_id=6)

        self.assertIsNone(result,
            "Non-stale in_progress filing must not be reclaimed")

    # ── 4. Recovered filing at max_attempts goes to skipped ───────────────────

    def test_recovered_at_max_attempts_becomes_skipped(self):
        """
        When a stale in_progress filing has attempt_count >= max_attempts, the
        reaper must transition it to 'skipped' (not 'failed'), with no retry.
        """
        reaped_row = {
            "id": 7, "pdf_url": "https://example.com/terminal.pdf",
            "status": "skipped",    # ← DB chose skipped because attempt_count >= max_attempts
            "attempt_count": 3, "max_attempts": 3,
            "next_attempt_after": None,
            "last_error": "Stale in_progress: no completion signal for 30 min (attempt 3 of 3)",
        }
        client = _mock_client()
        _wire_rpc(client, [reaped_row])

        reaped = fl.reap_stale_filings(client, stale_minutes=30)

        self.assertEqual(len(reaped), 1)
        self.assertEqual(reaped[0]["status"], "skipped",
            "Filing at max_attempts must be permanently skipped, not retried")
        self.assertIsNone(reaped[0]["next_attempt_after"],
            "Permanently skipped filing must have no retry window")

    def test_skipped_filing_not_eligible_after_reap(self):
        """A filing transitioned to 'skipped' by the reaper is not eligible."""
        skipped = {
            "id": 7, "status": "skipped",
            "attempt_count": 3, "max_attempts": 3,
            "next_attempt_after": None,
        }
        self.assertFalse(fl.is_eligible(skipped))

    # ── 5. Retry still works after stale recovery ─────────────────────────────

    def test_retry_works_after_stale_recovery(self):
        """
        After the reaper transitions a stale filing to 'failed', a subsequent
        claim_filing call must be able to reclaim it once the backoff elapses.
        """
        # Filing is now in 'failed' state with elapsed backoff (post-reap)
        reclaimed_row = {
            "id": 8, "pdf_url": "https://example.com/recovered.pdf",
            "status": "in_progress",
            "attempt_count": 2,  # DB incremented: was 1, now 2
            "max_attempts": 3,
            "next_attempt_after": None,
        }
        client = _mock_client()
        _wire_rpc(client, [reclaimed_row])

        result = fl.claim_filing(client, filing_id=8)

        self.assertIsNotNone(result,
            "Recovered (failed) filing must be claimable after backoff")
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["attempt_count"], 2,
            "Attempt count must reflect the DB's cumulative total")

    def test_recovered_filing_respects_max_attempts_on_next_fail(self):
        """
        A filing that was reaped (attempt_count=2, max_attempts=3) and then
        fails again on the retry: fail_filing with attempt_count=2, max_attempts=3
        → 2 < 3 → still 'failed' (not yet permanently skipped).
        Then on attempt_count=3 it transitions to 'skipped'.
        """
        client = _mock_client()
        _wire_update(client)  # non-empty data so lease checks pass

        # Attempt 2 (of 3) fails → should remain 'failed'
        fl.fail_filing(
            client, filing_id=8,
            error="timeout", attempt_count=2, max_attempts=3,
            claim_token="tok-8",
        )
        payload_2 = client.table.return_value.update.call_args[0][0]
        self.assertEqual(payload_2["status"], "failed",
            "Attempt 2 of 3 must remain in 'failed' for retry")
        self.assertIsNotNone(payload_2["next_attempt_after"])

        # Attempt 3 (of 3) fails → must be permanently skipped
        fl.fail_filing(
            client, filing_id=8,
            error="timeout again", attempt_count=3, max_attempts=3,
            claim_token="tok-8",
        )
        payload_3 = client.table.return_value.update.call_args[0][0]
        self.assertEqual(payload_3["status"], "skipped",
            "Attempt 3 of 3 must permanently skip")
        self.assertIsNone(payload_3["next_attempt_after"])


class TestClaimLeaseToken(unittest.TestCase):
    """
    Lease-token tests.

    Scenario exercised:
      1. Worker A claims a filing → receives claim_token_A
      2. The filing becomes stale; the reaper clears claim_token_A and sets
         status = 'failed'
      3. Worker B claims the filing → receives claim_token_B
      4. Worker A attempts complete_filing / fail_filing with claim_token_A
         → the UPDATE WHERE clause finds no row (token mismatch) → rejected
      5. Worker B completes the filing with claim_token_B → succeeds
    """

    TOKEN_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    TOKEN_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    FILING_ID = 100
    URL = "https://example.com/contested.pdf"

    # ── complete_filing ───────────────────────────────────────────────────────

    def test_worker_a_complete_rejected_after_lease_lost(self):
        """
        Worker A's complete_filing is rejected when its token no longer matches.
        """
        client_a = _mock_client()
        _wire_update(client_a, data=[])   # DB returns 0 rows: token mismatch

        ok = fl.complete_filing(
            client_a, filing_id=self.FILING_ID,
            tx_inserted=3, tx_dedup=0, pdf_sha256="sha-a",
            claim_token=self.TOKEN_A,
        )
        self.assertFalse(ok, "Worker A's stale complete_filing must be rejected")

    def test_worker_b_complete_succeeds_with_live_token(self):
        """
        Worker B's complete_filing succeeds with its current valid token.
        """
        client_b = _mock_client()
        _wire_update(client_b, data=[{"id": self.FILING_ID, "status": "completed"}])

        ok = fl.complete_filing(
            client_b, filing_id=self.FILING_ID,
            tx_inserted=3, tx_dedup=0, pdf_sha256="sha-b",
            claim_token=self.TOKEN_B,
        )
        self.assertTrue(ok, "Worker B's complete_filing must succeed")

    def test_complete_clears_claim_token_on_success(self):
        """
        complete_filing must set claim_token=None in the UPDATE payload so the
        token cannot be replayed after a filing is completed.
        """
        client = _mock_client()
        _wire_update(client)

        fl.complete_filing(
            client, filing_id=self.FILING_ID,
            tx_inserted=1, tx_dedup=0, pdf_sha256="sha",
            claim_token=self.TOKEN_B,
        )

        payload = client.table.return_value.update.call_args[0][0]
        self.assertIsNone(payload["claim_token"],
            "claim_token must be cleared (set to None) on completion")

    # ── fail_filing ───────────────────────────────────────────────────────────

    def test_worker_a_fail_rejected_after_lease_lost(self):
        """
        Worker A's fail_filing is rejected when its token no longer matches.
        """
        client_a = _mock_client()
        _wire_update(client_a, data=[])   # token mismatch

        ok = fl.fail_filing(
            client_a, filing_id=self.FILING_ID,
            error="timeout", attempt_count=1, max_attempts=3,
            claim_token=self.TOKEN_A,
        )
        self.assertFalse(ok, "Worker A's stale fail_filing must be rejected")

    def test_worker_b_fail_succeeds_with_live_token(self):
        """
        Worker B's fail_filing succeeds — its token is still current.
        """
        client_b = _mock_client()
        _wire_update(client_b, data=[{"id": self.FILING_ID, "status": "failed"}])

        ok = fl.fail_filing(
            client_b, filing_id=self.FILING_ID,
            error="connection reset", attempt_count=1, max_attempts=3,
            claim_token=self.TOKEN_B,
        )
        self.assertTrue(ok, "Worker B's fail_filing must succeed")

    def test_fail_clears_claim_token(self):
        """fail_filing must clear claim_token in the UPDATE payload."""
        client = _mock_client()
        _wire_update(client)

        fl.fail_filing(
            client, filing_id=self.FILING_ID,
            error="err", attempt_count=1, max_attempts=3,
            claim_token=self.TOKEN_B,
        )

        payload = client.table.return_value.update.call_args[0][0]
        self.assertIsNone(payload["claim_token"],
            "claim_token must be cleared on fail_filing")

    # ── skip_filing ───────────────────────────────────────────────────────────

    def test_worker_a_skip_rejected_after_lease_lost(self):
        """Worker A's skip_filing is rejected after it loses the lease."""
        client_a = _mock_client()
        _wire_update(client_a, data=[])

        ok = fl.skip_filing(
            client_a, filing_id=self.FILING_ID,
            reason="no transactions",
            claim_token=self.TOKEN_A,
        )
        self.assertFalse(ok)

    def test_worker_b_skip_succeeds(self):
        """Worker B's skip_filing succeeds with its live token."""
        client_b = _mock_client()
        _wire_update(client_b, data=[{"id": self.FILING_ID, "status": "skipped"}])

        ok = fl.skip_filing(
            client_b, filing_id=self.FILING_ID,
            reason="no transactions",
            claim_token=self.TOKEN_B,
        )
        self.assertTrue(ok)

    # ── Full scenario ─────────────────────────────────────────────────────────

    def test_only_token_holder_can_write_filing_state(self):
        """
        End-to-end scenario:
          · Worker A claims → TOKEN_A
          · Reaper clears TOKEN_A (simulated)
          · Worker B claims → TOKEN_B
          · Worker A complete → rejected
          · Worker B complete → succeeds
          · Only one completion occurs
        """
        # Worker A claims
        client_a = _mock_client()
        _wire_rpc(client_a, [{"id": self.FILING_ID, "status": "in_progress",
                               "attempt_count": 1, "max_attempts": 3,
                               "claim_token": self.TOKEN_A}])
        claim_a = fl.claim_filing(client_a, filing_id=self.FILING_ID)
        self.assertIsNotNone(claim_a)
        self.assertEqual(claim_a["claim_token"], self.TOKEN_A)

        # Worker B claims (reaper has already cleared A's token; B wins)
        client_b = _mock_client()
        _wire_rpc(client_b, [{"id": self.FILING_ID, "status": "in_progress",
                               "attempt_count": 2, "max_attempts": 3,
                               "claim_token": self.TOKEN_B}])
        claim_b = fl.claim_filing(client_b, filing_id=self.FILING_ID)
        self.assertIsNotNone(claim_b)
        self.assertEqual(claim_b["claim_token"], self.TOKEN_B)

        # Worker A attempts complete with its now-stale token → rejected
        _wire_update(client_a, data=[])
        ok_a = fl.complete_filing(
            client_a, filing_id=self.FILING_ID,
            tx_inserted=5, tx_dedup=0, pdf_sha256="sha-a",
            claim_token=self.TOKEN_A,
        )
        self.assertFalse(ok_a, "Worker A's stale complete must be rejected")

        # Worker B completes with its live token → succeeds
        _wire_update(client_b, data=[{"id": self.FILING_ID, "status": "completed"}])
        ok_b = fl.complete_filing(
            client_b, filing_id=self.FILING_ID,
            tx_inserted=5, tx_dedup=0, pdf_sha256="sha-b",
            claim_token=self.TOKEN_B,
        )
        self.assertTrue(ok_b, "Worker B's live complete must succeed")


if __name__ == "__main__":
    unittest.main()
