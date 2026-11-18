# 0023 — Code laid out by feature

**Status:** accepted · 2026-11-17

## Context

In `backend/src/inventory_tng`, routes, page rendering, queries and rules
were mixed in the same files, and the top folder held rules, JSON
endpoints, sign-in routes, plumbing and commands side by side, so it was
hard to find where something happens (inventory-tng-x8bp). `frontend/src`
held every module of every page in one folder (inventory-tng-x5jz). Ali
decided the structure below on 2026-11-17.

## Decision

- **Package by feature**, such as `catalogue/`, `stock/` or `devices/`.
  Every feature names its files by the same roles: `<thing>.py` for a
  table's rules and writes (its owner, 0016), `queries.py`, `forms.py`,
  `routes.py` for HTML pages, `api.py` for JSON, `schemas.py`, `paths.py`,
  `guards.py` for what every route needing a sign-in or a device depends
  on, and `command.py`. Where a feature has several files of a role, each
  is named for its page or thing, as `admins/sign_in_routes.py`,
  `scan/batch_api.py` or `admins/totp_keys_command.py`. URLs do not
  change.
- **The top of `inventory_tng`** holds only the entry points (`app.py`,
  `server.py`, `__main__.py`, `manage.py` and `openapi.py`) and
  `models/`.
- **`models/`** holds the mapped classes, a module per feature named for
  it, with what they share in `base.py` and `names.py`. Its `__init__.py`
  imports every module, so `Base.metadata`, Alembic and the guards see
  every table (Ali, 2026-11-18, inventory-tng-h657.1). It re-exports
  nothing: an import names the module a model is in.
- **`web/`** holds what every server-rendered page shares; **`core/`**
  holds plumbing, such as settings, db, history, logs, telemetry and mail.
  Core means plumbing, not business rules. **`dev/`** holds developer
  tools.
- **The administrators' navigation** is `web/navigation.py`: every
  section, in order, and its badge, listed by name, set by nothing at
  import (inventory-tng-atmx.10).
  It imports features' `paths.py` and what a badge counts with, never
  their routes, which import `web/pages.py`, which imports it.
- **No service layer and no repositories.** A route file does routing
  only: decorated handlers (`@router.get`, `@router.post`), one router per
  module, included by name in `app.py`, never discovered. A handler reads
  the request, calls `forms.py`, `queries.py` and the rules, then renders,
  returns or redirects. Queries are plain functions taking a `Session`; a
  trivial `session.get` may stay in a handler. Forms are plain functions
  given the form's fields, never the request. Plumbing with an endpoint of
  its own keeps it beside it, as `core/health.py` does.
- **One entry point for operator commands**, `python -m
  inventory_tng.manage <command>`, built with argparse (Ali, 2026-11-17,
  inventory-tng-icnp): a fixed table of each command's name, its module
  and a line on what it does, never discovered. It imports only the
  command chosen; with no command it lists them all. Each command sets up
  its own process (0007). Developer tools, `core/local_env.py`,
  `catalogue/units.py` and `openapi.py` are not operator commands and keep
  their own `python -m` paths (inventory-tng-atmx.9).
- **Imports go one way**, held by `backend/tests/test_import_directions.py`:
  - rules, queries and forms import neither FastAPI nor `web/`, but for
    `web/formatting.py` and `web/forms.py`, which import neither
    themselves (inventory-tng-atmx.2, inventory-tng-atmx.7);
  - route files run no queries (`select`, `execute`, `scalar`, `scalars`);
  - core imports nothing of the app but core and `models/`;
  - no feature's routes import another feature's routes; they link
    through its `paths.py`.

  The breaks found when the test was written are listed in it and may
  only shrink. On 2026-11-17 Ali dropped two rules that caught no real
  harm, package `__init__.py` files staying empty and only the entry point
  importing a command; the same change dropped a third, that a `paths.py`
  imports only core and other `paths.py`, as a cycle through one fails at
  import anyway (inventory-tng-atmx.16).

- **The frontend**, `frontend/src/`, is grouped by page (Ali, 2026-11-17,
  inventory-tng-x5jz): `scan/`, `register/` and `admin/` hold what one
  page uses, `shared/` what more than one does, and `testing/` the
  helpers only tests import. The entries, `styles.css`, `api/`,
  `locales/` and the unit tables stay at the top. No barrel files. Tests
  sit beside their modules. Imports go one way, held by Biome's
  `noRestrictedImports` in `frontend/biome.json`: `shared/` imports no
  page's folder, a page's folder imports no other page's, neither imports
  an entry, and only tests import `testing/`. The patterns match a
  folder's name anywhere in a relative path, never in a package's. Tests
  may import any folder, as some check every page's words together
  (inventory-tng-atmx.14).

