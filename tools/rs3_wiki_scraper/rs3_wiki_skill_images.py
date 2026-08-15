#!/usr/bin/env python3
"""
Download RuneScape Wiki reference images grouped by skill.

For Woodcutting, --tree-only narrows the scrape to tree resource pages and
keeps one canonical tree render per page rather than every image referenced by
the whole Woodcutting category tree. This is the default mode used by the
GitHub Actions workflow on the rs3-wiki-skill-image-runner branch.
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
    "BBXAI-RS3-Skill-Image-Archiver/1.1 "
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

TREE_PAGE_INCLUDE = [
    r"(?i)\btree\b",
    r"(?i)\bevergreen\b",
    r"(?i)\barctic pine\b",
    r"(?i)\beucalyptus\b",
    r"(?i)\bbloodwood\b",
    r"(?i)\bblisterwood\b",
]

TREE_PAGE_EXCLUDE = [
    r"(?i)\bstump\b",
    r"(?i)\bsapling\b",
    r"(?i)\bseed\b",
    r"(?i)\bpatch\b",
    r"(?i)\btree[- ]?shaking\b",
    r"(?i)\bspirit tree\b",
    r"(?i)\bevil tree\b",
    r"(?i)\btree gnome\b",
    r"(?i)\btreehouse\b",
    r"(?i)\bcanopy\b",
]

TREE_IMAGE_EXCLUDE = [
    r"(?i)\blogs?\b",
    r"(?i)\bdetail\b",
    r"(?i)\bicon\b",
    r"(?i)\bmap\b",
    r"(?i)\blocation\b",
    r"(?i)\binterface\b",
    r"(?i)\bupdate image\b",
    r"(?i)\bevent\b",
    r"(?i)\bfarm(?:ing)?\b",
    r"(?i)\bpatch\b",
    r"(?i)\bseed\b",
    r"(?i)\bsapling\b",
    r"(?i)\bstump\b",
    r"(?i)\bcutting\b",
    r"(?i)\bchopping\b",
    r"(?i)\bequipped\b",
    r"(?i)\bplayer\b",
    r"(?i)\banimation\b",
    r"(?i)\bbanner\b",
    r"(?i)\bposter\b",
    r"(?i)\bskillcape\b",
]


def safe_name(name: str) -> str:
    name = name.removeprefix("File:")
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)
    return name.strip() or "unnamed_file"


def norm(text: str) -> str:
    text = text.removeprefix("File:")
    text = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", text)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().casefold()


def is_tree_page(title: str) -> bool:
    if any(re.search(p, title) for p in TREE_PAGE_EXCLUDE):
        return False
    return any(re.search(p, title) for p in TREE_PAGE_INCLUDE)


def tree_image_score(page_title: str, image_title: str) -> int:
    """Higher means more likely to be the clean canonical render for a page."""
    if any(re.search(p, image_title) for p in TREE_IMAGE_EXCLUDE):
        return -10_000

    page = norm(page_title)
    image = norm(image_title)
    score = 0

    if image == page:
        score += 10_000
    elif image == f"{page} tree":
        score += 9_500
    elif page in image:
        score += 8_000
    elif image in page and len(image) >= 5:
        score += 6_000

    if "tree" in image:
        score += 1_000

    # Prefer current canonical-looking filenames over dated/variant renders.
    if not re.search(r"\([^)]*\)", image_title):
        score += 300
    if not re.search(r"\b(?:19|20)\d{2}\b", image_title):
        score += 300
    if image_title.casefold().endswith(".png"):
        score += 100

    return score


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


def canonical_tree_images(page_images: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    """Keep one best candidate image for every tree page."""
    selected: Dict[str, Set[str]] = defaultdict(set)
    for page, images in page_images.items():
        ranked = sorted(
            ((tree_image_score(page, image), image) for image in images),
            key=lambda x: (x[0], x[1]),
            reverse=True,
        )
        if ranked and ranked[0][0] > 0:
            selected[page].add(ranked[0][1])
    return selected


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
    parser.add_argument(
        "--tree-only",
        action="store_true",
        help="For Woodcutting, keep only canonical renders from tree resource pages",
    )
    parser.add_argument("--no-zip", action="store_true")
    args = parser.parse_args()

    skills = DEFAULT_SKILLS if args.all_skills else args.skills
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    wiki = Wiki()
    global_manifest = []
    failures = []
    tree_page_counts = {}

    for skill in skills:
        print(f"\n=== {skill} ===", flush=True)
        skill_dir = out / skill
        skill_dir.mkdir(parents=True, exist_ok=True)

        print("Walking categories...", flush=True)
        pages = sorted(wiki.pages_in_skill(skill, args.depth))
        print(f"  {len(pages)} article pages before mode filters", flush=True)

        tree_mode = args.tree_only and skill.casefold() == "woodcutting"
        if tree_mode:
            pages = [p for p in pages if is_tree_page(p)]
            tree_page_counts[skill] = len(pages)
            print(f"  {len(pages)} likely tree resource pages", flush=True)
            (skill_dir / "_tree_pages.txt").write_text("\n".join(pages), encoding="utf-8")
        else:
            (skill_dir / "_pages.txt").write_text("\n".join(pages), encoding="utf-8")

        print("Finding referenced images...", flush=True)
        page_images = wiki.images_on_pages(pages)
        if tree_mode:
            page_images = canonical_tree_images(page_images)

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
            if not str(ii.get("mime", "")).startswith("image/"):
                continue

            filename = safe_name(title)
            if filename.lower() in used_names:
                stem, dot, ext = filename.rpartition(".")
                stem = stem or filename
                filename = f"{stem}_{idx}{dot}{ext}" if dot else f"{filename}_{idx}"
            used_names.add(filename.lower())

            if tree_mode:
                primary_page = sorted(image_to_pages[title])[0]
                dest = skill_dir / "Trees" / safe_name(primary_page) / filename
            else:
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
                "filename": str(dest.relative_to(skill_dir)),
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
            "tree_only": args.tree_only,
            "tree_page_counts": tree_page_counts,
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
