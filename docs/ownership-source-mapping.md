# Phase 17B — Ownership Source Field Mapping

Phase 17B Preparation · Schema readiness + source validation  
Status: **DRAFT — research pass, pre-implementation**  
Date: 2026-06-29

---

## 1. Scope

This document maps observable fields from official Italian major-shareholding
notifications (CONSOB Modulo 103A-equivalent disclosures) to columns in the
Phase 17A schema tables:

- `context_sources` (one row per source document)
- `entities` (one row per declarant / holding vehicle)
- `ownership_events` (one row per threshold-crossing event)

The mapping is based on the fields known to appear in CONSOB partecipazioni
notifications. Exact field names and structure must be confirmed against live
portal data before implementation (see [ownership-source-assessment.md](ownership-source-assessment.md) §5).

---

## 2. `context_sources` mapping

One `context_sources` row is created per unique notification document URL
(the canonical PDF or notification page URL, not the search result page).

| `context_sources` column | Source | Confidence | Notes |
|--------------------------|--------|-----------|-------|
| `source_url` | Direct URL to the notification document or page | `parsed_fact` | Natural identity; UNIQUE constraint; must be the direct link, not a redirect |
| `source_type` | Always `'regulatory_filing'` for CONSOB notifications | `parsed_fact` | Hard-coded in pilot collector |
| `publisher` | Always `'CONSOB'` | `parsed_fact` | Hard-coded |
| `document_title` | HTML page title or PDF document name | `parsed_fact` | May be absent; extract from `<title>` tag or PDF metadata |
| `publication_timestamp` | Notification date shown on CONSOB portal | `parsed_fact` | ISO 8601 UTC; source shows date only — set to 00:00:00 UTC |
| `discovered_timestamp` | Set by DB default `NOW()` | — | Never set by collector |
| `document_hash` | SHA-256 of raw downloaded bytes | `parsed_fact` | Compute before parsing; store in hex; used for dedup on re-run |
| `storage_path` | Internal path where PDF is stored | `parsed_fact` | INTERNAL — never expose via API; format: `context/<year>/<month>/<sha256>.pdf` |
| `raw_text` | Full extracted text from PDF | `parsed_fact` | INTERNAL — never expose via API; store for re-parse and audit |
| `issuer_id` | FK to `issuers` resolved from ISIN on notification | `parsed_fact` when ISIN exact-match | NULL if ISIN not in issuers master; enter review queue |
| `ingestion_run_id` | Pilot run identifier (e.g. `pilot-2026-06-29-<issuer>`) | — | Set by caller |

---

## 3. `entities` mapping

One `entities` row is created (or resolved) for each declarant name and each
distinct holding vehicle name appearing in the notification. Entity rows persist
across issuers and sources; deduplication requires human review unless LEI is
available for exact match.

### 3.1 Declarant entity (the filer)

| `entities` column | Source | Confidence | Notes |
|-------------------|--------|-----------|-------|
| `legal_name` | Declarant name verbatim from notification form | `parsed_fact` | Preserve exact legal form (S.p.A., S.r.l., Ltd, natural person full name) |
| `short_name` | Operator-assigned abbreviation | `operator_entry` | Leave NULL on insert; operator fills via review UI |
| `entity_type` | Inferred from name suffix | `heuristic_suggestion` | `company` if name contains S.p.A./S.r.l./Ltd; `natural_person` if no legal suffix; else `other`. Must be confirmed by reviewer |
| `jurisdiction` | Inferred from entity name / context | `heuristic_suggestion` | Default `'IT'`; change if name or domicile hints at foreign jurisdiction |
| `lei` | Not directly on CONSOB notification — must be sourced from GLEIF | — | NULL on insert; populate if GLEIF lookup matches unambiguously |
| `issuer_id` | Set if the declarant entity is also a listed issuer | `parsed_fact` when ISIN match | Rare; only when a listed company holds a stake in another listed company |
| `insider_id` | Set if the declarant matches a known MAR insider | `heuristic_suggestion` | Normalized name similarity; must be confirmed by reviewer before `reviewer_confirmed` |
| `review_status` | Always `'pending_review'` on insert | — | Operator confirms or rejects; never auto-confirm without LEI/ISIN exact match |

### 3.2 Holding vehicle entity (intermediary, when indirect holding disclosed)

Same mapping as §3.1. Additional notes:
- `entity_type` is typically `holding_company`, `company`, `trust`, `fund`, or `nominee`
- `raw_vehicle_name` on `ownership_events` preserves the verbatim name even when
  `holding_vehicle_entity_id` is NULL (unresolved)

