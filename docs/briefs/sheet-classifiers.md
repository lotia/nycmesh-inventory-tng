# Sheet classifiers

Six rules for reading the exported Google Sheet, and the figures each one
produces. They live together because they are the same thing seen twice: every
figure here is the output of a rule that
[the sheet importer](../data-model.md#migrating-the-existing-sheet) has to apply
to every historical row anyway. A number quoted without its rule is not
reproducible, and this repository has already published several that were not.

**Why these are not in [decision 0008](../decisions/0008-stock-ledger-transfer-graph.md).**
That record keeps the counts that need no judgement about meaning — 52
catalogued items, 3,439 submissions, 2,455 check-outs against 984 check-ins,
145 distinct item strings, 102 distinct name spellings. The last two are counts
of *distinct strings*, which is why they belong there; how many distinct
**things** those strings name is a different question, needs a case rule, and is
answered in §1 and §6 below. Everything here needs a judgement about what a note
or a string means, so the judgement is stated beside the number.

## How to re-run

The workbook carries volunteer names and email addresses and is not ours to
publish, so nothing in CI can read it. **Keep your copy in `ignored/`**, which
is where the repository expects it; `*.xlsx`, `*.ods` and `*.csv` are ignored
everywhere as a second guard, because one `git add -A` over a copy left
elsewhere would publish real people's contact details.

```bash
cd backend && uv run python src/manage.py profile_sheet \
    "../ignored/NYC Mesh - Inventory Sheet.xlsx"
```

It prints a section per rule, and the rules land one at a time. A figure below
that the command does not yet print is a hand count and says so — look for
**(hand count)**. **A figure taken from here is quoted with its rule or not at
all.**

## The population

Which rows count, and why 3,439 rather than the tab's 3,456, is stated once in
[decision 0008](../decisions/0008-stock-ledger-transfer-graph.md#context)
alongside the counts that rest on it. Every figure here uses that same
population, and `profile_sheet` prints it first so that a section beneath it
can be read against the rows it divided:

```
Population
  rows on QRresponses       3456
    carrying a direction    3439
      Checking Out          2455
      Checking In            984
    carrying neither          17
  catalogued items            52
```

Catalogue figures come from the `Fast Inventory` tab, whose item name is
**column D**; column C holds the QR link and happens to have the same number of
filled rows, which has caught one reader already.

## 1. Item string to item

**Rule.** A submission's item string is looked up against the 52 catalogued
names. The sheet's own lookup is `VLOOKUP` against that catalogue, and VLOOKUP
is case-insensitive, so a string differing only in case still resolves.

Everything the case rule does not settle is a judgement per string, written
into an alias table in `inventory/sheet/items.py` rather than inferred by a
pattern, because the readings have different consequences and no edit distance
tells them apart. The largest unmatched string is `TP-Link SFP-RJ45` at 40
submissions: the catalogue holds `SFP-RJ45 Module` and, separately, `Tp-Link`,
which is a router. Read as a typo it mints an identifier against the router;
read as what it is — a TP-Link-branded SFP-to-RJ45 module — it resolves to the
module.

**Unresolvable is an answer**, and a string that gets it carries the reason
why. Three kinds do: ambiguous ones (`mast` names one of three masts,
`RJ45 couplers` names either coupler), ones the catalogue simply does not hold
(`RCA pole`, and `Matt`, which is somebody's name typed into the item field),
and the 53 retired `NYCM-ER-LBEG2`-style codes, whose key is in no tab of the
workbook. Seven of those decode to exactly one catalogued item and are aliased;
the rest are left alone, because `NYCM-ER-SXTSQ` is an SXTsq and the catalogue
holds two, so a guess is a coin toss over 9 submissions.

**Figures.**

```
Item strings
  distinct strings named                        145
    matching the catalogue exactly               52
    matching but for case                         9
    resolved by a hand-written alias             28
    recorded as naming no catalogued item        56
    neither resolved nor accounted for            0
  submissions naming an item                   3436
    reaching a catalogued item                 3337
    reaching nothing                             99
  submissions naming no item at all               3
  alias targets the catalogue does not hold       0
```

The last line of each group is a self-check rather than a figure. Zero
unaccounted strings is what "every one of the 145 is decided" means as
something a machine can say; zero stray alias targets is what notices an item
renamed in the catalogue — and an alias whose target has gone resolves to
nothing rather than to a name the catalogue does not hold, so the two halves
of the report cannot disagree about the same row.

Case is compared as `Lower(Trim())`, which is what
`ItemIdentifier.value_normalised` is: [data-model.md](../data-model.md#item-itemidentifier-category)
asks that normalisation not drift between the write path, the importer and the
scan endpoint, and this is the importer.

An earlier reading of this section gave 32 strings matching nothing across 83
submissions and 52 retired codes across 125. Those partitioned 145 strings but
only 3,436 submissions, because three rows name no item at all and were in
neither group; the retired codes are 53, not 52. **Do not cite the unmatched
strings as "typos"** — most of the submissions behind them are the alias above.

**Becomes.** `ItemIdentifier` rows, and at runtime a search that resolves what a
volunteer actually types.

## 2. Note to correction

**Rule.** A note is a correction when it names **the record** and **an act of
adjusting it**, case-insensitively, and both halves are required:

| Half | Pattern |
| --- | --- |
| the record | `inven\w*`, `invneottr`, `counts?`, `stock` |
| the act | `fix\w*`, `correct\w*`, `updat\w*`, `adjust\w*`, `seed\w*`, `initial`, `recount\w*` |

Neither half alone is a correction, and the export shows why both are needed:
`fixing loose pole nn540` and `hex house fix` are repairs to hardware at a
site, while `inventory order` is an order and `apartment stock` is a place. The
three spellings `invenotry`, `inventry` and `invneottr` are in the record half
because somebody typed them.

**Whole-note against substring, settled.** This brief used to say substring
matching double-counts because `inventory correction` contains `inventory
correct`. That is true of *summing a count per phrase* and false of asking a
row whether it matches any of them — a row is one row however many predicates
it satisfies. Over the four phrases the three readings give 407 rows
whole-note, 445 per row, and 517 summed per phrase; the last counts 72 rows
twice — exactly 517 − 445 — and is not a count of anything. So the rule matches
substrings, evaluated per row: whole-note equality loses `fixing inventory (2
today)`, which is the same act with a detail added.

All three are printed by the command, including the one being argued against,
because a figure quoted here that no code produces is the failure this brief
exists to stop.

**Figures.** 606 of the 3,439 — **17.6%**.

```
Corrections
  submissions                                       3439
    with no note at all                             1184
    naming the record and an act of adjusting it     606
    naming the record only                           116
    naming an act only                                32
    naming neither, note or no note                 2685
  the four enumerated phrases, whole-note            407
    the same phrases, per row                        445
    the same phrases, summed per phrase              517
```

The two half-matching lines are the ones to argue with, and the report prints
them so that the argument has a number. `inventory` alone is 52 of the 116 and
is plainly the same practice written lazily; `inventory order` and `apartment
stock` are 25 more and are not, so the rule takes neither rather than
special-casing a bare word.

Decision 0008 previously stated 18.7%. It does not reproduce under this rule
either, and its original method is unrecorded; 17.6% replaces it. The
"about 21%" this brief also carried was a broad regex over
fix/update/correct/adjust with no object required, which is the reading that
imports `fixing loose pole nn540` as an adjustment. It is withdrawn on those
grounds rather than merely superseded.

**Becomes.** Historical rows imported as `adjustment`, not as volunteer
activity. At runtime the stock-count workflow replaces the practice entirely.

## 3. Note to location

**Rule.** A note names a candidate location when it contains one of a stated
vocabulary of place phrases, matched case-insensitively. The vocabulary is in
`inventory/sheet/locations.py`, read off the export rather than imagined, and
is **deliberately not exhaustive**: what the real list of stock locations is,
is a question for the people who use the room, so this offers a seed and
reports what it does not cover rather than inventing a gazetteer.

**Case is folded, and that is settled.** The mesh room is named in 41 distinct
notes read literally and 33 read case-insensitively, and folding raises the
commonest of them from 97 submissions to 118. Notes rather than spellings —
two notes naming the room identically and differing after it are two, which is
what the count has always been. Nothing is lost by folding: case has never
distinguished two places in this ledger.

Broome is spelled five ways: `broome` (198), `broom` (7 more), `beoome` (4),
`briome` (1) and `brooke` (1). One pattern covers them, and the room's other
half — `mesh room` — is likewise written once and used in both places the
report speaks about that room, so its two lines cannot disagree.

**Which notes name *this* room** had three readings, 38 submissions apart —
a fifth of the smaller — and the rule takes the widest, `mesh room`. The
narrower two answer a question nobody asked: the mesh room is the mesh room,
and the one note where the phrase is not only about it, `Blue Stockings + mesh
room`, names *both* places. That is why a note yields a tuple rather than one
answer, and why the report counts the notes naming more than one. All three
readings stay printed, because a figure quoted without its predicate is not
one.

**Figures.** The seed, one line per candidate, and the coverage beneath it:

```
Locations
  submissions with a note                     2255
    naming a candidate location                408
     of those, naming more than one              6
    naming none of the vocabulary             1847
  distinct candidates named                     19
    131 Broome                                 232
    Blue Stockings                               3
    Mil Mundos                                  15
    Olmsted                                     20
    President Street                            14
    Greenwood Cemetery                          11
    Belmont                                      8
    BAM                                          7
    Astoria                                     13
    Columbia                                     6
    Flatbush Cats                                4
    Grand Street                                21
    Boro Park                                   12
    Harlem                                      10
    SN1                                          6
    W 171st                                     10
    a volunteer's home                          18
    basement                                     3
    backup shelf                                 2
  the mesh room, however written               231
    and 131 or a Broome spelling               205
    and Broome spelled correctly               193
  notes naming it, read literally               41
  notes naming it, read case-insensitively      33
```

`131 Broome` is 232 rather than 231 because one note gives the address without
the room. `naming more than one` is indented a level deeper than the two lines
it sits between, because it is a subset of the first of them rather than a
third share of the population. The 1,847 uncovered notes are mostly installs
and orders rather than places, and the number is here because a seed that hid
its coverage would read as a finished list.

**`a volunteer's home` is not a `Location` row**, and o5t should not read it as
one. `location_held_by_iff_custody` requires a custody location to name its
holder, and which volunteer is §6's question. It is a marker that custody was
meant, for the importer to pair with a person.

**Becomes.** `Location` rows and the `from`/`to` sides of imported movements —
after review, not before. This list is the seed `inventory-tng-o5t` takes to
the stakeholder meeting.

## 4. Note to job reference

**Rule.** `NN`, an optional space, then digits, case-insensitively —
`\bNN\s*(\d+)\b`. The census of the 137 references is `NN` 58, `Nn` 25, `nn`
21, `NN␣` 17, `Nn␣` 15, `nn␣` 1, and it is what both pieces of latitude rest
on: title case is 40 on its own, so a case-sensitive read finds 75 of the 136
and loses 61; 33 submissions put a space in, so a pattern demanding `NN904`
finds 103. No submission writes `NN#217`, so the pattern does not admit a `#`
— latitude for input that is not in the ledger is latitude nothing can check.

**Figures.** 136 submissions cite a job, across 54 distinct numbers. One note
cites two — `nn498-nn6622`, a link between two nodes rather than two jobs — so
the rule takes the first, and one cited job is therefore carried by nothing.

```
Job references
  submissions citing a job                         136
  distinct jobs cited                               54
  submissions citing more than one                   1
  cited jobs the imported field will not carry       1
```

**Becomes.** `StockTransaction.job_reference`, a field that already exists, so
nothing has to parse prose again.

## 5. Submissions to batch

*Not settled, and every figure in this section is a **(hand count)** —
`profile_sheet` has no section for it yet. `inventory-tng-a82` settles it.*

**Rule.** Chaining submissions by the same person with gaps of ten minutes or
less. That gives a largest burst of **24** under every submitter key tried, and
**76.6%** of submissions inside a burst keying on email with a name fallback
(76.8% by name, 77.8% by email alone). A fixed ten-minute window anchored on the
first submission gives a largest burst of 17 instead, so the window rule is what
decides that figure; the submitter key moves only the percentage, by about a
point.

**Correction.** An earlier version of this brief said decision 0008's "76% and
24" came from two different methods and could not both be right. That was wrong:
chaining produces both, and the pairing in 0008 reproduces. The submitter key
still needs settling, because 45% of submissions carry no email.

**Becomes.** One `StockTransaction` per burst. At runtime this is already solved
by the cart: one submission carries many movements.

## 6. Person to volunteer

*Not settled, and every figure in this section is a **(hand count)** —
`profile_sheet` has no section for it yet. `inventory-tng-5r2` settles it.*

**Rule.** Not settled. 102 distinct name spellings and 65 distinct emails, but
45% of submissions carry no email, and 41 spellings never appear beside one at
all — several of them plainly separate people. **65 is a count of emails, not a
headcount.** Case-folding names alone gives 86.

**Becomes.** `Volunteer` rows and merges. The real headcount is above 65 and is
not yet known.

## What no rule can recover

**Whether hardware came back.** Every printed QR opens a form preset to
`Checking Out`, so a low return rate is what this instrument produces whether or
not anything is returned.

Nor can a check-in be read as a return. §2 can now tell a correction from a
movement, so the 984 could be sorted — but a share of them would still measure
how often somebody wrote something down rather than how often it happened,
which is the point this section exists to make. What can be said without any
rule at all: **68 check-ins say in so many words that something came back**
(hand count), on a whole-note reading. That is a floor, not a rate.

The earlier version of this section gave a four-way percentage split. Those
shares rested on a broad regex nothing now endorses, and they did not sum to
984. They are withdrawn rather than recomputed, for the reason above.

This is not a classifier waiting to be written. It is the question the
stakeholder meeting exists to answer
(`inventory-tng-8sq`), and it should not be attempted from this data again.
