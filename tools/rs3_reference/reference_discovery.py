#!/usr/bin/env python3
"""Probe RuneScape Wiki structured data and image taxonomy without bulk downloads.

Metadata-only discovery for building a canonical RS3 art reference corpus.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://runescape.wiki/api.php"
UA = "BBXAI-RS3-Reference-Discovery/1.1 (personal research/reference)"

BUCKET_PAGES = [
    "Bucket:Infobox item",
    "Bucket:Recipe",
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

SAMPLE_BUCKET_QUERIES = {
    "recipe": "bucket('recipe').select('page_name','page_name_sub','uses_skill','uses_material','production_output','uses_tool','production_json').limit(3).run()",
    "items": "bucket('infobox_item').select('page_name','page_name_sub','item_name','image','item_id').limit(3).run()",
    "scenery": "bucket('infobox_scenery').select('page_name','page_name_sub','object_name','image','object_id','options').limit(3).run()",
    "resource_locations": "bucket('resource_locations').select('page_name','resource','skill','resource_location_type','count').limit(3).run()",
}


def api(**params):
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def bucket_api(query: str):
    return api(action="bucket", query=query)


def bucket_definition(title: str):
    data = api(
        action="query", prop="revisions", titles=title,
        rvprop="content|timestamp", rvslots="main", rvlimit="1",
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
            pass
    return {"title": title, "missing": False, "timestamp": timestamp, "raw": content, "parsed": parsed}


def search_categories(term: str):
    data = api(action="query", list="search", srnamespace="14", srsearch=f'intitle:"{term}"', srlimit="20")
    return [x.get("title") for x in data.get("query", {}).get("search", [])]


def category_info(titles):
    if not titles:
        return []
    data = api(action="query", prop="categoryinfo", titles="|".join(titles[:50]))
    return [
        {"title": p.get("title"), "missing": bool(p.get("missing")), "categoryinfo": p.get("categoryinfo")}
        for p in data.get("query", {}).get("pages", [])
    ]


def main():
    out = Path("rs3_reference_discovery")
    out.mkdir(exist_ok=True)
    report = {
        "api": API,
        "bucket_paraminfo": api(action="paraminfo", modules="bucket"),
        "bucket_definitions": {},
        "bucket_samples": {},
        "category_searches": {},
        "category_info": {},
    }

    print("Reading Bucket definitions...", flush=True)
    for title in BUCKET_PAGES:
        print(" ", title, flush=True)
        report["bucket_definitions"][title] = bucket_definition(title)

    print("Testing Bucket API output...", flush=True)
    for name, query in SAMPLE_BUCKET_QUERIES.items():
        try:
            sample = bucket_api(query)
            report["bucket_samples"][name] = sample
            print(f"  {name}: OK; top-level keys={list(sample) if isinstance(sample, dict) else type(sample).__name__}", flush=True)
        except Exception as exc:
            report["bucket_samples"][name] = {"error": repr(exc), "query": query}
            print(f"  {name}: FAILED {exc}", flush=True)

    print("Discovering image categories...", flush=True)
    for term in CATEGORY_TERMS:
        hits = search_categories(term)
        report["category_searches"][term] = hits
        report["category_info"][term] = category_info(hits[:10])
        print(f"  {term}: {len(hits)} hits", flush=True)

    (out / "discovery.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = ["# RS3 reference discovery", ""]
    for title, d in report["bucket_definitions"].items():
        lines.append(f"## {title}")
        if d.get("missing"):
            lines.append("MISSING")
        elif isinstance(d.get("parsed"), dict):
            lines.append("Fields: " + ", ".join(d["parsed"].keys()))
        else:
            lines.append("Found, but definition was not parsed as JSON")
        lines.append("")

    lines.append("# Bucket API samples")
    for name, sample in report["bucket_samples"].items():
        if isinstance(sample, dict):
            lines.append(f"- {name}: top-level keys: {', '.join(sample.keys())}")
        else:
            lines.append(f"- {name}: {type(sample).__name__}")

    lines.append("")
    lines.append("# Image category candidates")
    for term, hits in report["category_searches"].items():
        lines.append(f"- {term}: " + (", ".join(hits[:8]) if hits else "none"))

    (out / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("Discovery complete", flush=True)


if __name__ == "__main__":
    main()
