# Code style and typing

What the linters, the formatters and the type checkers hold every change to,
and the one command per language that fixes what can be fixed. None of it is
advisory: CI fails on all of it, exactly as it does on a failing test.

## Code style

Style is not a matter of taste here — it is enforced, and CI fails on it. Both
languages use one fast tool that covers linting *and* formatting, so there is a
single configuration file per language and nothing to argue about in review.

| | Backend (Python) | Frontend (TypeScript) |
| --- | --- | --- |
| Lint + format | [ruff](https://docs.astral.sh/ruff/) | [Biome](https://biomejs.dev/) |
| Type check | [ty](https://github.com/astral-sh/ty) | `tsc --noEmit` |
| Configuration | `backend/pyproject.toml` | `frontend/biome.json`, `frontend/tsconfig.json` |
| Check everything | `uv run ruff check . && uv run ruff format --check . && uv run ty check src` | `npm run lint && npm run typecheck` |
| Fix what can be fixed | `uv run ruff check --fix . && uv run ruff format .` | `npm run lint:fix` |

Run the fixer before you open a pull request and the check will pass.

Both toolchains follow the same principle: one binary replacing what used to be
three or four. On the Python side ruff does the work of black, isort, and
flake8, and `ty` type checks; both come from [Astral](https://astral.sh/),
alongside `uv`, which manages the environment. On the TypeScript side Biome does
the work of ESLint and Prettier. Rationale is in
[docs/decisions/0004-python-tooling.md](decisions/0004-python-tooling.md)
and
[docs/decisions/0006-frontend-tooling.md](decisions/0006-frontend-tooling.md).

Notable rules that are deliberate rather than default: Python targets a
120-character line and enables the bugbear, pyupgrade, and Django rule sets;
TypeScript forbids `any` and non-null assertions, so a type error has to be
solved rather than silenced.

## Typing

**Every function you write is annotated.** Arguments and return types, in both
languages. This is not advisory: `ruff` enforces it on the Python side through
the `ANN` rule set, and CI fails on it, exactly as it does for tests and
formatting. TypeScript gets this from the compiler already.

### The bar is "a type is present", not "the best possible type"

*This latitude is Python-only.* TypeScript infers return types reliably and
`any` stays forbidden there, as [Code style](#code-style) says — on the frontend
`any` is an escape from the type system, whereas in Python `Any` is the on-ramp
onto it.

Types here exist to make the code readable and to let editors help you. They are
not a puzzle you have to solve before your contribution counts.

```python
from typing import Any

def summarise(rows: Any) -> Any:      # fine. passes. ship it.
    ...

def summarise(rows: list[dict[str, Any]]) -> dict[str, int]:   # better, later
    ...
```

`Any` is deliberately allowed — the `ANN401` rule that would forbid it is
switched off on purpose. If you cannot work out the right type, write `Any`,
open the pull request, and someone will suggest something tighter. That is a
review conversation, not a blocker. Reviewers: asking for a more specific type
is a suggestion, never a rejection.

The reasoning behind requiring annotations at all is in
[decision 0009](decisions/0009-type-annotations-required.md).

### Getting help from the machinery

Three commands, in the order you will want them.

| I want to… | Run |
| --- | --- |
| See everything that is missing a type | `uv run ruff check --select ANN .` |
| Add the obvious ones automatically | `uv run ruff check --select ANN --fix --unsafe-fixes .` |
| Ask what type something actually is | put `reveal_type(x)` on a line, then `uv run ty check src` |

The second adds return annotations such as `-> None` where it can prove them.
It is "unsafe" only in ruff's sense that it edits annotations rather than
whitespace; the scoping to `--select ANN` keeps it from touching anything else.
Run `uv run ruff format .` afterwards.

The third is the one worth remembering. `reveal_type()` needs no import and is
understood by the type checker directly:

```python
def get(self, request: Request) -> Response:
    with connection.cursor() as cursor:
        reveal_type(cursor)        # ty prints: `CursorWrapper`
```

```
info[revealed-type]: Revealed type
 --> src/inventory/views.py:22:21
  |
  |         reveal_type(cursor)
  |                     ^^^^^^ `CursorWrapper`
```

Copy the answer into the annotation and delete the `reveal_type` line. This
works for any expression, and it is the fastest way to type a Django or DRF
object whose type you would otherwise have to go looking for.

### What the checker cannot see

Django generates some attributes at runtime. `django-stubs` describes them
through a mypy plugin, and `ty` does not run plugins yet, so it reports them as
missing even though the code is correct:

| Pattern | Use instead |
| --- | --- |
| `obj.get_kind_display()` | `obj.Kind(obj.kind).label` — explicit and typed |
| `item.identifiers`, `item.history` (reverse accessors, history managers) | Nothing better exists. Add `# ty: ignore[unresolved-attribute]` |

Suppress with `# ty: ignore[<rule>]` on the line, naming the rule rather than
silencing everything. Most are `unresolved-attribute`, from the table above;
the rest are places a third-party stub is narrower than the function it
describes, and each one carries a comment saying which stub and why.

A suppression is a statement that the checker is wrong, so if you are not sure
it is, it is a bug worth looking at instead — and the comment beside it is what
lets the next reader tell the two apart. This is the cost of `ty` being pre-1.0
and is expected to shrink — tracked as `inventory-tng-61b`.

### Editor setup

`ty` ships a language server, so your editor can show inferred types as you
type rather than at check time. `.vscode/settings.json` in this repository
configures it, and the devcontainer installs the extensions
(`astral-sh.ty`, `charliermarsh.ruff`). Outside VS Code, point your editor's
LSP client at `uv run ty server`.

Coding agents should use the same three commands above — they are deterministic
and their output is stable enough to act on directly.

### Learning Python typing

If annotations are new to you, these are the ones worth having open. The first
is the most useful by a distance:

- [mypy type-system cheat sheet](https://mypy.readthedocs.io/en/stable/cheat_sheet_py3.html)
  — one page, practical, and applies to `ty` just as well despite the name
- [Python typing guides](https://typing.python.org/en/latest/guides/index.html)
  — the official introduction, longer form
- [`typing` module reference](https://docs.python.org/3/library/typing.html)
  — what is available to import
- [django-stubs](https://github.com/typeddjango/django-stubs) — how Django's own
  types are described, when you need to know what a queryset or a request is

