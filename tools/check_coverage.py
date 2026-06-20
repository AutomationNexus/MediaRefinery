"""Coverage gate helper for CI release validation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    """Enforce global and per-file coverage thresholds from a coverage.py JSON report."""
    parser = argparse.ArgumentParser(
        description="Require global and per-file coverage percentages.",
    )
    parser.add_argument(
        "--json",
        default="tmp/coverage.json",
        help="Path to coverage.py JSON output.",
    )
    parser.add_argument(
        "--minimum",
        type=float,
        default=90.0,
        help="Minimum percentage required for total and per-file coverage.",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.json)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    minimum = float(args.minimum)

    failures: list[str] = []
    totals = _summary(data["totals"])
    total_percent = float(totals["percent_covered"])
    if total_percent < minimum:
        failures.append(f"total coverage {total_percent:.2f}% is below {minimum:.2f}%")

    files: dict[str, Any] = data.get("files", {})
    for filename in sorted(files):
        summary = _summary(files[filename]["summary"])
        statements = int(summary.get("num_statements", 0))
        if statements == 0:
            continue
        percent = float(summary["percent_covered"])
        if percent < minimum:
            failures.append(f"{filename}: {percent:.2f}% is below {minimum:.2f}%")

    if failures:
        print("Coverage gate failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"Coverage gate passed: total and every file are >= {minimum:.2f}%")
    return 0


def _summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("coverage summary must be an object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
