# 0003 — A transfer ledger over nested locations

**Status:** accepted · 2026-10-30

## Context

Stock does not sit in one place. Volunteers take it from the store to
installs, and some of it rides in vehicles or stays with a volunteer for a
while. So we need more than one location from the start.

## Decision

- **A transaction is one batch.** It has an actor, a time, a kind, one *from*
  location, one *to* location, an idempotency key, and lines of item and
  positive quantity. It is written atomically: all lines or none.
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
- **Balance is in minus out**, computed by a database view.
- **Negative balances are allowed.** They mean the records are wrong
  somewhere, so the item needs counting. A volunteer is never blocked.
- **Reorder is a separate signal**: total stock in stores at or below the
  minimum. An item with a negative balance shows on reorder as "count first".
- **The rules live in PostgreSQL**, as constraints and triggers, so every
  writer obeys them.

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
