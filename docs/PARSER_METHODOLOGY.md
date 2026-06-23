# Parser Methodology

## Overview

`scraper/parser.py` extracts structured transaction data from MAR Art 19 internal-dealing notification PDFs published on Borsa Italiana.  The two public entry points are:

| Function | Input | Use case |
|---|---|---|
| `parse_pdf(pdf_bytes, source_url, filing_date)` | Raw PDF bytes | Production scraper |
| `parse_text(full_text, source_url, filing_date, doc_sha256)` | Pre-extracted text | Regression tests, re-processing |

`parse_pdf` computes the document SHA-256, calls `extract_text` (pdfplumber), then delegates to `parse_text`.  Both return a list of `ParsedTransaction` objects — one per transaction block inside the filing.

---

## PDF Layouts

Three distinct layouts exist in the wild.  All originate from the ESMA standard bilingual (Italian/English) notification form but differ in formatting:

### Layout 1 — older/smaller companies (e.g. Emak)
- Insider name: `Nome: Paolo Cognome: Zambelli`
- Role:         `Ruolo: Persona che esercita funzioni di amministrazione`
- Direction:    standalone line `CESSIONE` between `Natura dell'operazione` and `A norma dell'articolo`
- ISIN:         `ISIN: IT0001237053`
- Prices:       `0.911 EUR 10000` rows (price EUR qty_shares)
- Aggregated:   `Volume aggregato: 34000` / `Prezzo: 0.9066 EUR`

### Layout 2 — larger/newer companies (e.g. Eni)
- Insider name: `a) Nome/First Name EMMA MARCEGAGLIA` (all-caps, may be interleaved — see Degarbling)
- Role:         `PERSONA CHE ESERCITA FUNZIONI DI AMMINISTRAZIONE/PERSON EXERCISING MANAGERIAL RESPONSIBILITIES`
- Direction:    inline `ACQUISTO/PURCHASE` on the `Nature of the` line
- ISIN:         `Identification code XS3262549754`
- Prices:       `98,7590 % 300000 EUR` (price% face_value_EUR) for bonds; `14.20 EUR 2000` for equities

### Layout 3 — compact 2016/523 form
- Insider name: `a) Nome Massimo Guerra` (mixed case)
- Role:         `a) Posizione/qualifica Direttore Generale`
- Direction:    `b) Natura dell'operazione Acquisto` (inline, same line as label)
- ISIN:         `Codice di identificazione IT0005623845`
- Prices:       `DD-MM-YYYY price volume` rows in aggregated section

---

## Field Extraction Pipeline

Each PDF is split into a header section and one or more transaction blocks by the `Operazione - N` / `Operazione/Operation - N` markers.  Header-level fields (company, insider name, role) are extracted once and shared across all blocks.  Per-block fields (ISIN, direction, price/volume, date) are extracted independently.

### Header fields

| Field | Primary pattern | Fallback |
|---|---|---|
| `company_name` | `Mittente del comunicato : NAME` | — |
| `insider_name` | Layout 1: `Nome: X Cognome: Y`; Layout 2: `Nome/First Name X Y`; Layout 3: `a) Nome X Y` | AVVISO subject line |
| `role` | Layout 1: `Ruolo: ...`; Layout 2: `Posizione - PERSONA...`; Layout 3: `Posizione/qualifica ...` | — |

### Per-transaction fields

| Field | Strategy |
|---|---|
| `isin` | Three ISIN patterns (`ISIN:`, `Identification code`, `Codice di identificazione`) |
| `direction` / `transaction_type` | Keyword map (`_DIRECTION_MAP`) on section 4b text; `Altro/Other` description parsing; SI/YES option-programme signal |
| `quantity` / `unit_price` | Aggregated section 4d preferred; fallback: compute weighted average from individual 4c rows |
| `transaction_date` | ISO 8601 after `Data dell'operazione`; fallback: any ISO date; DD-MM-YYYY conversion for Layout 3 |

### Direction keyword map

