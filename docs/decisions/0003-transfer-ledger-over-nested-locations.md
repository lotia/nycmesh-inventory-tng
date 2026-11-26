# 0003 — A transfer ledger over nested locations

**Status:** accepted · 2026-10-30 · amended 2026-11-01 by 0009 (an
unregistered device is blocked), 2026-11-03 (counts, retired items),
2026-11-06 (counting while stock moves), 2026-11-08 by 0016 (where the
rules live, balance), 2026-11-09 (stock at a retired place), 2026-11-11
by 0020 (sealed and open packages), 2026-11-15 (counting some items),
2026-11-18 (a count form's moment read without a lock), 2026-11-19 (a
count's moment stays a ledger number), 2026-11-19 and 2026-11-20 (a
retired package with none left is never taken), 2026-11-21 (a store
counted with its areas), 2026-11-23 (work saved as it was shown),
2026-11-24 (a retired item counted to zero), 2026-11-24 (every stored
amount records its unit), 2026-11-24 (m and ft converted after stock has
moved), 2026-11-25 (the guided recount of a unit wrong from the start)

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
sent again, such as by going back after the save, saying anything
otherwise (other figures, the open package nobody recorded found
elsewhere, otherwise or not at all, an open package closed, a place the
first send left out now typed at, a move's amount fixed) is refused
whole: nothing is written, and it goes back to Counts saying "This count
was saved already, so what you changed wasn't saved. To record it, start
a new count." (Ali, 2026-11-22, inventory-tng-53im, inventory-tng-kro9).
Sent as it was, it is answered as before. The save keeps a digest of what
the form said under its key to tell the two apart. We rejected a fresh
form with a new key keeping what was typed, as anything the first save
found would be found a second time, and leaving a changed send to say
Counted though nothing more was saved.

The form lists the places it shows, so an area moved meanwhile only
regroups. Its moment is read across them all, and the count holds every
one, in id order, as saves take them (inventory-tng-8i4.51.3), then the
items, packages and open packages the form names, each by id across them
all, and the checks of recorded more than moved it may settle, before
writing any (inventory-tng-nfia). A move between two of them while
counting withholds the item at both, and the recount asks for it place by
place. A send naming an area retired since it was shown, or a retired area
emptied since, is refused whole, keeping what was typed, and the form is
shown again without it (Ali, 2026-11-22, inventory-tng-ygk5). One emptied
is said so, and what left it while it was counted is shown under "Moved to
Shelf B", a part for each area it came from ("From Shelf C"), each item
with the amount moved and "Fix amount", or listed under "Taken out of" the
store if it went elsewhere. A move left alone is taken as right and writes
nothing; "How many arrived?", fixed lower or higher, records the
difference as lost or found where it went, never against the area it left.
It is fixed in the forms the move arrived in: sealed packages by how many,
whole, and one box for each form when it arrived partly sealed and partly
loose, as the count form's own lines split them, so a fix never takes
loose stock below nothing (the coordinator, 2026-11-22, for Ali to
confirm). The place's own lines count what it held before the moves, which
are set aside from what its records say, in the forms it holds them in,
never more than it holds: a sealed package opened there since is set aside
from what opening it made, an open package its lines then leave out or
loose units, and what was used or has gone from there since from nothing.
A fix lowering a move is lost from those forms too, never more than is
still there of it (2026-11-23, inventory-tng-nlog). Under the item, a
muted line for each package of it opened there since, before the form's
moment, says what is left of it then: "Opened here since: 250 m left.";
with none left, where the last of it went, as the ledger line that took
it says: "Opened here since, then moved to Shelf A." for another place on
the form, "Opened here since, then taken out of Supernode 1." for one
elsewhere, else "Opened here since, then used up." The box still asks for
sealed packages. We rejected asking for the open
package as it is now, and no "Fix amount" for an opened arrival (Ali,
2026-11-23, inventory-tng-0fbn). A line under the heading, in the browser
only, gives what it will hold once saved. Found at leaves the
area out, saying it is retired, and a "Bring it back" button on the form
brings it back, as its own page does, and shows the form again with it,
everything typed kept. A recorded move is never undone: if the items never
left, "How many arrived?" says none, and what is at the area is found
there. One retired with nothing moved is said so, its heading dimmed; one
emptied and another retired in one send show both alerts. We rejected one
message for both cases with what came in and went out only, a short
message without the reason, a box to count the moved items again, yes or
no buttons per move, an Arrived box always shown, and "Correct" or "Fix
count" for the button (Ali, 2026-11-22).

We rejected counting the store as a whole (where in it stock is would be
lost, and the areas' own counts would fight the store's) and a read-only
list of each area's stock linking to its own count (it saves no work at
the shelf). A "found in another area" choice that records a move was
left out for now: revisit it if counters find a loss and a find awkward.