We rejected a central routes file wired with `add_api_route`, which is not
how FastAPI apps are usually read, and import-linter, as the rules fit a
short test. `core/` is the name FastAPI's full-stack template uses;
`platform` clashes with Python's own module.

## Consequences

- The templates folder stays whole for now (Ali, 2026-11-17;
  inventory-tng-5q07). `models.py` was split into `models/` on 2026-11-18
  (inventory-tng-h657.1).
- Older decision records keep the paths of their time. The tables below
  give each old path's new home; a later move adds its rows.

In `backend/src/inventory_tng/`, these files moved whole into a feature
and kept their names:

| Folder | Old paths |
| --- | --- |
| `admins/` | `accounts.py`, `activity.py` |
| `catalogue/` | `categories.py`, `items.py`, `names.py`, `reading_rules.py`, `units.py` |
| `core/` | `command.py`, `cross_site.py`, `database_role.py`, `db.py`, `history.py`, `local_env.py`, `local_time.py`, `logs.py`, `mail.py`, `masking.py`, `phrases.py`, `rate_limits.py`, `request_logging.py`, `settings.py`, `telemetry.py`, `time_zones.py` |
| `dev/` | `dev_database.py`, `seed.py`, `stacks.py` |
| `labels/` | `labels.py` |
| `places/` | `locations.py` |
| `scan/` | `bundle.py`, `page_builds.py` |
| `stock/` | `adjustment_reasons.py`, `open_packages.py`, `over_recorded.py`, `admin/needs_action.py` |
| `volunteers/` | `volunteers.py` |
| `web/` | `once.py`, `security_headers.py`, `admin/listing.py` |

The rest were renamed or split, sorted by old path. Unless a row says
otherwise, a page's paths went to its feature's `paths.py`, how its forms
are read, with their problems in words, to its `forms.py`, and its
queries to its `queries.py`.

| Old path | New paths |
| --- | --- |
| `account.py` | `admins/account_routes.py` |
| `admin/__init__.py` | its docstring: `web/admin.py`; the navigation's sections and badges: `web/navigation.py`; the pages' routers, included in order: `app.py` |
| `admin/admins.py` | `admins/admins_routes.py` |
| `admin/categories.py` | `catalogue/categories_routes.py`; `TAKEN`: `catalogue/forms.py`, as `CATEGORY_TAKEN` |
| `admin/common.py` | `web/admin.py`; how a form is checked, and `RULES`: `web/forms.py`; `ADMIN`: `web/paths.py`; `ADMINS`: `admins/paths.py`; `LABELS`: `labels/paths.py`; the sections' names (`SECTION_ITEMS` and the others): `web/navigation.py` |
| `admin/counts.py` | `stock/counts_routes.py`; `badge`: `web/navigation.py`, as `counts_badge` |
| `admin/devices.py` | `devices/routes.py` |
| `admin/home.py` | `admins/home_routes.py`; its query: `catalogue/queries.py`, as `current_count` |
| `admin/items.py` | `catalogue/items_routes.py`; `SORTING`, `SEARCHED`: `catalogue/items_listing.py`; `_in_stock`: `stock/queries.py`, as `item_total` |
| `admin/labels.py` | `labels/routes.py` |
| `admin/length_checks.py` | `stock/length_checks_routes.py` |
| `admin/locations.py` | `places/routes.py`; its volunteers' names: `volunteers/queries.py`, as `volunteer_names` |
| `admin/over_recorded.py` | `stock/over_recorded_routes.py` |
| `admin/reading_rules.py` | `catalogue/reading_rules_routes.py` |
| `admin/settings.py` | `site_settings/routes.py` |
| `admin/stock.py` | `stock/stock_routes.py` |
| `admin/used_up.py` | `stock/used_up_routes.py` |
| `admin/volunteers.py` | `volunteers/routes.py` |
| `admins.py` | `admins/admins_command.py` |
| `catalogue.py` | `scan/catalogue_api.py`; the reply schemas: `scan/catalogue_schemas.py`; its queries: `scan/queries.py` |
| `devices.py` | `devices/devices.py`; `register_device`: `devices/api.py`; the request and reply schemas: `devices/schemas.py`; what every route needing a device depends on: `devices/guards.py`; `REGISTER_PATH`: `devices/paths.py` |
| `health.py` | `core/health.py`; its query: `core/db.py`, as `ping` |
| `ledger.py` | `stock/ledger.py`; the balance queries: `stock/balance.py`; `save_batch`: `scan/batch_api.py`; the batch request and reply schemas: `scan/batch_schemas.py` |
| `models.py` | `models/`: each table in its feature's module; the column types, `whole`, the columns and constraints most tables have, and `Versioned`: `models/base.py`; canonical names, name keys and search folding (`CanonicalName`, `KeyedName`, `Searchable`, `fold` and the rest): `models/names.py`; `_refold`: `models/catalogue.py`; `_reference`, `_one_of`, `_between`, `_unique_name`, `_unique_current_name`, `_name_key_check`: each without its `_` |
| `page_telemetry.py` | `scan/page_telemetry.py`; `over_limit`: `web/browser.py` |
| `pages.py` | `web/pages.py`; `amount`, `printed_length`: `web/formatting.py`; `Badge`, `retired_holding`, `sections`: `web/navigation.py` |
| `registration_codes.py` | `devices/command.py` |
| `resets.py` | `admins/reset_routes.py` |
| `scan_settings.py` | `site_settings/scan_settings.py`; its command: `site_settings/command.py` |
| `sign_in.py` | the sign-in pages: `admins/sign_in_routes.py`; what every administrator page depends on: `admins/guards.py`; `reset_url`: `admins/paths.py` |
| `totp_keys.py` | `admins/totp_keys.py`; its command: `admins/totp_keys_command.py` |
| `unit_readings.py` | `catalogue/unit_readings.py`; its command: `catalogue/command.py` |
| `web.py` | `web/browser.py` |

