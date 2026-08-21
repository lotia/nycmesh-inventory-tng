# Sheet classifiers

Six rules for reading the exported Google Sheet, and the figures each one
produces. They live together because they are the same thing seen twice: every
figure here is the output of a rule that
[the sheet importer](../data-model.md#migrating-the-existing-sheet) has to apply
to every historical row anyway. A number quoted without its rule is not
reproducible, and this repository has already published several that were not.

**Why these are not in [decision 0008](../decisions/0008-stock-ledger-transfer-graph.md).**
That record keeps the counts that need no rule — 52 catalogued items, 3,439
submissions, 2,455 check-outs against 984 check-ins, 145 item strings, 102 name
spellings. Everything below needs a judgement about what a note means, so the
judgement is stated beside the number.

## How to re-run

The workbook is not ours to publish and is gitignored, so nothing in CI can read
it. Supply your own copy:

```bash
uv run manage.py profile_sheet "path/to/NYC Mesh - Inventory Sheet.xlsx"
```

Until that command lands (`inventory-tng-6s9`), the figures below were counted by
hand using exactly the predicates stated. **Any figure taken from here is quoted
with its rule or not at all.**

## The population

Counted over the `QRresponses` tab, restricted to rows carrying a direction:
2,455 `Checking Out` plus 984 `Checking In` is the 3,439. Seventeen further rows
carry neither — they are sheet furniture (`ADD NEW PRODUCTS HERE TO PREVENT
ERRORS`) and are not submissions. Catalogue figures come from the `Fast
Inventory` tab, whose item name is **column D**; column C holds the QR link and
happens to have the same number of filled rows, which has caught one reader
already.

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
double-counts, because `inventory correct` contains `inventory correction`. A
broad regex over fix/update/correct/adjust gives about 21%.

**Figure.** Decision 0008 previously stated 18.7%. That reproduces under none of
the above, and its original method is unrecorded. **Treat the four exact counts
as the only reproducible part** until the rule is settled.

**Becomes.** Historical rows imported as `adjustment`, not as volunteer
activity. At runtime the stock-count workflow replaces the practice entirely.

## 3. Note to location

**Rule.** Not settled; case handling is the open question. One room at 131
Broome is written 31 ways across 205 submissions counting case-sensitively, of
which `Sean mesh room 131 broome st` is 97; folding case gives 24 ways and 118
for the leading spelling. Notes mentioning any mesh room, including a different
site, total 231 across 41 — do not use that as the figure for this room.

**Becomes.** `Location` rows and the `from`/`to` sides of imported movements.
Seeds `inventory-tng-o5t`.

## 4. Note to job reference

**Rule.** A note containing `NN` followed by digits. 136 submissions cite one,
across 54 distinct numbers.

**Becomes.** `StockTransaction.job_reference`, a field that already exists, so
nothing has to parse prose again.

## 5. Submissions to batch

**Rule.** Not settled. Chaining submissions by the same person with gaps of ten
minutes or less gives about 77% of submissions inside a burst and a largest
burst of 24. A fixed ten-minute window anchored on the first submission gives
about 76% but a largest of 17. Decision 0008 previously stated 76% and 24, which
no single rule produces — the two halves come from different methods.

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
not anything is returned. Of the 984 check-ins, 39% are corrections, 21%
deliveries, 23% carry no note at all, and 68 say in so many words that something
came back — but that last figure measures how often somebody wrote it down, not
how often it happened.

This is not a classifier waiting to be written. It is the question the
stakeholder meeting exists to answer
(`inventory-tng-8sq`), and it should not be attempted from this data again.
