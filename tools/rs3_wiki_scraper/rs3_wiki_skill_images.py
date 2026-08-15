#!/usr/bin/env python3
"""
Download RuneScape Wiki images associated with one or more skills.

The collector uses the RuneScape Wiki MediaWiki Action API rather than
scraping rendered HTML. It recursively walks Category:<Skill>, finds images
used by pages in that category tree, downloads original files, writes per-skill
manifests, and creates a ZIP suitable for a GitHub Actions artifact.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, List, Set

import requests

API = "https://runescape.wiki/api.php"

DEFAULT_SKILLS = [
    "Agility", "Archaeology", "Attack", "Constitution", "Construction",
    "Cooking", "Crafting", "Defence", "Divination", "Dungeoneering",
    "Farming", "Firemaking", "Fishing", "Fletching", "Herblore", "Hunter",
    "Invention", "Magic", "Mining", "Necromancy", "Prayer", "Ranged",
    "Runecrafting", "Slayer", "Smithing", "Strength", "Summoning",
    "Thieving", "Woodcutting",
]

USER_AGENT = (
    "BBXAI-RS3-Skill-Image-Archiver/1.0 "
    "(personal research/reference; MediaWiki API client)"
)

BAD_IMAGE_PATTERNS = [
    r"^File:.*\.ogg$",
    r"^File:.*\.oga$",
    r"^File:.*\.mp3$",
    r"^File:.*\.webm$",
]

DEFAULT_SKIP_PATTERNS = [
    r"(?i)\bicon\b",
    r"(?i)\bbutton\b",
    r"(?i)\bcursor\b",
    r"(?i)\binterface\b",
    r"(?i)\bwiki\b.*\blogo\b",
    r"(?i)\bnavbar\b",
    r"(?i)\bmap icon\b",
    r"(?i)\bskill icon\b",
]


def safe_name(name: str) -> str:
    name = name.removeprefix("File:")
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)
    return name.strip() or "unnamed_file"


class Wiki:
    def __init__(self, delay: float = 0.12):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})

    def api(self, **params):
        base = {"action": "query", "format": "json", "formatversion": 2}
        base.update(params)
        while True:
            r = self.s.get(API, params=base, timeout=60)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(3)
                continue
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            time.sleep(self.delay)
            return data

    def category_members(self, category: str) -> Iterable[dict]:
        cont = {}
        while True:
            data = self.api(
                list="categorymembers",
                cmtitle=category,
                cmtype="page|subcat",
                cmnamespace="0|14",
                cmlimit="max",
                **cont,
            )
            yield from data["query"]["categorymembers"]
            if "continue" not in data:
                break
            cont = data["continue"]

    def pages_in_skill(self, skill: str, depth: int) -> Set[str]:
        root = f"Category:{skill}"
        q = deque([(root, 0)])
        seen_categories = set()
        pages = set()

        while q:
            cat, d = q.popleft()
            if cat in seen_categories:
                continue
            seen_categories.add(cat)

            for member in self.category_members(cat):
                namespace = member.get("ns")
                title = member["title"]
                if namespace == 0:
                    pages.add(title)
                elif namespace == 14 and d < depth:
                    q.append((title, d + 1))

        return pages

    def images_on_pages(self, titles: List[str]) -> Dict[str, Set[str]]:
        out: Dict[str, Set[str]] = defaultdict(set)
        for i in range(0, len(titles), 25):
            chunk = titles[i:i + 25]
            cont = {}
            while True:
                data = self.api(
                    prop="images",
                    titles="|".join(chunk),
                    imlimit="max",
                    **cont,
                )
                for page in data["query"].get("pages", []):
                    page_title = page.get("title")
                    for image in page.get("images", []):
                        out[page_title].add(image["title"])
                if "continue" not in data:
                    break
                cont = data["continue"]
        return out

    def image_info(self, file_titles: List[str]) -> Dict[str, dict]:
        out = {}
        for i in range(0, len(file_titles), 25):
            chunk = file_titles[i:i + 25]
            data = self.api(
                prop="imageinfo",
                titles="|".join(chunk),
                iiprop="url|mime|size|sha1",
            )
            for page in data["query"].get("pages", []):
                title = page.get("title")
                infos = page.get("imageinfo") or []
                if infos:
                    out[title] = infos[0]
        return out


def rejected(title: str, skip_ui: bool) -> bool:
    if any(re.search(pattern, title) for pattern in BAD_IMAGE_PATTERNS):
        return True
    return skip_ui and any(re.search(pattern, title) for pattern in DEFAULT_SKIP_PATTERNS)


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(session: requests.Session, url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return sha1_file(dest)

    tmp = dest.with_suffix(dest.suffix + ".part")
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for block in r.iter_content(1024 * 256):
                if block:
                    f.write(block)
    tmp.replace(dest)
    return sha1_file(dest)


def zip_tree(root: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(root.parent))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--skills", nargs="+", help="Skills to collect")
    group.add_argument("--all-skills", action="store_true")
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--out", default="rs3_wiki_skill_images")
    parser.add_argument("--keep-ui", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    skills = DEFAULT_SKILLS if args.all_skills else args.skills
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    wiki = Wiki()
    global_manifest = []
    failures = []

    for skill in skills:
        print(f"\n=== {skill} ===", flush=True)
        skill_dir = out / skill
        skill_dir.mkdir(parents=True, exist_ok=True)

        print("Walking categories...", flush=True)
        pages = sorted(wiki.pages_in_skill(skill, args.depth))
        print(f"  {len(pages)} article pages", flush=True)
        (skill_dir / "_pages.txt").write_text("\n".join(pages), encoding="utf-8")

        print("Finding referenced images...", flush=True)
        page_images = wiki.images_on_pages(pages)
        image_to_pages: Dict[str, Set[str]] = defaultdict(set)
        for page, images in page_images.items():
            for image in images:
                if not rejected(image, skip_ui=not args.keep_ui):
                    image_to_pages[image].add(page)

        image_titles = sorted(image_to_pages)
        print(f"  {len(image_titles)} candidate images after filters", flush=True)

        print("Resolving original file URLs...", flush=True)
        info = wiki.image_info(image_titles)
        rows = []
        used_names = set()

        for idx, title in enumerate(image_titles, 1):
            ii = info.get(title)
            if not ii or not ii.get("url"):
                failures.append((skill, title, "No image URL returned"))
                continue

            filename = safe_name(title)
            if filename.lower() in used_names:
                stem, dot, ext = filename.rpartition(".")
                stem = stem or filename
                filename = f"{stem}_{idx}{dot}{ext}" if dot else f"{filename}_{idx}"
            used_names.add(filename.lower())

            dest = skill_dir / filename
            try:
                local_sha1 = download(wiki.s, ii["url"], dest)
            except Exception as exc:
                failures.append((skill, title, repr(exc)))
                print(f"  FAILED {title}: {exc}", flush=True)
                continue

            row = {
                "skill": skill,
                "file_title": title,
                "filename": filename,
                "source_url": ii["url"],
                "mime": ii.get("mime", ""),
                "width": ii.get("width", ""),
                "height": ii.get("height", ""),
                "wiki_sha1": ii.get("sha1", ""),
                "local_sha1": local_sha1,
                "used_on_pages": " | ".join(sorted(image_to_pages[title])),
            }
            rows.append(row)
            global_manifest.append(row)

            if idx % 25 == 0 or idx == len(image_titles):
                print(f"  downloaded/resolved {idx}/{len(image_titles)}", flush=True)

        manifest_path = skill_dir / "_manifest.csv"
        with manifest_path.open("w", newline="", encoding="utf-8") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

    if global_manifest:
        with (out / "_all_images_manifest.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=global_manifest[0].keys())
            writer.writeheader()
            writer.writerows(global_manifest)

    (out / "_run_info.json").write_text(
        json.dumps({
            "api": API,
            "skills": skills,
            "depth": args.depth,
            "skip_ui": not args.keep_ui,
            "image_count": len(global_manifest),
            "failures": len(failures),
        }, indent=2),
        encoding="utf-8",
    )

    if failures:
        with (out / "_failures.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["skill", "file_title", "error"])
            writer.writerows(failures)

    if not args.no_zip:
        zip_path = out.with_suffix(".zip")
        print(f"\nCreating {zip_path} ...", flush=True)
        zip_tree(out, zip_path)
        print(f"Done: {zip_path}", flush=True)
    else:
        print(f"\nDone: {out}", flush=True)


if __name__ == "__main__":
    main()
