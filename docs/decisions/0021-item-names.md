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
  folded together, and signs beyond ASCII, such as `±`, are kept; a space,
  quote or dash that looks like ASCII's, such as `–`, is read as ASCII's
  (Amendment, 2026-11-15).

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
`CÂBLE` are two names there, where they were one. An en dash and a hyphen
were two names too, until look-alikes were read as ASCII's (2026-11-15,
below). Accents stay apart in uniqueness, as above. A name still needs a
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
`85°C` and `2 m3` as `2m³`; a unit's word, such as `feet`, never spells
a name (amended 2026-11-15, below). The symbol is the one
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
known by (Ali, 2026-11-12, inventory-tng-c0c; built in
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

## Amendment: searches keep what was typed too (2026-11-15)

Search keeps each searched field folded twice where the two differ: as
typed, its units as they are, and with its units respelt, and matches what
is typed both ways (Ali, 2026-11-15, inventory-tng-c0c.2). So every
search that found something before units were read still does, and the
unit matches above are added: `feet` finds `Cable 100 feet`, as `100 ft`
does; `48 V` finds `48 VDC`; `2.5 mm` finds `2.5 mm²`; and on the scan
page, a unit half typed, such as `10 μ`, finds `10μF` as one types.
`models.folds` gives the two, `models.held` what `search_text` keeps of a
field, aliases and the category the catalogue sends included, and the scan
page's `cart.bares` does the same; `units-vectors.json` holds both forms
where both sides spell them alike. Migration 0039 folded every row again.
A search mixing the two, such as `100 ft 48 V` for `100 feet 48 VDC`, is
matched group by group as well (below). Two spellings, not every spelling,
so the rejection above stands.

A unit's word (`inch`, `feet`, `volts`, `watt`, `amps` and the like) is read
only by search, never to spell a name (Ali, 2026-11-15, inventory-tng-c0c.2),
and `in` is no unit, as it is a word more often than an inch: an inch is read
by its words and by its UCUM code, `[in_i]`, its symbol in what search
compares. A lone `Ω` or `ω` is read as ohms, as above (Ali, 2026-11-15,
inventory-tng-c0c.2).

## Amendment: look-alike spaces, quotes and dashes clash (2026-11-15)

A name's key reads a space, quote or dash that looks like ASCII's as
ASCII's, before leaving spaces and punctuation out (Ali, 2026-11-15,
inventory-tng-6sb): every Unicode space (a non-breaking one, a thin
one), curly and other apostrophes and quotes, primes, and en and em
dashes, the minus sign and other hyphens. So `Tough cable` pasted with a
non-breaking space, or `Ubiquiti’s` with a curly apostrophe, clashes with
the name typed plainly, and the form names the item that has it. Names
are stored as typed, look-alikes kept.

`names.LOOK_ALIKES` lists them code point by code point, read by Python's
`str.translate` and PostgreSQL's `translate()`, so the CHECK reads the
same list whatever the database's version, where a Unicode category would
follow its version. The agreement test checks the two on every character,
and that the list holds every space Unicode has. Migration 0040 wrote the
CHECKs again and refilled every key, items', packages' and aliases',
naming by id first any names it would make one, an alias and an item's
name included.

A name with no letter or number is refused with "A name needs at least one
letter or number." (Ali, 2026-11-15, inventory-tng-6sb).

## Amendment: a rename keeps the old name as an alias (2026-11-15)

Renaming an item keeps its old name as an alias, so a search for it still
finds the item; the administrator may remove it (Ali, 2026-11-15,
inventory-tng-c0c.6). Renaming an item to one of its own aliases
swaps the two: the alias is removed and the old name becomes one, where
before the rename was refused. A name only respelt, its key the same,
keeps nothing. Both are part of the save, so a save refused for any field
keeps neither. The old name is always free as an alias: it was the item's
name, so no other item's name and no current alias. Changing aliases
still changes nothing the item's form shows, so it moves no version.

The question whether a new name is a different item also lists items
whose current aliases are close, saying which alias (`items.similar`):
each item once, by its name where that is as close. Each alias keeps its
name folded (`name_folded`, migration 0041), as an item does.

Merging items is its own decision (inventory-tng-2f1), the merged item's name
then moving to the survivor as an alias (Ali, 2026-11-15, inventory-tng-c0c.6).

## Amendment: reading rules, and names that read alike are one (2026-11-15)

