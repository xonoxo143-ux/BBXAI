#!/usr/bin/env python3
"""Download a Zenodo orbital-residual dataset and rank anomaly candidates.

Designed for the LAGEOS/LAGEOS-II supporting data in Zenodo record 18441938,
but intentionally generic enough to inspect other tabular Zenodo records.

This is an anomaly finder, not a PBH detector. It ranks values, first-difference
jumps, and local mean change-points. Physical vetting must follow.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import tarfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests

ZENODO_API = "https://zenodo.org/api/records/{record_id}"
TEXT_EXTS = {".csv", ".tsv", ".txt", ".dat", ".res", ".asc", ".out", ".lis"}
SHEET_EXTS = {".xlsx", ".xls"}


@dataclass
class Candidate:
    source: str
    sheet: str
    column: str
    row: int
    epoch: str
    value: float
    value_z: float
    diff_z: float
    cp_score: float
    score: float


def robust_z(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    if not np.isfinite(mad) or mad == 0:
        sd = np.nanstd(x)
        if not np.isfinite(sd) or sd == 0:
            return np.zeros_like(x, dtype=float)
        return (x - med) / sd
    return 0.6744897501960817 * (x - med) / mad


def cp_scores(x: np.ndarray, windows=(2, 3, 5, 10)) -> np.ndarray:
    """Return the strongest robust two-sided local mean-shift score."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    out = np.zeros(n, dtype=float)
    for w in windows:
        if n < 2 * w + 1:
            continue
        raw = np.full(n, np.nan, dtype=float)
        for i in range(w, n - w):
            left = x[i - w:i]
            right = x[i:i + w]
            if np.isfinite(left).sum() < max(2, w // 2):
                continue
            if np.isfinite(right).sum() < max(2, w // 2):
                continue
            raw[i] = np.nanmean(right) - np.nanmean(left)
        z = np.abs(robust_z(raw))
        z[~np.isfinite(z)] = 0.0
        out = np.maximum(out, z)
    return out


def safe_extract_zip(path: Path, dest: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise RuntimeError(f"Unsafe ZIP path: {member.filename}")
        zf.extractall(dest)


def safe_extract_tar(path: Path, dest: Path) -> None:
    mode = "r:gz" if path.name.endswith((".tar.gz", ".tgz")) or path.suffix == ".gz" else "r:"
    with tarfile.open(path, mode) as tf:
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise RuntimeError(f"Unsafe TAR path: {member.name}")
        tf.extractall(dest)


def flatten_files(payload: dict) -> list[dict]:
    files = payload.get("files", [])
    if isinstance(files, list):
        return files
    if isinstance(files, dict):
        if isinstance(files.get("entries"), dict):
            out = []
            for key, val in files["entries"].items():
                item = dict(val)
                item.setdefault("key", key)
                out.append(item)
            return out
        if isinstance(files.get("entries"), list):
            return list(files["entries"])
    return []


def file_name(item: dict) -> str:
    return str(item.get("key") or item.get("filename") or item.get("name") or "download.bin")


def file_url(item: dict) -> str | None:
    links = item.get("links") or {}
    return links.get("content") or links.get("self") or item.get("download")


def download_record(record_id: str, root: Path, max_mb: float) -> tuple[dict, list[Path]]:
    root.mkdir(parents=True, exist_ok=True)
    r = requests.get(ZENODO_API.format(record_id=record_id), timeout=60)
    r.raise_for_status()
    payload = r.json()
    (root / "record.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    downloaded: list[Path] = []
    for item in flatten_files(payload):
        name = file_name(item)
        size = item.get("size")
        if isinstance(size, (int, float)) and size > max_mb * 1024 * 1024:
            print(f"SKIP {name}: {size / 1024 / 1024:.1f} MB > {max_mb:.1f} MB")
            continue
        url = file_url(item)
        if not url:
            print(f"SKIP {name}: no download URL")
            continue
        dest = root / Path(name).name
        print(f"DOWNLOAD {name}")
        with requests.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            total = 0
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_mb * 1024 * 1024:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise RuntimeError(f"Download exceeded --max-mb for {name}")
                    fh.write(chunk)
        downloaded.append(dest)
    return payload, downloaded


def expand_archives(paths: Iterable[Path], dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    leaves: list[Path] = []
    for p in paths:
        name = p.name.lower()
        sub = dest / re.sub(r"[^A-Za-z0-9_.-]+", "_", p.stem)
        try:
            if name.endswith(".zip"):
                sub.mkdir(exist_ok=True)
                safe_extract_zip(p, sub)
                leaves.extend(q for q in sub.rglob("*") if q.is_file())
            elif name.endswith((".tar", ".tar.gz", ".tgz")):
                sub.mkdir(exist_ok=True)
                safe_extract_tar(p, sub)
                leaves.extend(q for q in sub.rglob("*") if q.is_file())
            else:
                leaves.append(p)
        except Exception as exc:
            print(f"ARCHIVE ERROR {p.name}: {exc}")
            leaves.append(p)
    return leaves


def try_read_text(path: Path) -> pd.DataFrame | None:
    attempts = [
        dict(sep=None, engine="python", comment="#"),
        dict(sep=r"\s+", engine="python", comment="#"),
        dict(sep=",", engine="python", comment="#"),
    ]
    for kwargs in attempts:
        try:
            df = pd.read_csv(path, **kwargs)
            if df.shape[0] >= 5 and df.shape[1] >= 1:
                return df
        except Exception:
            pass
    try:
        arr = np.genfromtxt(path, comments="#", invalid_raise=False)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.ndim == 2 and arr.shape[0] >= 5:
            return pd.DataFrame(arr, columns=[f"col_{i}" for i in range(arr.shape[1])])
    except Exception:
        pass
    return None


def tables_from(path: Path) -> list[tuple[str, pd.DataFrame]]:
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        df = try_read_text(path)
        return [("", df)] if df is not None else []
    if ext in SHEET_EXTS:
        try:
            sheets = pd.read_excel(path, sheet_name=None)
            return [(str(k), v) for k, v in sheets.items() if len(v) >= 5]
        except Exception as exc:
            print(f"SHEET ERROR {path.name}: {exc}")
    return []


def numeric_series(df: pd.DataFrame) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().sum() >= max(5, int(0.6 * len(df))):
            out[str(c)] = s.to_numpy(dtype=float)
    return out


def choose_epoch(df: pd.DataFrame, nums: dict[str, np.ndarray]) -> list[str]:
    names = list(df.columns)
    preferred = [c for c in names if any(k in str(c).lower() for k in ("mjd", "epoch", "date", "time", "day", "jd"))]
    if preferred:
        return df[preferred[0]].astype(str).tolist()
    for _, x in nums.items():
        finite = x[np.isfinite(x)]
        if len(finite) >= 5 and np.all(np.diff(finite) >= 0) and np.nanmax(finite) > np.nanmin(finite):
            return ["" if not np.isfinite(v) else f"{v:.10g}" for v in x]
    return [str(i) for i in range(len(df))]


def analyze_table(source: str, sheet: str, df: pd.DataFrame, top_per_column: int) -> list[Candidate]:
    nums = numeric_series(df)
    if not nums:
        return []
    epoch = choose_epoch(df, nums)
    out: list[Candidate] = []
    for col, x in nums.items():
        low = col.lower()
        if any(k in low for k in ("mjd", "epoch", "date", "time", "day", "jd")):
            continue
        if np.isfinite(x).sum() < 8 or np.nanstd(x) == 0:
            continue
        vz = np.abs(robust_z(x))
        d = np.full_like(x, np.nan)
        d[1:] = np.diff(x)
        dz = np.abs(robust_z(d))
        cp = cp_scores(x)
        for arr in (vz, dz, cp):
            arr[~np.isfinite(arr)] = 0.0
        score = np.maximum.reduce([vz, 1.15 * dz, 1.25 * cp])
        idxs = np.argsort(score)[::-1][:top_per_column]
        for i in idxs:
            if score[i] <= 0:
                continue
            out.append(Candidate(
                source=source,
                sheet=sheet,
                column=col,
                row=int(i),
                epoch=epoch[i] if i < len(epoch) else str(i),
                value=float(x[i]) if np.isfinite(x[i]) else math.nan,
                value_z=float(vz[i]),
                diff_z=float(dz[i]),
                cp_score=float(cp[i]),
                score=float(score[i]),
            ))
    return out


def correlated_candidates(cands: list[Candidate], top_n: int = 30) -> list[dict]:
    buckets: dict[str, list[Candidate]] = {}
    for c in cands:
        if c.score < 4.0:
            continue
        buckets.setdefault(c.epoch, []).append(c)
    rows = []
    for epoch, items in buckets.items():
        sources = sorted(set(i.source for i in items))
        columns = sorted(set(f"{i.source}:{i.column}" for i in items))
        if len(columns) < 2:
            continue
        rows.append({
            "epoch": epoch,
            "n_channels": len(columns),
            "n_sources": len(sources),
            "max_score": max(i.score for i in items),
            "sources": sources,
            "channels": columns[:12],
        })
    rows.sort(key=lambda r: (r["n_sources"], r["n_channels"], r["max_score"]), reverse=True)
    return rows[:top_n]


def markdown_summary(payload: dict, cands: list[Candidate], corr: list[dict], files_seen: list[str]) -> str:
    title = ((payload.get("metadata") or {}).get("title") or payload.get("title") or "Zenodo record")
    lines = [
        "# PBH-oriented orbital anomaly scan",
        "",
        f"Dataset: **{title}**",
        "",
        "## Important interpretation limit",
        "",
        "This is an anomaly ranking, **not evidence of primordial black holes**. For the LAGEOS supporting dataset, the published orbit determination used independent seven-day arcs with separately fitted initial state vectors. A true impulsive velocity change near an arc boundary can therefore be partially or completely absorbed by the next arc's fitted initial conditions. These residuals are useful for finding odd arcs, but raw SLR normal-point reprocessing is required for a decisive impulse search.",
        "",
        f"Files/tables considered: **{len(files_seen)}**",
        f"Ranked candidates generated: **{len(cands)}**",
        "",
        "## Highest-ranked candidates",
        "",
        "| score | epoch | source | column | value-z | diff-z | change-point |",
        "|---:|---|---|---|---:|---:|---:|",
    ]
    for c in sorted(cands, key=lambda x: x.score, reverse=True)[:40]:
        lines.append(f"| {c.score:.2f} | {c.epoch} | {Path(c.source).name} | {c.column} | {c.value_z:.2f} | {c.diff_z:.2f} | {c.cp_score:.2f} |")
    lines += ["", "## Same-epoch coincidences", ""]
    if not corr:
        lines.append("No exact same-epoch multi-channel coincidences above score 4 were found.")
    else:
        lines += [
            "| epoch | sources | channels | max score |",
            "|---|---:|---:|---:|",
        ]
        for r in corr:
            lines.append(f"| {r['epoch']} | {r['n_sources']} | {r['n_channels']} | {r['max_score']:.2f} |")
    lines += [
        "",
        "## Score definitions",
        "",
        "- **value-z**: robust outlier score of the value itself.",
        "- **diff-z**: robust outlier score of the one-step change.",
        "- **change-point**: strongest robust two-sided local mean-shift score over several window sizes.",
        "- **score**: maximum of those three, with a modest preference for persistent mean shifts.",
        "",
        "The next physical-vetting stage should cross-check top epochs against eclipses, known force-model transitions, station coverage, data gaps, and independent orbit solutions before treating any item as unexplained.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default="18441938", help="Zenodo record id")
    ap.add_argument("--workdir", default="work", help="download/extraction directory")
    ap.add_argument("--outdir", default="output", help="results directory")
    ap.add_argument("--max-mb", type=float, default=250.0, help="maximum per-file download size")
    ap.add_argument("--top-per-column", type=int, default=25)
    args = ap.parse_args()

    work = Path(args.workdir)
    out = Path(args.outdir)
    if work.exists():
        shutil.rmtree(work)
    if out.exists():
        shutil.rmtree(out)
    work.mkdir(parents=True)
    out.mkdir(parents=True)

    payload, downloads = download_record(args.record, work / "download", args.max_mb)
    leaves = expand_archives(downloads, work / "expanded")

    all_cands: list[Candidate] = []
    files_seen: list[str] = []
    for p in leaves:
        for sheet, df in tables_from(p):
            if df is None or df.empty:
                continue
            label = str(p.relative_to(work)) if work in p.parents else str(p)
            files_seen.append(label + (f"#{sheet}" if sheet else ""))
            all_cands.extend(analyze_table(label, sheet, df, args.top_per_column))

    all_cands.sort(key=lambda c: c.score, reverse=True)
    corr = correlated_candidates(all_cands)

    (out / "candidates.json").write_text(json.dumps([asdict(c) for c in all_cands], indent=2), encoding="utf-8")
    pd.DataFrame([asdict(c) for c in all_cands]).to_csv(out / "candidates.csv", index=False)
    (out / "coincidences.json").write_text(json.dumps(corr, indent=2), encoding="utf-8")
    (out / "files_seen.json").write_text(json.dumps(files_seen, indent=2), encoding="utf-8")
    (out / "SUMMARY.md").write_text(markdown_summary(payload, all_cands, corr, files_seen), encoding="utf-8")

    print((out / "SUMMARY.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
