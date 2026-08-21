# 0019 — `active=False` means not offered, not not-there

**Status:** accepted

## Context

`Item.active` and `Location.active` retire a row without deleting it, because
[the ledger refers to it](0008-stock-ledger-transfer-graph.md) and cannot be
rewritten. Nothing said what retirement means for stock the retired row still
holds, and the two halves of the API answered differently by omission:

- **Reading.** `/api/items` and `/api/locations` hide retired rows, which is
  right — they are pick-lists, and a retired item is not something to add to a
  cart.
- **Writing.** `POST /api/stock/transactions` filters the actor to a selectable
  volunteer and then accepts any `item`, `from_location` and `to_location`. So
  stock could be moved *into* a decommissioned room, and it would reappear in
  `inventory_stock_balance` under a row nothing offers.

The two directions are not one question. Draining a location while
decommissioning it is the legitimate act; filling one is not.

## Decision

**Retirement is a statement about the pick-list, not about the world.** A
retired row is not offered for new work. It keeps whatever stock it holds, and
that stock stays countable and movable *out*.

Three rules follow.

1. **Stock may leave a retired row and may not arrive at one.**
   `stock_movement_to_location_is_active` refuses a movement arriving at a
   location with `active = false`, or under an item with `active = false`. It
   is a rule about which values may be stored and it reads another table, so by
   [0016](0016-invariants-for-every-writer.md) it is a trigger and binds the
   admin and the importer as well as the API.

   `from_location` is deliberately unconstrained: emptying the room is how it
   gets decommissioned, and draining a retired item is how its last stock
   leaves.

   The item half is not a separate rule. A receipt is pure arrival with no
   from-side, so "draining is legitimate, filling is not" does not distinguish
   an item from a location: 40 units received against a retired item is a
   balance `/api/items` never shows, which is exactly what this rule exists to
   prevent.

2. **Adjustments and counts arrive anywhere.**
   [0011](0011-qr-batch-scanning.md#6-the-batch-endpoint-and-what-the-client-keeps)
   section 6 makes them the one claim this API must never argue with, and the
   reason applies with more force here than elsewhere: finding three of a
   retired item on a shelf has to be recordable, or the stock this decision
   cares about is precisely the stock nobody can reconcile. Retirement must not
   strand what it retires — the defect
   [`inventory-tng-ai0`](0008-stock-ledger-transfer-graph.md) fixed for a
   volunteer's custody location.

3. **A retired row is reachable for a count, and not by guessing.**
   `?withdrawn=true` on either collection lists what that collection has taken
   out, for administrators — the affordance already added for repairing a
   retirement. That is the read half of this decision, and it needed nothing
   new.

## Consequences

- Decommissioning a room is: move the stock out, then retire it. Retiring it
  first still works and leaves the stock reachable, because the room only stops
  being *offered*.
- A balance under a retired row is a real balance and a piece of work, not a
  bug. Something has to surface those — the same argument as
  [`inventory-tng-ayu`](0008-stock-ledger-transfer-graph.md) makes for open
  custody, and it is not built here.
- The importer inherits rule 1. If the sheet's history moves stock into a room
  that is retired by the time the import runs, the import fails loudly rather
  than writing a row nobody can see. Whether historical rows should be exempt
  is `inventory-tng-2dg`'s to decide, and this record does not decide it.
- What retirement means to a volunteer is still unmeasured. If they retire an
  item to mean "stop ordering this" rather than "this is gone", rule 1's item
  half will feel like an obstruction on the day a delivery of it arrives, and
  the answer is then to un-retire it rather than to weaken the rule.
