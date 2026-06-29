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

## Auditability — and the documented gap

Per-row review fields are written on every action:

- `ownership_events` and `entity_relationships` have `reviewed_by` + `reviewed_at`
  (migrations 018 / 017) — both are set, recording **who** and **when** alongside
  the new `review_status`.
- `entities` (migration 016) has **no** `reviewed_by` / `reviewed_at` columns, so
  entity reviews record `review_status` + `updated_at` only — the acting operator
  is the shared-admin actor (`INTERNAL_ACTOR_LABEL`), not stored per entity row.

**Minimal gap (not closed in this phase):** the central `internal_audit_log`
table (migration 008) has a CHECK constraint limiting `entity_type` to
`transaction | filing | unmatched_issuer`, so ownership records cannot be written
to it without a schema change. Per the phase instruction, no broad new audit
system was added. Closing the gap later would be a small, additive migration:

1. extend the `internal_audit_log.entity_type` CHECK to include
   `entity | ownership_event | entity_relationship`; and
2. add `reviewed_by` / `reviewed_at` columns to `entities` (optional); and
3. route ownership review mutations through audit-writing RPCs mirroring
   `db/migrations/009_internal_rpc.sql` so the UPDATE + audit INSERT are atomic.

This is documented here and intentionally **not** implemented now.

## Out of scope (unchanged)

- No new ownership collection.
- `/internal/ownership` is still a placeholder (not activated as a research page).
- No public UI, no alerts, no signal output.
