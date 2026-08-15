#!/usr/bin/env python3
"""Build a metadata-only census of canonical RS3 skilling art references.

Sources:
- Bucket:recipe production_json for exact material/output image filenames
- Bucket:resource_locations for gatherable world-resource names by skill

Nothing is downloaded here. The goal is to measure the corpus before creating it.
"""
from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

API = "https://runescape.wiki/api.php"
UA = "BBXAI-RS3-Reference-Census/1.0 (personal research/reference)"
BATCH = 750


def api(**params):
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def bucket_query(query: str):
    data = api(action="bucket", query=query)
    rows = data.get("bucket") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected Bucket response: {type(data).__name__} keys={list(data) if isinstance(data, dict) else None}")
    return rows


def bucket_all(name: str, fields: list[str], batch: int = BATCH, max_rows: int = 100_000):
    rows = []
    offset = 0
    select = ",".join(repr(f) for f in fields)
    while offset < max_rows:
        q = f"bucket('{name}').select({select}).limit({batch}).offset({offset}).orderBy('page_name','asc').run()"
        part = bucket_query(q)
        rows.extend(part)
        print(f"  {name}: {len(rows)} rows", flush=True)
        if len(part) < batch:
            break
        offset += batch
        time.sleep(0.08)
    return rows


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def norm_image(v):
    if not v:
        return None
    s = str(v).strip()
    if s.lower().startswith("file:"):
        s = s[5:]
    return s or None


def add_entity(entities, name, image, role, skills, source_page):
    if not name and not image:
        return
    name = str(name or "").strip()
    image = norm_image(image)
    key = (name.casefold(), image.casefold() if image else "")
    e = entities.setdefault(key, {
        "name": name,
        "image": image,
        "roles": set(),
        "skills": set(),
        "source_pages": set(),
    })
    e["roles"].add(role)
    e["skills"].update(x for x in skills if x)
    if source_page:
        e["source_pages"].add(source_page)


