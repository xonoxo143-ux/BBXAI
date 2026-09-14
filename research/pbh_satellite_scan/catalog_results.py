#!/usr/bin/env python3
"""Normalize PBH-search outputs into one machine-readable event catalog.

The goal is to let GitHub Actions do the repetitive sorting. Each analysis writes
its native detailed files; this script converts the important rows into a small,
consistent catalog that can be ranked, filtered, deduplicated and compared across
satellites/datasets without manually opening every artifact.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def f(v: Any, default=float("nan")) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def event_id(parts: list[str]) -> str:
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:14]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def add_lageos_residuals(output: Path, rows: list[dict]) -> None:
    path = output / "lageos_candidates.csv"
    for r in read_csv(path):
        sat = "LAGEOS I" if r.get("source") == "O_LG1" else "LAGEOS II" if r.get("source") == "O_LG2" else r.get("source", "unknown")
        epoch = r.get("epoch", "")
        score = f(r.get("score"))
        rows.append({
            "event_id": event_id(["residual", sat, epoch, r.get("column", "")]),
            "dataset": "Zenodo 18441938 O_LG1/O_LG2",
            "satellite": sat,
            "epoch": epoch,
            "event_type": "published_residual_anomaly",
            "primary_metric": "robust_combined_score",
            "primary_value": score,
            "secondary_value": max(f(r.get("diff_z"), 0.0), f(r.get("cp_score"), 0.0)),
            "units": "dimensionless robust score",
            "n_independent_sources": 1,
            "status": "screening",
            "source_file": str(path.relative_to(output.parent)),
            "details": json.dumps({
                "value": f(r.get("value")),
                "value_z": f(r.get("value_z")),
                "diff_z": f(r.get("diff_z")),
                "change_point": f(r.get("cp_score")),
                "column": r.get("column"),
            }, separators=(",", ":")),
        })


def add_boundaries(output: Path, rows: list[dict]) -> None:
    path = output / "boundary_scan_2018" / "boundary_consensus.csv"
    for r in read_csv(path):
        sat = "LAGEOS I" if r.get("sat") == "lageos1" else "LAGEOS II" if r.get("sat") == "lageos2" else r.get("sat", "unknown")
        epoch = r.get("epoch", "")
        jump = f(r.get("median_jump_m"))
        rows.append({
            "event_id": event_id(["boundary", sat, epoch]),
            "dataset": "ILRS v70 SP3 independent weekly orbit solutions",
            "satellite": sat,
            "epoch": epoch,
            "event_type": "weekly_orbit_boundary_discontinuity",
            "primary_metric": "median_cross_center_position_jump",
            "primary_value": jump,
            "secondary_value": f(r.get("empirical_percentile")),
            "units": "m",
            "n_independent_sources": int(f(r.get("n_centers"), 0)),
            "status": "cross_checked" if int(f(r.get("n_centers"), 0)) >= 3 else "screening",
            "source_file": str(path.relative_to(output.parent)),
            "details": json.dumps({
                "rank_2018": int(f(r.get("rank_desc"), 0)),
                "n_boundaries_2018": int(f(r.get("n_boundaries_sat"), 0)),
                "percentile_2018": f(r.get("empirical_percentile")),
                "max_jump_m": f(r.get("max_jump_m")),
                "median_vector_norm_m": f(r.get("median_vector_norm_m")),
                "center_scatter_m": f(r.get("center_scatter_m")),
            }, separators=(",", ":")),
        })


def add_target_geometry(output: Path, rows: list[dict]) -> None:
    path = output / "feb2018_candidate" / "boundary_rtn.csv"
    native = read_csv(path)
    if not native:
        return
    epoch = native[0].get("epoch", "")
    jumps = [f(r.get("jump_m"), 0.0) for r in native]
    along = [f(r.get("along_track_fraction_abs"), 0.0) for r in native]
    rows.append({
        "event_id": event_id(["geometry", "LAGEOS I", epoch]),
        "dataset": "ILRS v70 SP3 Feb-2018 targeted geometry",
        "satellite": "LAGEOS I",
        "epoch": epoch,
        "event_type": "targeted_candidate_geometry",
        "primary_metric": "median_center_jump",
        "primary_value": float(sorted(jumps)[len(jumps)//2]),
        "secondary_value": 100.0 * sum(along) / len(along),
        "units": "m; secondary=% abs along-track",
        "n_independent_sources": len(native),
        "status": "physical_vetting",
        "source_file": str(path.relative_to(output.parent)),
        "details": json.dumps({"centers": [r.get("center") for r in native]}, separators=(",", ":")),
    })


def rank(rows: list[dict]) -> None:
    # Cross-dataset values are not numerically comparable, so rank only inside
    # each dataset/event-type group. The catalog remains sortable globally by
    # satellite/time/status without pretending metres and z-scores are equivalent.
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["dataset"], r["event_type"]), []).append(r)
    for group in groups.values():
        group.sort(key=lambda r: f(r["primary_value"], -1e300), reverse=True)
        for i, r in enumerate(group, 1):
            r["rank_within_dataset"] = i
            r["n_events_in_dataset"] = len(group)


def write(output: Path, rows: list[dict]) -> None:
    cat = output / "catalog"
    cat.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (r["satellite"], r["epoch"], r["event_type"]))
    fields = [
        "event_id", "dataset", "satellite", "epoch", "event_type",
        "primary_metric", "primary_value", "secondary_value", "units",
        "n_independent_sources", "status", "rank_within_dataset",
        "n_events_in_dataset", "source_file", "details",
    ]
    with (cat / "events.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
    (cat / "events.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    top = sorted(rows, key=lambda r: (
        1 if r["status"] == "physical_vetting" else 0,
        int(r.get("n_independent_sources", 0)),
        -int(r.get("rank_within_dataset", 999999)),
    ), reverse=True)[:40]
    lines = [
        "# PBH satellite-search event catalog",
        "",
        f"Normalized events: **{len(rows)}**",
        "",
        "The catalog deliberately does **not** combine unlike scores into one fake universal significance number. Each event is ranked within its own dataset, while cross-dataset corroboration is recorded separately.",
        "",
        "| satellite | epoch | type | value | rank | independent sources | status |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for r in top:
        lines.append(f"| {r['satellite']} | {r['epoch']} | {r['event_type']} | {f(r['primary_value']):.4g} | {r['rank_within_dataset']}/{r['n_events_in_dataset']} | {r['n_independent_sources']} | {r['status']} |")
    (cat / "CATALOG_SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="output")
    args = ap.parse_args()
    output = Path(args.output)
    rows: list[dict] = []
    add_lageos_residuals(output, rows)
    add_boundaries(output, rows)
    add_target_geometry(output, rows)
    rank(rows)
    write(output, rows)
    print((output / "catalog" / "CATALOG_SUMMARY.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
