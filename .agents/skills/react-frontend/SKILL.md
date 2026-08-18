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

Never read an API base URL from the environment in application code. In
development Vite proxies `/api` to Django; in production nginx does. This is
what lets one frontend image run in every environment, and a hardcoded or
build-time-injected URL breaks that. See
[docs/architecture.md](../../../docs/architecture.md).

## Components

- MUI components before custom CSS. Assembling documented components keeps the
  barrier low for volunteer contributors.
- Colour and typography decisions belong in `src/theme.ts`, not scattered
  through `sx` props.
- TypeScript is strict, and Biome sets `noExplicitAny` and
  `noNonNullAssertion` to `error`. Solve the type error rather than silencing
  it.

## State

There is no state management library, because nothing yet needs one. If the
multi-scan QR flow turns out to need shared state beyond React's own hooks, that
is a decision worth recording in
[docs/decisions/](../../../docs/decisions/) rather than adding quietly.

## Tests

Vitest with [Testing Library](https://testing-library.com/docs/react-testing-library/intro/),
in `*.test.tsx` files next to the code they cover.

`npm test` runs coverage and **fails below the threshold**, so a new component
needs a test in the same change. Thresholds and exclusions live in
`frontend/vite.config.ts` and are explained in
[Testing and coverage](../../../DEVELOPERS.md#testing-and-coverage). Do not widen
the exclusion list to get a build passing.

Query by role and accessible name (`getByRole("button", { name: /save/i })`)
rather than by test id or class. It asserts what a user can actually perceive,
and it catches accessibility regressions for free.

## Not yet built

The QR scanning flow — the feature this project exists for — is still being
designed, including which browser scanning library to use. Check `bd ready`
before starting on it.
