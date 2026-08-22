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

It prints a section per rule, and **every figure below now comes out of it.**
There are no hand counts left in this document. A number in the prose is
either in one of the fenced blocks, or is arithmetic over two of them — a
percentage, or a difference — or is a withdrawn figure named as one, and those
say so where they appear. **A figure taken from here is quoted with its rule or
not at all.**

Every unlabelled fenced block below is that command's output, pasted. A test
reads them back and fails if a block's labels are no longer the ones its
section emits, so a rule that gains, loses or renames a line cannot leave a
stale block behind. The numbers themselves are not checked and cannot be: they
come from a workbook nothing in CI may read.

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
  distinct strings named                         145
    matching the catalogue exactly                52
    matching but for case                          9
    resolved by a hand-written alias              28
    recorded as naming no catalogued item         56
    neither resolved nor accounted for             0
  submissions naming an item                    3436
    reaching a catalogued item                  3337
    reaching nothing                              99
  submissions naming no item at all                3
  retired NYCM codes among the strings            53
   of those, decoding to one catalogued item       7
   submissions on the largest that does not        9
  submissions the largest alias speaks for        40
  alias targets the catalogue does not hold        0
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
     of those, the bare word alone                    52
     of those, an order or a place                    25
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

Broome is spelled five ways and the report counts each of them, so the
pattern's own reading is the one on the page rather than a substring count
taken beside it. One pattern covers all five, and the room's other half —
`mesh room` — is likewise written once and used in both places the report
speaks about that room, so its two lines cannot disagree.

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
  submissions with a note                                  2255
    naming a candidate location                             408
     of those, naming more than one                           6
    naming none of the vocabulary                          1847
  distinct candidates named                                  19
    131 Broome                                              232
    Blue Stockings                                            3
    Mil Mundos                                               15
    Olmsted                                                  20
    President Street                                         14
    Greenwood Cemetery                                       11
    Belmont                                                   8
    BAM                                                       7
    Astoria                                                  13
    Columbia                                                  6
    Flatbush Cats                                             4
    Grand Street                                             21
    Boro Park                                                12
    Harlem                                                   10
    SN1                                                       6
    W 171st                                                  10
    a volunteer's home                                       18
    basement                                                  3
    backup shelf                                              2
  the mesh room, however written                            231
    and 131 or a Broome spelling                            205
    and Broome spelled correctly                            193
  notes naming it, read literally                            41
  notes naming it, read case-insensitively                   33
  the commonest of those notes, read literally               97
  the commonest of those notes, read case-insensitively     118
  submissions spelling the street                           202
    broome                                                  191
    broom                                                     5
    beoome                                                    4
    briome                                                    1
    brooke                                                    1
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
`\bNN\s*(\d+)\b`. The census below is what both pieces of latitude rest on,
and it is title case rather than lower case that makes the first one matter —
a case-sensitive read finds 75 of the 136. No submission writes `NN#217`, so
the pattern does not admit a `#`: latitude for input that is not in the ledger
is latitude nothing can check. `␣` marks the space the pattern allows.

**Figures.** 136 submissions cite a job, across 54 distinct numbers. One note
cites two — `nn498-nn6622`, a link between two nodes rather than two jobs — so
the rule takes the first, and one cited job is therefore carried by nothing.

```
Job references
  submissions citing a job                            136
  distinct jobs cited                                  54
  submissions citing more than one                      1
  cited jobs the imported field will not carry          1
  references written                                  137
    NN                                                 58
    NN␣                                                17
    Nn                                                 25
    Nn␣                                                15
    nN                                                  0
    nN␣                                                 0
    nn                                                 21
    nn␣                                                 1
  submissions a case-sensitive read would find         75
  submissions a read allowing no space would find     103
```

**Becomes.** `StockTransaction.job_reference`, a field that already exists, so
nothing has to parse prose again.

## 5. Submissions to batch

**Rule.** A batch is a run of submissions by the **same submitter**, each within
**ten minutes of the one before it**, where the submitter is the volunteer's
**name, case-folded** — and a name is a name by §6's reading of the field, so a
row answering it with `testing` or `update inventory` names nobody here just as
it names nobody there. A submission the rule cannot attribute or cannot place in
time — no name, or no timestamp — is a batch of one.

