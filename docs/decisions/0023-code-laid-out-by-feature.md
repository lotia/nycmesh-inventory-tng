# 0023 — Code laid out by feature

**Status:** accepted · 2026-11-17 · amended 2026-11-18 (how files and
tests are named)

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
  table's rules and writes (its owner, 0016); one each of `queries.py`,
  `forms.py`, `paths.py` and `guards.py`, the last for what every route
  needing a sign-in or a device depends on, and the reply it refuses
  with; and, named for the page, endpoint or command each serves,
  `<page>_routes.py` for HTML pages, `<thing>_api.py` for JSON,
  `<thing>_schemas.py` and `<command>_command.py`, as
  `admins/sign_in_routes.py`, `scan/batch_api.py` or
  `admins/totp_keys_command.py` (Amendment, 2026-11-18, replacing
  `routes.py`, `api.py`, `schemas.py` and `command.py` while a feature
  had only one). URLs do not change.
- **The top of `inventory_tng`** holds only the entry points (`app.py`,
  `server.py`, `__main__.py` and `manage.py`), `models/` and
  `templates/`, beside folders of data such as `locales/`.
- **`models/`** holds the mapped classes, a module per feature named for
  it, with what they share in `base.py`, `names.py` and `search_text.py`.
  Its `__init__.py` imports every module, so `Base.metadata`, Alembic and
  the guards see every table (Ali, 2026-11-18, inventory-tng-h657.1). It
  re-exports nothing: callers import a model's names from the module it is
  in, never the module itself (Amendment, 2026-11-18).
- **`templates/`** has a folder per feature, named as its package, with
  the pages its routes render and the partials only its pages include.
  Layouts and partials more than one feature uses, and the pages `web/`
  renders, stay at the top (Ali, 2026-11-18, inventory-tng-h657.2). A
  test in `backend/tests/codebase/test_import_directions.py` checks that
  only its feature uses a feature's folder, and that what sits at the top
  is shared.
- **`web/`** holds what every server-rendered page shares; **`core/`**
  holds plumbing, such as settings, db, history, logs, telemetry and mail.
  Core means plumbing, not business rules.
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
  its own process (0007).
- **Developer tools** live in `backend/devtools/`, beside `tests/` and
  outside `src/`, so no image ships them (Ali, 2026-11-18,
  inventory-tng-wzlr). A tool that writes a file the app or the frontend
  reads belongs there; one that writes a reference file only tests read
  stays in `tests/helpers/`. They have one entry point, `python -m
  devtools <command>`, run from `backend/`: its commands, in `COMMANDS` in
  its `__main__.py`, are listed and dispatched as the operator's are, by
  the same code (`core/entry_point.py`), so the two cannot drift. Modules
  in `src/` that a tool uses, such as `core/local_env.py` and
  `catalogue/units.py`, have no `python -m` path of their own.
- **Imports go one way**, held by
  `backend/tests/codebase/test_import_directions.py`:
  - rules, queries and forms import neither FastAPI nor `web/`, but for
    `web/formatting.py` and `web/forms.py`, which import neither
    themselves (inventory-tng-atmx.2, inventory-tng-atmx.7);
  - route files run no queries (`select`, `execute`, `scalar`, `scalars`);
  - core imports nothing of the app but core and `models/`;
  - no feature's routes import another feature's routes; they link
    through its `paths.py`;
  - nothing of the app imports `backend/devtools/` (inventory-tng-wzlr);
  - nothing in `stock/` imports `scan/`: a volunteer's batch
    (`scan/batch.py`) writes through the ledger, so imports run from scan
    to stock (Ali, 2026-11-18, inventory-tng-h657.4).

  The app breaks none of them: the breaks found when the test was written,
  all in `web/once.py`, went when it was split (inventory-tng-h657.3). On
  2026-11-17 Ali dropped two rules that caught no real harm, package
  `__init__.py` files staying empty and only the entry point importing a
  command; the same change dropped a third, that a `paths.py` imports only
  core and other `paths.py`, as a cycle through one fails at import anyway
  (inventory-tng-atmx.16).

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

- On 2026-11-18, as Ali decided (inventory-tng-5q07), `models.py` was
  split into `models/` (inventory-tng-h657.1) and the templates into a
  folder per feature (inventory-tng-h657.2).
