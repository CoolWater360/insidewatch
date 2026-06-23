"""
Transaction classification rules engine — Phase 7.

Takes raw extracted fields from the parser and produces a final
transaction_type (from the extended 13-value taxonomy), economic_intent,
and a human-readable classification_rationale.

Design principles:
  - Rules are applied in strict priority order; first match wins.
  - Every outcome records a rationale tag so classifications are auditable.
  - No DB access — pure function; caller decides what to persist.
  - The parser's direction detection is treated as an authoritative hint;
    the classifier only overrides it when a stronger signal is present.

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
    transaction_type: str   # one of the 13 taxonomy values
    economic_intent: str    # discretionary | mechanical | unclear
    rationale: str          # "<rule>: <detail>" — stored in DB


# ─── Economic intent mapping ─────────────────────────────────────────────────

# Transactions where the insider exercised active discretion.
_DISCRETIONARY = {"buy", "sell", "subscription"}

# Transactions driven by contract, plan, or external event — not active choice.
_MECHANICAL = {
    "grant", "option_exercise", "sell_to_cover",
    "conversion", "inheritance", "gift_in", "gift_out",
    "transfer_in", "transfer_out",
}


def _economic_intent(transaction_type: str) -> str:
    if transaction_type in _DISCRETIONARY:
        return "discretionary"
    if transaction_type in _MECHANICAL:
        return "mechanical"
    return "unclear"


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
        )

    # ── Rule 2: Parser already resolved to a specific extended type ───────────
    # If the direction map produced a fine-grained type (not buy/sell/other),
    # trust it — the keyword was unambiguous.
    if parser_type_hint in (
        "grant", "option_exercise", "sell_to_cover",
        "subscription", "conversion", "inheritance",
        "gift_in", "gift_out", "transfer_in", "transfer_out",
    ):
        # Special refinement: TRASFERIMENTO keyword defaults to transfer_out
        # but direction=='buy' means it's actually an inbound transfer.
        if parser_type_hint == "transfer_out" and direction == "buy":
            return _result("transfer_in", "direction_buy_transfer: TRASFERIMENTO + buy direction")
        # DONAZIONE as buy → gift_in; as sell → gift_out (default from parser)
        if parser_type_hint == "gift_out" and direction == "buy":
            return _result("gift_in", "direction_buy_gift: DONAZIONE + buy direction")
        return _result(parser_type_hint, f"parser_keyword: {parser_type_hint}")

    # ── Rule 3: Raw nature text keywords (Altro/Other and fallback paths) ─────
    # Applied when the direction map produced 'buy', 'sell', or 'other' and
    # the raw nature text contains a more specific keyword.

    if _contains(nature, "SUCCESSIONE", "EREDIT", "INHERITANCE", "HEREDIT"):
        return _result("inheritance", f"raw_nature_keyword: inheritance signal in section-4b")

    if _contains(nature, "SOTTOSCRIZIONE", "SUBSCRIPTION", "RIGHTS ISSUE", "AUMENTO"):
        return _result("subscription", "raw_nature_keyword: subscription/rights issue signal")

    if _contains(nature, "CONVERSIONE", "CONVERSION", "CONVERT"):
        return _result("conversion", "raw_nature_keyword: conversion signal")

    if _contains(nature, "PERMUTA", "EXCHANGE", "SWAP"):
        return _result("conversion", "raw_nature_keyword: permuta/exchange signal")

    if _contains(nature, "DONAZIONE", "DONATION", "DONATO", "GIFT"):
        tx_type = "gift_in" if direction == "buy" else "gift_out"
        return _result(tx_type, f"raw_nature_keyword: donation + direction={direction}")

    if _contains(nature, "TRASFERIMENTO", "TRANSFER"):
        tx_type = "transfer_in" if direction == "buy" else "transfer_out"
        return _result(tx_type, f"raw_nature_keyword: transfer + direction={direction}")

    if _contains(nature, "ASSEGNAZIONE", "ATTRIBUZIONE", "GRATUITO", "AWARD", "ATTRIBUTION"):
        if _contains(nature, "VENDITA", "SELL", "COPERTURA", "COVER"):
            return _result("sell_to_cover", "raw_nature_keyword: grant+sell → sell_to_cover")
        return _result("grant", "raw_nature_keyword: assignment/award signal")

    if _contains(nature, "ESERCIZIO", "EXERCISE", "OPZION", "OPTION", "WARRANT"):
        return _result("option_exercise", "raw_nature_keyword: option exercise signal")

    # ── Rule 4: Direction fallthrough ─────────────────────────────────────────
    if direction == "buy":
        return _result("buy", "direction_fallthrough: buy")
    if direction == "sell":
        return _result("sell", "direction_fallthrough: sell")

    # ── Rule 5: Unclassifiable ────────────────────────────────────────────────
    return _result(
        "other",
        f"unclassified: direction={direction}, hint={parser_type_hint}",
    )


def _result(tx_type: str, rationale: str) -> ClassificationResult:
    return ClassificationResult(
        transaction_type=tx_type,
        economic_intent=_economic_intent(tx_type),
        rationale=rationale,
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
