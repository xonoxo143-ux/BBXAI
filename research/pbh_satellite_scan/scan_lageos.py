#!/usr/bin/env python3
"""Dedicated anomaly scan for the extensionless O_LG1/O_LG2 Zenodo files."""
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from scan_zenodo import analyze_table, correlated_candidates

ZENODO_URL = "https://zenodo.org/api/records/18441938/files/ancillary.zip/content"
FLOAT_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?$")


def as_float(token: str) -> float | None:
    t = token.strip().replace("D", "E").replace("d", "e")
    if not FLOAT_RE.match(t):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_numeric_text(text: str) -> tuple[pd.DataFrame, list[str]]:
    lines = text.splitlines()
    rows: list[list[float]] = []
    row_line_numbers: list[int] = []
    widths: dict[int, int] = {}

    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!", "%", ";")):
            continue
        toks = stripped.replace(",", " ").split()
        vals = [as_float(t) for t in toks]
        if len(vals) < 2 or any(v is None for v in vals):
            continue
        nums = [float(v) for v in vals if v is not None]
        widths[len(nums)] = widths.get(len(nums), 0) + 1
        rows.append(nums)
        row_line_numbers.append(line_no)

    if not rows:
        raise RuntimeError("No multi-column numeric rows found")

    width = max(widths, key=lambda w: widths[w])
    kept = [(r, n) for r, n in zip(rows, row_line_numbers) if len(r) == width]
    arr = np.asarray([r for r, _ in kept], dtype=float)
    line_numbers = [str(n) for _, n in kept]

    columns = [f"col_{i}" for i in range(width)]
    if width and len(arr) >= 5:
        first = arr[:, 0]
        if np.all(np.isfinite(first)) and np.all(np.diff(first) >= 0) and np.ptp(first) > 0:
            columns[0] = "epoch"
    return pd.DataFrame(arr, columns=columns), line_numbers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="output")
    ap.add_argument("--top-per-column", type=int, default=40)
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    r = requests.get(ZENODO_URL, timeout=120)
    r.raise_for_status()

    all_candidates = []
    file_info = []
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        for short in ("O_LG1", "O_LG2"):
            names = [n for n in zf.namelist() if Path(n).name == short]
            if not names:
                raise RuntimeError(f"{short} not found in ancillary.zip")
            name = names[0]
            raw = zf.read(name)
            text = raw.decode("utf-8", errors="replace")
            (out / f"{short}.preview.txt").write_text("\n".join(text.splitlines()[:80]) + "\n", encoding="utf-8")

            df, _ = parse_numeric_text(text)
            df.to_csv(out / f"{short}.parsed.csv", index=False)
            file_info.append({"file": short, "rows": int(len(df)), "columns": list(df.columns)})
            all_candidates.extend(analyze_table(short, "", df, args.top_per_column))

    all_candidates.sort(key=lambda c: c.score, reverse=True)
    coincidences = correlated_candidates(all_candidates, top_n=50)

    pd.DataFrame([asdict(c) for c in all_candidates]).to_csv(out / "lageos_candidates.csv", index=False)
    (out / "lageos_candidates.json").write_text(json.dumps([asdict(c) for c in all_candidates], indent=2), encoding="utf-8")
    (out / "lageos_coincidences.json").write_text(json.dumps(coincidences, indent=2), encoding="utf-8")
    (out / "lageos_files.json").write_text(json.dumps(file_info, indent=2), encoding="utf-8")

    lines = [
        "# LAGEOS / LAGEOS II anomaly ranking",
        "",
        "These candidates come from the public O_LG1/O_LG2 supporting observables. They are **not PBH detections**.",
        "",
        "The source orbit determination uses independent seven-day arcs with separately fitted initial states, so this dataset cannot by itself prove an instantaneous velocity impulse. It can identify unusually structured residual epochs worth checking against raw SLR data.",
        "",
        "## Parsed files",
        "",
    ]
    for info in file_info:
        lines.append(f"- {info['file']}: {info['rows']} rows, {len(info['columns'])} numeric columns")
    lines += [
        "",
        "## Highest-ranked events",
        "",
        "| score | epoch | file | column | value-z | diff-z | change-point |",
        "|---:|---|---|---|---:|---:|---:|",
    ]
    for c in all_candidates[:50]:
        lines.append(f"| {c.score:.2f} | {c.epoch} | {c.source} | {c.column} | {c.value_z:.2f} | {c.diff_z:.2f} | {c.cp_score:.2f} |")
    lines += ["", "## Exact-epoch coincidences", ""]
    if coincidences:
        lines += ["| epoch | independent files | channels | max score |", "|---|---:|---:|---:|"]
        for row in coincidences:
            lines.append(f"| {row['epoch']} | {row['n_sources']} | {row['n_channels']} | {row['max_score']:.2f} |")
    else:
        lines.append("No exact-epoch multi-channel coincidences above score 4.")

    summary = "\n".join(lines) + "\n"
    (out / "LAGEOS_SUMMARY.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
