#!/usr/bin/env python3
"""Record the exact files contained in a Zenodo record/archive."""
from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default="18441938")
    ap.add_argument("--out", default="output/input_manifest.json")
    args = ap.parse_args()

    meta_url = f"https://zenodo.org/api/records/{args.record}"
    meta = requests.get(meta_url, timeout=60)
    meta.raise_for_status()
    payload = meta.json()

    files = payload.get("files", [])
    if isinstance(files, dict):
        entries = files.get("entries", {})
        if isinstance(entries, dict):
            files = [dict(v, key=k) for k, v in entries.items()]
        else:
            files = entries or []

    manifest = {"record": args.record, "record_files": [], "archive_members": []}
    for item in files:
        name = str(item.get("key") or item.get("filename") or item.get("name") or "unknown")
        links = item.get("links") or {}
        url = links.get("content") or links.get("self") or item.get("download")
        rec = {"name": name, "size": item.get("size"), "url": url}
        manifest["record_files"].append(rec)
        print("RECORD FILE", name, item.get("size"))
        if not url or not name.lower().endswith(".zip"):
            continue
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            for info in zf.infolist():
                member = {"archive": name, "name": info.filename, "size": info.file_size}
                manifest["archive_members"].append(member)
                print("  MEMBER", info.filename, info.file_size)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
