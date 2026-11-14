# 0010 — How the scan page is built and served

**Status:** accepted · 2026-11-01 · builds on 0002 (one client-side page, in
TypeScript)

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
  volunteer must upgrade and that their data is kept. The newer build puts
  an older save that goes back whole into an empty cart without a tap,
  and lists the rest to add to the cart or discard; one holding an item
  retired since is named from the catalogue, which now lists retired
  items' names apart from those offered, and can only be discarded. We
  rejected asking only for a tap on each, which Ali found needless where
  nothing needs the volunteer, and naming such an item only by its
  absence, which left the volunteer unable to tell what the save held.
  A save put back keeps its batch key, and the cart it fills is saved
  under it (Ali, 2026-11-13, inventory-tng-co9): an older tab open beside
  may be sending it as the page loads, and the server's idempotent key
  then takes it once, or refuses it changed, which the page says. We
  rejected putting back only saves the server says it does not hold,
  which still leaves the window between asking and saving.
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
  A refused batch in the outbox list lists its lines, numbered as its
  problems name them, so the volunteer can compare it with what was
  recorded. One put back from an older build and refused as another tab
  recorded it first asks for a reload, in place of Dismiss, so the page
  shows what was recorded, held back as a newer build's reload is; after
  it, the batch stays listed, no longer asking, until dismissed. We
  rejected leaving only Dismiss, which dropped the note before the
  volunteer had seen the stock as it is.
- Compression behaves the same in compose and in Kubernetes, because the
  app serves the compressed files itself. The image holds three copies of
  each file; the bundle is small, so that costs little.
- ServeStatic is a younger fork of WhiteNoise, made to add ASGI. If it
  stalls, a server such as Caddy or nginx, which serves the same `.br` and
  `.gz` files, can replace it without changing the build.
- The Content-Security-Policy allows `'wasm-unsafe-eval'` for the decoder.
- Testing the camera on a phone in development needs HTTPS for the dev
  server, or a tunnel.
