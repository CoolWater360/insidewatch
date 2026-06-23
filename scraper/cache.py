"""
Atomic file-based URL cache for the listener.

Stores an ordered list of processed PDF URLs so the listener can skip
filings it has already handled.  Ordering is oldest-first; on save the
list is truncated to the most-recent MAX_ENTRIES entries.

Atomic write: data goes to a .tmp file in the same directory, then
os.replace() renames it — a single syscall that is crash-safe on POSIX.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent / "recent_scrapes.json"
MAX_ENTRIES = 20


def load() -> list[str]:
    """Return the ordered list of already-processed PDF URLs (oldest first)."""
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        entries = raw.get("urls", [])
        if not isinstance(entries, list):
            return []
        return [str(u) for u in entries]
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Cache file corrupt, starting fresh: %s", exc)
        return []


def save(urls: list[str]) -> None:
    """Atomically write the cache, keeping only the last MAX_ENTRIES URLs."""
    trimmed = urls[-MAX_ENTRIES:]
    payload = json.dumps({"urls": trimmed}, indent=2, ensure_ascii=False)
    fd, tmp_path = tempfile.mkstemp(dir=_CACHE_PATH.parent, suffix=".tmp")
    try:
        os.write(fd, payload.encode("utf-8"))
        os.close(fd)
        os.replace(tmp_path, _CACHE_PATH)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