- On 2026-11-18 the developer tools left `src/` for `backend/devtools/`
  (inventory-tng-wzlr), each in the tables below. `dev/suite_report.py`
  kept its name; the commands of `core/local_env.py` and
  `catalogue/units.py` became `setting.py` and `write_unit_table.py`,
  with `units.TABLE` as `write_unit_table.TABLE`.
- On 2026-11-18, as Ali decided (inventory-tng-h657.4), `stock/ledger.py`
  was split into its core, the one writer, and three parts that call it:
  its reads (`stock/moments.py`), counts and adjustments
  (`stock/counts.py`) and a volunteer's batch (`scan/batch.py`). The
  ledger no longer imports `scan/batch_schemas.py` (inventory-tng-4jxw)
  or `stock/open_packages.py`. The `ledger.py` row below gives each
  name's home.
- On 2026-11-18 the files serving one page, endpoint or command took the
  names the amendment below gives them (inventory-tng-u3fp.2), each in the
  tables below. The two replies other endpoints declare left
  `devices/schemas.py`: a refused device's for `devices/guards.py`, which
  refuses it, and a rate limit's for `core/rate_limits.py`.
- On 2026-11-18 the other files and names the amendment's decision named
  were renamed or moved (inventory-tng-u3fp.3), each in the tables below,
  and callers stopped importing modules under an alias.
- On 2026-11-18 `web/once.py` was split in two (inventory-tng-h657.3):
  `core/sent_forms.py`, the `sent_form` table's owner, which imports
  neither FastAPI nor `web/`, and `web/single_use_forms.py`, the form
  field, the template global and the page saying a form was sent again.
  "Once" became "single-use" in their names and in the form field's,
  which is `single_use`.
- On 2026-11-18 `models/names.py` was split in two (inventory-tng-u3fp.6):
  `models/names.py` keeps how a name is stored and keyed, and
  `models/search_text.py` holds how text is folded, kept and matched for
  search. The test of the fold reference took the new module's name.
- On 2026-11-18 `core/db.py` was split in four, each named `db_` and its
  job (inventory-tng-u3fp.7): `core/db_sessions.py`, `core/db_locking.py`,
  `core/db_protection.py` and `core/db_errors.py`. No `core/db.py` is left.
  The test of the pools and sessions took the first one's name.
- On 2026-11-18 `core/mail.py` was split in two (inventory-tng-u3fp.8):
  `core/mail.py` keeps how mail is sent, and `admins/mail_messages.py`
  holds what each mail says, as every mail is to administrators. Its
  test was split alike: the tests of reset links went to
  `admins/test_reset_routes.py`, and those of which action sends a notice
  to the tests of that action's routes.
- On 2026-11-18 an authenticator's TOTP key and the checking of its
  codes left `admins/accounts.py` for `admins/totp_codes.py`
  (inventory-tng-u3fp.9), apart from `admins/totp_keys.py`, which seals
  those keys at rest. Its test of a code's time window moved alike. The
  username, email and password checks stayed in `admins/accounts.py`.
  Moved to `admins/forms.py`, which imports `admins/accounts.py`, they
  would make a cycle: `admins/accounts.py`'s operator invitation checks
  the username and email too.
- Older decision records keep the paths of their time. The tables below
  give each old path's new home; a later move adds its rows.

In `backend/src/inventory_tng/`, these files moved whole into a feature
and kept their names:

