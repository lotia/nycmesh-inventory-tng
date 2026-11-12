# 0020 — Sealed and open packages: spools in stock

**Status:** accepted · 2026-11-11 · amends 0003 (what a ledger line holds,
reorder) and adds the home page's "Waiting for you" (now "Needs your
action") ·
amended 2026-11-11 (tracking opened packages is a setting; bulk actions)
· amended 2026-11-12 (an open package on the scan page; correcting its
length; "Needs your action")

## Context

Cable is bought by the metre and in spools of several lengths, and other
supplies come in packs. Today an item has packages (`ItemPackage`, such as
"Spool 305 m" of size 305), a package's label adds its size in the item's
unit, and the ledger keeps one balance per item and place. So stock says
"1,000 m" and cannot say whether that is three sealed spools and 85 m, or
ten spools nearly used. People need both: the total, and how many spools
are unopened (Ali, 2026-11-11, inventory-tng-8i4.60.22). Nothing is
deployed, so the migrations may be rewritten (0016).

## Decision

### One item, in several forms

- **An item stays one item**, counted in its unit, whatever it is packed in.
  Interchangeable makes are one item; hardware that is not interchangeable,
  such as two generations of a radio, is two (8i4.60.22).
- **Stock of an item at a place is held in forms**: sealed packages of each
  size, open packages, and loose units. Every package is tracked this way
  (Ali, 2026-11-11). The item's total is the sum of its forms.
- **An open package is a row of its own**: which package it was, where and
  when it was opened, whether a volunteer reported it used up (who and
  when), and when an administrator closed it. What is left on it is its
  balance in the ledger. It has no label (Ali, 2026-11-11: nobody would
  scan one); it is picked from a list.
- **A ledger line says the form stock leaves in and the form it arrives
  in**: a sealed package of a size, an open package, or loose. Quantities
  stay in the item's unit, so balance is still in minus out, per item, place
  and form. A line of sealed packages is a whole number of them.

### What volunteers do

- **Scanning a package's label moves one sealed package**, as today, now
  kept sealed where it arrives.
- **Opening**: at a place, a sealed package becomes an open one with its
  full size on it, in one transaction, so nothing appears or disappears.
  Volunteers open packages on the scan page. Picking an item there, by
  search or its label, adds one of its package sealed at that place,
  current or retired; if several are sealed there, or none, it fills no
  package, for the volunteer to type (Ali, 2026-11-12, inventory-tng-f9h).
  What is sealed there is the catalogue's count, less what the page's
  outbox has opened or taken that the catalogue does not show yet, plus
  what it brought; the catalogue says which of the outbox's batches it
  shows, and is asked for again after each save (inventory-tng-jlc).
  A saved batch stays in the outbox, dismissed from its list or not,
  until a catalogue shows it (inventory-tng-3p4).
  With no place picked yet, an item of one current package adds that
  package; once the place is picked or changed, every opening line the
  volunteer has not typed into is filled again by the rule above. A line
  typed into, or added by a package's own label, is never filled again
  (Ali, 2026-11-12, inventory-tng-prp).
  A package's own label adds that package, as ever.
- **Cutting or taking from an open package**: scan the item's label, pick
  the open package from those at the place ("opened 3 Nov, 120 m left"),
  type how much. It leaves the open package and arrives loose, or goes to
  an install.
- **Moving an open package** takes all of it to the new place, a vehicle
  or a volunteer holding stock included; it stays open there (Ali,
  2026-11-11).
- **Reporting used up, when a cut empties it**: a cut that reaches or
  passes what the open package shows asks "Is the spool used up?". Yes
  reports it used up; the cut itself is saved as usual.
- **Reporting used up, by a button**: an open package can be reported used
  up without a cut, when what is left is no use.
- **A report is not a close.** A volunteer never writes an adjustment
  (0003: counts and adjustments are an administrator's, and the ledger
  refuses one without an administrator). A reported package is shown as
  *reported used up*, is no longer offered for cuts, and waits for an
  administrator (Ali, 2026-11-11). Its metres stay in stock until then.

### What administrators confirm

- **"Waiting for you" on the home page** (now "Needs your action", by
  item: the 2026-11-12 amendment) lists what needs an
  administrator's decision, for example "2 spools reported used up:
  Confirm". It stays until each is dealt with, however often anyone signs
  in, like the home page's other warnings, and unlike "Since you last
  signed in", which shows finished changes to who may sign in, once, and is
  mailed. Nothing here is mailed. Spools are its first kind; later kinds
  join it as their decisions say.
- **The confirm page** lists each reported package: the item, where it is,
  what it still shows, who reported it and when. For each:
  - **Confirm used up** closes it. Closing writes an adjustment, under the
    administrator's name, for whatever it still showed, so its balance is
    zero: what is typed drifts from the real spool (rounding, ends made). A
    cut past what it showed is allowed, as negative balances are (0003),
    and closing evens it. The reason is Used up, seeded with the others and
    kept apart from Lost or stolen, so cable used is told from cable lost
    (Ali, 2026-11-11).
  - **Not used up** clears the report: the package is offered for cuts
    again, for a button pressed by mistake.
