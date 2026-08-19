---
name: react-frontend
description: Use when working in frontend/ on the inventory-tng React single-page app - components, MUI theming, Vite configuration, API calls, or frontend tests.
---

# Frontend conventions

Commands (dev server, build, lint, test) are in
[DEVELOPERS.md](../../../DEVELOPERS.md#frontend), style rules in
[Code style](../../../DEVELOPERS.md#code-style), and test and coverage
requirements in
[Testing and coverage](../../../DEVELOPERS.md#testing-and-coverage). Stack rationale is in
[docs/decisions/0002-frontend-stack.md](../../../docs/decisions/0002-frontend-stack.md).
This file covers only conventions you cannot infer from the code.

## Calling the API

Always use **relative** paths: `fetch("/api/...")`.

Never read an API base URL from the environment in application code. A
hardcoded or build-time-injected URL breaks the single-origin arrangement
described in
[docs/architecture.md](../../../docs/architecture.md#shape), which is what lets
one frontend image run in every environment.

## Components

- MUI components before custom CSS — why, in
  [docs/architecture.md](../../../docs/architecture.md#frontend).
- Colour and typography decisions belong in `src/theme.ts`, not scattered
  through `sx` props.

## State

There is no state management library. The multi-scan cart is the one piece of
genuinely shared state and deliberately does not add one; its design is settled
in [decision 0011](../../../docs/decisions/0011-qr-batch-scanning.md). Anything
needing more than that is a decision worth recording in
[docs/decisions/](../../../docs/decisions/) rather than adding quietly.

## Tests

Framework, file location, and the coverage threshold `npm test` enforces are in
[Testing and coverage](../../../DEVELOPERS.md#testing-and-coverage); the
frontend thresholds and exclusions themselves live in
`frontend/vite.config.ts`. One convention that is not written down there:

Query by role and accessible name (`getByRole("button", { name: /save/i })`)
rather than by test id or class. It asserts what a user can actually perceive,
and it catches accessibility regressions for free.

## Not yet built

The QR scanning client is not built, though the endpoints it needs are. That
gap and the others are listed in
[docs/architecture.md](../../../docs/architecture.md#not-yet-built). Read
[decision 0011](../../../docs/decisions/0011-qr-batch-scanning.md) before
touching it: it settles the scanning library, what the cart is, and what the
endpoints look like.
