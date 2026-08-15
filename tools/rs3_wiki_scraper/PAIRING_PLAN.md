# RS3 asset-pair reference collector

The reference pipeline should prefer **explicit Wiki image taxonomy + semantic pairs** over broad skill-page crawling.

## Production profile

### Woodcutting

- Resource/world asset: `Category:Tree images`
- Yield/item asset: `Category:Log images`
- Pairing examples:
  - `Acadia tree.png` ↔ `Acadia logs.png`
  - `Magic tree.png` ↔ `Magic logs.png`
  - `Arctic pine.png` ↔ `Arctic pine logs.png`
- Known many-to-one exception: regular Tree, Dead tree, and Evergreen can all map to the basic `Logs.png` item.

The collector downloads **only matched canonical pairs**. Everything else in either image category remains metadata-only in unmatched audit lists.

## Next profiles to add

### Mining: rock/node ↔ ore

Use the same architecture when the exact Wiki categories are verified. This is analogous to tree ↔ logs: a world resource node paired with the inventory item it yields.

Expected normalization concept:

- `Copper rock...` / canonical copper mining-node render ↔ `Copper ore.png`
- `Iron rock...` ↔ `Iron ore.png`

Do not assume ore ↔ bar as a filename pair. Smelting has recipe relationships such as copper + tin → bronze, so that needs an explicit recipe-relation manifest rather than same-name pairing.

### Cooking: raw ↔ cooked

The Wiki already categorizes raw food images. Add the cooked counterpart after confirming the exact image category used by the Wiki.

Expected normalization concept:

- `Raw lobster.png` ↔ `Lobster.png`
- `Raw swordfish.png` ↔ `Swordfish.png`

This is prefix-based pairing (`raw ` removed on the left) rather than suffix-based pairing.

## General pairing rules

Use automatic category/name pairing when the relationship is naturally 1:1 or simple 1:many:

- world resource ↔ gathered item
- raw ↔ cooked
- unfinished ↔ finished, when names map cleanly
- charged ↔ uncharged, when names map cleanly

Use explicit relationship tables instead of filename guessing for:

- many-input recipes
- one input producing multiple unrelated outputs
- upgrade trees with branching outputs
- transformations whose names are not semantically parallel

The goal is not to scrape every image for a skill. The goal is to build a small, auditable reference corpus containing the asset relationships the game actually needs.