# InsideWatch — Operations Reference

> Status: Italy Alpha — private beta.
> Last updated: 2026-06-23

---

## 1. Local development setup

### Prerequisites

- Node.js v24 (managed via nvm — `.nvmrc` not yet present; use `nvm use 24`)
- Python 3.11+
- A `.env.local` file (copy from `.env.local.example`)

### Frontend

```bash
cd "Italian Internal Dealing Tracker"
npm install
npm run dev
# App available at http://localhost:3000
```

Or using the helper script:
```bash
./dev.sh
```

### Scraper (local)

```bash
# Install Python dependencies
pip install -r scraper/requirements.txt

# Run the full sweep (all tiers, 1 worker, 2s delay)
python3 -m scraper.run_phase2

# Run the lightweight listener (single pass)
python3 -m scraper.run_listener

# Run listener with debug output
python3 -m scraper.run_listener --verbose

# Crawl only Tier 1 companies, 3 workers
python3 -m scraper.run_phase2 --tier 1 --workers 3 --delay 1.0

# Crawl a single company
python3 -m scraper.run_phase2 --company "Eni"
```

---

## 2. Environment variables

Copy `.env.local.example` to `.env.local` and fill in:

```bash
# Required — Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key          # WARNING: same key used by frontend and scraper

# Scraper alerts (optional but recommended)
RESEND_API_KEY=re_...
ALERT_EMAIL=you@example.com
ALERT_FROM_EMAIL=InsideWatch <alerts@yourdomain.com>

# Telegram alerts (optional — add bot token and channel ID when ready)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=
```

**Future (Phase 1):**
```bash
SUPABASE_SERVICE_ROLE_KEY=...  # Scraper-only — never expose to frontend
INSIDEWATCH_API_KEY=...        # Private API authentication (Phase 10)
```

---

## 3. Database migrations

Migrations are SQL files stored in `db/`. They are applied manually via the Supabase SQL editor (Dashboard → SQL Editor → New Query → paste → Run).

**Current migration order (must be applied in this order on a fresh database):**

```
1. db/schema.sql
2. db/migration_phase1.sql      (transaction_type, needs_review)
3. db/migration_phase4.sql      (insider_verified, role_category)
4. db/migration_phase5.sql      (priority_tier, scraper_runs)
5. db/migration_phase_a.sql     (dedup insiders, normalize names)
```

