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
            "pledge_or_security", "derivative_transaction",
        ):
            assert _economic_intent(t) == "mechanical", t

    def test_unclear_types(self):
        for t in ("other", "unknown", ""):
            assert _economic_intent(t) == "unclear", t

    def test_unknown_is_distinct_from_other(self):
        # 'unknown' = source wording cannot be determined (classifier output for unreadable events).
        # 'other'   = understood but outside principal taxonomy (set via classify_override only).
        # Both must be persisted as distinct values in the DB and both map to economic_intent=unclear.
        assert _economic_intent("unknown") == "unclear"
        assert _economic_intent("other") == "unclear"
        # Classifier emits 'unknown' for direction=unknown with no recognisable keywords.
        r = classify("unknown", "", 5.0, 100.0, "other", [])
        assert r.transaction_type == "unknown", (
            f"Expected 'unknown', got '{r.transaction_type}' — "
            "classifier must not collapse undetermined events into 'other'"
        )
        assert r.economic_intent == "unclear"
        assert r.needs_review is True
        # Classifier never autonomously emits 'other'; that value is reserved for operator overrides.
        for direction in ("buy", "sell", "unknown"):
            r2 = classify(direction, "", 1.0, 1.0, "other", [])
            assert r2.transaction_type != "other", (
                f"Classifier should not emit 'other' autonomously (direction={direction})"
            )


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

    def test_unknown_direction_becomes_unknown(self):
        r = classify("unknown", "", 10.0, 100.0, "other", [])
        assert r.transaction_type == "unknown"
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


# ─── Roadmap alignment: explicit canonical-type tests ─────────────────────────
#
# Required by alignment review: direct test for each roadmap canonical type.
# Roadmap name → internal type(s):
#   open_market_buy        → buy           (discretionary)
#   open_market_sell       → sell          (discretionary)
#   sell_to_cover          → sell_to_cover (mechanical) — MUST NOT be treated as sell
#   option_exercise        → option_exercise (mechanical)
#   share_grant / vesting  → grant         (mechanical)
#   transfer               → transfer_in / transfer_out (mechanical)
#   inheritance_or_donation→ inheritance / gift_in / gift_out (mechanical)
#   pledge_or_security     → pledge_or_security (mechanical)
#   derivative_transaction → derivative_transaction (mechanical)
#   other                  → other         (unclear)
#   unknown / ambiguous    → other + needs_review=True (unclear)

