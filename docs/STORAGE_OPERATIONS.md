# Storage Operations

## Overview

Phase 3 introduces persistent raw PDF storage to preserve every document the scraper downloads. PDFs are stored before parsing and indexed by their SHA-256 hash.

## Supabase bucket setup (manual — one time)

Before running the scraper in production:

1. Open **Supabase Dashboard → Storage → New bucket**
2. Name: `filings-pdfs`
3. Public access: **OFF** (private)
4. No file size limit needed (MAR filings are typically < 500 KB)
5. Click **Create bucket**

The service-role key used by the scraper bypasses RLS, so no bucket policy is needed for the scraper itself. Never expose the bucket as public.

## Storage path format

```
filings/{year}/{month:02d}/{sha256}.pdf
```

Examples:
```
filings/2024/03/e3b0c44298fc1c149afbf4c8996fb924...pdf
filings/2024/11/a4d8c3e0fbb21cc8b37d0a2c98f72b10...pdf
filings/undated/7f83b1657ff1fc53b92dc18148a1d65d...pdf
```

The `undated/` prefix is used when `filing_date` is absent or could not be parsed.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` for dev; `supabase` for production |
| `LOCAL_STORAGE_ROOT` | `./local_storage` | Root directory for the local adapter |
| `SUPABASE_STORAGE_BUCKET` | `filings-pdfs` | Supabase Storage bucket name |

Set `STORAGE_BACKEND=supabase` in GitHub Actions and any production environment.

## Adapter behaviour

### Local (`STORAGE_BACKEND=local`)

- Files are stored under `LOCAL_STORAGE_ROOT` (default: `./local_storage`)
- Directory structure mirrors the storage path: `./local_storage/filings/2024/03/{sha256}.pdf`
- Suitable for local development and CI tests

### Supabase (`STORAGE_BACKEND=supabase`)

- Uploads to the `filings-pdfs` bucket using the service-role key
- Upload is checked for existence first (via `list()`) to avoid duplicate writes
- If two workers race on the same file, the second silently skips (idempotent)
- Downloaded via `supabase.storage.from_("filings-pdfs").download(path)`

## Integrity verification

```bash
python3 -m scraper.verify_document_integrity           # all completed filings
python3 -m scraper.verify_document_integrity --limit 50
python3 -m scraper.verify_document_integrity --filing-id 42
python3 -m scraper.verify_document_integrity --status all
```

For each filing, the script:
1. Reads `storage_path` and `pdf_sha256` from the `filings` table
2. Downloads the file from the storage backend
3. Recomputes SHA-256 of the downloaded bytes
4. Reports `OK` / `MISSING` / `MISMATCH`

**Exit codes**: 0 = all OK, 1 = some missing, 2 = at least one mismatch (corruption).

Run this periodically in CI, or after any storage migration.

## Recovering missing storage

If a filing has `storage_path = NULL` (Phase 3 not yet reached it, or storage failed):

1. Reset the filing to `pending`:
   ```bash
   python3 -m scraper.cli retry <filing-id>
   ```
2. On the next scraper run, the filing will be re-downloaded, stored, and re-parsed.

This is safe: transaction dedup via `raw_hash` prevents duplicate rows.

## Storage failure policy

PDF storage is a **required** step for new filings. If `store_pdf()` fails (network error, auth error, bucket not found), the filing is marked `failed` and will be retried on the next run. This ensures that every filing that reaches `completed` has a stored PDF.

The metadata update (`record_storage`) is non-fatal: if it fails after a successful upload, the filing continues to parse and complete. On the next run, `storage_path` will already exist (idempotent), and the metadata will be written.

## Security

- The `filings-pdfs` bucket must remain private.
- The `SUPABASE_SERVICE_ROLE_KEY` is the only credential that can access it.
- Never add `NEXT_PUBLIC_SUPABASE_*` variables that expose the service-role key.
- The scraper accesses storage with the same service-role key used for database writes.
