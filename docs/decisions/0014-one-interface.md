# 0014 — Administrator powers appear in the volunteer app, not a second one

**Status:** accepted

## Context

[Decision 0013](0013-administrator-sign-in.md) settles how an administrator
proves who they are. This one settles where they then work.

The obvious answer is the Django admin, which is already present, already
behind a login, and generated from the models — register a model and it is
editable, with filters, search and history, for the cost of three lines. It is
close to free, and it stays current almost by itself.

It is also a different application. Its navigation, vocabulary and idiom are
Django's, not this project's: an administrator who has just been looking at the
item list, with its balances and its packaging chips, has to leave, find the
same item under a different name in a different layout, and translate between
two mental models of the same data. For someone correcting a count that a
volunteer got wrong, that switch is the whole task, and it is jarring out of
proportion to the change being made.

The people involved are also the same people. NYC Mesh administrators are
volunteers who also happen to hold the staff flag. They will be in the
volunteer app already, because that is where the stock is.

Against that: rebuilding the catalogue's editing surface in the single-page
app is the largest piece of work in this plan, and unlike the Django admin it
will not keep itself current as the models change.

## Decision

**The single-page application is the default administrative interface. The
Django admin is retained as a fallback and stays complete.**

1. **Administrator capability arrives in the same session and the same app.**
   Signing in does not navigate anywhere else; it changes what the interface
   offers. The item list gains editing, the volunteer picker gains merging, a
   label gains revocation — in place, where the thing already is.

2. **The API grows catalogue write endpoints**, permissioned to staff. These
   are the operations [decision 0012](0012-two-populations.md) reserves for
   authenticated people, and they are what the interface in point 1 is built
   on. Volunteers reaching them get a refusal, not a hidden button.

3. **The server reports what the caller may do**, so the interface renders from
   its answer rather than guessing. A client that draws an editing control it is
   not allowed to use is a bug report waiting to happen.

4. **The Django admin remains, with every model registered, and a test says
   so.** It is the path that still works when the single-page app does not —
   a broken deployment, a model with no bespoke interface yet, a correction
   somebody needs to make at three in the morning. Its currency is nearly free,
   so the obligation is small and worth honouring: a test asserts that every
   concrete model in the domain app is registered, which turns "kept up to
   date" into a build failure rather than a promise.

5. **Destructive operations require re-authentication.** Merging volunteers,
   revoking labels and editing the catalogue prompt again for whichever factor
   [decision 0013](0013-administrator-sign-in.md) established, even inside a
   valid session. This is the mitigation for the
   consequence below, and it is deliberately narrow: appending to the ledger,
   which is what an administrator does most often, is not affected.

## Consequences

- **This re-couples what decision 0012 separated, and that is the cost.** The
  value of two populations is that the surface reachable without a credential
  is append-only. Putting administrative capability in the same application
  means a script injected into that application reaches the destructive
  operations too — from the browser of somebody who legitimately holds them.

  It does not make the merge wrong: an administrator using a separate app is
  equally lost to a compromise of that app. But it raises what the volunteer
  app's own integrity is worth, so a strict Content-Security-Policy and point 5
  are requirements of this decision rather than general good practice, and the
  Django admin's value as an out-of-band path goes up rather than down.

- **The catalogue write API is now on the critical path.** It was listed as not
  built; it becomes a prerequisite for the administrative interface, and it is
  the bulk of the work this decision creates.

- **Two interfaces over one model must not disagree.** Rules enforced only in
  the single-page app would be absent from the Django admin, which is exactly
  the gap `inventory-tng-fi5` already tracks for the API's own rules. Anything
  this work adds belongs in the model or the database, not in a view.

- **The single-page app's currency is a standing cost.** Point 4's test keeps
  the Django admin complete automatically; nothing equivalent exists for the
  bespoke interface, so a new model appears in the fallback immediately and in
  the primary interface only when somebody builds it. That asymmetry is
  acceptable — it is why the fallback is kept — but it should be expected
  rather than discovered.

## References

- [The Django admin site](https://docs.djangoproject.com/en/stable/ref/contrib/admin/)
  — what point 4 retains, and why keeping it current costs so little.
- [Content Security Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CSP)
  — MDN. The mitigation the first consequence makes a requirement.
- The [decision brief](../briefs/authorisation.md) covers this choice for a
  non-implementer audience, including what was given up by not using the Django
  admin as the primary interface.
