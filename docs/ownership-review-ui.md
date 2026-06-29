# Phase 17B.6 — Internal Ownership Review Queue

A minimal **internal** review surface for the ownership-pilot data. Internal use
only; not the public `/internal/ownership` research page (which remains a
truthful "planned" placeholder).

Date: 2026-06-30

---

## Route

`/internal/review/ownership` (server component, `force-dynamic`).

Gated by the existing internal auth boundary: `middleware.ts` enforces HTTP
Basic Auth on `/internal/*` and `/api/internal/*`. The generic Review Queue
(`/internal/review`) is untouched.

## What it shows

Three sections, each listing only `pending_review` pilot records:

1. **Entities** — legal name, current type, proposed-type recommendation (if
   any), reason/evidence, status, approve / reject / set-specific-type controls.
2. **Ownership events** — issuer, declarant (+ explicit vehicle), voting-right
   %, event type, event date / publication date, direct/indirect, CONSOB source
   link, status, approve / reject. The Cucinelli/FMR record shows
   `event_type=other` with a visible **"direction not determined from source"**
   badge.
3. **Relationships** — subject entity, relationship type, object entity, CONSOB
   source link, status, approve / reject.

A prominent **pilot / partial-coverage** banner is shown. No ownership
"signal" or investment interpretation is displayed.

## Actions supported

POST `/api/internal/ownership-review` (service-role; CSRF/origin-checked):

| kind | action | effect |
|------|--------|--------|
| entity | approve | `review_status = confirmed` |
| entity | reject | `review_status = rejected` |
| entity | set_type | `entity_type = <selected>` + `review_status = confirmed` (only when operator explicitly selects a valid type) |
| event | approve / reject | `review_status` + `reviewed_by` + `reviewed_at` |
| relationship | approve / reject | `review_status` + `reviewed_by` + `reviewed_at` |

Raw source facts (percentages, dates, names, source URL, hashes, event type) are
**not** editable through this surface.

## Access / security

- All reads and writes use `getSupabaseServer()` (service-role) on the server.
  No anonymous or browser-side Supabase queries.
- The mutation endpoint rejects cross-origin browser POSTs (Origin check) and is
  behind Basic Auth (middleware).
- No public routes, public views, or RLS policies are changed. `context_sources`
  internal columns (`storage_path`, `raw_text`) are never selected or rendered.

## Auditability (hardened in Phase 17B.7 — migration 020)

The review-audit gap is now closed by **`db/migrations/020_ownership_review_audit.sql`**:

1. **`entities.reviewed_by` / `reviewed_at`** added (mirroring `ownership_events`
   and `entity_relationships`), so entity reviews record who/when per row.
2. **`internal_audit_log.entity_type` CHECK** extended to also accept
   `ownership_entity | ownership_event | ownership_relationship`. All existing
   values and rows are unaffected.
3. **Atomic review RPCs** (`internal_review_ownership_entity`,
   `internal_set_ownership_entity_type`, `internal_review_ownership_event`,
   `internal_review_ownership_relationship`) perform the business `UPDATE` and the
   `internal_audit_log` INSERT in one PL/pgSQL transaction — mirroring
   `db/migrations/009_internal_rpc.sql`. The server actions in
   `lib/ownership-review-actions.ts` call these via `db.rpc(...)`; on any error
   the whole call rolls back, so there is never an un-audited change.

No existing audit-log checks, FKs, RPCs, or review workflows were modified. No
broad new audit system was introduced.

### Apply note

Migrations in this project are applied manually (Supabase → SQL Editor). Until
migration 020 is applied to a given environment, the review **display** works
unchanged, but the approve/reject/set-type **mutations** will error (the RPCs do
not yet exist). This is intentional — Phase 17B.7 ships the migration and routes
the actions through it; the operator applies the migration when ready.

### Historical limitation (no backfill)

Reviewer attribution is **not** backfilled for review actions taken before
migration 020 / the RPC path existed:

- entities **3** and **5** — entity-type corrections approved in Phase 17B.5
  (2026-06-29 ~21:57, via the Python `scraper.ownership.review` CLI);
- entities **1, 2, 4, 6** — approved by the operator in Phase 17B.6
  (2026-06-29 ~22:15, via the first version of this UI, which used direct
  `UPDATE`s without an audit row).

These rows are `confirmed` with `updated_at` set but no `reviewed_by` /
`reviewed_at` and no `internal_audit_log` entry. This is intentional — no fake
reviewer data is fabricated retroactively. All **future** ownership review
actions (events and relationships, all still `pending_review`, plus any future
entities) are fully attributed and audited via the RPCs.

## Out of scope (unchanged)

- No new ownership collection.
- `/internal/ownership` is still a placeholder (not activated as a research page).
- No public UI, no alerts, no signal output.
