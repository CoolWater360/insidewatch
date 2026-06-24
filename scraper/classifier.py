"""
Transaction classification rules engine — Phase 7 / Phase 12.

Takes raw extracted fields from the parser and produces a final
transaction_type (from the extended 13-value taxonomy), economic_intent,
a human-readable classification_rationale, and a confidence score.

Design principles:
  - Rules are applied in strict priority order; first match wins.
  - Every outcome records a rationale tag so classifications are auditable.
  - Every outcome carries a confidence score reflecting rule certainty.
  - No DB access — pure function; caller decides what to persist.
  - The parser's direction detection is treated as an authoritative hint;
    the classifier only overrides it when a stronger signal is present.

Confidence levels by rule (0.0–1.0):
  0.90  zero_price_grant        — unambiguous structural signal
  0.85  parser_keyword          — keyword was unambiguously matched by parser
  0.85  raw_nature_keyword      — keyword present in section-4b text
  0.65  direction_fallthrough   — no specific keyword found; direction used
  0.40  vague_direction         — direction inferred from weak/fallback signal
  0.30  undetermined            — direction and type both unknown

Public API
----------
classify(direction, raw_nature_text, unit_price, quantity,
         parser_type_hint, parse_warnings) -> ClassificationResult

classify_override(client, transaction_id, transaction_type, economic_intent,
                  rationale, *, changed_by) -> None
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional, List

logger = logging.getLogger(__name__)


# ─── Result type ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ClassificationResult:
    transaction_type: str   # one of the 15 taxonomy values
    economic_intent: str    # discretionary | mechanical | unclear
    rationale: str          # "<rule>: <detail>" — stored in DB
    needs_review: bool = False   # True when classification is uncertain or ambiguous
    confidence: float = 0.5      # 0.0–1.0; see module docstring for per-rule levels


# ─── Economic intent mapping ─────────────────────────────────────────────────

# Transactions where the insider exercised active discretion.
_DISCRETIONARY = {"buy", "sell", "subscription"}

# Cannot be determined — source wording/type is absent or unreadable.
# Always emits economic_intent=unclear and needs_review=True.
_UNKNOWN = {"unknown"}

# Transactions driven by contract, plan, or external event — not active choice.
_MECHANICAL = {
    "grant", "option_exercise", "sell_to_cover",
    "conversion", "inheritance", "gift_in", "gift_out",
    "transfer_in", "transfer_out",
    "pledge_or_security",     # collateral / encumbrance — not a market transaction
    "derivative_transaction", # derivative instrument over issuer shares
}


def _economic_intent(transaction_type: str) -> str:
    if transaction_type in _DISCRETIONARY:
        return "discretionary"
    if transaction_type in _MECHANICAL:
        return "mechanical"
    return "unclear"  # covers both 'other' and 'unknown'


# ─── Keyword helpers ──────────────────────────────────────────────────────────

def _contains(text: str, *keywords: str) -> bool:
    """Case-insensitive substring check for any keyword."""
    upper = text.upper()
    return any(k in upper for k in keywords)


# ─── Main classifier ─────────────────────────────────────────────────────────

def classify(
    direction: str,
    raw_nature_text: str,
    unit_price: float,
    quantity: float,
    parser_type_hint: str,
    parse_warnings: List[str],
) -> ClassificationResult:
    """
    Apply classification rules in priority order.

    Parameters
    ----------
    direction         : 'buy' | 'sell' | 'unknown' — from parser direction detection
    raw_nature_text   : raw section-4b text (Natura dell'operazione) from PDF
    unit_price        : extracted unit price (0.0 if not found)
    quantity          : extracted quantity (0.0 if not found)
    parser_type_hint  : transaction_type produced by _DIRECTION_MAP / Altro path
    parse_warnings    : list of warning strings from the parser

    Returns
    -------
    ClassificationResult with transaction_type, economic_intent, rationale
    """
    nature = raw_nature_text or ""

    # ── Rule 1: Zero-price acquisition → grant ────────────────────────────────
    # Zero price + quantity = non-cash transfer → grant.
    # Excluded: types where zero price is expected and not grant-like.
    _ZERO_PRICE_EXPECTED = {
        "inheritance", "gift_in", "gift_out",
        "transfer_in", "transfer_out", "conversion",
        "pledge_or_security", "derivative_transaction",
    }
    _NON_GRANT_NATURE_KEYWORDS = (
        "SUCCESSIONE", "EREDIT", "INHERITANCE", "HEREDIT",
        "DONAZIONE", "DONATION", "GIFT",
        "TRASFERIMENTO", "TRANSFER",
        "CONVERSIONE", "CONVERSION", "PERMUTA",
    )
    if (
        unit_price == 0.0
        and quantity > 0
        and direction != "sell"
        and parser_type_hint not in _ZERO_PRICE_EXPECTED
        and not _contains(nature, *_NON_GRANT_NATURE_KEYWORDS)
    ):
        return _result(
            "grant",
            f"zero_price_grant: unit_price=0.0, qty={quantity:.0f}",
            confidence=0.90,
        )

    # ── Rule 2: Parser already resolved to a specific extended type ───────────
    # If the direction map produced a fine-grained type (not buy/sell/other),
    # trust it — the keyword was unambiguous.
    if parser_type_hint in (
        "grant", "option_exercise", "sell_to_cover",
        "subscription", "conversion", "inheritance",
        "gift_in", "gift_out", "transfer_in", "transfer_out",
        "pledge_or_security", "derivative_transaction",
    ):
        # Special refinement: TRASFERIMENTO keyword defaults to transfer_out
        # but direction=='buy' means it's actually an inbound transfer.
        if parser_type_hint == "transfer_out" and direction == "buy":
            return _result("transfer_in", "direction_buy_transfer: TRASFERIMENTO + buy direction", confidence=0.85)
        # DONAZIONE as buy → gift_in; as sell → gift_out (default from parser)
        if parser_type_hint == "gift_out" and direction == "buy":
            return _result("gift_in", "direction_buy_gift: DONAZIONE + buy direction", confidence=0.85)
        return _result(parser_type_hint, f"parser_keyword: {parser_type_hint}", confidence=0.85)

    # ── Rule 3: Raw nature text keywords (Altro/Other and fallback paths) ─────
    # Applied when the direction map produced 'buy', 'sell', or 'other' and
    # the raw nature text contains a more specific keyword.
    # All matches in this block carry confidence=0.85 (clear textual evidence).

    if _contains(nature, "SUCCESSIONE", "EREDIT", "INHERITANCE", "HEREDIT"):
        return _result("inheritance", "raw_nature_keyword: inheritance signal in section-4b", confidence=0.85)

    if _contains(nature, "SOTTOSCRIZIONE", "SUBSCRIPTION", "RIGHTS ISSUE", "AUMENTO"):
        return _result("subscription", "raw_nature_keyword: subscription/rights issue signal", confidence=0.85)

    if _contains(nature, "CONVERSIONE", "CONVERSION", "CONVERT",
                 "FUSIONE", "SCISSIONE", "MERGER", "DEMERGER"):
        return _result("conversion", "raw_nature_keyword: conversion/merger/demerger signal", confidence=0.85)

    if _contains(nature, "PERMUTA", "EXCHANGE", "SWAP", "SCAMBIO"):
        return _result("conversion", "raw_nature_keyword: permuta/exchange signal", confidence=0.85)

    if _contains(nature, "DONAZIONE", "DONATION", "DONATO", "GIFT"):
        tx_type = "gift_in" if direction == "buy" else "gift_out"
        return _result(tx_type, f"raw_nature_keyword: donation + direction={direction}", confidence=0.85)

    if _contains(nature, "TRASFERIMENTO", "TRANSFER"):
        tx_type = "transfer_in" if direction == "buy" else "transfer_out"
        return _result(tx_type, f"raw_nature_keyword: transfer + direction={direction}", confidence=0.85)

    # Pledge / collateral — not a market transaction; signals encumbrance of shares.
    # Keywords: PEGNO (pledge), GARANZIA (guarantee/collateral), VINCOLO (encumbrance)
    if _contains(nature, "PEGNO", "GARANZIA", "VINCOLO", "PLEDGE", "COLLATERAL", "SECURITY INTEREST"):
        return _result("pledge_or_security", "raw_nature_keyword: pledge/collateral signal", confidence=0.85)

    # Derivative transaction — instrument is a derivative over the issuer's shares.
    if _contains(nature, "DERIVAT", "FUTURES", "FORWARD", "CONTRATTO DERIVATO", "DERIVATIVE"):
        return _result("derivative_transaction", "raw_nature_keyword: derivative instrument signal", confidence=0.85)

    if _contains(nature, "ASSEGNAZIONE", "ASSEGNAZIONE GRATUITA", "ATTRIBUZIONE",
                 "GRATUITO", "AWARD", "ATTRIBUTION"):
        if _contains(nature, "VENDITA", "SELL", "COPERTURA", "COVER"):
            return _result("sell_to_cover", "raw_nature_keyword: grant+sell → sell_to_cover", confidence=0.85)
        return _result("grant", "raw_nature_keyword: assignment/award signal", confidence=0.85)

    # Option exercise — includes Italian incentive plan and rights terminology.
    if _contains(nature, "ESERCIZIO", "EXERCISE", "OPZION", "OPTION", "WARRANT",
                 "DIRITTI DI OPZIONE", "PIANO DI INCENTIVAZIONE", "STOCK OPTION", "STOCK GRANT"):
        return _result("option_exercise", "raw_nature_keyword: option exercise signal", confidence=0.85)

    # Sell-to-cover can appear without an explicit grant keyword when the nature
    # text only says the sale is for coverage/tax purposes.
    if direction == "sell" and _contains(nature, "COPERTURA", "COVER", "FISCALE"):
        return _result("sell_to_cover", "raw_nature_keyword: standalone cover/copertura sell", confidence=0.85)

    # Related-entity vehicle — transaction via subsidiary, trust, or fiduciary.
    # Not necessarily a mechanical event but the beneficial owner chain is indirect;
    # flag for review so the operator can confirm the economic substance.
    if _contains(nature, "SOCIETÀ CONTROLLATA", "CONTROLLATA", "FIDUCIARIA",
                 "FIDUCIARY", "NOMINEE", "TRUST"):
        tx_type = "transfer_in" if direction == "buy" else "transfer_out"
        return _result(
            tx_type,
            f"raw_nature_keyword: related_entity_vehicle + direction={direction}",
            needs_review=True,
            confidence=0.60,
        )

    # ── Rule 4: Direction fallthrough ─────────────────────────────────────────
    # Direction was extracted from a keyword (ACQUISTO/CESSIONE etc.) but no
    # specific extended-type keyword was present in the nature text.
    # Guard: if parse_warnings indicate weak/inferred direction, downgrade to
    # other/unclear rather than asserting confident discretionary intent.
    _WEAK_DIRECTION_SIGNALS = (
        "SI/YES flag",          # direction inferred from option programme, not keyword
        "fallback",             # any parser fallback path
    )
    _direction_is_weak = any(
        any(sig in w for sig in _WEAK_DIRECTION_SIGNALS)
        for w in parse_warnings
    )

    if direction == "buy":
        if _direction_is_weak:
            return _result(
                "unknown",
                "vague_direction: buy inferred from weak signal only",
                needs_review=True,
                confidence=0.40,
            )
        return _result("buy", "direction_fallthrough: buy", confidence=0.65)

    if direction == "sell":
        if _direction_is_weak:
            return _result(
                "unknown",
                "vague_direction: sell inferred from weak signal only",
                needs_review=True,
                confidence=0.40,
            )
        return _result("sell", "direction_fallthrough: sell", confidence=0.65)

    # ── Rule 5: Source wording/type cannot be determined ─────────────────────
    # 'unknown' is distinct from 'other': the event itself is unreadable,
    # not merely outside the principal taxonomy.
    return _result(
        "unknown",
        f"undetermined: direction={direction}, hint={parser_type_hint}",
        needs_review=True,
        confidence=0.30,
    )


def _result(
    tx_type: str,
    rationale: str,
    *,
    needs_review: bool = False,
    confidence: float = 0.5,
) -> ClassificationResult:
    uncertain = tx_type in ("other", "unknown")
    return ClassificationResult(
        transaction_type=tx_type,
        economic_intent=_economic_intent(tx_type),
        rationale=rationale,
        needs_review=needs_review or uncertain,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
    )


# ─── DB override ─────────────────────────────────────────────────────────────

def classify_override(
    client,
    transaction_id: int,
    transaction_type: str,
    economic_intent: str,
    rationale: str,
    *,
    changed_by: str = "operator",
) -> None:
    """
    Override the classification of a transaction with a full audit trail.

    Snapshots the current row into transaction_versions before updating,
    then sets transaction_type, economic_intent, classification_rationale,
    classification_override=True, and classification_overridden_by/at.

    Non-fatal: logs on failure rather than raising.
    """
    from datetime import datetime, timezone

    try:
        current = (
            client.table("transactions")
            .select("*")
            .eq("id", transaction_id)
            .limit(1)
            .execute()
        )
        if not current.data:
            logger.warning("classify_override: transaction %d not found", transaction_id)
            return

        row = current.data[0]

        # Snapshot before change
        try:
            client.table("transaction_versions").insert({
                "transaction_id": transaction_id,
                "version_number": row.get("version_number", 1),
                "snapshot": row,
                "changed_by": changed_by,
                "change_reason": f"classification_override: {rationale}",
            }).execute()
        except Exception as snap_exc:
            logger.warning("classify_override snapshot failed for tx %d: %s", transaction_id, snap_exc)

        # Apply override
        now = datetime.now(timezone.utc).isoformat()
        client.table("transactions").update({
            "transaction_type": transaction_type,
            "economic_intent": economic_intent,
            "classification_rationale": rationale,
            "classification_override": True,
            "classification_overridden_by": changed_by,
            "classification_overridden_at": now,
            "version_number": (row.get("version_number") or 1) + 1,
            "updated_at": now,
        }).eq("id", transaction_id).execute()

        logger.info(
            "classify_override applied to tx %d: %s → %s by %s",
            transaction_id, row.get("transaction_type"), transaction_type, changed_by,
        )

    except Exception as exc:
        logger.error("classify_override failed for tx %d: %s", transaction_id, exc)