- **An administrator may also close an open package directly**, from its
  item's stock or a count, with the same adjustment; nothing waits.

### Short ends and reordering

- **Too short to use**: a package may set a length, optional and with no
  default (Ali, 2026-11-11). An open package below it is a short end: shown
  on a line of its own, not counted toward usable stock, the reorder point
  or the minimum sealed. The next cut from one asks whether to report it
  used up.
- **Usable stock** is everything but short ends: sealed packages, open
  packages and loose units (Ali, 2026-11-11). Stock always shows each form
  on a line of its own, clearly labelled, loose units included, so a
  total never hides what it is made of, for example: 3 sealed spools of
  305 m · open: 120 m, 45 m · loose: 85 m · short ends: 12 m · usable
  1,165 m.
- **Reorder** compares usable stock in stores with the item's reorder
  point.
- **Minimum sealed**: a package may set an optional minimum of sealed
  packages in stores (Ali, 2026-11-11). Below it the item shows on reorder
  as low on sealed packages, besides the reorder point on the total.

### Counting

- **A count asks for each form**: how many sealed packages of each size,
  what is left on each open package, and how much is loose. Each form is
  corrected on its own, as items are today.
- **A count may find an open package nobody recorded**, adding one with
  what is on it, and may close one that is not there, recording the loss
  (Ali, 2026-11-11). A count is an administrator's, so it closes packages,
  reported ones included, without the confirm page.

### Saved offline

- **The scan page makes each open package's key**, one for every package
  it opens, so the same batch sent again opens it once, and a later batch
  in the outbox can take from it before the server has seen it; the
  outbox sends a batch naming an open package only after the batches
  before it that make it, take from it or report it are saved. A batch
  with no key for a package it opens gets one made from the batch's own,
  one for each package its line opens, up to as many as a batch may have
  lines.

## Rejected

- **A separate item for each form** (`Cat6 cable, spool 305 m` beside
  `Cat6 cable`): no total without adding by hand, opening is a conversion
  between items, and search shows several items for one thing.
- **One balance per item, sealed counts estimated** from total ÷ size: it
  cannot tell sealed from used.
- **A label on each open package**: nobody would scan it (Ali).
- **Only some packages tracked sealed**: one rule for every package is
  simpler to explain and to count (Ali). Sealed packages are always
  counted; only tracking each opened one is a setting (Amendment).
- **A default "too short to use" length**: a sensible one depends on the
  cable and its runs.
- **Volunteers closing packages themselves**, by a volunteer adjustment or
  a new kind of ledger entry: either lets a mistaken tap write stock off,
  and the first breaks 0003's rule that adjustments are an
  administrator's (Ali, 2026-11-11).
- **Confirming through "Since you last signed in"**: it shows each change
  once, after which it is gone from the home page whether or not anyone
  acted, and it mails every administrator, which spools do not warrant.

## Consequences

- The ledger, balances, stock views, counts, reorder, labels and the scan
  page all learn forms. 0003 is amended to match, and the usage
  documentation explains each action for people, volunteers' and
  administrators' (inventory-tng-0kj).
- Metres on a reported spool stay in stock until an administrator confirms,
  so stock is a little high meanwhile; the home page keeps the wait
  visible.
- A volunteer cutting cable picks an open package; with two at a place,
  picking the wrong one is possible, and the next count corrects it.
- Open packages can be listed, so stock shows what is on each.
- Opening is a transaction of its own kind, `open`, whose from and to are
  the same place, the one kind that may name one place twice.
- A volunteer's report of open packages used up, alone, is a transaction
  of its own kind too, `report`, at the place, with no lines: it moves
  nothing, and the open packages say who reported them and when.
- A batch's reports are kept on its transaction, as sent (`used_up`): one
  sent again under its key is compared with them, so other reports are
  refused as the key reused, whatever became of the packages since
  (inventory-tng-ckl).
- Metrics: packages opened; reported used up, by how (`cut`, `button`);
  closed, by how (`confirmed`, `direct`, `count`); and reports cleared as
  not used up.

## Amendment: tracking opened packages is a setting (2026-11-11)

Tracking every opened package would make each opened box of connectors or
bag of zip ties an open package to pick from and to confirm used up, which
would fill "Waiting for you" with small confirmations every shift. Ali
wants this to be easy for administrators.

- **Sealed packages are always counted**, as above.
- **"Track each opened package" is set on an item**, on or off, off for a
  new item, and is the default for all its packages. **Each package** is
  "Same as the item" (the default), "Track" or "Don't track"; its own
  choice wins, and changing the item never overrides it. The item's page
  shows each package's effective setting and where it differs.
- **Tracked**: opening makes an open package, as decided above: picked
  when taking from it, reported used up, confirmed by an administrator.
- **Not tracked**: opening turns the sealed package into loose units, in
  one transaction; there is no open package and nothing to confirm.
- **An open package is made only for a package whose setting is on.**
  The server's setting decides, not the page's: an opening a page sent
  as tracked, of packages since set to not tracked, becomes loose units,
  and one it sent as not tracked, of several packages since set to
  tracked, becomes one open package each.
