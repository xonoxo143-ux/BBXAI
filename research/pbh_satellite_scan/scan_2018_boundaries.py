#!/usr/bin/env python3
"""Rank every 2018 weekly ILRS orbit-boundary discontinuity for LAGEOS I/II.

This is a direct consistency test using analysis centers whose v70 SP3 products
normally include the exact Sunday 00:00 epoch in both adjacent weekly files.
It asks whether the large Feb-04-2018 LAGEOS-I cross-arc mismatch is routine or
an outlier relative to the rest of 2018.
"""
from __future__ import annotations

import csv
import gzip
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

BASE = "https://edc.dgfi.tum.de/pub/slr/products/orbits/{sat}/{week}/{ac}.orb.{sat}.{week}.v70.sp3.gz"
# These four centers had exact shared boundary epochs in the focused Feb scan.
ACS = ("asi", "dgfi", "esa", "gfz")
SATS = ("lageos1", "lageos2")
UA = {"User-Agent": "BBXAI-PBH-satellite-scan/0.3 (+public-research)"}


def week_code(d: date) -> str:
    return d.strftime("%y%m%d")


def saturday_range(start: date, end: date) -> list[str]:
    # Inputs are Saturdays, inclusive.
    out = []
    d = start
    while d <= end:
        out.append(week_code(d))
        d += timedelta(days=7)
    return out


def parse_epoch(line: str) -> datetime:
    p = line[1:].split()
    y, m, d, hh, mm = map(int, p[:5])
    sec = float(p[5])
    whole = int(sec)
    micro = int(round((sec - whole) * 1_000_000))
    return datetime(y, m, d, hh, mm, whole, micro, tzinfo=timezone.utc)


def boundary_points(raw_gz: bytes) -> tuple[datetime | None, np.ndarray | None, datetime | None, np.ndarray | None]:
    text = gzip.decompress(raw_gz).decode("ascii", errors="replace")
    first_t = last_t = None
    first_p = last_p = None
    epoch = None
    for line in text.splitlines():
        if line.startswith("*"):
            try:
                epoch = parse_epoch(line)
            except Exception:
                epoch = None
        elif line.startswith("P") and epoch is not None:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    p = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=float) * 1000.0
                except ValueError:
                    epoch = None
                    continue
                if first_t is None:
                    first_t, first_p = epoch, p
                last_t, last_p = epoch, p
            epoch = None
    return first_t, first_p, last_t, last_p


def fetch_one(sat: str, week: str, ac: str) -> tuple[tuple[str, str, str], dict]:
    url = BASE.format(sat=sat, week=week, ac=ac)
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200:
            return (sat, week, ac), {"status": r.status_code, "url": url}
        ft, fp, lt, lp = boundary_points(r.content)
        return (sat, week, ac), {
            "status": 200, "url": url, "bytes": len(r.content),
            "first_t": ft, "first_p": fp, "last_t": lt, "last_p": lp,
        }
    except Exception as exc:
        return (sat, week, ac), {"error": repr(exc), "url": url}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader(); w.writerows(rows)


