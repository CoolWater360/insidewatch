# InsideWatch — Data Model Reference

> Status: Italy Alpha — source-linked, not investment advice.
> Last updated: 2026-06-23

---

## 1. Current tables (live Supabase state)

### 1.1 `companies`

```sql
id              BIGSERIAL PRIMARY KEY
name            TEXT NOT NULL UNIQUE
ticker          TEXT UNIQUE
isin            TEXT UNIQUE
sector          TEXT
ir_internal_dealing_url  TEXT
priority_tier   INT DEFAULT 2          -- 1=FTSE MIB, 2=Mid Cap, 3=rest
created_at      TIMESTAMP DEFAULT NOW()
updated_at      TIMESTAMP DEFAULT NOW()
```

**Issues:**
- `priority_tier` is absent from `db/schema.sql` (added in `migration_phase5.sql`).
- `name` uniqueness is enforced but name normalization is inconsistent — duplicate companies exist under slight name variations (e.g., "Intesa Sanpaolo" vs "Intesa Sanpaolo S.p.A.").
- No `isin` uniqueness guard beyond a UNIQUE constraint — race conditions during concurrent inserts are handled defensively in `db.py` but not guaranteed at DB level.

---

### 1.2 `insiders`

```sql
id                BIGSERIAL PRIMARY KEY
full_name         TEXT NOT NULL
role              TEXT
company_id        BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE
insider_verified  BOOLEAN DEFAULT TRUE   -- False = entity/intermediary, not natural person
role_category     TEXT DEFAULT 'other'   -- executive|board|major_shareholder|related_person|other
created_at        TIMESTAMP DEFAULT NOW()
UNIQUE(full_name, company_id)
```

**Issues:**
- `insider_verified` and `role_category` absent from `db/schema.sql` (added in `migration_phase4.sql`).
- `full_name` uniqueness per company: a single person transacting on behalf of multiple issuer companies will have one record per company (intentional), but the same person at the same company under two slightly different name spellings creates a duplicate.
- Deduplication migration (Phase A) merged known duplicates, but new ingestions can still create them.

---

### 1.3 `transactions`

```sql
id                BIGSERIAL PRIMARY KEY
insider_id        BIGINT NOT NULL REFERENCES insiders(id) ON DELETE CASCADE
company_id        BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE
transaction_date  DATE NOT NULL
filed_date        DATE
direction         TEXT CHECK (direction IN ('buy', 'sell', 'unknown'))
transaction_type  TEXT DEFAULT 'buy'    -- buy|sell|grant|option_exercise|sell_to_cover|other
needs_review      BOOLEAN DEFAULT FALSE
instrument_type   TEXT
isin              TEXT
quantity          NUMERIC NOT NULL
unit_price        NUMERIC NOT NULL
total_value       NUMERIC NOT NULL
currency          TEXT DEFAULT 'EUR'
source_url        TEXT
raw_hash          TEXT NOT NULL UNIQUE
created_at        TIMESTAMP DEFAULT NOW()
updated_at        TIMESTAMP DEFAULT NOW()
```

**Canonical schema gap:** `transaction_type` and `needs_review` are absent from `db/schema.sql` but present in the live database via `migration_phase1.sql`. A fresh schema install would silently omit them.

**Known issues with `raw_hash`:**

The current hash function:
```python
SHA-256( f"{insider_name}|{company}|{tx_date}|{quantity}|{unit_price}" )
```

This hash:
- Does **not** include the source document hash.
- Does **not** include filing identity or transaction index within the filing.
- Does **not** include `direction` — a buy and a sell with identical insider/company/date/qty/price produce the same hash (pathological but possible for sell-to-cover pairs).
- Is **parse-output-dependent**: re-parsing the same PDF with a corrected parser may produce a different quantity or price, generating a new hash and a duplicate record instead of overwriting.
- Cannot identify the same physical event across two parse runs with different output.

**Correct identity design (Phase 4):**
```
filing_id + document_sha256 + source_transaction_index + isin + direction
```

**Missing fields (added in Phase 1):**
- `economic_intent` — discretionary | mechanical | unclear
- `extraction_confidence` — 0.0–1.0 float
- `classification_confidence` — 0.0–1.0 float
- `review_status` — pending_review | under_review | confirmed | rejected | corrected
- `review_reason` — TEXT (structured tag: e.g. `ambiguous_direction`, `missing_issuer`)
- `source_filing_id` — FK to future `filings` table
- `source_transaction_index` — integer position within the PDF
- `raw_document_sha256` — SHA-256 of the PDF bytes at ingestion time
- `parser_version` — string, e.g. `"1.0.0"`

