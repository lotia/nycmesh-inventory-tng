# 0007 — Coverage thresholds enforced in the test command

**Status:** accepted

## Context

Test coverage that is only reported gets looked at once and then ignored.
Coverage that fails the build changes behaviour. But a coverage gate can also be
theatre: a high percentage across a codebase padded with generated files,
configuration, and entry points says nothing about whether the code that can
break is tested.

There is also a failure mode where coverage runs only in CI, so contributors
discover a threshold failure after pushing rather than while working.

## Decision

Three things together:

1. **Coverage runs as part of the ordinary test command** — `uv run pytest` and
   `npm test` — not as a separate CI step. Local and CI runs enforce identical
   rules.
2. **Thresholds fail the command**, at 90% for both backend and frontend. The
   frontend additionally gates branches, functions, and statements.
3. **Exclusion lists carry the real weight.** Entry points, declarative
   configuration, and generated migrations are excluded, so the percentage
   describes code that implements behaviour.

The exclusions and their justifications are documented in
[DEVELOPERS.md](../../DEVELOPERS.md#testing-and-coverage), which is the single
place they are explained.

## Consequences

- Untested new code fails the build, locally and in CI, for everyone including
  AI agents. There is one standard.
- 90% rather than 100% leaves room for the genuinely awkward case without
  inviting tests written purely to move a number.
- The interesting review question shifts from "what is the percentage?" to "is
  this exclusion honest?", which is the question actually worth asking. Adding
  an exclusion requires justification in the pull request.
- Coverage adds a little time to every test run. At this size it is negligible,
  and the parity between local and CI is worth more.
