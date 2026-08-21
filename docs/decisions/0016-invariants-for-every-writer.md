# 0016 — Invariants belong to every writer; affordances belong to the API

**Status:** accepted

## Context

Seven rules were enforced only by the serializers and the batch view. The API is
not the only thing that writes: the Django admin does, a fixture load does, and
the sheet importer that
[docs/data-model.md](../data-model.md#migrating-the-existing-sheet) plans will
write more rows in one run than volunteers have written in four years. None of
those passes through a serializer.

Every one of the seven needs something a `CheckConstraint` cannot see — another
table, the current time, or the row's own previous value — which is why they
ended up above the database rather than in it. Two of them had already been
copied into a second place (`Label.code` is refused in `LabelSerializer` *and*
in `LabelAdmin.get_readonly_fields`), which is what a rule with no home below
the API eventually costs.

The stakes are not the same for all seven. A receipt carrying a `from_location`
drains a warehouse that shipped nothing, and the ledger is append-only, so that
row stands until somebody works out what happened and compensates it. A client
choosing its own label code, by contrast, is a bad request and nothing more: the
row it would have created is one the admin is allowed to create deliberately.

## Decision

**Sort each rule by what it is about. A rule about which values may be stored is
the database's, and is a trigger. A rule about which caller may supply a value is
the API's, and stays a client contract.**

The test is whether the admin, writing deliberately, should be refused too. If
yes it is an invariant; if no it is an affordance, and no trigger could express
it anyway — there is no column recording which writer a value came from.

### Enforced for every writer, by trigger (migration 0008)

1. **A movement carries the sides its transaction's kind calls for.**
   `stock_movement_matches_kind`, the rule stated in
   [0011](0011-qr-batch-scanning.md#6-the-batch-endpoint-and-what-the-client-keeps)
   section 6. Cross-table — the movement's two columns against its parent's
   `kind` — and permanent, because the ledger cannot be edited.
2. **Nothing happened in the future**, for `StockTransaction.occurred_at` and
   `Label.revoked_at` alike. `inventory_reject_future_timestamp`. Not a check
   constraint because `now()` is not immutable.
3. **A volunteer named on a row is one the pick-list still offers** — the actor
   of a transaction, the holder of a custody location, the survivor a merge
   points at. `inventory_require_selectable_volunteer`, one function over three
   columns, because it is one rule (`VolunteerManager.selectable`). The merge
   case is the one that closes a hole: without it a merge chain can be built
   backwards, and two ordinary merges make a cycle.
4. **A label's code does not change once the row exists.**
   `label_code_is_printed`. The code is on a sticker on a shelf, and no database
   write can go and reprint it.

A fifth was added later by the same test:
[decision 0019](0019-retired-means-not-offered.md) refuses stock arriving at a
retired location (`stock_movement_to_location_is_active`, migration 0010). It
reads another table, and an administrator filling a decommissioned room should
be refused too, so it is a trigger. It is the one with no mirrored `400`,
because a client cannot reach it: the location pick-list does not offer a
retired row.

Each is checked **when the column is written**, not continuously. Retiring a
volunteer who already holds a custody location stays an ordinary act, and an
update that leaves the column alone — renaming a shelf — is not an occasion to
re-litigate a choice made earlier.

### Recorded as client contracts, enforced at the API

1. **A client does not choose a label's code.** The API mints it and refuses a
   submitted one. The admin supplies codes legitimately — it is another
   authorised writer, not a client — so the database cannot tell the two apart.
   What the database *can* hold, and does, is that whatever code is stored is
   Crockford Base32 (`label_code_is_crockford_base32`) and never changes
   afterwards. The half a trigger can hold is held; the half about who is asking
   cannot be.
2. **A client does not time a revocation.** It sends the boolean `revoked` and
   the server reads its own clock. A future date is refused below the API, but a
   plausible date supplied by a client is indistinguishable from one the server
   wrote — so the shape of the request, not a constraint, is what keeps the
   clock the server's.

## Consequences

- The admin is now refused things it used to allow: merging into a merged
  record, recording a batch for a retired volunteer, changing a printed code.
  Each was already impossible through the API, and each was a way to create data
  the API would then refuse to read back consistently.
- Serializer validators that mirror these triggers stay exactly as they were.
  They are *reporting*, in the sense `inventory/serializers.py` sets out: a
  volunteer with 24 scans needs to be told which line to fix, and a trigger can
  only refuse the write. The rule now has one owner and one reporter rather than
  one owner who is also the only enforcement.
- The two-copy problem in `LabelAdmin.get_readonly_fields` is no longer a second
  enforcement point, and reads as what it is: a form that does not offer a field
  the database will not let it save.
- Rows written before the migration are not examined. A trigger fires on writes,
  so a database already holding a row these rules would refuse keeps it until
  something writes it again. That is deliberate — the alternative is a migration
  that fails on production data at deploy time, and there is nothing to repair
  it to.
- Two rules are now written twice on purpose, and each pair is held together by
  a test rather than by care. `KIND_SIDES` in `views.py` and the trigger have to
  agree, and `test_the_database_permits_exactly_the_shapes_the_api_does` walks
  every kind against every shape to prove they do. `CLOCK_SKEW` in
  `serializers.py` and the interval in `inventory_reject_future_timestamp` are
  the other, and the pair only works one way round — a serializer that accepted
  more drift than the trigger allows would answer 201 to a batch the INSERT then
  refuses, reaching a volunteer with 24 scans as a 500 naming nothing.
  `test_the_database_allows_every_moment_the_api_does` is what fails instead.
