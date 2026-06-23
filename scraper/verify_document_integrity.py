"""
Document integrity verifier — Phase 3.

Downloads every stored PDF from the storage backend and recomputes its
SHA-256 to confirm the stored bytes match the hash recorded in the filings
table.  Any mismatch or missing file is reported.

Usage:
    python3 -m scraper.verify_document_integrity
    python3 -m scraper.verify_document_integrity --filing-id 42
    python3 -m scraper.verify_document_integrity --limit 50
    python3 -m scraper.verify_document_integrity --status all
"""

import argparse
import hashlib
import logging
import sys

from .db import get_supabase_client
from . import storage as doc_storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _verify_filing(backend, row: dict) -> str:
    """
    Verify one filing.  Returns 'ok', 'missing', or 'mismatch'.
    Prints a line to stdout for non-ok results.
    """
    filing_id      = row["id"]
    storage_path   = row.get("storage_path")
    expected_sha   = row.get("pdf_sha256")
    pdf_url        = row.get("pdf_url", "")

    if not storage_path:
        print(f"MISSING  #{filing_id:<6}  storage_path=NULL  {pdf_url[:80]}")
        return "missing"

    if not expected_sha:
        print(f"MISSING  #{filing_id:<6}  pdf_sha256=NULL    {pdf_url[:80]}")
        return "missing"

    if not backend.exists(storage_path):
        print(f"MISSING  #{filing_id:<6}  {storage_path}")
        return "missing"

    try:
        pdf_bytes   = backend.download(storage_path)
        actual_sha  = hashlib.sha256(pdf_bytes).hexdigest()
    except Exception as exc:
        print(f"ERROR    #{filing_id:<6}  download failed: {exc}")
        return "missing"

    if actual_sha == expected_sha:
        logger.debug("OK  #%d  %s", filing_id, storage_path)
        return "ok"

    print(
        f"MISMATCH #{filing_id:<6}  expected={expected_sha[:16]}…  "
        f"actual={actual_sha[:16]}…  {storage_path}"
    )
    return "mismatch"


def verify_all(
    client,
    backend,
    *,
    filing_id: int = None,
    limit: int = None,
    include_all_statuses: bool = False,
) -> dict:
    """
    Verify stored documents.  Returns a summary dict.

    By default only 'completed' filings are checked.  Pass
    include_all_statuses=True to include filings that have a storage_path
    but are in another status (edge case from partial failures).
    """
    query = client.table("filings").select("id, pdf_url, status, storage_path, pdf_sha256")

    if filing_id is not None:
        query = query.eq("id", filing_id)
    elif not include_all_statuses:
        query = query.eq("status", "completed")

    if limit:
        query = query.limit(limit)

    result = query.execute()
    rows   = result.data or []

    summary = {"ok": 0, "missing": 0, "mismatch": 0, "total": len(rows)}

    for row in rows:
        outcome = _verify_filing(backend, row)
        summary[outcome] += 1

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify integrity of stored PDFs against filings.pdf_sha256."
    )
    parser.add_argument("--filing-id", type=int, help="Verify a single filing by ID")
    parser.add_argument("--limit", type=int, help="Verify only first N filings")
    parser.add_argument(
        "--status", default="completed",
        choices=["completed", "all"],
        help="Which filings to verify (default: completed)",
    )
    args = parser.parse_args()

    try:
        client = get_supabase_client()
    except ValueError as exc:
        logger.error("Supabase not configured: %s", exc)
        sys.exit(1)

    backend = doc_storage.get_storage_backend(client)
    logger.info("Backend: %r", backend)

    summary = verify_all(
        client,
        backend,
        filing_id=args.filing_id,
        limit=args.limit,
        include_all_statuses=(args.status == "all"),
    )

    print()
    print("=" * 60)
    print("INTEGRITY SUMMARY")
    print("=" * 60)
    print(f"  Total checked : {summary['total']}")
    print(f"  OK            : {summary['ok']}")
    print(f"  Missing       : {summary['missing']}")
    print(f"  Mismatch      : {summary['mismatch']}")
    print("=" * 60)

    if summary["mismatch"] > 0:
        logger.error("%d SHA-256 mismatch(es) detected — storage may be corrupted", summary["mismatch"])
        sys.exit(2)
    if summary["missing"] > 0:
        logger.warning("%d filing(s) have no stored document", summary["missing"])
        sys.exit(1)

    logger.info("All %d stored documents verified OK", summary["ok"])


if __name__ == "__main__":
    main()
