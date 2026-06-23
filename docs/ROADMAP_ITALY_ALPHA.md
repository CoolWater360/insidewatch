# InsideWatch — Italy Alpha Roadmap

> Objective: Italy-first MAR Article 19 ingestion and research-data platform
> suitable for private institutional pilots.
> Status: private beta — not investment advice.
> Last updated: 2026-06-23

---

## Risk-ranked issue list

The following issues exist in the current codebase, ranked by severity.

| # | Severity | Issue | Phase fix |
|---|---|---|---|
| 1 | CRITICAL | Silent data loss: listener marks failed filings as "seen" regardless of outcome | 2 |
| 2 | CRITICAL | RLS disabled: all tables publicly readable and writable via anon key | 1 |
| 3 | HIGH | `transaction_type = NULL` silently excluded from all frontend queries | 1 |
| 4 | HIGH | No source PDF preservation: evidence lost when Borsa Italiana removes files | 3 |
| 5 | HIGH | Anon key used for scraper writes: enabling RLS would silently break ingestion | 1 |
| 6 | HIGH | `schema.sql` is stale: fresh DB missing 5 columns across 3 tables | 1 |
| 7 | HIGH | No corrections without overwriting: no audit trail for data corrections | 4 |
| 8 | MEDIUM | Weak `raw_hash`: same physical event re-parsed produces duplicate record | 4 |
| 9 | MEDIUM | Issuer resolution via fuzzy name match: creates duplicate company rows | 6 |
| 10 | MEDIUM | No per-filing retry: transient failures not retried systematically | 2 |
| 11 | MEDIUM | Parser uses `float` for price/quantity: rounding errors accumulate | 1 |
| 12 | MEDIUM | `direction` check excludes buy/sell: does not exclude `needs_review` transactions from signals | complete |
| 13 | LOW | `schema.sql` and migrations are out of sync: onboarding a new developer is error-prone | 1 |
| 14 | LOW | GitHub Actions scheduler latency: 1-min cron delivers every 5–15 min at peak | 9 |
| 15 | LOW | No structured logs: failures are visible only in GitHub Actions console | 9 |

---

## Phase overview

### Phase 0 — Repository Audit and Safe Baseline ✅ IN PROGRESS

**Goal:** Document current state without changing behaviour.

**Deliverables:**
- `docs/ARCHITECTURE.md` ✅
- `docs/DATA_MODEL.md` ✅
- `docs/OPERATIONS.md` ✅
- `docs/ROADMAP_ITALY_ALPHA.md` ✅ (this file)

**No code changes. Documentation only.**

---

### Phase 1 — Schema Integrity, Security, and Migration Foundation

**Goal:** Make the database safe enough to trust as the system of record.

**Scope:**
1. Consolidate `schema.sql` with all fields that currently exist only in migration files.
2. Add missing fields to `transactions`: `economic_intent`, `extraction_confidence`, `classification_confidence`, `review_status`, `review_reason`, `source_filing_id`, `source_transaction_index`, `raw_document_sha256`, `parser_version`.
3. Add CHECK constraints for `transaction_type`, `review_status`, `economic_intent`, `direction`.
4. Write idempotent backfill migration for legacy NULL values — do not assume open-market.
5. Fix `lib/queries.ts` null-sensitive filtering (`.neq()` chain).
6. Separate anon key (frontend, read-only) from service-role key (scraper, write).
7. Write RLS policies.
8. Add migration verification script and schema validation test.
9. Add `Decimal` to scraper price/quantity handling.

**Files changed:**
- `db/schema.sql` — consolidate and extend
- `db/migrations/001_schema_integrity.sql` — new
- `lib/queries.ts` — fix filtering
- `lib/supabase.ts` — key separation
- `lib/types.ts` — new fields
- `scraper/models.py` — new fields
- `scraper/db.py` — use Decimal; pass new fields
- `scraper/parser.py` — new fields in ParsedTransaction
- `.env.local.example` — add service-role key

**Manual Supabase dashboard steps required:**
- Enable RLS on all tables
- Copy service_role key into GitHub Secrets
- Set Spend Cap to €0

**Rollback:** `DROP COLUMN` for new columns; revert queries.ts via git; disable RLS if blocking.

**Gate to Phase 2:** Schema validation test passes; no NULL transaction_type in DB; RLS enabled with scraper write working.

---

### Phase 2 — Durable Filing Ledger and Retry-Safe Ingestion

**Goal:** Eliminate silent data loss.

**Scope:**
1. Create `filings` table (source-of-truth ingestion ledger).
2. Rewrite listener to use filing ledger as state machine instead of file cache.
3. Rewrite run_phase2 to log every filing attempt to the ledger.
4. Implement exponential backoff retry for failed filings.
5. Add `python -m scraper.retry_failed_filings`.
6. Add `python -m scraper.reprocess_filing --filing-id <id>`.
7. Add `python -m scraper.inspect_filing --filing-id <id>`.
8. Deprecate `scraper/cache.py` (keep temporarily for compatibility).
9. Add tests for filing lifecycle states.

