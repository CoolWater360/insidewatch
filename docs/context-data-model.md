# InsideWatch — Context Data Model

Phase 17A · Design document  
Status: **DRAFT — pending review before any migration is applied**  
Author: automated design pass over live schema  
Date: 2026-06-28

---

## 1. Purpose

Phase 17 adds ownership, governance, buyback, corporate-action, and entity-relationship
intelligence to explain whether an insider transaction is discretionary, mechanical,
ownership-related, compensation-related, or part of a wider issuer event.

This document defines:

- the canonical schema for eight new table groups;
- an entity-relationship map across all tables (new + existing);
- the source-provenance policy that every context record must satisfy;
- the migration plan (four numbered migrations, applied in order);
- a compatibility analysis confirming that no existing table or API is broken.

No SQL migration is applied until this design has been reviewed and approved.

---

## 2. Product principles (binding on every table)

Every context record must answer four questions:

| # | Question | Schema enforcement |
|---|----------|-------------------|
| 1 | What happened? | `event_type` CHECK constraint on every event table |
| 2 | Who was involved? | entity_id + raw_entity_name on every event that mentions a person/vehicle |
| 3 | When did it become public / when did we find it? | `publication_timestamp` + `discovered_timestamp` in `context_sources` |
| 4 | What primary source supports it? | `source_id NOT NULL` FK on every event table |

No inferred motive may be stored as a confirmed fact.

Every record distinguishes:

| Confidence level | Meaning | Allowed auto-assign? |
|-----------------|---------|---------------------|
| `parsed_fact` | Extracted verbatim from a source document by parser | Yes |
| `heuristic_suggestion` | Inferred by algorithmic rule; not from source text | Yes, but always labeled |
| `reviewer_confirmed` | Human operator has explicitly confirmed | No — requires `reviewed_by` + `reviewed_at` |

---

## 3. Existing schema — tables in scope

The following tables already exist and are **not modified** in Phase 17.

```
companies           — legacy per-Avviso company rows; linked to issuers via issuer_id
insiders            — MAR Art 19 declarants; linked to companies
transactions        — core event records; linked to insiders + companies + filings
transaction_versions — immutable audit log of row changes
filings             — PDF filing ledger with state machine
filing_processing_runs
issuers             — canonical issuer master (canonical_name, lei, etc.)
issuer_aliases      — name/ISIN/ticker aliases for issuers
securities          — ISIN → issuer mapping
unmatched_issuers   — review queue for unresolved company names
quality_reviews     — human review decisions (truth set for calibration)
```

The key identifiers used in cross-references:

- `issuers.id`     — canonical issuer identity
- `transactions.id` — canonical transaction identity
- `insiders.id`    — MAR declarant identity
- `filings.id`     — source PDF document identity

---

## 4. New table groups

Eight new table groups are introduced. Dependency order for DDL:

```
1. context_sources           (no new dependencies)
2. entities                  (no new dependencies; optional FK to issuers, insiders)
3. entity_relationships      (depends on: entities, context_sources)
4. ownership_events          (depends on: issuers, entities, context_sources)
5. governance_events         (depends on: issuers, entities, insiders, context_sources)
6. buyback_events            (depends on: issuers, context_sources)
7. corporate_events          (depends on: issuers, context_sources)
8. context_event_links       (depends on: all event tables above, transactions, issuers)
```

---

## 5. Entity-relationship diagram

```
                        ┌─────────────────┐
                        │   context_sources│
                        │  (provenance)    │
                        └────────┬────────┘
                                 │ source_id (NOT NULL FK)
            ┌────────────────────┼─────────────────────┐
            ▼                    ▼                      ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ ownership_events │  │ governance_events│  │  buyback_events  │
   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            │                     │                      │
            │           ┌─────────┘                      │
            │           │           ┌────────────────────┘
            ▼           ▼           ▼
   ┌──────────────────────────────────────────────────────┐
   │                context_event_links                   │
   │  (ownership|governance|buyback|corporate → tx/signal)│
   └──────────────────────────────────────────────────────┘
            │                    │                  │
            ▼                    ▼                  ▼
   transactions            issuers              (signals)

   ┌──────────────────┐
   │ corporate_events │
   │                  │───── issuers.id
   └──────────────────┘

   ┌──────────────────┐      ┌──────────────────────┐
   │    entities      │◄─────│ entity_relationships  │
   │ (persons,        │      │ (control, ownership,  │
   │  vehicles, etc.) │      │  related-party)       │
   └───┬──────────────┘      └──────────────────────┘
       │ optional FK
       ├──► issuers.id  (when entity is also a listed issuer)
       └──► insiders.id (when entity matches a known MAR declarant)

   Existing tables (unchanged):
   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ issuers  │   │companies │   │ insiders │   │filings   │
   └──────────┘   └──────────┘   └──────────┘   └──────────┘
        │               │               │               │
        └───────────────┴───────────────┴───────────────┘
                        transactions (unchanged)
```

