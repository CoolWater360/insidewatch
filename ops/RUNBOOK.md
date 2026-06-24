# InsideWatch — Production Activation Runbook

Precise deployment order for first-time private production activation.
No steps may be skipped or reordered.

---

## Prerequisites

- [ ] Supabase project created (free tier is sufficient to start)
- [ ] **Spend Cap set to €0** → Supabase Dashboard → Settings → Billing → Spend Cap
- [ ] Vercel project connected to this repo
- [ ] Local `.env.local` populated from `.env.local.example`
- [ ] Python ≥ 3.11 installed with `pip install -r requirements.txt` complete

---

## Step 1 — Apply database migrations (Supabase SQL Editor)

Run each file in strict order.  Paste the contents into the Supabase SQL Editor
and execute.  Do **not** skip a migration even if it appears to do nothing — each
one is idempotent (safe to re-run).

| # | File | Purpose |
|---|------|---------|
| 1 | `db/migrations/001_preflight.sql` | Pre-flight environment check |
| 2 | `db/migrations/001_schema_integrity.sql` | Schema-integrity constraints |
| 3 | `db/migrations/002_filings_table.sql` | Filings ledger |
| 4 | `db/migrations/003_transaction_identity.sql` | Identity hashing |
| 5 | `db/migrations/004_versioning.sql` | Transaction versioning |
| 6 | `db/migrations/005_issuer_master.sql` | Company / insider tables |
| 7 | `db/migrations/006_classification.sql` | Classification fields |
| 8 | `db/migrations/007_review_queue.sql` | Review queue |
| 9 | `db/migrations/008_audit_log.sql` | Audit logging |
| 10 | `db/migrations/009_internal_rpc.sql` | Internal RPC functions |
| 11 | `db/migrations/010_latency_timestamps.sql` | Latency timestamp columns |
| 12 | `db/migrations/011_api_audit_log.sql` | API audit logging |
| 13 | `db/migrations/012_patch_constraint_and_rpc.sql` | Constraint patches |
| 14 | `db/migrations/013_rls_policies.sql` | RLS policies + public views |
| 15 | `db/migrations/014_stored_utc.sql` | `stored_utc` latency column |

After applying migration 014, confirm the column exists:

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'filings' AND column_name = 'stored_utc';
```

---

## Step 2 — Enable Row Level Security (manual — Supabase SQL Editor)

Migration 013 installs the RLS policies but does **not** enable them — that is
a manual step to prevent accidental lockout during setup.

After confirming the anon/service-role key setup below is correct, run:

```sql
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies    ENABLE ROW LEVEL SECURITY;
ALTER TABLE insiders     ENABLE ROW LEVEL SECURITY;
```

Verify:

```sql
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('transactions', 'companies', 'insiders');
```

All three rows must show `rowsecurity = true`.

---

## Step 3 — Create private Storage bucket

1. Supabase Dashboard → Storage → New Bucket
2. Name: `filings-pdfs`
3. **Public: OFF** (access only via service-role key)
4. No additional policies needed (scraper uses service-role key server-side)

Verify from the SQL Editor:

```sql
SELECT id, name, public FROM storage.buckets WHERE name = 'filings-pdfs';
```

---

## Step 4 — Set GitHub Actions secrets

Repository → Settings → Secrets and variables → Actions → New repository secret.

| Secret name | Where to find it |
|---|---|
| `SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → service_role (keep secret) |
| `RESEND_API_KEY` | Resend dashboard → API Keys |
| `ALERT_EMAIL` | Destination address for alert emails |
| `ALERT_FROM_EMAIL` | Verified sender address in Resend |
| `TELEGRAM_BOT_TOKEN` | BotFather → /mybots → API Token |
| `TELEGRAM_CHANNEL_ID` | Channel ID including `-100` prefix |
| `INTERNAL_SECRET` | Random 32-char string — generate with `openssl rand -hex 16` |
| `INSIDEWATCH_API_KEY` | Random 32-char string — generate with `openssl rand -hex 16` |

**Critical:** `SUPABASE_SERVICE_ROLE_KEY` must never be added as a Vercel
`NEXT_PUBLIC_*` variable.  It is a backend-only credential that bypasses RLS.

---

## Step 5 — Set Vercel environment variables

Vercel Dashboard → Project → Settings → Environment Variables.

