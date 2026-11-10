# 0019 — How pages look and act: links, your account, actions and lists

**Status:** accepted · 2026-11-10 · builds on 0014 (one stylesheet in NYC
Mesh's look)

## Context

0014 settled the stylesheet and MeshDB's look, but not how pages behave:
whether a thing is a link or a button, where the account links go, how
records are retired and listed. Each page had decided for itself. Research
into established design systems (GOV.UK, USWDS, Primer, Polaris, Atlassian,
Material) and WCAG 2.2 AA, with options drawn in the app's own colours, was
reviewed with Ali on 2026-11-10 (inventory-tng-8i4.60.12; the mock-ups are
at <https://claude.ai/artifact/Ju4jnjECT3qHVEqUWwTj2D>). The rules below
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
  for what cannot be undone (revoking, resetting a sign-in, merging).
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
  Pages of 50 past 50 rows. An empty list says what to do next.
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
- Each kind of list gains search, filters and paging as it grows past what
  one screen shows.