## Amendment: work saved as it was shown (2026-11-23)

Decided by Ali on 2026-11-23 (inventory-tng-jwlh, built as
inventory-tng-57u8). An item's unit may change until its first movement,
and while no label of it or its packages is current; a package's size
until it has moved or been opened, and while no label of it is current:
exactly when its first saves may still be on their way, such as a phone's
outbox holding a receipt of 50 m of a new cable when an administrator
switches it to ft. So every save says the units and package sizes it
showed: each line of a scan page's batch its item's unit, as it already
said its package's size, and so each of its reports of what is on an
open package (inventory-tng-fs50); a count form the unit of every item and the size
of every package it shows; an adjustment the unit of every item it offers;
an open package's count confirmed its item's unit; on an item's page, a
package added its item's unit, and a too-short length typed its package's
(inventory-tng-74f3). The save compares them
with the catalogue while holding the items and packages, as it does to
write, so no change slips between. One changed since is refused, nothing
written, and asked again in the new ones: the batch's line is refused
(`unit_changed`, as `package_resized`) for the outbox to say so, and a
form is shown again as it is now, saying beside the box "This item is now
counted in ft. Check the amount and save again." A form shown again
for anything else while keeping what was typed (Bring it back on a count,
a save refused by another rule) is shown in the units and sizes now
too, so it says the same beside each box of one changed since
(inventory-tng-dcqp), a box it could not read too. Bring it back that
brings the area back answers 200 even so, as it saved no amount for the
words to refuse (Ali, 2026-11-23, inventory-tng-itfm). We rejected converting
what was shown into what is now: a unit switched because it was wrong may
mean the volunteer's number was right in the new one, so asking again is
safer.

The scan page says so before Record too (Ali, 2026-11-23,
inventory-tng-h7ua): where its catalogue shows a line's item counted in
another unit than its amounts were typed in, any unit, it clears what was
typed and says beside the box "The unit changed from m to ft. Enter the
amount again in ft.", and refuses to record the cart until the amount is
typed again, as it does for a package resized. Record loads the catalogue
first and checks the cart against it; without an answer in a few seconds
it queues the batch as before, and the server's refusal still holds.
Rejected again: converting the amount, and saying so only after Record.
A cart changed while Record waits is not queued, and the page says so
(Ali, 2026-11-23, inventory-tng-h60m): changed in another tab, "The cart
was changed in another tab. Check it, then record it again."; changed in
this one, "The cart changed while it was being checked, so it wasn't
recorded. Tap Record again." Rejected: locking the cart while Record
checks, as another tab's change would still need saying, and the page
would feel frozen on a slow connection.

A label fixes an item's unit as a movement does (Ali, 2026-11-23,
inventory-tng-rpud): it says the unit, and a scan of it counts the
package's size, or one, in whatever unit the item has. So a "Spool 305 m"
label scanned after a switch to ft would receive 305 ft. A change is
refused. Making a label locks the item, as changing its unit does, so
neither slips past the other. Rejected: converting m and ft numbers
automatically, and allowing the change with a warning.

Only labels not revoked count (Ali, 2026-11-23, inventory-tng-37dj),
so a unit or size set wrong and labelled can be corrected: a revoked
label scans as no label, and work queued from it is refused as above.
Its page says why first, with how many labels: "each: fixed until both
its printed labels are revoked. Revoke its 2 labels to change it." (Ali,
2026-11-25, inventory-tng-62qs). The link goes to all the item's labels
on the labels page, a retired package's too, each not revoked offered
to revoke (Ali, 2026-11-23, inventory-tng-x30g); a package's size fixed
only by its labels says so beside it, with the same link. A movement still fixes
both for good, and an opening the size. Revoking takes the catalogue
lock, as a unit or size change does, so neither slips past the other.

A batch line must say its unit: one without is refused. It was required
at once, not added first and required in a later deploy (CONTRIBUTING.md,
The scan page), as nothing is deployed and Ali decided on 2026-11-15 that
earlier builds, their tabs and what they stored need not keep working.

## Amendment: a retired item counted to zero (2026-11-24)

