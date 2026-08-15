#!/usr/bin/env python3
"""Collect paired RuneScape Wiki image assets directly from image categories.

The first production profile is Woodcutting: Tree images <-> Log images.
Unlike the old skill crawler, this never walks hundreds of skill article pages.
It asks MediaWiki for the two file categories, normalizes filenames, pairs
matching resources, downloads only paired images, and records unmatched names
for auditing.
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
from typing import Dict, Iterable, List, Set, Tuple

import requests

API = "https://runescape.wiki/api.php"
USER_AGENT = "BBXAI-RS3-Asset-Pair-Collector/1.0 (personal research/reference)"

PROFILES = {
    "woodcutting": {
        "skill": "Woodcutting",
        "left_label": "tree",
        "right_label": "logs",
        "left_category": "Category:Tree images",
        "right_category": "Category:Log images",
        "left_prefixes": [],
        "left_suffixes": [" tree", " trees"],
        "right_prefixes": [],
        "right_suffixes": [" logs", " log"],
        # Multiple scenery types legitimately yield the basic Logs item.
        "left_aliases": {
            "tree": "__basic_logs__",
            "dead tree": "__basic_logs__",
            "evergreen": "__basic_logs__",
            "evergreen tree": "__basic_logs__",
        },
        "right_aliases": {
            "logs": "__basic_logs__",
            "log": "__basic_logs__",
        },
    },
}

# Strong signs that an image is a variant, historical capture, state, screenshot,
# or otherwise not the clean canonical asset we want for art reference.
CANONICAL_REJECT = [
    r"\b(?:19|20)\d{2}\b",
    r"\bstump\b",
    r"\bstage\s*\d+\b",
    r"\bproduce\b",
    r"\bgrown\b",
    r"\bempty\b",
    r"\bpotted\b",
    r"\bchop(?:ping|ped)?\b",
    r"\bcutting\b",
    r"\banimation\b",
    r"\bupdate image\b",
    r"\bevent\b",
    r"\blocation\b",
    r"\bdetail\b",
    r"\bmap\b",
    r"\binterface\b",
    r"\bicon\b",
]


def base_name(title: str) -> str:
    s = title.removeprefix("File:")
    s = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", s)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip().casefold()
    return s


def safe_name(text: str) -> str:
    text = text.removeprefix("File:").replace("/", "_").replace("\\", "_")
    text = re.sub(r'[\x00-\x1f<>:"|?*]', "_", text)
    return text.strip() or "unnamed"


def canonical_ok(title: str) -> bool:
    n = base_name(title)
    if any(re.search(p, n, flags=re.I) for p in CANONICAL_REJECT):
        return False
    # Parenthesized variants are usually location/state/history variants.
    if "(" in n or ")" in n:
        return False
    return True


def candidate_keys(title: str, prefixes: List[str], suffixes: List[str], aliases: Dict[str, str]) -> Set[str]:
    n = base_name(title)
    keys = {n}
    if n in aliases:
        keys.add(aliases[n])
    for prefix in prefixes:
        if n.startswith(prefix) and len(n) > len(prefix):
            keys.add(n[len(prefix):].strip())
    for suffix in suffixes:
        if n.endswith(suffix) and len(n) > len(suffix):
            keys.add(n[:-len(suffix)].strip())
    return {k for k in keys if k}


def canonical_score(title: str, side_label: str) -> Tuple[int, int, str]:
    """Prefer exact modern-looking inventory/scenery names and PNGs."""
    n = base_name(title)
    score = 0
    if canonical_ok(title):
        score += 10000
    if title.casefold().endswith(".png"):
        score += 500
    if side_label in n:
        score += 250
    # Shorter names tend to be canonical compared with descriptive variants.
    return (score, -len(n), title.casefold())


class Wiki:
    def __init__(self, delay: float = 0.08):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})

    def api(self, **params):
        base = {"action": "query", "format": "json", "formatversion": 2}
        base.update(params)
        while True:
            r = self.s.get(API, params=base, timeout=60)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2)
                continue
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            time.sleep(self.delay)
            return data

    def category_files(self, category: str) -> List[str]:
        files: List[str] = []
        cont = {}
        while True:
            data = self.api(
                list="categorymembers",
                cmtitle=category,
                cmtype="file",
                cmnamespace="6",
                cmlimit="max",
                **cont,
            )
            files.extend(m["title"] for m in data["query"]["categorymembers"])
            if "continue" not in data:
                break
            cont = data["continue"]
        return files

    def image_info(self, titles: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for i in range(0, len(titles), 25):
            data = self.api(
                prop="imageinfo",
                titles="|".join(titles[i:i + 25]),
                iiprop="url|mime|size|sha1",
            )
            for page in data["query"].get("pages", []):
                ii = page.get("imageinfo") or []
                if ii:
                    out[page["title"]] = ii[0]
        return out


def build_index(titles: Iterable[str], profile: dict, side: str) -> Dict[str, List[str]]:
    prefixes = profile[f"{side}_prefixes"]
    suffixes = profile[f"{side}_suffixes"]
    aliases = profile[f"{side}_aliases"]
    index: Dict[str, List[str]] = defaultdict(list)
    for title in titles:
        if not canonical_ok(title):
            continue
        for key in candidate_keys(title, prefixes, suffixes, aliases):
            index[key].append(title)
    return index


def choose_best(titles: List[str], side_label: str) -> str:
    return max(titles, key=lambda t: canonical_score(t, side_label))


def download(session: requests.Session, url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1()
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for block in r.iter_content(256 * 1024):
                if block:
                    h.update(block)
                    f.write(block)
    return h.hexdigest()


def zip_tree(root: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(root.parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), default="woodcutting")
    ap.add_argument("--out", default="rs3_wiki_pairs")
    ap.add_argument("--no-zip", action="store_true")
    args = ap.parse_args()

    profile = PROFILES[args.profile]
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    wiki = Wiki()

    print(f"Profile: {args.profile}", flush=True)
    print(f"Reading {profile['left_category']} ...", flush=True)
    left_files = wiki.category_files(profile["left_category"])
    print(f"  {len(left_files)} files", flush=True)
    print(f"Reading {profile['right_category']} ...", flush=True)
    right_files = wiki.category_files(profile["right_category"])
    print(f"  {len(right_files)} files", flush=True)

    left_index = build_index(left_files, profile, "left")
    right_index = build_index(right_files, profile, "right")
    paired_keys = sorted(set(left_index) & set(right_index))

    pairs = []
    selected_titles: Set[str] = set()
    for key in paired_keys:
        left = choose_best(left_index[key], profile["left_label"])
        right = choose_best(right_index[key], profile["right_label"])
        pairs.append((key, left, right))
        selected_titles.update((left, right))

    # Deduplicate identical left/right file pairs that can arise from multiple
    # normalization keys while preserving the useful semantic key.
    dedup = {}
    for key, left, right in pairs:
        dedup.setdefault((left, right), key)
    pairs = [(key, left, right) for (left, right), key in dedup.items()]
    pairs.sort(key=lambda x: (x[0], x[1], x[2]))
    selected_titles = {t for _, l, r in pairs for t in (l, r)}

    print(f"Matched {len(pairs)} canonical pairs; resolving {len(selected_titles)} files", flush=True)
    info = wiki.image_info(sorted(selected_titles))

    manifest_rows = []
    failures = []
    for i, (key, left, right) in enumerate(pairs, 1):
        pair_dir = out / profile["skill"] / "Pairs" / safe_name(key)
        for side, title in ((profile["left_label"], left), (profile["right_label"], right)):
            ii = info.get(title)
            if not ii or not str(ii.get("mime", "")).startswith("image/"):
                failures.append((key, side, title, "no usable image info"))
                continue
            filename = safe_name(title)
            dest = pair_dir / f"{side}__{filename}"
            try:
                sha1 = download(wiki.s, ii["url"], dest)
            except Exception as exc:
                failures.append((key, side, title, repr(exc)))
                continue
            manifest_rows.append({
                "profile": args.profile,
                "pair_key": key,
                "side": side,
                "file_title": title,
                "path": str(dest.relative_to(out)),
                "source_url": ii.get("url", ""),
                "mime": ii.get("mime", ""),
                "width": ii.get("width", ""),
                "height": ii.get("height", ""),
                "wiki_sha1": ii.get("sha1", ""),
                "local_sha1": sha1,
            })
        if i % 10 == 0 or i == len(pairs):
            print(f"  downloaded {i}/{len(pairs)} pairs", flush=True)

    with (out / "_pairs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pair_key", "left_file", "right_file"])
        w.writerows(pairs)

    if manifest_rows:
        with (out / "_manifest.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=manifest_rows[0].keys())
            w.writeheader()
            w.writerows(manifest_rows)

    # Audit lists are intentionally metadata-only; unmatched images are not downloaded.
    used_left = {l for _, l, _ in pairs}
    used_right = {r for _, _, r in pairs}
    (out / "_unmatched_tree_category.txt").write_text(
        "\n".join(sorted(t for t in left_files if t not in used_left)), encoding="utf-8"
    )
    (out / "_unmatched_log_category.txt").write_text(
        "\n".join(sorted(t for t in right_files if t not in used_right)), encoding="utf-8"
    )

    if failures:
        with (out / "_failures.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["pair_key", "side", "file_title", "error"])
            w.writerows(failures)

    run_info = {
        "profile": args.profile,
        "left_category": profile["left_category"],
        "right_category": profile["right_category"],
        "left_category_files": len(left_files),
        "right_category_files": len(right_files),
        "pairs": len(pairs),
        "downloaded_images": len(manifest_rows),
        "failures": len(failures),
    }
    (out / "_run_info.json").write_text(json.dumps(run_info, indent=2), encoding="utf-8")

    if not args.no_zip:
        zip_path = out.with_suffix(".zip")
        print(f"Creating {zip_path.name} ...", flush=True)
        zip_tree(out, zip_path)
        print(f"Done: {zip_path}", flush=True)


if __name__ == "__main__":
    main()
