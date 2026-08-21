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

That command does not exist yet — `inventory-tng-6s9` builds it. Until it does,
every figure below is a hand count, and **a figure taken from here is quoted
with its rule or not at all.**

## The population

Which rows count, and why 3,439 rather than the tab's 3,456, is stated once in
[decision 0008](../decisions/0008-stock-ledger-transfer-graph.md#context)
alongside the counts that rest on it. Every figure here uses that same
population.

Catalogue figures come from the `Fast Inventory` tab, whose item name is
**column D**; column C holds the QR link and happens to have the same number of
filled rows, which has caught one reader already.

## 1. Item string to item

**Rule.** A submission's item string is looked up against the 52 catalogued
names. The sheet's own lookup is `VLOOKUP` against that catalogue, and VLOOKUP
is case-insensitive, so a string differing only in case still resolves.

**Figures.** The 145 strings partition as 52 exact matches, 9 case-only variants
(172 submissions — `Mast straight` against the catalogue's `mast straight` is
most of them), 32 matching nothing at all (83 submissions), and 52 retired NYCM
codes (125 submissions). Strings that resolve to nothing never reached a
balance: 208 submissions between the last two groups.

**Caveat that matters.** The largest single unmatched string is
`TP-Link SFP-RJ45` at 40 submissions, and the catalogue holds `SFP-RJ45 Module`
separately — so it is an alias, not a typo, and the two readings have different
consequences for the importer. Do not cite the 83 as "typos".

**Becomes.** `ItemIdentifier` rows, and at runtime a search that resolves what a
volunteer actually types.

## 2. Note to correction

**Rule.** Not settled. Whole-note equality against `fixing inventory` (218),
`updating inventory` (71), `inventory correction` (67) and `inventory correct`
(50), case-insensitive, gives 406 rows — 11.8%. Substring matching
double-counts, because `inventory correction` contains `inventory correct`, so
each of those 67 rows is swept up by both predicates. A broad regex over
fix/update/correct/adjust gives about 21%.

**Figure.** Decision 0008 previously stated 18.7%. That reproduces under none of
the above, and its original method is unrecorded. **Treat the four exact counts
as the only reproducible part** until the rule is settled.

**Becomes.** Historical rows imported as `adjustment`, not as volunteer
activity. At runtime the stock-count workflow replaces the practice entirely.

## 3. Note to location

**Rule.** Not settled. Two questions are open: which notes name *this* room,
and whether case is folded.

The one figure needing no compound predicate: notes containing `mesh room`,
case-insensitively, number **231 across 41 distinct strings**. Not all are the
same room — `Blue Stockings + mesh room` is a different site — so that is the
count for the phrase, not for the place.

Narrowing to this room needs a second predicate, and the answer moves with it:
`mesh room` plus (`131` or a `broome` misspelling) gives 205 across 31; `mesh
room` plus `broome` alone gives 193 across 23. Folding case collapses the
spellings further and raises the leading one from 97 to 118. **Cite whichever
you use with the predicate attached** — the widest and narrowest readings of
"how often is this room named" are 231 and 193, which is 38 submissions apart,
or a fifth of the smaller.

**Becomes.** `Location` rows and the `from`/`to` sides of imported movements.
Seeds `inventory-tng-o5t`.

## 4. Note to job reference

**Rule.** A note containing `NN` followed by digits. 136 submissions cite one,
across 54 distinct numbers.

**Becomes.** `StockTransaction.job_reference`, a field that already exists, so
nothing has to parse prose again.

## 5. Submissions to batch

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

Nor can a check-in be read as a return. Sorting the 984 by what their notes say
needs the correction rule of §2, which is not settled, so the shares here are
not stated — that is the same discipline the rest of this brief asks for, and it
applies to the argument it is being used to make. What can be said without a
rule: **68 check-ins say in so many words that something came back**, on a
whole-note reading, and that measures how often somebody wrote it down rather
than how often it happened. It is a floor, not a rate.

The earlier version of this section gave a four-way percentage split. Those
shares rested on the broad regex §2 declines to endorse, and they did not sum to
984. They are withdrawn rather than restated.

This is not a classifier waiting to be written. It is the question the
stakeholder meeting exists to answer
(`inventory-tng-8sq`), and it should not be attempted from this data again.