An administrator settles how a number and its unit, as typed, are read
(Ali, 2026-11-12, inventory-tng-c0c.4): a reading rule, one row of
`reading_rule` per token as typed (`305M`, `10G`, `16P`), reads it as a
unit (`305M` as metres, `10G` as gigabits a second) or as no unit (`16P`),
with who settled it and when. A rule is looked up before the unit table
wherever a unit after a number is read: search, on the server and on the
scan page, which the catalogue sends the rules to (`reading_rules`); the
items list's search; and the item forms. The answer to the forms' question
about a unit is kept as a rule, so it is asked once. The Unit readings page
lists the rules, and changes or deletes one; a deleted rule's token is read
by the table again. A token is kept with its number, so `305M` and `100M`
are settled apart, and its case as typed.

Names that read alike once their units are read are one name (Ali,
2026-11-15): `10 uF`, `10 µF` and `10μF`, or `1 Gbps` and `1Gbit/s`, whose
keys differ. Each item, package and alias keeps a second key, `unit_key`:
the key of its name with each unit after a number that is read, by its
rule or the table, spelt by its symbol (`models.unit_key`), unique as
`name_key` is, items' and current aliases' one namespace, which `items.py`
checks, and among an item's packages. A form refuses a name taken so,
naming the item that has it. The app writes the column as the session
flushes, as it writes what searches compare: a CHECK cannot hold it to the
name, as the unit table and the rules are the app's, not PostgreSQL's
(0016). Migration 0042 filled it, naming by id first any names it would
make one. `Spool 305 m` and `Spool 305m` were one name already, by
`name_key`.

A rule changed, added or deleted reads every name with its number again,
its unit key and what search keeps of it (`items.reread`), holding the
catalogue lock; a change that would make two names one is refused, and the
page names them by id, to rename one first.

Rejected: a key in the database from a table of rules, which would need a
function reading the unit table (0016), and keying rules by the unit alone
(`M`), which would read `305M` and `100M` alike.

## Amendment: names are saved as typed, and units read by rules (2026-11-15)

