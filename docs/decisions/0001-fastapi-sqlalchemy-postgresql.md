# 0001 — FastAPI, SQLAlchemy 2 and PostgreSQL

**Status:** accepted · 2026-10-30

## Context

The old app was Django with Django REST Framework and a React front end. It
was hard to follow. Much of its behaviour was decided at runtime by
metaclasses, introspection and the Django admin, so reading a route did not
tell you what it did. Type checking needed plugins.

We want code where each route says in its own signature what it reads, what it
returns and what guards it. `ty` should check that without plugins. The sheet
being replaced is small: about 135 items and 1,000–1,900 movements a year.

## Decision

- **FastAPI** with **pydantic v2** for routes and their inputs and outputs.
- **SQLAlchemy 2**, typed with `Mapped[...]`, and **Alembic** for migrations.
- **PostgreSQL**, reached through **psycopg 3**.
- **Sync throughout,** run by uvicorn (0007). At this size, sync code is
  simpler and fast enough.
- **Guards are plain values** passed to routes as dependencies. `/api/me`
  reads one capabilities table.
- **No generic admin**, neither Django's nor sqladmin. Admin screens are
  ordinary pages, written like any other.

We weighed staying on Django to match MeshDB, NYC Mesh's other app, and
rejected it for the reasons above.

## Consequences

- A route can be understood by reading it, and `ty` checks it as written.
- The stack no longer mirrors MeshDB, so some shared know-how is lost.
  Volunteers who know Django will have less to reuse here.
- Without Django we lose its built-in admin, auth and forms. The admin pages
  must be built, and sign-in becomes this project's own glue over established
  libraries (see 0004).
