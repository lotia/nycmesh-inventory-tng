# Data model

The entity model for inventory. **Why** stock is modelled as a transfer graph
rather than a running total is in
[decision 0008](decisions/0008-stock-ledger-transfer-graph.md), which also
carries one open question about how much custody tracking to ask volunteers for.
That reasoning is not repeated here.

Status: implemented in `backend/src/inventory/models.py`, including the stock
ledger and the derived balance view. Which endpoints exist over it, and what
they take and return, is the generated schema's to say — see
[The API schema](../DEVELOPERS.md#the-api-schema); what is *not* built is in
[architecture.md](architecture.md#not-yet-built). The batch write over the
ledger is designed in
[decision 0011](decisions/0011-qr-batch-scanning.md#6-the-batch-endpoint-and-what-the-client-keeps).

## Shape

    Volunteer ─────────┐                Category ──< Item ──< ItemIdentifier
      display_name     │                              │
      email?           │                              ├──< Label (quantity)
      slack_id?        │                              │
      merged_into? ────┘ (self)                       └──< VendorOffer >── Vendor
                                                            url, unit_price
    Location (self-nesting tree)                            units_per_order
      name, kind, parent?                                   observed_at
      held_by? ──> Volunteer

    StockTransaction ──< StockMovement
      actor ──> Volunteer               item ──> Item
      kind, reason                      quantity
      job_reference?                    from_location? ──> Location
      occurred_at                       to_location?   ──> Location

## Entities

### Volunteer

A person who moves stock. Deliberately **not** `django.contrib.auth.User`:
under a hundred people transact, of whom a handful need administrative access.
(Between 59 and 72 of them, a range rather than a number for reasons
[§6 of the classifiers brief](briefs/sheet-classifiers.md#6-person-to-volunteer)
states beside the rule that produces it. The argument here holds at either end:
both are small.) There is no link between the two models; administrators have a
`User` (how they come by one is
[decision 0013](decisions/0013-administrator-sign-in.md)) and a `Volunteer` row
if they also move stock.

| Field | Notes |
| --- | --- |
| `display_name` | Required. Trigram-indexed for search-first deduplication |
| `email`, `slack_id` | Optional, unique where present (partial unique index) |
| `active` | Retires a volunteer without deleting their ledger history |
| `merged_into` | Self-FK. Set when an administrator merges a duplicate, and must point at a record the list still offers |

Volunteers may add themselves from the UI, and the form searches existing
records before offering to create one.

Merging sets `merged_into` on the duplicate and changes nothing else. Ledger
rows keep pointing at whichever volunteer row was recorded at the time — they
have to, because the ledger is append-only and cannot be rewritten — so a
reader that wants "everything Sean ever moved" follows `merged_into` forward
from every row it finds. Nothing is lost and nothing is edited.

### Location

Where stock physically is, as a self-nesting tree: a site contains rooms,
a room contains shelves. `kind` is one of `warehouse`, `hub`, `room`, `shelf`,
`volunteer_custody`, `vehicle`.

A volunteer holding stock **is** a location, with `held_by` pointing at them. A
check constraint enforces that `held_by` is set if and only if
`kind = volunteer_custody`, and a trigger enforces that whoever it names is
still offered by the pick-list — a custody location attached to a merged
duplicate is the second generation of the duplicate the merge removed. Merging
or retiring somebody who *already* holds one is refused, naming the location,
and so is bringing such a location back once its holder is gone —
`VolunteerDetailSerializer.validate` says why neither is repaired instead. External sources and sinks — a vendor shipment
arriving, hardware fitted at an install — are represented by a `NULL` location
on one side of a movement, not by a row.

### Item, ItemIdentifier, Category

`Item` is the catalogue entry, not the stock. It carries `unit_of_measure`,
`minimum_stock` (the reorder point), `reorder_quantity`, and `attributes` as
JSONB.

**Current stock is never stored on the item.** It is derived from the ledger.

`ItemIdentifier` is the fix for name-matching brittleness. Every string that has
ever referred to an item — manufacturer part number, vendor SKU, the retired
`NYCM-ER-LBEG2` codes, and informal aliases such as `tp link` — is a row, unique
on a normalised form of the value. A scan or a typed string resolves to exactly
one item, and renaming an item cannot break a count.

### Label

Maps an opaque printed token to the thing it names, so that a faded or damaged
label can be revoked and reprinted without touching item identity. Fields:
`code`, `item`, `location`, `quantity`, `printed_at`, `revoked_at`.

`code` is minted by the server, never supplied by a client: ten characters of
Crockford's Base32 — the digits and the uppercase letters less `I`, `L`, `O` and
`U` — drawn from `secrets`. The alphabet and the length are a check constraint
rather than a rule the minter is trusted to keep, because a code containing one
of the excluded letters folds to a string matching nothing and would be
unresolvable for the life of the object carrying it. It cannot change once the
row exists either — the code is on a sticker on a shelf, and no write can go and
reprint it — while everything else about a label stays editable, so a correction
is cheaper than a reprint. `revoked_at` cannot be dated in the future. Why that
alphabet, and what is printed around the code on the sticker, is in
[decision 0011](decisions/0011-qr-batch-scanning.md#3-the-printed-label-an-uppercase-url-wrapping-an-opaque-code).

`quantity` is what one scan of that token means, in the unit of the item the
label names. It defaults to `1` and must be positive where it is present, and
`label_quantity_iff_item` requires it exactly when the label names an item: a
wall code stands for no quantity of anything, so it carries `NULL` rather than
a sentinel every reader has to interpret. Why the multiplier belongs on the
label rather than on the item is in
[decision 0011](decisions/0011-qr-batch-scanning.md#5-one-scan-is-not-one-unit-label-carries-the-quantity-it-represents).

A label points at an item *or* a location, held as two nullable foreign keys
with a check constraint requiring exactly one. A generic relation would express
the same thing, but the database could not then enforce it and the columns
would lose their types — normalisation is worth more here than the flexibility
of pointing a label at some future third kind of target.

This replaces the current scheme, in which the QR encodes a Google Form URL with
the item's display name embedded in a query parameter.

### Vendor, VendorOffer

A vendor's listing for an item: `url`, `unit_price`, `units_per_order`,
`observed_at`, `is_preferred`. This absorbs the sheet's `url`,
`Alternate URL 1` and `Alternate URL 2` columns along with the price comparisons
written into free-text notes (`134.44 streakwave / 131.41 VodaNet / 65.00
Baltic`), which are a repeating group and therefore a table.

Prices are timestamped rather than overwritten, so a historical purchase price
remains recoverable.

### StockTransaction and StockMovement

`StockTransaction` is the batch header — one scanning session produces one row.
It records the `actor`, `occurred_at`, `kind`
(`checkout` | `checkin` | `receipt` | `consumption` | `transfer` | `adjustment` |
`count`), an optional `reason` and `job_reference`, and an `idempotency_key` so
that a phone retrying on a poor connection cannot double-post a batch.

`StockMovement` is a line: an `item`, a positive `quantity`, and a
`from_location` and `to_location`, either of which may be `NULL` to represent
somewhere outside the system.

Invariants, enforced by check constraints rather than application code:

- at least one of `from_location` and `to_location` is set;
- `from_location` and `to_location` differ;
- `quantity` is greater than zero — direction is expressed by which side the
  location sits on, never by the sign of the number.

Four more are triggers rather than check constraints, because each needs
something a constraint cannot see — the parent row, another table, or the
current time:

- a movement carries the sides its transaction's `kind` calls for, and not the
  ones it forbids (the rule is
  [decision 0011](decisions/0011-qr-batch-scanning.md#6-the-batch-endpoint-and-what-the-client-keeps)
  section 6);
- `occurred_at` is not in the future;
- `actor` is a volunteer the pick-list still offers — neither merged nor
  retired;
- `to_location` is a location the pick-list still offers, for the reason
  [decision 0019](decisions/0019-retired-means-not-offered.md) gives.

Why these live in the database at all, and which related rules deliberately do
not, is [decision 0016](decisions/0016-invariants-for-every-writer.md).

### Balances

    balance(item, location) = SUM(quantity WHERE to_location = location)
                            - SUM(quantity WHERE from_location = location)

Exposed as a plain database view. At the observed volume — 3,439 movements over
four years — this is trivially fast, and the previous system's slowness came
from `INDIRECT()` chains rather than from arithmetic. If it ever becomes a
bottleneck it can become a materialised view without any change to the ledger.

An item is low on stock when its balance falls below `minimum_stock`. No extra
schema is required for the low-stock alerting the volunteers have asked for.

## Units of measure

Quantities are `Decimal(12,3)` and each item declares its unit. Cable is
genuinely measured, not counted: ToughCable is tracked in metres and indoor
Cat 6 in feet, while heatshrink is counted in 100-foot rolls. All historical
quantities happen to be whole numbers, so decimals cost nothing today and avoid
a migration the first time somebody cuts half a metre.

The sheet stores one `units per order` value as the string `100 ft`; the
migration is responsible for resolving values like this into a number and a
unit.

## Where PostgreSQL-specific features are used

Each of these is here because a portable alternative is materially worse, not
for its own sake.

| Feature | Used for | Why |
| --- | --- | --- |
| `JSONB` + GIN | `Item.attributes` | Radios, cable, connectors, fibre and hand tools have genuinely disjoint specifications. The alternatives are EAV or a wide sparse table, both worse |
| `JSONB` | `StagedCatalogueRow.source`, `StagedSubmissionRow.source` | A spreadsheet row is whatever cells it had that day, and a column per cell would be a schema asserting a shape the export does not promise |
| `pg_trgm` + GIN | `Volunteer.display_name` | Fuzzy search at the moment of self-registration is what prevents a second generation of the sheet's 102 spellings |
| Partial unique indexes | `email`, `slack_id`, custody locations | Uniqueness that only applies to non-`NULL` rows, and one custody location per volunteer |
| Check constraints | Movement invariants, `held_by`, self-parent | Invariants that must hold regardless of which client wrote the row |
| `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` triggers | Ledger tables | Makes append-only a property of the database, not a rule contributors must remember. Both are needed: a row trigger cannot see `TRUNCATE`. Why a trigger rather than `REVOKE` is in [decision 0008](decisions/0008-stock-ledger-transfer-graph.md) |
| `BEFORE INSERT` and `BEFORE UPDATE` triggers | Movement shape and a batch's actor and date (insert only, the ledger being append-only); a label's code (update only, since a code is chosen once); selectable volunteers and future revocations elsewhere (both) | Invariants that need another table, the current time, or the row's previous value, so a `CheckConstraint` cannot express them. Which rules are here and which stayed at the API is [decision 0016](decisions/0016-invariants-for-every-writer.md) |
| `GENERATED ... STORED` | `ItemIdentifier.value_normalised` | Normalisation cannot drift between the write path, the importer and the scan endpoint if the database computes it |
| `NULLS NOT DISTINCT` | Category and Location names | A unique constraint on `(parent, name)` otherwise does nothing at the top level, where `parent` is NULL |

`JSONB` is deliberately **not** used for anything queried relationally.
Vendors, prices, locations and identifiers are all tables.

## Migrating the existing sheet

The export is small — a few dozen items and a few thousand submissions, not the
15,000 rows originally assumed. The measured breakdown is in
[decision 0008](decisions/0008-stock-ledger-transfer-graph.md#context) and is
not repeated here; the rules needed to read the free-text field, and the figures
each one produces, are in
[the sheet classifiers brief](briefs/sheet-classifiers.md). The work it implies:

- **Item strings must be resolved, not matched.** Every string that has ever
  named an item becomes an `ItemIdentifier`, including the ones that match
  nothing today and the retired NYCM codes.
- **People must be deduplicated**, across both name spellings and missing email
  addresses.
- **Intent must be recovered from the notes field**, which conflates reason,
  job reference and location. Corrections are a large share of the rows and
  must import as adjustments, not as check-outs by a volunteer.
- **Locations must be extracted** from that same field.
- Every imported row keeps its raw source as JSONB for provenance, so the import
  can be re-run and audited.

`manage.py stage_sheet` is the first of those steps, and the only one that
opens the workbook. It writes both tabs into `StagedCatalogueRow` and
`StagedSubmissionRow`, keyed on the row number the spreadsheet shows, each row
carrying its cells as JSONB beside what the reader made of them. Everything
after it works from those tables, so a rule that changes is re-applied by
somebody who has a database rather than only by somebody holding an export
that is not ours to publish. Running it again makes the tables equal the
export once more, which includes dropping a row the export has lost.

Neither of those tables is part of the entity model above and neither lives
with it: `backend/src/inventory/staging.py` holds them and says why, including
why a staged timestamp is text.

Anything genuinely unresolvable imports against a placeholder item and
volunteer, flagged for cleanup, rather than being silently dropped.
