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
    so a change to the API that breaks the page fails the type check.
  - Valibot to check what comes out of storage and off the network.
  - The barcode-detector ponyfill over zxing-wasm, served from this origin
    and loaded only when the camera opens: many browsers have no native
    `BarcodeDetector`.
  - Biome to lint and format, Vitest for unit tests, Playwright for the
    browser.
  - Search starts as plain substring matching; Fuse.js if that falls short.
- **No cryptography in the page** (AGENTS.md rule 3). The idempotency key
  comes from `crypto.randomUUID()`; the cookie is `HttpOnly`; phrases are
  hashed on the server. Biome refuses `crypto.subtle` in `frontend/`.

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
  reload. The cart and outbox keep a versioned storage format that a new
  build must read, so a deploy never strands a queued batch.
- Compression behaves the same in compose and in Kubernetes, because the
  app serves the compressed files itself. The image holds three copies of
  each file; the bundle is small, so that costs little.
- ServeStatic is a younger fork of WhiteNoise, made to add ASGI. If it
  stalls, a server such as Caddy or nginx, which serves the same `.br` and
  `.gz` files, can replace it without changing the build.
- The Content-Security-Policy allows `'wasm-unsafe-eval'` for the decoder.
- Testing the camera on a phone in development needs HTTPS for the dev
  server, or a tunnel.
