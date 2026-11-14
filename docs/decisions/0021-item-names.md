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
  can always come back (0019). A unique index on each item's key keeps it
  (`names.py`; Amendment, 2026-11-14), and a refused form names the item it
  clashes with.
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
- An item's or a package's name needs a letter or a digit, as Unicode
  classes them (Amendment, 2026-11-14): spaces, punctuation and signs
  alone, such as `- ?` or `–`, tell no name from another.
- Which characters are spaces and punctuation, and how case folds, is
  ASCII's, as a C locale has it, whatever the database's locale (Amendment,
  2026-11-14): letters such as `â` are kept, but `Â` and `â` are not
  folded together, and signs beyond ASCII, such as `–`, are kept.

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
and volunteers', places' and packages' `search_text`. Compatibility
characters are first made their canonical ones
(`unicodedata.normalize("NFKC")`, Ali, 2026-11-12, inventory-tng-gfe),
so the micro sign in `µF` folds as the Greek `μF` does, the Ohm sign as
`Ω` and `℃` as `°C`, whichever a keyboard gives. Both `µF` and `μF` fold
to `mF`, so `uF` typed finds neither (inventory-tng-8g2). The app writes
them as each row is added or changed (`models.Searchable`, before each
flush), never PostgreSQL (0016), and what is typed is folded alike before
it is compared. Items' and volunteers' are indexed with `pg_trgm`.

Each of those rows records what folded it, in `folded_with`, such as
`anyascii 0.3.3, Unicode 17.0.0` (Ali, 2026-11-12, inventory-tng-7u4;
the Unicode, whose NFKC it is, since inventory-tng-gfe), written with its
folded text; migration 0033 folded the rows there were again, to fill it.
A release of anyascii, or a Python with a newer Unicode, that spells a
character otherwise would leave rows folded the old way while what is
typed is folded the new. So `tests/fold_reference.tsv` keeps what `fold`
makes of every character in the blocks a name is likely to use, and some
whole strings, with the versions of anyascii and of Python's Unicode, and
a test fails with the difference when an upgrade changes either. Then
decide whether to take it; if so, write the file again and, if a spelling
changed, fold again in a migration the rows whose `folded_with` names the
old version (CONTRIBUTING.md, Dependency updates).

The scan page has no anyascii, which would weigh more than the rest of its
scripts (Ali, 2026-11-12). The catalogue sends each item's details as
spelt and its folded text, with its category's (`search_text`). The page
strips both, once per catalogue, and each word typed alike: in lower case,
without what the browser's own compatibility decomposing
(`normalize("NFKD")`) takes off (Ali, 2026-11-12, inventory-tng-ffn). So
`cafe`, `Café` and `cafè` all find `Café`, `łodz` finds `Łódź`, and so do
a fullwidth `ｃａｆｅ`, the ligature `ﬁ` and the micro sign in `µF`, as an
IME or a paste may give them. It shows only the details as spelt, never
the folded or stripped text. Accepted gap: a word typed half as spelt
and half as the server folds it finds nothing, such as `lø` or `ło` for
`Łø`, as `ø` and `ł` do not decompose.

Rejected: `unaccent()` at search time, which no index serves without a
function of our own wrapping it (0016), so each search reads the whole
table; and decomposing alone, for what it misses.

## Amendment: names are stored in canonical spelling (2026-11-14)

A name is stored in one spelling of each character, whichever a keyboard
or a paste gave (Ali, 2026-11-12, inventory-tng-c0c): Unicode's NFC, so
the Ohm, Kelvin and Angstrom signs are `Ω`, `K` and `Å` and an accent
typed apart is one letter with it; and the micro sign `µ` as the Greek
`μ`, and `℃` and `℉` as `°C` and `°F`, as the SI Brochure and ISO 80000
write them. Case and every other character stay as typed: not NFKC, which
would also show `m²` as `m2` and `ﬁ` as `fi`. So `10µF` typed is stored
`10μF`, and clashes with an item already so named.

