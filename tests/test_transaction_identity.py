"""
Transaction identity and versioning tests — Phase 4.

Tests the Phase 4 redesign of transaction deduplication:
  · Old raw_hash: SHA256(name|company|date|qty|price) — caused same-day
    same-price transactions to be silently suppressed.
  · New identity_hash: SHA256(filing_id|pdf_sha256|tx_index|isin|direction)
    — transactions are distinct by position within their source filing.

Also tests the version history, operator correction, and supersede paths.

Run with:
    python3 -m unittest tests.test_transaction_identity -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.db import (
    compute_identity_hash,
    correct_transaction,
    get_transaction_history,
    supersede_transaction,
    upsert_transaction,
    _derive_identity_hash,
    _has_changed,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_client():
    return MagicMock()


def _make_response(data=None):
    r = MagicMock()
    r.data = data or []
    return r


def _wire_select(client, data: list):
    """Wire client.table(*).select(*).eq(*).execute() → data."""
    resp = _make_response(data)
    chain = client.table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.execute.return_value = resp
    return resp


def _wire_insert(client, returned_row: dict):
    resp = _make_response([returned_row])
    client.table.return_value.insert.return_value.execute.return_value = resp
    return resp


def _wire_update(client, data=None):
    resp = _make_response(data or [{"id": 1}])
    update_mock = client.table.return_value.update.return_value
    update_mock.eq.return_value = update_mock
    update_mock.execute.return_value = resp
    return resp


def _base_upsert_kwargs(**overrides) -> dict:
    defaults = dict(
        raw_hash            = "oldhash_" + "a" * 56,
        insider_name        = "Mario Rossi",
        insider_role        = "CFO",
        company_name        = "Acme SpA",
        isin                = "IT0000123456",
        instrument_type     = "share",
        direction           = "buy",
        transaction_date    = "2024-01-15",
        filed_date          = "2024-01-16",
        quantity            = 1000.0,
        unit_price          = 10.50,
        total_value         = 10500.0,
        currency            = "EUR",
        source_url          = "https://example.com/a.pdf",
        source_transaction_index = 0,
        raw_document_sha256 = "sha256_" + "a" * 57,
        source_filing_id    = 42,
        parser_version      = "2.0.0",
    )
    defaults.update(overrides)
    return defaults


# ── compute_identity_hash ─────────────────────────────────────────────────────

class TestComputeIdentityHash(unittest.TestCase):

    def test_deterministic(self):
        h1 = compute_identity_hash(42, "sha256abc", 0, "IT0000123456", "buy")
        h2 = compute_identity_hash(42, "sha256abc", 0, "IT0000123456", "buy")
        self.assertEqual(h1, h2)

    def test_different_index_produces_different_hash(self):
        h0 = compute_identity_hash(42, "sha256abc", 0, "IT0000123456", "buy")
        h1 = compute_identity_hash(42, "sha256abc", 1, "IT0000123456", "buy")
        self.assertNotEqual(h0, h1)

    def test_different_direction_produces_different_hash(self):
        hbuy  = compute_identity_hash(42, "sha", 0, "IT0000123456", "buy")
        hsell = compute_identity_hash(42, "sha", 0, "IT0000123456", "sell")
        self.assertNotEqual(hbuy, hsell)

    def test_different_filing_produces_different_hash(self):
        h42 = compute_identity_hash(42,  "sha", 0, "IT0000123456", "buy")
        h99 = compute_identity_hash(99,  "sha", 0, "IT0000123456", "buy")
        self.assertNotEqual(h42, h99)

    def test_returns_64_char_hex(self):
        h = compute_identity_hash(1, "abc", 0, "IT0", "buy")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_null_isin_treated_as_no_isin(self):
        h_none = compute_identity_hash(1, "sha", 0, None, "buy")
        h_str  = compute_identity_hash(1, "sha", 0, "no_isin", "buy")
        self.assertEqual(h_none, h_str)


class TestDeriveIdentityHash(unittest.TestCase):

    def test_full_lineage_uses_new_formula(self):
        h = _derive_identity_hash(42, "sha256", 0, "IT0000", "buy", "oldhash")
        self.assertNotEqual(h, "legacy_oldhash")
        self.assertEqual(len(h), 64)

    def test_missing_filing_id_falls_back_to_legacy(self):
        h = _derive_identity_hash(None, "sha256", 0, "IT0000", "buy", "oldhash")
        self.assertEqual(h, "legacy_oldhash")

    def test_missing_sha256_falls_back_to_legacy(self):
        h = _derive_identity_hash(42, None, 0, "IT0000", "buy", "oldhash")
        self.assertEqual(h, "legacy_oldhash")

    def test_missing_index_falls_back_to_legacy(self):
        h = _derive_identity_hash(42, "sha256", None, "IT0000", "buy", "oldhash")
        self.assertEqual(h, "legacy_oldhash")


# ── _has_changed ──────────────────────────────────────────────────────────────

class TestHasChanged(unittest.TestCase):

    def _base_row(self, **overrides):
        row = {
            "direction": "buy", "transaction_date": "2024-01-15",
            "transaction_type": "buy", "economic_intent": "discretionary",
            "parser_version": "2.0.0", "currency": "EUR",
            "isin": "IT0000123456", "instrument_type": "share",
            "quantity": 1000.0, "unit_price": 10.50, "total_value": 10500.0,
        }
        row.update(overrides)
        return row

    def test_identical_rows_not_changed(self):
        row = self._base_row()
        self.assertFalse(_has_changed(row, dict(row)))

    def test_quantity_change_detected(self):
        self.assertTrue(_has_changed(
            self._base_row(quantity=1000.0),
            self._base_row(quantity=1001.0),
        ))

    def test_direction_change_detected(self):
        self.assertTrue(_has_changed(
            self._base_row(direction="buy"),
            self._base_row(direction="sell"),
        ))

    def test_parser_version_change_detected(self):
        self.assertTrue(_has_changed(
            self._base_row(parser_version="1.0.0"),
            self._base_row(parser_version="2.0.0"),
        ))

    def test_tiny_float_difference_not_flagged(self):
        self.assertFalse(_has_changed(
            self._base_row(quantity=1000.0),
            self._base_row(quantity=1000.0000000001),
        ))


# ── upsert_transaction: new transaction path ──────────────────────────────────

class TestUpsertNew(unittest.TestCase):

    def _make_client(self):
        client = _mock_client()
        # SELECT on identity_hash returns nothing (new transaction)
        _wire_select(client, [])
        # _resolve_company
        client.table.return_value.select.return_value.ilike.return_value.execute.return_value = _make_response([{"id": 10, "isin": None}])
        # _resolve_insider
        client.table.return_value.select.return_value.ilike.return_value.eq.return_value.execute.return_value = _make_response([{"id": 20}])
        return client

    @patch("scraper.db._resolve_company", return_value=10)
    @patch("scraper.db._resolve_insider", return_value=20)
    def test_new_transaction_inserted(self, mock_insider, mock_company):
        client = _mock_client()
        _wire_select(client, [])
        new_row = {"id": 99}
        _wire_insert(client, new_row)

        result = upsert_transaction(client, **_base_upsert_kwargs())
        self.assertTrue(result["inserted"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["transaction_id"], 99)

    @patch("scraper.db._resolve_company", return_value=10)
    @patch("scraper.db._resolve_insider", return_value=20)
    def test_insert_payload_contains_identity_hash(self, mock_insider, mock_company):
        client = _mock_client()
        _wire_select(client, [])
        _wire_insert(client, {"id": 99})

        upsert_transaction(client, **_base_upsert_kwargs())
        payload = client.table.return_value.insert.call_args[0][0]
        self.assertIn("identity_hash", payload)
        self.assertEqual(len(payload["identity_hash"]), 64)

    @patch("scraper.db._resolve_company", return_value=10)
    @patch("scraper.db._resolve_insider", return_value=20)
    def test_insert_payload_contains_both_hashes(self, mock_insider, mock_company):
        client = _mock_client()
        _wire_select(client, [])
        _wire_insert(client, {"id": 99})

        kwargs = _base_upsert_kwargs(raw_hash="oldhash_" + "a" * 56)
        upsert_transaction(client, **kwargs)
        payload = client.table.return_value.insert.call_args[0][0]
        # raw_hash preserved for forensic comparison
        self.assertIn("raw_hash", payload)
        # identity_hash is the new dedup key
        self.assertIn("identity_hash", payload)
        self.assertNotEqual(payload["raw_hash"], payload["identity_hash"])


# ── Gate test: same-day same-price both stored ────────────────────────────────

class TestSameDaySamePriceBothStored(unittest.TestCase):
    """
    Two legitimate transactions from the same filing on the same day at the
    same price must BOTH be inserted — they differ only in source_transaction_index.

    This was impossible with the old raw_hash (name|company|date|qty|price)
    because the second INSERT would have hit the UNIQUE constraint.
    """

    @patch("scraper.db._resolve_company", return_value=10)
    @patch("scraper.db._resolve_insider", return_value=20)
    def test_two_transactions_different_index_produce_different_identity(
        self, mock_insider, mock_company
    ):
        h0 = compute_identity_hash(42, "pdf_sha256_abc", 0, "IT0000123456", "buy")
        h1 = compute_identity_hash(42, "pdf_sha256_abc", 1, "IT0000123456", "buy")
        self.assertNotEqual(h0, h1,
            "Transactions at index 0 and 1 of the same filing must have different identity hashes")

    @patch("scraper.db._resolve_company", return_value=10)
    @patch("scraper.db._resolve_insider", return_value=20)
    def test_first_transaction_inserted(self, mock_insider, mock_company):
        client = _mock_client()
        _wire_select(client, [])
        _wire_insert(client, {"id": 101})
        result = upsert_transaction(client, **_base_upsert_kwargs(source_transaction_index=0))
        self.assertTrue(result["inserted"])

    @patch("scraper.db._resolve_company", return_value=10)
    @patch("scraper.db._resolve_insider", return_value=20)
    def test_second_transaction_same_price_same_day_also_inserted(self, mock_insider, mock_company):
        client = _mock_client()
        # No existing row with this identity_hash (different index → different hash)
        _wire_select(client, [])
        _wire_insert(client, {"id": 102})
        # Same day, same price, same qty, same insider — but index=1
        result = upsert_transaction(
            client,
            **_base_upsert_kwargs(source_transaction_index=1),
        )
        self.assertTrue(result["inserted"],
            "Second transaction at index=1 must be inserted, not suppressed")

    def test_buy_and_sell_from_same_insider_have_different_identity(self):
        h_buy  = compute_identity_hash(42, "sha", 0, "IT0000123456", "buy")
        h_sell = compute_identity_hash(42, "sha", 0, "IT0000123456", "sell")
        self.assertNotEqual(h_buy, h_sell,
            "Buy and sell at the same index must not share an identity hash")


# ── upsert_transaction: true dedup path ──────────────────────────────────────

class TestUpsertTrueDedup(unittest.TestCase):
    """
    When the exact same filing is re-ingested with no field changes, the
    transaction must be silently skipped (inserted=False, updated=False).
    """

    def _existing_row(self, **overrides):
        # Values must match the defaults in _base_upsert_kwargs + upsert_transaction.
        row = {
            "id": 77, "version_number": 1,
            "direction": "buy", "transaction_date": "2024-01-15",
            "transaction_type": "buy",
            "economic_intent": "unclear",   # matches upsert_transaction default
            "parser_version": "2.0.0", "currency": "EUR",
            "isin": "IT0000123456", "instrument_type": "share",
            "quantity": 1000.0, "unit_price": 10.50, "total_value": 10500.0,
            "company_id": 10, "insider_id": 20,
        }
        row.update(overrides)
        return row

    def test_unchanged_re_ingest_returns_not_inserted_not_updated(self):
        client = _mock_client()
        _wire_select(client, [self._existing_row()])

        result = upsert_transaction(client, **_base_upsert_kwargs())
        self.assertFalse(result["inserted"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["transaction_id"], 77)
        # Must not call INSERT or UPDATE
        client.table.return_value.insert.assert_not_called()
        client.table.return_value.update.assert_not_called()


# ── upsert_transaction: versioned update path ─────────────────────────────────

class TestUpsertVersioned(unittest.TestCase):
    """
    When a filing is re-processed and a field has changed (e.g. the parser
    was fixed and now extracts a different quantity), the existing row must be
    versioned (snapshotted) and then updated with the new values.
    """

    def _existing_row(self, **overrides):
        row = {
            "id": 55, "version_number": 1,
            "direction": "buy", "transaction_date": "2024-01-15",
            "transaction_type": "buy", "economic_intent": "discretionary",
            "parser_version": "1.0.0", "currency": "EUR",
            "isin": "IT0000123456", "instrument_type": "share",
            "quantity": 999.0,      # old value
            "unit_price": 10.50,
            "total_value": 10489.5,
            "company_id": 10, "insider_id": 20,
        }
        row.update(overrides)
        return row

    def test_changed_field_triggers_version_and_update(self):
        client = _mock_client()
        _wire_select(client, [self._existing_row()])
        _wire_update(client)

        # Re-ingest with corrected quantity (999 → 1000)
        result = upsert_transaction(
            client,
            **_base_upsert_kwargs(quantity=1000.0, parser_version="2.0.0"),
        )

        self.assertFalse(result["inserted"])
        self.assertTrue(result["updated"])
        self.assertEqual(result["transaction_id"], 55)

        # A version snapshot must have been inserted
        client.table.return_value.insert.assert_called_once()
        version_payload = client.table.return_value.insert.call_args[0][0]
        self.assertEqual(version_payload["transaction_id"], 55)
        self.assertIn("snapshot", version_payload)

        # An update must have been applied
        client.table.return_value.update.assert_called_once()
        update_payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_payload["version_number"], 2)
        self.assertAlmostEqual(float(update_payload["quantity"]), 1000.0)

    def test_parser_version_change_alone_triggers_versioning(self):
        client = _mock_client()
        _wire_select(client, [self._existing_row(parser_version="1.0.0")])
        _wire_update(client)

        result = upsert_transaction(
            client,
            **_base_upsert_kwargs(parser_version="2.0.0"),
        )
        self.assertTrue(result["updated"])


# ── correct_transaction ───────────────────────────────────────────────────────

class TestCorrectTransaction(unittest.TestCase):

    def _existing_row(self):
        return {
            "id": 33, "version_number": 1,
            "quantity": 500.0, "unit_price": 5.0, "total_value": 2500.0,
            "direction": "buy",
        }

    def test_correct_creates_version_snapshot(self):
        client = _mock_client()
        _wire_select(client, [self._existing_row()])
        _wire_update(client)

        ok = correct_transaction(
            client, 33,
            changes={"quantity": 600.0, "total_value": 3000.0},
            reason="Parser missed one lot",
        )

        self.assertTrue(ok)
        # Version insert
        client.table.return_value.insert.assert_called_once()
        snap_payload = client.table.return_value.insert.call_args[0][0]
        self.assertEqual(snap_payload["transaction_id"], 33)
        self.assertEqual(snap_payload["version_number"], 1)
        self.assertIn("snapshot", snap_payload)

    def test_correct_increments_version_number(self):
        client = _mock_client()
        _wire_select(client, [self._existing_row()])
        _wire_update(client)

        correct_transaction(
            client, 33,
            changes={"quantity": 600.0},
            reason="Manual fix",
        )
        update_payload = client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_payload["version_number"], 2)

    def test_correct_returns_false_when_not_found(self):
        client = _mock_client()
        _wire_select(client, [])   # not found

        ok = correct_transaction(client, 999, changes={}, reason="test")
        self.assertFalse(ok)
        client.table.return_value.insert.assert_not_called()
        client.table.return_value.update.assert_not_called()


# ── supersede_transaction ─────────────────────────────────────────────────────

class TestSupersedeTransaction(unittest.TestCase):

    def _existing_row(self):
        return {
            "id": 11, "version_number": 1,
            "is_current": True, "quantity": 100.0,
        }

    def test_supersede_sets_is_current_false(self):
        client = _mock_client()
        _wire_select(client, [self._existing_row()])
        _wire_update(client)

        ok = supersede_transaction(client, old_id=11, new_id=22, reason="duplicate filing")
        self.assertTrue(ok)

        update_payload = client.table.return_value.update.call_args[0][0]
        self.assertFalse(update_payload["is_current"])
        self.assertEqual(update_payload["superseded_by"], 22)
        self.assertIsNotNone(update_payload["superseded_at"])

    def test_supersede_creates_version_before_marking_stale(self):
        client = _mock_client()
        _wire_select(client, [self._existing_row()])
        _wire_update(client)

        supersede_transaction(client, old_id=11, new_id=22, reason="duplicate filing")
        client.table.return_value.insert.assert_called_once()
        snap = client.table.return_value.insert.call_args[0][0]
        self.assertIn("snapshot", snap)
        self.assertEqual(snap["transaction_id"], 11)


# ── get_transaction_history ───────────────────────────────────────────────────

class TestGetTransactionHistory(unittest.TestCase):

    def test_returns_current_and_versions(self):
        client = _mock_client()
        current_row = {"id": 5, "source_filing_id": None}
        versions    = [{"id": 1, "transaction_id": 5, "version_number": 1, "snapshot": {}}]

        # Wire two different .select().eq().execute() calls
        # First call: transactions table
        # Second call: transaction_versions table
        call_count = {"n": 0}
        responses  = [_make_response([current_row]), _make_response(versions)]

        original_select = client.table.return_value.select
        def side_select(*args, **kwargs):
            m = MagicMock()
            n = call_count["n"]
            call_count["n"] += 1
            chain = MagicMock()
            chain.eq.return_value   = chain
            chain.order.return_value = chain
            chain.execute.return_value = responses[n] if n < len(responses) else _make_response([])
            m.return_value = chain
            return chain
        client.table.return_value.select.side_effect = side_select

        history = get_transaction_history(client, 5)
        self.assertIsNotNone(history["current"])
        self.assertEqual(history["current"]["id"], 5)
        self.assertEqual(len(history["versions"]), 1)
        self.assertIsNone(history["filing"])  # no source_filing_id

    def test_returns_empty_when_not_found(self):
        client = _mock_client()
        chain = MagicMock()
        chain.eq.return_value    = chain
        chain.order.return_value = chain
        chain.execute.return_value = _make_response([])
        client.table.return_value.select.return_value = chain

        history = get_transaction_history(client, 999)
        self.assertIsNone(history["current"])
        self.assertEqual(history["versions"], [])


if __name__ == "__main__":
    unittest.main()