---

### 1.4 `scraper_runs`

```sql
tier                  INT PRIMARY KEY        -- 1|2|3
last_successful_run   TIMESTAMP
companies_crawled     INT DEFAULT 0
transactions_inserted INT DEFAULT 0
updated_at            TIMESTAMP DEFAULT NOW()
```

This table provides coarse-grained monitoring only. It does not track per-filing status, failures, latency, or retry counts. Replaced by `filings` in Phase 2 as the authoritative ingestion ledger.

---

## 2. Tables to be created

### 2.1 `filings` (Phase 2)

Source-of-truth ledger for every filing discovered. A filing record is created when a PDF URL is discovered and not marked complete until every step of the pipeline has succeeded.

```sql
id                    BIGSERIAL PRIMARY KEY
source_name           TEXT NOT NULL DEFAULT 'borsa_italiana'
source_url            TEXT NOT NULL
issuer_name_as_published  TEXT
source_published_utc  TIMESTAMPTZ        -- date shown on listing page
discovered_utc        TIMESTAMPTZ NOT NULL DEFAULT NOW()
downloaded_utc        TIMESTAMPTZ
parsed_utc            TIMESTAMPTZ
validated_utc         TIMESTAMPTZ
completed_utc         TIMESTAMPTZ
status                TEXT NOT NULL DEFAULT 'discovered'
  CHECK (status IN ('discovered','downloaded','parsing','parsed',
                    'review_required','complete','retry_pending','failed'))
attempt_count         INT NOT NULL DEFAULT 0
next_retry_utc        TIMESTAMPTZ
last_error            TEXT
storage_path          TEXT               -- Supabase Storage / local path
content_type          TEXT DEFAULT 'application/pdf'
file_size_bytes       BIGINT
pdf_sha256            TEXT               -- SHA-256 hex of raw bytes
raw_extracted_text    TEXT               -- full pdfplumber output
parser_version        TEXT
created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
UNIQUE(source_url, pdf_sha256)           -- same URL with different content = new version
```

### 2.2 `transaction_versions` (Phase 4)

Immutable append-only history of every transaction state, keyed to the parent transaction row.

```sql
id                    BIGSERIAL PRIMARY KEY
transaction_id        BIGINT NOT NULL REFERENCES transactions(id)
version_number        INT NOT NULL
snapshot              JSONB NOT NULL     -- full field snapshot at this version
parser_version        TEXT
classification_version TEXT
changed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
change_reason         TEXT
change_origin         TEXT NOT NULL      -- parser|reviewer|migration|manual_correction
changed_by            TEXT               -- reviewer identifier or 'system'
UNIQUE(transaction_id, version_number)
```

### 2.3 `filing_processing_runs` (Phase 4)

Tracks each time a filing is processed (initial parse, re-parse with new parser version).

```sql
id                    BIGSERIAL PRIMARY KEY
filing_id             BIGINT NOT NULL REFERENCES filings(id)
parser_version        TEXT NOT NULL
started_at            TIMESTAMPTZ NOT NULL
completed_at          TIMESTAMPTZ
transactions_created  INT DEFAULT 0
transactions_updated  INT DEFAULT 0
warnings              JSONB              -- array of warning strings
error                 TEXT
```

### 2.4 `issuers` / `issuer_aliases` / `securities` / `issuer_source_mappings` (Phase 6)

Master security reference tables — see Phase 6 specification.

---

## 3. `transaction_type` taxonomy

Current values (defined in `scraper/parser.py`):
```
buy             Open-market purchase
sell            Open-market sale
grant           Free share assignment (zero price)
option_exercise Exercise of option or warrant
sell_to_cover   Sell-to-cover (automatic sale to fund tax on vested shares)
other           Any other type, or Altro/Other with unrecognised description
```

Extended taxonomy (Phase 7):
```
open_market_buy
open_market_sell
sell_to_cover
option_exercise
share_grant
vesting
transfer
inheritance_or_donation
pledge_or_security
derivative_transaction
other
unknown
```

---

## 4. `economic_intent` field (Phase 1 addition, Phase 7 full implementation)

