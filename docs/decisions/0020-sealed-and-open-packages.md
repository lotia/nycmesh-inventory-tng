# 0020 — Sealed and open packages: spools in stock

**Status:** accepted · 2026-11-11 · amends 0003 (what a ledger line holds,
reorder) and adds the home page's "Waiting for you" (now "Needs your
action") ·
amended 2026-11-11 (tracking opened packages is a setting; bulk actions)
· amended 2026-11-12 (an open package on the scan page; correcting its
length; "Needs your action") · amended 2026-11-14 (recorded more than
moved; those settled listed, 2026-11-15) · amended 2026-11-18 (an item's
open packages listed fewest left first, short ends after the usable ones
on the scan page and flagged; its sealed packages largest first on the
stock pages, smallest first on the item, scan and count pages; packages
of one size by id) · amended 2026-11-23 (an item counted each; following
turned off; an opening recorded more than moved made loose, and one
moved whole, 2026-11-24)

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
  and form. A line of sealed packages is a whole number of them. The scan
  page sends the size it counted them in, and a line of a package resized
  since, as one nobody has used may be, is refused, never recounted in the
  new size: whether the volunteer's count or the old size was wrong is not
  the server's to guess (inventory-tng-5f7). Every line of a package says
  its size: one kept in an outbox by a page from before is refused, naming
  its line, as none was ever deployed (Ali, 2026-11-12, inventory-tng-1p8).
  The cart keeps that size with each count, so a package resized while a
  cart counts it is never saved at the new size: where the page's
  catalogue shows another size, the count is cleared and the line asks
  for it again, until it is typed or scanned; until then the cart is not
  recorded, as the rest without it would record less than was moved (Ali,
  2026-11-13, inventory-tng-0qi). We rejected keeping the count and
  refusing the save until the volunteer taps to say it was checked, which
  keeps the typing but adds a button and a step.

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
  until a catalogue shows it (inventory-tng-3p4). Reports and dismissals
  took the outbox to a key of its own, as a tab of the build before would
  drop them (inventory-tng-ktj); since inventory-tng-omi a build no longer
  copies an older one's batches, but that build's tab sends them (0010).
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
  305 m · open: 45 m, 120 m · loose: 85 m · short ends: 12 m · usable
  1,165 m. An item's open packages and short ends at a place are each
  listed by the scan page's rule: fewest left first (Ali, 2026-11-18).
  Its sealed packages are listed largest package size first, ties by
  package id, so the same records always show the same order (Ali,
  2026-11-18). The reorder page lists those low on sealed the same way,
  so the two pages agree (Ali, 2026-11-18). The item page, the scan
  page, the count form's sealed packages and the count page's choice of
  package for an open one nobody recorded list an item's packages
  smallest first, ties by package id, for the same reason (Ali,
  2026-11-18).
- **Reorder** compares usable stock in stores with the item's reorder
  point.
- **Minimum sealed**: a package may set an optional minimum of sealed
  packages in stores (Ali, 2026-11-11). Below it the item shows on reorder
  as low on sealed packages, besides the reorder point on the total.

### Counting

- **A count asks for each form**: how many sealed packages of each size,
  what is left on each open package, and how much is loose. Each form is
  corrected on its own, as items are today. The form asks for an item's
  sealed packages first, in the order Stock gives above, then its open
  packages oldest first, then loose (Ali, 2026-11-18).
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

Ali reviewed cutting from an open spool (inventory-tng-ck0).
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
- **Several open packages at a place** are a card each, fewest left
  first, so volunteers use up the nearly empty ones before starting
  fuller ones; ties go by open package id, so the same records always
  show the same order (Ali, 2026-11-18, replacing most left first). The
  stock page uses the same rule. Short ends follow the usable ones, in
  the same order, since none is long enough to use, each flagged "Short
  end" so the order reads right (Ali, 2026-11-18).
  One showing nothing or less is last, greyed, flagged "Shows nothing
  left", so the next volunteer can report it. A short end has something
  left, so no card has both flags. Each flag is in the card's legend, so
  read out with it. Ordered and flagged by **Now**, so no card moves or
  changes flag while a cut is typed.
- **One checkbox, one wording**: "Nothing usable left on it", whatever is
  typed, in the card whose legend names the package.