### 3.3 Entity resolution rules (matches data model §7.3)

| Condition | Action |
|-----------|--------|
| LEI match exact (20-char GLEIF code) | Set `entity_id` FK; `confidence = 'parsed_fact'` |
| Normalized `legal_name` exact match in `entities.legal_name` | Set `entity_id` FK; `confidence = 'parsed_fact'` |
| ILIKE / fuzzy name match | Set `entity_id` FK; `confidence = 'heuristic_suggestion'`; `review_status = 'pending_review'` |
| No match | Leave `entity_id = NULL`; set `raw_entity_name`; insert new entity row with `review_status = 'pending_review'` |

---

## 4. `ownership_events` mapping

One `ownership_events` row is created per threshold-crossing event per declarant
as disclosed in the notification. Correction notifications produce a
`'cancellation'` row against the original plus a new corrected row (linked via
`superseded_by`).

| `ownership_events` column | Source | Confidence | Notes |
|--------------------------|--------|-----------|-------|
| `issuer_id` | ISIN on notification → `issuers.id` via `securities` or `issuer_aliases` | `parsed_fact` when ISIN exact-match | NOT NULL required; block insert if ISIN not resolved; enter operator queue |
| `entity_id` | Resolved from declarant name (see §3.3) | `parsed_fact` / `heuristic_suggestion` | NULL when unresolved |
| `raw_entity_name` | Verbatim declarant name from notification | `parsed_fact` | Always populate regardless of `entity_id` resolution state |
| `holding_vehicle_entity_id` | Resolved from holding vehicle name (indirect holding) | `parsed_fact` / `heuristic_suggestion` | NULL when not disclosed or unresolved |
| `raw_vehicle_name` | Verbatim holding vehicle name from notification | `parsed_fact` | Populate when indirect holding is disclosed; NULL for direct-only |
| `event_type` | Derived from stake change direction vs. threshold | `parsed_fact` | See §4.1 for classification rules |
| `event_date` | Date the threshold crossing occurred (NOT the filing date) | `parsed_fact` | Required field on Modulo 103A; format YYYY-MM-DD |
| `stake_pct_before` | Previous percentage as stated in notification | `parsed_fact` | Numeric; NULL if not stated (initial disclosures) |
| `stake_pct_after` | New percentage as stated in notification | `parsed_fact` | Numeric; required |
| `voting_pct_before` | Previous voting rights % if separately disclosed | `parsed_fact` | NULL when same as economic stake or not stated |
| `voting_pct_after` | New voting rights % if separately disclosed | `parsed_fact` | NULL when same as economic stake or not stated |
| `direct_or_indirect` | Nature of holding from notification form | `parsed_fact` | Map form values: diretto→`'direct'`, indiretto→`'indirect'`, diretto e indiretto→`'both'` |
| `source_id` | FK to `context_sources` row for this notification | `parsed_fact` | NOT NULL; always set from `upsert_context_source()` return value |
| `evidence_text` | Verbatim key passage from the notification | `parsed_fact` | Include declarant name, issuer, percentages, and date; limit to 500 chars |
| `confidence` | `'parsed_fact'` for machine-extracted fields | — | Do NOT set `'reviewer_confirmed'` automatically; requires operator |
| `is_current` | Always `TRUE` on insert | — | Set `FALSE` via `supersede_context_event()` on correction |
| `version_number` | Always `1` on insert | — | Incremented by `supersede_context_event()` |
| `review_status` | Always `'pending_review'` on insert | — | Operator review required before any downstream use |

### 4.1 `event_type` classification rules

The `ownership_events.event_type` CHECK constraint (migration 018) allows:

```
threshold_crossing_up | threshold_crossing_down | initial_disclosure |
cancellation | change_in_nature | pledge | pledge_release | other
```

Classification algorithm for the pilot collector:

```python
def classify_event_type(
    stake_pct_before: float | None,
    stake_pct_after: float,
    notification_type_raw: str,   # raw field from notification form if available
) -> str:
    # Correction / cancellation notifications
    if "cancell" in notification_type_raw.lower() or "correct" in notification_type_raw.lower():
        return "cancellation"
    # Pledge-specific notifications
    if "pegno" in notification_type_raw.lower() or "pledge" in notification_type_raw.lower():
        return "pledge"
    if "svinc" in notification_type_raw.lower():   # svincolo = release
        return "pledge_release"
    # Initial disclosure (no prior percentage stated)
    if stake_pct_before is None:
        return "initial_disclosure"
    # Direction of crossing
    if stake_pct_after > stake_pct_before:
        return "threshold_crossing_up"
    if stake_pct_after < stake_pct_before:
        return "threshold_crossing_down"
    # No direction change (change in nature of holding)
    return "change_in_nature"
```

