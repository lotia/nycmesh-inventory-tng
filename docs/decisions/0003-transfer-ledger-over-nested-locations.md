# 0003 — A transfer ledger over nested locations

**Status:** accepted · 2026-10-30 · amended 2026-11-01 by 0009 (an
unregistered device is blocked), 2026-11-03 (counts, retired items),
2026-11-06 (counting while stock moves), 2026-11-08 by 0016 (where the
rules live, balance), 2026-11-09 (stock at a retired place), 2026-11-11
by 0020 (sealed and open packages), 2026-11-15 (counting some items),
2026-11-18 (a count form's moment read without a lock)

## Context

Stock does not sit in one place. Volunteers take it from the store to
installs, and some of it rides in vehicles or stays with a volunteer for a
while. So we need more than one location from the start.

## Decision

- **A transaction is one batch.** It has an actor, a time, a kind, one *from*
  location, one *to* location, an idempotency key, and lines of item and
  positive quantity. It is written atomically: all lines or none. A line
  also says the form its stock leaves in and arrives in: a sealed package,
  an open package or loose (0020).
- **A NULL side means outside the system**: a vendor delivery coming in, or an
  install going out. A transaction going to an install can carry its MeshDB
  install number, so "what went up on that roof?" has an answer.
- **Locations nest**, for example store → area or shelf. Kinds include store,
  vehicle and volunteer custody: a volunteer holding stock is a location.
  A place that keeps spare stock, a hub included, is a store, and a store's
  stock counts towards reorder. Equipment installed anywhere, hubs included,
  has left the inventory: it moves to the NULL side.
- **The ledger is append-only.** Mistakes are fixed with a count or adjustment
  transaction, never by editing a row.
- **Balance is in minus out**, computed by a query (0016), per item, place
  and form (0020).
- **Negative balances are allowed.** They mean the records are wrong
  somewhere, so the item needs counting. A volunteer is never blocked.
- **Reorder is a separate signal**: usable stock in stores (all but short
  ends, 0020) at or below the minimum, and, where a package sets one, sealed
  packages below their own minimum. An item with a negative balance shows
  on reorder as "count first".
- **The rules are the app's**, checked by the one function that writes each
  table, with constraints and privileges in PostgreSQL (0016).
- **Counts and adjustments are an administrator's**, and record whose. A count
  of a place writes what differs, found or lost, and a count that matches is
  written with no lines, so when a place was last counted is known. A count is
  true for the moment its form was shown, so moves saved while it is under way
  still apply. An item that moved at the place meanwhile might have been
  counted before or after the move, so its line is not saved; the page asks
  for it again, and saves every other line (Ali, 2026-11-03). Showing the
  form holds up no save; one under way then that commits after refuses the
  whole count, keeping what was typed (amendment, 2026-11-18). A count asks
  for each form of an item separately, and may add or close an open package
  (0020). It also puts right the end at that place of a line recorded more
  than moved (0020). A count names each item once: a form naming one twice
  is refused, saying why, as it isn't clear which number is right (Ali,
  2026-11-08). A
  place's count page can show one item, or those whose names match a search
  as the items list searches; then only those are counted, and the rest stay
  as they are (Ali, 2026-11-15). An
  adjustment gives a reason from a list administrators extend, seeded with
  Damaged, Lost or stolen, Found, Used for testing and Records were wrong (Ali,
  2026-11-03), and Used up, for closing an open package (0020). A volunteer
  only reports an open package used up; an administrator closes it.
- **Nothing is deleted: retiring is the soft delete**, and bringing back
  undoes it. A retired item that still has stock, or a retired package
  with some still sealed, is never received again, but volunteers may take
  what is left, until it runs out (Ali, 2026-11-03; packages, 2026-11-11). A
  place is retired only once it holds nothing, but a save that waited in
  an outbox can still bring stock to it or take some from it, and is
  accepted. Such a save is logged and counted, and the place
  shows on the home page, beside Counts in the navigation and on the stock
  page, and can be counted or adjusted, until it holds nothing again (Ali,
  2026-11-09; inventory-tng-8i4.51.2).

We rejected keeping one quantity per item, because it cannot say where stock
is or how it got there.

## Consequences

- History is never lost, and every balance can be explained line by line.
- Fixing a mistake means adding a transaction, which takes a little more work
  than editing a cell.
- Volunteers can always record what they did, even when the numbers look
  wrong. Counting catches the error later.
- There is one store today, which receives the sheet's opening counts. A
  second store, or a hub, is just another store location.

## Amendment: a count form's moment, read without a lock (2026-11-18)

Decided in implementing inventory-tng-0um on 2026-11-18, for Ali to
confirm. Showing a count form took its place's lock for a moment, holding
up volunteers' saves there. It now reads, with no lock, the place's latest
ledger transaction and how many there it saw. A save under way then may
commit after with an id inside that moment. The count, holding the place,
counts the place's transactions up to the moment again, and if there are
more, it is refused whole. Which items that save moved cannot be told from
the form, so unlike an item moved meanwhile (Ali, 2026-11-03) no line is
saved: the form is shown again as the records are now, keeping what was
typed, to check and save again. The form, opened, reads its moment and
its figures on one snapshot, so a save committing as it is shown is in
both or in neither. Shown again after a send, it reads them apart, so a
save committing just then may refuse it once more, keeping what was typed.

We rejected carrying each line's figure shown (many fields, and it misses
counts by form), comparing PostgreSQL's transaction snapshots (exact, but
it ties the ledger to transaction ids' internals), and taking the lock
without waiting (it still holds up saves).