**Chaining against a fixed window, settled.** Measuring the gap from the
previous submission rather than from the first is what decides the largest
batch: **24** chained against **17** anchored, a third of the figure. Anchoring
cuts a trip on the clock rather than on a pause, so somebody working steadily
through a dozen items crosses the boundary while still standing at the shelf and
the rest of the trip becomes a second transaction that nothing in the ledger
separates from the first. It does that 107 times over this export — the
difference between the two batch counts below. The objection to chaining is the
opposite one, that an unbroken afternoon could chain into a single enormous
batch; in this export it does not, and **no batch spans as much as an hour**.
The report prints the longest so that the next export cannot change that behind
the rule.

**The name against the email, settled.** 1,540 submissions — 45% — carry no
email, and keying on the email puts every one of them under one empty key. That
is not a small distortion: it is where **77.8%** came from. Let the emailless
rows stand alone instead, as rows the rule cannot attribute, and the same key
reports **41.6%** inside a batch, so more than half of what it was measuring was
the collapse rather than anybody's trip. The name field answers with nothing
usable on 48 submissions rather than 1,540 — §6 decides what counts as a name,
and this rule takes that answer — so the same treatment costs it almost
nothing.

Email-with-a-name-fallback is the worst of the three rather than a compromise.
It gives **112 keys** where the name gives 80, because a volunteer who typed
their email on one visit and not on the next gets one of each. It splits real
trips on whether somebody filled a field in, and it invents submitters while
doing it.

Case is folded, as in §1 and §3 and for the same reason: case has never
distinguished two of anything in this ledger. What the folding costs in name
spellings is §6's count, not this one's.

**Figures.** The chosen rule first, then what each rejected reading would have
said. Every alternative is printed rather than described, because a reading
argued against in prose that no code produces is the failure this brief exists
to stop.

```
Batches
  submissions                                        3439
    inside a batch of more than one                  2623
    alone in a batch of their own                     768
    naming nobody, or no time, alone by that rule      48
  batches                                            1557
  largest batch                                        24
  longest batch, in minutes                            49
  submitters the rule can name                         80
  anchoring a fixed window instead, batches          1664
    the anchored reading, inside a batch             2580
    the anchored reading, largest batch                17
  submissions carrying no email                      1540
  keying on the email instead, submitters              65
    the email key, inside a batch                    1430
    the email key, largest batch                       23
    the email key, chaining the emailless as one     2676
     that reading's largest batch                      24
  keying on the email with a name fallback, keys      112
    the fallback key, inside a batch                 2633
    the fallback key, largest batch                    24
```

Decision 0008's pairing of "76% and 24" reproduces: chaining gives both halves,
and an earlier version of this brief was wrong to say they came from two
different methods. What the pairing rested on was the reading in which nobody is
a submitter — 24 holds under all three keys while the emailless rows chain as
one, and the email key drops to 23 once they stop. The percentage moves further,
and **76.3%** is what the settled rule gives.

`submitters the rule can name` is a count of folded name spellings and not a
headcount, for the same reason §6 gives about the emails: the real number of
people is §6's question and is answered there.

**Becomes.** One `StockTransaction` per batch, with one movement per submission
in it. At runtime this is already solved by the cart: one submission carries many
movements.

## 6. Person to volunteer

**Rule.** A submission is from the volunteer its **name** field names, folded
to lower case. Two folded spellings are one volunteer when the workbook shows
*both* halves of a test:

| Half | What it asks |
| --- | --- |
| the same name | one spelling is the other written longer — a surname or an initial added — or differs from it by a single character, once spaces come out |
| the same person | the two spellings appear beside a common address |

Where the name field holds no name — an address typed into it, a quantity, a
note, the word `Testing` — the address stands in, and only where exactly one
volunteer is ever named beside it. Everything the two halves do not join is a
volunteer of its own. Why each half is needed, and why the joins are stated as
a rule rather than written out per person, is in `inventory/sheet/people.py`.

