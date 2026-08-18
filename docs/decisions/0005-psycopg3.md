# 0005 — psycopg 3 instead of psycopg2

**Status:** accepted

## Context

MeshDB uses `psycopg2-binary`. psycopg 3 is the current generation of the same
driver, is what Django's own documentation now recommends, and is the version
receiving development.

## Decision

Use `psycopg[binary]` version 3.

## Consequences

- Supported connection pooling and async support if the project ever wants them.
- Matches current Django documentation, so contributors' searches lead to
  applicable answers.
- A difference from MeshDB, but an invisible one: Django's ORM abstracts the
  driver, and no application code changes.