| Keyword | direction | transaction_type |
|---|---|---|
| ACQUISTO / PURCHASE / SOTTOSCRIZIONE | buy | buy |
| ASSEGNAZIONE / ATTRIBUZIONE / ASSIGNMENT | buy | grant |
| ESERCIZIO / EXERCISE | buy | option_exercise |
| CESSIONE / VENDITA / SALE / TRASFERIMENTO / DONAZIONE / PERMUTA | sell | sell |
| SUCCESSIONE | buy | other |

Zero-price rows (`unit_price == 0` and `total_value == 0` and `quantity > 0`) are reclassified to `transaction_type = "grant"` regardless of the direction keyword.

---

## Two-Column Degarbling

Layout 2 PDFs render Italian text in the left column and English in the right column side-by-side.  pdfplumber reads across rows and interleaves characters: `ALTO/OTHER` → `A L T O / O T H E R`.

`_degarble()` detects runs of 5+ space-separated single uppercase characters and takes every second character (even indices = Italian column).  This is applied selectively to insider name extraction and direction parsing.

---

## Confidence Scoring

### Extraction confidence (`extraction_confidence`)

Additive per-field score (0.0–1.0):

| Condition | Points |
|---|---|
| `insider_name` present and not "Unknown" | +0.25 |
| `company_name` present and not "Unknown" | +0.15 |
| `transaction_date` extracted | +0.25 |
| `quantity > 0` or `transaction_type == "grant"` | +0.15 |
| `unit_price > 0` or `transaction_type == "grant"` | +0.10 |
| `isin` present | +0.10 |
| Each "Could not" / "fallback" warning (max 3) | −0.04 |

Maximum: 1.0 (perfect extraction).  Scores below 0.5 should be investigated.

### Classification confidence (`classification_confidence`)

Subtractive from 1.0:

| Condition | Penalty |
|---|---|
| `direction == "unknown"` | −0.50 |
| direction inferred from SI/YES flag | −0.30 |
| `transaction_type == "other"` | −0.20 |

---

## Review Flags

`needs_review = True` is set when:
- Direction is unknown AND transaction_type is not "grant"
- Direction was inferred from the SI/YES option-programme signal
- Insider name appears truncated (single word, no space)

`review_reason` tags (stable identifiers stored in the database):

| Tag | Meaning |
|---|---|
| `ambiguous_direction` | Could not determine buy/sell |
| `si_yes_inferred` | Direction guessed from option programme flag |
| `truncated_name` | Insider name is a single word |
| `missing_issuer` | Company name could not be extracted |

---

## Version History

| Version | Change |
|---|---|
| `0.0.0` | Pre-Phase-1 legacy records (no parser version recorded) |
| `1.0.0` | Phase 1 — derived fields, confidence scoring, review flags |
| `1.1.0` | Phase 5 — additive confidence scoring, `isin` as scored field, `parse_text()` separation |

---

## Regression Testing

**Fixture-based tests** in `tests/test_parser_regression.py` load JSON fixtures from `tests/fixtures/filings/` and run `parse_text()` without PDF binary files.  Each fixture specifies:

```json
{
  "meta": { "id": "...", "description": "..." },
  "raw_text": "...",
  "source_url": "...",
  "filing_date": "YYYY-MM-DD",
  "expected": {
    "count": 1,
    "transactions": [
      {
        "insider_name": "...",
        "direction": "buy",
        "min_extraction_confidence": 0.85
      }
    ]
  }
}
```

**Runner**: `python3 -m scraper.run_parser_regression` — prints a field-level accuracy table and exits 1 if any fixture fails.

**DB quality report**: `python3 -m scraper.generate_quality_report [--since YYYY-MM-DD]` — queries live transactions and prints confidence distributions, missing field rates, and review flag counts.

---

## Adding Fixtures

1. Copy a new PDF's extracted text into `tests/fixtures/filings/<id>.json`
2. Fill in `expected.transactions` with ground-truth field values from the filing
3. Run `python3 -m scraper.run_parser_regression` — failures reveal which patterns need extending
4. Extend `parser.py` with a new regex pattern, bump `PARSER_VERSION` to `1.x.y`
5. Re-run until all fixtures pass
