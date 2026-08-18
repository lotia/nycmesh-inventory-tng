# 0004 — Astral toolchain for Python: uv, ruff, ty

**Status:** accepted

## Context

MeshDB lints Python with black, isort, and flake8, and type checks with mypy —
four tools, four configurations, four ways for a contributor's environment to
disagree with CI. Each is a separate install and a separate thing to explain to
someone making their first contribution.

[Astral](https://astral.sh/) publishes a coherent set of Rust-based replacements
that share conventions and configuration style.

## Decision

Use the Astral toolchain throughout the backend:

| Tool | Replaces |
| --- | --- |
| [uv](https://docs.astral.sh/uv/) | pip, pip-tools, virtualenv, poetry |
| [ruff](https://docs.astral.sh/ruff/) | black, isort, flake8, pyupgrade, bugbear |
| [ty](https://github.com/astral-sh/ty) | mypy |

All configuration lives in `backend/pyproject.toml`. Type stubs
(`django-stubs`, `djangorestframework-stubs`) are kept — they describe the
libraries and are not tied to any particular checker.

## Consequences

- One install, one configuration file, one place to look. This is the main
  reason for the change: fewer steps between a new contributor and a passing
  build.
- Substantially faster, so the checks run in a pre-commit-sized amount of time
  rather than being something people skip locally and discover in CI.
- ruff's formatter is black-compatible but not byte-identical, so formatting
  differs slightly from MeshDB's. This affects nothing but diffs.
- **`ty` is pre-1.0** and moving quickly. It currently type checks this codebase
  cleanly, including Django and DRF via their stub packages. If it regresses,
  mypy remains a drop-in fallback: the stubs are already present and only the
  command in [DEVELOPERS.md](../../DEVELOPERS.md#code-style) and CI would change.
  This is a deliberate, reversible bet on tooling that is improving rapidly.
- Adopting `ty` meant writing the health check as a class-based DRF view rather
  than an `@api_view` function, because the decorated function's type does not
  match Django's `path()` overloads. The class-based form is more idiomatic DRF
  anyway.
