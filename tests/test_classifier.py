"""
Tests for scraper/classifier.py.

Covers every classification rule, the economic intent mapping, the
zero-price-grant override (including exclusions), and classify_override().
"""

import pytest
from unittest.mock import MagicMock

from scraper.classifier import (
    classify,
    classify_override,
    ClassificationResult,
    _economic_intent,
)


# ─── economic_intent mapping ──────────────────────────────────────────────────

class TestEconomicIntent:
    def test_discretionary_types(self):
        for t in ("buy", "sell", "subscription"):
            assert _economic_intent(t) == "discretionary", t

    def test_mechanical_types(self):
        for t in (
            "grant", "option_exercise", "sell_to_cover",
            "conversion", "inheritance",
            "gift_in", "gift_out", "transfer_in", "transfer_out",
        ):
            assert _economic_intent(t) == "mechanical", t

    def test_unclear_types(self):
        for t in ("other", "unknown", ""):
            assert _economic_intent(t) == "unclear", t


# ─── Rule 1: zero-price grant ─────────────────────────────────────────────────

class TestZeroPriceGrant:
    def test_zero_price_buy_becomes_grant(self):
        r = classify("buy", "ASSEGNAZIONE", 0.0, 5000.0, "grant", [])
        assert r.transaction_type == "grant"
        assert r.economic_intent == "mechanical"
        assert "zero_price_grant" in r.rationale

    def test_zero_price_sell_does_not_become_grant(self):
        r = classify("sell", "", 0.0, 100.0, "sell", [])
        assert r.transaction_type == "sell"

    def test_zero_price_zero_quantity_not_grant(self):
        r = classify("buy", "", 0.0, 0.0, "buy", [])
        # quantity == 0 → Rule 1 does not fire
        assert r.transaction_type != "grant"

    def test_zero_price_inheritance_not_reclassified(self):
        r = classify("buy", "SUCCESSIONE", 0.0, 500.0, "inheritance", [])
        assert r.transaction_type == "inheritance"

    def test_zero_price_gift_in_not_reclassified(self):
        r = classify("buy", "DONAZIONE", 0.0, 200.0, "gift_in", [])
        assert r.transaction_type == "gift_in"

    def test_zero_price_transfer_not_reclassified(self):
        r = classify("buy", "TRASFERIMENTO", 0.0, 100.0, "transfer_in", [])
        assert r.transaction_type == "transfer_in"

    def test_zero_price_conversion_not_reclassified(self):
        r = classify("buy", "", 0.0, 1000.0, "conversion", [])
        assert r.transaction_type == "conversion"

    def test_zero_price_with_nature_text_keyword_not_grant(self):
        # Raw nature text contains SUCCESSIONE → Rule 1 excluded by nature keyword check
        r = classify("buy", "SUCCESSIONE EREDITARIA", 0.0, 50.0, "other", [])
        assert r.transaction_type == "inheritance"


# ─── Rule 2: parser keyword hint ─────────────────────────────────────────────

class TestParserHint:
    def test_option_exercise_hint_passes_through(self):
        r = classify("buy", "", 5.0, 100.0, "option_exercise", [])
        assert r.transaction_type == "option_exercise"
        assert "parser_keyword" in r.rationale

    def test_sell_to_cover_hint_passes_through(self):
        r = classify("sell", "", 15.0, 300.0, "sell_to_cover", [])
        assert r.transaction_type == "sell_to_cover"

    def test_subscription_hint_passes_through(self):
        r = classify("buy", "", 3.0, 500.0, "subscription", [])
        assert r.transaction_type == "subscription"
        assert r.economic_intent == "discretionary"

    def test_transfer_out_hint_buy_becomes_transfer_in(self):
        r = classify("buy", "TRASFERIMENTO", 5.0, 100.0, "transfer_out", [])
        assert r.transaction_type == "transfer_in"

    def test_transfer_out_hint_sell_stays_transfer_out(self):
        r = classify("sell", "TRASFERIMENTO", 5.0, 100.0, "transfer_out", [])
        assert r.transaction_type == "transfer_out"

    def test_gift_out_hint_buy_becomes_gift_in(self):
        r = classify("buy", "DONAZIONE", 0.0, 200.0, "gift_out", [])
        assert r.transaction_type == "gift_in"

    def test_gift_out_hint_sell_stays_gift_out(self):
        r = classify("sell", "DONAZIONE", 0.0, 200.0, "gift_out", [])
        assert r.transaction_type == "gift_out"

    def test_inheritance_hint_passes_through(self):
        r = classify("buy", "SUCCESSIONE", 0.0, 500.0, "inheritance", [])
        assert r.transaction_type == "inheritance"

    def test_conversion_hint_passes_through(self):
        r = classify("sell", "PERMUTA", 0.0, 1000.0, "conversion", [])
        assert r.transaction_type == "conversion"


# ─── Rule 3: raw nature text keywords ─────────────────────────────────────────