All migrations are idempotent (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`) and can be re-applied without side effects.

**Verification query (run after each migration):**
```sql
SELECT column_name, data_type, column_default, is_nullable
FROM information_schema.columns
WHERE table_name = 'transactions'
ORDER BY ordinal_position;
```

**Rollback strategy:**
- Supabase free tier does not provide point-in-time recovery.
- Before any migration, export the affected tables:
  ```sql
  -- Run in SQL editor, then download CSV
  SELECT * FROM transactions ORDER BY created_at DESC;
  SELECT * FROM insiders;
  SELECT * FROM companies;
  ```
- Additive-only migrations (ADD COLUMN, CREATE TABLE) are trivially reversible with DROP COLUMN / DROP TABLE.
- Data-modifying migrations (UPDATE, DELETE) must have a documented rollback UPDATE.

---

## 4. GitHub Actions

**Workflow file:** `.github/workflows/scraper.yml`

**Jobs:**

| Job | Trigger | Timeout | Command |
|---|---|---|---|
| `listener` | `*/1 8-18 * * 1-5` (weekdays, market hours) | 3 min | `python3 -m scraper.run_listener` |
| `sweep` | `0 7 * * 1-5` (daily 07:00 UTC) | 25 min | `python3 -m scraper.run_phase2 --tier 3 ...` |

**Important note on listener scheduling:** GitHub Actions has a guaranteed minimum delivery latency of ~5–15 minutes at peak times even when cron is set to `*/1`. The 1-minute cron expresses intent. For sub-5-minute latency, a self-hosted runner or a cloud run service (e.g., Render free tier, fly.io) would be required.

**Listener URL cache persistence:**
The listener uses `actions/cache` to persist `scraper/recent_scrapes.json` between runs. Cache key format: `listener-cache-<run_id>`. The restore-key `listener-cache-` always restores the most recent save.

**Manual trigger (workflow_dispatch):**
```
GitHub Actions → InsideWatch Scraper → Run workflow
  → job: listener | sweep
  → tier (sweep only): 1 | 2 | 3
```

**Required GitHub Secrets:**
```
SUPABASE_URL
SUPABASE_KEY
RESEND_API_KEY
ALERT_EMAIL
ALERT_FROM_EMAIL
TELEGRAM_BOT_TOKEN      (add when ready — alert fires immediately)
TELEGRAM_CHANNEL_ID     (add when ready)
```

---

## 5. Monitoring (current state)

**Available signals:**
- GitHub Actions job status (green/red)
- `scraper_runs` table: `SELECT * FROM scraper_runs ORDER BY tier` — last successful sweep per tier
- Email/Telegram alerts: fire on each new Tier 1/2 verified transaction

**Gaps (to be addressed in Phase 9):**
- No per-filing success/failure tracking
- No latency metrics
- No coverage metrics (how many companies had filings today vs. yesterday)
- No review queue size monitoring
- No parser failure rate tracking
- No alert if listener has not run in the last 30 minutes

**Health check (manual):**
```sql
-- When did each tier last sweep successfully?
SELECT tier, last_successful_run, companies_crawled, transactions_inserted
FROM scraper_runs ORDER BY tier;

-- How many transactions were inserted in the last 24 hours?
SELECT COUNT(*), MIN(created_at), MAX(created_at)
FROM transactions
WHERE created_at > NOW() - INTERVAL '24 hours';

-- Are there transactions needing review?
SELECT COUNT(*) FROM transactions WHERE needs_review = TRUE;
```

---

## 6. Known operational risks

### Risk 1 — CRITICAL: Silent data loss on listener failures

**Status:** Confirmed present.

In `scraper/listener.py`, the `finally` block in `run_listener()`:
```python
finally:
    seen_list.append(row.pdf_url)
```
This marks the URL as seen even when:
- PDF download failed (BlockedError, Timeout, 404)
- Parser returned no transactions (parsing error or unsupported layout)
- Database insert failed (network, constraint violation)

**Result:** A filing that fails at any stage is silently treated as processed. The next listener run skips it. The daily sweep may catch it, but only if the URL appears in the company's listing history within the next sweep's window.

**Fix:** Phase 2 (filing ledger) — replace file cache with DB state machine; failed filings scheduled for retry.

---

### Risk 2 — HIGH: RLS disabled on Supabase

**Status:** Confirmed present.

All Supabase tables are publicly readable and writable by anyone who discovers the anon key. The anon key is visible in `.env.local` and is transmitted in every API call from the frontend.

**Fix:** Phase 1 — enable RLS, separate anon key (read-only) from service-role key (write).

---

### Risk 3 — HIGH: `transaction_type = NULL` silently excluded from frontend

**Status:** Confirmed present.

Frontend queries use `.neq("transaction_type", "grant")` which excludes NULLs in PostgREST. Any legacy record without `transaction_type` set is invisible in the UI.

**Mitigation:** Apply `migration_phase1.sql` — sets `transaction_type` defaults for all existing rows.
**Fix:** Phase 1 — replace `.neq()` chain with explicit inclusive filter.

---

### Risk 4 — HIGH: No source PDF preservation

**Status:** Confirmed present.

Source PDFs are not stored. `source_url` points to Borsa Italiana CDN URLs which:
- May be removed after a retention period
- May change if Borsa Italiana restructures their storage
- Cannot be used to verify or reprocess historical data

**Fix:** Phase 3 — Supabase Storage adapter with deterministic path and SHA-256 verification.

---

### Risk 5 — MEDIUM: Weak `raw_hash` allows silent deduplication of distinct events

**Status:** Confirmed present (see DATA_MODEL.md §7).

**Fix:** Phase 4 — redesign transaction identity.

---

### Risk 6 — MEDIUM: No corrections without overwriting (no audit trail)

**Status:** Confirmed present.

If a transaction record contains an incorrect price, direction, or insider name, the only way to correct it is a direct SQL UPDATE. No prior version is preserved.

**Fix:** Phase 4 — `transaction_versions` table with append-only versioning.

---

### Risk 7 — MEDIUM: Issuer matching via fuzzy `ilike`

**Status:** Confirmed present.

`_resolve_company()` uses case-insensitive name matching as the primary issuer resolution mechanism. This creates duplicate company rows when:
- The published name varies (e.g., "A2A S.p.A." vs "A2A")
- Accents are stripped or added
- Abbreviations differ

**Current mitigation:** ISIN secondary lookup partially compensates.
**Fix:** Phase 6 — issuer master with explicit alias table.

---

### Risk 8 — LOW: `schema.sql` is stale

**Status:** Confirmed present.

`db/schema.sql` does not include `transaction_type`, `needs_review`, `insider_verified`, `role_category`, or `priority_tier`. A fresh database created from `schema.sql` would be missing these fields.

**Fix:** Phase 1 — consolidate all migrations into `schema.sql`.

---

## 7. Rollback strategy per phase

| Phase | Rollback action |
|---|---|
| 0 | Delete `docs/` directory — no code changed |
| 1 | `DROP COLUMN` for new columns; revert `lib/queries.ts` via git |
| 2 | `DROP TABLE filings` (no FK deps yet); revert listener.py and run_phase2.py |
| 3 | Delete Supabase Storage bucket; revert storage.py usage |
| 4 | `DROP TABLE transaction_versions, filing_processing_runs`; existing transactions untouched |
| 5–12 | Per-phase SQL rollback scripts to be included in each migration file |

---

## 8. Seed data

`db/seed_companies.csv` — CSV with `name`, `ticker`, `isin`, `sector` for Italian listed companies.

`db/real_companies.json` — Extended company list with additional metadata.

Both are loaded by `db.py`'s `load_seed_companies()` at the start of each `run_phase2` crawl. Existing companies are skipped; new ones are inserted.

---

## 9. Supabase dashboard actions required (Phase 1)

The following cannot be scripted via SQL alone and require manual steps in the Supabase dashboard:

1. **Enable RLS** — Dashboard → Authentication → Policies → Enable RLS on each table.
2. **Create service_role secret** — Dashboard → Settings → API → copy `service_role` key into GitHub Secrets as `SUPABASE_SERVICE_ROLE_KEY`.
3. **Set Spend Cap to €0** — Dashboard → Settings → Billing → Spend Cap → €0 (prevents unexpected charges on free tier).
4. **Create Storage bucket** (Phase 3) — Dashboard → Storage → New bucket: `filings-pdfs`, Private.
