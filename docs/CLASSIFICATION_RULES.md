# Classification Rules Engine

## Architecture

The classification pipeline is a two-stage process:

```
PDF text
   │
   ▼
parser.py  ─── _DIRECTION_MAP ──► direction, parser_type_hint
   │                               raw_nature_text (section 4b)
   │
   ▼
classifier.py ─── 5 rules ──────► transaction_type
                                    economic_intent
                                    classification_rationale
```

The parser handles text extraction and keyword matching.  The classifier applies semantic rules and produces the final classification.  This separation means:
- The parser can be regression-tested without DB access (Phase 5 fixtures).
- The classifier is a pure function — fast, deterministic, easily testable.
- Adding a new type requires adding one rule in `classifier.py` only.

---

## Rule Priority

Rules are evaluated in order.  **First match wins.**

### Rule 1 — Zero-price acquisition → grant

**Condition:**  
`unit_price == 0.0 AND quantity > 0 AND direction != 'sell'`  
AND `parser_type_hint` not in `{inheritance, gift_in, gift_out, transfer_in, transfer_out, conversion}`  
AND raw_nature_text does not contain non-grant keywords (SUCCESSIONE, DONAZIONE, TRASFERIMENTO, CONVERSIONE, PERMUTA, …)

**Outcome:** `grant` / `mechanical`

**Rationale:** A zero-price acquisition of a positive quantity is definitionally a non-cash award.  The exclusions prevent reclassifying inheritance or gift events where zero price is also expected.

---

### Rule 2 — Parser keyword hint

**Condition:**  
`parser_type_hint` is one of the extended types:  
`grant | option_exercise | sell_to_cover | subscription | conversion | inheritance | gift_in | gift_out | transfer_in | transfer_out`

**Outcome:** Use `parser_type_hint` directly (with two direction refinements):

- `transfer_out` + `direction == buy` → `transfer_in`  
  A TRASFERIMENTO keyword paired with a buy direction means the insider *received* the transfer.
- `gift_out` + `direction == buy` → `gift_in`  
  A DONAZIONE keyword paired with a buy direction means the insider *received* the gift.

**Rationale:** The parser's `_DIRECTION_MAP` performs exact keyword matching.  When a keyword unambiguously identifies an extended type, the classifier trusts the parser's output.

---

### Rule 3 — Raw nature text keywords

**Condition:**  
Applies when the hint is `buy`, `sell`, or `other` (i.e. the parser did not resolve to an extended type).  The raw section-4b text (Natura dell'operazione) is scanned for semantic keywords.

| Keywords (case-insensitive) | Outcome |
|---|---|
| SUCCESSIONE, EREDIT, INHERITANCE, HEREDIT | `inheritance` |
| SOTTOSCRIZIONE, SUBSCRIPTION, RIGHTS ISSUE, AUMENTO | `subscription` |
| CONVERSIONE, CONVERSION, CONVERT | `conversion` |
| PERMUTA, EXCHANGE, SWAP | `conversion` |
| DONAZIONE, DONATION, DONATO, GIFT | `gift_in` (buy) or `gift_out` (sell) |
| TRASFERIMENTO, TRANSFER | `transfer_in` (buy) or `transfer_out` (sell) |
| ASSEGNAZIONE, ATTRIBUZIONE, GRATUITO, AWARD, ATTRIBUTION (without VENDITA/SELL/COPERTURA) | `grant` |
| ASSEGNAZIONE/ATTRIBUZIONE + VENDITA/SELL/COPERTURA | `sell_to_cover` |
| ESERCIZIO, EXERCISE, OPZION, OPTION, WARRANT | `option_exercise` |

This rule handles "Altro / Other" filings where the PDF uses a non-standard keyword path in section 4b but describes the nature in free text.

---

### Rule 4 — Direction fallthrough

**Condition:**  
`direction == 'buy'` or `direction == 'sell'`

**Outcome:**  
`direction == 'buy'` → `buy` / `discretionary`  
`direction == 'sell'` → `sell` / `discretionary`

---

### Rule 5 — Unclassifiable

**Condition:**  
No previous rule matched (typically `direction == 'unknown'` with no nature text).

**Outcome:** `other` / `unclear`  

These transactions are flagged `needs_review = True` upstream by the parser.

---

## Override Workflow

```python
from scraper.classifier import classify_override

classify_override(
    client,
    transaction_id=12345,
    transaction_type="sell_to_cover",
    economic_intent="mechanical",
    rationale="operator_correction: tax-driven sale post-vesting confirmed",
    changed_by="alice",
)
```

`classify_override()` snapshots the current row into `transaction_versions` before modifying it, sets `classification_override = TRUE`, records `classification_overridden_by` and `classification_overridden_at`, and increments `version_number`.

Overridden transactions are visible in the ops console (Phase 8) and queryable via:

```sql
SELECT * FROM transactions WHERE classification_override = TRUE;
```

---

## Adding a New Type

1. Add the type to the CHECK constraint in a new migration (extend the list in `006_classification.sql`).
2. Add the type to `_DISCRETIONARY` or `_MECHANICAL` in `classifier.py`.
3. Add a Rule 2 or Rule 3 branch in `classify()`.
4. Add fixtures and test cases in `tests/test_classifier.py`.
5. Update `docs/CLASSIFICATION_TAXONOMY.md`.
6. Bump `PARSER_VERSION` in `parser.py`.

---

## Source Preservation

`raw_nature_text` stores the exact text of section 4b (Natura dell'operazione) extracted from the PDF, before any classification is applied.  This allows:

- **Regulator queries**: answer "what did the filing actually say?" without re-downloading the PDF.
- **Re-classification**: apply new rules to historical records without re-parsing PDFs.
- **Dispute resolution**: demonstrate that the classification was derived from a specific source string.

`classification_rationale` stores the rule tag, making classifications auditable and reproducible.
