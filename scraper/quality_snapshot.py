"""
Quality snapshot — before/after comparison tool.

Takes a point-in-time snapshot of quality metrics and can compare two
snapshots to quantify the effect of a reprocessing run.

Usage
-----
    # Take a snapshot and print to stdout:
    python3 -m scraper.quality_snapshot

    # Save a snapshot to a file:
    python3 -m scraper.quality_snapshot --save ops/snapshots/before.json

    # Compare two snapshots:
    python3 -m scraper.quality_snapshot \\
        --compare ops/snapshots/before.json ops/snapshots/after.json

    # Full workflow around a reprocessing run:
    python3 -m scraper.quality_snapshot --save ops/snapshots/before.json
    python3 -m scraper.reprocess_historical --apply --limit 200
    python3 -m scraper.quality_snapshot --save ops/snapshots/after.json
    python3 -m scraper.quality_snapshot \\
        --compare ops/snapshots/before.json ops/snapshots/after.json
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_COMPARABLE_SCALARS = [
    "total_transactions",
    "needs_review_count",
    "review_rate_pct",
    "unknown_direction_count",
    "unknown_direction_pct",
    "unknown_type_count",
    "unknown_type_pct",
    "correction_count",
    "correction_rate_pct",
    "stale_in_progress_count",
    "failed_filings_7d",
]

_COMPARABLE_CONFIDENCE = [
    ("confidence", "extraction",     "avg"),
    ("confidence", "extraction",     "p95"),
    ("confidence", "classification", "avg"),
    ("confidence", "classification", "p95"),
]


def take_snapshot(client=None, *, window_days: int = 30) -> dict:
    """
    Compute and return a quality snapshot dict.

    Includes all metrics from compute_quality_metrics() plus a timestamp
    so snapshots can be compared over time.
    """
    import os

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY", "")

    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) must be set."
        )

    from .quality import compute_quality_metrics
    metrics = compute_quality_metrics(url, key, window_days=window_days)

    return {
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        **metrics,
    }


def save_snapshot(snapshot: dict, path: str) -> None:
    """Write a snapshot dict to a JSON file, creating parent directories."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=str)
    logger.info("Snapshot saved to %s", path)


def load_snapshot(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _delta(before, after) -> Optional[dict]:
    """Return a dict describing the change between two numeric values."""
    if before is None or after is None:
        return None
    try:
        b, a = float(before), float(after)
        change = a - b
        pct = round(100 * change / b, 2) if b != 0 else None
        return {
            "before": round(b, 4),
            "after":  round(a, 4),
            "change": round(change, 4),
            "change_pct": pct,
            "direction": "up" if change > 0 else ("down" if change < 0 else "flat"),
        }
    except (TypeError, ValueError):
        return {"before": before, "after": after, "change": None}


def compare_snapshots(before: dict, after: dict) -> dict:
    """
    Return a diff dict showing what changed between two snapshots.

    Each key under 'metrics' contains before/after/change/change_pct/direction.
    """
    diff: dict = {
        "before_snapshot_at": before.get("snapshot_at"),
        "after_snapshot_at":  after.get("snapshot_at"),
        "metrics": {},
    }

    for key in _COMPARABLE_SCALARS:
        d = _delta(before.get(key), after.get(key))
        if d is not None:
            diff["metrics"][key] = d

    for outer, inner, stat in _COMPARABLE_CONFIDENCE:
        label = f"{outer}.{inner}.{stat}"
        b_val = (before.get(outer) or {}).get(inner, {}).get(stat)
        a_val = (after.get(outer)  or {}).get(inner, {}).get(stat)
        d = _delta(b_val, a_val)
        if d is not None:
            diff["metrics"][label] = d

    # High-level verdict
    rr_before = before.get("review_rate_pct")
    rr_after  = after.get("review_rate_pct")
    unk_before = before.get("unknown_direction_pct")
    unk_after  = after.get("unknown_direction_pct")

    improved = []
    degraded = []

    if rr_before is not None and rr_after is not None:
        if rr_after < rr_before:
            improved.append(f"review_rate_pct: {rr_before}% → {rr_after}%")
        elif rr_after > rr_before:
            degraded.append(f"review_rate_pct: {rr_before}% → {rr_after}%")

    if unk_before is not None and unk_after is not None:
        if unk_after < unk_before:
            improved.append(f"unknown_direction_pct: {unk_before}% → {unk_after}%")
        elif unk_after > unk_before:
            degraded.append(f"unknown_direction_pct: {unk_before}% → {unk_after}%")

    diff["verdict"] = {
        "improved": improved,
        "degraded": degraded,
        "summary": (
            "Improved" if improved and not degraded
            else "Degraded" if degraded and not improved
            else "Mixed" if improved and degraded
            else "No change"
        ),
    }

    return diff


def _print_comparison(diff: dict) -> None:
    print(f"\nBefore: {diff.get('before_snapshot_at', 'unknown')}")
    print(f"After:  {diff.get('after_snapshot_at', 'unknown')}")
    print()
    print(f"{'Metric':<45} {'Before':>10} {'After':>10} {'Change':>10} {'Dir':>5}")
    print("-" * 85)

    for key, d in diff.get("metrics", {}).items():
        b = d.get("before", "—")
        a = d.get("after",  "—")
        c = d.get("change", "—")
        direction = d.get("direction", "")
        arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(direction, "")
        print(f"{key:<45} {str(b):>10} {str(a):>10} {str(c):>10} {arrow:>5}")

    verdict = diff.get("verdict", {})
    print()
    print(f"Verdict: {verdict.get('summary', '—')}")
    if verdict.get("improved"):
        for msg in verdict["improved"]:
            print(f"  ✓ {msg}")
    if verdict.get("degraded"):
        for msg in verdict["degraded"]:
            print(f"  ✗ {msg}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    try:
        import dotenv
        dotenv.load_dotenv(".env.local")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Quality snapshot: capture and compare quality metrics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        default=None,
        help="Save the current snapshot to this JSON file.",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        default=None,
        help="Compare two snapshot files and print a diff.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=30,
        metavar="DAYS",
        help="Lookback window in days for the snapshot (default: 30).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Pretty-print JSON output.",
    )
    args = parser.parse_args()

    indent = 2 if args.pretty else None

    if args.compare:
        before_path, after_path = args.compare
        before = load_snapshot(before_path)
        after  = load_snapshot(after_path)
        diff = compare_snapshots(before, after)
        _print_comparison(diff)
        if args.save:
            save_snapshot(diff, args.save)
        return

    snapshot = take_snapshot(window_days=args.window)

    if args.save:
        save_snapshot(snapshot, args.save)
    else:
        print(json.dumps(snapshot, indent=indent, default=str))


if __name__ == "__main__":
    main()
