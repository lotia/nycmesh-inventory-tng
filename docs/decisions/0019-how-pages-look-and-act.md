# 0019 — How pages look and act: links, your account, actions and lists

**Status:** accepted · 2026-11-10 · builds on 0014 (one stylesheet in NYC
Mesh's look) · amended 2026-11-12 (packages renamed and brought back),
2026-11-15 (lists sort, filter and choose their page size), 2026-11-25
(amount boxes say their unit), 2026-11-26 (a unit inside a box's label
is in its description), 2026-11-27 (double taps)

## Context

0014 settled the stylesheet and MeshDB's look, but not how pages behave:
whether a thing is a link or a button, where the account links go, how
records are retired and listed. Each page had decided for itself. Research
into established design systems (GOV.UK, USWDS, Primer, Polaris, Atlassian,
Material) and WCAG 2.2 AA, with options drawn in the app's own colours, was
reviewed with Ali on 2026-11-10 (inventory-tng-8i4.60.12). The rules below
apply to the administrator pages and, where they have the same parts, to
the scan pages, on a phone (360 to 430 px, one hand) and a desktop alike.

## Decision

### Links and buttons

- **A link goes somewhere; a button changes something.** A GET is a link, a
  change is a POST button. A link may look like a button only to start a
  task.
- **Words say what happens.** A link that opens a task's page says it
  starts it ("Start a count", "Start counting Hub stockroom"), never that
  it does it; only the button that changes the records promises a change
  ("Record it"). An undo that changes a record is a button ("Bring it
  back").
- **Links are ink, underlined** in text and tables; hover thickens the
  underline. No link colour and no visited state. Keyboard focus, on
  everything (`:focus-visible`): links get the highlight token behind
  them and a thick ink underline, buttons a 3 px outline.
- **Link text makes sense alone**, with screen-reader-only words where the
  visible text is short (a "Revoke" link read as "Revoke ABC-123", the
  label's code in a visually hidden span).
- **Masthead sections** carry no underline, are at least 44 px tall, and
  mark the current page (`aria-current="page"`) in bold over a brand yellow
  line. No whole-row clicks: a record's name in a table is its link.

### Your account

- **"Signed in as dev"** stays, the username linking to the account page,
  its accessible name saying so ("dev, your account"). It sits in its own
  area apart from the sections: top right on a desktop, under a small
  "Your account" label after the sections in the phone menu. Sign out
  beside it is a secondary button, with no confirmation.
- **Settings is "Site settings"**, as it changes the site for everyone;
  one's own email, password, authenticators and sessions stay under the
  account.

### Actions

- **Three button styles.** Primary (brand fill, ink text): one per form,
  such as Save or Invite. Secondary (today's band button): the rest.
  Danger (problem-coloured text and border): only on a confirmation page,
  for what cannot be undone (revoking, removing an authenticator,
  resetting a sign-in, merging).
- **Buttons, and links in a row of actions, are at least 44 px tall.**
- **The way back is a link above the title** ("‹ Items"), not beside the
  buttons.
- **Retiring has its own last section** on a record's page ("Retire this
  item", a line on what it means, a secondary button), away from Save. It
  is one tap, as it can be undone: the notice after it offers "Bring it
  back" as a button. If the page has unsaved changes, it warns first, in
  the browser: retire anyway, or go back.
- **Confirmation pages stay pages**: the danger button first, then a link
  to keep things as they are. No sticky footers.

### Double taps

- **A tap never moves what is under the finger** (Ali, 2026-11-27,
  inventory-tng-r3jx). A double tap's second tap lands a moment after
  the first. When the first takes away or shrinks something with
  controls below it, they move up, and the second lands on one never
  meant. So such a tap keeps that space blank, or has the page ignore
  taps, for `SETTLE_MS` (`frontend/src/shared/settling.ts`).
- **Focus moves without scrolling**, to the heading of what moved up,
  the list's heading, or the control the removed content was about;
  never to a box or list whose keyboard or picker would open. A tap
  that asks to go somewhere far from it scrolls there, as Change goes
  to Your name, and taps then wait too, as everything moved.
- **An ignored tap** puts focus back on that target; an ignored key's
  press leaves focus where the volunteer moved it.
- **How:** the scan page keeps the space of a part that goes, or
  settles a list's own taps, with the hooks in
  `frontend/src/scan/settling-hooks.tsx`; every other such tap, on any
  page, ignores all of the page's taps with `ignoreTaps`. Only taps start
  a wait: typing, picking from a list and a label read leave taps taken.
  `backend/tests/codebase/test_focus_and_settling.py` refuses focus
  moved, or a tap told from a key's press, anywhere else.
- We rejected deciding each place apart, as each fix found another (Ali,
  2026-11-27).

### Amounts

- **Every box for an amount says its unit right after it**, on screen and
  in its accessible description ("m", "ft", "each"). Where a box sits
  inside its label, the unit is hidden from the label's name, so it is
  said once (Ali, 2026-11-26, inventory-tng-0mor, inventory-tng-h39f,
  inventory-tng-4mcj). A box counting sealed packages says "sealed", and
  a screen reader hears it once, from the box's label (Ali, 2026-11-25,
  inventory-tng-mdvz).

### Lists and retired records

- **Status is a word in a tag** (`.tag`: band background, line border, ink
  text), never faded text, which fails contrast: "Retired"; for
  administrators also "Invited" and "You"; for codes "Used up" and
  "Expired".
- **A retired record's page** shows its tag, "Retired on {date} by {who}"
  (from the history) and what that means; it is read only, with "Bring it
  back" its only action. To change it, bring it back first.
- **Lists are tables**, on a phone too: columns that do not fit move to a
  hint line under the name. A Retired section apart while lists are short;
  once a list has search, "Current · Retired · All" filter links instead.
  An empty list says what to do next.
- **Lists sort, filter and choose their page size** with one control of
  our own, not a library (Ali, 2026-11-15, inventory-tng-qwb). Headings are
  links: the sorted one has `aria-sort` and an arrow, and pressing it again
  reverses the order. "Show 25 · 50 · 100 · All" links above the table,
  the current one marked; 50 by default, and the pager below once there is
  more than one page. Filters are a form above the list, Filter and Clear.
  Each list opens in its own order, as before it could be sorted. While
  searching, matches come best first; a heading sorts them instead, and a
  "Best match" link puts the match order back. Sort, filters and page size
  live in the address only: nothing is remembered per administrator, and
  the navigation opens each list as it starts; an answer sent from a list
  comes back to it as it was, or to its last page if that one is gone.
  All of it works without JavaScript, and the page's statements do not
  grow with its rows.
- **Grouped lists keep their groups.** Stock by place or by item, and
  Needs counting, stay in groups in name order: the headings sort the rows
  within each group, and the page size counts groups, so a group is never
  split across pages. Their size rows say so: Stock's "Places shown:" or
  "Items shown:" in place of "Show" (Ali, 2026-11-16), and Needs
  counting's "Places shown:" (Ali, 2026-11-16). Locations stay a tree,
  with no list control.
- **Search forgives typos** (PostgreSQL's trigram matching) and offers
  suggestions from the current list, as its filter shows it, narrowing as
  one types (an ARIA combobox: arrows, Enter, Escape; 44 px rows).
  Picking one narrows the table to it ("1 of 212 items, matching …", with
  Clear search); Enter without picking lists every close match, best
  first, which is also what search does without JavaScript.
- **Actions act on one record, on its own page.** The one exception is the
  devices list, for after a compromise: a window of registration times,
  sorting by registered or last used, and "Revoke selected" for the
  devices selected, after a confirmation. Each device also has a page of
  its own, where it is named and revoked alone.

We rejected a link colour (one more token to keep in step with the dark
scheme, for a cue the underline already gives), "Your account (dev)" and a
stacked name (Ali preferred today's wording), a confirmation before every
retire (one more page, for something undoable), editing retired records,
bulk actions on every list, and search across all kinds of record from one
list's box.

## Consequences

- Pages change to match in the follow-up work under inventory-tng-8i4.60;
  until then, some still differ, and new pages follow this record.
- "Settings" moves to "Site settings" in the masthead and its page title.
- Each kind of list gains search as it grows past what one screen shows,
  and the list control's sorting, filters and page size.
- A retired record being read only, a name a retired one holds would keep
  it from coming back. So the names of items, categories and places are
  unique among retired ones too (Ali, 2026-11-10,
  inventory-tng-8i4.60.21). So are packages' among their item's, compared
  as items' names are (0021): a package can be renamed, its size changed
  until some has moved or been opened, or while a label of it is not
  revoked (0003, work saved as it was shown), and a retired one brought back
  (Ali, 2026-11-12, inventory-tng-f4f). Volunteers' are unique among
  current ones only: a new volunteer may share a retired one's name. A
  volunteer's custody location is made again under their name each time
  they hold stock.