- **It ticks itself** when a cut leaves a short end, uses up what the
  package shows, or passes it, and a note appears in the card saying why,
  naming the checkbox it ticked ("so “Nothing usable left on it” is
  ticked", Ali, 2026-11-24), and that an administrator confirms: "Your
  cut uses up the Spool 305 m",
  "This Spool 305 m will be unusable after this cut" (a short end), or
  that the cut is more than it shows and an administrator will check it. The
  volunteer unticks it when cable is left, a short end kept for patch
  leads included. The note is a polite live region, and the cut's box is
  described by both figures; nothing else is announced as one types.
- **A cut past what a package shows is warned of and saved as typed**,
  never refused: the cable is already cut, a phone offline cannot know
  other phones' cuts, and negative balances are allowed (0003).
- **An open package of an item counted each is worded for pieces**
  (Ali, 2026-11-23, inventory-tng-uwi3): "Now 300 left", "After taking
  20", "Take from the Box", "Shows the wrong number left?", with the
  number keypad, and whole amounts only, which the server refuses too.
  Lengths keep the words above. The check page is "Amounts to check".
  Its flag reads "Too few left", not "Short end", on the card and on the
  stock pages, and a fraction the server refuses for what is left in it
  reads "Box of 500: count whole pieces left." (Ali, 2026-11-24).

### Correcting what a package shows

- **A volunteer may report what is on it now**: an optional field when a
  cut passes what it shows, and a "Shows the wrong length?" link on any
  open package. It is a report, not an adjustment (0003), and waits for
  an administrator (Ali: it saves administrators a recount).
- **The app raises the same check by itself** when an open package goes
  below zero, reported or not (Ali): its record was wrong, which is
  worth a look even when the spool is finished.
- **The check page** shows the item, package and place, what it shows,
  the volunteer's figure, who and when, and the recent cuts. **Set to N
  m** (the button follows the box; "Set to it" without JavaScript)
  writes a count under the administrator's name of the difference
  between N and what the package showed then: at the volunteer's report,
  for the volunteer's figure, else on the page, each with any count or
  adjustment of it since, which put it right already. So a cut since
  stays as saved, and Set is never refused for one (Ali, 2026-11-12,
  inventory-tng-b1z); if cuts since leave it below zero, it is asked
  about again. **Leave it** clears the check. **Closing the package**
  settles it too.
- **Items measured in length** (metres, feet) get a hint on the count
  page and on the reporting field: read the length printed on the cable,
  and if the marks count up from its start, what is left is the
  package's size less the mark (Ali). Items counted each get none, nor do
  items not tracked, which have no open packages.

### "Needs your action" on the home page

- **"Waiting for you" becomes "Needs your action (N)"**, N being what is
  to do, gone when nothing is (Ali: the name must say work is waiting).
- **Under each kind**, reported used up, shows the wrong amount, and went
  below zero, **one line per item** names how many of each of its
  packages wait, by the package's name ("Cat6 cable: 3 × Spool 305 m",
  "RJ45 plugs: 2 × Bag of 100": inventory-tng-b1z), and links to that
  kind's page showing only that item; "all N" links to the whole list
  and its bulk buttons. A reported package below zero
  shows its shortfall there, so none is confirmed unseen.

### Recorded more than moved (2026-11-14)

- **A line recorded with more than was moved** waits under "Needs your
  action" as "Recorded more than moved", one line per item with each
  amount, as a package's are ("RJ45 connectors: 1 × Bag of 50"), linking
  to its page: the item, the amount, the places and the transaction (Ali,
  2026-11-14, inventory-tng-0gg). It is raised when a scan page's save,
  changed after another tab recorded its cart under the same key, is
  refused holding less of a line than was recorded and than its cart held
  when put back, which it sends (0010); the server compares them, so
  nothing else can raise one. A later such save says the amount again
  while it waits.
- **Correct** writes an adjustment on each side of the transaction, with
  the reason "Recorded more than moved", in the forms it moved in: found
  again where it left, written off where it arrived. **Mark checked**
  settles it as it is, for one corrected another way. Either settles it
  only as the administrator saw it.
