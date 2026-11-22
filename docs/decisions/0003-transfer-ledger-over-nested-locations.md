# 0003 — A transfer ledger over nested locations

**Status:** accepted · 2026-10-30 · amended 2026-11-01 by 0009 (an
unregistered device is blocked), 2026-11-03 (counts, retired items),
2026-11-06 (counting while stock moves), 2026-11-08 by 0016 (where the
rules live, balance), 2026-11-09 (stock at a retired place), 2026-11-11
by 0020 (sealed and open packages), 2026-11-15 (counting some items),
2026-11-18 (a count form's moment read without a lock), 2026-11-19 (a
count's moment stays a ledger number), 2026-11-19 and 2026-11-20 (a
retired package with none left is never taken), 2026-11-21 (a store
counted with its areas)

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
  somewhere, so the item needs counting. A move that takes stock below
  zero is taken from every client, the scan page included, as none can
  know what others moved meanwhile, with one exception: a retired package
  with none sealed left (amendment below: a retired package with none
  left is never taken).
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
  as they are (Ali, 2026-11-15). A place with areas is counted with
  every area in it on one form, each line written at its own place
  (amendment below). An
  adjustment gives a reason from a list administrators extend, seeded with
  Damaged, Lost or stolen, Found, Used for testing and Records were wrong (Ali,
  2026-11-03), and Used up, for closing an open package (0020). A volunteer
  only reports an open package used up; an administrator closes it.
- **Nothing is deleted: retiring is the soft delete**, and bringing back
  undoes it. A retired item that still has stock, or a retired package
  with some still sealed, is never received again, but volunteers may take
  what is left, until it runs out (Ali, 2026-11-03; packages, 2026-11-11),
  and then never take that package sealed again (same amendment). A
  place is retired only once it holds nothing, but a save that waited in
  an outbox can still bring stock to it or take some from it, and is
  accepted, but for that amendment's exception. Such a save is logged and
  counted, and the place shows on the home page, beside Counts in the
  navigation and on the stock page, and can be counted or adjusted, until
  it holds nothing again (Ali, 2026-11-09; inventory-tng-8i4.51.2).

We rejected keeping one quantity per item, because it cannot say where stock
is or how it got there.

## Consequences

- History is never lost, and every balance can be explained line by line.
- Fixing a mistake means adding a transaction, which takes a little more work
  than editing a cell.
- Volunteers can record what they did even when the numbers look wrong,
  but for taking sealed a retired package with none left. Counting catches
  the error later.
- There is one store today, which receives the sheet's opening counts. A
  second store, or a hub, is just another store location.

## Amendment: a count form's moment, read without a lock (2026-11-18)

Decided in implementing inventory-tng-0um on 2026-11-18; Ali confirmed it
on 2026-11-19, preferring it to the lock and to asking again line by line.
Showing a count form took its place's lock for a moment, holding
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
counts by form), comparing PostgreSQL's transaction snapshots (the next
amendment says why), and taking the lock without waiting (it still holds
up saves).

## Amendment: a count's moment stays a ledger number (2026-11-19)

Decided by Ali on 2026-11-19 (inventory-tng-8i4.65): a count's moment
stays the place's latest ledger number, kept true by place locks (a save
holds its places before it draws its number; a count locks its own), not
PostgreSQL's transaction snapshots (`xid8`). Snapshots would keep both
locks and remove only the wait as a count form opens, which the
amendment above already removed. They would add another PostgreSQL-only
feature (0016), a hidden snapshot field to validate, a check no index can
serve, and rewritten concurrency tests.

A known limit, accepted: production runs on CloudNativePG, which
replicates asynchronously unless set to wait for a replica. A failover
can lose the last few commits and the ledger numbers they took, so the
new primary may hand those numbers out again. A count form opened before
the failover could then quietly absorb a move saved after it; a later
count of the place puts it right.

## Amendment: a retired package with none left is never taken (2026-11-19)

