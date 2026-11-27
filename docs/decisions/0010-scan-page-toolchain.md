# 0010 — How the scan page is built and served

**Status:** accepted · 2026-11-01 · builds on 0002 (one client-side page, in
TypeScript) · amended 2026-11-22 (`Referer` to the app's own pages)

## Context

0002 settled that one page, scan-and-batch, runs in the browser and is
written in TypeScript. It did not say how that page is built, which libraries
it rests on, or how a server-rendered app serves it. The old project's React
app showed what to keep (the decoder, the outbox, the Playwright specs) and
what cost too much (a component library, Emotion, a bundle shaped for a
single-page app).

The page is used on phones over cellular. It needs the camera, which only
works over HTTPS or on localhost. It holds the cart and the outbox in
`localStorage` and relies on the device cookie (0009), both of which belong
to one origin in one browser.

## Decision

- **The code lives in `frontend/`**, beside `backend/`. It has two entry
  points: `/scan` and `/register`. Registering lives in the same bundle so
  it shares the API client and can resend the outbox the moment the device
  is registered, in the browser that holds it.
- **Vite builds it** into files whose names carry a hash of their contents,
  plus a manifest. The Jinja templates for `/scan` and `/register` write the
  script and style tags from the manifest (Vite's backend integration). In
  development they point at Vite's dev server instead.
- **Same origin as the API.** No CORS, and the device cookie is sent with
  every save without the page touching it.
- **Compressed once, at build.** ServeStatic's command line writes a Brotli
  (`.br`, at its highest quality) and a gzip (`.gz`, level 9) copy beside
  each built file. ServeStatic, wrapping the ASGI app, serves `/static`: it
  picks the smallest copy the browser's `Accept-Encoding` allows, sets
  `Content-Encoding` and `Vary: Accept-Encoding`, and keeps the original's
  type, `application/wasm` included. Starlette's `StaticFiles` serves only
  the file a URL names, so it cannot do this.
- **Caching follows the hashes.** Everything under `/static/assets/` is
  cached for a year as immutable. ServeStatic's own test for hashed names
  does not match Vite's, so it is told which files are immutable. HTML is
  never cached, so a deploy is new HTML naming new files.
- **HTML and JSON are not compressed**, for now. They are small, and
  compressing a response that holds a secret beside text an attacker can
  influence leaks the secret through its size (BREACH). Revisit with the
  sign-in pages (inventory-tng-8i4.12) if size matters.
- **Node runs at build time only.** The container builds the bundle in a
  stage of its own and copies the files into the Python image. CI runs the
  frontend's checks beside the backend's.
- **Well-maintained libraries over code of our own:**
  - Preact with signals for the UI: React's model at a fraction of the size.
  - openapi-typescript and openapi-fetch, generated from FastAPI's schema,
    so a change to the API that breaks the page fails the type check. The
    schema and the generated types are committed, and checked in CI.
    openapi-typescript 7.13 needs TypeScript 5's compiler API, which
    TypeScript 7 does not have (openapi-typescript issue #2841), so it runs
    from `frontend/tools/openapi/` with its own TypeScript 5.9.3. The pages
    type-check with TypeScript 7. Fold the generator back into `frontend/`
    once a release supports TypeScript 7.
  - Valibot to check what comes out of storage and off the network.
  - The barcode-detector ponyfill over zxing-wasm, served from this origin
    and loaded only when the camera opens: many browsers have no native
    `BarcodeDetector`.
  - Biome to lint and format, Vitest for unit tests, Playwright for the
    browser.
  - Search starts as plain substring matching; Fuse.js if that falls short.
- **No cryptography of our own in the page** (AGENTS.md rule 3). The
  idempotency key comes from the `uuid` library, which uses
  `crypto.randomUUID()` where the page is secure and
  `crypto.getRandomValues()` where it is not, such as a phone on plain http
  in development; `randomUUID` alone would leave Save broken there. The
  cookie is `HttpOnly`; phrases are hashed on the server. Any cryptography
  the page needs later comes from a library or the browser's Web Crypto
  API, never code of our own.

For the generator we also rejected orval, which works with TypeScript 7
but commits a copy of its fetch code into every endpoint's function, and
whose status types claim a reply can only have a status the schema lists.

We rejected React (about ten times Preact's size, for one page), MUI and
Emotion (heavy, and not shared with the Jinja pages, which use one plain
stylesheet), plain DOM code or Lit (the cart and outbox state gets fiddly by
hand), a service worker (clients are assumed online, 0002), IndexedDB
(`localStorage` holds a few batches easily, 0009), serving the bundle
from another origin (it would need CORS and break the cookie's simplicity),
compressing at the ingress (work on every uncached response, at the lower
levels that allows; nothing compresses in compose; and it would compress
pages holding secrets), and a `StaticFiles` subclass of our own that picks
the compressed copy (fiddly header code ServeStatic already gets right).

## Consequences

- Contributors need Node for the frontend, pinned in `mise.toml`, as well as
  Python.
- A tab opened before a deploy can ask for a file the new build no longer
  has. The page treats a failed lazy import as a reason to keep the cart and
  reload.
- **Across a deploy, each build keeps to its own storage** (Ali,
  2026-11-12, inventory-tng-omi). The cart and the outbox are kept under
  keys with a version, and a build reads and writes only its own: a new
  build never copies an older one's batches, so a change to what is kept
  needs no copying code, and Valibot's objects stay strict. Instead, a tab
  of an older build sends its own batches and lets the volunteer save the
  cart, then asks for a reload. It learns that a newer build is out from
  another tab's stamp in storage (each build stamps its id, the time it
  was made, on what it keeps) or from the server, whose reply to a batch
  names its build. What an older build left under its keys is listed to the
  volunteer (Ali, 2026-11-12, inventory-tng-kk1), read only in the parts
  every version kept (the kind, who, the places, items and how much, and
  when), for them to put each back in the cart, to check and save again,
  or to discard. An older key is removed only once nothing in it is left
  to list. We rejected only reporting it, which left it on the phone for
  good with no one able to act on it, and an administrator's page, as the
  volunteer holding the phone is the one who can. So the server keeps
  taking what older builds send: a field is never made required in the
  deploy that adds it. When it is, the server answers a batch from a build
  before that one 409 `build_too_old`, before checking it, and the tab
  keeps the batch and asks for a reload, where a 422 would have it
  refused. Only a batch that names its build is checked so (Ali,
  2026-11-12, inventory-tng-omi.1): another client of the API names none,
  and a 409 on every batch would leave it no way to save, where a 422
  tells it which field it lacks. A test holds the server to it (Ali,
  2026-11-12, inventory-tng-iv6): it keeps what the batch endpoint's schema
  accepts, and fails on any change that accepts less, not only a field made
  required, and on one that accepts more until the copy is written again, so
  it never measures against a stale one. Rules inside validators are not in
  the schema, and are watched by hand. We rejected copying between keys, which
  earlier builds did and which every change to what is kept made harder, and
  reading leniently (`v.looseObject`), which keeps unknown fields but still
  needs a new key for a changed value. The page checks that 409 by its reason
  alone (Ali, 2026-11-12, inventory-tng-op6), the one reply not held to match
  its generated type exactly, rather than freezing its other fields: only a
  page older than the server reads it, so a later server may change them
  without that page sending the batch again for ever. Each batch names the
  build that queued it (Ali, 2026-11-13, inventory-tng-1cj), not the build
  of the tab that sends it: builds that keep the same shape share an outbox
  key, so a newer tab, after a reload or open beside an older one, would
  otherwise send an older build's batch as its own, and a limit narrowed
  without a new key would refuse it (422) rather than hold it back. Held
  back, an older build's batch is listed with what older builds left, and
  only a batch of the tab's own build, or a newer one, makes the tab too
  old. We rejected making every change that sets `OLDEST_BUILD` take a new
  outbox key, which listed, rather than sent, the saves of the build
  before left on phones with no tab of it open, and accepting the gap.
  Ali chose the words and the behaviour the volunteer meets (Ali,
  2026-11-13, inventory-tng-3c0): until the cart is saved and its saves
  sent, the tab says nothing of a newer build, then "The scanner has been
  updated." with an Upgrade button; too old, it says at once that the
  volunteer must upgrade and that their data is kept. Each one listed
  says when it was kept, by date and time in the device's own format,
  as "Kept on this device 14 Nov 2026, 09:30, not sent." or, for a
  cart, "Added on 14 Nov 2026, 09:30 but not sent" (Ali, 2026-11-16,
  inventory-tng-6fl), so two carts of one day are told apart. We
  rejected the date alone, first chosen, which left them alike. The
  newer build puts an older save that goes back whole into an empty
  cart without a tap, and lists the rest to add to the cart or discard;
  one holding an item retired since is named from the catalogue, which
  now lists retired items' names apart from those offered, and goes in
  the cart only once that line is discarded (inventory-tng-0su). We
  rejected asking only for a tap on each, which Ali found needless where
  nothing needs the volunteer, and naming such an item only by its
  absence, which left the volunteer unable to tell what the save held.
  A save put back keeps its batch key, and the cart it fills is saved
  under it (Ali, 2026-11-13, inventory-tng-co9): an older tab open beside
  may be sending it as the page loads, and the server's idempotent key
  then takes it once, or refuses it changed, which the page says. We
  rejected putting back only saves the server says it does not hold,
  which still leaves the window between asking and saving.
  A cart gets its batch key when its first line is added, not at Record,
  and drops it when emptied or discarded (Ali, 2026-11-13,
  inventory-tng-e2d): a newer build takes a cart over from an older one's
  storage, and an older tab recording it meanwhile then sends the same
  key, so the server takes it once. A cart a build from before filled
  has no key until a tab of this one changes it, or puts it back, and
  until then the race remains. We rejected keeping keys only for carts
  filled from a save, which left the race at every change of the cart's
  storage key.
  Add to cart on one another tab changed since it was listed puts
  nothing in the cart: the page lists it again as it is now and says,
  bold as a whole, "That one was changed in another tab. Check it, then
  add it to the cart again." (Ali, 2026-11-14, inventory-tng-8gj), or,
  if as it is now it offers no Add to cart, "That one was changed in
  another tab. Check it again." An entry's Discard, or a line's, on one
  changed meanwhile discards nothing and says, bold as a whole, "This
  item was changed in another tab. Check it before discarding." One
  the page was putting back without a tap is tried again as it is now.
  One gone meanwhile leaves the list, and nothing is said. We rejected
  saying it had gone, which under an entry still listed was untrue.
  A count of a package the catalogue no longer offers is left out of
  what goes back and said, as a resized one is (inventory-tng-adb). We
  rejected putting it back loose, which says loose units moved where
  sealed packages did.
  Each line that cannot go back says why under it, and has a Discard of
  its own that drops that line from what the older build kept (Ali,
  2026-11-13, inventory-tng-0su); an entry with nothing to put back
  offers only Discard. One that says lines could not be read stays once
  its last listed line goes, until its own Discard, so the note is not
  lost unseen. After a line's Discard, focus goes to the entry's heading,
  not the page, so the volunteer keeps their place, and taps on the
  entry's buttons are ignored for half a second, so a double tap's second
  cannot discard the line that moves up (Ali, 2026-11-13). We rejected
  asking before each line's Discard, which slows every one.
  When a tap takes the entry itself away (its last line's Discard, its
  own Discard confirmed, or Add to cart), every entry's taps are ignored
  as long, and focus goes to the heading of the entry that moved up, or
  the list's if none did (Ali, 2026-11-27, inventory-tng-ensw).
  When it takes the last entry away, the list's space stays blank as
  long, so the cart form below does not move up under the finger, and
  focus goes to the Cart heading; a tap in that space puts focus back
  there (Ali, 2026-11-27, inventory-tng-lm1b). None of these focus
  moves scrolls the heading into view, as the page moving would put
  something new under the finger (Ali, 2026-11-27).
  A save keeps its batch key while its lines go, as
  one put back and changed in the cart does: an older tab may have sent
  it as it was, and the key then has the server take it once. We
  rejected one note for the whole entry, which left the volunteer to
  work out which line it meant, and dropping the key with a line, which
  could record the save twice.
  A line whose package was retired with none left names it, as the
  catalogue lists such packages apart from those offered, and only one
  the catalogue no longer has at all is "a removed package or item";
  once added to the cart, the status names each line's item before its
  note (Ali, 2026-11-13, inventory-tng-0su). We rejected naming the
  package only by the amount, which left the volunteer to work out what
  was counted.
  A failure or refusal note on the scan page drops "Sorry." and is bold
  as a whole, the counts the page wrote in it in bold italic; a
  refusal's problems, listed after "Not recorded.", are not bold (Ali,
  2026-11-13, inventory-tng-93z). We rejected bolding only the words
  naming the problem, which Ali found hard to spot once seen, opening
  "Sorry.", which delays the problem, and italicising the numbers of
  names and the server's words, which are no counts.
  In "Carts from this device", a save held until the volunteer upgrades
  is in a caution box like the upgrade banner's, not read out, like
  every note there (Ali, 2026-11-16, inventory-tng-odn): it should stand out
  without reading as a failure. We rejected plain small text, which did
  not stand out, and the alert's style, which reads as a failure.
  Once the tab is too old, every save waiting there is held, the one the
  server held back included, and each says "Held until you upgrade." in
  that box, never "Sending…", while the banner keeps the full note and
  the Upgrade button (Ali, 2026-11-16, inventory-tng-n7yv). The build's
  other tabs learn from the one held back that they are too old too,
  but only from a write that holds one back, which the storage event
  shows as it was before and after: one held before a tab loaded stays
  kept until sent, and after a rollback would hold a current tab for
  good (found in review, inventory-tng-1trb, 2026-11-16). We rejected
  clearing the hold when the build is next accepted, which needs a
  send, so offline the stale hold still spreads, and comparing with
  what the tab last read, which its own writes may have read the hold
  into.
  After the reload each is sent as the build that queued it, and one the
  server still holds back is listed with what older builds left. We
  rejected the full note on every held save, which repeats the banner,
  and the full note on the one held back with plain small text on the
  others.
  The outbox keeps why a batch was refused, or waits, as a code with
  its data (the HTTP status and tries, a 422's problems by line or
  report with each one's kind), and the page makes the words as it
  shows them (Ali, 2026-11-14, inventory-tng-7x2; kinds, not the
  server's words, since 2026-11-16, decision 0022). We rejected keeping
  the sentence, which tied the stored shape to the wording, so a change
  of words took a new key, and had the page match sentences to know
  what happened. Older keys keep main's sentences, read for their code
  and shown as kept.
  A refused batch in the outbox list lists its lines, numbered as its
  problems name them, so the volunteer can compare it with what was
  recorded. One put back from an older build and refused as another tab
  recorded it first makes the page ask for the catalogue and stock at
  once, as its periodic refresh does, so the stock shows what was
  recorded, and stays listed with Dismiss, saying "Not recorded: another
  tab recorded this cart before your changes. Scan again only what you
  changed." (Ali, 2026-11-14, inventory-tng-g0e). It replaces a Reload
  button in place of Dismiss (inventory-tng-93z), held back as a newer
  build's reload is while the cart held lines or batches were sending,
  with a note for each wait: refreshing in place gives the volunteer the
  same stock, holds nothing back and needs one note. We rejected leaving
  only Dismiss without a refresh, which dropped the note before the
  volunteer had seen the stock as it is. Such a batch never goes in a
  cart again, in the older list too, which offers only its Discard (Ali,
  inventory-tng-wyq): what it held is recorded, and once more would
  record it twice.
  The server's refusal says what it recorded under the key, to the
  request that sent it, and the save shows that under "Recorded by the
  other tab (transaction n)", then "Your changes, not recorded", each
  line the volunteer changed since the cart went back as "n more", "new"
  or "n fewer", in place of "Scan
  again only what you changed.", which stays only for a server that
  says nothing (Ali, 2026-11-14, inventory-tng-0gg). That note could
  read as covering lines rescanned since, and so record them twice.
  "Add the difference to cart" puts what was added or raised in an
  empty cart under a new key; what was taken from an open package never
  goes back so, as the package may have changed. A line taken down
  says "Recorded 2 × Bag of 50, you changed it to 1. An administrator
  will correct it.": the ledger never changes, and a volunteer makes no
  adjustment (0003). The server keeps that difference itself as it
  refuses the save, checked against the transaction it recorded, rather
  than the page reporting it later: no request more to lose or retry,
  and a device can flag only what it sent of a cart recorded. Neither
  side alone says which batch is the newer: a tab of the build before,
  sending the cart as it was after a newer tab changed and recorded it,
  would otherwise flag that tab's additions. So a cart put back under its
  key keeps the lines it went back with, and sends them (`changed_from`):
  the volunteer changed only what differs from those, and took off only
  what it holds less of than both those and what was recorded (review,
  2026-11-14). A batch without them, from the build before or a cart
  never put back, changed nothing of its own, and flags nothing. A save
  another tab of this build recorded first is never sent under the key,
  so it is sent once nothing under the key waits, only for that answer.
  What the server recorded is kept with the refused save in the outbox
  under its key, as optional fields: a tab of the build before drops
  them, which leaves the save as that build shows it. We rejected a new
  storage key, which would have listed every save waiting at a deploy
  with what older builds left.
  Ali reworded it all (2026-11-15, inventory-tng-0gg), in place of every
  earlier wording of this refusal, the older list's too, in three parts:
  the alert, bold as a whole, "Some items were not recorded. Details
  below."; the heading "Recorded in another tab in transaction 412", or,
  where this browser's own tab recorded it (it could not empty the cart,
  as when storage was full), "Already recorded from this tab in
  transaction 412", with no reason given, and the lines recorded; then
  the heading "Not recorded. Make any changes and record again." over
  each difference. A removed line reads "RJ45 connectors: removed", with
  "Recorded 2 × Bag of 50, you removed it. An administrator will correct
  it."; a loose count keeps its unit. From a server that says nothing,
  the alert and that heading over the save's lines. What was added or
  raised goes in an empty cart by itself, as a save put back does, saying
  "These are in your cart, waiting to be recorded." until recorded; while
  the cart has lines, "Add these to cart" waits beside them, which, as
  Add to cart in the older list, takes only an empty cart. We rejected
  keeping the button alone, a tap more for what is nearly always wanted,
  and adding to a cart with lines, which may be of another kind or
  between other places. The page counts each add, by itself or by a tap,
  as a telemetry event of its own, two an hour taken from the times to
  save (20 to 18). No entry in the list changes height as its save
  finishes, so a tap on Dismiss lands where it was aimed (Ali,
  2026-11-19, inventory-tng-2lad): one being sent keeps Dismiss's room,
  the button unseen, unfocusable and not read out, and once its
  difference is recorded, "These are in your cart", if shown, is hidden
  in the same way, not removed, keeping its room. We rejected
  listing the newest last, which changes the order volunteers see.
  A Dismiss guards a double tap as the older list's taps do: every
  entry's taps are ignored as long, focus goes, without scrolling, to
  the heading of the entry that moved up, or the list's, and once the
  last is dismissed the list's space stays blank as long and focus goes
  to the Cart heading (Ali, 2026-11-27, inventory-tng-34ns). We
  rejected asking before a refused save's Dismiss, which slows every
  one.
  A save refused (422) whose problems name lines offers "Record the
  rest" (Ali, 2026-11-20, inventory-tng-clvh): it puts the save's other
  lines in an empty cart, under a key made from the save's, and the
  cart's status names each line taken off, "item: taken off." and the
  problem's words, so nothing is dropped unseen; the volunteer then
  presses Record. A refused save recorded nothing, so the rest is never
  recorded twice. It is never sent by itself. As "Add these to cart", it
  takes only an empty cart, and the save stays listed, saying "The other
  lines are in your cart, waiting to be recorded." until recorded, then
  keeping that room, until Dismiss. The page counts each tap as a
  telemetry event of its own, two an hour added beside the rest. Ali
  renamed the button "Add the rest to cart" on 2026-11-20
  (inventory-tng-r18l), as it records nothing itself, and then "Add to
  cart" the same day (inventory-tng-r87e).
  The page's words drop "save" (Ali, 2026-11-13, inventory-tng-wyq): the
  cart lives in the browser, written to storage as it changes, so what
  the browser holds is "kept", and what the inventory takes is
  "recorded"; the main button is Record, an entry is "Sending…" until
  the inventory answers, then "Recorded (transaction n)", and the list is
  "Carts from this device". Discard cart, beside Record, empties the cart
  after an in-page confirmation, as an older entry's Discard asks. The
  names in code, and the metric of the time to Record, keep "save", as
  volunteers never see them and a renamed metric loses its history. We
  rejected keeping "Save", which says the cart is not kept until it is
  pressed, when every change already is, and emptying a cart only by
  removing each line. Each discard is a telemetry event, four an hour
  added to the page's budget, not taken from another's: the page sends
  60 events an hour at most, well under the server's 30 reports a minute
  (Ali, 2026-11-16, inventory-tng-6wr).
- Compression behaves the same in compose and in Kubernetes, because the
  app serves the compressed files itself. The image holds three copies of
  each file; the bundle is small, so that costs little.
- ServeStatic is a younger fork of WhiteNoise, made to add ASGI. If it
  stalls, a server such as Caddy or nginx, which serves the same `.br` and
  `.gz` files, can replace it without changing the build.
- **Every response carries an enforced Content-Security-Policy** (Ali,
  2026-11-16, inventory-tng-e0y), defined in
  `backend/src/inventory_tng/security_headers.py`. Scripts, styles, images
  and requests come from this origin only, and the policy allows
  `'wasm-unsafe-eval'` for the decoder. No page has an inline script,
  `<style>` or style attribute: the administrator pages' inline scripts
  (sending a form once, the Print and Copy buttons) moved into
  `frontend/src/admin.ts`, which Vite builds, and a test refuses any in a
  template. Two things need a nonce, made for each response with Python's
  `secrets`: in development, the `<style>` elements Vite's dev server adds,
  whose client reads the nonce from the page, with the dev server's address
  and websocket allowed there only; and the API's docs page, FastAPI's,
  whose inline script starts Swagger UI from its CDN, which that page alone
  allows. That page is served in development only (decided in the review
  of inventory-tng-e0y, 2026-11-16): the CDN's script would run as this
  origin's, with a signed-in administrator's cookie, so a compromised CDN
  could act as them. We rejected serving Swagger UI's files ourselves from
  a pinned package (more to build and keep current for a page operators
  rarely need) and keeping the CDN with Subresource Integrity hashes
  (FastAPI's page has none, and they would have to follow its versions).
  The schema, `/api/schema`, is served everywhere. Ali chose to enforce
  the policy from the start, as nothing is deployed and both browser
  suites fail on any violation, which they listen for in
  every page they open; we rejected report-only first. Violations are
  counted (Ali, same day): browsers report them to an endpoint the policy
  names (`report-uri`, and `report-to` for browsers with the Reporting API,
  which sends only over HTTPS), counted in `inventory.csp.violations` by
  the directive violated, from a fixed list or "other", and rate limited by
  address as the pages' telemetry is. A report that names no violated
  directive is refused and not counted, with a log line giving only the
  reason: browsers always name it, and counting such a request as "other"
  would show violations that never happened (Ali, 2026-11-17,
  inventory-tng-e0y). Nothing else a report says is kept
  or logged: it names pages, addresses and what the page held. We rejected
  sending no reports. Beside it, every response says `nosniff`, sends
  `Referer` to the app's own pages only (`Referrer-Policy: same-origin`,
  see the amendment: reset and sign-in links carry a token in their
  address, which no other site should see), and allows the camera
  to this origin only, denying every other feature a page could ask for
  (`Permissions-Policy`).
- **The policy allows one style attribute, WebKit's own for a `<select>`**
  (Ali, 2026-11-17, inventory-tng-y6af). Safari 27.2 gives each select's
  inner element `style="text-overflow:inherit"` and wrongly checks it
  against the page's policy (WebKit bug 327212, a regression from WebKit
  321159@main): without the exception, every page with a select sent a
  `style-src-attr` report, which the server counted, logged and limited by
  address, and the select lost an author's `text-overflow`. So
  `style-src-attr` allows `'unsafe-hashes'` and that text's SHA-256 hash,
  made with `hashlib` in `security_headers.py` and checked against the text
  by a test. A browser test showed, in Playwright 1.64.1's WebKit, which
  has the bug, that with it a select is refused nothing and keeps its
  ellipsis; Playwright 1.65's WebKit has the fix, so the tests now run
  without the bug. `'unsafe-hashes'` allows a style attribute only if its
  exact text hashes to a listed hash, so a page's own
  `style="text-overflow:inherit"` is allowed too, which is harmless: it
  only passes on the parent's value. Remove the exception once the Safari
  versions we support ship the fix, WebKit 324283@main (in Safari
  Technology Preview 256; no stable release named yet). We rejected
  sending `'report-sample'` and not counting reports whose sample is that
  text (server code, and samples are free text to keep out of logs),
  dropping every `style-src-attr` report (it hides real ones), and counting
  them (one report per select on each Safari 27.2 page).
- Testing the camera on a phone in development needs HTTPS for the dev
  server, or a tunnel.

## Amendment: `Referer` to the app's own pages (2026-11-22)

Decided by Ali on 2026-11-22 (inventory-tng-7f6e): every response sends
`Referrer-Policy: same-origin`, not `no-referrer`. Under `no-referrer`,
browsers send `Origin: null` on a same-origin form post or fetch. Current
browsers also send `Sec-Fetch-Site`, which the cross-site check (0012)
trusts first, but one without it (Safari before 16.4, so iPhones on iOS
15) fell back to `Origin`, saw `null`, and was refused every save and
sign-in. Under `same-origin`, the app's own requests carry `Referer` and
their real `Origin`, and other sites get neither. A post with
`Origin: null` and no `Sec-Fetch-Site` is still refused.

What `no-referrer` kept from the app is nothing it lacked: its own access
log is off and its request log has no headers, and a link's token is
already in the address any proxy logs on the `GET`. The web search link
keeps `rel="noreferrer"`, as it goes to another site.

We rejected keeping `no-referrer` and stating the oldest browsers
supported, and leaving it as it was.
