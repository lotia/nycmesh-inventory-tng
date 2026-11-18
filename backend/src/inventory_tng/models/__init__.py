"""The catalogue, locations, volunteers and labels (inventory-tng-8i4.10),
registered devices (inventory-tng-8i4.42), the ledger (inventory-tng-8i4.11)
and administrators' sign-in (docs/decisions/0012), one module per feature
that owns its tables, with what they share in base.py and names.py.

This module imports every one, so importing any model registers every
mapped class; migrations/env.py imports it, so autogenerate sees every
table. A new module of models is imported here.

Each table declares its protection, and each column any flag, as
db.protection says.

Conventions, on every table with history:

- An integer id and `created_at`, set by the database.
- Nothing is deleted. A row is retired (`retired_at`; NULL means current) and
  every foreign key is ON DELETE RESTRICT, in PostgreSQL itself.
- Quantities are numeric(12,3); money is numeric(10,2).
- Names are unique, ignoring case: among current rows, or all of them,
  retired ones too, as each table says. An item's and a package's are
  compared by their key, which ignores spaces and punctuation too, but a
  decimal point between digits (catalogue/names.py; models/names.py,
  KeyedName).

A rule about one row's own values is a check constraint, which raises a
check violation (SQLSTATE 23514) named for the rule, as describe_error()
logs; one PostgreSQL cannot check, such as a known time zone, is the
model's own (@validates, as SiteSettings'). Rules that need other rows are
the app's, checked by each table's one writer: CONTRIBUTING.md, The
catalogue's rules (docs/decisions/0016).
"""

from inventory_tng.models import (  # noqa: F401
    admins,
    catalogue,
    core,
    devices,
    labels,
    places,
    site_settings,
    stock,
    volunteers,
    web,
)