It holds for every name an administrator types: items', packages',
categories', places', volunteers' and adjustment reasons'
(`models.CanonicalName`, as each is set, whoever sets it). The item form
compares a name in that spelling, so the clash names the other item.
What searches compare does not change: `fold` makes the same of either
spelling. Migration 0035 rewrote the names there were in place, and
names by id any that would clash once rewritten, to rename first.

Matching units whatever their spelling is inventory-tng-c0c's next part.

## Amendment: searches read units after a number (2026-11-14)

A unit right after a number, with or without a space, is searched in one
spelling, whichever was typed (Ali, 2026-11-12 and 2026-11-14,
inventory-tng-c0c): its symbol in ASCII, joined to the number. So `10uF`,
`10 µF`, `10μF` and `10 ΜF` are all searched as `10uF` and find each
other (inventory-tng-8g2); `85 degC`, `85°C`, `85℃`, `85 Cel` and
`85 deg C` as `85degC`; `4.7 kΩ` and `4.7kohm` as `4.7kOhm`. Case does
not matter, as in every search, so `mF` and `MF` are one, as before.

`inventory_tng/units.py` holds the table: one row per unit this
inventory's names use, of its kinds of quantity (length, frequency, data
rate, power, power as a level in dBm, antenna gain in dBi, voltage,
current, temperature, capacitance, resistance, mass, volume), with its
symbol, its kind, whether SI prefixes go on it, and the spellings it is
also read by. Every UCUM code of a unit is one, so the UCUM form always
works; a test checks them against UCUM 2.2's (`tests/ucum_codes.tsv`).
Prefixed forms are derived from the prefixes, all the SI's, micro also
`μ` and `µ`, never rows. Celsius is `degC` (Ali: the convention). Each kind
keeps one plausible size, for telling `305M` (metres) from megametres
later.

Only where a number comes first: alone, `Cel` finds only text holding
`Cel`, as `Cell` and `m` would be misread. A unit alone is read only if
spelt with a sign no word is (`°`, `μ`, `µ`, `Ω`), so `μF`, `°C` and `kΩ`
still find `10μF`, `85 °C` and `4.7 kΩ`, as they did before units were
read (added in review, 2026-11-14). So a Greek word is folded as
letters, as before, whatever its case: `μέρα` finds `ΜΕΡΑ`, and the
server and the scan page read a capital `Μ` alike, as micro after a number
and as a letter elsewhere (inventory-tng-5d2). The cost: a unit
typed alone in an ASCII spelling other than its symbol, such as `Mbps` for
`100Mbit/s`, finds only what search finds close.

`models.fold` reads units in the decomposed text (NFKD) before NFKC and
anyascii, and folds each searched field alone, so a field's number never
reads the next field as a unit. The scan page reads them in the same
table (`frontend/src/units.json`, which the server writes) the same way
(`frontend/src/units.ts`), so both spell a search alike; both are tested
against `frontend/src/units-vectors.json`. Each folded row's
`folded_with` names the table's version too, and migration 0036 folded
the rows there were again; a later table comes with its own such
migration, which a test asks for.

Rejected: storing every spelling of a unit in the search text, which
grows it and lets a part of one spelling match the wrong unit; and a
character table spelling `μ` as `u` everywhere, which misspells Greek
words. pint, a units library, was not needed for a table this size.

## Amendment: each name's key is a column the app writes (2026-11-14)

An item's and a package's key, the name as uniqueness compares it, is kept
in a column, `name_key`, which the app writes as the name is set
(`models.KeyedName`), as it writes what searches compare (Ali, 2026-11-12,
inventory-tng-6sb). A CHECK constraint holds it to the name, so a key
written wrong, by SQL or a bug, is refused, and plain unique indexes on
`name_key`, and on `item_id` and `name_key`, keep names unique. A CHECK is
a declaration, so this is within 0016, whose exception for an index on a
regular expression is gone. `inventory_tng/names.py` says the rule once, as
a pattern Python and PostgreSQL read alike, and a test checks the two
agree on every character. Migration 0037 added and filled the columns.