**Neither field is a key, and the address is the one that surprises.** 1,540
of the 3,439 submissions — **44.8%** — carry no address at all, so keying on
the address loses nearly half the ledger before it starts. Nor does it divide
the half that is left: 13 volunteers wrote more than one address and the
busiest wrote six, while 2 addresses carry submissions naming more than one
volunteer and one of those names three. *Unioning* spellings that share an
address is worse than keying on it, because it is transitive — the phone
somebody lends the most pulls everybody it ever carried into a single row.

**Figures.**

```
People
  distinct name spellings                              102
    the same but for case                               16
  distinct names                                        86
    holding no name at all                               6
    joined to another by a shared address               14
    a volunteer in their own right                      66
  volunteers the import mints                           72
    known only by an address                             6
    flagged as possibly a duplicate                     22
     submissions those flags carry                     107
  the fewest volunteers this can be                     59
  volunteers who wrote no address                       31
  volunteers who wrote more than one                    13
   the most any one of them wrote                        6
  addresses written beside more than one volunteer       2
   the most volunteers any one of them names             3
  submissions reaching a volunteer                    3432
    by the name field                                 3391
    by an address, the name being unusable              41
  submissions reaching nobody                            7
  not-a-name entries no submission wrote                 0
```

**65 was never a headcount, and it is not a floor either.** It counts
addresses: 31 of the 66 volunteers the names give never wrote one, which
pushes the answer up, and 13 of the remaining 35 wrote several, which pushes
it down. Pairing it with the 102 gave a range whose ends measure two different
things, and it is withdrawn on those grounds rather than corrected.

**The answer is a range, and the report prints both ends.** 72 is what the
import mints, and it is the top: merging only ever removes a row. 59 is the
bottom — what is left when every flag that names a candidate turns out to be
one. Not "if every flag is a duplicate": six of the 22 are addresses nobody
was ever named beside, and there is nobody in particular for them to be a
duplicate *of*, so they survive any answer to the other sixteen. Nothing in
the workbook narrows the range further. So this ledger is **59 to 72
people**. The claim this section used to carry, that the real headcount is
above 65, does not survive the measurement either: the bottom of the range is
below it.

**Flagged rather than merged, and that is the whole design.** The 22 are 16
names that never appeared beside an address and are another volunteer's name
written longer or a character away from it, plus 6 addresses no name is ever
written beside. Together they carry 107 submissions. Each is a `Volunteer` row
an administrator merges in a moment — `merged_into` is set, the duplicate
stops being offered, and the ledger is untouched — whereas a wrong merge is
written into every row imported against it and an append-only ledger cannot
take it back. Two people who share a first-name spelling and never gave an
address are not one person, and nothing here can tell them from one person who
spelled their name two ways.

Flagging is deliberately generous for the same reason: a three-letter name one
character from another three-letter name is flagged even where the two are
plainly separate names. The cost is an administrator's glance.

**Becomes.** `Volunteer` rows, and the merges an administrator makes over
them. Seven submissions reach nobody at all and have no actor to be imported
against; what the importer does with those is `inventory-tng-2dg`'s to settle.

## What no rule can recover

**Whether hardware came back.** Every printed QR opens a form preset to
`Checking Out`, so a low return rate is what this instrument produces whether or
not anything is returned.

Nor can a check-in be read as a return. §2 can now tell a correction from a
movement, so the 984 could be sorted — but a share of them would still measure
how often somebody wrote something down rather than how often it happened,
which is the point this section exists to make.

What can be said without inferring anything is how often a note uses the word,
and `profile_sheet` says it rather than this brief asserting it:

```
Return language
  submissions whose note says return      58
    recorded as a check-in                52
    recorded as a check-out                6
  check-ins                              984
```

**That is a floor under "how often did somebody write it down", and is not a
rate of anything.** Six of the submissions that say `return` record stock going
*out*, so the word does not reliably give even the direction. This brief
previously said 68 check-ins on a hand count; the reading above gives 52, and
68 reproduces under nothing.

The earlier version of this section also gave a four-way percentage split.
Those shares rested on a broad regex nothing now endorses, and they did not sum
to 984. They are withdrawn rather than recomputed, for the reason above.

This is not a classifier waiting to be written. It is the question the
stakeholder meeting exists to answer
(`inventory-tng-8sq`), and it should not be attempted from this data again.
