# RS3 canon reference pipeline

Purpose: collect the **smallest useful set of current RuneScape 3 visual references** needed to guide original pixel-art production. This is not a general RuneScape Wiki mirror.

## Production outputs

### Canonical skilling inventory corpus

Workflow: `.github/workflows/rs3-reference-corpus.yml`

Source of truth: the RuneScape Wiki structured `recipe` Bucket. The recipe records supply exact current material/output image filenames, so the collector downloads only inventory art that participates in skill-bearing recipes and writes a normalized recipe graph beside it.

Output includes:
- canonical inventory PNG references
- `inventory_manifest.csv`
- `recipes.jsonl`
- missing/failure audit files

This naturally covers broad skilling families such as ores and bars, herbs and potions, raw and cooked food, logs and processed products, runes and essence, gems and jewellery, smithing/crafting outputs, and other recipe-connected inventory items.

### Compact world/support corpus

Workflow: `.github/workflows/rs3-world-reference-corpus.yml`

Uses targeted structured/category relationships instead of broad page crawling:
- Woodcutting: `Tree images` ↔ `Log images` pairing
- Mining: structured Mining resources ↔ scenery with the `Mine` option
- recipe tools and resolvable facilities
- canonical skill icons

World/scenery references are downloaded as Wiki thumbnails capped at 512 px. The original filenames/URLs remain in the manifest; 512 px is more than enough for recognition-driven pixel-art conversion and avoids archiving unnecessarily large source renders.

`relationships.csv` is separate from the unique-image manifest so one image may legitimately represent multiple resource/facility relationships without duplicating the file.

## Helper scripts

- `reference_image_plan.py`: shared Bucket/image-title normalization and MediaWiki image resolution helpers.
- `reference_support_plan.py`: derives concrete tools and useful facility references from recipe metadata.
- `reference_mining_nodes.py`: derives Mining node art from structured resource data.
- `reference_recipe_corpus.py`: builds the inventory/recipe corpus.
- `reference_world_corpus.py`: builds the compact world/support corpus.
- `../rs3_wiki_scraper/rs3_wiki_pairs_v2.py`: narrow semantic image-pair collector, currently tree ↔ logs.

## Rules

1. **Discover game entities first; resolve images second.** Never scrape every image under a skill page tree.
2. Prefer exact structured Wiki filenames when available.
3. Use category pairing only for simple semantic relationships such as tree ↔ logs.
4. Use structured recipe relationships for many-input transformations such as ore → bars/items.
5. Preserve ambiguous or broken cases in audit/exception files instead of guessing.
6. Add skill-specific world-art collectors only when that skill actually needs them.

Known exceptional/broken current Wiki image references are documented in `REFERENCE_EXCEPTIONS.json`.
