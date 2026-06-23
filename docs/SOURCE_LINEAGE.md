# Source Lineage

## What it means

Every transaction in InsideWatch traces to a verifiable primary source.

```
transactions.source_filing_id
      │
      └─► filings.id
                │
                ├── pdf_url            Borsa Italiana URL of the original PDF
                ├── storage_path       filings/{year}/{month}/{sha256}.pdf
                ├── pdf_sha256         SHA-256 of the bytes at time of ingestion
                ├── file_size_bytes    Exact byte count
                └── raw_extracted_text Full pdfplumber extraction (pre-parse)
```

A transaction can be traced to the exact byte sequence that produced it — not merely the URL where the document was found, but the document itself in archival storage.

## Why the path is the hash

The storage path includes the document's SHA-256:

```
filings/2024/03/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855.pdf
```

This means:
- **Idempotent uploads**: the same document always maps to the same path. If the scraper encounters the same PDF a second time (retry, duplicate listing) it finds it already stored and skips.
- **Automatic versioning**: if Borsa Italiana silently replaces a PDF at the same URL with different content, the new bytes hash differently and are stored at a new path. Both versions coexist; neither overwrites the other.
- **Integrity-by-path**: the presence of a file at a sha256-based path is evidence that its content matches the hash. `verify_document_integrity` confirms this by re-downloading and re-hashing.

## Lineage chain

```
Borsa Italiana listing page
        │
        │  scraper/fetcher.py  →  listing URL scraped
        │
        ▼
    pdf_url (in filings table)
        │
        │  scraper/fetcher.py  →  download_pdf()
        │
        ▼
    pdf_bytes in memory
        │
        ├──► hashlib.sha256()  →  pdf_sha256
        ├──► storage.store_pdf()  →  storage_path (Supabase Storage or local)
        ├──► storage.extract_raw_text()  →  raw_extracted_text
        │
        │  scraper/parser.py  →  parse_pdf()
        │
        ▼
    ParsedTransaction objects
        │
        │  scraper/db.py  →  upsert_transaction()
        │
        ▼
    transactions table
        │
        └── source_filing_id  →  filings.id  (FK, ON DELETE SET NULL)
```

## Intended guarantees (Phase 3 gate)

Every filing that reaches status = `completed` in the ledger has:

1. `pdf_sha256` — verified at completion time
2. `storage_path` — PDF stored at the backend (Supabase Storage in production)
3. `file_size_bytes` — recorded at storage time
4. `raw_extracted_text` — pre-parse text captured before any field extraction

Filings with `storage_path = NULL` predate Phase 3 or encountered a storage failure and need re-processing.

## Re-processing

To re-extract transactions from a stored document without hitting Borsa Italiana:

1. Download the PDF from `storage_path` via the Supabase Storage bucket `filings-pdfs`.
2. Verify SHA-256 matches `pdf_sha256`.
3. Run `parse_pdf(pdf_bytes, pdf_url, filing_date)`.

This is intentionally a manual step. The scraper does not automatically re-parse stored documents; that would require a separate Phase (future roadmap).

## Verification

```bash
python3 -m scraper.verify_document_integrity
```

Re-downloads every stored PDF, recomputes its SHA-256, and compares it to `filings.pdf_sha256`. Reports `OK`, `MISSING`, or `MISMATCH`.

Exit codes: 0 = all OK, 1 = some missing, 2 = at least one mismatch.
