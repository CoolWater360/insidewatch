# InsideWatch — Architecture Reference

> Status: Italy Alpha / Private Beta — source-linked, review-flagged, not investment advice.
> Last updated: 2026-06-23

---

## 1. High-level overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Borsa Italiana SDIR / NIS Feed                                     │
│  https://www.borsaitaliana.it/.../internal-dealing.html             │
└────────────────────┬────────────────────────────────────────────────┘
                     │  HTML listing + PDF links
           ┌─────────▼──────────┐
           │  Python Scraper    │   GitHub Actions (free tier)
           │  scraper/          │
           │  ├ fetcher.py      │   fetch HTML (requests + BS4)
           │  ├ parser.py       │   extract structured data (pdfplumber)
           │  ├ db.py           │   upsert to Supabase
           │  ├ alerts.py       │   email (Resend) + Telegram
           │  ├ listener.py     │   lightweight 1-min watcher
           │  └ run_phase2.py   │   full sweep (daily backfill)
           └─────────┬──────────┘
                     │  supabase-py (anon key — see Security section)
           ┌─────────▼──────────┐
           │  Supabase          │   Managed PostgreSQL + Auth + Storage
           │  (free tier)       │   RLS: currently DISABLED (risk)
           │  Tables:           │
           │  ├ companies       │
           │  ├ insiders        │
           │  ├ transactions    │
           │  └ scraper_runs    │
           └─────────┬──────────┘
                     │  @supabase/supabase-js (anon key — see Security)
           ┌─────────▼──────────┐
           │  Next.js 16        │   Vercel / local
           │  App Router        │
           │  app/              │
           │  ├ page.tsx        │   Home — transaction list
           │  ├ signals/        │   Cluster-buy signals
           │  └ company/[id]/   │   Company detail
           │  components/       │   TransactionTable, Filters, etc.
           │  lib/              │
           │  ├ queries.ts      │   Supabase query layer
           │  └ supabase.ts     │   Client singleton
           └────────────────────┘
```

---

## 2. Frontend

**Stack:** Next.js 16 (App Router), React 18, TypeScript, Tailwind CSS 3.

**Pages:**
| Route | Type | Description |
|---|---|---|
| `/` | Server Component | Paginated transaction list with Filters |
| `/signals` | Server Component | Cluster-buy signal cards (≥2 insiders, 7-day window) |
| `/company/[id]` | Server Component | Company detail — stats + transaction history |

**Language:** Italian/English toggle via `insidewatch_lang` cookie, read server-side at render time and client-side via `LanguageProvider` context.

**Data access:** All DB queries live in `lib/queries.ts`. The frontend uses the anon Supabase key (see Security section — this is a current risk).

**No API routes exist.** There is no `/api/` layer. The frontend queries Supabase directly.

---

## 3. Scraper / Listener

**Entry points:**

| Command | Schedule | Purpose |
|---|---|---|
| `python -m scraper.run_listener` | Every 1 min (weekdays 08-18 UTC) | Lightweight: fetch listing page 1, skip cached URLs, parse + insert new PDFs only |
| `python -m scraper.run_phase2` | Daily 07:00 UTC | Full sweep: iterates every company in DB, downloads up to 15 PDFs per company |

**Key modules:**
- `fetcher.py` — HTTP via `requests.Session` with retry/backoff, `BlockedError` for 403/429/CAPTCHA detection.
- `parser.py` — pdfplumber text extraction, handles 3 distinct Borsa Italiana PDF layouts (Layout 1: older/compact, Layout 2: bilingual ESMA form, Layout 3: 2016/523 compact form). `_degarble()` recovers text from two-column interleaving.
- `db.py` — `upsert_transaction()` with `raw_hash` dedup; `_resolve_company()` using name-then-ISIN lookup; `_resolve_insider()` with exact-then-ilike fallback.
- `alerts.py` — `dispatch()` sends email (Resend) + Telegram for Tier 1/2 verified non-review transactions.
- `listener.py` — Lightweight watcher: fetches only page 1 of listing (6 s timeout), diffs against `recent_scrapes.json` file cache, processes only new URLs.
- `cache.py` — Atomic JSON file cache for listener's seen-URL set (last 20 entries). **Current risk: ephemeral on GitHub Actions; persisted via `actions/cache`.**

**Parser limitations (known):**
- Three layout variants handled; additional variants will fail silently (no transaction returned, no error logged to Supabase).
- Price stored as `float`, not `Decimal`. Rounding errors possible at high precision.
- `raw_hash` does not include filing identity or document hash — see Phase 4.

---

## 4. Database

**Provider:** Supabase (PostgreSQL 15, free tier). No local DB for development — all environments point at the single hosted instance.

**Tables as of 2026-06-23:**

```
companies
  id, name, ticker, isin, sector, ir_internal_dealing_url, priority_tier,
  created_at, updated_at

insiders
  id, full_name, role, company_id (FK → companies), insider_verified,
  role_category, created_at