---

## 6. Table specifications

### 6.1 `context_sources`

Stores every primary source document used to support a context record.
One row per unique source URL. If the same document is available at multiple
URLs, the canonical URL is preferred; alternate URLs can be stored as aliases
(not modelled in Phase 17A — add as needed).

```sql
CREATE TABLE context_sources (
    id                    BIGSERIAL PRIMARY KEY,

    -- Primary identifier: the URL of the source document.
    -- Must be the canonical direct link (not a redirect or landing page).
    source_url            TEXT NOT NULL,

    -- Source type taxonomy — see §7 for full list.
    source_type           TEXT NOT NULL CHECK (source_type IN (
        'regulatory_filing',   -- CONSOB/Borsa Italiana mandatory disclosure
        'exchange_notice',     -- Borsa Italiana avviso (e.g. buyback weekly report)
        'annual_report',       -- annual or interim report
        'press_release',       -- company press release / investor relations
        'prospectus',          -- IPO, rights issue, or capital-increase prospectus
        'governance_notice',   -- board appointment/resignation announcement
        'official_gazette',    -- Gazzetta Ufficiale della Repubblica Italiana
        'operator_entry',      -- manually entered by an InsideWatch operator
        'other'
    )),

    -- Who published this document.
    publisher             TEXT,

    -- Document title as it appears on the source site.
    document_title        TEXT,

    -- When the source was published (NULL if unknown — avoid where possible).
    publication_timestamp TIMESTAMPTZ,

    -- When InsideWatch first discovered this source. Set on INSERT; never updated.
    discovered_timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- SHA-256 of the raw downloaded bytes. NULL if document was not preserved.
    document_hash         TEXT,

    -- Path in the storage backend (same convention as filings.storage_path).
    storage_path          TEXT,

    -- Extracted raw text (for re-parse and audit). TOAST-compressed automatically.
    raw_text              TEXT,

    -- Which issuer this source primarily concerns (optional — many sources span issuers).
    issuer_id             BIGINT REFERENCES issuers(id) ON DELETE SET NULL,

    -- Identifier for the ingestion batch that found this source.
    ingestion_run_id      TEXT,

    -- Operator review state.
    review_status         TEXT NOT NULL DEFAULT 'unreviewed'
                              CHECK (review_status IN ('unreviewed', 'reviewed', 'disputed')),

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source_url)
);
```

**Key indexes:**
```sql
CREATE INDEX idx_context_sources_issuer_id   ON context_sources(issuer_id) WHERE issuer_id IS NOT NULL;
CREATE INDEX idx_context_sources_source_type ON context_sources(source_type);
CREATE INDEX idx_context_sources_publication ON context_sources(publication_timestamp DESC);
CREATE INDEX idx_context_sources_hash        ON context_sources(document_hash) WHERE document_hash IS NOT NULL;
```

---

### 6.2 `entities`

Represents any legal entity in the ownership and governance structure:
natural persons, companies, holding vehicles, trusts, nominees, etc.

This table is **explicitly distinct** from `insiders` (which represents
MAR Art 19 declarants tied to a specific company) and from `issuers` (which
represents securities issuers).  An entity may optionally be linked to both.

