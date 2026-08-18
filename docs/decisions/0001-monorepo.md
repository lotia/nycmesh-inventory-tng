# 0001 — Backend and frontend in one repository

**Status:** accepted

## Context

MeshDB splits its API ([meshdb](https://github.com/nycmeshnet/meshdb)) and its
frontend ([meshforms](https://github.com/nycmeshnet/meshforms)) across two
repositories. In practice a change to an API field requires coordinated pull
requests in both, and the two drift.

This project is smaller, earlier, and expects heavy churn in the API surface as
the inventory data model takes shape.

## Decision

Backend and frontend live in one repository, in `backend/` and `frontend/`.

Each keeps its own native tooling — `uv` for Python, `npm` for JavaScript. No
monorepo build system (Nx, Turborepo) is layered on top.

## Consequences

- A change spanning both sides is one reviewable commit, not a race between two
  pull requests.
- Refactors that move a responsibility across the boundary are ordinary edits.
- CI runs both suites on every change. At this size that is cheap and
  simpler than path filtering, which can skip a job that a cross-cutting change
  actually needed.
- The two deployable images are still built and versioned independently, so this
  says nothing about how they are released.