| Folder | Old paths |
| --- | --- |
| `admins/` | `accounts.py`, `activity.py` |
| `catalogue/` | `categories.py`, `items.py`, `names.py`, `reading_rules.py`, `units.py` |
| `core/` | `command.py`, `cross_site.py`, `database_role.py`, `db.py`, `history.py`, `local_env.py`, `local_time.py`, `logs.py`, `mail.py`, `masking.py`, `phrases.py`, `rate_limits.py`, `request_logging.py`, `settings.py`, `telemetry.py`, `time_zones.py` |
| `labels/` | `labels.py` |
| `places/` | `locations.py` |
| `scan/` | `bundle.py`, `page_builds.py` |
| `stock/` | `adjustment_reasons.py`, `over_recorded.py`, `admin/needs_action.py` |
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
| `admins/accounts.py` | itself, but for `qr_svg`: `web/qr_codes.py`; `name_up_to`: `web/forms.py`; `new_totp_key`, `is_totp_key`, `totp_link`, `grouped`, `key_for_typing`, `accepted_step`, `ISSUER`, `TOTP_FIELD_MAX`, `TOTP_CODE`, `TOTP_INTERVAL`, with `_totp_code` as `totp_code`: `admins/totp_codes.py` |
| `catalogue.py` | `scan/catalogue_api.py`; the reply schemas: `scan/catalogue_schemas.py`; its queries: `scan/queries.py` |
| `catalogue/command.py` | `catalogue/unit_readings_command.py` |
| `catalogue/names.py` | `catalogue/name_keys.py` |
| `core/command.py` | `core/operator_commands.py` |
| `core/db.py` | `Base`, the pools, `Database`, sessions, the actor, `ping`, `exists_where`, `by_name`, `hold_until_commit`, `release_hold`, `run_outside_transaction`, `record_last_seen`: `core/db_sessions.py`; `locked`, `get_locked`, `settings_row`, `Seen`, `unchanged`, `lock_catalogue`, the lock guard, `_next_version`, `require_current`, `locked_row`, `relock`: `core/db_locking.py`; `protection`, `Lock`, `Flag`, `Action`, `flagged`, `Write`, `changed_columns`, `flushed_id`, `flush_writes`, with `_table` as `table_of`: `core/db_protection.py`; `PoolTimeout`, `NoSession`, `RuleBroken`, `Gone`, `enforce`, `attempt`, `refusal`, `unique`, `describe_error`, the SQLSTATEs, `UNAVAILABLE`, `install`: `core/db_errors.py` |
| `core/mail.py` | itself, with `Message`, `Kind`, `Told`, `Mailer`, `SmtpMailer`, `get_mailer`, `Notifier`, `Notify`, `deliver`, `sent`, `TIMEOUT_SECONDS`; what each mail says (`branded`, the shared paragraphs, `TOTP_POLICY_WORDS`, the counts' plurals and a function per mail): `admins/mail_messages.py` |
| `dev_database.py` | `backend/devtools/database.py`; `seed` is one of its subcommands |
| `devices.py` | `devices/devices.py`; `register_device`: `devices/api.py`; the request and reply schemas: `devices/schemas.py`; what every route needing a device depends on: `devices/guards.py`; `REGISTER_PATH`: `devices/paths.py` |
| `devices/api.py` | `devices/registration_api.py` |
| `devices/command.py` | `devices/registration_codes_command.py` |
| `devices/routes.py` | `devices/devices_routes.py` |
| `devices/schemas.py` | `devices/registration_schemas.py`; `DeviceRefusedReply`: `devices/guards.py`; `RateLimitedReply`: `core/rate_limits.py` |
| `health.py` | `core/health.py`; its query: `core/db_sessions.py`, as `ping` |
| `labels/routes.py` | `labels/labels_routes.py` |
| `ledger.py` | `stock/ledger.py`, with `_each_once`, `_hold_forms` as `each_once`, `hold_forms`; `record`, with openings into open packages, the problems of a batch by line, the replay of a batch sent again, `BatchRefused`, `KeyReused`, `Recorded`, `retired_sides` and their metrics: `scan/batch.py`; `moment`, `expected`, `expected_forms`, `left_at`, `corrected_since`: `stock/moments.py`, with `_flows`, `_form_flows`, `_at_place` as `flows`, `form_flows`, `at_place`; `record_count`, `count_open_package`, `record_adjustment`, the rest of counts and adjustments and their metrics: `stock/counts.py`; the balance queries: `stock/balance.py`; `save_batch`: `scan/batch_api.py`; the batch request and reply schemas: `scan/batch_schemas.py`, but for `Line.entry`: `scan/batch.py`, as `entry_of` |
| `models.py` | `models/`: each table in its feature's module; the column types, `whole`, the columns and constraints most tables have, and `Versioned`: `models/base.py`; canonical names, name keys and search folding (`CanonicalName`, `KeyedName`, `Searchable`, `fold` and the rest): `models/names.py`; `_refold`: `models/catalogue.py`; `_reference`, `_one_of`, `_between`, `_unique_name`, `_unique_current_name`, `_name_key_check`: each without its `_` |
| `models/catalogue.py` | itself, with `reading_rules` as `reading_rules_held` |
| `models/names.py` | itself, with `canonical`, `CANONICAL_SIGNS`, `CanonicalName`, `KeyedName`, `unit_key`, `name_key_check`; `fold`, `folds`, `held`, `holds`, `in_ascii`, `in_ascii_once`, `fuzzy`, `Searchable`, `FOLDED_WITH`: `models/search_text.py` |
| `models/web.py` | `models/core.py` |
| `open_packages.py` | `stock/open_packages.py`; `tracks_opened`: `stock/ledger.py` |
| `openapi.py` | `backend/devtools/write_api_schema.py` |
| `page_telemetry.py` | `scan/page_telemetry.py`; `over_limit`: `web/browser.py` |
| `pages.py` | `web/pages.py`; `amount`, `printed_length`: `web/formatting.py`; `Badge`, `retired_holding`, `sections`: `web/navigation.py` |
| `places/routes.py` | `places/locations_routes.py` |
| `registration_codes.py` | `devices/command.py` |
| `resets.py` | `admins/reset_routes.py` |
| `scan/bundle.py` | `web/frontend_bundle.py` |
| `scan/page_telemetry.py` | `scan/page_telemetry_api.py` |
| `scan_settings.py` | `site_settings/scan_settings.py`; its command: `site_settings/command.py` |
| `seed.py` | `backend/devtools/seed.py` |
| `sign_in.py` | the sign-in pages: `admins/sign_in_routes.py`; what every administrator page depends on: `admins/guards.py`; `reset_url`: `admins/paths.py` |
| `site_settings/command.py` | `site_settings/scan_settings_command.py` |
| `site_settings/routes.py` | `site_settings/site_settings_routes.py` |
| `stacks.py` | `backend/devtools/stack.py`; its `stale` is `list-stale` |
| `stock/paths.py` | itself, with `RECORDED_MORE` and `RECORDED_MORE_SETTLED` as `OVER_RECORDED` and `OVER_RECORDED_SETTLED` |
| `templates/scan/page.html` | `templates/frontend_page.html` |
| `totp_keys.py` | `admins/totp_keys.py`; its command: `admins/totp_keys_command.py` |
| `unit_readings.py` | `catalogue/unit_readings.py`; its command: `catalogue/command.py` |
| `volunteers/routes.py` | `volunteers/volunteers_routes.py` |
| `web.py` | `web/browser.py` |
| `web/admin.py` | `web/admin_pages.py`; `SETTLED_VIEW`: `stock/over_recorded_routes.py`; `TICK_OPEN_PACKAGES`: `stock/forms.py` |
| `web/once.py` | `web/single_use_forms.py`, with `OnceKey` and `once_field` as `SingleUseKey` and `single_use_field`; `form_key`, `spent` as `is_spent`, and spending a key, as `spend`: `core/sent_forms.py` |

In `backend/src/inventory_tng/templates/`, these templates moved whole
into their feature's folder and kept their names:

| Folder | Old paths |
| --- | --- |
| `admins/` | `account.html`, `_activity.html`, `admin.html`, `admin_home.html`, `admin_reset.html`, `admins.html`, `authenticator_new.html`, `_code_field.html`, `confirm.html`, `forgot.html`, `link_made.html`, `_password_hint.html`, `recover.html`, `recovery_codes.html`, `reset.html`, `setup.html`, `sign_in.html`, `_totp_fields.html` |
| `catalogue/` | `categories.html`, `category.html`, `item.html`, `_item_name.html`, `items.html`, `reading_rule.html`, `reading_rules.html`, `reading_seed.html`, `_units_read.html` |
| `devices/` | `code.html`, `code_made.html`, `code_revoke.html`, `device.html`, `device_list.html`, `device_revoke.html`, `device_revoke_one.html`, `devices.html` |
| `labels/` | `label_revoke.html`, `labels.html`, `labels_print.html` |
| `places/` | `location.html`, `locations.html` |
| `scan/` | `page.html` |
| `site_settings/` | `settings.html` |
| `stock/` | `count.html`, `counts.html`, `_forms.html`, `length_checks.html`, `needs_counting.html`, `open_package_close.html`, `over_recorded.html`, `over_recorded_settled.html`, `reorder.html`, `_retired.html`, `stock.html`, `used_up.html`, `_views.html` |
| `volunteers/` | `volunteer.html`, `volunteer_merge.html`, `volunteers.html` |

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

Later renamed:

| Old path | New path |
| --- | --- |
| `admin/once.ts` | `admin/single-use-forms.ts`, with `ONCE_FIELD` as `SINGLE_USE_FIELD` |
| `scan/labels.ts` | `scan/label-codes.ts` |
| `scan/recorded.ts` | `scan/recorded-elsewhere.ts` |
| `shared/stored.ts` | `shared/local-storage.ts` |

In `frontend/browser-tests/`, these were renamed:

| Old path | New path |
| --- | --- |
| `codes.ts` | `registration-codes.ts` |
| `links.spec.ts` | `reset-links.spec.ts` |
| `older.spec.ts` | `older-builds.spec.ts` |
| `once.spec.ts` | `single-use-forms.spec.ts` |
| `own.ts` | `own-rows.ts` |
| `policy.spec.ts` | `content-security-policy.spec.ts` |
| `policy.ts` | `csp-violations.ts` |

In `backend/tests/`, these tests moved whole into the folders the
Amendment: names, below, gives them, and kept their names:

| Folder | Old paths |
| --- | --- |
| `across/` | `test_edit_forms.py`, `test_locking.py`, `test_names_shown.py`, `test_notices.py`, `test_query_counts.py`, `test_requests_at_once.py`, `test_retired_records.py`, `test_write_owners.py` |
| `admins/` | `test_break_glass.py`, `test_recover.py`, `test_totp_keys.py` |
| `catalogue/` | `test_accent_search.py`, `test_reading_rules.py`, `test_unit_readings.py`, `test_units.py` |
| `codebase/` | `test_artifact_links.py`, `test_compose_app.py`, `test_compose_task.py`, `test_contributing.py`, `test_dependencies.py`, `test_import_directions.py`, `test_podman_exec_oom.py`, `test_test_imports.py`, `test_webkit_container.py` |
| `core/` | `test_connections.py`, `test_database_role.py`, `test_health.py`, `test_history.py`, `test_local_env.py`, `test_local_time.py`, `test_log_redaction.py`, `test_logs.py`, `test_mail.py`, `test_masking.py`, `test_phrases.py`, `test_rate_limits.py`, `test_request_logging.py`, `test_settings.py`, `test_telemetry.py`, `test_trace_redaction.py` |
| `devices/` | `test_devices.py` |
| `devtools/` | `test_seed.py`, `test_suite_report.py` |
| `models/` | `test_fold_reference.py`, `test_migrations.py`, `test_models.py` |
| `scan/` | `test_batch_schema.py`, `test_page_builds.py` |
| `site_settings/` | `test_scan_settings.py` |
| `stock/` | `test_ledger.py`, `test_length_checks.py`, `test_over_recorded_closed.py`, `test_over_recorded_counted_ends.py`, `test_over_recorded_mark_checked.py`, `test_over_recorded_moved_whole.py`, `test_over_recorded_refused.py`, `test_over_recorded_reply.py`, `test_over_recorded_sealed_end.py`, `test_over_recorded_settled.py`, `test_package_forms_batches.py`, `test_package_forms_counts.py`, `test_package_forms_openings.py`, `test_package_forms_rules.py`, `test_package_forms_stock.py`, `test_package_forms_used_up.py` |
| `web/` | `test_error_pages.py`, `test_security_headers.py`, `test_single_use_forms.py` |

These were renamed, or split, as they moved:

| Old path | New path |
| --- | --- |
| `helpers/categories.py` | `helpers/suite_categories.py` |
| `helpers/pages.py` | `helpers/swept_pages.py` |
| `helpers/queries.py` | `helpers/query_counts.py` |
| `test_account.py` | `admins/test_account_routes.py` |
| `test_admin_admins.py` | `admins/test_admins_routes.py` |
| `test_admin_categories.py` | `catalogue/test_categories_routes.py` |
| `test_admin_counts.py` | `stock/test_counts_routes.py` |
| `test_admin_devices.py` | `devices/test_devices_routes.py` |
| `test_admin_home.py` | `admins/test_home_routes.py` |
| `test_admin_items_aliases.py` | `catalogue/test_items_routes_aliases.py` |
| `test_admin_items_editing.py` | `catalogue/test_items_routes_editing.py` |
| `test_admin_items_list.py` | `catalogue/test_items_routes_list.py` |
| `test_admin_items_packages.py` | `catalogue/test_items_routes_packages.py` |
| `test_admin_items_units.py` | `catalogue/test_items_routes_units.py` |
| `test_admin_labels.py` | `labels/test_labels_routes.py` |
| `test_admin_locations.py` | `places/test_locations_routes.py` |
| `test_admin_settings.py` | `site_settings/test_site_settings_routes.py` |
| `test_admin_stock.py` | `stock/test_stock_routes.py` |
| `test_admin_volunteers.py` | `volunteers/test_volunteers_routes.py` |
| `test_bundle.py` | `web/test_frontend_bundle.py` |
| `test_catalogue.py` | `scan/test_catalogue_api.py` |
| `test_catalogue_contexts.py` | `codebase/test_phrase_contexts.py` |
| `test_catalogue_rules.py` | `places/test_locations.py`, its locations' rules; `volunteers/test_volunteers.py`, its volunteer's |
| `test_categories.py` | `test_suite_categories.py` |
| `test_dev_database.py` | `devtools/test_database.py` |
| `test_devtools.py` | `devtools/test_main.py` |
| `test_list_control.py` | `web/test_listing.py` |
| `test_openapi.py` | `devtools/test_write_api_schema.py` |
| `test_page_telemetry.py` | `scan/test_page_telemetry_api.py` |
| `test_sign_in.py` | `admins/test_sign_in_routes.py` |
| `test_sign_in_models.py` | `models/test_admins.py` |
| `test_stacks.py` | `devtools/test_stack.py` |

Later renamed:

| Old path | New path |
| --- | --- |
| `admins/test_sign_in_routes.py` | itself; the test of a code's time window: `admins/test_totp_codes.py` |
| `core/test_connections.py` | `core/test_db_sessions.py` |
| `core/test_mail.py` | itself, its settings and sending; the words of the mails: `admins/test_mail_messages.py`; the reset links: `admins/test_reset_routes.py`; a notice of a recovery code used: `admins/test_sign_in_routes.py`; the notices of a password changed: `admins/test_account_routes.py` |
| `models/test_fold_reference.py` | `models/test_search_text_fold_reference.py` |

## Amendment: names (2026-11-18)

Ali decided on 2026-11-18 how files, modules and tests are named
(inventory-tng-u3fp), so that a name says what its file holds and can be
searched for (AGENTS.md rule 12).

- **A file serving one page, endpoint or command is named for it**, even
  when it is its feature's only one: `<page>_routes.py`, `<thing>_api.py`,
  `<thing>_schemas.py` and `<command>_command.py`, the last for its
  command's name (`registration-codes` is `registration_codes_command.py`).
  A name may repeat its folder, as `devices/devices_routes.py`. The name
  can then be searched for, stays the same when a second file of its role
  arrives, and needs no alias where `app.py` or `manage.py` imports it.
- **A feature's role files keep their names**: `queries.py`, `forms.py`,
  `paths.py` and `guards.py`, one of each per feature. Callers import
  names from them (`from inventory_tng.stock.queries import item_total`),
  never the module under an alias such as `stock_queries`.
- **`models/<feature>.py` keeps its name.** Callers import a model's
  names, never the module, so it never meets its feature's package of the
  same name under aliases. A function in it is named so it cannot be read
  as one of the feature's modules.
- **Backend tests mirror the source tree**: a test of one module is
  `test_<module>.py`, or `test_<module>_<aspect>.py`, in the folder of
  the module's package, and a test about one feature sits in that
  feature's folder. pytest runs in its importlib import mode. Helpers stay
  importable as `helpers` through `pythonpath`, and are never named
  `test_*`, which pytest would collect. Two folders hold tests no package
  owns: `tests/across/`, properties of the whole running app, such as
  requests at once, locking and the page sweeps; and `tests/codebase/`,
  rules about the code, read from its files without running the app, such
  as the import directions. Every folder under `backend/tests/` that names
  no source package, such as these two and `helpers/`, holds a short
  `README.md` saying what it holds, and a test checks it. CONTRIBUTING.md
  says only that the tests mirror the source tree and that such a folder
  has its README; it never lists the folders, which would drift.
- **In the frontend**, components are PascalCase, named for the
  component; other modules are kebab-case, named for what they hold, as
  `testing/scan-page.tsx`. Tests sit beside them, as `<module>.test.ts` or
  `<Component>.<aspect>.test.tsx`.

We rejected prefixing the role files with their feature
(`stock/stock_queries.py`) and suffixing the models' modules
(`models/catalogue_tables.py`): there is one of each per feature, and the
clashes came from importing modules, not from their names. We rejected
flat test names after the module tested, as Ali pointed out that importlib
mode, which lets the tests mirror the tree, is pytest's recommendation for
new projects.

Until the steps of inventory-tng-u3fp land, some files, imports and
tests do not follow these yet. Each step renames or splits files, or
moves tests, and adds its rows to the tables above.
