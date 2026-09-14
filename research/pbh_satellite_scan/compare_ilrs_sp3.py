#!/usr/bin/env python3
"""Cross-check the Feb-2018 LAGEOS candidate with independent ILRS SP3 orbits.

For the weeks surrounding the large LAGEOS-I residual jump this script downloads
all public v70 orbit products from the ILRS analysis/combination centers, parses
SP3 positions, measures analysis-center agreement within each week, and measures
cross-week state discontinuities at shared boundary epochs.

A common physical perturbation can still be absorbed by every weekly orbit fit,
so agreement is not proof of a PBH. Conversely, a large discrepancy confined to
one analysis center is evidence against an astrophysical interpretation.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

BASE = "https://edc.dgfi.tum.de/pub/slr/products/orbits/{sat}/{week}/{ac}.orb.{sat}.{week}.v70.sp3.gz"
ACS = ("asi", "bkg", "dgfi", "esa", "gfz", "ilrsa", "ilrsb", "jcet", "nsgf")
WEEKS = ("180127", "180203", "180210", "180217", "180224")
SATS = ("lageos1", "lageos2")
UA = {"User-Agent": "BBXAI-PBH-satellite-scan/0.2 (+public-research)"}


def parse_epoch(line: str) -> datetime:
    p = line[1:].split()
    year, month, day, hour, minute = map(int, p[:5])
    sec = float(p[5])
    whole = int(sec)
    micro = int(round((sec - whole) * 1_000_000))
    if micro >= 1_000_000:
        whole += 1
        micro -= 1_000_000
    return datetime(year, month, day, hour, minute, whole, micro, tzinfo=timezone.utc)


def parse_sp3(raw_gz: bytes) -> dict[datetime, np.ndarray]:
    text = gzip.decompress(raw_gz).decode("ascii", errors="replace")
    out: dict[datetime, np.ndarray] = {}
    epoch = None
    for line in text.splitlines():
        if line.startswith("*"):
            try:
                epoch = parse_epoch(line)
            except Exception:
                epoch = None
        elif line.startswith("P") and epoch is not None:
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                # SP3 coordinates are kilometres. Convert to metres.
                xyz = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=float) * 1000.0
            except ValueError:
                continue
            if np.all(np.isfinite(xyz)) and np.max(np.abs(xyz)) < 1e9:
                out[epoch] = xyz
                # Every file in this archive is a single-satellite product.
                # If a future file has multiple P records per epoch, keep first.
                epoch = None
    return out


def download_products(out: Path) -> tuple[dict, list[dict]]:
    products = {}
    log = []
    cache = out / "sp3"
    cache.mkdir(parents=True, exist_ok=True)
    for sat in SATS:
        for week in WEEKS:
            for ac in ACS:
                url = BASE.format(sat=sat, week=week, ac=ac)
                key = (sat, week, ac)
                target = cache / f"{ac}.orb.{sat}.{week}.v70.sp3.gz"
                try:
                    r = requests.get(url, headers=UA, timeout=90)
                    if r.status_code != 200:
                        log.append({"sat": sat, "week": week, "ac": ac, "status": r.status_code, "url": url})
                        continue
                    target.write_bytes(r.content)
                    series = parse_sp3(r.content)
                    products[key] = series
                    epochs = sorted(series)
                    log.append({
                        "sat": sat, "week": week, "ac": ac, "status": 200,
                        "bytes": len(r.content), "epochs": len(series),
                        "first": epochs[0].isoformat() if epochs else None,
                        "last": epochs[-1].isoformat() if epochs else None,
                        "url": url,
                    })
                except Exception as exc:
                    log.append({"sat": sat, "week": week, "ac": ac, "error": repr(exc), "url": url})
    return products, log


def norm(x: np.ndarray) -> float:
    return float(np.linalg.norm(x))


def within_week_agreement(products: dict) -> list[dict]:
    rows = []
    for sat in SATS:
        for week in WEEKS:
            series_by_ac = {ac: products[(sat, week, ac)] for ac in ACS if (sat, week, ac) in products}
            if len(series_by_ac) < 2:
                continue
            common = set.intersection(*(set(s) for s in series_by_ac.values()))
            if not common:
                continue
            diffs = defaultdict(list)
            epoch_spreads = []
            for t in sorted(common):
                stack = np.stack([series_by_ac[ac][t] for ac in series_by_ac])
                med = np.median(stack, axis=0)
                ds = np.linalg.norm(stack - med, axis=1)
                epoch_spreads.append(float(np.max(ds)))
                for ac, d in zip(series_by_ac, ds):
                    diffs[ac].append(float(d))
            all_d = np.array([d for vals in diffs.values() for d in vals], dtype=float)
            rows.append({
                "sat": sat,
                "week": week,
                "n_centers": len(series_by_ac),
                "n_common_epochs": len(common),
                "median_center_offset_m": float(np.median(all_d)),
                "p95_center_offset_m": float(np.percentile(all_d, 95)),
                "max_center_offset_m": float(np.max(all_d)),
                "median_epoch_max_spread_m": float(np.median(epoch_spreads)),
                "p95_epoch_max_spread_m": float(np.percentile(epoch_spreads, 95)),
            })
    return rows


def boundary_rows(products: dict) -> list[dict]:
    rows = []
    for sat in SATS:
        for w0, w1 in zip(WEEKS[:-1], WEEKS[1:]):
            for ac in ACS:
                a = products.get((sat, w0, ac))
                b = products.get((sat, w1, ac))
                if not a or not b:
                    continue
                overlap = sorted(set(a).intersection(b))
                if overlap:
                    # Use the shared epoch nearest the boundary (normally exactly one).
                    t = overlap[-1]
                    dp = b[t] - a[t]
                    rows.append({
                        "sat": sat, "prev_week": w0, "next_week": w1, "ac": ac,
                        "method": "shared_epoch", "epoch": t.isoformat(),
                        "position_discontinuity_m": norm(dp),
                        "dx_m": float(dp[0]), "dy_m": float(dp[1]), "dz_m": float(dp[2]),
                    })
                    continue

                ta = max(a)
                tb = min(b)
                gap = (tb - ta).total_seconds()
                if gap <= 0 or gap > 3600:
                    continue

                # Local constant-acceleration extrapolation in ECEF using final 3 points.
                prev_times = sorted(a)[-3:]
                if len(prev_times) < 3:
                    continue
                t0 = prev_times[-1]
                xs = np.array([(t - t0).total_seconds() for t in prev_times], dtype=float)
                pred = np.zeros(3)
                for k in range(3):
                    ys = np.array([a[t][k] for t in prev_times])
                    coef = np.polyfit(xs, ys, 2)
                    pred[k] = np.polyval(coef, (tb - t0).total_seconds())
                dp = b[tb] - pred
                rows.append({
                    "sat": sat, "prev_week": w0, "next_week": w1, "ac": ac,
                    "method": "quadratic_extrapolation", "epoch": tb.isoformat(),
                    "position_discontinuity_m": norm(dp),
                    "dx_m": float(dp[0]), "dy_m": float(dp[1]), "dz_m": float(dp[2]),
                    "gap_s": gap,
                })
    return rows


def boundary_consensus(boundaries: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in boundaries:
        groups[(r["sat"], r["prev_week"], r["next_week"], r["method"])].append(r)
    out = []
    for (sat, w0, w1, method), rs in groups.items():
        vecs = np.array([[r["dx_m"], r["dy_m"], r["dz_m"]] for r in rs], dtype=float)
        mags = np.linalg.norm(vecs, axis=1)
        med_vec = np.median(vecs, axis=0)
        scatter = np.linalg.norm(vecs - med_vec, axis=1)
        out.append({
            "sat": sat, "prev_week": w0, "next_week": w1, "method": method,
            "n_centers": len(rs),
            "median_discontinuity_m": float(np.median(mags)),
            "max_discontinuity_m": float(np.max(mags)),
            "median_vector_dx_m": float(med_vec[0]),
            "median_vector_dy_m": float(med_vec[1]),
            "median_vector_dz_m": float(med_vec[2]),
            "median_center_scatter_m": float(np.median(scatter)),
            "max_center_scatter_m": float(np.max(scatter)),
        })
    out.sort(key=lambda r: (r["sat"], r["prev_week"]))
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    out = Path("output/sp3_2018_compare")
    out.mkdir(parents=True, exist_ok=True)
    products, log = download_products(out)
    agree = within_week_agreement(products)
    bounds = boundary_rows(products)
    consensus = boundary_consensus(bounds)

    (out / "download_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    write_csv(out / "within_week_agreement.csv", agree)
    write_csv(out / "boundary_by_center.csv", bounds)
    write_csv(out / "boundary_consensus.csv", consensus)

    lines = [
        "# Independent ILRS SP3 cross-check around the February 2018 candidate",
        "",
        "Nine public analysis/combination-center orbit products are compared for LAGEOS I and II.",
        "",
        "## Within-week center agreement",
        "",
        "| satellite | week | centers | common epochs | median offset (m) | p95 offset (m) | max offset (m) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in agree:
        lines.append(
            f"| {r['sat']} | {r['week']} | {r['n_centers']} | {r['n_common_epochs']} | "
            f"{r['median_center_offset_m']:.4f} | {r['p95_center_offset_m']:.4f} | {r['max_center_offset_m']:.4f} |"
        )
    lines += [
        "",
        "## Cross-week boundary continuity",
        "",
        "If adjacent SP3 arcs share an epoch, the table reports the median position difference between the two independently fitted weekly arcs at exactly that epoch. Otherwise a short local quadratic extrapolation is used and labeled explicitly.",
        "",
        "| satellite | boundary | method | centers | median discontinuity (m) | max (m) | median center scatter (m) |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for r in consensus:
        lines.append(
            f"| {r['sat']} | {r['prev_week']}→{r['next_week']} | {r['method']} | {r['n_centers']} | "
            f"{r['median_discontinuity_m']:.4f} | {r['max_discontinuity_m']:.4f} | {r['median_center_scatter_m']:.4f} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "A single-center blow-up would point strongly to processing/model error. Agreement among centers only establishes that the same tracking data support similar weekly orbits; it cannot by itself distinguish a real perturbation from a common force-model error or an effect absorbed into every weekly initial state.",
    ]
    summary = "\n".join(lines) + "\n"
    (out / "SP3_SUMMARY.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