A name is saved as typed, in canonical spelling: a reading changes what
search matches and how names are compared, not the name (Ali, 2026-11-15,
inventory-tng-c0c.3). This replaces respelling each unit by its
symbol (2026-11-14, above), which changed names silently: `SFP+ 10G` was
saved as `SFP+ 10g`, read as grams. A name is respelt only where its
reading rule says so (`respell`, set on the rule's page).

Three layers decide a reading. A network profile ranks kinds where a unit
could be several (`units.PROFILE`): data rate first, then length, power
level and gain, power, voltage, current, frequency, the rest, and mass
last; and a bare `G`, `M` or `K` after a number may be a data rate (`10G`,
10 Gbps). A product list is never read as units (`units.PRODUCTS`:
`Cat6a`, `SFP+`, `5AC`, `Gen2`, `2-in-1` and the like). And reading rules,
seeded once from every name saved: the seeding page tallies each number
and unit no rule settles, with the words near it (`10G` near `SFP+` or
`uplink` is a data rate, `305M` near `spool` a length), proposes a reading
with that evidence, and an administrator accepts them all, changes any
first, or reviews them one by one; one nothing settles needs a choice.
It is run again after an import (inventory-tng-c0c.5).

On the form, under the name: `Read as 10Gbps · data rate. Saved as
typed.`, with "Not right?" offering the other readings and "Not a unit",
which, saved with the name, updates the rule. A number and unit nothing
settles, two readings being likely, is asked once, inline, as the form
saves (`"3M" is new. Is it a unit?`, with `3Mbps · data rate`, `3m ·
length` and `Not a unit: keep "3M"`), only kinds this inventory uses
offered: `24P`, which none reads, is simply no unit. Where one reading
alone is likely, it is taken, and said, as `4 K` is 4 kbps, kelvins being
unlikely. A reading is written by its usual printed symbol: data rates as
`bps`, `Mbps`, `Gbps`, and `m³`. Corrections are made in three
places, the seeding page, the form and the rules page, and each reads every
name with that spelling again. Search still reads a bare `K` as kelvins
where no rule says otherwise: the profile is the forms'.

## Amendment: rules kept by the unit, and search reads as the forms do (2026-11-15)

Ali's choice above, that names are saved as typed and units read by
rules, with the wording Ali gave for it, settled the implement stage's
questions (2026-11-15, inventory-tng-c0c.3 and c0c.4), and Ali added a
seventh the same day. This replaces, above, keeping a rule by its number
and unit (`305M`) and search not reading the profile.

1. A rule is kept by the unit as typed after a number, case kept (`M`,
   `G`, `g`), so one answer reads `10G`, `1G` and `40 G` ("Remembered for
   "M" after a number when the name has no other clue"). `G` never
   settles `g`.
2. A rule is looked up first and always wins. Where none is and a unit
   may be several kinds, the words near it in the name decide
   (`units.CLUES`: `spool`, `cable`, `Cat6` a length; `switch`, `port`,
   `uplink`, `SFP` a data rate; `paste`, `grease`, `weight` grams), then
   the profile. Every reading can be corrected under "Not right?", one
   the words decided too (the hint says them: `by "spool"`), and the
   correction, kept as the rule, then wins over those words in every
   name (Ali, 2026-11-15, answering the review's questions).
3. The profile settles silently where its ranking decides: `G` and `K`
   after a number are Gbps and kbps without asking, grams and kelvins
   ranking below (`Hub 4 K` is 4 kbps). `M`, a data rate or a length, both
   ranked high, is asked where no word near it says. `300 K` is 300
   kbps too, though kelvins are plausible (Ali, 2026-11-15).
4. A `g` after a number is a data rate or grams: never silently grams,
   asked where no word near it says (Ali, the seventh).
5. The hint names the kind (`Read as 10Gbps · data rate. Saved as
   typed.`); the question's choices name the unit (`3m · meters`,
   `10g · grams`), a data rate so (`3Mbps · data rate`).
6. Search, here and on the scan page, reads units as the forms do, the
   profile, the words near each unit, products and rules alike (units
   version 3, migration 0043 folding every row again), so what a form
   reads and what search finds agree. What nothing settles, search reads
   by the table, as before.

A rule change reads again only names with that unit after a number, found
by a regular expression on the name and what is searched of it, so a
common number locks nothing more.

Words the app shows for units are in US spelling, as the app follows US
conventions (inventory-tng-g6u): `meters`, `liters`, `cubic meters`,
`metric tons` (Ali, 2026-11-15). Both spellings are still read, in a
search and as words near a unit (`metre`, `fibre`).

## Amendment: searches match group by group (2026-11-15)

Ali decided on 2026-11-15 (inventory-tng-ist.1, from the research in
inventory-tng-mgx) that a search is matched in groups too, beside the
whole forms above, so `100 ft 48 V` finds `Cable 100 feet, PoE 48 VDC`,
which holds neither form whole.

A group is a number with the unit search reads right after it, its space
kept (`100 ft`), or any other word alone (`PoE`). Each has two forms, as
typed and respelt, the respelt one cut from respelling the whole search,
so reading rules, products and the words near a unit read as they do there
(`spool 305M` is `305m`). `units.groups` gives them, and `units.ts` the
same, both tested against `units-vectors.json`. A search with more than
eight groups (`units.GROUPS_MAX`) is matched whole only.

Both sides find a row holding a whole form, as before, so typos are still
forgiven, or one holding every group, each as typed or respelt, groups
perhaps in different fields. On the server, a row holding a whole form
ranks first, then one holding every group, then one only close:
`100 ft 48 V` lists `PoE cable 100 ft 48 V`, then `Cable 100 feet, PoE 48
VDC`, then `Antenna, 1000 FT, 48A`. The scan page lists what it finds in
catalogue order, as before.

Search also reads `VDC` and `VAC`, in any case, after a number as volts,
and a number before a unit it reads without its thousands separators, so
`1000 ft`, `1,000 ft` and `1 000 ft` (a thin space too) find each other.
Both are search's only: no name is respelt by them, nor its key
read so. Units version 4; migration 0046 folded every row again.

Accepted misreading (review, 2026-11-15): as a plain space groups
thousands (`1 000 ft`), two numbers spaced so are read as one, `Pack 2 100
ft` as `2100ft`. Kept: Ali's example is spaced plainly, a thin space is a
plain one once decomposed, and what was typed is kept beside it, so `100
ft` and `100 feet` still find it. A product's number is never the first
of such a group, so `Cat 6 305 m` is `Cat 6 305m`, and after a number and
a space only commas group, so `Cat 6 1,000 ft` is `Cat 6 1000ft`.

Ali decided on 2026-11-15 (inventory-tng-4i1) that, to search, a comma
right after a lone nought is a decimal point, so `0,500 m` is `0.500 m`
and `0,5 m` `0.5 m`, where it had been read as `500 m`. Every other comma
between digits still groups thousands: `10,500 m` is `10500 m`, and
`0,500,000 m` is left as typed. A lone nought starts a decimal in every
convention, so reading names by locale later need not change this. Names
are not affected: they are shown as typed. The forms judge which unit
such a number has by the same reading, so `0,500 Mg` is megagrams to
both, not milligrams to the form. Units version 5; migration 0048 folded
every row again and read each unit key and name as shown again.

## Amendment: names are shown with each number and unit spaced (2026-11-15)

Ali decided on 2026-11-15 (inventory-tng-ist.3) that a name stays saved
as typed, and beside it each item, package and alias keeps the name as
the app shows it, in its own column, `name_shown` (`models.KeyedName`),
so a later change of mind changes only which is shown, and nothing typed
is lost:

- Each number a unit is read right after, as the forms read it (its rule,
  the words near it, the profile; never a product's), or a unit's word
  as search reads it, is spaced from the unit by a no-break space, the
  unit spelt as typed: `Cable 100ft 48 VDC` is shown `Cable 100 ft 48 VDC`,
  `Cap 10uF 50V` `Cap 10 uF 50 V` and `Spool 305M` `Spool 305 M`. A rule's
  respell still spells the name saved. What is not read as a unit and a
  product (`Cat6a`, `5AC`, `SFP+`) are shown as typed.
- That number is shown without its thousands separators, `Box 1,000 ft`
  as `Box 1000 ft`, and from five digits with a thin no-break space
  (U+202F) between each three, as the SI writes it: `10,000 ft` is
  `10 000 ft`, which never wraps.
- It is shown wherever a name is read: the admin lists and item pages,
  the scan page (the catalogue sends it), labels and notices, save the
  exceptions below. The item form edits the name as typed and says how
  it is shown where that differs (`Shown as Box 10 000 ft.`), as do the
  package and alias forms.

`units.shown_name` makes it. The app writes it as the session flushes,
wherever a name is written, and again when a reading rule changes
(`items.reread`); migration 0047 filled it, and a migration like it fills
it again if the rules change. Uniqueness and search are unchanged: search
keeps the name as typed, and reads a no-break or thin space as a space, so
a name copied from a page finds the item.

Decided in building it (inventory-tng-ist.3, 2026-11-15): only commas
group thousands here, as a plain space may part two numbers (`Pack 2 100
ft`), which search accepts but a name shown must not join (a number
grouped by spaces is shown whole and as typed, as decided below), and a
comma after a lone nought (`0,500 m`) groups none, being perhaps a
decimal comma, so is shown as typed; a number within a word or a code,
right after a letter, digit, `-`, `/`, `.` or `+` (`UF-SM-10G`,
`ES-8-150W`), or whose unit runs on into one by `-` or `/` (`10G-SFP+`,
`48V-to-12V`), is shown as typed; the seeding page shows names as typed,
as evidence of how a unit was typed; and an item's form, its packages'
and its aliases' fields hold the name as typed.

Ali decided on 2026-11-15 (inventory-tng-dfz) that a refusal because
names would read alike, on a form, a rule's page, the seeding page or
`unit_readings load`, names each as typed, not as shown, as it is about
how typed names read (`reading_rules.read_alike_words`).

Ali decided on 2026-11-15 (inventory-tng-tze) that a unit's word
(`feet`, `metres`, `VDC`, `VAC`) is shown as a unit's symbol is: a
no-break space between the number and the word, the number without
thousands separators and grouped from five digits, the word kept as
typed, never respelt as the symbol. So `Box 1,000 feet` is shown
`Box 1000 feet`, `Cable 100feet` `Cable 100 feet`, `PoE 48VDC`
`PoE 48 VDC` and `Spool 10,000 metres` `Spool 10 000 metres`. A number
within a word or a code is shown as typed, as for a symbol. Nothing typed
and no search changes; migration 0048 shows existing names so.

Ali decided on 2026-11-16 (inventory-tng-t78) that a number grouped by
spaces before a unit is shown whole and exactly as typed, and search
keeps reading it as one number. Pack counts written with a space before
the size are realistic, and showing them as one long length would
mislead. So `Box 1 000 feet` is shown `Box 1 000 feet` and still found as
`1000 ft`; `Cable 2 100 ft` (two of 100 ft) is shown as typed, never
`Cable 2100 ft`, and found by `100 ft`, and by `2100 ft` too, an extra
result accepted. As for any number, its unit is spaced from it by a
no-break space, and judged by the number as search reads it, so
`Box 1 000 ft` gains one before `ft`, where it had been shown as typed.
The read-alike check still ignores spaces. Rejected: joining it in the
shown name too, which shows packs as one long cable; and reading it as
typed in search too, which loses `Box 1 000 feet` as `1000 ft` and gains
nothing. Writing packs as `2 x 100 ft` reads correctly either way. Ali
then decided, the same day, that the ordinary spaces within such a number
are shown as no-break spaces, so `Box 1 000 ft` never breaks across a
line. They look the same, and nothing else changes: no digits are joined,
and no-break and thin spaces stay as typed. A pack count such as
`Cable 2 100 ft` is one grouped number as search reads it, so its space
is no-break too. Migration 0048, rewritten in place, shows existing
names so.

Found in review (inventory-tng-uxo6, 2026-11-16): the forms and the
read-alike check judged a unit by the last group of such a number, `000`
in `Box 1 000 M`, so a form never asked about it, and `Cap 1 000 μF` and
`Cap 1000 uF` were two names, though search read both as one. Now search,
the forms, the read-alike check and the shown name all judge a number as
search reads it (`units._searched`): one number, grouped by commas or by
any space, its sign with it, so `-1 000 °C` is minus a thousand, no
plausible temperature, wherever it is read. The read-alike check still
ignores spaces, so those two names are one. Units version 6; migration
0048, rewritten in place, as nothing is deployed (Ali, 2026-11-16), read
each unit key again and folded every row again.

Found in review (inventory-tng-vw0f, 2026-11-16): the forms and the
read-alike check found no unit after a number and a no-break, thin or
other space, so `Cap 1000 μF` with a no-break space and `Cap 1000 uF`
were two names, and a form never asked about `Box 1000 M` spaced so,
though search reads every space as a plain one. Now search, the forms,
the read-alike check and the shown name all look for a unit after a
number in the name with every space (`names.SPACES`) plain, one
character for one, so what a form says is where it was typed. The shown
name puts a no-break space before the unit whatever space was typed
there; within a number grouped by spaces, no-break and thin spaces stay
as typed. Units version 7; migration 0048, rewritten in place again,
reads each unit key and name shown again, naming clashes by id.

Ali decided on 2026-11-16 (inventory-tng-mz31) that every breaking space
within a number grouped by spaces is shown as a no-break one, which
replaces t78's "no-break and thin spaces stay as typed", as a thin space
still let such a number break across a line. In the shown name, within
such a number: the space (U+0020) becomes a no-break space (U+00A0), as
before; thin (U+2009), hair (U+200A), punctuation (U+2008), six-per-em
(U+2006) and medium mathematical (U+205F) spaces become narrow no-break
spaces (U+202F), as close to their width as a no-break space comes; en
and em quads, en, em, three- and four-per-em spaces (U+2000 to U+2005),
ideographic (U+3000) and Ogham (U+1680) spaces become no-break spaces.
No-break (U+00A0), narrow no-break (U+202F) and figure (U+2007) spaces
stay as typed. The space before the unit is a no-break space, as before.
The name is kept as typed; search, the forms and the read-alike check do
not change. Rejected: every breaking space a no-break space (U+00A0).
The mapping is `names.NO_BREAK`, beside `names.SPACES`. The units
version stays 7, as no reading changes and the scan page has no shown
name of its own; migration 0048, which already fills each name shown as
`units.shown_name` does, shows existing names so.

## Amendment: the import's readings file (2026-11-14)

An import starts with no reading rules, and its readings are settled
before it writes a name (Ali, 2026-11-12, inventory-tng-c0c.5). An
operator command, `unit_readings`, writes the dry run's readings to a
file: one row per unit as typed after a number that the seeding page
would propose, with the same proposal and evidence (the reading proposed
and why, the readings offered, how many names need it, the words near it
where they settle it, a few names), and empty `correction` and
`respell` columns (respell `yes` saves names with the reading's symbol,
as a rule's respell does). It is tab-separated UTF-8, sorted by unit,
with comment lines saying what each column holds, so a correction is a
one-line diff. The operator corrects it once and keeps it: a later dry
run keeps every row with its correction and respell, says in `why` which
it no longer proposes (still loaded unless deleted), and adds what is
new.

Loading it is the first source of rules: each unit read by its correction,
else its proposal, written with no administrator (`decided_by_admin_id`
NULL) and the command line as the history's actor. A rule an
administrator set otherwise is never overwritten: it is kept, and the
command says so with what the file says. A unit with no reading, a
reading not offered for it, or rules that would make two saved names read
alike load nothing. The seeding page then proposes what is left.

The sheet import (inventory-tng-8i4.17), not yet written, calls these for
its dry run and before it writes; until then the command reads names from
a text file, one a line.
