# 0002 — Server-rendered pages and a scan page

**Status:** accepted · 2026-10-30

## Context

The old front end was a React single-page app. Most of its code was admin
forms. It tied the backend to the wire habits of Django REST Framework, and it
still did not work offline. Its good parts were the cart, the outbox and the
scanner.

Volunteers use the app in the storage space and on roofs. Both have signal.

## Decision

- **Most pages are server-rendered** with Jinja, using htmx where it helps:
  catalogue, locations, volunteers, labels, counts and reports.
- **One small client-side page, in TypeScript, handles scan-and-batch.** A
  volunteer fills a cart by camera, scanner gun, typed code or a list, then
  presses Save once.
- **Clients are assumed to be online.** A small outbox keeps the batch on the
  phone until the server accepts it. It resends with the same idempotency key,
  so a dropped connection loses nothing and saves nothing twice.
- The cart, outbox and scanner are carried over from the old app in spirit,
  not copied file for file.

We rejected keeping a single-page app for everything: it cost the most code
for the least benefit.

## Consequences

- Most screens are plain HTML from the server, with no client state to keep
  in step.
- The backend serves pages, not a general API shaped for a client framework.
  The scan page uses a few JSON endpoints.
- The app does not work fully offline. The outbox covers short gaps only.
- There are two ways to build a page. Anything new should be server-rendered
  unless it needs the camera or a cart.