class TestRoadmapCanonicalTypes:
    # ── open_market_buy ───────────────────────────────────────────────────────
    def test_open_market_buy_is_discretionary(self):
        r = classify("buy", "ACQUISTO", 10.0, 500.0, "buy", [])
        assert r.transaction_type == "buy"
        assert r.economic_intent == "discretionary"
        assert r.needs_review is False

    # ── open_market_sell ──────────────────────────────────────────────────────
    def test_open_market_sell_is_discretionary(self):
        r = classify("sell", "CESSIONE", 10.0, 500.0, "sell", [])
        assert r.transaction_type == "sell"
        assert r.economic_intent == "discretionary"
        assert r.needs_review is False

    # ── sell_to_cover: must NOT be treated as a discretionary sale ────────────
    def test_sell_to_cover_is_mechanical_not_sell(self):
        r = classify("sell", "", 15.0, 200.0, "sell_to_cover", [])
        assert r.transaction_type == "sell_to_cover"
        assert r.economic_intent == "mechanical"   # key assertion
        assert r.transaction_type != "sell"
        assert r.needs_review is False

    def test_sell_to_cover_via_grant_plus_vendita(self):
        r = classify("sell", "ASSEGNAZIONE GRATUITA VENDITA PER COPERTURA FISCALE", 15.0, 200.0, "sell", [])
        assert r.transaction_type == "sell_to_cover"
        assert r.economic_intent == "mechanical"

    def test_sell_to_cover_via_standalone_copertura(self):
        # Standalone COPERTURA without explicit ASSEGNAZIONE — explicit sell-to-cover rule
        r = classify("sell", "VENDITA PER COPERTURA FISCALE", 12.0, 100.0, "sell", [])
        assert r.transaction_type == "sell_to_cover"
        assert r.economic_intent == "mechanical"

    def test_sell_to_cover_standalone_cover_keyword(self):
        r = classify("sell", "SALE FOR TAX COVER", 20.0, 50.0, "sell", [])
        assert r.transaction_type == "sell_to_cover"
        assert r.economic_intent == "mechanical"

    # ── option_exercise ───────────────────────────────────────────────────────
    def test_option_exercise_is_mechanical(self):
        r = classify("buy", "ESERCIZIO OPZIONI", 5.0, 1000.0, "option_exercise", [])
        assert r.transaction_type == "option_exercise"
        assert r.economic_intent == "mechanical"
        assert r.needs_review is False

    # ── share_grant / vesting (same event in MAR Art 19 context) ─────────────
    def test_share_grant_zero_price(self):
        r = classify("buy", "ASSEGNAZIONE", 0.0, 2000.0, "grant", [])
        assert r.transaction_type == "grant"
        assert r.economic_intent == "mechanical"
        assert r.needs_review is False

    def test_vesting_maps_to_grant(self):
        # "Vesting" in MAR filings is the share delivery event — classified as grant
        r = classify("buy", "ASSEGNAZIONE AZIONI MATURATE", 0.0, 500.0, "grant", [])
        assert r.transaction_type == "grant"
        assert r.economic_intent == "mechanical"

    # ── transfer ─────────────────────────────────────────────────────────────
    def test_transfer_in_is_mechanical(self):
        r = classify("buy", "TRASFERIMENTO", 5.0, 300.0, "transfer_out", [])
        assert r.transaction_type == "transfer_in"
        assert r.economic_intent == "mechanical"

    def test_transfer_out_is_mechanical(self):
        r = classify("sell", "TRASFERIMENTO PORTAFOGLIO", 5.0, 300.0, "sell", [])
        assert r.transaction_type == "transfer_out"
        assert r.economic_intent == "mechanical"

    # ── inheritance_or_donation ───────────────────────────────────────────────
    def test_inheritance_is_mechanical(self):
        r = classify("buy", "SUCCESSIONE", 0.0, 100.0, "inheritance", [])
        assert r.transaction_type == "inheritance"
        assert r.economic_intent == "mechanical"

    def test_donation_received_is_mechanical(self):
        r = classify("buy", "DONAZIONE", 0.0, 50.0, "gift_out", [])
        assert r.transaction_type == "gift_in"
        assert r.economic_intent == "mechanical"

    def test_donation_given_is_mechanical(self):
        r = classify("sell", "DONAZIONE", 0.0, 50.0, "gift_out", [])
        assert r.transaction_type == "gift_out"
        assert r.economic_intent == "mechanical"

    # ── pledge_or_security ────────────────────────────────────────────────────
    def test_pledge_is_mechanical(self):
        r = classify("sell", "PEGNO SU AZIONI", 0.0, 500.0, "sell", [])
        assert r.transaction_type == "pledge_or_security"
        assert r.economic_intent == "mechanical"

    def test_garanzia_is_pledge(self):
        r = classify("sell", "GARANZIA FINANZIARIA", 0.0, 200.0, "sell", [])
        assert r.transaction_type == "pledge_or_security"
        assert r.economic_intent == "mechanical"

    def test_vincolo_is_pledge(self):
        r = classify("sell", "VINCOLO DI PEGNO", 0.0, 100.0, "sell", [])
        assert r.transaction_type == "pledge_or_security"
        assert r.economic_intent == "mechanical"

    # ── derivative_transaction ────────────────────────────────────────────────
    def test_derivative_is_mechanical(self):
        r = classify("buy", "CONTRATTO DERIVATO SU AZIONI", 5.0, 1000.0, "buy", [])
        assert r.transaction_type == "derivative_transaction"
        assert r.economic_intent == "mechanical"

    def test_futures_keyword(self):
        r = classify("sell", "FUTURES SU AZIONI ENI", 0.0, 500.0, "sell", [])
        assert r.transaction_type == "derivative_transaction"
        assert r.economic_intent == "mechanical"

    # ── other / unknown ───────────────────────────────────────────────────────
    def test_unclassifiable_is_unclear_with_review(self):
        r = classify("unknown", "", 10.0, 100.0, "other", [])
        assert r.transaction_type == "unknown"
        assert r.economic_intent == "unclear"
        assert r.needs_review is True

    def test_unknown_type_always_needs_review(self):
        r = classify("unknown", "ALTRO NON SPECIFICATO", 5.0, 50.0, "other", [])
        assert r.transaction_type == "unknown"
        assert r.needs_review is True


# ─── Ambiguous event / vague source wording ───────────────────────────────────