class TestRawNatureTextKeywords:
    def test_successione_in_nature_text(self):
        r = classify("buy", "SUCCESSIONE EREDITARIA", 5.0, 50.0, "other", [])
        assert r.transaction_type == "inheritance"

    def test_heredit_in_nature_text(self):
        r = classify("buy", "EREDITA DI AZIONI", 5.0, 50.0, "other", [])
        assert r.transaction_type == "inheritance"

    def test_sottoscrizione_in_nature_text(self):
        r = classify("buy", "SOTTOSCRIZIONE NUOVE AZIONI", 3.0, 500.0, "buy", [])
        assert r.transaction_type == "subscription"

    def test_conversione_in_nature_text(self):
        r = classify("buy", "CONVERSIONE OBBLIGAZIONI", 0.0, 1000.0, "other", [])
        assert r.transaction_type == "conversion"

    def test_permuta_in_nature_text(self):
        r = classify("sell", "PERMUTA", 0.0, 500.0, "sell", [])
        assert r.transaction_type == "conversion"

    def test_donation_buy_in_nature_text(self):
        r = classify("buy", "DONAZIONE DI AZIONI", 0.0, 100.0, "buy", [])
        assert r.transaction_type == "gift_in"

    def test_donation_sell_in_nature_text(self):
        r = classify("sell", "DONAZIONE", 0.0, 100.0, "sell", [])
        assert r.transaction_type == "gift_out"

    def test_transfer_buy_in_nature_text(self):
        r = classify("buy", "TRASFERIMENTO PORTAFOGLIO", 5.0, 500.0, "buy", [])
        assert r.transaction_type == "transfer_in"

    def test_transfer_sell_in_nature_text(self):
        r = classify("sell", "TRASFERIMENTO", 5.0, 500.0, "sell", [])
        assert r.transaction_type == "transfer_out"

    def test_assegnazione_gratuita_in_nature_text(self):
        r = classify("buy", "ASSEGNAZIONE GRATUITA AZIONI", 0.0, 1000.0, "buy", [])
        assert r.transaction_type == "grant"

    def test_sell_to_cover_via_assegnazione_plus_vendita(self):
        r = classify("sell", "ASSEGNAZIONE GRATUITA VENDITA PER COPERTURA", 15.0, 300.0, "sell", [])
        assert r.transaction_type == "sell_to_cover"

    def test_option_in_nature_text(self):
        r = classify("buy", "ESERCIZIO OPZIONE SU AZIONI", 5.0, 200.0, "buy", [])
        assert r.transaction_type == "option_exercise"


# ─── Rule 4/5: direction fallthrough / unclassifiable ─────────────────────────

class TestFallthrough:
    def test_buy_direction_fallthrough(self):
        r = classify("buy", "", 10.0, 100.0, "buy", [])
        assert r.transaction_type == "buy"
        assert r.economic_intent == "discretionary"

    def test_sell_direction_fallthrough(self):
        r = classify("sell", "", 10.0, 100.0, "sell", [])
        assert r.transaction_type == "sell"
        assert r.economic_intent == "discretionary"

    def test_unknown_direction_becomes_other(self):
        r = classify("unknown", "", 10.0, 100.0, "other", [])
        assert r.transaction_type == "other"
        assert r.economic_intent == "unclear"

    def test_rationale_always_present(self):
        for direction in ("buy", "sell", "unknown"):
            r = classify(direction, "", 1.0, 1.0, "other", [])
            assert r.rationale, f"No rationale for direction={direction}"


# ─── classify_override ────────────────────────────────────────────────────────

class TestClassifyOverride:
    def _make_client(self, existing_row: dict):
        client = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.update.return_value = tbl
        tbl.insert.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[existing_row])
        client.table.return_value = tbl
        return client, tbl

    def test_override_updates_transaction(self):
        row = {
            "id": 1, "transaction_type": "buy", "economic_intent": "discretionary",
            "version_number": 1,
        }
        client, tbl = self._make_client(row)
        classify_override(client, 1, "sell_to_cover", "mechanical", "operator_correction", changed_by="alice")
        update_payload = tbl.update.call_args[0][0]
        assert update_payload["transaction_type"] == "sell_to_cover"
        assert update_payload["economic_intent"] == "mechanical"
        assert update_payload["classification_override"] is True
        assert update_payload["classification_overridden_by"] == "alice"
        assert update_payload["version_number"] == 2

    def test_override_missing_transaction_does_not_raise(self):
        client = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value = tbl
        tbl.eq.return_value = tbl
        tbl.limit.return_value = tbl
        tbl.execute.return_value = MagicMock(data=[])
        client.table.return_value = tbl
        # Should not raise
        classify_override(client, 99999, "buy", "discretionary", "test")

    def test_override_db_error_does_not_raise(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("DB error")
        # Should not raise
        classify_override(client, 1, "buy", "discretionary", "test")