In `frontend/src/`, these files moved whole, with their tests, and kept
their names:

| Folder | Old paths |
| --- | --- |
| `admin/` | `buttons.ts`, `combobox.ts`, `hints.ts`, `once.ts`, `retire.ts` |
| `register/` | `RegisterPage.tsx` |
| `scan/` | `camera.ts`, `cart.ts`, `decoder.ts`, `labels.ts`, `older.ts`, `OpenPackageCard.tsx`, `recorded.ts`, `Scanner.tsx`, `ScanPage.tsx` |
| `shared/` | `build.ts`, `Field.tsx`, `i18n.ts`, `mount.tsx`, `outbox.ts`, `quantity.ts`, `registration.ts`, `stored.ts`, `telemetry.ts` |
| `testing/` | `test-camera.ts`, `test-catalogue.ts`, `test-fetch.ts`, `test-saves.ts`, `test-scan-page.tsx`, `test-setup.ts`, `test-storage.ts`, each without its `test-` |

Parts of `cart.ts` and `ScanPage.tsx` were split out first:

| Old path | New path |
| --- | --- |
| `cart.test.ts`: its tests of search (`finding`) | `scan/search.test.ts` |
| `cart.ts`: `toBatch`, with `Batch`, the server's limits (`MAX_ID`, `MAX_AMOUNT`, `MAX_LINES`), `openings`, `packageLine`, `withOffered` and the problems it says (`AN_ITEM` to `TOO_MUCH_FOR_ONE`); `batchWords`, with its words (`ITEMS` to `SAID_LENGTHS`) | `scan/batch.ts` |
| `cart.ts`: `offered` and `offeredAt`, the cuts of open packages (`afterCut`, `cutOf`, `usedUpOf`), the batches not yet seen (`unseen`, `keysToAsk`, `MAX_KEYS_ASKED`) and the packages sealed (`sealedCounts`, `sealedKey`) | `scan/offered.ts` |
| `cart.ts`: `search` and `bares`, with the lookups worked out once per catalogue that search reads (`indexOf`, `Named`) | `scan/search.ts` |
| `ScanPage.tsx`: `CartForm`, with `Place`, `PlaceSides` and `CHECK_EVERY_MS` | `scan/CartForm.tsx` |
| `ScanPage.tsx`: `CartLine` | `scan/CartLine.tsx` |
| `ScanPage.tsx`: `Finder` | `scan/Finder.tsx` |
| `ScanPage.tsx`: `KIND_WORDS` | `scan/cart.ts` |
| `ScanPage.tsx`: `loadCatalogue` | `scan/catalogue.ts` |
| `ScanPage.tsx`: `NoteSaid`, `AlertWords`, `Counted`, `ConfirmDiscard`, `keyedByWords`, `RULED_LIST`, `CAUTION_BOX` | `scan/Notes.tsx` |
| `ScanPage.tsx`: `OlderList`, with the words it says of each (`olderSaid`, `olderKind`, `olderWhere`, `olderWhen`) and its telemetry (`heldIn`, `recordOlder`) | `scan/OlderList.tsx` |
| `ScanPage.tsx`: `OutboxList`, with `RecordedElsewhere`, `useOnline` and `AddDifference` | `scan/OutboxList.tsx` |
