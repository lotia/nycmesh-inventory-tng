## What changed

<!-- What does this do, and why? Link the issue if there is one. -->

## Issues in this batch

<!--
One row per issue, in the order they were landed. A single issue on its own is
one row and needs no epic; more than one belongs to an epic that records the
batch. See ../DEVELOPERS.md#pull-requests.
-->

| Issue | |
| --- | --- |
|  |  |

- [ ] **Every commit holds work from exactly one issue**, and names it. Review
      fixes go back to the commit that caused them; a finding that belongs to no
      single issue became a new one rather than widening a commit.

## Definition of Done

See [DEVELOPERS.md](../DEVELOPERS.md#definition-of-done).

- [ ] Tests and coverage thresholds pass (`uv run pytest`, `npm test`)
- [ ] Lint, format, and type checks pass
- [ ] New behaviour has a test. Any new coverage exclusion is justified below
- [ ] **Documentation is consistent with this change.** If this alters setup
      steps, commands, environment variables, architecture, the API surface, or
      deployment, the canonical document for that topic is updated *in this pull
      request*. Tick this after checking, not by default.
- [ ] Any decision a future reader would ask "why?" about has a record in
      [docs/decisions/](../docs/decisions/)

## Documentation touched

<!-- List the docs you updated, or write "none needed, checked" and say why. -->
