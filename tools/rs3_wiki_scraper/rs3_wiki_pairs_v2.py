#!/usr/bin/env python3
"""Small category-to-category reference collector for RuneScape Wiki assets.

Production profile: Woodcutting tree scenery <-> log inventory item.
Supports ordinary 1:1 filename pairs plus explicit 1:many aliases such as
Tree / Dead tree / Evergreen -> Logs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import requests

API = "https://runescape.wiki/api.php"
UA = "BBXAI-RS3-Asset-Pairs/2.0 (personal research/reference)"

PROFILES = {
    "woodcutting": {
        "skill": "Woodcutting",
        "left_label": "tree",
        "right_label": "logs",
        "left_category": "Category:Tree images",
        "right_category": "Category:Log images",
        "left_suffixes": [" tree", " trees"],
        "right_suffixes": [" logs", " log"],
        "left_aliases": {
            "tree": "__basic_logs__",
            "dead tree": "__basic_logs__",
            "evergreen": "__basic_logs__",
            "evergreen tree": "__basic_logs__",
        },
        "right_aliases": {"logs": "__basic_logs__", "log": "__basic_logs__"},
        "one_to_many_keys": {"__basic_logs__"},
    }
}

REJECT = [
    r"\b(?:19|20)\d{2}\b", r"\bstump\b", r"\bstage\s*\d+\b",
    r"\bproduce\b", r"\bgrown\b", r"\bempty\b", r"\bpotted\b",
    r"\bchop(?:ping|ped)?\b", r"\bcutting\b", r"\banimation\b",
    r"\bupdate image\b", r"\bevent\b", r"\blocation\b", r"\bdetail\b",
    r"\bmap\b", r"\binterface\b", r"\bicon\b",
]


def base(title: str) -> str:
    s = title.removeprefix("File:")
    s = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", s)
    s = re.sub(r"\s+", " ", s.replace("_", " ")).strip().casefold()
    return s


def safe(s: str) -> str:
    s = s.removeprefix("File:").replace("/", "_").replace("\\", "_")
    return re.sub(r'[\x00-\x1f<>:"|?*]', "_", s).strip() or "unnamed"


def canonical(title: str) -> bool:
    n = base(title)
    return "(" not in n and ")" not in n and not any(re.search(p, n, re.I) for p in REJECT)


def keys_for(title: str, suffixes: list[str], aliases: dict[str, str]) -> set[str]:
    n = base(title)
    keys = {n}
    if n in aliases:
        keys.add(aliases[n])
    for suffix in suffixes:
        if n.endswith(suffix) and len(n) > len(suffix):
            keys.add(n[:-len(suffix)].strip())
    return {k for k in keys if k}


def score(title: str, label: str):
    n = base(title)
    return (
        10000 if canonical(title) else 0,
        500 if title.casefold().endswith(".png") else 0,
        250 if label in n else 0,
        -len(n),
        title.casefold(),
    )


class Wiki:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def api(self, **params):
        p = {"action": "query", "format": "json", "formatversion": 2, **params}
        while True:
            r = self.s.get(API, params=p, timeout=60)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2)
                continue
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            time.sleep(0.06)
            return data

    def category_files(self, category: str) -> list[str]:
        out, cont = [], {}
        while True:
            d = self.api(list="categorymembers", cmtitle=category, cmtype="file",
                         cmnamespace="6", cmlimit="max", **cont)
            out.extend(x["title"] for x in d["query"]["categorymembers"])
            if "continue" not in d:
                return out
            cont = d["continue"]

    def image_info(self, titles: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(titles), 25):
            d = self.api(prop="imageinfo", titles="|".join(titles[i:i+25]),
                         iiprop="url|mime|size|sha1")
            for page in d["query"].get("pages", []):
                ii = page.get("imageinfo") or []
                if ii:
                    out[page["title"]] = ii[0]
        return out


def index(titles: list[str], suffixes: list[str], aliases: dict[str, str]):
    out = defaultdict(list)
    for title in titles:
        if canonical(title):
            for key in keys_for(title, suffixes, aliases):
                out[key].append(title)
    return out


def build_pairs(profile: dict, left: dict, right: dict):
    pairs = []
    for key in sorted(set(left) & set(right)):
        best_right = max(right[key], key=lambda t: score(t, profile["right_label"]))
        if key in profile["one_to_many_keys"]:
            left_choices = sorted(set(left[key]))
        else:
            left_choices = [max(left[key], key=lambda t: score(t, profile["left_label"]))]
        for best_left in left_choices:
            pairs.append((key, best_left, best_right))

    # Same file pair can arise through raw-name and stripped-suffix keys.
    unique = {}
    for key, l, r in pairs:
        unique.setdefault((l, r), key)
    return sorted((key, l, r) for (l, r), key in unique.items())


def download(session, url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1()
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if chunk:
                    h.update(chunk); f.write(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="woodcutting")
    ap.add_argument("--out", default="rs3_wiki_pairs")
    args = ap.parse_args()

    p = PROFILES[args.profile]
    root = Path(args.out).resolve(); root.mkdir(parents=True, exist_ok=True)
    wiki = Wiki()

    print(f"Profile: {args.profile}", flush=True)
    lf = wiki.category_files(p["left_category"])
    rf = wiki.category_files(p["right_category"])
    print(f"Category files: {len(lf)} left / {len(rf)} right", flush=True)

    li = index(lf, p["left_suffixes"], p["left_aliases"])
    ri = index(rf, p["right_suffixes"], p["right_aliases"])
    pairs = build_pairs(p, li, ri)
    titles = sorted({x for _, l, r in pairs for x in (l, r)})
    print(f"Matched {len(pairs)} relationships; {len(titles)} unique images", flush=True)
    info = wiki.image_info(titles)

    rows, failures = [], []
    for i, (key, left, right) in enumerate(pairs, 1):
        # one-to-many groups get distinct folders by left asset name
        folder_key = base(left) if key in p["one_to_many_keys"] else key
        pair_dir = root / p["skill"] / "Pairs" / safe(folder_key)
        for side, title in ((p["left_label"], left), (p["right_label"], right)):
            ii = info.get(title)
            if not ii or not str(ii.get("mime", "")).startswith("image/"):
                failures.append((key, side, title, "missing image info")); continue
            dest = pair_dir / f"{side}__{safe(title)}"
            try:
                local_sha1 = download(wiki.s, ii["url"], dest)
            except Exception as exc:
                failures.append((key, side, title, repr(exc))); continue
            rows.append({"pair_key": key, "folder_key": folder_key, "side": side,
                         "file_title": title, "path": str(dest.relative_to(root)),
                         "source_url": ii["url"], "mime": ii.get("mime", ""),
                         "width": ii.get("width", ""), "height": ii.get("height", ""),
                         "wiki_sha1": ii.get("sha1", ""), "local_sha1": local_sha1})
        if i % 10 == 0 or i == len(pairs):
            print(f"  downloaded {i}/{len(pairs)} relationships", flush=True)

    with (root / "_pairs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["pair_key", "left_file", "right_file"]); w.writerows(pairs)
    if rows:
        with (root / "_manifest.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    if failures:
        with (root / "_failures.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["pair_key", "side", "file", "error"]); w.writerows(failures)

    used_l, used_r = {l for _, l, _ in pairs}, {r for _, _, r in pairs}
    (root / "_unmatched_left.txt").write_text("\n".join(sorted(set(lf)-used_l)), encoding="utf-8")
    (root / "_unmatched_right.txt").write_text("\n".join(sorted(set(rf)-used_r)), encoding="utf-8")
    (root / "_run_info.json").write_text(json.dumps({
        "profile": args.profile, "left_category": p["left_category"],
        "right_category": p["right_category"], "left_category_files": len(lf),
        "right_category_files": len(rf), "relationships": len(pairs),
        "unique_images": len(titles), "downloaded_files": len(rows),
        "failures": len(failures)}, indent=2), encoding="utf-8")

    zip_path = root.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for file in root.rglob("*"):
            if file.is_file(): z.write(file, file.relative_to(root.parent))
    print(f"Done: {zip_path}", flush=True)


if __name__ == "__main__":
    main()