**Files changed/created:**
- `db/migrations/002_filings_ledger.sql` — new
- `scraper/filing_ledger.py` — new
- `scraper/listener.py` — rewrite
- `scraper/run_phase2.py` — update
- `scraper/retry_failed_filings.py` — new
- `scraper/reprocess_filing.py` — new
- `scraper/inspect_filing.py` — new
- `scraper/cache.py` — mark deprecated
- `tests/test_filing_lifecycle.py` — new

**Gate to Phase 3:** A failed download cannot result in a filing marked complete. Retry queue drains on subsequent runs.

---

### Phase 3 — Raw Document Preservation and Immutable Source Lineage

**Goal:** Preserve evidence; enable future reprocessing.

**Scope:**
1. Create `scraper/storage.py` with upload/download/exists/metadata interface.
2. Implement local-filesystem adapter (development) and Supabase Storage adapter (production).
3. Calculate SHA-256 immediately after download; never overwrite an existing document silently.
4. Store every PDF under deterministic path: `filings/{year}/{month}/{pdf_sha256}.pdf`.
5. Persist `storage_path`, `pdf_sha256`, `file_size_bytes`, `raw_extracted_text`, `parser_version` to `filings`.
6. Add `python -m scraper.verify_document_integrity`.
7. Create `docs/SOURCE_LINEAGE.md` and `docs/STORAGE_OPERATIONS.md`.

**Files changed/created:**
- `scraper/storage.py` — new
- `scraper/filing_ledger.py` — update
- `scraper/verify_document_integrity.py` — new
- `docs/SOURCE_LINEAGE.md` — new
- `docs/STORAGE_OPERATIONS.md` — new

**Manual Supabase step:** Create Storage bucket `filings-pdfs` (private).

**Gate to Phase 4:** Every new filing has a stored PDF with verified SHA-256.

---

### Phase 4 — Transaction Identity, Versioning, and Audit Trail

**Goal:** Prevent duplicate suppression; preserve every correction.

**Scope:**
1. Redesign `raw_hash` → new identity key: `filing_id + pdf_sha256 + source_transaction_index + isin + direction`.
2. Create `transaction_versions` table.
3. Create `filing_processing_runs` table.
4. Implement correction functions (create, correct, supersede, link).
5. Add backend audit retrieval (current + all versions + source filing).
6. Migrate existing transactions to new identity scheme (idempotent).
7. Add tests: same-day same-price dedup, buy/sell non-merge, parser reprocess, manual correction, idempotent re-ingest.

**Files changed/created:**
- `db/migrations/003_transaction_identity.sql` — new
- `db/migrations/004_versioning.sql` — new
- `scraper/db.py` — new identity, versioning functions
- `scraper/parser.py` — add `source_transaction_index`
- `scraper/models.py` — add index field
- `tests/test_transaction_identity.py` — new

**Gate to Phase 5:** Two valid same-day same-price same-insider transactions both stored. Manual correction preserved in version history.

---

### Phase 5 — Parser Regression Suite and Confidence Scoring

**Goal:** Make parser quality measurable.

**Scope:**
1. Create `tests/fixtures/filings/` framework with anonymised PDF fixtures and expected JSON.
2. Implement `run_parser_regression.py` with field-level accuracy reporting.
3. Add deterministic confidence scoring: `extraction_confidence` (field coverage) and `classification_confidence` (direction/type certainty).
4. Store confidence fields in `transactions`.
5. Define and store `review_reason` tags.
6. Add `generate_quality_report.py`.
7. Create `docs/PARSER_METHODOLOGY.md`.

**Files changed/created:**
- `tests/fixtures/filings/` — new
- `tests/test_parser_regression.py` — new
- `scraper/run_parser_regression.py` — new
- `scraper/generate_quality_report.py` — new
- `scraper/parser.py` — add confidence scoring
- `docs/PARSER_METHODOLOGY.md` — new

---

### Phase 6 — Issuer Master and Security Mapping

**Goal:** Eliminate fragile name matching.

**Scope:**
1. Create `issuers`, `issuer_aliases`, `securities`, `issuer_source_mappings` tables.
2. Import Italy universe from Borsa Italiana / ESMA register.
3. Replace `ilike` primary lookup with exact alias lookup.
4. Keep `ilike` as review-suggestion fallback only.
5. Flag unmatched issuers for manual review.
6. Add import tooling.
7. Add review workflow commands.

**Files changed/created:**
- `db/migrations/005_issuer_master.sql` — new
- `scraper/issuer_resolver.py` — new
- `scraper/db.py` — update `_resolve_company()` to use issuer master
- `tests/test_issuer_resolver.py` — new

---

### Phase 7 — Classification and Discretionary Intent Layer

**Goal:** Create the first meaningful data moat.

