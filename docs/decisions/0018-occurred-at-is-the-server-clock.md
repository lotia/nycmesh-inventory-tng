# 0018 — `occurred_at` is the server's clock, not the client's

**Status:** accepted

## Context

`StockTransaction.occurred_at` is the ledger's default ordering key, and
[the ledger is append-only](0008-stock-ledger-transfer-graph.md): a batch
recorded at the wrong time cannot be corrected, only compensated by a second
movement that says so.

[Decision 0011](0011-qr-batch-scanning.md#6-the-batch-endpoint-and-what-the-client-keeps)
lists `occurred_at` in the batch request body, and the client does not send it,
so every batch is recorded at the moment Save reached the server. Meanwhile the
cart mints and stores a `createdAt` when it opens, which nothing reads.

That looked like an oversight, and the obvious fix is to send `createdAt`: a
volunteer fills a cart at a shelf with no signal and saves at the door two
hours later, and the ledger should say when the work happened rather than when
the phone found a bar.

The reason it is not that simple is the other end of the same behaviour. A cart
is restored from `localStorage`, so one left open overnight, or reopened days
later, would post a `createdAt` from before the stock moved. That is not a
small error at the margin; it is a wrong, unfixable row in the middle of the
ledger. And the server cannot tell the two apart:
`StockTransactionCreateSerializer` refuses a time more than `CLOCK_SKEW` in the
future and nothing at all in the past, because there is nothing to check a past
time against.

## Decision

**The server's clock decides `occurred_at`, and the client does not send one.**

`cart.createdAt` is removed rather than left unread. A field that is minted,
stored and validated but never used is an invitation to start sending it, and
the whole of this record is why that would be wrong.

The endpoint keeps accepting `occurred_at`, and it is worth being honest that
this leaves a field no client sends — the same shape as the `createdAt` being
deleted a paragraph above. The two differ in one way that decides it: `createdAt`
was the client's own state, which nothing outside the client could ever have a
use for, while `occurred_at` is a published contract that
[0011](0011-qr-batch-scanning.md#6-the-batch-endpoint-and-what-the-client-keeps)
section 6 specifies and that another open question turns on. Removing it would
answer that question by making it impossible, which is not this record's to do.

The question is the offline queue, `inventory-tng-ykw`: a batch filled during an
outage and replayed when the network returns is recorded, under this decision, at
the replay. That is the signal-less shelf again and worse, because an outage can
last a day. This record does not solve it, and the honest reading is that the
answer there will be a time the volunteer states deliberately rather than one the
client asserts silently — see the first consequence below.

The sheet importer is not a reason either way. It writes through the ORM, past
the serializers, as [0016](0016-invariants-for-every-writer.md) and
`serializers.py` both record, so nothing it does depends on this field being in
a request body.

Why the server's clock wins:

- **It is the only time anything can vouch for.** A client time cannot be
  validated in the past, and a wrong device clock or a stale restored cart
  writes a permanent row.
  [Decision 0016](0016-invariants-for-every-writer.md) puts rules where every
  writer meets them; a client-asserted past has no such place.
- **What it costs is small and bounded.** The balance is identical either way,
  since a movement's effect does not depend on its timestamp. What shifts is
  the ordering of batches recorded within a few hours of each other, and which
  day a late-evening batch lands on.
- **What the alternative costs is unbounded.** A restored cart can be wrong by
  days, and nothing downstream can detect it.

## Consequences

- A batch filled at a shelf and saved at the door is recorded at the door. When
  the difference matters to somebody, the answer is a field for the time the
  work happened, entered deliberately and visible in the interface — not a
  timestamp the client asserts and the server cannot check.
- `occurred_at` on a live batch is always the server's, so an unexpected time
  in the ledger means a clock problem on the server or an import, and not a
  volunteer's phone.
- The importer supplies its own `occurred_at`, through the ORM rather than the
  API. What time an imported row should carry is `inventory-tng-a82`'s to
  settle, not this record's.
- Removing `createdAt` shortens the stored cart. An older stored cart still
  restores: the shape check requires the fields it names and ignores the rest.
