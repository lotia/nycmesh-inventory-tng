# 0021 — What makes an item's name its own, and how items are named

**Status:** accepted · 2026-11-11 · builds on 0019 (retired names stay
taken)

## Context

An item is found by its name: in search, on the scan page, and on every
list. The old sheet's duplicates were spellings of one thing (`ToughCable`
and `Tough Cable`, `RJ45` and `RJ-45`, `tp link` and `Tp-Link`), and its
real ambiguities were names too loose to tell things apart (`LiteBeam`
for two generations, `mast` for three masts). Labels name an item by its
id, so uniqueness is there to stop a second row for one thing, not to
identify it. Researched with Ali on 2026-11-11 (inventory-tng-8i4.60.22).

## Decision

- **An item's name is its own, ignoring case, spaces and punctuation**
  (Ali, 2026-11-11): `ToughCable`, `Tough cable` and `tough-cable` are one
  name. Letters and digits still tell names apart, so `LiteBeam 5AC Gen1`
  and `LiteBeam 5AC Gen2` are two. Retired items' names stay taken, so one
  can always come back (0019). A unique index on `models.name_key(name)`
  keeps it, and a refused form names the item it clashes with.
- **Manufacturer and part number together, and the old code, stay unique**
  where given, and both stay optional: most of the mesh's supplies have no
  part number that means anything.
- **The category is not part of the key.** Search runs across categories,
  so the same name in two would offer two items for one thing, and moving
  an item to another category could clash with a stranger.
- **An item is what installers treat as interchangeable** (Ali,
  2026-11-11). Two makes of outdoor Cat6 are one item; two generations of
  a radio are two. One item with several makes is inventory-tng-8i4.93's.
- **Naming convention: what it is, then what tells it apart from the items
  nearest it.** Leave out what has a field of its own: the maker
  (Manufacturer), the package or unit (packages), the category. Hardware
  leads with the model as installers say it (`LiteBeam 5AC Gen2`);
  supplies lead with the common noun, then details after commas (`Cat6
  cable, outdoor, shielded`; `RJ45 coupler, indoor`). The add and edit
  forms say so beside Name.
- **A name close to other items' asks first** (inventory-tng-8i4.132).
  Adding an item, or renaming one, lists items whose names are close,
  retired ones too, and saves nothing; sent again with that name, it saves.
  A name changed after the question is asked about again. Close is what
  search finds, or a name held in the new one (`mast` in `Mast,
  telescopic`). Every other refusal comes first, and respelling an item's
  own name asks nothing.

We rejected keying on name and category, for the reasons above, and a
required "variant" field beside the name: most supplies have no variant,
so admins would type half a name in each field.

## Consequences

- A name differing from another only by case, spaces or punctuation is
  refused when an item is added or renamed; respelling an item's own name
  is not.
- Migration 0023 names, by id, any items already clashing so, to rename
  before the index can be made.
- Categories, places and volunteers still compare names ignoring case
  only.
- An item's name needs more than spaces and punctuation, as the database
  classes them: such names alone would all be one name.
- Which characters are spaces and punctuation, and how case folds, is the
  database's locale's. Under a C locale, as CloudNativePG makes by default,
  that is ASCII's: letters such as `â` are kept, but `Â` and `â` are not
  folded together.
