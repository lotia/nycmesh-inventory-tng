# 0012 — Volunteers append without signing in; administrators sign in

**Status:** accepted

## Context

The volunteer proposal that started this project asks for "no user accounts,
no phone app needed" and for the system to be reachable from anywhere.
[Decision 0008](0008-stock-ledger-transfer-graph.md) point 5 follows it:
volunteers are a pick-list with no password. But the backend's default
permission class is `IsAuthenticated`, and those three things cannot all hold.

An attempt to resolve it by trusting the browser was investigated and
abandoned. The idea was that the backend would hold a list of allowed origins
and issue a token to any request arriving from one of them. It cannot work:
`Origin` is a request header, so `curl -H 'Origin: https://inventory.nycmesh.net'`
produces a byte-identical request to the real frontend's. Nor can a page prove
which certificate served it — no browser API exposes that, and the backend
already knows its own. The IETF's guidance for browser applications is explicit
that a public client cannot hold a secret and cannot be authenticated by one.

What the investigation did establish is that the question has two halves, and
they have different answers. The API's entire write surface is two endpoints:
`POST /api/stock/transactions`, which appends to a ledger that cannot be
rewritten, and `POST /api/volunteers`, which adds a name to a list. Everything
else it exposes is a read, and every operation that can change or destroy what
is already recorded lives in the Django admin, behind a login.

So the *shape* is already there. The permissions are not: every endpoint but
the index and the health check requires a session today, the volunteer ones
included, which is exactly why the app cannot yet be put in front of anybody.

## Decision

**Two populations, with different obligations.**

1. **Volunteers do not sign in.** They append to the ledger and add themselves
   to the volunteer list, and nothing else. Their work is attributed to a name
   they pick, not to a credential they hold.

2. **Administrators sign in.** Every operation that edits the catalogue, merges
   volunteers, revokes labels or corrects the ledger requires an authenticated
   person. How that sign-in works is [decision 0013](0013-administrator-sign-in.md);
   where it appears is [decision 0014](0014-one-interface.md).

3. **The two volunteer endpoints will require no client credential.** The
   defences are that the ledger is append-only, that every row carries an
   actor, that writes are rate limited, and that an identified administrator
   can see and compensate anything wrong. This is the posture of the Google
   Form being replaced, chosen rather than inherited.

4. **Enrolled devices are the next step, not the first.** Once the flow is in
   real use and the cost of enrolment friction is known, a browser will
   register a key it cannot export and sign requests with it, turning "anyone
   who can reach the API" into "anyone who has been in the room". Deferred
   because it adds friction to the flow that has to feel fast, and because the
   append-only ledger bounds the cost of waiting. The shape and its reading
   list are on `inventory-tng-jro`; it will need its own record.

5. **Authentication never touches attribution.** Work is attributed through
   `StockTransaction.actor`, a volunteer chosen from a pick-list
   ([data model](../data-model.md#volunteer)), and that stays true whatever
   authenticates the request.

### A third endpoint, and what made it arguable

Amended 2026-08-24. Point 3 named two endpoints, and the consequences below say
any further credential-free endpoint has to be argued against this record
rather than added beside it. This is that argument, for
`POST /api/client-failures`.

**What it is for.** The volunteer app runs on a phone in a basement. When its
scanner stops, or a submission cannot be sent, or a sheet will not print, the
only account of it was a `console.error` nobody was ever going to read. A
volunteer cannot send a stack trace and should not be asked to. Without this
the failures that happen to the people this project is for are the ones nobody
can see, which is the whole of what `inventory-tng-nb8` exists to end.

**Why it does not weaken the posture above.** The argument for that posture is
that the write surface appends and nothing more, so a client that is abused can
write rows that are wrong but cannot delete, edit or rewrite. This endpoint is
weaker still: it writes **no row at all**. It records a log line and returns
`204`. There is nothing to correct afterwards, nothing to compensate, and no
table for a later reader to have to clean.

**What it can be abused for, and what bounds it.** Volume, and putting text
somebody chose into a collector. The same rate limits as the two endpoints
above, one bounded field for the message, and no field for anything else — no
URL, no user agent, no identifier. `inventory_tng.redaction` is deny-by-default
about what may reach a collector; this is the one place a caller writes into
that stream, so what it may carry is a list rather than whatever a client
thinks to send. What no list can police is the sentence itself, which is the
same boundary every log message in this system has.

**What it is not.** It is not somewhere to send telemetry generally. A
browser's spans go to the collector through a path that requires the signed
token of decision 0021, and that stays true; this carries the failures that are
worth hearing about from a device nobody has flagged.

## Consequences

- **The dangerous surface is small and already gated.** A volunteer client that
  is abused, or compromised, can write rows that are wrong. It cannot delete,
  cannot edit, and cannot rewrite. Correction is a compensating movement made
  by somebody identified.

- **Rate limiting stops being optional.** With no credential on the append
  endpoints it is the only thing between the ledger and a script, so it is a
  requirement of this decision rather than a later hardening.

- **The open posture must be revisited if the surface grows.** This decision
  holds precisely because the volunteer endpoints append and nothing more. Any
  new endpoint reachable without a credential has to be argued against this
  record, not added beside it.

- **Both halves must be visible in the API.** An endpoint's audience is now part
  of its contract, so the generated schema has to make clear which operations
  require a session.

## References

A version of this investigation written for collaborators rather than
implementers, with the rejected options side by side, is in
[docs/briefs/authorisation.md](../briefs/authorisation.md). These records are
authoritative where the two disagree.

- [OAuth 2.0 for Browser-Based Applications](https://www.ietf.org/archive/id/draft-ietf-oauth-browser-based-apps-26.html)
  — IETF. Sections 5.1 and 6.3.3 are what rule out authenticating the frontend
  by its origin, and what say a public client cannot hold a secret.
- [CORS Origin Header Scrutiny](https://owasp.org/www-community/attacks/CORS_OriginHeaderScrutiny)
  — OWASP, on the `Origin` header not being an authentication signal.
