# Phase 17B.5 — Ownership Pilot Internal Review

Internal review of the three applied ownership-pilot records.
Status: **review report — operator decisions pending; no DB changes applied**
Date: 2026-06-29

Scope: only the pilot records applied in Phase 17B.4 (6 entities, 3 ownership
events, 3 relationships). No new collection. `/internal/ownership` not activated.

Read-only review tool:

```bash
python3 -m scraper.ownership.review            # list pending + recommendations
```

Operator-approved correction (explicit, one id at a time — NOT run yet):

```bash
python3 -m scraper.ownership.review --approve-entity-type <id> --to <type>
```

---

## 1. Entities pending review (6)

| id | Current type | Legal name | Status |
|----|-------------|-----------|--------|
| 1 | `company` | `FMR LLC` | correct — no change |
| 2 | `company` | `THE GOLDMAN SACHS GROUP, INC.` | correct — no change |
| 3 | `natural_person` | `GOLDMAN SACHS INTERNATIONAL` | **mislabeled → recommendation below** |
| 4 | `company` | `GOLDMAN SACHS BANK EUROPE SE` | correct — no change |
| 5 | `natural_person` | `MORGAN STANLEY` | **mislabeled → recommendation below** |
| 6 | `company` | `Morgan Stanley & Co. International plc` | correct — no change |

All 6 remain `review_status = pending_review` until an operator confirms.

## 2. Ownership events pending review (3)

| id | Issuer | Declarant | event_type | Voting % | review |
|----|--------|-----------|-----------|----------|--------|
| 1 | Brunello Cucinelli S.p.A. (1) | FMR LLC | `other` | 5.588 | pending_review |
| 2 | Mediobanca S.p.A. (2) | THE GOLDMAN SACHS GROUP, INC. | `initial_disclosure` | 3.069 | pending_review |
| 3 | Italmobiliare S.p.A. (3) | MORGAN STANLEY | `initial_disclosure` | 3.001 | pending_review |

Event 1 (Cucinelli) stays `event_type = other` — its prior notification carries
an ambiguous `a)/b)` split, so direction is undetermined. It must remain
excluded from directional/threshold-crossing signal logic. No change recommended.

## 3. Relationships pending review (3)

| id | Subject (declarant) | Type | Object (vehicle) | review |
|----|--------------------|------|-----------------|--------|
| 1 | THE GOLDMAN SACHS GROUP, INC. (2) | `controls` | GOLDMAN SACHS INTERNATIONAL (3) | pending_review |
| 2 | THE GOLDMAN SACHS GROUP, INC. (2) | `controls` | GOLDMAN SACHS BANK EUROPE SE (4) | pending_review |
| 3 | MORGAN STANLEY (5) | `controls` | Morgan Stanley & Co. International plc (6) | pending_review |

Each is an explicit "società controllata dal dichiarante" stated in the source.
No relationship was created for Cucinelli/FMR (no named controlled vehicle in
the aggregate notice). No shareholder / UBO / concert-party / beneficial-owner /
holding-chain relationships were inferred. No change recommended.

---

## 4. Entity-type correction recommendations (require operator decision)

The ingestion entity-type heuristic infers `company` only from legal-form
suffixes (S.p.A., LLC, plc, SE, …). Two declarant-side names have no such
suffix and were therefore tagged `natural_person`, although the name clearly
denotes an organisation. These are **recommendations only** — not applied.

| Entity id | Current type | Proposed type | Evidence | Required operator decision |
|-----------|-------------|--------------|----------|---------------------------|
| 3 | `natural_person` | `company` | Legal name `GOLDMAN SACHS INTERNATIONAL` contains organisation indicator "INTERNATIONAL"; it is the UK Goldman Sachs broker-dealer subsidiary named as a controlled vehicle in the Mediobanca notice | Approve → `company`, or set a more specific type, or reject |
| 5 | `natural_person` | `company` | Legal name `MORGAN STANLEY` is the declarant group named in the Italmobiliare notice; not a natural person | Approve → `company`, or set a more specific type, or reject |

Proposed type is the generic `company`; the operator may instead choose a more
specific valid type (`holding_company`, `fund`, etc.).

To apply an approved correction (operator runs this; it sets `entity_type` and
marks the entity `review_status = confirmed`):

```bash
python3 -m scraper.ownership.review --approve-entity-type 3 --to company
python3 -m scraper.ownership.review --approve-entity-type 5 --to company
```

**No correction has been applied. No entity type or review status has been
changed in the database.** The four correctly-typed entities (1, 2, 4, 6) need
no type change but still require ordinary operator review-status confirmation.

---

## 5. Why this is conservative

- The heuristic is **not** turned into a fact: mismatches surface as
  recommendations with cited evidence, and an operator must approve each one.
- No entity is reclassified automatically; the only write path is the explicit,
  single-id `--approve-entity-type` command.
- Ingestion semantics are unchanged (no collector edits). The suffix-heuristic
  imperfection is handled at review time, not by rewriting parse rules mid-pilot.
