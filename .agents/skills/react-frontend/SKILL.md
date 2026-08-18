---
name: react-frontend
description: Use when working in frontend/ on the inventory-tng React single-page app - components, MUI theming, Vite configuration, API calls, or frontend tests.
---

# Frontend conventions

Commands (dev server, build, lint, format, test) are in
[DEVELOPERS.md](../../../DEVELOPERS.md#frontend). Stack rationale is in
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
- TypeScript is strict. Do not reach for `any` to get past a type error.

## State

There is no state management library, because nothing yet needs one. If the
multi-scan QR flow turns out to need shared state beyond React's own hooks, that
is a decision worth recording in
[docs/decisions/](../../../docs/decisions/) rather than adding quietly.

## Not yet built

The QR scanning flow — the feature this project exists for — is still being
designed, including which browser scanning library to use. Check `bd ready`
before starting on it.