def main():
    out = Path("rs3_reference_census")
    out.mkdir(exist_ok=True)

    print("Querying recipes...", flush=True)
    recipes = bucket_all("recipe", [
        "page_name", "page_name_sub", "uses_skill", "uses_material",
        "production_output", "uses_tool", "uses_facility", "source_template",
        "production_json",
    ])

    entities = {}
    tools = defaultdict(set)
    facilities = defaultdict(set)
    recipe_rows_by_skill = defaultdict(int)
    recipe_images_by_skill = defaultdict(set)
    parse_failures = []
    no_skill_rows = 0
    recipe_with_outputs = 0

    for row in recipes:
        page = row.get("page_name") or ""
        raw = row.get("production_json")
        if not raw:
            continue
        try:
            prod = json.loads(raw)
        except Exception as exc:
            parse_failures.append({"page": page, "error": repr(exc)})
            continue

        skills = []
        for s in as_list(prod.get("skills")):
            if isinstance(s, dict) and s.get("name"):
                skills.append(str(s["name"]))
        if not skills:
            skills.extend(str(x) for x in as_list(row.get("uses_skill")) if x)
        skills = sorted(set(skills))
        if not skills:
            no_skill_rows += 1
        for skill in skills:
            recipe_rows_by_skill[skill] += 1

        outputs = as_list(prod.get("outputs"))
        materials = as_list(prod.get("materials"))
        if outputs:
            recipe_with_outputs += 1

        for mat in materials:
            if not isinstance(mat, dict):
                continue
            name = mat.get("name") or mat.get("page")
            image = mat.get("image")
            add_entity(entities, name, image, "material", skills, page)
            if image:
                for skill in skills:
                    recipe_images_by_skill[skill].add(norm_image(image))

        for product in outputs:
            if not isinstance(product, dict):
                continue
            name = product.get("name") or product.get("page")
            image = product.get("image")
            add_entity(entities, name, image, "output", skills, page)
            if image:
                for skill in skills:
                    recipe_images_by_skill[skill].add(norm_image(image))

        # Recipe JSON is not fully uniform; tools may be strings, lists, or dicts.
        tool_values = prod.get("tool") if "tool" in prod else prod.get("tools")
        for tool in as_list(tool_values) + as_list(row.get("uses_tool")):
            if isinstance(tool, dict):
                name = tool.get("name") or tool.get("page")
                image = tool.get("image")
                if name:
                    for skill in skills or ["(unclassified)"]:
                        tools[str(name)].add(skill)
                add_entity(entities, name, image, "tool", skills, page)
            elif tool:
                for skill in skills or ["(unclassified)"]:
                    tools[str(tool)].add(skill)

        facility_values = prod.get("facility") if "facility" in prod else prod.get("facilities")
        for facility in as_list(facility_values) + as_list(row.get("uses_facility")):
            if isinstance(facility, dict):
                name = facility.get("name") or facility.get("page")
            else:
                name = facility
            if name:
                for skill in skills or ["(unclassified)"]:
                    facilities[str(name)].add(skill)

    print("Querying resource locations...", flush=True)
    resource_rows = bucket_all("resource_locations", [
        "page_name", "resource", "skill", "resource_location_type", "count", "requirements"
    ])
    resources = {}
    for row in resource_rows:
        resource = str(row.get("resource") or "").strip()
        skill = str(row.get("skill") or "").strip()
        if not resource:
            continue
        key = (skill.casefold(), resource.casefold(), str(row.get("resource_location_type") or "").casefold())
        r = resources.setdefault(key, {
            "skill": skill,
            "resource": resource,
            "type": row.get("resource_location_type") or "",
            "locations": set(),
            "count": 0,
        })
        if row.get("page_name"):
            r["locations"].add(str(row["page_name"]))
        try:
            r["count"] += int(row.get("count") or 0)
        except Exception:
            pass

    explicit_images = sorted({e["image"] for e in entities.values() if e.get("image")})
    names_without_images = sorted({e["name"] for e in entities.values() if e.get("name") and not e.get("image")})

    # CSV: exact recipe-derived art entities.
    with (out / "recipe_entities.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "image", "roles", "skills", "source_page_count"])
        for e in sorted(entities.values(), key=lambda x: (x["name"].casefold(), (x["image"] or "").casefold())):
            w.writerow([
                e["name"], e["image"] or "", " | ".join(sorted(e["roles"])),
                " | ".join(sorted(e["skills"])), len(e["source_pages"]),
            ])

    with (out / "resources.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["skill", "resource", "type", "location_count", "total_instances"])
        for r in sorted(resources.values(), key=lambda x: (x["skill"].casefold(), x["resource"].casefold())):
            w.writerow([r["skill"], r["resource"], r["type"], len(r["locations"]), r["count"]])

    with (out / "tools.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["tool", "skills"])
        for name, sk in sorted(tools.items(), key=lambda x: x[0].casefold()):
            w.writerow([name, " | ".join(sorted(sk))])

    with (out / "facilities.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["facility", "skills"])
        for name, sk in sorted(facilities.items(), key=lambda x: x[0].casefold()):
            w.writerow([name, " | ".join(sorted(sk))])

    summary = {
        "recipe_rows": len(recipes),
        "recipe_rows_with_outputs": recipe_with_outputs,
        "recipe_rows_without_skill": no_skill_rows,
        "recipe_parse_failures": len(parse_failures),
        "unique_recipe_entities": len(entities),
        "unique_explicit_recipe_images": len(explicit_images),
        "recipe_entities_without_explicit_image": len(names_without_images),
        "unique_tools": len(tools),
        "unique_facilities": len(facilities),
        "resource_location_rows": len(resource_rows),
        "unique_resources": len(resources),
        "recipe_rows_by_skill": dict(sorted(recipe_rows_by_skill.items())),
        "recipe_image_counts_by_skill": {k: len(v) for k, v in sorted(recipe_images_by_skill.items())},
        "sample_unresolved_entity_names": names_without_images[:100],
        "parse_failures": parse_failures[:50],
    }
    (out / "census.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# RS3 canonical reference census",
        "",
        f"- Recipe rows: **{summary['recipe_rows']}**",
        f"- Recipe rows with outputs: **{summary['recipe_rows_with_outputs']}**",
        f"- Unique material/output/tool entities seen in recipe JSON: **{summary['unique_recipe_entities']}**",
        f"- Unique exact image filenames explicitly supplied by recipe JSON: **{summary['unique_explicit_recipe_images']}**",
        f"- Recipe entities lacking an explicit image filename: **{summary['recipe_entities_without_explicit_image']}**",
        f"- Unique tools: **{summary['unique_tools']}**",
        f"- Unique facilities: **{summary['unique_facilities']}**",
        f"- Resource-location rows: **{summary['resource_location_rows']}**",
        f"- Unique skill/resource/type combinations: **{summary['unique_resources']}**",
        "",
        "## Recipe image counts by skill",
    ]
    for skill, count in summary["recipe_image_counts_by_skill"].items():
        lines.append(f"- {skill}: {count}")
    (out / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("Census complete", flush=True)


if __name__ == "__main__":
    main()
