#!/usr/bin/env python3
"""Starter BBXAI audio analyzer.

This is intentionally small. It is here to establish the engine shape,
not to pretend the main product is solved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def analyze_file(path: Path) -> dict[str, Any]:
    """Return a basic placeholder report for one audio file.

    Real librosa analysis will be added after the project direction is stable.
    """
    return {
        "file": str(path),
        "exists": path.exists(),
        "role": None,
        "notes": "Placeholder. Add librosa-based duration, loudness, onset, and pitch analysis later.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze beatbox sample files for BBXAI.")
    parser.add_argument("files", nargs="*", help="Audio files to analyze")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args()

    reports = [analyze_file(Path(file)) for file in args.files]

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        for report in reports:
            status = "found" if report["exists"] else "missing"
            print(f"{report['file']}: {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