**Scope:**
1. Implement extended `transaction_type` taxonomy (12 values).
2. Add `economic_intent` field (discretionary/mechanical/unclear).
3. Create documented rules engine (`scraper/classifier.py`).
4. Preserve raw source wording.
5. Store classification rationale.
6. Add internal override capability with version history.
7. Create `docs/CLASSIFICATION_TAXONOMY.md` and `docs/CLASSIFICATION_RULES.md`.

---

### Phase 8 — Internal Operations Console and Manual Review Workflow

**Goal:** Make exceptions manageable without direct DB access.

**Scope:**
1. Build `/internal` Next.js route (protected, no public access).
2. Review queue: failed filings, low-confidence extractions, unmatched issuers, duplicate candidates.
3. Reviewer actions: confirm, correct, reject, flag, re-run parser, map issuer, override classification.
4. Every action creates an audit record in `transaction_versions`.
5. Daily review queue summary email.

**Files changed/created:**
- `app/internal/` — new (protected Next.js routes)
- `app/api/internal/` — new (server actions / API routes for review ops)
- `lib/review.ts` — new

---

### Phase 9 — Latency, Coverage, Monitoring, and Daily Operations

**Goal:** Prove operational reliability with measured data.

**Scope:**
1. Add event timestamps to `filings`: `source_published_utc`, `discovered_utc`, `downloaded_utc`, `parsed_utc`, `validated_utc`, `delivered_utc`.
2. Calculate latency metrics.
3. Build daily operational report (`generate_daily_operations_report.py`).
4. Add `run_health_check.py`.
5. Add authenticated `/api/v1/health` endpoint.
6. Add structured JSON logging to scraper.

---

### Phase 10 — Private Institutional API and Export Layer

**Goal:** Prepare for private design-partner testing.

**Endpoints:**
```
GET /api/v1/transactions
GET /api/v1/transactions/{id}
GET /api/v1/filings/{id}
GET /api/v1/issuers
GET /api/v1/issuers/{id}
GET /api/v1/signals/cluster-buys
GET /api/v1/health
```

**Requirements:** token auth, rate limiting, audit log, pagination, CSV/Parquet export, OpenAPI spec, versioned schema.

**Files created:** `app/api/v1/`, `docs/API_V1.md`, `openapi.yaml`.

---

### Phase 11 — Italy Corporate-Event Context (Buybacks first)

**Goal:** Add one additional event type cleanly.

**Scope:** Issuer buyback / treasury-share notices. Separate source adapter, same filing ledger, same issuer master.

---

### Phase 12 — Pilot Readiness Package

**Goal:** Private institutional discussion materials.

**Deliverables:** `docs/PILOT_DATA_METHODOLOGY.md`, `docs/DATA_DICTIONARY.md`, `docs/CORRECTION_POLICY.md`, `docs/KNOWN_LIMITATIONS.md`, `docs/SECURITY_SUMMARY.md`.

---

## Migration dependency map

```
Phase 1  ──► Phase 2  ──► Phase 3  ──► Phase 4  ──► Phase 5
   │                                       │
   ▼                                       ▼
Phase 6  ──► Phase 7  ──► Phase 8  ──► Phase 9  ──► Phase 10
                                                        │
                                               Phase 11 ◄─┘
                                                   │
                                               Phase 12
```

Phases 5 and 6 can proceed in parallel after Phase 4.
Phase 8 depends on Phase 2 (filing ledger) and Phase 5 (confidence scoring).
Phase 10 depends on Phase 7 (classification) and Phase 9 (health/monitoring).

---

## Files to be changed by phase

| File | P0 | P1 | P2 | P3 | P4 | P5 | P6 | P7 | P8 | P9 | P10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `db/schema.sql` | — | ✏ | — | — | — | — | — | — | — | — | — |
| `lib/queries.ts` | — | ✏ | — | — | ✏ | — | — | ✏ | — | — | — |
| `lib/supabase.ts` | — | ✏ | — | — | — | — | — | — | — | — | — |
| `lib/types.ts` | — | ✏ | ✏ | — | ✏ | ✏ | — | ✏ | — | — | — |
| `scraper/db.py` | — | ✏ | ✏ | ✏ | ✏ | — | ✏ | — | — | — | — |
| `scraper/listener.py` | — | — | ✏ | — | — | — | — | — | — | — | — |
| `scraper/run_phase2.py` | — | — | ✏ | — | — | — | — | — | — | — | — |
| `scraper/parser.py` | — | ✏ | — | — | ✏ | ✏ | — | ✏ | — | — | — |
| `scraper/models.py` | — | ✏ | ✏ | — | ✏ | ✏ | — | ✏ | — | — | — |
| `scraper/cache.py` | — | — | ✏ deprecated | — | — | — | — | — | — | — | — |
| `scraper/alerts.py` | — | — | — | — | — | — | — | — | — | ✏ | ✏ |
| `.github/workflows/scraper.yml` | — | — | ✏ | — | — | — | — | — | — | ✏ | — |
| `.env.local.example` | — | ✏ | — | — | — | — | — | — | — | — | ✏ |

New files per phase: see individual phase sections above.