- **Mark checked says why** (Ali, 2026-11-15, inventory-tng-zgy), as it is
  the only way of settling one that writes nothing to the ledger, so a
  bare one could mean fixed elsewhere, nothing fixed, or one end fixed.
  Under "Why it is right as it is", one of "Counted where it left",
  "Counted where it arrived", "Counted both ends" or "Put right another
  way:" with a box for the words, up to 200 characters. With none picked,
  or another way left blank, it is refused with "Say why it is right as it
  is." beside them, keeping what was typed. Words typed with another
  reason are not dropped: it is refused the same way with "You wrote why,
  but picked another reason. Pick Put right another way to keep your
  words, or clear them." (Ali, 2026-11-15); words only of spaces count as
  none. Asked from a refusal for an open package moved whole, whose row
  has no Mark checked, the form is asked again above the list; one
  settled meanwhile is told so, as with a reason. Refused when sent from
  a refusal's alert at a retired place, the alert and its Bring back are
  drawn again, the problem and what was typed in its form, not the row's
  (Claude, 2026-11-15, left to Claude by Ali, inventory-tng-e5b): the
  form names that place in a hidden field, read again, so one that is not
  of the transaction, or may be counted now, shows the problem in the row
  alone. It answers 400, as any problem typed. The row keeps the reason
  as a value from a fixed list (`left`, `arrived`, `both`, `other`) and
  the words as typed, in canonical spelling, with who settled it and
  when; CHECK constraints allow a reason only on a row settled as checked
  and words only, and always, for another way. Confirm count, which
  settles a row as checked too, keeps no reason: the movement holds the
  count. The words are never logged; the list of those settled shows
  them (below).
- **An open package moved whole** is now where it arrived, so cannot be
  found again where it left. Its row says "An open package moved to
  the van. Confirm or correct count.", naming the place, with a box, "Count in the
  package", filled with what it held when it moved, and one button,
  Confirm count (Ali, 2026-11-15, inventory-tng-0gg). Unchanged, it
  settles the row and writes nothing ("Confirmed."); changed, an
  adjustment at that place, in the package, for the difference, with the
  reason "Recorded more than moved" ("Count corrected."). Once edited, the
  box says what it held under it ("Was 180 m"; without JavaScript, once
  sent back with another count). We rejected offering only
  Mark checked there, which left the count to be fixed elsewhere.
- **A count puts an end right** (Ali, 2026-11-15, inventory-tng-qs8). A
  row has two ends, where the stock left and where it arrived, or one
  when its transaction has no place on the other side (a receipt, an
  install). A count at an end, as of a moment at or after the movement's
  transaction (the place's latest when the count form was shown, 0003),
  puts that end right if it counted the item by its total, or, counted
  by form, the form the movement left or arrived in there: those
  sealed packages, that open package, or loose. A count shown before the
  move does not, nor does a line it withheld as moved meanwhile, nor a
  count sent again. An open package moved whole has the arrival end
  only, put right by a count of that open package there, on the count
  form or by Set on its length check (one that writes nothing, as it
  showed that already, puts nothing right, nor one set to a report sent
  in the batch that recorded the move). The row keeps the count's
  transaction for each end (`left_count_id`, `arrived_count_id`),
  written in the count's own transaction under the place's lock, the
  rows held by id after it, as Correct and Mark checked take them.
- **An opening's ends** (Ali, 2026-11-15, inventory-tng-r5z) are both at
  one place: sealed packages of the package opened where it left, and
  where it arrived the open package it became (its item tracked opened)
  or loose units. Only a count by form puts one right: a count of those
  sealed packages the one, a count of that open package, or of loose
  units, the other. A count by total puts neither right, as an opening
  never changes the total and a total cannot show the sealed and open
  split, so the row waits for Correct (both adjustments at the place,
  the total unchanged) or Mark checked.
- **An opening waiting is counted by form** (Ali, 2026-11-15,
  inventory-tng-he9, inventory-tng-dd8). While one waits at a place (as
  of the count form), its count shows that item, at 0 if the records
  hold none there, by form, with a line of the package opened, sealed,
  at 0 if the records hold none: so the sealed ones really there can be
  counted. So it is even where the place holds loose units alone, which
  is otherwise counted by total, and while only its other end waits, so
  a count can put either end right. Left at 0, the line counts none
  sealed, as any line left as it is. A count that closes the open
  package an opening arrived in (used up, or not there) puts that end
  right, as a count of it at 0 would, so Correct writes only the sealed
  end. Closing one by Confirm used up, or from its item's page, is no
  count, so that end keeps no count, though the close wrote off what it
  showed (below). A count of a move's open package closed puts no end
  right. A length check's Set never closes one; it counts it, which puts
  an opening's end in it right already.
- **An opening's open package closed** since, however it closed (Confirm
  used up, Close on its item's page, a count, even one whose every line
  was withheld as moved meanwhile, which writes no count; not made loose,
  below), wrote off what it showed, so Correct skips that end and writes
  only the sealed end, not refused (Ali, 2026-11-15, inventory-tng-nyl).
  The row then says "Open spool closed 14 Nov. Sealed spool still to put
  right." (the day it closed) and offers "Correct sealed spool" and "Count
  Van", as for one end counted. Correct settles it as `corrected`; the
  skipped end keeps no count, as the close was none. A count of the sealed
  spool there puts the other end right, so it closes as `counted`, as does
  any count of its item there once both ends are right. A Correct seen
  before the close is not refused, as the sealed end alone is right either
  way. Correct and a count read the close under the place's lock, which a
  close takes first, so they go one at a time. Moves are unchanged: a
  move's open package closed still refuses its Correct and puts no end
  right.
