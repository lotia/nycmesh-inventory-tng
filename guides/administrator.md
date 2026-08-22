# Looking after the inventory

For the handful of people who keep the catalogue, the people list and the
labels in order. Everything here needs a login; volunteers need
[the other guide](volunteer.md).

What each record means is [the data model](../docs/data-model.md). This says
what to do, and where.

---

## Where you work

Two places, and they are one system.

- **The app itself**, for anything you are already looking at. An item row
  carries an **Edit** button for you and for nobody else.
- **Django's admin**, at `/admin/`, for everything else. It is complete: every
  kind of record this system stores has a page there.

Sign in at `/accounts/login/`. Signing in with a password asks for a code from
an authenticator app as well, every time, and there is no way round it —
[decision 0013](../docs/decisions/0013-administrator-sign-in.md) says why.

**Saving** anything then asks you to sign in once more, inside the same
session, and puts you back where you were. Reading never does.

---

## The catalogue, and what an identifier is for

An item is the thing, never the stock of it. Its count is worked out and never
typed. Underneath it on its page are its identifiers.

![The identifiers on an item](images/administrator-identifiers.png)

**An identifier is any string that has ever meant this item**: a part number, a
vendor's code, one of the retired `NYCM-` codes, or simply what people call it.
A volunteer typing `tp link` into the search finds the item because somebody
put `tp link` here.

This is the whole fix for the old spreadsheet, where a name that did not match
matched nothing at all and the movement was silently lost. So:

**When somebody's word for a thing finds nothing, add it as an identifier.** Do
not rename the item to match. Two strings can name one item; one string may
never name two, and the database enforces that.

---

## Volunteers, and merging duplicates

Volunteers add themselves, so duplicates happen. Merging them is ordinary work,
not a repair.

Open the duplicate — the one you want to stop seeing — and set **Merged into**
to the record you are keeping.

![Merging one record into another](images/administrator-merge.png)

What that does, and does not do:

- The duplicate stops being offered to anybody, and stops being usable as the
  person a batch is recorded against.
- **Every movement it is already on stays exactly where it was.** The ledger is
  never rewritten, so nothing is lost and nothing changes number.
- There is no delete, and there should not be.

Retiring a record with **Active** is the other half: it takes somebody out of
the list without claiming they were somebody else. Use it for a volunteer who
has moved on.

---

## The questions the sheet import could not answer

The import from the old Google Sheet never guesses. Where it could not decide
something it wrote the question onto the record and left it for you. There are
two such columns, one on a volunteer and one on an item, and **both are
answered by being emptied**.

### On a volunteer

Filter the list to the rows carrying one.

![The volunteers the import could not tell apart](images/administrator-volunteers-flagged.png)

Each says which other record it might be the same person as. Decide, then
either merge it as above, or empty the field because they really are two
people. Either way the field ends up empty, and an empty field means settled.

### On an item

![The question the import left on an item](images/administrator-item-flag.png)

Every item the import moved stock for carries one, and it says the same thing
each time: these are the quantities the sheet recorded, taken literally,
because nothing in the export says whether a volunteer meant that many things
or that many packets of them. Nothing was multiplied by anything.

Settling one means deciding what a packet of that item is, putting that number
on the labels you print for it, and emptying the field.

You cannot repair the historical rows by answering it. The ledger cannot be
rewritten. If the number on the shelf is wrong, count it and record the count.

---

## Locations

![The places stock can be](images/administrator-locations.png)

Locations nest: a site holds rooms, a room holds shelves. A volunteer carrying
stock is a location too, with **Held by** naming them.

Retiring a location stops it being offered. Stock can still be taken out of a
retired place, but nothing can be moved into one.

---

## Labels

A label is a printed code pointing at one item, or at one place. The code means
nothing by itself, so renaming an item never breaks a sticker.

![One label](images/administrator-label.png)

**To make one:** add a label. The **Code** box arrives with a code already
minted in it — leave it alone unless you are recording a sticker that was
printed somewhere else. It cannot be changed afterwards, because by then it is
on a shelf. Point the label at an item *or* at a location, never both. On an
item label, **Quantity** is what one scan of that sticker means: put `100` on
the sticker that goes on a packet of a hundred. On a wall label, leave it
empty.

**To print them:** collect the codes and ask for a sheet, comma separated.

    /api/labels/sheet?code=7QK3M2XV9A,4NP8R7T2WQ

![A printable sheet of stickers](images/administrator-label-sheet.png)

That page is laid out to be printed as it is. **Print it at 100%** — anything
else shrinks the squares, and small squares are the first thing faded ink
destroys. Everything else that decides whether a sticker still scans after a
year in a basement is fixed for you and is not worth adjusting. The code is
printed in words underneath so a dead sticker is still a usable label, and the
date is there so a faded batch can be found and replaced as a set.

**To replace a faded sticker:** print a new label and set **Revoked at** on the
old one. Do not reprint the old code. A revoked sticker still scans, and the
volunteer scanning it is told to have the shelf reprinted — so nobody is
blocked while you get round to it.

---

## Reading a balance

**A balance is never stored.** It is arithmetic over the movements: everything
that arrived somewhere, less everything that left.

![The movements a balance is made of](images/administrator-ledger.png)

You read the number itself in the catalogue, on the item's row in the app —
that is the figure a volunteer sees, and it is everything on every shelf added
together. Per shelf, read the item through the API.

You read *why* it is that number here, in the movements. When somebody reports
that a shelf disagrees with the app, this list is where the answer is.

Nothing in the ledger can be edited or deleted, by anybody, ever — the database
itself refuses. A wrong figure is put right by recording a count, which is a
new entry saying what is actually on the shelf.
