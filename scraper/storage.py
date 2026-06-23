"""
Raw document storage — Phase 3.

Preserves every PDF immediately after download under a deterministic path
derived from the document's SHA-256:

    filings/{year}/{month:02d}/{sha256}.pdf
    filings/undated/{sha256}.pdf   (when filing_date is absent or unparseable)

Because the path IS the SHA-256:
  · uploads are idempotent: same bytes → same path → skip silently if present;
  · the file's presence at that path is evidence of its content;
  · if Borsa Italiana silently replaces a PDF at the same URL, the new bytes
    hash to a different path — both versions are automatically preserved.

Adapters
────────
  LocalStorageBackend     — dev/testing; stores under LOCAL_STORAGE_ROOT.
  SupabaseStorageBackend  — production; uploads to a Supabase Storage bucket.

Factory
───────
  get_storage_backend(supabase_client=None)
  Reads STORAGE_BACKEND env var: 'local' (default) or 'supabase'.

Core helpers
────────────
  make_storage_path(sha256, filing_date) → str
  store_pdf(backend, pdf_bytes, sha256, filing_date) → (path, is_new)
  extract_raw_text(pdf_bytes) → str

Each adapter exposes:
  upload(pdf_bytes, path) → bool     True = newly stored, False = already existed
  exists(path) → bool
  download(path) → bytes
  file_size(path) → int              bytes; 0 if unknown
"""

import hashlib
import io
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_BUCKET     = "filings-pdfs"
_DEFAULT_LOCAL_ROOT = "./local_storage"


# ── Path helper ───────────────────────────────────────────────────────────────

def make_storage_path(sha256: str, filing_date: Optional[str]) -> str:
    """
    Return the deterministic storage path for a PDF.

    Format: filings/{year}/{month:02d}/{sha256}.pdf
    Fallback: filings/undated/{sha256}.pdf when the date is absent or invalid.
    """
    if filing_date:
        try:
            d = date.fromisoformat(str(filing_date)[:10])
            return f"filings/{d.year}/{d.month:02d}/{sha256}.pdf"
        except (ValueError, TypeError):
            pass
    return f"filings/undated/{sha256}.pdf"


# ── Local filesystem adapter ──────────────────────────────────────────────────

class LocalStorageBackend:
    """Filesystem adapter for development and testing."""

    def __init__(self, root: str = None):
        self.root = Path(root or os.getenv("LOCAL_STORAGE_ROOT", _DEFAULT_LOCAL_ROOT))

    def upload(self, pdf_bytes: bytes, path: str) -> bool:
        dest = self.root / path
        if dest.exists():
            logger.debug("Storage[local]: already exists at %s", dest)
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf_bytes)
        logger.debug("Storage[local]: stored %d bytes at %s", len(pdf_bytes), dest)
        return True

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()

    def download(self, path: str) -> bytes:
        dest = self.root / path
        if not dest.exists():
            raise FileNotFoundError(f"Not found in local storage: {path}")
        return dest.read_bytes()

    def file_size(self, path: str) -> int:
        dest = self.root / path
        return dest.stat().st_size if dest.exists() else 0

    def __repr__(self) -> str:
        return f"LocalStorageBackend(root={self.root})"


# ── Supabase Storage adapter ──────────────────────────────────────────────────

class SupabaseStorageBackend:
    """Supabase Storage adapter for production."""

    def __init__(self, supabase_client, bucket: str = None):
        self._bucket_name = bucket or os.getenv("SUPABASE_STORAGE_BUCKET", _DEFAULT_BUCKET)
        self._bucket = supabase_client.storage.from_(self._bucket_name)

    def _exists_in_listing(self, path: str) -> bool:
        folder, name = path.rsplit("/", 1) if "/" in path else ("", path)
        try:
            files = self._bucket.list(folder) or []
            return any(f.get("name") == name for f in files)
        except Exception:
            return False

    def upload(self, pdf_bytes: bytes, path: str) -> bool:
        if self._exists_in_listing(path):
            logger.debug("Storage[supabase]: already exists at %s/%s", self._bucket_name, path)
            return False
        try:
            self._bucket.upload(
                path=path,
                file=pdf_bytes,
                file_options={"content-type": "application/pdf"},
            )
            logger.debug(
                "Storage[supabase]: uploaded %d bytes to %s/%s",
                len(pdf_bytes), self._bucket_name, path,
            )
            return True
        except Exception as exc:
            # Concurrent upload of the same sha256: path is the same so content
            # is identical — treat as idempotent rather than raising.
            exc_lower = str(exc).lower()
            if "already exists" in exc_lower or "409" in exc_lower or "duplicate" in exc_lower:
                logger.debug(
                    "Storage[supabase]: concurrent upload race for %s — already stored", path
                )
                return False
            raise

    def exists(self, path: str) -> bool:
        return self._exists_in_listing(path)

    def download(self, path: str) -> bytes:
        return self._bucket.download(path)

    def file_size(self, path: str) -> int:
        folder, name = path.rsplit("/", 1) if "/" in path else ("", path)
        try:
            files = self._bucket.list(folder) or []
            for f in files:
                if f.get("name") == name:
                    return f.get("metadata", {}).get("size", 0)
        except Exception:
            pass
        return 0

    def __repr__(self) -> str:
        return f"SupabaseStorageBackend(bucket={self._bucket_name})"


# ── Factory ───────────────────────────────────────────────────────────────────

def get_storage_backend(supabase_client=None):
    """
    Return the configured storage backend.

    Reads STORAGE_BACKEND env var: 'supabase' or 'local' (default).
    'supabase' requires a live Supabase client.
    """
    backend_type = os.getenv("STORAGE_BACKEND", "local").lower()
    if backend_type == "supabase":
        if supabase_client is None:
            raise ValueError(
                "STORAGE_BACKEND=supabase requires a Supabase client. "
                "Pass supabase_client= to get_storage_backend()."
            )
        backend = SupabaseStorageBackend(supabase_client)
        logger.info("Storage backend: %r", backend)
        return backend
    backend = LocalStorageBackend()
    logger.info("Storage backend: %r", backend)
    return backend


# ── Core operation ────────────────────────────────────────────────────────────

def store_pdf(
    backend,
    pdf_bytes: bytes,
    sha256: str,
    filing_date: Optional[str],
) -> Tuple[str, bool]:
    """
    Compute the deterministic path and upload pdf_bytes to the backend.

    Returns (storage_path, is_new):
      is_new=True  → first time this document was stored
      is_new=False → already existed (idempotent upload, content unchanged)

    Raises on unexpected storage errors (e.g. network failure, auth error).
    """
    path = make_storage_path(sha256, filing_date)
    is_new = backend.upload(pdf_bytes, path)
    return path, is_new


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_raw_text(pdf_bytes: bytes) -> str:
    """
    Extract all text from a PDF as a single string.

    Uses pdfplumber to concatenate per-page text with double newlines.
    Returns an empty string (not None) on any failure, so callers never
    receive None for raw_extracted_text.
    """
    try:
        import pdfplumber  # only required for this helper
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        return "\n\n".join(pages).strip()
    except ImportError:
        logger.warning("pdfplumber not installed — raw_extracted_text will be empty")
        return ""
    except Exception as exc:
        logger.warning("extract_raw_text failed: %s", exc)
        return ""


# ── Integrity check (used by verify_document_integrity.py) ───────────────────

def verify_sha256(pdf_bytes: bytes, expected_sha256: str) -> bool:
    """Return True if the bytes match the expected SHA-256 hex digest."""
    return hashlib.sha256(pdf_bytes).hexdigest() == expected_sha256