The key is collated `"C"`, whatever the database's locale, so the app can
write it as PostgreSQL checks it: only ASCII's spaces and punctuation are
left out, and only its letters lowered. Production, on CloudNativePG's
default C locale, compares names as it did. The development database,
made with `en_US.utf8`, now compares them as production does: `Câble` and
`CÂBLE`, or an en dash and a hyphen, are two names there, where they were
one. Accents stay apart in uniqueness, as above. A name still needs a
letter or a digit, Unicode's, which the app checks, so a name of an en
dash alone is refused though its key keeps it.

Rejected: a key in PostgreSQL's own locale, which Python cannot match, so
the CHECK would refuse names the app keyed, and a function or trigger
computing it (0016).

## Amendment: a name spells each unit by its symbol (2026-11-14)

An item's or a package's name, as an administrator types it on its form,
is stored with each unit right after a number spelt by its symbol and
joined to the number, whatever the spelling or spacing typed (Ali,
2026-11-12, inventory-tng-c0c.3): `Spool 305 m` is stored `Spool 305m`,
`10 uF` and `10 µF` as `10μF`, `4.7 kohm` as `4.7kΩ`, `85 deg C` as
`85°C`, `2 m3` as `2m³` and `100 feet` as `100ft`. The symbol is the one
the SI Brochure writes, in canonical spelling (`μ`, `Ω`, `°C`), not the
ASCII one searches compare. Only units in the table are read, and only
after a number; the rest of the name is as typed.

Case is read loosely, but only as a size this inventory may hold: each
kind's plausible range in `units.KINDS`, a level in decibels as typed. So
`305M` is `305m`, as 305 megametres is no likely length, `10MF` is
`10mF`, and `100 mhz` is `100MHz`. A token with no likely size is left as
typed: `4 K` would be -269 °C. Where two readings are likely and neither
is spelt as typed, the form asks which, with both as choices, and saves
nothing until answered: `5 MG` may be megagrams or milligrams. Spelt as
one of them, as `5 mg` or `10 MΩ`, it is taken as typed.

Under the name, the form says how each unit was read (`305m: metres`), and
which were not read as units, with no extra step (Ali, 2026-11-12). The
hint is the server's, as the page is shown; the page reads nothing as one
types, which would need the plausible sizes on the page too.

Names already stored are not respelt; saving one respells it. Names a
seed or an import writes are not read here. Settled readings kept as
rules, so a reading such as `10G` (read as grams) can be overruled, are
inventory-tng-c0c.4's.

## Amendment: items have aliases, taken as names are (2026-11-14)

An item may have aliases: other names it is found by, such as one it was
known by (Ali, 2026-11-12, inventory-tng-c0c, Q6-A; built in
inventory-tng-c0c.6). An administrator adds and removes them on the item's
page, typed and stored as a name is: in canonical spelling, each unit
after a number spelt by its symbol, asked about where its case leaves two
readings. Searches find an item by its aliases, as by its name: each is
folded alone into the item's `search_text`, which the admin list and the
scan page search, and the catalogue sends them as spelt too
(`aliases`), so the scan page matches them as it does the name.

Names and aliases are one namespace, compared by their keys
(`names.name_key`): no item is named what another item is named or what
any item, itself included, has as an alias; no alias is any item's name,
retired items' too, or another current alias. Each alias is a row of
`item_alias`, keyed and held to its name by the same CHECK as items, with
a unique index on the keys of current aliases. That no alias is an item's
name is the app's to check (`items.py`), holding the catalogue lock, which
every write to either table takes (0016): one index cannot span two
tables, and a table holding every name and alias, which items' rows would
point to, would need a constraint checked at commit and a second row for
every item. A refused form names the item whose name or alias it is.

A removed alias is retired, so its history stays, and its name is free
again. A retired item's aliases stay taken, as its name does (0019), and
are read only until it is brought back.

Merging two items, which would keep the merged one's name and aliases as
the survivor's aliases, does not exist yet.