```
discretionary   Active decision by the insider (open-market buy/sell)
mechanical      Automatic/contractual (grant, option exercise, sell-to-cover, vesting)
unclear         Cannot be determined from source data
```

This field drives signal logic and is separate from `transaction_type`. It must never suppress raw events from storage.

---

## 5. `direction` vs `transaction_type` vs `economic_intent`

These three fields are often confused:

| Field | Question answered | Example |
|---|---|---|
| `direction` | Cash flow direction | `buy` (money out, shares in) |
| `transaction_type` | Mechanism of the transaction | `option_exercise` |
| `economic_intent` | Was this a free choice? | `mechanical` |

A sell-to-cover has `direction=sell`, `transaction_type=sell_to_cover`, `economic_intent=mechanical`. It should be stored but excluded from discretionary signal logic.

---

## 6. Known null-handling bugs in frontend queries

`lib/queries.ts` uses:
```typescript
.neq("transaction_type", "grant")
.neq("transaction_type", "option_exercise")
```

In PostgREST, `.neq()` generates `transaction_type <> 'grant'`. The expression `NULL <> 'grant'` evaluates to `NULL` (not `TRUE`), so **rows where `transaction_type IS NULL` are silently excluded** from all frontend queries.

This affects any legacy records inserted before `migration_phase1.sql` was applied, or records where `transaction_type` defaulted to NULL rather than `'buy'`.

**Fix (Phase 1):** Replace chained `.neq()` with an explicit inclusive filter:
```typescript
.in("transaction_type", ["buy", "sell", "other", "sell_to_cover", null])
// or equivalently:
.not("transaction_type", "in", '("grant","option_exercise")')
// with explicit NULL handling via .or()
```

---

## 7. `raw_hash` collision risks — documented cases

| Scenario | Risk |
|---|---|
| Same insider, same company, same day, same qty, different direction | Buy and sell collide (same hash). Buy is inserted first, sell is silently skipped. |
| Parser bug fix changes extracted quantity/price | Re-parse produces new hash → duplicate record rather than correction |
| Two legitimate transactions, same insider, same day, same exact price and qty | Second transaction silently deduped even though it is a distinct event |

**Mitigation in Phase 4:** new identity key = `filing_id + pdf_sha256 + source_transaction_index + isin + direction`.

---

## 8. Data flow and field origins

```
Borsa Italiana listing page
  └─ filing_date (listing column 2, DD/MM/YYYY)
  └─ source_url  (PDF link, extracted via _extract_pdf_path())

PDF document
  └─ company_name     ← Mittente del comunicato
  └─ insider_name     ← Layout 1: Nome/Cognome | Layout 2: Nome/First Name
  └─ role             ← Layout 1: Ruolo | Layout 2: Posizione
  └─ isin             ← ISIN: / Identification code
  └─ instrument_type  ← Description of the / tipo di
  └─ direction        ← section 4b keywords
  └─ transaction_type ← inferred from direction keyword
  └─ quantity         ← section 4c/4d Volume aggregato
  └─ unit_price       ← section 4c/4d Prezzo
  └─ currency         ← EUR / % (bonds)
  └─ transaction_date ← section 4e Data dell'operazione
  └─ total_value      ← quantity × unit_price (computed)
  └─ raw_hash         ← SHA-256(insider|company|date|qty|price)
  └─ needs_review     ← bool: unknown direction / SI-YES fallback / single-word name
  └─ parse_warnings   ← list of strings (not persisted to DB currently)
```

---

## 9. Migration dependency order

```
schema.sql                     ← base schema (must be applied first)
migration_phase1.sql           ← transaction_type, needs_review
migration_phase4.sql           ← insider_verified, role_category
migration_phase5.sql           ← priority_tier, scraper_runs
migration_phase_a.sql          ← dedup insiders, normalize names
── future ──
migrations/001_schema_integrity.sql    (Phase 1)
migrations/002_filings_ledger.sql      (Phase 2)
migrations/003_transaction_identity.sql (Phase 4)
migrations/004_versioning.sql          (Phase 4)
migrations/005_issuer_master.sql       (Phase 6)
migrations/006_classification.sql      (Phase 7)
```

Each future migration must be idempotent (`IF NOT EXISTS`, `IF NOT EXISTS`, `ON CONFLICT DO NOTHING`) and reversible with a documented rollback.