This algorithm should be called after all percentage fields are parsed.
In ambiguous cases, default to `'other'` and flag for operator review via
`review_status = 'pending_review'`.

---

## 5. Fields NOT mapped (excluded from pilot)

The following fields appear in some CONSOB notifications but are not captured
in the pilot due to ambiguity, absence from the schema, or strict data quality
requirements.

| Source field | Reason for exclusion |
|-------------|---------------------|
| Ultimate beneficial owner (UBO) | Not required on CONSOB Modulo 103A for all notification types; inferring from indirect chain without explicit disclosure violates §7.5 of the data model |
| Control chain percentages beyond one holding vehicle | Requires chain reconstruction from multiple filings — deferred to Phase 17C (entity_relationships) |
| Investment purpose / strategic intent | Subjective; not a mandatory structured field; constitutes an inferred motive — prohibited under data model §7.5 |
| Declarant's codice fiscale | Sensitive personal identifier; not stored as a structured column per data model §7.5; may appear in `raw_text` |
| Number of shares (absolute) | Not always available on HTML listing; present in PDF — defer to PDF parser phase; `stake_pct_after` is sufficient for pilot |
| Nominee disclosure (Article 12 proxy) | Complex multi-party scenarios; not in MVP |
| ESMA notification ID | Not always exposed on CONSOB portal; add in a later phase |

---

## 6. Pilot data flow: end-to-end

```
CONSOB partecipazioni portal
        │
        │  1. Per-issuer search (ISIN query)
        │     → HTML listing of notifications for that issuer
        │
        ▼
  HTML listing parse
        │
        │  2. Extract: notification date, declarant name,
        │     issuer ISIN, %, link to document
        │
        ▼
  Document fetch (PDF or HTML)
        │
        │  3. Download document bytes
        │     → compute SHA-256 hash
        │     → extract text (pdfplumber or equivalent)
        │
        ▼
  upsert_context_source()
        │
        │  4. source_url UNIQUE → find or create context_sources row
        │     Store: source_url, source_type='regulatory_filing',
        │     publisher='CONSOB', document_hash, raw_text, issuer_id
        │     Returns: source_id (int)
        │
        ▼
  Entity resolution
        │
        │  5. Normalize declarant name
        │     → query entities.legal_name (LOWER() index lookup)
        │     → exact match → entity_id (parsed_fact)
        │     → fuzzy match → entity_id (heuristic_suggestion, pending review)
        │     → no match → INSERT entities (pending_review)
        │
        ▼
  assert_source_provenance()
        │
        │  6. Validate source_id present, confidence valid
        │     Raises ValueError if violated — blocks INSERT
        │
        ▼
  INSERT ownership_events
        │
        │  7. Map fields per §4 above
        │     review_status = 'pending_review'
        │     confidence = 'parsed_fact' (for parser-extracted fields)
        │
        ▼
  Context event link (DEFERRED)
        │
        │  8. Link to transactions will be created in a later phase
        │     after pilot data is operator-reviewed
```

---

## 7. Three-issuer pilot recommendations

The pilot must target exactly three issuers to validate the ingestion pipeline
without activating the Ownership UI for un-reviewed data. The following three
issuers are recommended based on distinct ownership archetypes, expected data
availability on CONSOB, and relevance to InsideWatch's analytical goals.

### 7.1 Pilot Issuer A — Family-controlled (simple structure)

**Recommended: De' Longhi S.p.A.**  
ISIN: IT0003115950 · Ticker: DLG · Euronext Milan  
Lei (to verify): —  
Sector: Consumer electronics / household appliances

