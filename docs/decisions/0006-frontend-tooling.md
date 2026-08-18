# 0006 — Biome for TypeScript linting and formatting

**Status:** accepted

## Context

The frontend started with ESLint and Prettier, the conventional pairing. That is
two tools, two configuration files, two plugin ecosystems, and a well-known
seam where the linter and the formatter disagree about the same line.

The backend had already adopted the opposite approach — one fast tool covering
lint and format (see
[0004-python-tooling.md](0004-python-tooling.md)). [Biome](https://biomejs.dev/)
is the equivalent for JavaScript and TypeScript.

## Decision

Use Biome for both linting and formatting. Configuration lives in
`frontend/biome.json`. Type checking stays with `tsc --noEmit`, since Biome does
not type check.

ESLint, Prettier, and their plugins were removed.

## Consequences

- One tool and one configuration file per language, matching the backend. A
  contributor learns the same shape twice instead of two different ones.
- Much faster; the whole project lints in milliseconds.
- Linting and formatting can no longer contradict each other.
- Biome's rule set is smaller than ESLint's ecosystem. Rules we rely on —
  exhaustive hook dependencies, hooks at top level, no `any`, no non-null
  assertion — are all present and set to `error`. A niche ESLint plugin would
  not be available to us.
- Biome versions its configuration schema; `biome migrate --write` updates the
  file when upgrading.
