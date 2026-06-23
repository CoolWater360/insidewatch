# Classification Taxonomy

## Overview

Every transaction extracted from a MAR Art 19 filing is assigned a `transaction_type` from the taxonomy below and an `economic_intent` that groups types by whether the insider exercised active discretion.

The classifier (`scraper/classifier.py`) applies rules in priority order and records a `classification_rationale` string explaining which rule produced the outcome.  Operators can override any classification via `classify_override()` or the review CLI, with a full audit trail in `transaction_versions`.

---

## Transaction Types

### Discretionary — insider actively chose to transact

| Type | Italian keyword | Description |
|---|---|---|
| `buy` | ACQUISTO / PURCHASE | Open-market purchase of existing shares |
| `sell` | CESSIONE / VENDITA / SALE | Open-market sale of existing shares |
| `subscription` | SOTTOSCRIZIONE | Subscription to new shares in a rights issue or capital increase |

### Mechanical — automatic, contractual, or externally triggered

| Type | Italian keyword | Description |
|---|---|---|
| `grant` | ASSEGNAZIONE / ATTRIBUZIONE / GRATUITO | Free share award with zero or nominal price (RSU, performance share plan, LTIP) |
| `option_exercise` | ESERCIZIO / EXERCISE | Exercise of stock option or warrant at the contracted strike price |
| `sell_to_cover` | (Altro: ASSEGNAZIONE + VENDITA/COPERTURA) | Selling a portion of vested/exercised shares to cover tax or exercise cost |
| `conversion` | PERMUTA / CONVERSIONE / CONVERSION | Converting one instrument class to another (e.g. convertible bond → equity) |
| `inheritance` | SUCCESSIONE / EREDIT | Acquiring shares through legal succession or inheritance |
| `gift_in` | DONAZIONE (buy direction) | Receiving shares as a gift or charitable transfer |
| `gift_out` | DONAZIONE (sell direction) | Giving away shares as a gift or charitable transfer |
| `transfer_in` | TRASFERIMENTO (buy direction) | Inbound transfer from another portfolio or custody account |
| `transfer_out` | TRASFERIMENTO (sell direction) | Outbound transfer to another portfolio or custody account |

### Unresolved

| Type | Condition | Description |
|---|---|---|
| `other` | No rule matched | Event could not be mapped to any named class; requires manual review |

---

## Economic Intent

| Intent | Types | Meaning |
|---|---|---|
| `discretionary` | buy, sell, subscription | Insider made an active investment decision |
| `mechanical` | grant, option_exercise, sell_to_cover, conversion, inheritance, gift_in, gift_out, transfer_in, transfer_out | Transaction was automatic, contractual, or externally triggered — no active choice |
| `unclear` | other | Intent cannot be determined |

**Signal filter**: only `discretionary` transactions are considered for clustering and alert signals.  Mechanical events are displayed but excluded from buy/sell cluster calculations.

---

## Direction vs. Type

`direction` (buy / sell / unknown) is distinct from `transaction_type`:

- `direction` is a binary buy/sell classification extracted directly from the PDF keyword.
- `transaction_type` is a semantic classification that refines `direction` with additional context.

Example: a TRASFERIMENTO (transfer) is `direction=sell` but `transaction_type=transfer_out` — it is categorically different from an open-market VENDITA even though both have `direction=sell`.

---

## Classification Rationale Tags

The `classification_rationale` column stores a stable tag string identifying which rule produced the classification.  Format: `<rule_name>: <detail>`.

| Tag prefix | Source |
|---|---|
| `zero_price_grant` | Rule 1 — unit_price == 0, direction != sell |
| `parser_keyword` | Rule 2 — direction map produced an extended type directly |
| `direction_buy_transfer` | Rule 2 refinement — transfer_out + buy direction → transfer_in |
| `direction_buy_gift` | Rule 2 refinement — gift_out + buy direction → gift_in |
| `raw_nature_keyword` | Rule 3 — extended type inferred from raw section-4b text |
| `direction_fallthrough` | Rule 4 — direction was the only signal |
| `unclassified` | Rule 5 — no rule matched |
| `operator_correction` | Manual override via `classify_override()` |

---

## Historical Values

Transactions ingested before Phase 7 (parser version < 1.2.0) will have:
- `transaction_type` in the original 6-value set (`buy | sell | grant | option_exercise | sell_to_cover | other`)
- `classification_rationale = NULL`
- `raw_nature_text = NULL`

The Phase 6 migration preserves existing values; no backfill is performed on historical rows.  Re-processing a filing through the Phase 7 parser will populate all three fields.
