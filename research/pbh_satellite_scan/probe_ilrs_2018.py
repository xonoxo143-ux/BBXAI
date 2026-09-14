#!/usr/bin/env python3
"""Probe raw ILRS coverage and precise-orbit directories around the Feb-2018 candidate.

This stage does not fit an orbit. It answers two prerequisite questions:
1. Which SLR stations/passes contributed normal points around the anomalous arcs?
2. Which independent weekly SP3 precise-orbit products are publicly available for
   the same interval and can be used for cross-validation?
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE_NPT = "https://edc.dgfi.tum.de/pub/slr/data/npt_crd/{sat}/2018/{sat}_2018{month:02d}.npt"
BASE_ORB = "https://edc.dgfi.tum.de/pub/slr/products/orbits/{sat}/{week}/"
UA = {"User-Agent": "BBXAI-PBH-satellite-scan/0.1 (+public-research)"}


def get(url: str, timeout: int = 120) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


def parse_crd(text: str, sat: str) -> tuple[list[dict], list[dict]]:
    current_station = None
    current_pass = None
    passes: list[dict] = []
    points: list[dict] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        rec = parts[0].lower()
        try:
            if rec == "h2" and len(parts) >= 3:
                current_station = {
                    "station_name": parts[1],
                    "station_id": parts[2],
                }
            elif rec == "h4" and len(parts) >= 14:
                # h4 1 YYYY MM DD hh mm ss YYYY MM DD hh mm ss ...
                start = datetime(
                    int(parts[2]), int(parts[3]), int(parts[4]),
                    int(parts[5]), int(parts[6]), int(float(parts[7])),
                    tzinfo=timezone.utc,
                )
                end = datetime(
                    int(parts[8]), int(parts[9]), int(parts[10]),
                    int(parts[11]), int(parts[12]), int(float(parts[13])),
                    tzinfo=timezone.utc,
                )
                current_pass = {
                    "satellite": sat,
                    "station_name": (current_station or {}).get("station_name", "unknown"),
                    "station_id": (current_station or {}).get("station_id", "unknown"),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "date": start.date().isoformat(),
                    "normal_points": 0,
                }
                passes.append(current_pass)
            elif rec == "11" and current_pass is not None:
                current_pass["normal_points"] += 1
                points.append({
                    "satellite": sat,
                    "station_name": current_pass["station_name"],
                    "station_id": current_pass["station_id"],
                    "date": current_pass["date"],
                    "pass_start": current_pass["start"],
                })
            elif rec in {"h8", "h9"}:
                current_pass = None
        except (ValueError, IndexError):
            continue
    return passes, points


def mjd(dt: datetime) -> float:
    origin = datetime(1858, 11, 17, tzinfo=timezone.utc)
    return (dt - origin).total_seconds() / 86400.0


def arc_start_for_date(dt: datetime) -> datetime:
    # Published observable epochs are Fridays separated by 7 days.
    # Anchor on MJD 58151 = 2018-02-02.
    anchor = datetime(2018, 2, 2, tzinfo=timezone.utc)
    delta_days = (dt.date() - anchor.date()).days
    k = delta_days // 7
    return anchor + timedelta(days=7 * k)


def aggregate(passes: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str, str], dict] = {}
    for p in passes:
        dt = datetime.fromisoformat(p["start"])
        arc = arc_start_for_date(dt).date().isoformat()
        key = (p["satellite"], arc, p["station_id"])
        b = buckets.setdefault(key, {
            "satellite": p["satellite"],
            "arc_start": arc,
            "station_id": p["station_id"],
            "station_name": p["station_name"],
            "passes": 0,
            "normal_points": 0,
        })
        b["passes"] += 1
        b["normal_points"] += int(p["normal_points"])
    rows = list(buckets.values())
    rows.sort(key=lambda r: (r["satellite"], r["arc_start"], -r["normal_points"], r["station_id"]))
    return rows


def directory_listing(url: str) -> dict:
    try:
        r = get(url, timeout=60)
    except Exception as exc:
        return {"url": url, "error": repr(exc), "files": []}
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', r.text, flags=re.I)
    files = []
    for href in hrefs:
        if href in {"../", "./", "/"} or href.startswith("?"):
            continue
        full = urljoin(url, href)
        name = href.rstrip("/").split("/")[-1]
        if name:
            files.append({"name": name, "url": full})
    return {"url": url, "status": r.status_code, "files": files}


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    out = Path("output/ilrs_2018_probe")
    out.mkdir(parents=True, exist_ok=True)

    all_passes: list[dict] = []
    downloads = []
    for sat in ("lageos1", "lageos2"):
        for month in (1, 2, 3):
            url = BASE_NPT.format(sat=sat, month=month)
            try:
                r = get(url)
                text = r.text
                target = out / f"{sat}_2018{month:02d}.npt"
                target.write_text(text, encoding="utf-8")
                passes, _ = parse_crd(text, sat)
                all_passes.extend(passes)
                downloads.append({"url": url, "status": r.status_code, "bytes": len(r.content), "passes": len(passes)})
            except Exception as exc:
                downloads.append({"url": url, "error": repr(exc)})

    write_csv(out / "passes.csv", all_passes)
    agg = aggregate(all_passes)
    write_csv(out / "station_arc_coverage.csv", agg)

    orbit_dirs = []
    # Sundays bracketing the candidate and adjacent weeks.
    for sat in ("lageos1", "lageos2"):
        for week in ("180121", "180128", "180204", "180211", "180218"):
            orbit_dirs.append(directory_listing(BASE_ORB.format(sat=sat, week=week)))

    (out / "downloads.json").write_text(json.dumps(downloads, indent=2), encoding="utf-8")
    (out / "orbit_directories.json").write_text(json.dumps(orbit_dirs, indent=2), encoding="utf-8")

    # Human-readable coverage table for the candidate arcs and neighbors.
    focus_arcs = {"2018-01-19", "2018-01-26", "2018-02-02", "2018-02-09", "2018-02-16", "2018-02-23"}
    lines = [
        "# ILRS raw-data probe around the February 2018 LAGEOS-I candidate",
        "",
        "This is a coverage/availability audit, not an orbit fit.",
        "",
    ]
    for sat in ("lageos1", "lageos2"):
        lines += [f"## {sat}", ""]
        for arc in sorted(focus_arcs):
            rows = [r for r in agg if r["satellite"] == sat and r["arc_start"] == arc]
            total_p = sum(int(r["passes"]) for r in rows)
            total_n = sum(int(r["normal_points"]) for r in rows)
            lines.append(f"### Arc {arc}: {total_p} passes, {total_n} normal points, {len(rows)} stations")
            lines.append("")
            lines.append("| station | passes | normal points |")
            lines.append("|---|---:|---:|")
            for r in sorted(rows, key=lambda x: int(x["normal_points"]), reverse=True):
                lines.append(f"| {r['station_name']} ({r['station_id']}) | {r['passes']} | {r['normal_points']} |")
            lines.append("")

    lines += ["## Precise-orbit directory discovery", ""]
    for d in orbit_dirs:
        lines.append(f"- `{d['url']}`: {len(d.get('files', []))} entries" + (f"; ERROR {d['error']}" if d.get("error") else ""))
        for f in d.get("files", [])[:30]:
            lines.append(f"  - `{f['name']}`")

    (out / "PROBE_SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((out / "PROBE_SUMMARY.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
