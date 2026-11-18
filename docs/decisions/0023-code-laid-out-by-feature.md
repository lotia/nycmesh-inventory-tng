# 0023 — Code laid out by feature

**Status:** accepted · 2026-11-17

## Context

In `backend/src/inventory_tng`, routes, page rendering, queries and rules
were mixed in the same files, and the top folder held rules, JSON
endpoints, sign-in routes, plumbing and commands side by side, so it was
hard to find where something happens (inventory-tng-x8bp). Ali decided the
structure below on 2026-11-17.

## Decision

- **Package by feature**, such as `catalogue/`, `stock/` or `devices/`.
  Every feature names its files by the same roles: `<thing>.py` for a
  table's rules and writes (its owner, 0016), `queries.py`, `forms.py`,
  `routes.py` for HTML pages (`<page>_routes.py` where a feature has
  several, as `admins/sign_in_routes.py`), `api.py` for JSON,
  `schemas.py`, `paths.py` and `command.py`. URLs do not change.
- **`web/`** holds what every server-rendered page shares; **`core/`**
  holds plumbing, such as settings, db, history, logs, telemetry,
  masking, mail, phrases, local_time and local_env. Core means plumbing,
  not business rules. **`dev/`** holds developer tools, such as stacks,
  dev_database and seed.
- **No service layer and no repositories.** A route file does routing
  only: decorated handlers (`@router.get`, `@router.post`), one router per
  module, included by name in `app.py`, never discovered. A handler reads
  the request, calls `forms.py`, `queries.py` and the rules, then renders,
  returns or redirects. Queries are plain functions taking a `Session`;
  a trivial `session.get` may stay in a handler. Forms are plain
  functions too, given the form's fields as a mapping of text or the
  values its `Form()` parameters read: the handler reads the request.
- **One entry point for operator commands**, `python -m
  inventory_tng.manage <command>`, from a fixed table of each feature's
  `command.py`.
- **Imports go one way**, held by `backend/tests/test_import_directions.py`:
  core imports no feature; rules, queries and forms import neither
  FastAPI nor `web/`, but for `web/formatting.py` and `web/forms.py`,
  which themselves import neither (inventory-tng-atmx.2,
  inventory-tng-atmx.7); route files run no queries (`select`,
  `session.execute`, `session.scalar` or `scalars`); no feature's routes
  import another feature's routes, linking through its `paths.py`
  instead; package `__init__.py` files stay empty; and only the entry
  point imports a feature's `command.py`. The breaks found when the test
  was written are listed in it and may only shrink.

We rejected a central routes file wired with `add_api_route`, which is not
how FastAPI apps are usually read, and import-linter, as the rules fit a
short test. `core/` is the name FastAPI's full-stack template uses;
`platform` clashes with Python's own module.

## Consequences

- Older decision records keep the paths of their time. The table below
  gives each old path's new home, both under `backend/src/inventory_tng/`;
  a row is added with each move.
- `models.py` and the templates folder stay whole for now (Ali,
  2026-11-17; inventory-tng-5q07).

| Old path | New path |
| --- | --- |
| `command.py` | `core/command.py` |
| `database_role.py` | `core/database_role.py` |
| `db.py` | `core/db.py` |
| `history.py` | `core/history.py` |
| `local_env.py` | `core/local_env.py` |
| `local_time.py` | `core/local_time.py` |
| `logs.py` | `core/logs.py` |
| `mail.py` | `core/mail.py` |
| `masking.py` | `core/masking.py` |
| `phrases.py` | `core/phrases.py` |
| `rate_limits.py` | `core/rate_limits.py` |
| `request_logging.py` | `core/request_logging.py` |
| `settings.py` | `core/settings.py` |
| `telemetry.py` | `core/telemetry.py` |
| `time_zones.py` | `core/time_zones.py` |
| `dev_database.py` | `dev/dev_database.py` |
| `seed.py` | `dev/seed.py` |
| `stacks.py` | `dev/stacks.py` |
| `pages.py`: `amount`, `printed_length` | `web/formatting.py` |
| `web.py` | `web/browser.py` |
| `admins.py` | `admins/command.py` |
| `sign_in.py`: what every administrator page depends on | `admins/guards.py` |
| `sign_in.py`: the sign-in pages | `admins/sign_in_routes.py` |
| `ledger.py`: the batch endpoint, `save_batch` | `scan/batch_api.py` |
| `ledger.py`: the batch request and reply schemas | `scan/batch_schemas.py` |
| `ledger.py`: the balance queries | `stock/balance.py` |
| `devices.py` | `devices/devices.py` |
| `devices.py`: what every route needing a device depends on | `devices/guards.py` |
| `devices.py`: the registration endpoint, `register_device` | `devices/api.py` |
| `devices.py`: the request and reply schemas | `devices/schemas.py` |
| `catalogue.py` | `scan/catalogue_api.py` |
| `catalogue.py`: the reply schemas | `scan/catalogue_schemas.py` |
| `admin/stock.py`: its queries | `stock/queries.py` |
| `admin/counts.py`: its queries | `stock/queries.py` |
| `admin/length_checks.py`: its queries | `stock/queries.py` |
| `admin/used_up.py`: its query | `stock/queries.py` |
| `admin/over_recorded.py`: its query | `stock/queries.py` |
| `admin/items.py`: `_in_stock` | `stock/queries.py`: `item_total` |
| `scan/catalogue_api.py`: its queries | `scan/queries.py` |
| `admin/admins.py`: its query | `admins/queries.py` |
| `admin/categories.py`: its queries | `catalogue/queries.py` |
| `admin/items.py`: its other queries | `catalogue/queries.py` |
| `admin/reading_rules.py`: its query | `catalogue/queries.py` |
| `admin/devices.py`: its queries | `devices/queries.py` |
| `admin/home.py`: its query | `catalogue/queries.py`: `current_count` |
| `labels.py` | `labels/labels.py` |
| `admin/labels.py`: its queries | `labels/queries.py` |
| `admin/locations.py`: its queries | `places/queries.py` |
| `admin/settings.py`: its query | `site_settings/queries.py` |
| `volunteers.py` | `volunteers/volunteers.py` |
| `admin/volunteers.py`: its queries | `volunteers/queries.py` |
| `admin/locations.py`: its volunteers' names | `volunteers/queries.py`: `volunteer_names` |
| `health.py`: its query | `core/db.py`: `ping` |
| `admin/common.py`: how a form is checked | `web/forms.py` |
| `admin/items.py`: how its forms are read, and their problems in words | `catalogue/forms.py` |
| `admin/common.py`: the rules put into words (`RULES`) | `web/forms.py` |
| `admin/counts.py`: how its forms are read, and their problems in words | `stock/forms.py` |
| `admin/over_recorded.py`: how its forms are read, and their problems in words | `stock/forms.py` |
| `admin/devices.py`: how its forms are read, and their problems in words | `devices/forms.py` |
| `admin/length_checks.py`: how its forms are read | `stock/forms.py` |
| `admin/reading_rules.py`: how its forms are read, and their problems in words | `catalogue/forms.py` |
| `admin/volunteers.py`: how its forms are read, and their problems in words | `volunteers/forms.py` |
