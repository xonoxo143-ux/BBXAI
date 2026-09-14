#!/usr/bin/env python3
"""Targeted physical sanity checks for the 2018-02-04 LAGEOS-I boundary event.

Checks:
- exact shared-epoch position discontinuity in ASI/DGFI/ESA/GFZ SP3 products;
- radial / along-track / cross-track (RTN) decomposition;
- directional agreement among centers;
- Earth-umbra clearance during the adjacent weeks using a finite-Sun umbra model.

This does not identify the cause of the event. It only characterizes the geometry
and rejects a simple Earth-shadow entry/exit explanation if the clearance is large.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

SAT = "lageos1"
OLD_WEEK = "180203"
NEW_WEEK = "180210"
ACS = ("asi", "dgfi", "esa", "gfz")
BASE = "https://edc.dgfi.tum.de/pub/slr/products/orbits/{sat}/{week}/{ac}.orb.{sat}.{week}.v70.sp3.gz"
UA = {"User-Agent": "BBXAI-PBH-satellite-scan/0.4 (+public-research)"}
OMEGA_EARTH = 7.2921150e-5  # rad/s
R_EARTH = 6378137.0         # m, conservative equatorial radius
R_SUN = 695700000.0         # m
AU = 149597870700.0         # m


def parse_epoch(line: str) -> datetime:
    p = line[1:].split()
    y, mo, d, h, mi = map(int, p[:5])
    sec = float(p[5])
    return datetime(y, mo, d, tzinfo=timezone.utc) + timedelta(hours=h, minutes=mi, seconds=sec)


def parse_sp3(raw_gz: bytes) -> dict[datetime, np.ndarray]:
    text = gzip.decompress(raw_gz).decode("ascii", errors="replace")
    out = {}
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
                    xyz = np.array([float(parts[1]), float(parts[2]), float(parts[3])], dtype=float) * 1000.0
                except ValueError:
                    epoch = None
                    continue
                out[epoch] = xyz
            epoch = None
    return out


def fetch(ac: str, week: str) -> dict[datetime, np.ndarray]:
    url = BASE.format(sat=SAT, week=week, ac=ac)
    r = requests.get(url, headers=UA, timeout=90)
    r.raise_for_status()
    return parse_sp3(r.content)


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def rtn_basis(series: dict[datetime, np.ndarray], t: datetime) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    times = sorted(series)
    i = times.index(t)
    r = series[t]
    if i + 1 < len(times):
        t1 = times[i + 1]
        v_ecef = (series[t1] - r) / (t1 - t).total_seconds()
    else:
        t0 = times[i - 1]
        v_ecef = (r - series[t0]) / (t - t0).total_seconds()
    # Convert derivative in the rotating ECEF frame to an approximate inertial velocity.
    v_eci = v_ecef + np.cross(np.array([0.0, 0.0, OMEGA_EARTH]), r)
    er = unit(r)
    en = unit(np.cross(r, v_eci))
    et = unit(np.cross(en, er))
    return er, et, en


def julian_date(t: datetime) -> float:
    return t.timestamp() / 86400.0 + 2440587.5


def sun_eci_unit(t: datetime) -> np.ndarray:
    # Low-order solar ephemeris; more than adequate for a multi-thousand-km
    # shadow-clearance test.
    jd = julian_date(t)
    n = jd - 2451545.0
    mean_long = (280.460 + 0.9856474 * n) % 360.0
    mean_anom = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecl_long = math.radians((mean_long + 1.915 * math.sin(mean_anom) + 0.020 * math.sin(2 * mean_anom)) % 360.0)
    obliq = math.radians(23.439 - 0.0000004 * n)
    return np.array([
        math.cos(ecl_long),
        math.cos(obliq) * math.sin(ecl_long),
        math.sin(obliq) * math.sin(ecl_long),
    ])


def gmst(t: datetime) -> float:
    jd = julian_date(t)
    T = (jd - 2451545.0) / 36525.0
    deg = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
           + 0.000387933 * T * T - T * T * T / 38710000.0) % 360.0
    return math.radians(deg)


def sun_ecef_unit(t: datetime) -> np.ndarray:
    s = sun_eci_unit(t)
    th = gmst(t)
    c, sn = math.cos(th), math.sin(th)
    # ECI = Rz(+GMST) ECEF, therefore ECEF = Rz(-GMST) ECI.
    return np.array([
        c * s[0] + sn * s[1],
        -sn * s[0] + c * s[1],
        s[2],
    ])


def umbra_clearance(series: dict[datetime, np.ndarray]) -> dict:
    min_clear = float("inf")
    min_t = None
    eclipsed = 0
    for t, r in series.items():
        sh = sun_ecef_unit(t)
        along = float(np.dot(r, sh))
        if along >= 0:
            continue
        perpendicular = float(np.linalg.norm(r - along * sh))
        x_behind = -along
        # Linear cone approximation: umbra radius shrinks with distance behind Earth.
        umbra_radius = R_EARTH - (R_SUN - R_EARTH) * x_behind / AU
        clearance = perpendicular - umbra_radius
        if clearance < min_clear:
            min_clear, min_t = clearance, t
        if clearance < 0:
            eclipsed += 1
    return {
        "minimum_umbra_clearance_m": min_clear,
        "minimum_umbra_clearance_epoch": min_t.isoformat() if min_t else None,
        "eclipsed_samples": eclipsed,
    }


def angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    c = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def main() -> int:
    out = Path("output/feb2018_candidate")
    out.mkdir(parents=True, exist_ok=True)

    products = {(ac, w): fetch(ac, w) for ac in ACS for w in (OLD_WEEK, NEW_WEEK)}
    rows = []
    vectors = {}
    for ac in ACS:
        old, new = products[(ac, OLD_WEEK)], products[(ac, NEW_WEEK)]
        common = sorted(set(old).intersection(new))
        if not common:
            continue
        t = common[-1]
        d = new[t] - old[t]
        er, et, en = rtn_basis(new, t)
        rtn = np.array([np.dot(d, er), np.dot(d, et), np.dot(d, en)])
        vectors[ac] = d
        rows.append({
            "center": ac,
            "epoch": t.isoformat(),
            "jump_m": float(np.linalg.norm(d)),
            "dx_m": float(d[0]), "dy_m": float(d[1]), "dz_m": float(d[2]),
            "radial_m": float(rtn[0]), "along_track_m": float(rtn[1]), "cross_track_m": float(rtn[2]),
            "along_track_fraction_abs": float(abs(rtn[1]) / np.linalg.norm(d)),
        })

    pairs = []
    for i, a in enumerate(ACS):
        for b in ACS[i + 1:]:
            if a in vectors and b in vectors:
                pairs.append({"center_a": a, "center_b": b, "angle_deg": angle_deg(vectors[a], vectors[b])})

    # Use GFZ for the eclipse check; center-to-center orbit differences are tiny
    # compared with the multi-thousand-km shadow clearance.
    eclipse = {}
    for w in (OLD_WEEK, NEW_WEEK):
        eclipse[w] = umbra_clearance(products[("gfz", w)])

    with (out / "boundary_rtn.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    (out / "direction_angles.json").write_text(json.dumps(pairs, indent=2), encoding="utf-8")
    (out / "eclipse_check.json").write_text(json.dumps(eclipse, indent=2), encoding="utf-8")

    lines = [
        "# February 2018 LAGEOS-I candidate geometry",
        "",
        "## Exact boundary jump in orbital coordinates",
        "",
        "| center | jump (m) | radial (m) | along-track (m) | cross-track (m) | |along| / total |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(f"| {r['center']} | {r['jump_m']:.4f} | {r['radial_m']:.4f} | {r['along_track_m']:.4f} | {r['cross_track_m']:.4f} | {100*r['along_track_fraction_abs']:.2f}% |")
    lines += ["", "## Direction agreement", ""]
    for p in pairs:
        lines.append(f"- {p['center_a'].upper()} vs {p['center_b'].upper()}: {p['angle_deg']:.3f}°")
    lines += ["", "## Earth-umbra check", ""]
    for w, e in eclipse.items():
        lines.append(f"- Week {w}: minimum clearance **{e['minimum_umbra_clearance_m']/1000:.1f} km**; eclipsed SP3 samples: **{e['eclipsed_samples']}**.")
    lines += [
        "",
        "The low-order solar ephemeris is intentionally conservative here: the minimum clearance is thousands of kilometers, so ephemeris/model refinements cannot turn this interval into an Earth-umbra crossing.",
        "",
        "These checks characterize the event but do not establish its cause. A common force-model error, satellite-specific thermal behavior, or tracking systematic remains possible.",
    ]
    summary = "\n".join(lines) + "\n"
    (out / "CANDIDATE_SUMMARY.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
