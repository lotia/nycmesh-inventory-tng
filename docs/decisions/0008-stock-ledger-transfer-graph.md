# 0008 — Stock as a double-entry transfer graph

**Status:** proposed — the schema is settled; one workflow question below is
open pending stakeholder input.

## Context

The system being replaced is a Google Sheet. Stock levels are computed by a
chain of `INDIRECT()` formulas over raw Google Form submissions, and an item is
found by looking its display name up in a catalogue. Analysis of the exported
sheet — 52 catalogued items and 3,439 submissions between July 2022 and August
2026 — shows what that costs:

- **145 distinct item strings for 52 items.** Some are typos, some are informal
  names, and 52 are a retired `NYCM-ER-LBEG2`-style SKU scheme abandoned in
  2022. A string the catalogue does not hold resolves to nothing, so those
  movements silently never reached a balance.
- **102 name spellings** (`sean`/`Sean`, `Lydon`/`lydon`/`Lydon Thorpe`,
  `JohnB`/`Johnb`/`johnb`), because the form asks for a name as free text on
  every submission, and a large share of submissions carry no email to
  disambiguate them.
- **2,455 check-outs against 984 check-ins.** Every printed QR code encodes a
  form link hardcoded to `Checking Out`, so returning stock costs strictly more
  effort than taking it. This is a bias in the instrument, not a measurement of
  behaviour: a check-in is also how a correction and a delivery are recorded, so
  **the column cannot be read as return behaviour in either direction**.
- **A large share of the ledger is corrections** — `fixing inventory`,
  `updating inventory`, `inventory correction` and similar. Volunteers fake
  check-ins and check-outs because there is no other way to say "the shelf
  disagrees with the sheet".
- **The notes field carries three unrelated things at once**: a reason, a job
  reference (an `NN` number), and a location — `Sean mesh room 131 broome st`
  and its many misspellings, alongside `apartment stock`, `returning equipment
  from apartment`, `Set aside for SN1`, `backup shelf` and
  `Moving inventory to basement`.

Four of those five need a rule before they can be counted: which strings count
as unresolvable, which notes are corrections, which name a place, which name a
job. Those rules are not documentation — each one is a decision the sheet
importer has to make about every historical row, so the rule and the number it
produces belong together and away from here. Both live in
[the sheet classifiers brief](../briefs/sheet-classifiers.md), which states each
rule and the command that emits its figure. Quoting a number from that brief
without the rule beside it is how this record previously came to carry several
that nobody could reproduce.

That last point is the important one. **Checking out is frequently not
consumption.** Hardware moves to a volunteer's home or a hub, sits there, and is
then either fitted at an install or brought back. The sheet can only record
"gone", so volunteers describe the real state in prose that nothing can query.

## Decision

Model every stock event as a **movement between two locations**, either of which
may be external (`NULL`), rather than as a signed counter against a single pool.

    checkout    131 Broome    -> custody:Sean    qty 3
    return      custody:Sean  -> 131 Broome      qty 1
    receipt     NULL          -> 131 Broome      qty 50
    install     custody:Sean  -> NULL            qty 2   job=NN217
    correction  131 Broome    -> NULL            qty 3   reason=cycle_count