transactions
  id, insider_id (FK), company_id (FK), transaction_date, filed_date,
  direction, instrument_type, isin, quantity, unit_price, total_value,
  currency, source_url, raw_hash (UNIQUE), transaction_type*, needs_review*,
  created_at, updated_at
  (* added via migration_phase1.sql — NOT in schema.sql — missing from
     fresh schema installs)

scraper_runs
  tier (PK), last_successful_run, companies_crawled,
  transactions_inserted, updated_at
```

**Missing from schema.sql** (in live DB but not canonical schema file):
- `transactions.transaction_type`
- `transactions.needs_review`
- `insiders.insider_verified`
- `insiders.role_category`
- `companies.priority_tier`

**Tables that do not yet exist:**
- `filings` (filing-level ledger — Phase 2)
- `transaction_versions` (audit trail — Phase 4)
- `filing_processing_runs` (Phase 4)
- `issuers` / `issuer_aliases` / `securities` (Phase 6)

---

## 5. Supabase usage

| Feature | Status |
|---|---|
| PostgreSQL | In use |
| Auth | Not used (no user accounts) |
| Storage | Not used (PDFs not stored) |
| Edge Functions | Not used |
| RLS | **Disabled** — all tables are publicly readable and writable |
| Realtime | Not used |

---

## 6. Deployment / Workflow

**GitHub Actions** (`.github/workflows/scraper.yml`):

| Job | Trigger | Timeout |
|---|---|---|
| `listener` | `*/1 8-18 * * 1-5` (every minute, market hours) | 3 min |
| `sweep` | `0 7 * * 1-5` (daily 07:00 UTC) | 25 min |

Both jobs pass `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHANNEL_ID` as secrets.

Listener uses `actions/cache` to persist `scraper/recent_scrapes.json` across runs.

**Frontend deployment:** Not yet deployed publicly. Runs locally via `npm run dev` or `./dev.sh`.

---

## 7. Environment variables

| Variable | Used by | Description |
|---|---|---|
| `SUPABASE_URL` | Next.js + Scraper | Supabase project URL |
| `SUPABASE_KEY` | Next.js + Scraper | **Anon key** — same key for both; no separation |
| `NEXT_PUBLIC_SUPABASE_URL` | Next.js (fallback) | Conventional alias |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Next.js (fallback) | Conventional alias |
| `RESEND_API_KEY` | Scraper | Email alert via Resend |
| `ALERT_EMAIL` | Scraper | Recipient(s) comma-separated |
| `ALERT_FROM_EMAIL` | Scraper | Verified sender address |
| `TELEGRAM_BOT_TOKEN` | Scraper | Telegram bot (not yet set in Actions secrets) |
| `TELEGRAM_CHANNEL_ID` | Scraper | Telegram channel ID (not yet set) |

**Critical gap:** There is no `SUPABASE_SERVICE_ROLE_KEY`. The scraper uses the anon key to write to the database. This works only because RLS is disabled — enabling RLS without adding a service-role key to the scraper would silently break all ingestion.

---

## 8. Security model (current state — risks)

| Area | Current state | Risk |
|---|---|---|
| Frontend Supabase key | Anon key, server-side only | Low — key is not in client bundle |
| Scraper Supabase key | Anon key | **High** — RLS disabled, anon key has full write access |
| RLS | Disabled | **Critical** — any anon key holder can read/write/delete all data |
| Service-role key | Not configured | **High** — needed before RLS can be enabled safely |
| PDF storage | None | Medium — source evidence not preserved |
| API authentication | No API exists | N/A |
| GitHub Actions secrets | Keys stored as secrets | Low |

---

## 9. Files that will change in later phases

| File | Phase(s) |
|---|---|
| `db/schema.sql` | 1 — canonical schema catch-up |
| `lib/queries.ts` | 1 — fix null-sensitive filtering |
| `lib/supabase.ts` | 1 — service-role key separation |
| `lib/types.ts` | 1, 4 — new fields |
| `scraper/db.py` | 2, 3, 4 — filing ledger, storage, versioning |
| `scraper/listener.py` | 2 — replace file cache with filing ledger |
| `scraper/run_phase2.py` | 2 — use filing ledger |
| `scraper/parser.py` | 5, 7 — confidence scoring, taxonomy |
| `scraper/cache.py` | 2 — deprecate after filing ledger ships |
| `scraper/models.py` | 1, 2, 4, 5 — new fields |
| `scraper/alerts.py` | 10 — API audit log |
| `.github/workflows/scraper.yml` | 2, 9 — retry job |
| `.env.local.example` | 1, 10 — service-role key, API key |
| `app/page.tsx` | 1, 8 |
| `app/signals/page.tsx` | 7 |
| **New files** | |
| `scraper/storage.py` | 3 |
| `scraper/filing_ledger.py` | 2 |
| `scraper/retry_failed_filings.py` | 2 |
| `scraper/reprocess_filing.py` | 2, 3 |
| `scraper/inspect_filing.py` | 2 |
| `scraper/classifier.py` | 7 |
| `app/api/v1/` | 10 |
| `app/internal/` | 8 |
| `db/migrations/` | 1–7 (numbered sequential) |
| `tests/` | 1–10 |
| `docs/` | 0–12 |
