# 0016 — The app holds the rules; the database holds data and privileges

**Status:** accepted · 2026-11-08 · amends 0003 (where the rules live,
balance) and 0013 (who checks the time zone)

## Context

The rules that need other rows, the ledger's append-only rule and the
history writer were 17 PL/pgSQL functions run by triggers, beside a balance
view (inventory-tng-8i4.95). Some were also checked in Python, so a rule could
live in two places. Ali wants no logic in the database, so that moving to
another database stays possible and every rule has one home. Nothing is
deployed yet, so the migrations were rewritten rather than added to.

## Decision

- **Every rule is the app's.** Each table is written by one module, through
  one function per kind of write, which locks what the rule reads, checks
  it and writes through the ORM. A rule with more than one condition is a
  pure function with unit tests that need no database; each rule also has a
  PostgreSQL test through its write, and each rule that reads other rows a
  test with two sessions at once (inventory-tng-8i4.102).
- **The database keeps declarations only**: keys, foreign keys, CHECK
  constraints, unique and partial unique indexes, and privileges. A test
  refuses any trigger, function, view, rule or row-level security policy.
- **Privileges keep what must never change.** The app logs in as
  `inventory_app`, which may not update or delete the ledger or the history,
  delete a settings row, truncate any table, or update a column that never
  changes. Migrations run as the owner. The app refuses to start as a login
  that could change or empty the ledger or the history, or empty a settings
  table (DEPLOYMENT.md, Database roles).
- **The app's role has a fixed name.** CloudNativePG makes a managed role by
  whatever name it is given, and each cluster is a server of its own, so
  names cannot collide. A setting would have to reach the migrations and
  could drift from `DATABASE_URL`. Grants are default privileges, so a new
  table needs none of its own.
- **History is written by the app** as it saves, in the same transaction.
  Migrations and SQL run by hand are not recorded.
- **Balance is a query** (`ledger.balance`), not a view.
- **Migrations may be rewritten until a test deployment holds people's
  data.** After that they only go forward; just before production they are
  collapsed into one.

We rejected keeping the triggers that also catch SQL run by hand (the
ledger's and history's): privileges keep the ledger and history as safely
from the app, and the owner can drop a trigger as easily as grant itself a
privilege, so neither binds it.

## Consequences

- A rule is read, tested and changed in one place, in Python.
- Code that writes around a module's functions would skip its rules. Tests
  refuse rows made outside their module and statement writes to tables with
  history, and the app refuses a catalogue write made without the catalogue
  lock.
- Concurrency is the app's to get right: the lock order is written down once
  for the catalogue (`db.locked_row`) and once for the ledger (`ledger.py`).
- PostgreSQL features still in use, and what another database would need:
  - fuzzy search: `pg_trgm` and a GIN index on `item.search_text`, a computed
    column (the largest cost);
  - JSONB history rows, INET addresses, partial and `lower()` indexes, regular
    expressions and `num_nulls()` in CHECKs, identity columns;
  - advisory locks (the catalogue lock), `FOR KEY SHARE` and
    `FOR NO KEY UPDATE`, `LOCK TABLE ... SHARE ROW EXCLUSIVE`;
  - `INSERT ... ON CONFLICT DO NOTHING`, arrays in queries, and
    `has_table_privilege` in the startup check.
