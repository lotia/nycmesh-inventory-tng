# 0026 — What makes two strings one identifier, and what decides it

**Status:** accepted

## Context

The project owner asked for one rule, on 2026-08-22, while reading the sheet
importer's rule 1 — [which catalogued item an item string
names](../briefs/sheet-classifiers.md#1-item-string-to-item): free text keeps
whatever format, spelling and letter case somebody typed, but structured data
compares without regard to case, so no pick-list can ever offer one product
under three spellings. The
spelling on screen should be the one its manufacturer uses — `MacBook Pro`, not
`macBook pro` — and typing on a keyboard must stay easy. They asked what could
go wrong with that before anything was built.

Most of it is already built. `Item.name` holds the spelling that is shown,
`ItemIdentifier.value` keeps the string as it arrived, and
`ItemIdentifier.value_normalised` is a generated column under a unique index —
see [the data model](../data-model.md#item-itemidentifier-category). What was
never written down is the rule those three imply, and it is the rule the
owner's concern actually turns on: **a pick-list is populated from item names,
and identifiers are only ever matched against.** A control that offers
identifier values is what produces one product three times.

Everything below was measured on 2026-08-22 — against this checkout, against a
PostgreSQL 18.6 server, and against the real export — rather than recalled.
Where a figure has a home already it is linked to rather than repeated, and
each of the three sections says what it was measured with.

### What the data says about how hard to fold

Six candidate foldings over the corpus, from "lower case only" up to "discard
everything that is not `[0-9a-z]`". The corpus itself, and how each of the 145
typed strings is accounted for, is
[§1 of the sheet-classifiers brief](../briefs/sheet-classifiers.md#1-item-string-to-item);
what is added here is what happens to it under a fold it has not had yet.

- **No catalogue collision at any strength.** All 52 names stay 52 distinct
  folded forms under every rule tested. `Netpower 15R` and `Netpower 16P` stay
  apart even when punctuation and spacing are thrown away.
- **Folding harder buys almost nothing.** Coverage of the typed strings: 52
  resolve as written, 61 after lower case, 64 under the most aggressive rule —
  4 more submissions for that last step. Newly resolved strings attributable to
  punctuation folding alone: none.
- **The 56 strings that resolve to no item are not spelling variants.** 46 are
  opaque codes from the retired NYCM SKU scheme — the 53 the brief counts, less
  the 7 that decode to exactly one item and are aliased. The rest are the ones
  `UNRESOLVABLE` names by hand: 6 genuinely ambiguous (`mast`, `rj45`, `Omni`)
  and 4 that are not products at all. No fold reaches any of them, because
  there is nothing on the other side to reach.
- **Trimming, whitespace collapse and NFKC are no-ops on this corpus.** For all
  197 strings the four results are byte-identical. They also cost nothing.
- **Case folding alone does the work asked for.** It collapses the 145 typed
  strings to 134 across 9 groups — `LiteBeam`/`Litebeam`/`litebeam`,
  `OmniTikPOE`/`OmnitikPOE`, `Tp-Link`/`Tp-link` and six more. Not one of the
  nine merges two different products, and none spans two catalogue targets.
- **There is very little headroom below that.** No two catalogue names are one
  edit apart; the closest pairs are two apart, and folding does not tighten it.
  Those closest pairs are all real distinctions — 15 m against 30 m fibre,
  SC-APC against LC-UPC, `Netpower 15R` against `16P`, `UAP-AC-M` against
  `UAP-AC-IW`, `CubeSA` against `Cube`.
- **The corpus is ASCII.** Every catalogue name is, and the whole corpus holds
  exactly one non-ASCII character: a right single quotation mark, in the one
  prose string that resolves to nothing under any rule.

### What PostgreSQL says about the mechanism

A PostgreSQL 18.6 server: UTF8, `en_US.UTF-8`, libc, deterministic.

- **`lower()` in the database and `str.lower()` in Python disagree, in both
  directions.** On 24 probe strings they differ on 3, and the three are the
  interesting ones. `'İPHONE'` (U+0130) folds to `iphone` in PostgreSQL and to
  `i` + U+0307 + `phone` in Python, so the database calls it a duplicate of
  `IPHONE` and Python does not. Greek final sigma goes the other way: Python
  says two spellings of `ΟΔΟΣ` are one string and the database says they are
  two. `casefold()` is a third answer again, differing from the database on 9
  of the 24. And the database's answer moves with the collation, where
  Python's does not: under a Turkish ICU collation `lower('IPHONE')` starts
  with a dotless ı.
- **The current expression is blind to Unicode normalisation.** Two rows
  spelling `Café` with U+00E9 and with `e` + U+0301 are both accepted, render
  identically, and differ only in byte length. `normalize(x, NFC)` is
  `IMMUTABLE` and closes it; `unaccent()` is only `STABLE` and so cannot appear
  in a generated column at all.
- **`TRIM()` strips the ASCII space and nothing else.** Against a replica of
  the real table, four visually identical rows were accepted — three of them
  duplicates the unique index exists to refuse. Tab padding, a doubled
  internal space, a no-break space, a zero-width space, a narrow no-break
  space, an ideographic space, a byte-order mark and a soft hyphen all defeat
  it. `btrim`, `translate` and `regexp_replace` are all `IMMUTABLE` over a
  character set given as a literal, so the fix is available inside the
  generated column.
- **A nondeterministic ICU collation works, and is unsound here anyway.** With
  one, `=` itself becomes case- and normalisation-insensitive: the unique index
  refuses `macbook pro` against `MacBook Pro`, the manufacturer's spelling
  stays in the one column, and no application code is involved. Even `LIKE`
  works in PostgreSQL 18 and is collation-aware. But `pg_trgm` is not
  collation-aware, an index on such a column can still be created, and the
  planner will still use it: the same query returned one row on a sequential
  scan and zero rows through the index. `ILIKE`, every regular expression,
  `starts_with`, `^@` and `text_pattern_ops` are all refused outright. The
  escape hatch — a functional index casting back to `COLLATE "C"` — puts
  `lower()` back for searching while uniqueness keeps ICU's rules, which is two
  incompatible notions of sameness in one table.
- **`citext` is worse than either.** PostgreSQL 18's own page for it opens by
  recommending a nondeterministic collation instead, and its limitations
  section says it is not case-insensitive by Unicode's definition. It *is*
  `lower()`, so it inherits every disagreement above. And no index serves a
  prefix query on it: `text_pattern_ops` and `gin_trgm_ops` both create, and
  neither is ever chosen, because the operator that resolves is
  `citext ~~ citext`.
- **Django can express all three, and will not catch the mistake.** A
  `GeneratedField` whose expression is not `IMMUTABLE` passes
  `makemigrations`, ships in a committed migration, and fails at `migrate`
  time.
- **The design in use is missing the index it needs.** Measured on 6000 rows:
  the unique btree with default operator classes cannot serve a prefix query in
  an `en_US` database — sequential scan, 5900 rows filtered. A second index
  with `text_pattern_ops` turns it into a bitmap index scan. And Django's
  `__istartswith` compiles to `UPPER(col) LIKE UPPER(pattern)`, which matches no
  index under any of the three designs; only a plain `__startswith` against an
  already-lowered pattern uses one.

### What the codebase says

- **Nothing leaks today, and the survey was exhaustive rather than a spot
  check.** `ItemIdentifier` is imported by neither `serializers.py` nor
  `views.py`, there is no serializer for it, and no endpoint returns one. Every
  list endpoint returns a stored canonical name. No model has a foreign key to
  it, so Django never renders its `__str__` — which does begin with the raw
  value — inside a `<select>`. The frontend has no `Autocomplete`, no
  `datalist` and no bare `select`; every option list renders a stored name.
- **The rule is already kept in the one place identifiers are searchable, by
  accident.** `ItemAdmin` searches `identifiers__value` and displays `name`.
- **The place it will break is known.** The natural way to implement "search
  identifiers" in DRF is to annotate the matched value so the interface can
  show why a row matched, and that annotation rendered as an option label *is*
  the bug. Nothing in the repository would stop it: no test asserts that an
  item list carries no identifier value, and the frontend test drives from a
  fixture, so a fixture holding one would pass.
- **The Python/database disagreement has exactly one load-bearing site today.**
  `_identifiers.py` builds its dictionary keyed by the database's fold, read
  back off `value_normalised`, and looks it up with Python's fold. Where the
  two differ the lookup misses, a create fires, the unique index raises, and
  because minting runs in one transaction the whole catalogue import aborts —
  not one row of it.
- **`Label.normalise_code` is the shape that has none of this trouble**, and it
  is in this repository already, argued in
  [§3 of 0011](0011-qr-batch-scanning.md#3-the-printed-label-an-uppercase-url-wrapping-an-opaque-code):
  the canonical form is the only form
  stored, comparison is exact, and the alphabet is a check constraint rather
  than a rule the writer is trusted with. There is no second fold anywhere for
  it to disagree with. The identifier column is not built that way and this
  record does not propose rebuilding it — `value` is evidence of what somebody
  typed and is worth keeping — but it is the standard the arrangement below is
  measured against.

## Decision

### 1. The generated column stays. Not a collation, not `citext`

It is the only one of the three that supports a **sound** indexed prefix
search, and autocomplete is the feature this is all for.

**The nondeterministic collation was rejected for that unsoundness and for
nothing else.** It is otherwise the better mechanism — less application code,
one column, uniqueness and equality agreeing by construction — and PostgreSQL's
own documentation prefers it to `citext`. A future reader who finds this record
and concludes the collation is a poor tool has read it wrong. What disqualifies
it is that `pg_trgm` will silently return fewer rows through an index than
without one, and a search that quietly misses is worse than one that is slow.
If `pg_trgm` ever becomes collation-aware, this decision is worth reopening.

`citext` is rejected outright, on PostgreSQL's own advice and because no index
can serve a prefix query on it.

### 2. The expression is fixed, because that is where the real defects are

`LOWER(TRIM(value))` becomes three things: normalise to NFC, reduce whitespace,
lower case. Each is `IMMUTABLE` and legal in a generated column.

**Reducing whitespace means collapsing every run of it to one ASCII space and
then removing it from both ends** — so `TRIM`'s job, over a set `TRIM` does not
have. That set is what actually decides the outcome, so it is named here rather
than described:

- everything Unicode gives the `White_Space` property, which is what `TRIM`
  ought to have meant: the ASCII space and tab, line feed, vertical tab, form
  feed, carriage return, NEL, the no-break space, and the `U+2000`–`U+200A`,
  `U+2028`, `U+2029`, `U+202F`, `U+205F` and `U+3000` family;
- plus three characters that have no width at all and so are not whitespace by
  any definition, but are invisible in exactly the way that defeats a unique
  index: the soft hyphen `U+00AD`, the zero-width space `U+200B`, and the
  byte-order mark `U+FEFF`.

The order of the collapse and the trim is free — a leading no-break space goes
either way round — which is why they are one decision and not two.

This closes three holes the current constraint has and that were demonstrated
rather than imagined: the decomposed-accent duplicate, the tab and no-break
space duplicates, and the double-internal-space duplicate.

### 3. Fold case and whitespace. Nothing else

**Punctuation is not folded.** The four measurements above are the whole
argument: nothing collides at any strength, folding harder buys almost nothing,
what fails to resolve is not a spelling variant, and there is no headroom to
spend.

`RJ-45` against `RJ45` was a hazard imagined before the corpus was counted.
The catalogue does spell it both ways — `RJ45 passthrough` and `RJ45 ToughCable
connectors` beside `RJ-45 Coupler (Indoors)` — but those are three different
products, so folding the hyphen away would move them *towards* each other
rather than resolve anything. And the largest string that fails to resolve is
not a hyphenation at all: it is `TP-Link SFP-RJ45`, which fails because only
somebody who knows what the thing is can say whether it means the module or the
router, which is why `inventory/sheet/items.py` answers it by hand and §1 of
the brief works it through. The data does not contain the problem that fold was
for.

The same measurement forbids the obvious next step: a rule that folded digits,
dropped short tokens, or treated an edit distance of two as sameness would
merge real products immediately. **Fuzzy matching may suggest and must never
decide identity.**

### 4. The Python side is not an authority

The database's `lower()` and Python's `str.lower()` disagree in both directions
and cannot be reconciled — one is collation-dependent and the other is not.
So:

- **The database constraint decides.** `value_normalised` and its unique index
  are the only statement of whether two strings are one identifier.
- **`IntegrityError` on this path is a normal outcome**, reported as a clean
  conflict naming what already holds the string. It is never an assertion
  failure and never a 500. [0015](0015-merged-identifier-conflict.md) already
  settled what such a conflict says on the volunteer side.
- **The Python normaliser exists only to make the common case fast** — to avoid
  a round trip for the overwhelming majority of strings, where the two folds do
  agree. It must never be the thing that concludes two strings are one.

That last point is stated next to the function as well as here, because the
function is where somebody will be standing when they need it.

### 5. Search identifiers, display items

This is the rule that answers the question the owner actually asked, and it is
short: **a control that offers a choice is populated from `Item.name`.
Identifiers are matched against and never rendered.**

Nothing violates it today, so this is a guard to be written rather than a bug
to be fixed: one test on each side of the wire, holding that a response and a
rendered row carry the item's name and not the identifier that matched it.

**The guard scans, it does not allowlist.** A test naming the fields that may
not carry an identifier value passes the day somebody adds a field called
`matched_on`, which is precisely the shape [the survey](#what-the-codebase-says)
predicted the leak would take. A recursive scan of the whole response for the
string that was minted does not.

**The rule binds what is offered, not what is matched, and the difference is
load-bearing.** Matching more widely never produces the owner's complaint: one
product appears three times because three *labels* are drawn, not because three
strings were searched. So a search may look at `Item.name` and at the
identifiers together, as long as it answers with distinct items rendered by
their name — which is what `ItemAdmin` already does, and the survey found that
arrangement kept there by accident rather than by a test.

Reading it the other way round would be a regression rather than a purity, and
the reason is a gap this record has to name. `mint_items` writes an identifier
per catalogued name, so an imported item has one; **nothing on the API's create
path does, so an item added through the app has none at all.** A search that
consulted identifiers *instead of* names would make every item created in the
app permanently unfindable, by a route the interface offers no way to repair.
Searching both is safe whether or not that gap is ever closed; searching
identifiers alone is safe only afterwards. That ordering is the decision.

Closing the gap — minting an identifier from an item's own name when one is
created, and deciding what a later rename does to it — is real work and is
`inventory-tng-w4dg`. It is not part of implementing this rule: it changes the
write path rather than the read one, and a name colliding with an existing
identifier for a *different* item has to arrive as decision 4's clean conflict.

### 6. Prefix search needs an index the schema does not have

**A prefix match on `value_normalised` goes through a `text_pattern_ops` index,
against a term the caller has already lowered.** The measurement above is why
each half is needed: the unique index cannot serve the query in an `en_US`
database, and case-insensitivity has to be spent in Python because spending it
in SQL is what loses the index.

**So `__istartswith` must not appear on this path**, even though it is the
spelling somebody reaches for by default and reads like the obvious one. A test
holds the generated SQL, because this is a rule about a query plan and a review
reading the Python cannot see one.

### 7. The manufacturer's spelling is a data task, and it has an owner

The catalogue is not spelled the way the owner asked for, and no code change
can make it so. As it stood on 2026-08-22 it was wrong against the
manufacturers in at least six
places — `Tp-Link` should be `TP-Link`, `OmniTikPOE` should be `OmniTIK PoE`,
`Hex POE` should be `hEX PoE`, `Netpower` should be `netPower`, `ToughCable`
should be `TOUGHCable` — and it contradicts itself in two more, carrying
`Ubiquiti J-Pole` beside `Jpole Umount Extender`, and hyphenating `RJ-45
Coupler (Indoors)` where `RJ45 passthrough` and `RJ45 ToughCable connectors`
do not. Eleven names carry intercaps that a fold would visibly destroy if it
were ever used for display, and ten more are casually typed.

**This record does not change any of them.** Deciding what a manufacturer's
spelling is takes somebody who knows, it is a different piece of work from
normalising comparisons, and guessing here would write a wrong spelling into
the column every later screen reads. `inventory-tng-ypwr` is where it waits
for one.

## Consequences

- **The migration rewrites the column, and may surface duplicates the old
  constraint let through.** Two rows that differ only by a tab, a no-break
  space, a doubled space or a decomposed accent are legal today and are one
  string afterwards. The migration therefore **finds and reports them by
  primary key and value before it changes anything**, and stops with that
  report, rather than dying part-way through a rewrite with half the table
  converted and no list of what to fix. `inventory-tng-udz6` carries decisions
  2, 4, 5 and 6 as one issue each.
- The catalogue import stops being able to abort wholesale over a fold
  disagreement, because decision 4 makes the conflict a reported outcome.
- **Four sentences already in the repository are contradicted by what was
  measured here, and they move with the code rather than afterwards.** The
  comment on `value_normalised` and the paragraph `docs/data-model.md` takes
  from it both promise that the generated column removes the possibility of
  drift; the load-bearing site named above is that drift, and it is there
  today. The docstring on `items.normalised()` justifies the function by the
  one job decision 4 forbids it. And §1 of the brief names the old expression.
  `inventory-tng-udz6.1` and `udz6.2` carry all four as acceptance criteria.
- `value` keeps holding what was typed. Nothing in this record makes the raw
  string less trustworthy; it makes it less *authoritative*.
- Two folds still exist in the repository that this record does not cover.
  `Volunteer.sheet_key` and `Volunteer.email` have plain case-sensitive unique
  indexes and no generated column, and the sheet importer folds them in Python
  only. There is no drift risk while Python is the only fold, but the pair are
  not an instance of the arrangement decided here, and anything that says they
  are is wrong — `inventory-tng-xvp1` is a docstring that does. What the
  mismatch costs on the search side is `inventory-tng-xjpd`.
- Search behaviour is only decided for prefix matching. Substring and
  similarity search over identifiers would want `pg_trgm`, which is sound on
  this column precisely because the column is deterministic — but nothing here
  measures whether it is wanted.
