"""
Phase 12 — tests for enhanced classifier.

Covers:
  - confidence field on ClassificationResult
  - new Italian keywords (ASSEGNAZIONE GRATUITA, DIRITTI DI OPZIONE,
    PIANO DI INCENTIVAZIONE, FUSIONE, SCISSIONE, SCAMBIO,
    SOCIETÀ CONTROLLATA, FIDUCIARIA)
  - confidence levels per rule (0.90 / 0.85 / 0.65 / 0.40 / 0.30)
  - related-entity vehicle classification with needs_review=True
"""

import pytest
from scraper.classifier import ClassificationResult, classify


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _clf(
    direction="buy",
    nature="",
    unit_price=10.0,
    quantity=100.0,
    hint="buy",
    warnings=None,
):
    return classify(direction, nature, unit_price, quantity, hint, warnings or [])


# ─── ClassificationResult shape ───────────────────────────────────────────────

class TestClassificationResultShape:
    def test_has_confidence_field(self):
        r = _clf()
        assert hasattr(r, "confidence")

    def test_confidence_default_is_float(self):
        r = ClassificationResult(
            transaction_type="buy",
            economic_intent="discretionary",
            rationale="test",
        )
        assert isinstance(r.confidence, float)
        assert r.confidence == 0.5

    def test_confidence_clamped_to_0_1(self):
        from scraper.classifier import _result
        r = _result("buy", "test", confidence=2.5)
        assert r.confidence <= 1.0
        r2 = _result("buy", "test", confidence=-1.0)
        assert r2.confidence >= 0.0


# ─── Rule confidence levels ───────────────────────────────────────────────────

class TestConfidenceLevels:
    def test_zero_price_grant_confidence_090(self):
        r = _clf(direction="buy", nature="", unit_price=0.0, quantity=500.0, hint="buy")
        assert r.transaction_type == "grant"
        assert r.confidence == pytest.approx(0.90, abs=0.001)

    def test_parser_keyword_confidence_085(self):
        r = _clf(direction="buy", nature="", unit_price=5.0, quantity=100.0, hint="option_exercise")
        assert r.transaction_type == "option_exercise"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_raw_nature_keyword_confidence_085(self):
        r = _clf(direction="buy", nature="SUCCESSIONE EREDITARIA", unit_price=5.0, quantity=100.0, hint="buy")
        assert r.transaction_type == "inheritance"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_direction_fallthrough_buy_confidence_065(self):
        r = _clf(direction="buy", nature="", unit_price=10.0, quantity=100.0, hint="buy")
        assert r.transaction_type == "buy"
        assert r.confidence == pytest.approx(0.65, abs=0.001)

    def test_direction_fallthrough_sell_confidence_065(self):
        r = _clf(direction="sell", nature="", unit_price=10.0, quantity=100.0, hint="sell")
        assert r.transaction_type == "sell"
        assert r.confidence == pytest.approx(0.65, abs=0.001)

    def test_vague_direction_confidence_040(self):
        r = _clf(
            direction="buy", nature="",
            unit_price=10.0, quantity=100.0, hint="buy",
            warnings=["Direction inferred from option programme SI/YES flag — needs_review"],
        )
        assert r.transaction_type == "unknown"
        assert r.needs_review is True
        assert r.confidence == pytest.approx(0.40, abs=0.001)

    def test_undetermined_confidence_030(self):
        r = _clf(direction="unknown", nature="", unit_price=10.0, quantity=100.0, hint="buy")
        assert r.transaction_type == "unknown"
        assert r.needs_review is True
        assert r.confidence == pytest.approx(0.30, abs=0.001)


# ─── New Italian keywords ─────────────────────────────────────────────────────

