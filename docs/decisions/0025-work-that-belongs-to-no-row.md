# 0025 — Work that belongs to no row opens over the column, and closes back to it

**Status:** accepted

## Context

[Decision 0014](0014-one-interface.md) point 1 places three administrative
capabilities by name — editing on the item list, merging in the volunteer
picker, revocation on a label — and its principle places two more, because a
location and a category are fields on an item and so are made where an item
names one. Each of those has a row to sit on, and `admin/EditItem.tsx` is what
sitting on one looks like.

Three pieces of work have no row. A sheet of stickers spans items and is
rendered whole by `/api/labels/sheet`. "What custody is still open" spans
volunteers and locations. "Which labels are on this shelf, or came off this
print run" spans labels. All three are browse-and-act surfaces, and the app
they would arrive in is one column, `Container maxWidth='sm'`, laid out for
somebody holding a phone at a shelf.

Two constraints bind whatever is chosen, and they are not decoration. **The
volunteer flow is why this application exists**, and 0014 accepted rebuilding
the catalogue's editing surface as the largest piece of work in its plan — not
as a licence to reshape the app around administration. And **`frontend/package.json`
carries no routing dependency**, which under 0014 point 1 is correct rather than
missing: signing in changes what the interface offers, it does not navigate
anywhere.

Four shapes were weighed.

- **A route.** The most familiar answer and the most expensive one here. It
  adds a dependency, and it spends the one thing this app has already committed:
  its URL grammar belongs to labels. `/S/{code}` is minted into every QR symbol
  (`backend/src/inventory/labels.py`), read once on arrival, and then erased
  from the address bar so a reload does not re-apply the scan
  (`frontend/src/scan/deepLink.ts`). A second grammar beside that one is not
  free to change afterwards, because a URL that exists is a URL somebody
  bookmarked, mailed or printed.
- **A tab bar.** Splits the app into modes, which puts chrome on the volunteer's
  first screen that means nothing to them, and asks them to notice they are in
  one of two places. It is the constraint above being spent rather than
  respected.
- **A drawer.** Cheap, and a second surface all the same: it slides in beside
  the column and leaves the question of what is underneath it unanswered on a
  screen too narrow to show both.
- **An expandable panel in the column.** The literal reading of "stay in the
  column", and the worst of them on a phone: a list that scrolls inside a page
  that scrolls, with the thing that was being looked at pushed off the bottom.

What none of the four noticed at first is that the app already answers this.
`admin/EditItem.tsx` is a `Dialog`, and it is already the thing that appears
when what somebody is doing is not the column. The question is not what
navigation shape to add. It is whether the shape already here stretches to a
list, and it does: MUI makes a dialog full screen with one prop, at exactly the
breakpoint this app is designed for.

## Decision

**There is no second surface and no router. Work that belongs to no row is
opened from a control in the one column, drawn over it as a dialog, and closed
back to where it was opened from.**

1. **No routing dependency, and no second URL grammar.** The addresses this
   application answers to are a label's and the app's own root. What a person
   is looking at is not addressable, which is a real loss and a deliberate one:
   nothing here is worth linking to more than a label is, and a link that
   exists cannot later be taken back.

2. **The entry point is a control in the column, beside the collection it is
   about.** A sheet of stickers is asked for from the item list, because that
   is where the items are. It is not a menu of everything an administrator can
   do, because such a menu is a second surface wearing a smaller hat, and
   because a control placed beside its subject needs no label explaining where
   it leads.

3. **The control is drawn from a capability, so a volunteer's column is
   unchanged.** `useCan()` reads `/api/me`, which computes the answer by
   running each view's own permission classes (0014 point 3). A capability that
   is false draws nothing at all — not a disabled control, not a heading with
   nothing under it — so the population this app exists for sees exactly the
   screen it saw before.

4. **What opens is a `Dialog`, and a surface this record governs sets
   `fullScreen` below the `sm` breakpoint.** Which is to say: full screen on the
   phone the app is designed for, and a panel over the column on anything wider.
   The component is the one the catalogue editor already uses, so there is
   nothing new to learn and nothing new to import — but the size is this
   record's own and is not a description of that editor. `EditItem` and
   `CreateItem` are `fullWidth`, because a form of five fields on a phone is
   already a dialog's worth and a full screen would be mostly empty; they are
   anchored on a row and are 0014's. A list is the other case, and it takes the
   screen.

5. **Closing returns to the exact screen it was opened from.** This is the
   property worth naming, because it is the one a route would have to be built
   to fake. There is no navigation, so there is no history to manage, no scroll
   position to restore and no state to reconstruct: the column was never taken
   down. An administrator who prints a sheet is still looking at the item they
   were looking at, still with the search they typed.

6. **This does not license a dialog to be lived in.** One at a time, opened
   from the column and closed back to it. A surface that needs to be linkable,
   or two that need to be open at once, or a dialog that opens another, is the
   point at which this record is wrong rather than the point at which it is
   stretched — and it is where the argument for a router should be made
   properly.

**The reversibility is a reason and it is stated as one.** The four shapes are
not equally easy to undo. A dialog is already a component boundary: what is
inside it does not know it is in one, so promoting it to a route later is a
change to where it is mounted and to nothing else. A route cannot be withdrawn
the same way, because by then it has been shared. Where the argument above is
close — and between a dialog and a drawer it genuinely is — this is what
settles it.

## Consequences

- **`inventory-tng-2oba.6`, `inventory-tng-ayu` and `inventory-tng-3ez` adopt
  this rather than each inventing one.** That is what the record is for: the
  first of the three to be built would otherwise have settled the shape for the
  other two without anybody arguing it.
- **Nothing an administrator is looking at can be linked to or bookmarked.**
  Point 1 accepts that. The cost lands on the smaller population, doing
  occasional work, at a bench rather than at a shelf; the alternative spends the
  app's URL grammar, and that cost lands on every sticker already printed.
- **A surface that grows past what a dialog holds is a signal, not an
  inconvenience.** Point 6 makes it a decision to record rather than a shape to
  work around, which is the same treatment `docs/architecture.md` gives to
  shared state that outgrows the cart.
- **The Django admin remains the place where these lists are filtered and
  sorted at length**, which is 0014 point 4 doing its job rather than a gap.
  `inventory-tng-3ez` is that admin being made adequate for the print-run
  question, and this record does not oblige the single-page app to answer it as
  well.

## References

- [MUI Dialog](https://mui.com/material-ui/react-dialog/) — `fullScreen` and
  the breakpoint query behind point 4.
- [decision 0011](0011-qr-batch-scanning.md) sections 3 to 5 — the label URL
  grammar point 1 declines to sit beside.