- **Bulk actions**, so administrators need not go one by one: the items
  list sets "Track each opened package" for the items ticked (never a
  package's own choice), and the confirm page confirms or clears the
  reported packages ticked, besides each row's own buttons. "Confirm used
  up" is a plain button, not 0019's danger pattern: the confirm page is
  already the administrator's check, and a count puts a mistake right.
  Closing one directly, from its item's page, asks first, as 0019 says:
  that page is no check of its own.
- **A count that finds an open package missing** closes it with the reason
  Lost or stolen, kept apart from Used up.

We rejected the setting per package alone (each size decided separately,
though spools or boxes of one item behave alike) and per category alone
(a category can mix spools with things sold each).

## Amendment: an open package on the scan page, and correcting its length (2026-11-12)

Ali reviewed mockups of cutting from an open spool (inventory-tng-ck0).
On the page as built, an open package is one more grey box among the
sealed and loose ones and easy to miss; the used-up question changes its
wording silently as the volunteer types; and what an open package shows
is its size minus the cuts typed, which drifts from the real spool. A
cut can pass what it shows (a typo, cuts rounded up, a cut recorded
against the wrong spool, a spool longer than its label). "Waiting for
you" counts every report in one line, which hides what is waiting.

### On the scan page

- **Each open package is a card of its own**, above the item's sealed
  and loose boxes: its package, an "open" badge, when it was opened, and
  two figures side by side, **Now** and **After this cut** (blank until a
  cut is typed; below zero, "N m over"). The second is a preview: nothing
  changes until the save, and offline it counts only this phone's cuts.
- **Several open packages at a place** are a card each, most left first;
  one showing nothing or less is last, greyed, flagged "Shows nothing
  left", so the next volunteer can report it.
- **One checkbox, one wording**: "Nothing usable left on this spool" (the
  package's word), whatever is typed.
- **It ticks itself** when a cut leaves a short end, uses up what the
  package shows, or passes it, and a note appears in the card saying why
  and that an administrator confirms: "Your cut uses up the spool", "This
  spool will be unusable after this cut" (a short end), or that the cut
  is more than the spool shows and an administrator will check it. The
  volunteer unticks it when cable is left, a short end kept for patch
  leads included. The note is a polite live region, and the cut's box is
  described by both figures; nothing else is announced as one types.
- **A cut past what a package shows is warned of and saved as typed**,
  never refused: the cable is already cut, a phone offline cannot know
  other phones' cuts, and negative balances are allowed (0003).

### Correcting what a package shows

- **A volunteer may report what is on it now**: an optional field when a
  cut passes what it shows, and a "Shows the wrong length?" link on any
  open package. It is a report, not an adjustment (0003), and waits for
  an administrator (Ali: it saves administrators a recount).
- **The app raises the same check by itself** when an open package goes
  below zero, reported or not (Ali): its record was wrong, which is
  worth a look even when the spool is finished.
- **The check page** shows the item, package and place, what it shows,
  the volunteer's figure, who and when, and the recent cuts. **Set to N**
  writes an adjustment under the administrator's name, as a count does;
  **Leave it** clears the check.
- **Items measured in length** (metres, feet) get a hint on the count
  page and on the reporting field: read the length printed on the cable,
  and if the marks count up from its start, what is left is the
  package's size less the mark (Ali). Items counted each get none, nor do
  items not tracked, which have no open packages.

### "Needs your action" on the home page

- **"Waiting for you" becomes "Needs your action (N)"**, N being what is
  to do, gone when nothing is (Ali: the name must say work is waiting).
- **Under each kind**, reported used up, shows the wrong length, and went
  below zero, **one line per item** names how many of its packages wait,
  in the package's word ("Cat6 cable: 3 spools", "RJ45 plugs: 2 bags"),
  and links to that kind's page showing only that item; "all N" links to
  the whole list and its bulk buttons. A reported package below zero
  shows its shortfall there, so none is confirmed unseen.

### Rejected in this amendment

- **Closing an open package by itself at zero**: zero is only what was
  typed, so it could write off cable still there, and it would skip the
  administrator's confirmation.
- **Refusing a cut past what a package shows**: the volunteer would
  record nothing, or record it elsewhere, and the error would spread.
- **Asking at every cut that reaches zero, with no answer assumed**: a
  tap more on a frequent action, for what the self-ticking box and its
  note already ask.
- **One count for every report on the home page**: it hides what is
  waiting and where.
- **A setting for items with a printed length**: one more thing for
  administrators to set; the unit is enough.

### What follows

- Volunteers report more, and administrators confirm more, but each is a
  tap rather than a recount; the next count still corrects anything left.
- Metrics, besides those above: checks of a package's length raised, by
  how (`volunteer`, `below_zero`), and settled, by how (`set`, `left`).
- Built by inventory-tng-ck0 (the card and its box), inventory-tng-pll
  ("Needs your action"), inventory-tng-wy7 (reporting and checking a
  length) and inventory-tng-pg7 (the hint).