**Why:** De' Longhi is controlled through De' Longhi Partecipazioni S.r.l.
(the De' Longhi family holding vehicle). The control structure is stable,
relatively simple (one main holding vehicle, one family block), and well-
documented in CONSOB filings. The company reports insider transactions
frequently, making the correlation between ownership context and insider
activity directly testable.

**Expected CONSOB history:** Notifications from De' Longhi Partecipazioni
whenever the family stake fluctuates (share buybacks, capital increases).
Small number of declarants (1–3 entities). Low parsing complexity.

**Validation check:** Run CONSOB partecipazioni search for ISIN IT0003115950.
Count of notifications should be in the range of 10–50 for the past 5 years.

---

### 7.2 Pilot Issuer B — Contested / multi-shareholder (complex structure)

**Recommended: Mediobanca S.p.A.**  
ISIN: IT0000062957 · Ticker: MB · Euronext Milan  
Sector: Financial services / investment banking

**Why:** Mediobanca has a long-running, publicly documented contested
shareholder structure involving multiple large blocks:
- Fininvest S.p.A. (Berlusconi family)
- Caltagirone Editore / Francesco Gaetano Caltagirone
- Delfin S.à r.l. (Del Vecchio / Luxottica family holding)
- Assicurazioni Generali (cross-shareholding)
These are among Italy's most actively disclosed ownership positions, with
a high density of CONSOB notifications over the past five years.

**Why this is a good pilot stress test:** Multiple declarants, indirect
holdings through foreign vehicles (Luxembourg S.à r.l. → Italian S.p.A.),
concert party dynamics (explicitly disclosed), and correction notifications.
If the pilot pipeline handles Mediobanca correctly, it handles most Italian
complexity.

**Expected CONSOB history:** 50–200+ notifications in the past 5 years.
Multiple distinct entities per notification cycle.

**Validation check:** Run CONSOB search for ISIN IT0000062957. Confirm at
least 3 distinct declarant names in the result set.

---

### 7.3 Pilot Issuer C — Institutional / State-adjacent (clear disclosed structure)

**Recommended: Italgas S.p.A.**  
ISIN: IT0005211237 · Ticker: IG · Euronext Milan  
Sector: Gas distribution utilities

**Why:** Italgas is approximately 26% owned by Cassa Depositi e Prestiti
(CDP) through CDP Reti S.r.l., with Snam S.p.A. holding approximately 13%.
Both stakes are precisely at or near disclosure thresholds, generating
regular notification updates. The CDP / Snam control structure is publicly
documented and comparatively easy to resolve (both entities have LEIs,
both are listed companies themselves, enabling direct `issuer_id` FK links).

**Why this is valuable for the pilot:**  
- Tests the `entity.issuer_id` FK path (where the declarant is itself a
  listed issuer — Snam S.p.A. is listed as well as being a declarant)
- Tests institutional (non-family) ownership context
- Regular stake changes due to buybacks provide a stream of test events

**Expected CONSOB history:** 20–80 notifications, dominated by CDP and Snam.

**Validation check:** Run CONSOB search for ISIN IT0005211237. Confirm CDP
Reti or Cassa Depositi e Prestiti appears as a declarant.

---

## 8. Pilot collector architecture

### 8.1 Module structure

```
scraper/
  ownership/
    __init__.py
    collector.py          # top-level entry point: run for one issuer
    consob_client.py      # HTTP session, robots.txt compliance, rate limiting
    listing_parser.py     # parse HTML search result page → list of NotificationRef
    document_fetcher.py   # download and hash PDF or HTML document
    notification_parser.py # parse individual notification → OwnershipNotification
    entity_resolver.py    # look up / create entities rows; return entity_id
    dry_run.py            # dry-run mode: print what would be written without writing
```

The module is standalone — it does not modify or import from `scraper/parser.py`
or `scraper/db.py` (the existing transaction scraper). It shares only:
- `scraper/context_db.py` — `upsert_context_source`, `assert_source_provenance`,
  `supersede_context_event`
- `scraper/db.py` — `get_supabase_client` (same service-role key)

### 8.2 Data classes

```python
# scraper/ownership/collector.py

@dataclass
class NotificationRef:
    """Reference extracted from CONSOB search result listing."""
    issuer_isin: str
    notification_date: str        # YYYY-MM-DD
    declarant_name_raw: str       # verbatim from listing HTML
    document_url: str             # direct link to PDF or HTML notification
    pct_after: float | None       # percentage after, if available in listing

@dataclass
class OwnershipNotification:
    """Fully parsed notification, ready for DB insert."""
    # Source provenance
    document_url: str
    document_hash: str            # SHA-256 hex
    raw_text: str                 # full extracted text
    publication_date: str | None  # YYYY-MM-DD

    # Declarant
    declarant_name_raw: str
    declarant_entity_type_hint: str  # 'company' | 'natural_person' | 'other'
    holding_vehicle_name_raw: str | None

    # Event
    issuer_isin: str
    event_date: str               # YYYY-MM-DD
    stake_pct_before: float | None
    stake_pct_after: float
    voting_pct_before: float | None
    voting_pct_after: float | None
    direct_or_indirect: str       # 'direct' | 'indirect' | 'both' | 'unknown'

    # Parser provenance
    parser_version: str           # e.g. 'ownership-pilot-0.1.0'
    parse_warnings: list[str]
```

### 8.3 Collection workflow (per issuer)

```
1. Check robots.txt for partecipazioni.consob.it
   → Abort if Disallow: / with no Crawl-delay accommodation
   → Set crawl delay from Crawl-delay directive or default 5s

2. Fetch search result listing for ISIN
   → HTML page of notifications
   → Parse into list[NotificationRef]

3. For each NotificationRef:
   a. Check context_sources for existing row (source_url dedup)
      → Skip if already ingested (idempotent)
   b. Fetch document (PDF or HTML)
   c. Compute SHA-256 hash
   d. Extract text (pdfplumber for PDF; html.parser for HTML)
   e. Parse into OwnershipNotification
   f. Call upsert_context_source() → source_id
   g. Resolve declarant entity → entity_id (or None)
   h. Resolve holding vehicle entity → holding_vehicle_entity_id (or None)
   i. Call assert_source_provenance() → raises if invalid
   j. INSERT ownership_events row
   k. Sleep crawl_delay seconds

4. Return summary: {
     issued fetched, inserted, skipped (dedup), warnings
   }
```

### 8.4 Dry-run mode

Every insert path checks a `DRY_RUN: bool` flag (default `True` in the pilot).
When `DRY_RUN=True`:
- The collector logs what it would write but makes no DB mutations
- It does fetch and parse source documents (read-only)
- `upsert_context_source()` is patched to return a sentinel value (`-1`)
- All entity lookups are performed but no new entity rows are inserted

Run in dry-run mode first for all three pilot issuers. Review output before
switching to `DRY_RUN=False`.

### 8.5 Environment variables

```bash
# Required (already used by existing scraper)
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...

# Pilot-specific
OWNERSHIP_PILOT_DRY_RUN=true        # default: true; set false to write
OWNERSHIP_PILOT_CRAWL_DELAY_S=5     # seconds between requests
OWNERSHIP_PILOT_ISINS=IT0003115950,IT0000062957,IT0005211237  # pilot three
OWNERSHIP_PILOT_MAX_NOTIFICATIONS=20  # per-issuer cap during testing
```

No new secrets are needed. No new API keys. The service-role key is sufficient
and already exists in `.env.local` and GitHub Actions secrets.

### 8.6 Test plan

Before writing any collector code:

```
1. Verify: `partecipazioni.consob.it/robots.txt` — confirm not disallowed
2. Verify: URL structure for per-ISIN search on the portal
3. Verify: notification listing HTML structure (field names, CSS selectors)
4. Verify: individual notification document format (PDF vs HTML)
5. Dry run for IT0003115950 (De' Longhi) — expected: ~5–30 notifications
6. Review dry-run output for field coverage and parse warnings
7. Iterate parser until <5% warnings on De' Longhi set
8. Dry run for IT0000062957 (Mediobanca) — stress test with complex names
9. Dry run for IT0005211237 (Italgas) — test issuer FK on entity (Snam)
10. Write review for operator: entity resolution backlog, parse quality
11. ONLY after operator sign-off: DRY_RUN=False for De' Longhi
12. Confirm ownership_events rows in Supabase for De' Longhi
13. ONLY after De' Longhi verified: proceed to Mediobanca and Italgas
```

---

## 9. Ownership UI activation plan

The `/internal/ownership` workspace (currently a placeholder) must NOT be
activated until:

1. At least one pilot issuer has been processed and operator-reviewed (step 11
   of §8.6 above)
2. At least 10 `ownership_events` rows exist with `review_status = 'confirmed'`
3. The UI is connected to a read-only query (via `getSupabaseServer()`) with no
   client-side writes
4. Evidence text and source URL are shown; `storage_path` and `raw_text` are
   never rendered (see security constraints)

The UI activation is a separate phase item and is not part of Phase 17B.
Phase 17B delivers only: documentation, schema confirmation, and the pilot
collector architecture. No data is written to the database in Phase 17B.

---

## 10. What is NOT permitted in the pilot

In strict compliance with the project's data quality requirements:

- **No fabricated or estimated ownership percentages** — every percentage must
  come from a CONSOB notification verbatim
- **No inferred beneficial ownership** beyond what the notification explicitly
  states (no chain reconstruction, no implied UBO attribution)
- **No bulk scraping** across all Italian issuers — pilot is three issuers only
- **No activation of the Ownership UI** with unreviewed pilot data
- **No codice fiscale storage** as a structured indexed column
- **No linking to transactions** (context_event_links) until ownership data
  has passed operator review
