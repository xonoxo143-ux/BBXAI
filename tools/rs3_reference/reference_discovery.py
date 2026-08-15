#!/usr/bin/env python3
"""Probe RuneScape Wiki structured data and image taxonomy without bulk downloads.

This is deliberately metadata-only. It discovers the schemas/categories we can use
later to build a canonical reference corpus (items, recipes, resources, images)
without scraping broad article trees.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://runescape.wiki/api.php"
UA = "BBXAI-RS3-Reference-Discovery/1.0 (personal research/reference)"

BUCKET_PAGES = [
    "Bucket:Infobox item",
    "Bucket:Recipe",
    "Bucket:Resource",
    "Bucket:Resource locations",
    "Bucket:Infobox scenery",
]

CATEGORY_TERMS = [
    "tree images", "log images", "ore images", "rock images", "bar images",
    "herb images", "potion images", "raw food images", "food images",
    "fish images", "gem images", "rune images", "seed images",
    "bone images", "ash images", "bow images", "arrow images",
    "pickaxe images", "hatchet images", "tool images",
]


def api(**params):
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def bucket_definition(title: str):
    data = api(
        action="query",
        prop="revisions",
        titles=title,
        rvprop="content|timestamp",
        rvslots="main",
        rvlimit="1",
    )
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return {"title": title, "missing": True}
    page = pages[0]
    revs = page.get("revisions") or []
    content = None
    timestamp = None
    if revs:
        timestamp = revs[0].get("timestamp")
        content = (revs[0].get("slots") or {}).get("main", {}).get("content")
    parsed = None
    if content:
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = None
    return {
        "title": title,
        "missing": False,
        "timestamp": timestamp,
        "raw": content,
        "parsed": parsed,
    }


def search_categories(term: str):
    data = api(
        action="query",
        list="search",
        srnamespace="14",
        srsearch=f'intitle:"{term}"',
        srlimit="20",
    )
    return [x.get("title") for x in data.get("query", {}).get("search", [])]


def category_info(titles):
    if not titles:
        return []
    data = api(action="query", prop="categoryinfo", titles="|".join(titles[:50]))
    out = []
    for p in data.get("query", {}).get("pages", []):
        out.append({"title": p.get("title"), "missing": bool(p.get("missing")), "categoryinfo": p.get("categoryinfo")})
    return out


def main():
    out = Path("rs3_reference_discovery")
    out.mkdir(exist_ok=True)

    report = {
        "api": API,
        "bucket_paraminfo": api(action="paraminfo", modules="bucket"),
        "bucket_definitions": {},
        "category_searches": {},
        "category_info": {},
    }

    print("Reading Bucket definitions...", flush=True)
    for title in BUCKET_PAGES:
        print(" ", title, flush=True)
        report["bucket_definitions"][title] = bucket_definition(title)

    print("Discovering image categories...", flush=True)
    for term in CATEGORY_TERMS:
        hits = search_categories(term)
        report["category_searches"][term] = hits
        report["category_info"][term] = category_info(hits[:10])
        print(f"  {term}: {len(hits)} hits", flush=True)

    (out / "discovery.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Human-readable summary that is easy to inspect from Actions logs/artifacts.
    lines = ["# RS3 reference discovery", ""]
    for title, d in report["bucket_definitions"].items():
        lines.append(f"## {title}")
        if d.get("missing"):
            lines.append("MISSING")
        elif isinstance(d.get("parsed"), dict):
            parsed = d["parsed"]
            fields = parsed.get("fields") or parsed.get("properties") or {}
            if isinstance(fields, dict):
                lines.append("Fields: " + ", ".join(fields.keys()))
            else:
                lines.append("Definition parsed; inspect discovery.json")
        else:
            lines.append("Found, but definition was not parsed as JSON")
        lines.append("")

    lines.append("# Image category candidates")
    for term, hits in report["category_searches"].items():
        lines.append(f"- {term}: " + (", ".join(hits[:8]) if hits else "none"))

    (out / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("Discovery complete", flush=True)


if __name__ == "__main__":
    main()
