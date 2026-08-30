# 0027 — The admin is meant to look different

**Status:** accepted

## Context

Asked by the project owner on 2026-08-30, on being shown that Django's admin
now draws itself and asked whether it should be made to resemble the volunteer
app: *"if the django admin side is usable then don't style it to be similar, it
**should** stand out to make it clear that it is a **different** app and not
part of the frontend."*

Two things had just happened that make the question live rather than
theoretical.

**The admin had been unstyled, and that was a defect.** Under the compose
default it rendered as browser-default HTML — serif type on a transparent
background, every stylesheet answering 404 — because nothing served
`STATIC_ROOT` under gunicorn. That was `inventory-tng-o1uj.1` and it is fixed.
The admin now draws itself in Django's own style.

**The sign-in pages are unstyled too, and that is also a defect.** Everything
under `/accounts/` asks for no stylesheet at all, because allauth ships bare
templates expecting a project to wrap them and this one never has. That is
`inventory-tng-u1am`, and it is being fixed by making those pages match the
app.

Between those two sits the question this record answers. Once the sign-in
pages look like the app, the obvious next step is to make the admin look like
the app as well — it is the last surface that does not, and finishing the set
is exactly what a careful contributor would do next.

That instinct is wrong here, and nothing in the code says so.

## Decision

**The Django admin is not styled to resemble the rest of the application, and
looking like Django is a feature rather than an omission.**

The reason is not aesthetic. The admin is where the application stops holding
your hand — which is a narrower claim than it first sounds, and the narrower
version is the true one:

- **It is not that the admin is unguarded.**
  [0016](0016-invariants-for-every-writer.md) settles that invariants are
  enforced for every writer by trigger, the admin included, and that what a
  serializer adds on top is *reporting* rather than protection. An
  administrator is refused the same impossible things anybody is.
- **It is that nothing is shaped around you.** The app's screens report a
  refusal line by line and are built to keep somebody away from the
  consequences [0008](0008-stock-ledger-transfer-graph.md) and
  [0024](0024-no-hard-delete.md) describe — an append-only ledger, and no
  hard-delete path at all. The admin is where an administrator is trusted with
  those directly, and where a legal-but-wrong edit is nobody's to catch.

Somebody who has left the app and is editing rows should be able to *see* that
they have, without having to remember. A surface continuous with the app hides
the one boundary most worth noticing, and hides it precisely at the moment the
safety net is thinnest. The visual break is doing work.

**This is not an argument for leaving it broken.** An admin with no stylesheet
at all was a bug and was fixed; an admin in Django's own style is not one. The
distinction is between *unstyled* and *differently styled*, and only the first
is a defect.

**The contrast is why `/accounts/` is styled to match.** These two look like
opposite decisions and are the same one. A deliberate difference only reads as
deliberate when everything else is consistent — if the admin stands out and the
sign-in pages also look like nothing in particular, there is no signal, only
three surfaces each looking different for their own reasons. `u1am` is what
gives this record something to stand out from.

## Consequences

- The admin keeps Django's appearance. A pull request that themes it is
  refused on this record rather than on somebody's taste, and whoever wrote it
  is owed this reasoning rather than an opinion.
- Work on the admin's *usability* is untouched by this. Fields, ordering,
  filters, the columns a list shows, whether a page is reachable at all — none
  of that is appearance, and [the administrator's
  guide](../../guides/administrator.md) is where the gaps in it are recorded.
- The sign-in pages are the app's, not the admin's. They are the way in to both,
  and they belong to what is being entered — today overwhelmingly the volunteer
  app, since the [quickstart](../../README.md#quickstart) sends people to port
  8080, where nginx serves the frontend and forwards `/accounts` to Django.
- **What would reopen this: an admin that volunteers rather than administrators
  are routinely sent into.** The whole argument rests on the person in there
  having deliberately gone somewhere different, with the authority that
  implies. If the app ever routes ordinary work through the admin because it
  has no screen for it, the boundary this protects has already been crossed and
  this record should be revisited rather than cited. [The administrator's
  guide](../../guides/administrator.md) is where that shows up first — it
  already sends an administrator there for identifiers and for correcting a
  balance, and both are administrator work by construction, since the
  volunteer's app deliberately offers no stock count. A *volunteer* task
  appearing on that list is the signal.
