# Data model

The entity model for inventory. **Why** stock is modelled as a transfer graph
rather than a running total is in
[decision 0008](decisions/0008-stock-ledger-transfer-graph.md), which also
carries one open question about how much custody tracking to ask volunteers for.
That reasoning is not repeated here.

Status: implemented in `backend/src/inventory/models.py`, including the stock
ledger and the derived balance view. No API endpoints expose them yet.

## Shape

    Volunteer ─────────┐                Category ──< Item ──< ItemIdentifier
      display_name     │                              │
      email?           │                              ├──< Label
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
around 65 people transact, of whom a handful need administrative access. There
is no link between the two models; administrators have a `User` for the Django
admin and a `Volunteer` row if they also move stock.

| Field | Notes |
| --- | --- |
| `display_name` | Required. Trigram-indexed for search-first deduplication |
| `email`, `slack_id` | Optional, unique where present (partial unique index) |
| `active` | Retires a volunteer without deleting their ledger history |
| `merged_into` | Self-FK. Set when an administrator merges a duplicate |

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
`kind = volunteer_custody`. External sources and sinks — a vendor shipment
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
`code`, `item`, `location`, `printed_at`, `revoked_at`.

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
| `pg_trgm` + GIN | `Volunteer.display_name` | Fuzzy search at the moment of self-registration is what prevents a second generation of 102-spellings-for-65-people |
| Partial unique indexes | `email`, `slack_id`, custody locations | Uniqueness that only applies to non-`NULL` rows, and one custody location per volunteer |
| Check constraints | Movement invariants, `held_by`, self-parent | Invariants that must hold regardless of which client wrote the row |
| `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` triggers | Ledger tables | Makes append-only a property of the database, not a rule contributors must remember. Both are needed: a row trigger cannot see `TRUNCATE`. Why a trigger rather than `REVOKE` is in [decision 0008](decisions/0008-stock-ledger-transfer-graph.md) |
| `GENERATED ... STORED` | `ItemIdentifier.value_normalised` | Normalisation cannot drift between the write path, the importer and the scan endpoint if the database computes it |
| `NULLS NOT DISTINCT` | Category and Location names | A unique constraint on `(parent, name)` otherwise does nothing at the top level, where `parent` is NULL |

`JSONB` is deliberately **not** used for anything queried relationally.
Vendors, prices, locations and identifiers are all tables.

## Migrating the existing sheet

The export is small — a few dozen items and a few thousand submissions, not the
15,000 rows originally assumed. The measured breakdown is in
[decision 0008](decisions/0008-stock-ledger-transfer-graph.md#context) and is
not repeated here. The work it implies:

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

Anything genuinely unresolvable imports against a placeholder item and
volunteer, flagged for cleanup, rather than being silently dropped.
