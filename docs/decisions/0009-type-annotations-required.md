# 0009 — Type annotations are required, but not required to be good

**Status:** accepted

## Context

Python's typing is optional; TypeScript's is not. Left alone, that asymmetry
produces a codebase where the frontend is fully typed and the backend is typed
in the places whoever wrote them felt like it — which is the worst of both,
because a reader cannot tell whether an unannotated function is dynamic by
design or dynamic by neglect.

`ty` was already configured and already runs in CI, but a type *checker* does
not require annotations. It checks what is there and infers the rest, so a file
with no annotations at all passes cleanly. Nothing was actually asking for
types.

The obvious fix — turn on annotation enforcement — has a well-known failure
mode. A strict typing gate is a real barrier to a first contribution, and this
project is staffed by volunteers whose Python may be rusty or occasional. A
contributor who writes a working twenty-line function and then cannot get it
past CI because they do not know how to spell the type of a Django queryset is
a contributor the project has just lost. NYC Mesh cannot afford that trade.

## Decision

Require annotations. Do not require them to be precise.

1. **`ruff`'s `ANN` rule set is enabled**, so every function argument and return
   type must be annotated. It runs in the same `ruff check` that CI already
   performs, so there is no new tool, no new command, and no new CI step.
2. **`ANN401` is switched off**, which makes `Any` a legal annotation. The
   requirement is that a type is *present*, not that it is the tightest type
   expressible. `def f(rows: Any) -> Any` passes.
3. **Generated migrations are exempt**, because nobody writes them by hand.
4. **Tightening a type is review feedback, never a blocker.** This is stated
   in [DEVELOPERS.md](../../DEVELOPERS.md#typing) so that reviewers and
   contributors read the same rule.
5. **Deterministic help is provided rather than assumed**: a command that lists
   what is missing, a command that fills in the obvious cases, and
   `reveal_type()` for asking the checker what a type actually is. The commands
   are documented for humans and for coding agents in the same place.

## Consequences

- The annotation bar is a mechanical one a contributor can always clear:
  writing `Any` is always available and always sufficient. Nobody gets stuck.
- Codebase-wide, annotations become a reliable signal again. An unannotated
  function no longer exists, so the question "was this deliberate?" disappears.
- `Any` will appear in the codebase, and some of it will persist. This is the
  accepted cost. It is strictly better than the alternative it replaces, which
  is no annotation at all, and it leaves a greppable marker for anyone who wants
  to improve typing as a self-contained contribution.
- Enforcement lives in `backend/pyproject.toml`, so a local run and a CI run
  agree, and a contributor sees the failure while working rather than after
  pushing.
- `--fix --unsafe-fixes` is documented as an annotation-specific command rather
  than being added to the general fixer, so the blanket
  `ruff check --fix` in [Code style](../../DEVELOPERS.md#code-style) keeps its
  current safety properties.