A location is a self-nesting tree (site, room, shelf) and **a volunteer holding
stock is a location**. Balances are derived from the movements, never stored;
the formula and the view that implements it are in
[docs/data-model.md](../data-model.md#balances).

Supporting decisions, all settled:

1. **The ledger is append-only and is its own history.** Corrections are new
   compensating movements, never edits. A `BEFORE UPDATE OR DELETE` row trigger
   and a `BEFORE TRUNCATE` statement trigger raise on any attempt, so this is a
   guarantee rather than a convention. Both are needed: row triggers do not fire
   for `TRUNCATE`, which is how `manage.py flush` empties a table.
   (A trigger rather than revoking privileges: PostgreSQL superusers bypass
   privilege checks entirely and the postgres image makes `POSTGRES_USER` a
   superuser, so a `REVOKE` would have enforced nothing. Triggers fire for
   superusers too.) `django-simple-history` is applied to the catalogue tables
   only.
2. **Items are identified by rows, not by strings.** Manufacturer part numbers,
   vendor SKUs, the retired NYCM codes and informal aliases all become
   `ItemIdentifier` rows, so historical submissions resolve and a rename can
   never break a count.
3. **QR labels carry an opaque token**, not a display name, and can be revoked
   and reprinted without touching item identity.
4. **Stock counts are a first-class workflow.** A volunteer enters the quantity
   they physically found; the system computes the delta and posts it as an
   adjustment with a reason. Corrections stop masquerading as volunteer
   activity, which is what makes attribution reporting meaningful.
5. **Volunteers are a pick-list, not accounts.** No password; the device
   remembers the choice. Volunteers may add themselves, but the UI searches
   existing records first and administrators can merge duplicates. A merge sets
   `merged_into` on the duplicate and leaves the ledger untouched — it has to,
   since the ledger cannot be rewritten — and readers follow that pointer. See
   [docs/data-model.md](../data-model.md).

The full entity model is in [docs/data-model.md](../data-model.md); it is not
repeated here.

## Open question for stakeholders

**How much custody tracking does NYC Mesh actually want?**

The schema above supports full custody. Whether volunteers should be *asked to
use it* is a question about volunteer effort, not about database design, and the
project owner has asked for it to be settled with stakeholders before the
workflow is built.

The tension is that stock in a volunteer's apartment has two possible endings,
and only one of them is currently recorded anywhere:

- It **comes back to inventory** — cheap to record, since it is just a second
  scan in the opposite direction. How often this happens today is not
  knowable from the sheet, for the reason the Context gives.
- It **gets fitted at an install** and never comes back. Recording this is a
  second event that volunteers do not perform today, and is the entire cost of
  the proposal.

| Option | What volunteers do | What the system can answer | Cost |
| --- | --- | --- | --- |
| **A. Record consumption** | Check out; then either return it or mark it used at a job | "Who is holding what right now", plus a per-node bill of materials | One extra action per install |
| **B. Periodic reconciliation** | Check out and check in only; periodically confirm what they still hold | "Who is holding what", approximately and with lag | A recurring prompt; no per-node data |
| **C. Checkout means consumed** | Exactly what they do today | Only total stock on hand, as now | None |

**The schema is the same in all three cases.** Option C is Option A with no
custody locations created; Option B is Option A with a scheduled write-off. This
is deliberate: it means the decision can be deferred, and revisited later,
**without a migration of the ledger**. Nothing is being locked in by building
the transfer graph now.

The recommendation is **A**, on the grounds that "which volunteer has our
hardware" is the question the current system most conspicuously cannot answer,
and that a per-node bill of materials would be independently valuable. The
counter-argument is real and should be weighed by people who know the
volunteers: NYC Mesh runs on goodwill, an install is a bad moment to ask someone
to file paperwork, and a workflow that is skipped in practice produces custody
balances that drift upward and are worse than not tracking custody at all.

If stakeholders prefer B or C, this ADR is updated and the workflow is scoped
down. The entity model does not change.

## Consequences

- Balances are computed from an immutable log, so the "recalculate the whole
  sheet" problem is gone, and any historical balance is reconstructible.
- Movements are recorded in batches, which matches how the work already happens:
  76% of historical submissions fall inside a burst by one person within ten
  minutes, and the largest is 24 items. The current one-form-per-item design is
  the reason those bursts exist.
- Corrections, receipts, installs and genuine volunteer check-outs become
  distinguishable by construction, where today telling them apart means reading
  a free-text note and guessing — which is why the figures that rest on doing so
  live in a brief with the rule that produced them.
- More concepts than a counter. Locations and movement direction are things a
  contributor must understand before touching stock code, and this document plus
  [docs/data-model.md](../data-model.md) is where they learn them.
- The open question above must be closed before the check-out workflow is
  considered finished. It does not block schema work.