```sql
CREATE TABLE entities (
    id            BIGSERIAL PRIMARY KEY,

    -- Legal/documentary name exactly as it appears in the primary source.
    legal_name    TEXT NOT NULL,

    -- Abbreviated display name.
    short_name    TEXT,

    -- Category of legal vehicle.
    entity_type   TEXT NOT NULL CHECK (entity_type IN (
        'natural_person',
        'company',
        'holding_company',
        'trust',
        'fiduciary',
        'foundation',
        'fund',
        'nominee',
        'other'
    )),

    -- ISO 3166-1 alpha-2 jurisdiction of incorporation (or citizenship for persons).
    jurisdiction  TEXT DEFAULT 'IT',

    -- Legal Entity Identifier (20-char GLEIF code). Preferred unique identifier.
    lei           TEXT UNIQUE,

    -- If this entity is also a listed issuer, link it here.
    -- Set only when the match is confirmed (exact LEI or ISIN match preferred).
    issuer_id     BIGINT REFERENCES issuers(id) ON DELETE SET NULL,

    -- If this entity matches a known MAR declarant, link it here.
    -- NULL for entities that have never appeared as an insider in a filing.
    insider_id    BIGINT REFERENCES insiders(id) ON DELETE SET NULL,

    -- Operator notes — not published externally.
    notes         TEXT,

    -- review_status for the entity record itself (not for any specific relationship).
    review_status TEXT NOT NULL DEFAULT 'pending_review'
                      CHECK (review_status IN ('pending_review', 'confirmed', 'rejected')),

    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Design notes:**
- `lei` is the preferred globally-unique identifier. For Italian companies,
  an LEI can be obtained from GLEIF/infocamere.
- No codice-fiscale column is included. Personal tax identifiers for natural
  persons are not stored unless they appear verbatim in a publicly filed document
  (in which case they may be stored in a text field attached to the source context,
  not as a structured indexed column).
- `insider_id` is advisory: if the same person appears in both contexts, the link
  makes queries easier. It does NOT replace the insiders table.

**Key indexes:**
```sql
CREATE INDEX idx_entities_entity_type  ON entities(entity_type);
CREATE INDEX idx_entities_issuer_id    ON entities(issuer_id)  WHERE issuer_id IS NOT NULL;
CREATE INDEX idx_entities_insider_id   ON entities(insider_id) WHERE insider_id IS NOT NULL;
CREATE INDEX idx_entities_review       ON entities(review_status);
CREATE INDEX idx_entities_legal_name   ON entities(LOWER(legal_name));
```

---

### 6.3 `entity_relationships`

Records a structured relationship between two entities (or between an entity
and an issuer).  Every row must be backed by a `context_sources` record.

```sql
CREATE TABLE entity_relationships (
    id                BIGSERIAL PRIMARY KEY,

    -- The entity that holds/controls/owns (subject).
    subject_entity_id BIGINT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,

    -- The entity that is held/controlled (object). NULL if the object is an issuer.
    object_entity_id  BIGINT REFERENCES entities(id) ON DELETE CASCADE,

    -- If the relationship is to an issuer directly (e.g. shareholder of issuer).
    object_issuer_id  BIGINT REFERENCES issuers(id) ON DELETE CASCADE,

    -- Relationship type taxonomy.
    relationship_type TEXT NOT NULL CHECK (relationship_type IN (
        'controls',           -- subject directly or indirectly controls object
        'beneficially_owns',  -- subject is the beneficial owner
        'shareholder',        -- subject holds a stake in object (issuer)
        'related_party',      -- subject is a related party to the issuer
        'concert_party',      -- subject and object act in concert (explicitly disclosed)
        'nominee_for',        -- subject acts as nominee for a third party
        'trustee_of',         -- subject is trustee of object (trust/foundation)
        'beneficiary_of',     -- subject is a beneficiary of object (trust/foundation)
        'family_relation',    -- subject is spouse/child/etc. of object (public + legally relevant)
        'other'
    )),

    -- Evidence quality.
    confidence        TEXT NOT NULL DEFAULT 'parsed_fact'
                          CHECK (confidence IN (
                              'parsed_fact',
                              'heuristic_suggestion',
                              'reviewer_confirmed'
                          )),

    -- Ownership percentage (NULL if not in source or not applicable).
    ownership_pct     NUMERIC CHECK (ownership_pct BETWEEN 0 AND 100),

    -- Voting-right percentage (NULL if same as ownership_pct or not applicable).
    voting_pct        NUMERIC CHECK (voting_pct BETWEEN 0 AND 100),

    -- Whether the subject holds directly, indirectly, or combination.
    direct_or_indirect TEXT CHECK (direct_or_indirect IN ('direct', 'indirect', 'both', 'unknown')),

    -- Effective period. effective_to NULL = relationship still believed to be active.
    effective_from    DATE,
    effective_to      DATE,

    -- Mandatory source back-reference.
    source_id         BIGINT NOT NULL REFERENCES context_sources(id),

    -- Verbatim key text from the source document supporting this relationship.
    evidence_text     TEXT,

    -- Versioning (same pattern as transactions).
    is_current        BOOLEAN NOT NULL DEFAULT TRUE,
    version_number    INT     NOT NULL DEFAULT 1,
    superseded_by     BIGINT  REFERENCES entity_relationships(id),
    superseded_at     TIMESTAMPTZ,

    -- Review workflow.
    review_status     TEXT NOT NULL DEFAULT 'pending_review'
                          CHECK (review_status IN ('pending_review', 'confirmed', 'rejected')),
    reviewed_by       TEXT,
    reviewed_at       TIMESTAMPTZ,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- At least one of object_entity_id or object_issuer_id must be non-null.
    CHECK (object_entity_id IS NOT NULL OR object_issuer_id IS NOT NULL)
);
```

**Key indexes:**
```sql
CREATE INDEX idx_entity_rel_subject    ON entity_relationships(subject_entity_id);
CREATE INDEX idx_entity_rel_obj_entity ON entity_relationships(object_entity_id) WHERE object_entity_id IS NOT NULL;
CREATE INDEX idx_entity_rel_obj_issuer ON entity_relationships(object_issuer_id) WHERE object_issuer_id IS NOT NULL;
CREATE INDEX idx_entity_rel_type       ON entity_relationships(relationship_type);
CREATE INDEX idx_entity_rel_current    ON entity_relationships(is_current) WHERE is_current = FALSE;
CREATE INDEX idx_entity_rel_review     ON entity_relationships(review_status) WHERE review_status = 'pending_review';
```

---

### 6.4 `ownership_events`

Records a point-in-time ownership disclosure: a threshold crossing, stake
change, or voting-right notification as filed with CONSOB or Borsa Italiana.

```sql
CREATE TABLE ownership_events (
    id                       BIGSERIAL PRIMARY KEY,

    -- Which issuer's shares are involved.
    issuer_id                BIGINT NOT NULL REFERENCES issuers(id) ON DELETE CASCADE,

    -- The entity filing the notification (resolved). NULL if entity not yet resolved.
    entity_id                BIGINT REFERENCES entities(id) ON DELETE SET NULL,

    -- Raw entity name from the filing (always preserved, even when entity_id is set).
    raw_entity_name          TEXT NOT NULL,

    -- Type of ownership event.
    event_type               TEXT NOT NULL CHECK (event_type IN (
        'threshold_crossing_up',    -- stake crossed above a threshold
        'threshold_crossing_down',  -- stake crossed below a threshold
        'stake_increase',           -- increase without threshold crossing
        'stake_decrease',           -- decrease without threshold crossing
        'voting_right_change',      -- voting rights changed independently
        'concert_party_formed',
        'concert_party_dissolved',
        'notification_correction',  -- correction of a prior notification
        'other'
    )),

    -- The regulatory threshold crossed (e.g. 5, 10, 15, 20, 25, 30, 50, 75).
    -- NULL for stake_increase/decrease events that don't cross a threshold.
    threshold_pct            NUMERIC CHECK (threshold_pct BETWEEN 0 AND 100),

    -- Holdings before and after the event.
    previous_pct             NUMERIC CHECK (previous_pct BETWEEN 0 AND 100),
    new_pct                  NUMERIC CHECK (new_pct BETWEEN 0 AND 100),

    -- Voting rights (if disclosed separately from economic holding).
    previous_voting_pct      NUMERIC CHECK (previous_voting_pct BETWEEN 0 AND 100),
    new_voting_pct           NUMERIC CHECK (new_voting_pct BETWEEN 0 AND 100),

    -- Breakdown of holding structure where disclosed.
    direct_pct               NUMERIC CHECK (direct_pct BETWEEN 0 AND 100),
    indirect_pct             NUMERIC CHECK (indirect_pct BETWEEN 0 AND 100),

    -- Holding vehicle through which the indirect stake is held, if disclosed.
    holding_vehicle_entity_id BIGINT REFERENCES entities(id) ON DELETE SET NULL,
    holding_vehicle_raw_name  TEXT,   -- raw name from filing (preserved even when FK is set)

    -- Key dates.
    event_date               DATE NOT NULL,  -- date the stake change occurred
    publication_date         DATE,           -- date the notification was published

    -- Mandatory source.
    source_id                BIGINT NOT NULL REFERENCES context_sources(id),
    evidence_text            TEXT,

    -- Versioning.
    is_current               BOOLEAN NOT NULL DEFAULT TRUE,
    version_number           INT     NOT NULL DEFAULT 1,
    superseded_by            BIGINT  REFERENCES ownership_events(id),
    superseded_at            TIMESTAMPTZ,

    -- Review.
    review_status            TEXT NOT NULL DEFAULT 'pending_review'
                                 CHECK (review_status IN ('pending_review', 'confirmed', 'rejected')),
    reviewed_by              TEXT,
    reviewed_at              TIMESTAMPTZ,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Key indexes:**
```sql
CREATE INDEX idx_ownership_events_issuer      ON ownership_events(issuer_id, event_date DESC);
CREATE INDEX idx_ownership_events_entity      ON ownership_events(entity_id) WHERE entity_id IS NOT NULL;
CREATE INDEX idx_ownership_events_type        ON ownership_events(event_type);
CREATE INDEX idx_ownership_events_current     ON ownership_events(is_current) WHERE is_current = FALSE;
CREATE INDEX idx_ownership_events_review      ON ownership_events(review_status) WHERE review_status = 'pending_review';
CREATE INDEX idx_ownership_events_threshold   ON ownership_events(threshold_pct) WHERE threshold_pct IS NOT NULL;
```

---

### 6.5 `governance_events`

Records leadership and board changes: appointments, resignations, and
role transitions as disclosed in issuer notices.

```sql
CREATE TABLE governance_events (
    id                   BIGSERIAL PRIMARY KEY,

    -- Which issuer is affected.
    issuer_id            BIGINT NOT NULL REFERENCES issuers(id) ON DELETE CASCADE,

    -- Person involved — resolved to entity.
    entity_id            BIGINT REFERENCES entities(id) ON DELETE SET NULL,

    -- Person involved — resolved to known MAR insider (may be set alongside entity_id).
    insider_id           BIGINT REFERENCES insiders(id) ON DELETE SET NULL,

    -- Raw name as it appeared in the source.
    raw_person_name      TEXT,

    -- Raw role text from the source.
    raw_role             TEXT,

    -- Governance event taxonomy.
    event_type           TEXT NOT NULL CHECK (event_type IN (
        'board_appointment',
        'board_resignation',
        'ceo_appointment',
        'ceo_resignation',
        'cfo_appointment',
        'cfo_resignation',
        'chair_appointment',
        'chair_resignation',
        'executive_appointment',
        'executive_resignation',
        'committee_change',
        'interim_appointment',
        'role_change',
        'other'
    )),

    -- Formal role title from the source.
    role_title           TEXT,

    -- Key dates.
    effective_date       DATE,
    announced_date       DATE,

    -- Predecessor/successor if disclosed.
    predecessor_entity_id BIGINT REFERENCES entities(id) ON DELETE SET NULL,
    successor_entity_id   BIGINT REFERENCES entities(id) ON DELETE SET NULL,

    -- Mandatory source.
    source_id            BIGINT NOT NULL REFERENCES context_sources(id),
    evidence_text        TEXT,

    -- Versioning.
    is_current           BOOLEAN NOT NULL DEFAULT TRUE,
    version_number       INT     NOT NULL DEFAULT 1,
    superseded_by        BIGINT  REFERENCES governance_events(id),
    superseded_at        TIMESTAMPTZ,

    -- Review.
    review_status        TEXT NOT NULL DEFAULT 'pending_review'
                             CHECK (review_status IN ('pending_review', 'confirmed', 'rejected')),
    reviewed_by          TEXT,
    reviewed_at          TIMESTAMPTZ,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Key indexes:**
```sql
CREATE INDEX idx_governance_events_issuer   ON governance_events(issuer_id, effective_date DESC);
CREATE INDEX idx_governance_events_entity   ON governance_events(entity_id)   WHERE entity_id IS NOT NULL;
CREATE INDEX idx_governance_events_insider  ON governance_events(insider_id)  WHERE insider_id IS NOT NULL;
CREATE INDEX idx_governance_events_type     ON governance_events(event_type);
CREATE INDEX idx_governance_events_current  ON governance_events(is_current)  WHERE is_current = FALSE;
CREATE INDEX idx_governance_events_review   ON governance_events(review_status) WHERE review_status = 'pending_review';
```

---

### 6.6 `buyback_events`

Records the full lifecycle of an issuer share-buyback programme:
authorization, launch, execution reports, amendments, suspension, and completion.

```sql
CREATE TABLE buyback_events (
    id                      BIGSERIAL PRIMARY KEY,

    -- Which issuer's buyback programme.
    issuer_id               BIGINT NOT NULL REFERENCES issuers(id) ON DELETE CASCADE,

    -- Lifecycle stage.
    event_type              TEXT NOT NULL CHECK (event_type IN (
        'authorization',   -- board or shareholder authorization
        'launch',          -- programme announcement / commencement
        'execution',       -- periodic execution report (daily, weekly)
        'amendment',       -- programme terms amended
        'suspension',      -- programme suspended
        'completion'       -- programme completed or mandate expired
    )),

    -- Programme identifier — links execution records to their launch/authorization.
    -- Operator-assigned or derived from source (e.g. "2025-Q1-Buyback").
    programme_id            TEXT,

    -- Authorization parameters (set on 'authorization' events).
    authorization_max_shares BIGINT,
    authorization_max_value  NUMERIC,

    -- Execution record fields (set on 'execution' events).
    execution_date          DATE,
    shares_purchased        BIGINT,
    average_price           NUMERIC,
    total_consideration     NUMERIC,
    currency                TEXT NOT NULL DEFAULT 'EUR',
    cumulative_shares       BIGINT,     -- cumulative within this programme
    cumulative_value        NUMERIC,
    treasury_share_pct      NUMERIC CHECK (treasury_share_pct BETWEEN 0 AND 100),

    -- Effective date (for non-execution events).
    effective_date          DATE,

    -- Mandatory source.
    source_id               BIGINT NOT NULL REFERENCES context_sources(id),
    evidence_text           TEXT,

    -- Versioning.
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    version_number          INT     NOT NULL DEFAULT 1,
    superseded_by           BIGINT  REFERENCES buyback_events(id),
    superseded_at           TIMESTAMPTZ,

    -- Review.
    review_status           TEXT NOT NULL DEFAULT 'pending_review'
                                CHECK (review_status IN ('pending_review', 'confirmed', 'rejected')),
    reviewed_by             TEXT,
    reviewed_at             TIMESTAMPTZ,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Key indexes:**
```sql
CREATE INDEX idx_buyback_events_issuer      ON buyback_events(issuer_id, effective_date DESC NULLS LAST);
CREATE INDEX idx_buyback_events_type        ON buyback_events(event_type);
CREATE INDEX idx_buyback_events_programme   ON buyback_events(programme_id) WHERE programme_id IS NOT NULL;
CREATE INDEX idx_buyback_events_execution   ON buyback_events(execution_date DESC) WHERE event_type = 'execution';
CREATE INDEX idx_buyback_events_current     ON buyback_events(is_current) WHERE is_current = FALSE;
CREATE INDEX idx_buyback_events_review      ON buyback_events(review_status) WHERE review_status = 'pending_review';
```

---

### 6.7 `corporate_events`

Records issuer corporate actions that may explain insider transactions:
capital increases, rights issues, stock splits, mergers, employee share plans,
conversion events, and similar.

```sql
CREATE TABLE corporate_events (
    id                BIGSERIAL PRIMARY KEY,

    -- Which issuer.
    issuer_id         BIGINT NOT NULL REFERENCES issuers(id) ON DELETE CASCADE,

    -- Event taxonomy.
    event_type        TEXT NOT NULL CHECK (event_type IN (
        'capital_increase',
        'rights_issue',
        'employee_share_plan_grant',
        'option_exercise_window',
        'share_vesting',
        'conversion',
        'merger',
        'demerger',
        'stock_split',
        'reverse_split',
        'tender_offer',
        'dividend_in_kind',
        'share_plan_launch',
        'share_plan_completion',
        'restructuring',
        'other'
    )),

    -- Human-readable description from the source.
    event_description TEXT,

    -- Plan or programme name (for share-plan events).
    plan_name         TEXT,

    -- Key dates.
    announcement_date DATE,
    effective_date    DATE,
    expiry_date       DATE,      -- e.g. rights issue subscription expiry

    -- Numerical details (fill as available from source).
    shares_involved   BIGINT,
    price_per_share   NUMERIC,
    total_value       NUMERIC,
    currency          TEXT NOT NULL DEFAULT 'EUR',
    ratio             TEXT,      -- e.g. '2:1' for a stock split

    -- Mandatory source.
    source_id         BIGINT NOT NULL REFERENCES context_sources(id),
    evidence_text     TEXT,

    -- Versioning.
    is_current        BOOLEAN NOT NULL DEFAULT TRUE,
    version_number    INT     NOT NULL DEFAULT 1,
    superseded_by     BIGINT  REFERENCES corporate_events(id),
    superseded_at     TIMESTAMPTZ,

    -- Review.
    review_status     TEXT NOT NULL DEFAULT 'pending_review'
                          CHECK (review_status IN ('pending_review', 'confirmed', 'rejected')),
    reviewed_by       TEXT,
    reviewed_at       TIMESTAMPTZ,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Key indexes:**
```sql
CREATE INDEX idx_corporate_events_issuer     ON corporate_events(issuer_id, effective_date DESC NULLS LAST);
CREATE INDEX idx_corporate_events_type       ON corporate_events(event_type);
CREATE INDEX idx_corporate_events_current    ON corporate_events(is_current) WHERE is_current = FALSE;
CREATE INDEX idx_corporate_events_review     ON corporate_events(review_status) WHERE review_status = 'pending_review';
CREATE INDEX idx_corporate_events_announced  ON corporate_events(announcement_date DESC);
```

---

### 6.8 `context_event_links`

> **Design change vs. draft (applied in migration 019):** The original draft
> used a polymorphic pair `(link_target_type TEXT, link_target_id BIGINT)` for
> the link target.  This was replaced with **concrete FK columns**
> (`transaction_id`, `issuer_id`) protected by a mutual-exclusion CHECK
> constraint.  Reasons:
> - Postgres can enforce referential integrity on concrete FKs (not on a
>   polymorphic id column).
> - Partial unique indexes on concrete columns are simpler and more efficient.
> - There are exactly two link targets (transactions, issuers); the "open for
>   extension" argument for polymorphism does not apply here.
> - `link_type` vocabulary was also revised: proximity/explicit/heuristic
>   replaced by same_issuer/same_person/causal/concurrent/related_party/
>   background/other, which are more analytic than mechanical.
> - Versioning columns (is_current, superseded_by, etc.) were added to match
>   the pattern on all other Phase 17A tables.

Links context events to transactions or issuers.
Each row records **why** the link was made and at what confidence.

A link never modifies the linked transaction. It is purely additive context.

```sql
CREATE TABLE context_event_links (
    id              BIGSERIAL PRIMARY KEY,

    -- Which category of context event (polymorphic — no DB FK).
    context_type    TEXT NOT NULL CHECK (context_type IN (
        'ownership',    -- ownership_events
        'governance',   -- governance_events
        'buyback',      -- buyback_events
        'corporate'     -- corporate_events
    )),
    context_id      BIGINT NOT NULL,

    -- Link target: exactly one must be non-NULL (enforced by CHECK).
    transaction_id  BIGINT REFERENCES transactions(id) ON DELETE CASCADE,
    issuer_id       BIGINT REFERENCES issuers(id)      ON DELETE CASCADE,

    CONSTRAINT chk_cel_exactly_one_target
        CHECK (
            (transaction_id IS NOT NULL AND issuer_id IS NULL)
            OR
            (transaction_id IS NULL     AND issuer_id IS NOT NULL)
        ),

    -- Nature of the analytical relationship between context and target.
    link_type       TEXT NOT NULL CHECK (link_type IN (
        'same_issuer',    -- context event and target share the same issuer
        'same_person',    -- context event and target share the same person
        'causal',         -- context event causally explains the target
        'concurrent',     -- occurred in close proximity to the target
        'related_party',  -- context involves a related party of the target
        'background',     -- general background / informational context
        'other'
    )),

    -- Evidence quality of the link itself.
    confidence      TEXT NOT NULL DEFAULT 'heuristic_suggestion'
                        CHECK (confidence IN (
                            'parsed_fact',
                            'heuristic_suggestion',
                            'reviewer_confirmed'
                        )),

    -- Provenance of the link assertion (may differ from the context event's source).
    source_id       BIGINT NOT NULL REFERENCES context_sources(id) ON DELETE RESTRICT,

    -- Operator annotation.
    analyst_note    TEXT,

    -- Versioning (same pattern as all other Phase 17A event tables).
    is_current      BOOLEAN NOT NULL DEFAULT TRUE,
    version_number  INT     NOT NULL DEFAULT 1,
    superseded_by   BIGINT  REFERENCES context_event_links(id) ON DELETE SET NULL,
    superseded_at   TIMESTAMPTZ,

    -- Review workflow.
    review_status   TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (review_status IN (
                            'pending_review', 'confirmed', 'rejected'
                        )),
    reviewed_by     TEXT,
    reviewed_at     TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Natural event identity** (partial unique indexes):
```sql
-- One current link per context event + link type + transaction.
CREATE UNIQUE INDEX uidx_cel_tx_current
    ON context_event_links(context_type, context_id, link_type, transaction_id)
    WHERE is_current = TRUE AND transaction_id IS NOT NULL;

-- One current link per context event + link type + issuer.
CREATE UNIQUE INDEX uidx_cel_issuer_current
    ON context_event_links(context_type, context_id, link_type, issuer_id)
    WHERE is_current = TRUE AND issuer_id IS NOT NULL;
```

**ON DELETE choices:**

| FK | ON DELETE | Rationale |
|----|-----------|-----------|
| `transaction_id → transactions` | CASCADE | Link is derived context; if the tx is deleted the link has no target |
| `issuer_id → issuers` | CASCADE | Same reasoning |
| `source_id → context_sources` | RESTRICT | Provenance must not be silently lost |
| `superseded_by → self` | SET NULL | Prevents cycles; old row retains is_current=FALSE |

---

## 7. Source-provenance policy

Every context record must satisfy the following rules, enforced as NOT NULL
constraints in the schema and as runtime guards in the ingestion code.

### 7.1 Mandatory fields per record

| Field | Where enforced |
|-------|---------------|
| `source_id` (FK to `context_sources`) | NOT NULL on all event tables |
| `context_sources.source_url` | NOT NULL UNIQUE |
| `context_sources.discovered_timestamp` | NOT NULL DEFAULT NOW() |
| `confidence` | NOT NULL with CHECK constraint |
| `is_current` | NOT NULL DEFAULT TRUE |

### 7.2 Confidence level rules

| Level | When to assign | Auto-assign allowed? |
|-------|---------------|---------------------|
| `parsed_fact` | Field extracted verbatim from a source document by a parser | Yes |
| `heuristic_suggestion` | Inferred by algorithmic rule; not directly from source text | Yes, but must be labeled in API/export output |
| `reviewer_confirmed` | Human operator explicitly confirmed via CLI or review UI | No — requires non-null `reviewed_by` + `reviewed_at` |

Database enforcement: a CHECK trigger or application guard must reject
`reviewer_confirmed` when `reviewed_by IS NULL` or `reviewed_at IS NULL`.

### 7.3 Entity resolution rules

A context record may reference an entity by:

1. **FK (`entity_id`)** — resolved match; confidence ≥ `parsed_fact`.
2. **Raw name (`raw_entity_name`)** — unresolved; entity_id is NULL;
   record enters review queue (`review_status = 'pending_review'`).

Automatic resolution is permitted **only** when:
- ISIN or LEI match is exact; OR
- normalized legal name is an exact match in `entities.legal_name`.

Name-similarity matching (ILIKE, fuzzy) produces a `heuristic_suggestion`
and sets `review_status = 'pending_review'`. A human must confirm before
confidence is upgraded to `reviewer_confirmed`.

### 7.4 Versioning and correction

All event tables use the same pattern as `transactions`:

- `is_current = FALSE` when superseded.
- `superseded_by` → the row that replaced this one.
- `superseded_at` → timestamp of supersession.
- `version_number` → incremented on every change.

A separate `context_versions` table (analogous to `transaction_versions`)
is deferred to a later phase. For now, the superseded row is the history.

### 7.5 What must NOT be stored

- Inferred motives, investment intent, or character assessments.
- Beneficial ownership or control not stated in a primary source document.
- Personal identifiers (codice fiscale, passport numbers) not appearing
  verbatim in a publicly filed document.
- Source documents obtained in violation of publisher terms of service.

---

## 8. Migration plan

Four migrations, numbered 016–019. Applied in strict numerical order.
Each is idempotent (IF NOT EXISTS guards throughout).

| Migration | Creates | Depends on |
|-----------|---------|-----------|
| `016_context_sources_and_entities.sql` | `context_sources`, `entities` | `issuers`, `insiders` |
| `017_entity_relationships.sql` | `entity_relationships` | `entities`, `context_sources` |
| `018_context_events.sql` | `ownership_events`, `governance_events`, `buyback_events`, `corporate_events` | `entities`, `issuers`, `insiders`, `context_sources` |
| `019_context_event_links.sql` | `context_event_links` | all above + `transactions` |

**Rollback order (reverse):** 019 → 018 → 017 → 016

Each migration includes a `SELECT` verification block at the end (same
pattern as existing migrations 001–015).

---

## 9. Compatibility analysis

### 9.1 Existing tables — zero modifications

No column is added to, removed from, or renamed in any existing table.
All existing indexes, constraints, FKs, RPCs, and RLS policies are unchanged.

### 9.2 Existing API — zero modifications

All `/api/v1/*` endpoints return the same schema.
No new fields are added to `transactions`, `companies`, `insiders`, or `filings`.
Context data will be served via new endpoints added in Phase 17H.

### 9.3 Existing scraper — zero modifications

`scraper/db.py`, `scraper/parser.py`, `scraper/models.py`, and all ingestion
scripts continue to operate identically. They write only to the existing tables.
The new context tables are written to only by Phase 17B–17G ingestion modules.

### 9.4 FK safety

New tables reference `issuers.id` and `insiders.id` with `ON DELETE SET NULL`
(not CASCADE) so that deleting a stale issuer or insider row does not cascade-
delete ownership history. The context record retains `raw_entity_name` for
forensic traceability even when the FK is nulled.

### 9.5 PostgREST / Supabase client

All new tables are in the `public` schema and will be immediately visible
to PostgREST. Row-level security must be enabled (same procedure as existing
tables) before new tables are exposed via the anon key. Service-role key
access (used by the scraper) bypasses RLS and is unaffected.

### 9.6 Test suites

The 578-test Python suite and the TypeScript/Jest suite both pass on the
current codebase with no schema changes. Phase 17A adds no runnable code,
so the suites are unchanged by the design document itself.

---

## 10. Open design questions

These must be resolved before or during Phase 17B implementation.

| # | Question | Recommendation |
|---|----------|---------------|
| Q1 | **Polymorphic context_id** — `context_event_links.context_id` has no DB-enforced FK. Should we replace it with separate join tables per context type? | Keep polymorphic for now; add per-type partial indexes. Revisit if query complexity grows in 17C. |
| Q2 | **signals table** — Phase 17G needs to link context to signals. No `signals` table exists yet. | ~~Add `link_target_type = 'signal'` to the CHECK list.~~ Resolved in migration 019: `link_target_type` was removed entirely in favour of concrete FK columns (transaction_id, issuer_id). When signals are added, add a `signal_id BIGINT REFERENCES signals(id)` column and extend the mutual-exclusion CHECK. |
| Q3 | **entity_versions table** — Should we have a dedicated `entity_relationship_versions` table (like `transaction_versions`) for full history? | Defer to Phase 17F; the supersession column is sufficient for 17B–17E. |
| Q4 | **Source URL uniqueness** — The same CONSOB disclosure may be available at both a file URL and a landing-page URL. | Canonical URL is required in `source_url`. The document_hash provides a second identity axis if needed. Resolve per source in 17B. |
| Q5 | **Majority Italian focus** — Most sources are in Italian. Should `entities.legal_name` be normalized to Italian or accept multi-language variants? | Accept source-language text; normalization is an application-layer concern, not a schema constraint. |
| Q6 | **CONSOB major-holdings API** — CONSOB provides structured data at [partecipazioni.consob.it](https://partecipazioni.consob.it). Confirm API terms and rate limits before 17B build. | Research step for 17B kick-off. |

---

## 11. Recommendation for Phase 17B

After approval and migration of the 016–019 schema:

**Start with `ownership_events` + `context_sources` ingestion from CONSOB.**

Rationale:
- Threshold-crossing disclosures are the highest-value ownership signal for interpreting
  insider buys: a controlling shareholder increasing a stake is categorically different
  from a discretionary buy by an unrelated executive.
- CONSOB partecipazioni is a structured public feed (JSON/XML) — less parsing complexity
  than press releases or PDFs.
- The `ownership_events` table is the simplest event table to populate (no entity
  resolution ambiguity for disclosed major shareholders).
- Results immediately enrich existing signals (proximity window to transactions).

Secondary candidate: `buyback_events` from Borsa Italiana weekly notices, because:
- Source is already scraped for MAR Art 19 (same site/session).
- Reduces false positives in sell-signal alerts during active repurchase periods.