- **Its sealed end counted, then its open package closed**, however
  (Confirm used up, one or ticked, Close on its item's page, a count),
  the close closes the row as `counted`: the sealed end by its count, the
  open end by the close (Ali, 2026-11-15, inventory-tng-die). It is
  settled by the close's administrator at the close's time, in the
  close's transaction, measured `how=counted`. The open end keeps no
  count (`arrived_count_id` stays empty), as a close is none; the CHECK
  holds by the sealed end's. The close takes its place, its open package,
  then such rows by id, skipping one another holds: everything else that
  holds one holds its place first, which the close holds too unless the
  open package moved whole since. A row whose sealed end is not counted
  still waits, for Correct of the sealed end alone.
- **Both ends right, still waiting**, as when that close skipped a row a
  count at the opening's place held, the open package having moved whole
  (Ali, 2026-11-15, inventory-tng-dro): the row says "Both ends are
  right: sealed spool counted 14 Nov, open spool closed 15 Nov." (each end
  named as one end put right names it, the days in the site's zone) and
  offers "Close it" in place of Correct, and Mark checked. Close it
  settles it as `counted` by the administrator, then, writing nothing,
  under the same locks as Correct and refused as changed meanwhile if not
  as seen; Correct sent in that state does the same, so neither settles
  it as `corrected` with nothing written. Measured `how=counted`; listed
  settled as "Counted" with the count's day and the close's. Locking is
  unchanged.
- **One end put right**, the row says "Shelf B3 counted 14 Nov. Rack A
  still to put right." (the day in the site's zone) and offers "Correct
  Rack A", one adjustment at that end only, under a key of its own;
  "Count Rack A", the place's count page showing that item only (0003),
  if it may be counted; and Mark
  checked. An opening's row names its forms, not the place twice:
  "Sealed spool counted 14 Nov. Open spool still to put right.", or
  "Loose cat6 cable" for loose units, with "Correct open spool" and
  "Count Van". The package's and item's names are shown with their
  first letter lower case when the rest of their first word is ("Spool",
  "Cat6 cable"), else as given ("PoE injector", "UPS"), and never
  pluralised; what is stored is unchanged (Ali, 2026-11-15). A Correct
  sent as seen before the count is refused as changed meanwhile. A retired
  place that holds nothing cannot be counted, so Bring back stays the
  way to put it right there.
- **Every end put right**, the row closes as `counted`, by the
  administrator of the count that closed it, then, and leaves "Needs your
  action". A CHECK allows one end counted at most while it waits, and
  one at least on a row closed so.
- **Those settled** are listed on a page of their own, "Recorded more
  than moved, settled", read only (Ali, 2026-11-15, inventory-tng-73p).
  The stock pages' views always end "Recorded more (2) · Settled", the
  number those waiting, shown even at 0; both pages start "Waiting (2) ·
  Settled", the one shown not a link, and keep an item shown alone. Not
  in the top navigation. A table, newest settled first, in pages of 50,
  of one item or all: Settled (the day and time in the site's zone), Item
  (linked to its stock by item), Amount, Where ("Shelf B3 → Rack A";
  "Store (opened)" for an opening; "from" or "to" one place; each place
  linked to its count of the item if it may be counted), How, Why and By
  (the administrator's username). How and Why, from what the row keeps:
  "Corrected", with "Two adjustments", or the one end Correct wrote
  ("Rack A only", "Sealed spool only") after a count or a close put the
  other right; "Count confirmed", Confirm count unchanged (checked, with
  no reason), "Held 305 m"; "Count corrected", Confirm count changed (an
  open package moved whole, corrected), "Adjusted at Van"; "Checked",
  with its reason's words and, for another way, the words typed;
  "Counted", each end by the place or form counted and the day ("Shelf B3
  14 Nov, Rack A 15 Nov"; "Sealed spool 14 Nov, open spool closed 15
  Nov"). The wording of Count confirmed's, Count corrected's and one
  end's Why is Claude's (2026-11-15). Sorting by every column and the
  number of rows a page come with the list control every list page gets
  (inventory-tng-qwb).
- **The page's words** (Ali, 2026-11-15): "Another tab recorded these
  before a volunteer changed them, so the inventory shows more moved than
  was. Correct moves the extra back with an adjustment. Mark checked if
  you already fixed it."; notices "Corrected.", "Marked checked.",
  "Closed." for Close it, and, for
  one answered or changed meanwhile, "Not changed: someone answered or
  changed it meanwhile. Reload to see it as it is."
- **Refused by a rule** (Ali, 2026-11-15, inventory-tng-axb), Correct or
  Confirm count says why above the list, with two buttons under it. At a
  retired place that holds nothing, which cannot be adjusted or counted:
  "Not changed: Shelf B3 is retired and holds nothing, so the 2 can't be
  found there again." (where it arrived: "can't be written off there"; a
  count: "so its count can't be corrected there"), then "Bring back Shelf
  B3" and "Mark checked". Bring back makes the place current as the places
  page does, recorded as any place change, and returns with "Shelf B3
  brought back. Correct it now."; holding the stock then, it cannot be
  retired again until that is moved or counted elsewhere. A volunteer's
  custody comes back with them, so only Mark checked is offered. Any other
  rule: "Not changed: " and its words. One not an open package moved whole
  any more is said as one changed meanwhile.

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
  how (`volunteer`, `below_zero`), and settled, by how (`set`, `left`,
  `closed`); lines recorded more than moved raised, and settled, by how
  (`corrected`, `checked`, `counted`), and those marked checked by their reason
  (`left`, `arrived`, `both`, `other`); places brought back to correct
  one.
- Built by inventory-tng-ck0 (the card and its box), inventory-tng-pll
  ("Needs your action"), inventory-tng-wy7 (reporting and checking a
  length) and inventory-tng-pg7 (the hint).

## Amendment: a package's fractions once its item counts each (2026-11-23)

An item counted each moves whole units, so none of its current packages may
hold, or be too short to use below, a fraction (Ali, 2026-11-23,
inventory-tng-fa5i):

- **Switching an item to each** is refused beside its unit while a current
  package has one, naming the package. Holds with a fraction says what it
  holds and to change that to a whole number or retire the package. Too
  short to use with one is not named, as a length no longer applies once
  the item counts pieces: the words give the whole numbers it may be, more
  than 0 and less than what the package holds, and that package's box is
  outlined. Clearing the box is accepted too.
- **A package holding 1** can only be too short to use below a fraction,
  which no whole number fits and which only catches an empty package. The
  switch to each clears it by itself, saying nothing; history keeps the
  old value. Bringing such a package back to an item counted each clears it
  the same way.
- **A retired package holding a fraction** is not brought back to an item
  counted each. It has no Holds box while retired, so the words above the
  page link to the Add package form, to add a new package with a whole
  number; that form's Holds box is outlined and focused. Its Package box
  stays empty: a package's name is unique among its item's, retired ones
  too, so the old name cannot be taken again.
- **A retired package too short to use below a fraction**, holding more
  than 1, is brought back to an item counted each. The item page then
  outlines and focuses that package's Too short to use box, with the whole
  numbers it may be beside it. The fraction only decides when the scan
  page calls an open package a short end, never a count, so it is safe for
  the moment it takes to fix; saving the box refuses it until it is whole.
- **An item already counted each** may keep a current package with a
  fraction: only the switch is checked (inventory-tng-jwlh).

We rejected naming Too short to use, and clearing and hiding the box for
items counted each: the scan page uses it to show an open package as a
short end.

## Amendment: turning following off, and Too short to use (2026-11-23)

Turning "Track each opened package" off left the open packages already
made followed, offered for cuts and closed by an administrator, while new
openings went loose; and Too short to use was offered on every package,
though it only says when an open package is a short end (Ali, 2026-11-23,
inventory-tng-j89e):

- **Too short to use is shown only for a package whose opened ones are
  followed**, by its own setting or its item's. A hidden one keeps its
  value, and comes back with the box. A box to be set whole for an item
  counted each (above), or one a resize was refused for, at or below
  it, is shown however its package is set.
- **Turning following off closes the open packages no longer followed**,
  in the same save, whether set on the item, on a package or for the items
  ticked. What is left on each becomes loose units at its place, by a
  ledger transaction of its own kind, a loosening, under the
  administrator's name, so the place holds what it held. One left below
  zero takes as much from loose units. The open package is marked closed,
  never deleted, and its history says so. Nothing is written off, so a
  loosening puts no end of an opening recorded more than moved right.
  A count of a store whose areas are gone sets aside what arrived in an
  open package since loosened from the loose units the loosening made.
- **Locks**: the change takes its open packages' places first, as a count
  does, so a cut saved at the same moment is counted before the close or
  refused after it. One moved whole to another place, or opened at
  another, meanwhile refuses the change as made since the page was shown.
- **The page says how many were made loose**, after its own notice, in
  words Ali chose on 2026-11-24: "What was left on 2 open packages is now
  loose units, where each one was." It lists no amounts or places.
- **The item page says so before the save.** Under the item's "Track each
  opened package" box, while open packages follow the item's own setting,
  it counts only those: "2 open packages are followed now. Turning this
  off makes what's left on them loose units." Under a package's own
  setting, the same words count that package's open ones. The items list
  gives no hint.
- **No page calls this closing**: "close" is kept for write-offs, used up
  or lost. The open package's record is marked closed only inside the
  app. The ledger's kind, loosen, is never shown: the length checks leave
  it out of an open package's last cuts, as one shown just before it was
  made loose could otherwise list it.

- **An opening recorded more than moved, its open package made loose**,
  still waits for its open end (Ali, 2026-11-24, inventory-tng-n7dj). The
  row says "What was on the open spool became loose units at Shelf B on
  24 Nov. Count Shelf B to put this right." (the open form as the row
  names an end, the place and the day it was made loose) with "Count
  Shelf B", Correct and Mark checked. A count of the item there after it,
  by its total or its loose units, puts that end right; one counting the
  sealed spools too puts both right at once. Then the row reads as after
  any count, naming that end by what it became ("Loose cat6 cable counted
  25 Nov."). Correct writes that end off the loose units there. Only a
  write-off (used up, lost) says "closed" and puts the end right. The app
  tells the two apart by the loosening transaction; one made loose with
  nothing left on it has none, so reads as closed, as nothing became
  loose. Ali rejected "Loose at Shelf B since 24 Nov. Count Shelf B to
  put this right.", and treating a package made loose as put right.
- **The same holds wherever it was made loose** (Ali, 2026-11-24,
  inventory-tng-04z7): for an opening whose open package moved whole to
  another place first, and for an open package recorded as moved whole,
  whose one end it is. The row names the place it was made loose: "What
  was on the open spool became loose units at Van 2 on 24 Nov. Count
  Van 2 to put this right.", with "Count Van 2", Correct and Mark
  checked; one moved whole no longer asks to confirm its count. A count
  of the item at Van 2 after it puts that end right. Correct writes the
  extra off the loose units at Van 2, and holds that place with the
  row's own, in the ledger's order; made loose at a place it did not
  hold, it is refused as changed. Settled, one moved whole reads
  "Corrected", "Adjusted at Van 2". Ali rejected saying only Mark checked
  settles it.

We rejected leaving both as they were, and only showing "2 open packages
still followed". For the words, Ali rejected saying nothing before the
save, and a question to answer before saving.