class TestAmbiguousAndVagueSource:
    """
    Roadmap requirement: vague or incomplete source wording cannot produce
    a high-confidence discretionary classification. Those cases must become
    unclear/unknown with needs_review=True.
    """

    def test_unknown_direction_no_nature_text_is_unclear(self):
        # No direction keyword found, no nature text → undetermined → unknown
        r = classify("unknown", "", 10.0, 100.0, "other", [])
        assert r.transaction_type == "unknown"
        assert r.economic_intent == "unclear"
        assert r.needs_review is True

    def test_unknown_direction_with_nature_text_is_unclear(self):
        # Unrecognised description → undetermined → unknown
        r = classify("unknown", "OPERAZIONE NON SPECIFICATA", 10.0, 100.0, "other", [])
        assert r.transaction_type == "unknown"
        assert r.needs_review is True

    def test_si_yes_fallback_warning_prevents_discretionary(self):
        # Direction="buy" inferred from SI/YES option programme flag (not a keyword)
        # → must NOT produce high-confidence discretionary classification
        warning = "Direction inferred from option programme SI/YES flag — needs_review"
        r = classify("buy", "", 5.0, 100.0, "buy", [warning])
        # The SI/YES path returns option_exercise from the parser (mechanical),
        # so hint would normally be "option_exercise". If somehow hint is "buy"
        # with this warning, the vague-direction guard must fire.
        assert r.economic_intent != "discretionary" or r.needs_review is True

    def test_fallback_warning_in_parse_warnings_prevents_confident_buy(self):
        # "fallback" in parse_warnings signals weak extraction → vague direction
        r = classify("buy", "", 5.0, 100.0, "buy", ["Transaction date found via fallback ISO pattern"])
        # With "fallback" in warnings, Rule 4 guard fires → unknown/unclear/needs_review
        assert r.transaction_type == "unknown"
        assert r.economic_intent == "unclear"
        assert r.needs_review is True

    def test_clean_buy_with_no_warnings_is_confident_discretionary(self):
        # No warnings, clear ACQUISTO → confident discretionary
        r = classify("buy", "ACQUISTO", 10.0, 500.0, "buy", [])
        assert r.transaction_type == "buy"
        assert r.economic_intent == "discretionary"
        assert r.needs_review is False

    def test_clean_sell_with_no_warnings_is_confident_discretionary(self):
        r = classify("sell", "CESSIONE", 10.0, 500.0, "sell", [])
        assert r.transaction_type == "sell"
        assert r.economic_intent == "discretionary"
        assert r.needs_review is False

    def test_empty_nature_text_with_unknown_direction_is_unclassifiable(self):
        r = classify("unknown", "", 0.0, 0.0, "other", [])
        assert r.transaction_type == "unknown"
        assert r.needs_review is True

    def test_zero_price_zero_qty_unknown_direction_is_unknown(self):
        # Both price and qty are zero — extraction failure, not a grant
        r = classify("unknown", "", 0.0, 0.0, "other", [])
        assert r.transaction_type == "unknown"
        assert r.needs_review is True


# ─── Mixed filing: classifier correctly distinguishes types per block ──────────

class TestMixedFiling:
    """
    Verifies that per-transaction classification is independent.
    Mirrors the mixed_grant_and_buy.json fixture.
    """

    def test_grant_then_buy_in_same_filing(self):
        # Transaction 1: zero-price ASSEGNAZIONE → grant
        r1 = classify("buy", "ASSEGNAZIONE", 0.0, 3000.0, "grant", [])
        assert r1.transaction_type == "grant"
        assert r1.economic_intent == "mechanical"

        # Transaction 2: priced ACQUISTO → buy
        r2 = classify("buy", "ACQUISTO", 5.62, 1000.0, "buy", [])
        assert r2.transaction_type == "buy"
        assert r2.economic_intent == "discretionary"

        # The two are distinct — one mechanical, one discretionary
        assert r1.economic_intent != r2.economic_intent

    def test_option_exercise_then_sell_to_cover(self):
        # Common paired filing: exercise followed by covering sale
        r1 = classify("buy", "ESERCIZIO OPZIONI", 3.0, 500.0, "option_exercise", [])
        r2 = classify("sell", "VENDITA PER COPERTURA", 15.0, 200.0, "sell", [])
        assert r1.transaction_type == "option_exercise"
        assert r2.transaction_type == "sell_to_cover"
        # Both mechanical — neither counts as a discretionary signal
        assert r1.economic_intent == "mechanical"
        assert r2.economic_intent == "mechanical"

    def test_sell_to_cover_is_excluded_from_discretionary_signal_filter(self):
        from scraper.classifier import _DISCRETIONARY
        assert "sell_to_cover" not in _DISCRETIONARY

    def test_pledge_excluded_from_discretionary(self):
        from scraper.classifier import _DISCRETIONARY
        assert "pledge_or_security" not in _DISCRETIONARY

    def test_derivative_excluded_from_discretionary(self):
        from scraper.classifier import _DISCRETIONARY
        assert "derivative_transaction" not in _DISCRETIONARY