def main() -> int:
    out = Path("output/boundary_scan_2018")
    out.mkdir(parents=True, exist_ok=True)

    # Include bounding weeks so all 2018 Sunday boundaries can be tested.
    weeks = saturday_range(date(2017, 12, 30), date(2019, 1, 5))
    products = {}
    requests_to_make = [(s, w, a) for s in SATS for w in weeks for a in ACS]
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(fetch_one, *args) for args in requests_to_make]
        for fut in as_completed(futs):
            key, rec = fut.result()
            products[key] = rec

    raw_rows = []
    consensus = []
    for sat in SATS:
        for w0, w1 in zip(weeks[:-1], weeks[1:]):
            center_rows = []
            for ac in ACS:
                a = products.get((sat, w0, ac), {})
                b = products.get((sat, w1, ac), {})
                if a.get("status") != 200 or b.get("status") != 200:
                    continue
                if a.get("last_t") is None or b.get("first_t") is None:
                    continue
                # Only use an exact common epoch. No extrapolation in this scan.
                if a["last_t"] != b["first_t"]:
                    continue
                dp = b["first_p"] - a["last_p"]
                row = {
                    "sat": sat, "prev_week": w0, "next_week": w1, "ac": ac,
                    "epoch": a["last_t"].isoformat(),
                    "jump_m": float(np.linalg.norm(dp)),
                    "dx_m": float(dp[0]), "dy_m": float(dp[1]), "dz_m": float(dp[2]),
                }
                raw_rows.append(row); center_rows.append(row)
            if not center_rows:
                continue
            mags = np.array([r["jump_m"] for r in center_rows])
            vecs = np.array([[r["dx_m"], r["dy_m"], r["dz_m"]] for r in center_rows])
            med_vec = np.median(vecs, axis=0)
            consensus.append({
                "sat": sat, "prev_week": w0, "next_week": w1,
                "epoch": center_rows[0]["epoch"], "n_centers": len(center_rows),
                "median_jump_m": float(np.median(mags)),
                "max_jump_m": float(np.max(mags)),
                "median_dx_m": float(med_vec[0]), "median_dy_m": float(med_vec[1]), "median_dz_m": float(med_vec[2]),
                "median_vector_norm_m": float(np.linalg.norm(med_vec)),
                "center_scatter_m": float(np.median(np.linalg.norm(vecs - med_vec, axis=1))),
            })

    # Empirical percentile within each satellite, using median cross-center jump.
    for sat in SATS:
        rs = [r for r in consensus if r["sat"] == sat and r["n_centers"] >= 2]
        vals = np.array([r["median_jump_m"] for r in rs])
        if not len(vals):
            continue
        for r in rs:
            r["empirical_percentile"] = float(100.0 * np.mean(vals <= r["median_jump_m"]))
            r["rank_desc"] = int(1 + np.sum(vals > r["median_jump_m"]))
            r["n_boundaries_sat"] = int(len(vals))

    write_csv(out / "boundary_by_center.csv", raw_rows)
    write_csv(out / "boundary_consensus.csv", sorted(consensus, key=lambda r: (r["sat"], -r["median_jump_m"])))

    # Compact request log without numpy arrays.
    log = []
    for (sat, week, ac), r in sorted(products.items()):
        log.append({
            "sat": sat, "week": week, "ac": ac,
            "status": r.get("status"), "error": r.get("error"), "bytes": r.get("bytes"),
            "first": r.get("first_t").isoformat() if r.get("first_t") else None,
            "last": r.get("last_t").isoformat() if r.get("last_t") else None,
            "url": r.get("url"),
        })
    (out / "download_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")

    lines = [
        "# 2018 LAGEOS weekly-boundary consistency scan",
        "",
        "Direct position differences at exact shared SP3 epochs from ASI, DGFI, ESA, and GFZ. No polynomial extrapolation is used.",
        "",
    ]
    for sat in SATS:
        rows = [r for r in consensus if r["sat"] == sat and r.get("rank_desc")]
        rows.sort(key=lambda r: r["median_jump_m"], reverse=True)
        lines += [f"## {sat}", "", "| rank | boundary epoch | centers | median jump (m) | max jump (m) | percentile |", "|---:|---|---:|---:|---:|---:|"]
        for r in rows[:20]:
            lines.append(f"| {r['rank_desc']} | {r['epoch']} | {r['n_centers']} | {r['median_jump_m']:.4f} | {r['max_jump_m']:.4f} | {r['empirical_percentile']:.2f}% |")
        lines.append("")

    target = [r for r in consensus if r["sat"] == "lageos1" and r["prev_week"] == "180203" and r["next_week"] == "180210"]
    if target:
        r = target[0]
        lines += [
            "## February 2018 target",
            "",
            f"The 180203→180210 LAGEOS-I boundary ranks **{r.get('rank_desc', '?')} of {r.get('n_boundaries_sat', '?')}** tested 2018 boundaries by median cross-center discontinuity, at the **{r.get('empirical_percentile', float('nan')):.2f}th percentile**.",
            "",
        ]

    summary = "\n".join(lines) + "\n"
    (out / "BOUNDARY_2018_SUMMARY.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
