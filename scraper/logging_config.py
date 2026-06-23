"""
Structured logging configuration for InsideWatch scraper.

Usage:
    from .logging_config import configure_logging
    configure_logging(verbose=args.verbose)

Set LOG_FORMAT=json to emit newline-delimited JSON (NDJSON) to stdout.
Each line is a self-contained JSON object suitable for log aggregators
(Datadog, Loki, CloudWatch).

Without LOG_FORMAT=json the formatter falls back to the human-readable
format used in earlier phases.
"""

import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts":     datetime.now(timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(verbose: bool = False) -> None:
    """
    Configure root logger for the scraper process.

    verbose=True → DEBUG level; False → INFO level.
    LOG_FORMAT=json → JSON output; anything else → plain text.
    """
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler()

    if os.getenv("LOG_FORMAT", "").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s  %(message)s",
            datefmt="%H:%M:%S",
        ))

    logging.basicConfig(level=level, handlers=[handler], force=True)