Decided by Ali on 2026-11-19 (inventory-tng-m5y, built under
inventory-tng-3yo), and on 2026-11-20 that the scan page is a client like
any other, given nothing apart by the server (inventory-tng-o7i2). A move
that takes stock below zero is taken because no client can know what
others moved meanwhile, not as a promise to one client. One case is
refused, for every client: a move, install or opening that takes sealed a
retired package with none left sealed at any current place, nor at the
retired place it may be taken from (as above). The server
refuses it as it refuses a receipt of a retired package: the whole batch
is answered 422, naming the line's package (`retired_package_none_left`),
and nothing is saved. A batch the scan page saved offline before another
phone took the last one is refused the same way, and its outbox says why.
An administrator's count or adjustment may still find or lose some. Two
saves taking the last of one go one at a time, so the second finds none
left and is refused.

The other cases that take stock below zero stay as above, for every
client: a retired item with none left, or an item that never had stock at
a place, is taken and shows on reorder as "count first".

## Amendment: a store counted with its areas (2026-11-21)

Decided by Ali on 2026-11-21 (inventory-tng-8i4.51.1, built as
inventory-tng-8i4.51.4). A place's count page shows its own stock and that
of every area inside it, at any depth, grouped by area. Each line is
written at the place it is listed under: one count at each place, under a
key made from the form's and the place's id, so the ledger and when each
place was last counted stay per place. Every area is shown, an empty one
marked as holding nothing but still able to take what is found there, and
a retired one holding stock marked retired, so nobody puts anything there
again. There is no depth or size limit. A line stays with its area: stock
found in another area than the records say is counted there as missing
and added where it was found, a loss and a find, not a move. Something
else here and an open package nobody recorded are asked once for the whole
form, as on a count of one place, each beside the area it was found in;
the area picked comes back picked after the save. Something else here
naming an item the area picked already lists is refused, saying the item
is listed under that area and to type what was found on its line there;
nothing is saved and everything typed is kept (Ali, 2026-11-21,
inventory-tng-fh0e). We rejected hiding the items an area lists from the
picker as Found in changes, and adding the amount to the line, which could
count it twice without anyone noticing. So too on a recount, which asks
again only for the items that moved while it was counted: Something else
here naming an item the place picked held then, but the recount does not
ask for, is refused the same way, and the form shown again adds that
item's line under the place (Ali, 2026-11-22, inventory-tng-oazl). We
rejected adding the amount to what the place holds, and leaving the items
a place holds out of the picker. A count showing only some items
counts only the places something was typed at; sent with nothing typed
anywhere, nothing is recorded and the form is shown again, with the same
items, saying "Nothing was typed, so nothing was counted." (Ali,
2026-11-21, inventory-tng-fxsy). We rejected going back to Counts with no
message, and recording an empty count at each place. A form saved and
sent again, such as by going back after the save, finding the open package
nobody recorded at another place, another package, or one where the first
send found none, writes nothing more and goes back to Counts saying "This
count was saved already, so what you changed wasn't saved. To record it,
start a new count." (Ali, 2026-11-22, inventory-tng-53im). We rejected a
fresh form with a new key keeping what was typed, as anything the first
save found would be found a second time.

The form lists the places it shows, so an area moved meanwhile only
regroups. Its moment is read across them all, and the count holds every
one, in id order, as saves take them (inventory-tng-8i4.51.3), then the
items, packages and open packages the form names, each by id across them
all, and the checks of recorded more than moved it may settle, before
writing any (inventory-tng-nfia). A move between two of them while
counting withholds the item at both, and the recount asks for it place
by place. A send naming an area retired since it was shown, or a retired
area emptied since, is refused whole, keeping what was typed, and the
form is shown again without it (Ali, 2026-11-22, inventory-tng-ygk5).
One emptied is said so, and what left it while it was counted is shown
under "Moved from Shelf C to Shelf B", with an empty box, counted at the
place it went to and shown only there, or listed under "Taken out of"
the store if it went elsewhere. Found at leaves the area out, saying it
is retired, and a "Bring it back" button on the form brings it back, as
its own page does, and shows the form again with it, everything typed
kept. A recorded move is never undone: if the items never left, the
count records a loss where they went and a find where they are. One
retired with nothing moved is said so, its heading dimmed. We rejected
one message for both cases with what came in and went out only, and a
short message without the reason.

We rejected counting the store as a whole (where in it stock is would be
lost, and the areas' own counts would fight the store's) and a read-only
list of each area's stock linking to its own count (it saves no work at
the shelf). A "found in another area" choice that records a move was
left out for now: revisit it if counters find a loss and a find awkward.