| Variable | Environment | Notes |
|---|---|---|
| `SUPABASE_URL` | Production, Preview | Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Production, Preview | Anon key — safe to expose to browser |
| `SUPABASE_KEY` | Production, Preview | Same as anon key; used by server-side helpers |
| `SUPABASE_SERVICE_ROLE_KEY` | Production | **Server-only — never prefix with NEXT_PUBLIC_** |
| `INTERNAL_SECRET` | Production | Must match GitHub Actions secret exactly |
| `INSIDEWATCH_API_KEY` | Production | Must match GitHub Actions secret exactly |
| `RESEND_API_KEY` | Production | Alert email delivery |
| `ALERT_EMAIL` | Production | Alert destination |
| `ALERT_FROM_EMAIL` | Production | Verified Resend sender |

After adding, redeploy from the Vercel dashboard (or push a commit) so the new
env vars are picked up.

---

## Step 6 — Configure `.github/workflows/scraper.yml`

The workflow uses repository secrets automatically.  No file edits are needed
beyond confirming the workflow file is committed and the `on.schedule` cron
expression matches the desired run frequency.

To trigger a manual run:

```
GitHub → Actions → Scraper → Run workflow
```

---

## Step 7 — Verify local environment

```bash
# Confirm .env.local contains all required keys
python3 -c "
import os, dotenv
dotenv.load_dotenv('.env.local')
for k in ['SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY','INTERNAL_SECRET']:
    v = os.getenv(k)
    print(f'{k}: {\"OK\" if v else \"MISSING\"} ({len(v or \"\")} chars)')
"
```

---

## Step 8 — Database connectivity smoke test

```bash
python3 -m scraper.run_health_check
```

Expected output ends with `Health check passed` and zero `FAIL` lines.

If the check fails:
- Confirm `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are correct
- Confirm the migrations completed without errors
- Check Supabase → Logs → API for 401/403 responses

---

## Step 9 — Manual listener execution (first run)

The GitHub Actions workflow runs the scraper on a schedule.  For the first
run, trigger it manually to confirm everything works end-to-end:

```bash
# Runs the full pipeline: fetch listing → download PDF → parse → store
python3 -m scraper.run_listener
```

Monitor output for:
- `[OK] Discovered N new filings`
- `[OK] Stored N PDFs`
- `[OK] Parsed N transactions`

Any `ERROR` line warrants investigation before proceeding.

---

## Step 10 — Storage lineage smoke test

```bash
python3 -m scraper.cli verify-storage-lineage
```

Expected: zero `MISSING` entries for recently processed filings.
Historical filings (pre-Phase-2) may legitimately show missing storage
paths — these predate the local-file storage feature.

---

## Step 11 — API smoke test

Replace `$HOST` and `$KEY` with your Vercel deployment URL and
`INSIDEWATCH_API_KEY`:

```bash
export HOST="https://your-app.vercel.app"
export KEY="your_insidewatch_api_key"

# Health endpoint
curl -sf -H "Authorization: Bearer $KEY" "$HOST/api/v1/health" | jq .

# Transaction list (first page)
curl -sf -H "Authorization: Bearer $KEY" "$HOST/api/v1/transactions?limit=5" | jq .total_count
```

Expected: `200 OK` on both.  Non-zero `total_count` once at least one filing
has been processed.

---

## Step 12 — Internal console smoke test

```bash
curl -sf -u "admin:$INTERNAL_SECRET" "$HOST/internal/" | head -20
```

Expected: HTML page with the internal console, not a 401.

---

## Step 13 — Operations report (baseline)

Generates the first baseline report and confirms the email delivery path works:

```bash
python3 -m scraper.generate_daily_operations_report --pretty
```

If `RESEND_API_KEY` and `ALERT_EMAIL` are set, an email is sent.
Check the inbox within a few minutes.

---

## Rollback procedure

All migrations are additive (no DROP statements).  To roll back to a previous
application version:

1. Revert the Vercel deployment (Vercel Dashboard → Deployments → Redeploy
   previous).
2. The database schema remains in place — no rollback is needed unless a
   migration added a breaking constraint.
3. To undo `stored_utc`:
   ```sql
   -- Safe: column is nullable; removing it does not affect existing data.
   ALTER TABLE filings DROP COLUMN IF EXISTS stored_utc;
   DROP INDEX IF EXISTS idx_filings_stored_utc;
   ```

---

## Post-deployment checklist

Run after the first 24 hours of production traffic:

```bash
# Quality metrics baseline
python3 -m scraper.quality_snapshot --save ops/snapshots/baseline.json

# Latency coverage (how many filings have all 7 stage timestamps)
python3 -m scraper.cli backfill-latency --limit 200

# Full operations report
python3 -m scraper.generate_daily_operations_report --pretty
```

Review the report for:
- `review_rate_pct` < 20 %
- `unknown_direction_pct` < 5 %
- `stale_in_progress_count` ≤ 5
- P95 end-to-end latency < 3 600 s (1 h)

Do not make any public or external signal-quality claims until these metrics
have been stable for ≥ 7 days.
