# 0011 — One cart, one transaction: QR scanning and the batch endpoint

**Status:** proposed — the shape is settled; two questions marked below need the
project owner before the workflow ships.

## Context

This is the feature the project exists for. The system being replaced prints a
QR code per item encoding a Google Form URL hardcoded to *Checking Out*, with
the item's display name in a query parameter. One scan is therefore one form,
one item, one submission. The measured consequences are in
[decision 0008](0008-stock-ledger-transfer-graph.md#context) and are not
repeated here; the burst figure there is the whole case for batching, because
the bursts exist *because* one form can carry one item.

Three further facts shape this design. The first is solid, the other two are
weaker than they look and are stated with their limits.

**The volunteer's own proposal was not a scanning feature.** The mockup in the
#inventory thread shows a single QR posted on the wall — "only 1 QR code to
scan" — opening a page that lists every item with its count, a
`-1 / [count] / +1` control beside each (the middle box is editable; the
annotation reads "Can only check in/out by +1/-1 buttons or typing in count"),
and one **Save** at the bottom. Per-item scanning does not appear in it at all.
That is a statement about what the work feels like: you come back from an
install, you know what you took, you want to record five items and leave.

**Some printed labels do not scan.** In June 2023 one volunteer reported "the
ones with the faded ink never scan for me". Another queried it, and the thread
ends with a volunteer offering to reprint every label that day — so the
often-quoted "half the QR codes don't scan" is one person's estimate about a
label set that may already have been replaced. It is enough to justify not
making the camera the only way in; it is not a measurement.

**Connectivity where stock is kept is unmeasured.** The device is usually a
phone, and the free-text notes place stock in a basement and a mesh room, but
nobody has measured the signal. This design avoids a network round trip between
one scan and the next rather than assuming one is affordable — an assumption
worth testing before the caching in decision 6 is extended any further.

## Decision

### 1. The cart is the interface; scanning is one of three ways to fill it

The client builds a **cart** of lines — item, quantity, direction — and posts it
as exactly one `StockTransaction` with one `StockMovement` per line. Nothing is
recorded until the volunteer presses Save.

| Method | For | Requires |
| --- | --- | --- |
| Browse or search the item list | The mockup's flow; desktop; a label that will not scan | Nothing |
| Camera scan | A phone at the shelf, many items | Camera permission, secure context |
| Typed or wedge-scanned code | A USB/Bluetooth scanner, or reading the code printed under a dead QR | Nothing |

This ordering is the single most important decision here. Scanning is an
*accelerator over* a workflow that already works without it, not a precondition
for it, so faded labels, denied camera permission, a desktop with no camera and
iOS quirks all degrade to "find it in the list" rather than to a dead end.

A keyboard-wedge scanner types the decoded string followed by Enter, so it needs
no browser API — one focused input that submits on Enter covers it, and that
same input is the manual-entry path for an unreadable label. WebHID is not used:
keyboard emulation already works in every browser, including those WebHID does
not support.

### 2. Decode in WebAssembly, behind the `BarcodeDetector` interface

The browser has a native barcode API. Checking rather than assuming it is what
settles this. From [MDN's compatibility data](https://developer.mozilla.org/en-US/docs/Web/API/BarcodeDetector)
and [Chrome's capability documentation](https://developer.chrome.com/docs/capabilities/shape-detection),
as of 2026-08-18:

| Browser | Native `BarcodeDetector` |
| --- | --- |
| Chrome / Edge, Android | Yes, 83+ (Google Play Services required) |
| Chrome, macOS | 83+, but recorded as a partial implementation, and it failed silently on macOS Ventura and later before Chrome 113 |
| Chrome, ChromeOS | 88+ |
| Edge, desktop | macOS only |
| Chrome / Edge, **Windows and Linux** | **No.** Chrome documents support on macOS, ChromeOS and Android only |
| Safari, macOS and iOS | Present since 17.0 but **off by default** behind a feature flag |
| Firefox, any platform | **No** |

It is also a WICG Community Group draft, not on the W3C standards track. So on a
volunteer's Windows or Linux desktop, in Firefox, or in Safari there is no
native decoder — most of the desktop half of "must work on a phone and on a
desktop browser".

**Decode in WebAssembly on every platform, behind the `BarcodeDetector`
interface**, using the [`barcode-detector`](https://github.com/Sec-ant/barcode-detector)
ponyfill over [`zxing-wasm`](https://github.com/Sec-ant/zxing-wasm). Resolved
versions belong in `package.json` and the lockfile, not here.

Import the ponyfill class explicitly, **not** the package's side-effect entry
point, which installs a global only where the browser lacks a native
implementation — that form would decode through the OS on Android and through
WASM on Firefox, which is two code paths and two sets of camera behaviour. One
path written against the standard interface means adopting the native API later
is a change of import, not a change to the application.

Three constraints are not optional:

- **Self-host the `.wasm` binary.** The ponyfill fetches it from a public CDN by
  default, which is an external dependency on a page that must work on a weak
  connection. Bundle it as a Vite asset and point the ponyfill at the local URL.
  Add `application/wasm` to `gzip_types` in `frontend/nginx.conf.template` in
  the same change, or roughly a megabyte ships uncompressed.
- **Load it lazily**, by dynamic import when the scanner opens, so it stays off
  the critical path for the list-and-stepper flow most people use most of the
  time.
- **Let the user choose the camera.** `facingMode: "environment"` is a request,
  not a guarantee, and iOS has honoured it with the ultra-wide lens
  ([WebKit 253186](https://bugs.webkit.org/show_bug.cgi?id=253186)), which
  cannot focus close enough to read a small label. Offer a picker from
  `enumerateDevices()`.

`@zxing/library` and `html5-qrcode` were rejected on maintenance grounds: as of
August 2026 each states in its own README that it is in maintenance mode and
seeking new maintainers or owners. A volunteer-run project should not adopt a
dependency whose maintainer has publicly stopped.

### 3. The printed label: an uppercase URL wrapping an opaque code

    HTTPS://INVENTORY.NYCMESH.NET/S/7QK3M2XV9A

**A URL, not a bare token**, because pointing a phone's built-in camera at a
bare token produces a meaningless string; the URL deep-links into the app
instead. **Uppercase**, because URL schemes and hostnames are case-insensitive
and QR alphanumeric mode packs two characters into 11 bits where byte mode
spends 8 per character. **A one-letter path**, because every character shrinks
the modules at a fixed label size and small modules are what faded ink destroys
first.

**The code uses Crockford's Base32 alphabet** — digits and uppercase letters
excluding `I`, `L`, `O`, `U` — chosen for the *typing* path: the resolver
uppercases and folds `I`/`L` to `1` and `O` to `0`, so a code copied by hand off
a dying label cannot go wrong in the way people actually get it wrong. Ten
characters is 50 bits, unguessable in any sense that matters for a token printed
on a wall.

That folding only works while every minted code is Crockford: a code containing
`I`, `L` or `O` folds to a string matching nothing and is unresolvable for the
life of a physical object. **`Label.code` therefore carries a check constraint
on the alphabet and length**, not a rule the minter is trusted to follow — the
minter is one write path, and the importer, fixtures and the admin are others.
This is the standard this repository already applies to weaker invariants
([data model](../data-model.md#where-postgresql-specific-features-are-used)).

Codes stay opaque, as [0008](0008-stock-ledger-transfer-graph.md#decision)
decided: a label carries no item name, so renaming an item cannot break it.

**Printing is part of the design.** Unreadable labels are a printing failure,
not a decoding one, and no library choice fixes it. The generator fixes error
correction level Q rather than the L or M most generators default to, a minimum
module size and quiet zone, the code in human-readable text underneath, and a
print date so an old batch can be replaced as a set. **The generator's output is
asserted by tests** — decode what it produced, check the error-correction level
and the module count against the target physical size — because a rule that
lives only in prose drifts the first time somebody fits a label to a smaller
sticker, and the failure resurfaces months later as labels that will not scan.
That is the same reasoning as the coverage gate in
[0007](0007-test-coverage.md) and the schema gate in
[0010](0010-openapi-version.md).

### 4. Two QR concepts, one mechanism: the wall code is a location label

`Label` already points at an item *or* a location
([data model](../data-model.md#label)), so the mockup's wall QR is simply a
label whose `location` is set: scanning it opens the app with that location
preselected as the cart's source. One code space, one resolve endpoint, one
client path — and the wall code now answers a question the mockup could not,
*where is this stock moving from?*

### 5. One scan is not one unit: `Label` carries the quantity it represents

Many items are counted individually but *handled* in packets, and the label goes
on the packet. Nothing in the data model says what one scanned label means. Zip
ties are stocked in hundreds, sold in packs of 100, and the recorded quantities
are:

    1 ×30    100 ×17    200 ×9    2 ×5    400 ×2    5 ×2

Some volunteers logged `100`, meaning a packet's worth; others logged `1`,
apparently meaning one packet. RJ45 passthrough connectors (10, 20, 50, 100) and
RJ-45 couplers (1, 2, 5, 6, 10) show no such split, which suggests the intended
convention is individual units — but which unit any given historical `1` meant
is not recoverable from the data, and is question 2 below.

**The quantity is never implicit.** After every scan the cart line spells out the
resulting quantity in the item's own unit — *"Zip Ties Reusable — 100 each
(1 packet)"* — editable in one tap. No design that leaves the quantity invisible
is safe, whatever multiplier sits behind it.

**`Label` gains a `quantity`**: what one scan of this token means. A label is
stuck to a physical object and the object has a size, so this is its natural
home, and it lets one item carry a code on the 305 m box and another on the
shelf of offcuts — which no item-level pack size could express. It takes a
positive-value check constraint named in the same style as its peers
(`stock_movement_quantity_positive` and the rest), and it is meaningful only on
an item label, so it is constrained to be `1` on a location label rather than
left to mean nothing — the pattern `location_held_by_iff_custody` already sets.
This is a schema change with a migration and a
[data model](../data-model.md) update; there is no data to migrate yet.

**Measured items are never defaulted.** Where `unit_of_measure` is anything
other than `each`, a scan opens the quantity keypad and requires an entry. This
needs no new field. It is also the safer default regardless of cause: cable
check-ins are frequently not whole-box amounts, which *looks like* volunteers
returning part-used boxes, though that reading rests on 22 check-ins and is
indicative rather than measured.

The browse path gets this for free: the distinct quantities on an item's active
labels *are* its packaging, so the list offers them as one-tap chips — `+1` and
`+100 (packet)` — rather than stepping by one for an item stocked in hundreds.

### 6. The batch endpoint, and what the client keeps

**`GET /api/labels/{code}`** resolves a scanned code, returning `code`, `kind`
(`item` or `location`), `quantity`, `revoked_at`, and whichever of `item` or
`location` is populated. `quantity` is part of the contract because the client
cannot spell out the cart line without it. An unknown code is a `404` and the
client offers item search rather than treating it as a dead end; a **revoked
label returns `200` with `revoked_at` set**, because refusing a scan over a
superseded sticker blocks a volunteer for bookkeeping.

**`POST /api/stock/transactions`** posts the batch: `idempotency_key`, `kind`,
`actor`, `occurred_at`, optional `job_reference`, and `movements` referencing
items **by id, not by label code** — the client has already resolved every code,
so accepting codes here would add a second resolution path that fails after the
volunteer thinks they are done. `201` on success, `200` when the key was already
posted, `400` when nothing was saved.

**All or nothing, but the rejection names every bad line.** A partly-posted
batch writes ledger rows nobody intended, and the ledger is append-only, so they
could only ever be compensated. The whole batch is one database transaction —
which is only tolerable if one submission reports everything wrong at once, so
the `400` body lists each failure by its `index` in the submitted array and the
client highlights those cart lines in place.

**A batch that does not add up to the kind it claims is a `409`.** Every line
can be valid on its own while the batch is not the act it says it is: a
check-out whose second line takes stock from nowhere, a transfer with only one
side. That is a different failure from a malformed line — nothing needs
correcting field by field, the volunteer picked the wrong kind or scanned in
the wrong direction — so it answers `409` with the offending lines by index
rather than folding into the `400`.

A kind says which sides a movement must carry **and which it must not**, and
both halves are checked. Check-outs and consumption need a `from_location`,
check-ins and receipts a `to_location`, transfers both. A receipt must *not*
carry a `from_location`, and consumption must not carry a `to_location`: those
are the two kinds that cross the system's boundary, so naming the far side
claims a shelf was involved that was not. Requiring without forbidding was the
first version of this rule, and it let a receipt drain a warehouse —
permanently, the ledger being append-only. The API refuses it; nothing in the
database does yet, which is `inventory-tng-fi5`. Check-outs and check-ins forbid
nothing, because naming where stock went or came from is the ordinary case.

Adjustments and counts constrain nothing, deliberately: those are how a
volunteer says the shelf disagrees with the system, which is the one claim this
API must never argue with.

**Insufficient stock is a warning, not a rejection.** A check-out that drives a
balance negative is recorded, with a warning in the response. Refusing it is the
mistake the sheet makes: corrections are a large share of that ledger
([0008](0008-stock-ledger-transfer-graph.md#context)) precisely because
volunteers had no way to say "the shelf disagrees with the system". The shelf is
the authority; the answer is a stock count, not a blocked volunteer.

**The idempotency key is minted when the cart opens**, not at submit, so every
retry of the same cart carries the same key. It travels in the request body,
where the model already has a column for it, and the server matches on the key
without hashing the body: hashing to detect two carts under one key would need a
column that does not exist and would turn an invisible client bug into an error
the volunteer cannot act on.

**The key is scoped to the volunteer who sent it.** It is minted on the client,
so it is unique only to the phone that minted it, and two volunteers can produce
the same string. Matching across everybody would answer the second one with the
first one's transaction and discard their scans without saying so — a volunteer
told their work was recorded when it was not, which is the worst thing this
endpoint could do. The partial unique index is on `(actor, idempotency_key)` for
the same reason, so the lookup and the constraint agree.

The cost is that the actor is part of what a retry has to repeat. A cart
resubmitted under a different volunteer is a batch the server has not seen, and
it records a second transaction — so **the client mints a fresh key whenever
the actor changes**, rather than treating the key as belonging to the cart
alone.

**The cart is local.** It lives in the browser and is written to `localStorage`
on every change, so a phone that locks or reloads does not lose 24 scans. The
catalogue and label map are prefetched so a scan resolves without a round trip;
`GET /api/labels/{code}` remains the authority for anything the cache does not
know, and **the cache carries a short expiry** so a label printed while a phone
was cached does not stay unresolvable. State is one `useReducer` behind a
context — no state library, for one array and a few scalars.

Queued offline submission is **deferred, not dropped**: the two things that
would make it a rewrite, the client-side cart and the idempotency key, are being
built now, so a service worker is purely additive later.

The item list with balances, and the bulk label snapshot the cache needs, are
ordinary catalogue reads specified with the read API rather than here.

## Consequences

- **Every printed label must be reissued**, since the payload changes from a
  Google Form URL to an opaque code. That reprint was already being offered in
  the thread this comes from.
- **The hostname is printed on every physical label**, which is the same
  coupling that makes the current Google Form labels unchangeable. The generator
  reads it from configuration, and moving hostname means keeping a permanent
  redirect rather than reprinting stock.
- **The camera path requires HTTPS and fails invisibly without it.** On an
  insecure origin `navigator.mediaDevices` is
  [`undefined`](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
  rather than restricted, so it looks like a bug. `localhost` is exempt;
  **private and LAN addresses are not**, so the app must never be served over
  plain HTTP at `http://10.0.0.5:8080`, which is otherwise the natural way to
  try it from a phone on the mesh. This is the reasoning behind the TLS
  requirement in [deployment](../deployment.md).
- **iOS is the platform to test on.** Third-party iOS browsers have had
  `getUserMedia` since iOS 14.3
  ([WebKit 208667](https://bugs.webkit.org/show_bug.cgi?id=208667)); this design
  assumes an ordinary browser tab and does not depend on being installed.
- **`/S/{code}` becomes a client-side route** nginx must serve `index.html` for.
  The existing `try_files` does this, but it is now load-bearing.
- **A label generator, with its own tests, is in scope**, or none of the
  printing decisions happen.
- **Adding to the cart can never be a silent action**, since the quantity is
  always shown and editable. That is a deliberate half-second per line.

## Questions for the project owner

**1. How is the app itself authorised?** Answered by
[decision 0012](0012-two-populations.md); this design needed no change.

**2. Which unit did the historical zip tie rows mean?** Decision 5 fixes this
going forward but cannot repair the past: a recorded `1` is either one tie or
one packet of a hundred, and only someone who was there can say. This is an
input to the [sheet migration](../data-model.md#migrating-the-existing-sheet),
and in the absence of an answer the safe default is to import the value
literally and flag the item for a physical count rather than guess a multiplier.