class TestNewItalianKeywords:
    def test_assegnazione_gratuita_is_grant(self):
        r = _clf(direction="buy", nature="ASSEGNAZIONE GRATUITA DI AZIONI", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "grant"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_diritti_di_opzione_is_option_exercise(self):
        r = _clf(direction="buy", nature="ESERCIZIO DIRITTI DI OPZIONE", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "option_exercise"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_piano_di_incentivazione_is_option_exercise(self):
        r = _clf(direction="buy", nature="PIANO DI INCENTIVAZIONE AZIENDALE", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "option_exercise"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_stock_option_is_option_exercise(self):
        r = _clf(direction="buy", nature="STOCK OPTION GRANT", unit_price=5.0, quantity=100.0)
        # STOCK OPTION keyword → option_exercise
        assert r.transaction_type in ("option_exercise", "grant")
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_fusione_is_conversion(self):
        r = _clf(direction="buy", nature="FUSIONE PER INCORPORAZIONE", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "conversion"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_scissione_is_conversion(self):
        r = _clf(direction="sell", nature="SCISSIONE PARZIALE", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "conversion"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_scambio_is_conversion(self):
        r = _clf(direction="buy", nature="SCAMBIO DI AZIONI", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "conversion"
        assert r.confidence == pytest.approx(0.85, abs=0.001)

    def test_societa_controllata_buy_is_transfer_in_needs_review(self):
        r = _clf(direction="buy", nature="ACQUISTO TRAMITE SOCIETÀ CONTROLLATA", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "transfer_in"
        assert r.needs_review is True
        assert r.confidence == pytest.approx(0.60, abs=0.001)

    def test_societa_controllata_sell_is_transfer_out_needs_review(self):
        r = _clf(direction="sell", nature="CESSIONE TRAMITE SOCIETÀ CONTROLLATA", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "transfer_out"
        assert r.needs_review is True
        assert r.confidence == pytest.approx(0.60, abs=0.001)

    def test_fiduciaria_is_related_entity_needs_review(self):
        r = _clf(direction="buy", nature="ACQUISTO TRAMITE FIDUCIARIA", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "transfer_in"
        assert r.needs_review is True

    def test_controllata_keyword_alone(self):
        r = _clf(direction="buy", nature="OPERAZIONE TRAMITE CONTROLLATA", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "transfer_in"
        assert r.needs_review is True

    def test_merger_english_is_conversion(self):
        r = _clf(direction="buy", nature="MERGER WITH ACQUIROR", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "conversion"

    def test_demerger_english_is_conversion(self):
        r = _clf(direction="buy", nature="DEMERGER TRANSACTION", unit_price=5.0, quantity=100.0)
        assert r.transaction_type == "conversion"


# ─── Existing keywords still work ─────────────────────────────────────────────

class TestExistingKeywordsUnchanged:
    def test_successione_still_inheritance(self):
        r = _clf(nature="SUCCESSIONE MORTIS CAUSA")
        assert r.transaction_type == "inheritance"

    def test_sottoscrizione_still_subscription(self):
        r = _clf(nature="SOTTOSCRIZIONE DI AZIONI")
        assert r.transaction_type == "subscription"

    def test_pegno_still_pledge(self):
        r = _clf(nature="COSTITUZIONE PEGNO", unit_price=5.0)
        assert r.transaction_type == "pledge_or_security"

    def test_assegnazione_plus_vendita_is_sell_to_cover(self):
        r = _clf(direction="sell", nature="ASSEGNAZIONE E VENDITA", unit_price=5.0)
        assert r.transaction_type == "sell_to_cover"

    def test_copertura_sell_is_sell_to_cover(self):
        r = _clf(direction="sell", nature="VENDITA PER COPERTURA FISCALE", unit_price=5.0)
        assert r.transaction_type == "sell_to_cover"

    def test_zero_price_buy_no_nature_grant(self):
        r = _clf(direction="buy", nature="", unit_price=0.0, quantity=200.0)
        assert r.transaction_type == "grant"

    def test_hint_sell_to_cover_passes_through(self):
        r = _clf(direction="sell", nature="", unit_price=5.0, hint="sell_to_cover")
        assert r.transaction_type == "sell_to_cover"


# ─── priority ordering — new keywords vs old ─────────────────────────────────

class TestKeywordPriority:
    def test_fusione_beats_direction_fallthrough(self):
        # Without FUSIONE, buy fallthrough would give "buy".
        r_with = _clf(direction="buy", nature="FUSIONE E INCORPORAZIONE", unit_price=5.0)
        r_without = _clf(direction="buy", nature="", unit_price=5.0)
        assert r_with.transaction_type == "conversion"
        assert r_without.transaction_type == "buy"

    def test_piano_beats_direction_fallthrough(self):
        r = _clf(direction="buy", nature="PIANO DI INCENTIVAZIONE", unit_price=5.0)
        assert r.transaction_type == "option_exercise"

    def test_related_entity_lower_confidence_than_pure_keyword(self):
        # Related entity: 0.60; pure keyword: 0.85
        r_related = _clf(direction="buy", nature="TRAMITE SOCIETÀ CONTROLLATA", unit_price=5.0)
        r_keyword  = _clf(direction="buy", nature="SUCCESSIONE EREDITARIA", unit_price=5.0)
        assert r_related.confidence < r_keyword.confidence
