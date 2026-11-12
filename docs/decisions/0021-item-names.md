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
- A package's name is compared the same way, among its item's packages,
  retired ones too (0019; migration 0030 names any clashing by id).
  Categories, places and volunteers still compare names ignoring case
  only.
- An item's name needs more than spaces and punctuation, as the database
  classes them: such names alone would all be one name.
- Which characters are spaces and punctuation, and how case folds, is the
  database's locale's. Under a C locale, as CloudNativePG makes by default,
  that is ASCII's: letters such as `â` are kept, but `Â` and `â` are not
  folded together.

## Amendment: a decimal point tells names apart (2026-11-12)

A decimal point between digits is part of a name (Ali, 2026-11-12,
inventory-tng-0pm): `Spool 305 m` and `Spool 30.5 m` are two names, for
items and for their packages, and so are `v1.2` and `v12`. Only the point
counts, as US number conventions have it: a comma is punctuation
wherever it is, so `Box 1,000 ft` and `Box 1000 ft` are one name. A point
anywhere else is punctuation as before: `1. 5`, `.5` and `5.` lose it,
and `3.0.5` keeps both. `models.name_key` and migration 0031, which makes
the indexes again, say so in one expression, and a test checks they
agree.

**Limitation: where a comma is the decimal mark, this rule is wrong.**
`Spool 30,5 m` clashes with `Spool 305 m`, so the save is refused and the
admin has to type `30.5`. And `Spool 30.5 m` and `Spool 30,5 m`, or `1.000`
and `1000`, are not caught as one name (the question about close names
may still ask). No one fixed rule is right in both conventions. The app
is English-only and follows US conventions today: inventory-tng-g6u
collects what internationalisation would change, inventory-tng-g6u.1 is
this limitation and the options for it, and inventory-tng-g6u.2 is the
research into a fully locale-aware app.

Rejected: keeping a comma between digits too, which missed `1,000` and
`1000`, and treating a point and a comma alike, which misses thousands
separators.

## Amendment: searches ignore accents (2026-11-12)

Uniqueness still keeps accented letters apart, but searches do not (Ali,
2026-11-12, inventory-tng-8i4.135): many keyboards make accents hard to
type, so `cafe` finds `Café` and `lodz` finds `Łódź`, and the reverse.
Each searchable table keeps what is searched folded to ASCII, by the
anyascii library (`models.fold`), which also spells `ø`, `ł`, `æ` and `ß`,
as decomposing alone would not: items' `search_text` and `name_folded`,
and volunteers', places' and packages' `search_text`. The app writes them
as each row is added or changed (`models.Searchable`, before each flush),
never PostgreSQL (0016), and what is typed is folded alike before it is
compared. Items' and volunteers' are indexed with `pg_trgm`. A release of
anyascii that spells a character otherwise needs a migration to fold the
rows again.

The scan page has no anyascii, which would weigh more than the rest of its
scripts (Ali, 2026-11-12). The catalogue sends each item's details as
spelt and its folded text, with its category's (`search_text`). The page
looks for each word typed, ignoring case, as typed and without what the
browser's own decomposing (`normalize("NFD")`) takes off, in both, so
`cafe`, `Café` and `cafè` all find `Café`. It shows only the details as
spelt, never the folded text. Accepted gap: a typed letter decomposing
leaves alone (`ø`, `ł`, `æ`, `ß`) finds nothing unless the whole word is
typed as the name spells it.

Rejected: `unaccent()` at search time, which no index serves without a
function of our own wrapping it (0016), so each search reads the whole
table; and decomposing alone, for what it misses.
