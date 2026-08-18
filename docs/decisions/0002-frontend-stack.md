# 0002 — Vite + React SPA instead of meshforms

**Status:** accepted

## Context

The obvious move was to reuse or fork
[meshforms](https://github.com/nycmeshnet/meshforms), NYC Mesh's existing
frontend for MeshDB. Reviewing it in August 2026 showed it had gone stale:

| | meshforms | current |
| --- | --- | --- |
| Next.js | 14.2 | 16.3 |
| React | 18.2 | 19.2 |
| MUI | 5.15 | 9.3 |
| TypeScript | 5.3.3 | 7.0 |

Every commit for roughly the preceding year was a Dependabot bump of a CI
action; there was no feature work. It also carried three overlapping styling
systems (MUI, Bootstrap, Sass), both the end-of-life `aws-sdk` v2 and v3, `msw`
request mocking in production dependencies, and a `postgres` client package —
the frontend connecting directly to the database rather than through the API.

Adopting it would have meant a four-major-version upgrade across the whole
stack plus unpicking the direct database access, before writing any features.

## Decision

Build a new frontend: Vite, React 19, TypeScript, and MUI 9, compiled to static
assets and served by nginx.

## Consequences

- No Node runtime in production. One less container to run, patch, and monitor —
  it matters for a volunteer-operated system.
- No SSR. This is an authenticated internal tool, so search indexing and
  first-paint-from-server buy nothing.
- MUI is kept, so components are recognisable to anyone who has worked on
  meshforms.
- We do not benefit from meshforms' existing screens, and any shared UI work
  between the projects has to be done deliberately.
- TypeScript is pinned to 5.x rather than the newly released 7.x, until the
  lint and build ecosystem has settled on it.