Decided by Ali on 2026-11-24 (inventory-tng-a97r). A take of an item can
wait in a phone's outbox while an administrator retires the item, and is
still saved, as above, so a retired item can show less than nothing at a
place. A count of a place offers a retired item whose balance there is
not zero, positive or negative, so it can be counted to zero. The item
lists, the adjustment's on Counts and a count form's Something else
here, do not depend on the place: they offer a retired item whose
balance is not zero at any current place, in any form, as sealed and
loose that net to nothing are still to be counted. A retired item at
zero everywhere is not offered. Takes of a retired item stay allowed.
Rejected: refusing a take of a retired item that would go below zero.

## Amendment: every stored amount records its unit (2026-11-24)

Decided by Ali on 2026-11-24 (inventory-tng-2rlr, from the investigation
inventory-tng-hsq1). An amount stored apart from its item records its
unit beside it, rather than being read in whatever unit the item has now:

- A ledger line, in the unit it was entered in: the one its page showed
  (a batch line's, an adjustment form's, an open package's count
  confirmed in its line's unit), or, for an amount read from the records
  as a count's difference is, its item's.
- A package's size and too-short length, a vendor offer's units per
  order, what a volunteer said is on an open package, and how much more
  a line was recorded with than moved.
- An item's own reorder point and amount on order are in its unit, on
  the same row.
- A batch kept as sent, to answer it sent again, keeps the lengths as
  the page sent them, each with the unit it was shown in
  (inventory-tng-fs50).

The ledger writes a line only in its item's unit, as it holds the item.
Every sum of the ledger reads each line in its item's unit: a line
between m and ft converts as it is read (next amendment), and the
database refuses one between each and a length as it reads it, so no
sum mixes units unawares. Pages show each past
line in its own unit. While an item's unit may still change, its
packages and vendor offers change with it, their numbers as typed, each
a change of its own, so a package's form drawn before is refused.

The units were added by a new migration rather than by rewriting the
initial one, each filled from its item's, so a database already migrated
keeps its data (coordinator, 2026-11-25). The fill is exact, as an
item's unit is fixed once stock has moved in it.

## Amendment: m and ft converted after stock has moved (2026-11-24)

Decided by Ali on 2026-11-24 (inventory-tng-y50b, from the investigation
inventory-tng-hsq1). An item may change between m and ft after stock has
moved in it, or while a label of it is current, converting what is
recorded. Rejected: keeping the unit fixed once stock has moved.

- The item form offers only the other length, and asks "Were the
  recorded amounts really in m?", naming the unit it is in, with Yes and
  No. A change between each and a length still needs the item never to
  have moved or converted, and no current label, as above.
- Yes converts, in one transaction holding the item, its vendor offers
  and its open packages in the ledger's lock order: its stock, its
  Reorder at (as typed in the old unit), what is on order, its vendor
  offers' units per order, and what volunteers said is on its open
  packages. A save under way waits and is converted, or comes after and
  is refused as shown in the old unit, never written in one and read in
  the other; so is an answer from a length-checks page shown before.
  Refused, before it is asked, while a line of it recorded with more
  than was moved waits, as that check is shown in the line's unit; and
  on Yes if an amount converted would be too large to keep, or a
  package's size or too-short length too small or no longer below its
  size. A check raised after all, by a batch sent again or while the
  change ran, is corrected in the item's unit now, holding the item as
  the ledger does, and so is an open package's count confirmed in its
  line's unit: each end as the ledger reads that side of the line, by
  how many packages on a sealed side, else exactly (2026-11-25,
  inventory-tng-4fjq, inventory-tng-77eu).
- Yes revokes no labels, as none names the item's unit: an item label
  prints only its name, and a package label its own size and unit,
  which converting keeps (Ali, 2026-11-25, inventory-tng-8ema). This
  replaces his decision earlier that day that Yes revokes them, as that
  forced reprinting labels still true.
- No converts nothing. The note beside the unit says it is unchanged
  and, with one of the item's own amounts, what a change would
  recalculate, then links the way to put right a unit wrong from the
  start: the guided recount (next amendment), saying what it does; or,
  only labelled, revoking its labels (Ali, 2026-11-25,
  inventory-tng-aknv). It never says "converting". The amount is stock
  at a place if stock has moved and some is held, else a stored one. A
  retired item can't be changed until it is brought back, so its note
  says only that the unit is unchanged, with the example, and links
  nothing.
- Once converted, an item stays fixed (`Item.unit_converted`, set on
  Yes): every later change between m and ft is asked and converts on
  Yes, any number of times, and it never becomes counted each again.
  Revoking its labels does not free it, as a free change would keep its
  converted numbers in the other unit (Ali, 2026-11-25,
  inventory-tng-l26i). Rejected: a free change that converts silently,
  and keeping the free change as it was.
- Old ledger lines keep their unit; each sum converts them as it reads
  them. A foot is 0.3048 m exactly (`catalogue/unit_conversion.py`, in
  Python and SQL alike). Each side of a line is read apart (Ali,
  2026-11-25, inventory-tng-77eu): a side in sealed packages converts
  by its packages (below), and a side loose or in an open package
  converts exactly. A sum is rounded once, as amounts are kept
  (`QUANTITY_SCALE`), so 305 m loose on a shelf reads 1000.656 ft and
  a sum does not drift by its lines' roundings. So every form at every
  place nets as its lines say: loose units or an open package emptied
  before the change read nothing, not a part of a thousandth. An
  opening before the change gives the form it made the package's true
  length, so it no longer nets to nothing at its place: the item's
  total gains that part of a thousandth. A place's total, and what a
  count compares with, is the sum of its forms, each rounded once
  (2026-11-25, inventory-tng-8s2c). Rejected: accepting the drift for
  loose stock, and converting an opening exactly on both sides, which
  moves the part of a thousandth into the sealed form.
- A package keeps its size and too-short length in its own unit, so a
  "Spool 305 m" label stays true. Wherever stock moves, is counted or is
  sent to the scan page, a package's size is read in its item's unit, as
  kept: so many 305 m spools are so many times 1000.656 ft. A side of
  a line in sealed packages recorded before converts by its packages
  the same way, so spools received in m leave whole in ft, nothing
  over.

## Amendment: the guided recount of a unit wrong from the start (2026-11-25)

Decided by Ali on 2026-11-24 and 2026-11-25 (inventory-tng-qety). When
what was recorded was never really in the item's unit, converting is
wrong, as the numbers may already be in the other unit. A guided recount
corrects it on the same item, keeping its history and links
(`catalogue/unit_recounts.py`). Rejected: restating the numbers in the
other unit, and retiring the item for a new one.

- Offered from the answer No, for an item whose stock has moved, or
  which has converted before, into another length. Its page says
  beforehand all it will do, and asks Reorder at, what is on order and
  its vendor offers' units per order again in the new unit.
- In one transaction, holding the catalogue, every place the item has
  been at, then the item: each place is brought to zero form by form
  in the old unit, by adjustments with the reason Records were wrong,
  and again in the new unit for any part of a thousandth an earlier
  conversion left (loose, for an open package closed or moved since);
  its open packages close; its packages in the old unit are retired and
  renamed, its unit after their name, so a package of the same name can
  be added; its unit changes; and the amounts typed are kept. Refused,
  to be shown again, if anything it would do changed since its page was
  shown, or if it reached another place before the item was held.
- An item whose stock moved but holds nothing has nothing to write off
  or close; the rest is done as above. Its packages in the other unit,
  as a conversion left them, stay.
- An item converted before whose stock never moved has nothing to write
  off, close or count, and retires no package. Each of its packages in
  the old unit is asked its size again in the new unit, and its
  too-short length if it has one, and keeps its name and links; those
  in the other unit are not asked (Ali, 2026-11-25,
  inventory-tng-l26i). Rejected: retiring and renaming them, and
  leaving them as they are.
- Its retired packages in the old unit are asked too, marked retired,
  below its current ones, and stay retired, so one brought back later
  has the new size. Bringing one back refuses only a fraction on an
  item counted each, which a converted item never is again (Ali,
  2026-11-25, inventory-tng-l26i). Rejected: asking a retired one's size
  only when it is brought back, and leaving them as they are.
- The item keeps its last ledger transaction then
  (`Item.unit_recounted_after`). What left a place since a count form
  was first shown leaves out the lines up to it, and so must use summed
  over a time window, as they are in no unit to add to. A batch sent
  again after the recount raises no check of a line up to it: the
  recount wrote it all off, and correcting one would write it back
  (2026-11-25, batch-wide review of inventory-tng-v00h).
- It revokes no labels, for the reason Yes revokes none (previous
  amendment); a retired package's label stops scanning anyway (Ali,
  2026-11-25, replacing his decision earlier that day that it revokes
  the item's labels). The one exception: the labels of the packages it
  asks the size of again, retired ones' too, as they print the size as
  it was. Its page says so beforehand, and the page after links to the
  item's labels to print new ones of its current packages, and says a
  retired one's is printed once it is brought back (Ali, 2026-11-25,
  inventory-tng-l26i).
- The page after links to a count of the item at each place, in the new
  unit, and to adding its packages in it. For an item converted before
  whose stock never moved, neither page says to count it or add its
  packages, as nothing is counted or retired. The page after takes all
  this from its address, as the recount left the item, so reloaded once
  stock has moved it still offers no packages to add (inventory-tng-mu57).
