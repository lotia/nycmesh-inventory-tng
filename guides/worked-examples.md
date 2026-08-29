# Three sessions to try on your own machine

Short scripted walkthroughs against the invented catalogue `seed_demo_data`
puts in the database. They exist to be *followed*, on a laptop, so that the
awkward parts of the app are found by using it rather than by reading it.

These are examples, not the rules. What a thing means, and what happens when it
goes wrong, is in [the volunteer guide](volunteer.md) and
[the administrator guide](administrator.md); each step below links to the part
that explains it. If the two ever disagree, the guides are right and this file
is stale.

Getting the stack up, and signing in, is
[the quickstart](../README.md#quickstart). Everything below assumes you are
signed in at <http://localhost:8080> and looking at the item list.

**The scene the seed makes**, so you can recognise it:

| | |
| --- | --- |
| Volunteers | Demo Installer, Demo Volunteer, Integration Tester |
| Locations | 131 Broome, Demo hub, Demo store, Shelf B2 |
| Items | LiteBeam (31 on hand), LiteBeam AC Gen2, Cat6 outdoor cable, NanoStation 5AC Loco, OmniTik 5 PoE ac, RJ45 shielded connector, Sector antenna 120° 5 GHz |
| Label `DEM0000001` | an **item** sticker: one LiteBeam AC Gen2 |
| Label `DEM0000002` | a **wall** sticker: Shelf B2 |

`seed_demo_data` prints those two codes when it runs, and they are the only two
worth typing by hand — the other ten it makes are five-at-a-time LiteBeam
stickers with random codes.

> **If a Save is refused**, and the refusal mentions an origin rather than
> anything you typed, the stack is running a frontend image built before
> `inventory-tng-o1uj.6`. Rebuild it — `compose up --build -d frontend` — and
> try again. That fault refused every write in the application, and it is the
> reason these walkthroughs exist in the shape they do.

---

## 1. An administrator makes two stickers

**About twelve minutes**, and the point of it is that a code means nothing by
itself — you are minting two pieces of paper and deciding what one scan of each
will stand for. The rules are
[Labels](administrator.md#labels).

1. On the item list, press **Print labels**. It is above the list, and it is
   shown to administrators and to nobody else.
2. Make the first sticker a **single**: choose **LiteBeam AC Gen2**, ask for
   **1** sticker, and leave what one scan stands for at **1**. This is the
   ordinary case — one sticker on one radio.
3. Make the second a **packet**: choose **RJ45 shielded connector**, ask for
   **1** sticker, and set what one scan stands for to **100**. Now one scan of
   that sticker means a bag of a hundred, which is the whole reason the
   quantity is a property of the *sticker* and not of the item.
4. Press **Make them**. What it made is already ticked on the sheet below.
5. Press **Print the sheet**. It opens in a new tab laid out to be printed as
   it is — **at 100%**, because
   [shrinking the squares is what faded ink destroys](administrator.md#labels).
6. Do the check [Labels](administrator.md#labels) ends on, against the two
   codes you asked for — it is the only thing standing between a short sheet
   and a printed one.

**What to look for while you do it.** Whether you can tell the two stickers
apart afterwards without scanning them; whether "how many stickers" and "what
one scan stands for" are distinguishable when you are tired; and whether the
sheet tells you which is the packet.

*If you want the manufacturer's own barcode on an item instead — the number
printed on the box — that is an **identifier**, which is a different thing and
is only reachable from the Django admin today. Read
[the catalogue](administrator.md#the-catalogue-and-what-an-identifier-is-for)
first, and note that `inventory-tng-gz2` is the open issue that the app's own
search does not read them yet.*

---

## 2. A volunteer takes stock out

**About five minutes.** This is the flow the whole application is shaped
around, and the one to run most often. The rules are
[the volunteer guide](volunteer.md).

1. **Say who you are.** Press your name — use **Demo Installer**. The heading
   changes to *Working as Demo Installer*, and **Not you?** puts it back.
2. **Say where the stock is.** Type `DEM0000002` into *Scan or type a code* and
   press Enter. That is the wall sticker for Shelf B2, and the app answers
   *Location set for this batch*. A scanner gun types exactly this and presses
   Enter for you.
3. **Scan what you are holding.** Type `DEM0000001` and press Enter. The app
   answers *Added 1 × LiteBeam AC Gen2* and the item's row in the list now
   shows **1** against it.
4. Scan it twice more. The count goes to 3 — scanning the same sticker again
   adds again, which is what a person emptying a shelf actually does.
5. Add something without a sticker: find **LiteBeam** in the list and press
   **+** twice, or use the **+5** beside it, which is a packet button.
6. **Check the batch**, at the bottom. It names *What is happening* — leave it
   at **Taking stock out** — and *Where the stock is*, which already says
   **Shelf B2** from step 2. Every line you added is listed.
7. **Save.** The batch clears, and the item's on-hand figure moves by what you
   took. Taking out more than a shelf holds is allowed and leaves the figure
   negative — session 3 is how that gets answered.

**What to look for.** Whether step 2 is discoverable at all if nobody told you
to scan the wall first; what the app does when you scan a code it does not
know; and whether the batch at the bottom is visible on a phone without
scrolling past the whole catalogue.

---

## 3. An administrator answers a shelf that disagrees

**About fifteen minutes**, and it is the most interesting of the three because
the answer is deliberately *not* an edit. The rules, and the reasoning, are
[Reading a balance](administrator.md#reading-a-balance) and
[Putting a wrong number right](administrator.md#putting-a-wrong-number-right).

The scenario: somebody counts Shelf B2 by hand and finds **12** LiteBeam where
the app says **31**.

1. **Read what the app thinks.** The item's row in the list carries the figure,
   and it is everything on every shelf added together — not this shelf.
2. **Find out why.** In the Django admin, open *Stock movements* and filter to
   LiteBeam. Meet the limitation
   [Reading a balance](administrator.md#reading-a-balance) warns about — this
   is the step where it costs you time, and noticing how much is half the point
   of running the session.
3. **Work out what to record.** The shelf holds 19 fewer than the app says.
   Which of 12 and 19 goes in the form, and why the other one is unrecoverable,
   is [Putting a wrong number right](administrator.md#putting-a-wrong-number-right).
4. **Record it as a stock count**, filling the form exactly as
   [Putting a wrong number right](administrator.md#putting-a-wrong-number-right)
   sets out. For this scenario the numbers are: quantity **19**, and because
   the shelf holds *less* than the app says, **Shelf B2** is where the movement
   comes *from* and the other location box stays empty. Give the reason the
   count and its date — it is the only place the story survives.
5. **Save it once.** Nothing that was already recorded changes — the correction
   sits on top, and no row is ever edited or deleted, because the database
   itself refuses.

**What to look for.** How long step 2 takes, and whether you trust the answer
you got by eye; whether *From location* versus *To location* survives being
explained once; and whether anything on screen stops you typing the total
instead of the difference.

**Note what you had to already know**, because that is the finding. The
volunteer's app deliberately does not offer a count, so this is admin-only —
and the admin currently renders with no styling at all, which is
`inventory-tng-o1uj.1` and makes this walkthrough considerably harder than it
should be.
