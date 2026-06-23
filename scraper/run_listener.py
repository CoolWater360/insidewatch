#!/usr/bin/env python3
"""
Listener entry point.

Usage:
    python3 -m scraper.run_listener           # normal run
    python3 -m scraper.run_listener --verbose  # with DEBUG output

Designed to finish in < 2 s when no new filings are present, and < 30 s
in the worst case (1 new PDF to download, parse, and store).
"""

import argparse
import sys

from .listener import run_listener
from .logging_config import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Listener: check for new internal dealing filings."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)

    stats = run_listener()
    print(
        f"Listener done — new: {stats['new']}, "
        f"skipped: {stats['skipped']}, "
        f"errors: {stats['errors']}"
    )

    if stats["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
