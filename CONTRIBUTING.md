# Contributing

## Version control and issues

Issues are tracked with [beads](https://github.com/gastownhall/beads) (`bd`).
For version control, use git or [jj](https://jj-vcs.github.io/jj/), whichever
you prefer. Coding agents always use jj.

### Setup, once per clone

```sh
git clone <url>           # or: jj git clone <url>
bd bootstrap              # fetch the issue database; never `bd init`
```

To use jj in a git clone, run `jj git init --colocate` in it.

Issues live in a database under `.beads/`, not in commits, and travel through
the same remote on a ref of their own. `bd sync` pulls and pushes them. Run it
before you start and after you change an issue.

### Working on an issue

```sh
bd sync
bd ready                  # pick something unblocked
bd update <id> --claim
# make one commit (or jj change) for the issue
bd close <id>
bd sync
```

Commit messages follow [Conventional
Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```text
feat(catalogue): add item aliases

What changed and why, in plain paragraphs or a list, wrapped at 72
columns, ending with the result of mise run check.

Refs: inventory-tng-c0c.6
```

- The first line is `type(scope): description`, 72 characters or fewer,
  the description in lower case. Types: `feat`, `fix`, `refactor`,
  `perf`, `test`, `docs`, `build`, `ci`, `chore`. The scope is the
  issue's main area label without `area:`, such as `catalogue` or
  `scan-page`; leave it out when no one area fits.
- A blank line, then the body.
- A blank line, then the footers: `Refs:` with the issue's id (ids
  separated by commas), then any others, such as `Co-Authored-By:`.

Then run `mise run check` on what you are about to push, every time, and
push only if it passes: there is no pull request to catch what it would
have. Push straight to `main`, where CI checks it again: `git push origin
main`, or with jj, `jj bookmark set main -r <change>` and `jj git push`.

### Labels

This project labels its issues. To see the labels in use:

```sh
bd label list-all
```

## Requirements

- [mise](https://mise.jdx.dev/). It installs every other tool, at the
  versions in `mise.toml`, and runs the tasks below. The tasks are shell
  scripts, for macOS and Linux; on Windows, work in WSL.
- Podman or Docker, with compose, for the database (and, if you want it, the
  app). `mise run compose -- <arguments>` runs compose with Podman if it is
  installed, else Docker. To choose, set `INVENTORY_COMPOSE` in `.env`, such
  as `INVENTORY_COMPOSE=docker compose`.

Activate mise in your shell (`mise activate`, in its docs), so the tools it
installs are on your `PATH` in the clone. Then, in the clone, and again after
a pull that changes a dependency:

```sh
mise trust               # once: lets mise use this clone's mise.toml
mise run setup
```

It installs the tools in `mise.toml` (uv then installs the Python version the
backend asks for), the backend's and frontend's dependencies, and makes a
`.env` from `.env.example` and a key in `.local/totp-keys` (DEPLOYMENT.md,
TOTP keys) if there are none. Git ignores `.local/`. Every command below
runs Python through `uv run`.

## Local settings

`.env` holds your local settings: this checkout's compose project and
ports, its database's name and URLs, and any logging or telemetry settings
you want. Git ignores it, so it never reaches a
commit; change `.env.example` only to change what everyone starts from.
Compose, the run command and migrations read `.env`, and the tests read its
database port. A value set in your shell wins over the file.

Every clone, worktree or agent's checkout has a compose stack of its own:
its PostgreSQL server, app container and WebKit server, with their volume,
under the project `COMPOSE_PROJECT_NAME` in `.env`. So one checkout's
settings, rebuild or seed never touch another's. When `mise run setup`
makes `.env`, it names the project for the checkout's directory and where
that is, `inventory-tng-<directory>_<n>`, and the database on its server
`inventory_<directory>_<n>` (`INVENTORY_DB_NAME`, which both URLs name).
It gives each port compose publishes (`INVENTORY_DB_PORT`, 5433, beside
MeshDB's 5432; `INVENTORY_APP_PORT`, 8000; and the others `.env.example`
lists), and Vite's (`INVENTORY_VITE_PORT`, 5173), its usual number, or, if
another stack claims it or something listens on it, the next free one up.
Never copy a `.env` from another checkout.

A port can still be taken later, as by a checkout set up at the same time
whose stack was not yet started. So each task that starts something
(`mise run start-database`, and so every task that needs the server,
`mise run dev`, `mise run phone`, `mise run browser-webkit`) first runs
`mise run ports-free` for the ports it needs. It stops, before starting
what needs it, when one is in use and not by this checkout's stack, saying
which port and setting, what holds it (another checkout, or something else)
and what to do: `mise run setup -- --new-ports` gives `.env` free ports again,
changing only its port settings, and only those another stack claims or
something else holds. The URLs follow them. Without `--new-ports`, setup
never changes an existing `.env`.

A `.env` made before names no project, so its checkout uses the project
`inventory-tng` (`compose.yaml`'s name), as it always did, on ports 5433,
8000 and 3000, and a database `inventory`: nothing changes for a single
clone. Checkouts with such a `.env` share that one stack; to give one its
own, move its `.env` aside, run `mise run setup`, and copy back any
setting of yours.

Each task that needs the server runs `mise run start-database`, which
starts it, and makes its container again, keeping its volume, when
`compose.yaml` or `.env` changed its settings: that ends this stack's
connections, and in `inventory-tng` those of every checkout sharing it.
`mise run dev-database` starts the server, makes the database if it is
missing, and migrates it; `mise run dev` and `mise run seed` run it first.
It holds each to 30 connections (Connections, below).

A stack stays after its checkout is deleted. `mise run list-stale-stacks`
lists each whose `compose.yaml` is gone, and
`mise run remove-stale-stacks` lists them, asks, then removes those it
listed, with their volumes and every database in them. Neither touches
`inventory-tng` or this checkout's. A checkout moved or renamed keeps its
stack, which still names the old path, so it is listed too: check the list
before you confirm. Compose knows a stack's checkout only from its
containers, so a stack taken down with `down`, not `down --volumes`,
before its checkout was deleted leaves a volume neither can find.

The app logs in with `DATABASE_URL`, as `inventory_app`, which may read and
add to the ledger but never change it. Migrations log in with
`MIGRATION_DATABASE_URL`, as `inventory_owner`, which owns the schema
(DEPLOYMENT.md, Database roles). Both roles have their names as passwords.
The server's container makes them on a new volume, and
`mise run dev-database` and the tests make them if they are missing
(`backend/migrations/roles.sql`).

## Running the app

```sh
mise run dev             # http://127.0.0.1:8000, Ctrl-C stops it
```

Its port is `INVENTORY_APP_PORT` and Vite's `INVENTORY_VITE_PORT`, from
`.env` (Local settings), so checkouts can each run one at once; it prints
the address.

It runs, in order:

1. `mise run setup`, which installs what a pull changed;
2. `mise run ports-free` for the two servers' ports (Local settings);
3. `mise run dev-database`: the database, and its migrations;
4. two servers side by side, each line of output marked with which:
   `dev-backend`, which renders every page and restarts when you save its
   Python, and `dev-vite`, Vite's dev server for the frontend (The scan
   page, below). If either fails, the other stops too.

`mise run dev-backend` or `mise run dev-vite` runs one on its own. The
backend runs with `APP_ENV=development`, whatever `.env` says.

The backend is `uv run python -m inventory_tng --reload`, in `backend/`.
`--host`, `--port` and `--workers` are there too (`--help` lists them). The
run command starts uvicorn itself; uvicorn's own logging flags are not used,
the `LOG_*` settings are (Logging, below).

The developer's tools are `uv run python -m devtools <command>`, in
`backend/`, from `backend/devtools/`, which no image ships. With no command
it lists them, and each command's `--help` says what it takes. The tasks
below run them for you.

### Some data to work with

```sh
mise run seed                   # or: mise run seed -- --password <yours>
```

On an empty database it makes an administrator, `dev`, with a password it
prints once, and a few categories, items, places, volunteers and stock.
Sign-in codes become optional, so `dev` signs in with the password alone.
Run again, it changes only `dev`'s password: to the one you give, or to a
new one if `dev` has none. It refuses a database with other administrators, and
needs `APP_ENV=development`, as `.env.example` sets, also in the app
container: `mise run compose -- exec app python -m devtools database seed`.

### Migrations

Until a test deployment holds people's data, a change may edit an existing
migration instead of adding one, so the schema's history stays short; after
that, never: add a new one (decision 0016). After a pull that edits one,
your local database no longer matches its migrations: rebuild it, losing
its data, with `mise run reset-dev-database -- <its name>`. It drops only
this checkout's database, which you name to confirm, then makes and
migrates it again, empty. Never rebuild with `mise run compose -- down
--volumes`: that removes the stack's volume, with every database in it,
and in `inventory-tng` every checkout's that shares it.

You can keep your data instead when the edited migration and every one
after it only rewrite rows and have downgrades that do nothing. In
`backend`, downgrade to the revision before the edited one, then upgrade:

```sh
uv run alembic downgrade <revision> && uv run alembic upgrade head
```

Migrations run as the owner, and every table they create gives the app
`SELECT`, `INSERT`, `UPDATE` and `DELETE` (the initial migration's default
privileges). A migration that creates a table the app must not change
revokes what it must not do there, as the initial migration does for the
tables the app only adds to, those it never deletes from, and columns set
only as a row is made (`READ_AND_ADD`, `NEVER_DELETED` and `UPDATED_ONLY`).
`tests/core/test_database_role.py` lists those columns, so a new column
the app cannot change fails there until listed. What a migration writes is
not in the history. Migrations create no trigger, function or view: the
rules are the app's, and `tests/models/test_models.py` fails on one.

### History

Every row added, changed or deleted through the ORM, in a table with history
(see [The catalogue's rules](#the-catalogues-rules)), is written to
`catalogue_history` as the session flushes, with who did it
(`db_sessions.set_actor`). So write those tables through the ORM:
`session.add`, an object's attributes, `session.delete`. A statement such as
`update(Item)` is never recorded, and `tests/core/test_history.py` fails on
one. The app may only add to the history.

### The catalogue's rules

Each catalogue table is written only by its own module, the owner its model
names (`db_protection.protection`, below), such as `catalogue/items.py` for
items and their packages. Admin pages and the seed call those modules'
functions, one per kind of change, which take the catalogue lock, lock and
read what the rules need, and refuse a change that breaks one by raising
`db_errors.RuleBroken` with the rule's name. `db_errors.attempt` returns
that name, and `web/forms.py`'s `RULES` says it in words. A page reads rows
with `session.get` and leaves locking to those functions. The ledger is
written only by `ledger.insert_transaction`, which saves, counts and
adjustments all call, and which checks its rules the same way. A rule about
one row's own values is a check constraint, or, where PostgreSQL cannot
check it, the model's own `@validates`, as `SiteSettings` checks the site's
time zone.

Each model declares who writes its table, holding what, and whether it has
history, with `db_protection.protection`; `core/db_protection.py` gives the
rules, which the flush and `tests/across/test_write_owners.py` enforce. An
owner is a domain module, never a page's, as `stock/adjustment_reasons.py`
is; a settings row, made by its migration, has none, and
`scan_settings.change` is its one writer. `items.Fields` names every column
an item's save sets, each required, so none is blanked by being left out.
The ledger takes lines, not a mapping by item, so none is lost: a
transaction naming an item twice is refused (`movement_item_once`), and its
movements' unique index says so too.

Item and package names are unique ignoring case, spaces and punctuation,
ASCII's only, with each look-alike space, quote or dash read as ASCII's
(decision 0021). A decimal point between digits counts,
a comma never does, as US number conventions have it: `30.5 m` and
`305 m` are two names, `1,000 ft` and `1000 ft` one. Where a comma is the
decimal mark this is wrong (`30,5 m` clashes with `305 m`); the app is
English-only and follows US conventions today (inventory-tng-g6u.1, under
inventory-tng-g6u).

An item's aliases (`item_alias`) share its name's namespace: no item's
name is an alias, nor any alias an item's name or another alias, retired
items' too. A unique index keeps aliases apart; `catalogue/items.py`
checks names against aliases, holding the catalogue lock. A removed
alias is retired, freeing its name. Renaming an item keeps its old
name as an alias, and renaming it to one of its own aliases swaps the
two (`items.save`).

Each such row keeps its key in `name_key`, which the app writes as the
name is set (`models.names.KeyedName`), a CHECK constraint holds to the name,
and a plain unique index compares. Names that read alike once their units
are read are one too (`10 uF` and `10μF`): each row keeps `unit_key`,
which the app writes as the session flushes (`models.names.unit_key`), unique
as `name_key` is, with no CHECK, as the units are the app's. A unit is
judged by its number as search reads it (`units._searched`), sign and all,
so `Cap 1 000 μF` and `Cap 1000 uF` are one too, and found after any
space, as search reads every space as a plain one.
`backend/src/inventory_tng/catalogue/name_keys.py` has the rule: one pattern
and one table of look-alikes (`LOOK_ALIKES`), read by Python (`name_key`)
and PostgreSQL (`name_key_sql`), and a test checks both agree on every
character. To change the rule, change `catalogue/name_keys.py` and add a
migration that writes the CHECKs out again (items', packages' and
aliases') and refills every key, naming by id any rows that would then
clash. The initial migration's `NAME_KEY` shows how to write them out by
code point.

Every name an administrator types is stored in canonical spelling
(`models.names.canonical`, decision 0021): NFC, with `µ` as `μ` and `℃` and `℉`
as `°C` and `°F`, case kept. A form checks and compares a name in that
spelling.

An item's, a package's and an alias's name is shown, wherever it is read,
as `name_shown` holds it (decision 0021): each number and the unit or
unit's word read after it spaced by a no-break space, thousands without
commas and grouped by thin no-break spaces from five digits, but a number
grouped by spaces as typed, its breaking spaces no-break
(`name_keys.NO_BREAK`, `units.shown_name`). The app writes it as
the session flushes and when a reading rule changes (`items.reread`). A
page or reply that names one reads `name_shown`; only a form's field
reads `name`.
`tests/across/test_query_counts.py` checks every page for it.

Searches read a unit right after a number in one spelling, on the server
and the scan page alike (decision 0021): `10 µF` is `10uF`, and so is a
unit alone spelt with `°`, `μ`, `µ` or `Ω`: `μF` is `uF`. Each searched
field is kept, and each search matched, both as typed and so respelt,
where the two differ (`models.search_text.folds` and
`models.search_text.held`, `search.bares` on the scan page), so `feet`
still finds `100 feet`. A search is matched in groups too, each number
with its unit or other word, as typed or respelt (`units.groups`,
`units.ts`'s `groups`), so `100 ft 48 V` finds `100 feet 48 VDC`; the
items list ranks a row holding every group between one holding the whole
search and one only close. The table is
`backend/src/inventory_tng/catalogue/units.py`. After changing it, or
what search keeps, raise its `VERSION`, write the scan page's copy and
the fold reference again, and add a migration that folds every row again
with `models.search_text.held`, with `FOLDS_UNITS` the new version,
folding an item's current aliases after its fields, as `Item.refold`
does, and `name_folded` of items and aliases with
`models.search_text.fold`:

```sh
mise run regenerate-units           # frontend/src/units.json
mise run regenerate-fold-reference
```

`frontend/src/units-vectors.json` holds what both sides must make of some
text; both test against it.

A reading rule (`models.catalogue.ReadingRule`, written only by
`catalogue/reading_rules.py`) settles how a unit as typed after a
number, case kept (`M`, `g`), is read, or that it is no unit, on both
sides: the catalogue sends the rules. Each
change to one reads every name with that unit after a number again
(`items.reread`), refused where two names would then read alike.
Read them with `models.catalogue.reading_rules_held`, which reads them once a
transaction, and again after one is written or a savepoint rolled back;
a form's answers, not yet kept, are read on top of them (`Typed.rules`
in `catalogue/forms.py`). Administrators see and correct them on the Unit
readings page, linked from Items.

Search, the forms and the scan page read a unit after a number alike
(`units.found_in`, `units.respell`, `units.ts`): never in a product's
name (`units.PRODUCTS`); by its rule, which always wins; else, where
several readings are plausible sizes (`units.KINDS`), by the words near it
in the name (`units.CLUES`) if they say one kind; else by the one
plausible reading, the
one of a kind the profile ranks likely (`units.LIKELY`: a bare `G` or `K`
is a data rate, `units.BARE_RATES`), or, none being likely, the one spelt
as typed. A `g`, and a unit both of whose readings are likely, as `M`,
is left to the form (`units.ASKED`). Search reads what nothing settles
by the table.

An item's, a package's or an alias's form saves the name as typed, in
canonical spelling, and says under it how each unit after a number is
read. Where nothing settles one, it asks, offering the readings in
`units.PROFILE`'s order; the answer, or a correction under "Not right?",
is kept as a rule as the form saves. A name is respelt only where its rule
says (`units.spell`). A unit's words (`units.Unit.words`, such as `feet`)
are read by search and spaced in a name as shown, never respelt. A new
unit needs its name in words and, if a name spells it otherwise than its
symbol, that spelling: `units.NAMES` and `units.SHOWN`. The seeding page
(Unit readings, then Propose) proposes rules from every name, by the words
near each unit in the names they settle. The import's readings file
(`catalogue/unit_readings.py`, run by the operator's `unit-readings`
command, DEPLOYMENT.md)
proposes from the names an import brings with the same function
(`reading_rules.propose`), and loads the operator's corrected file as
rules by no administrator.

### Locking a row

Lock rows with `db_locking.locked` (a select) or `db_locking.get_locked`
(`Session.get`), never `with_for_update` itself: they also read the rows
again as locked, which a plain locking read does not do for a row already
loaded. `core/db_locking.py` says why; `tests/across/test_locking.py` fails
on `with_for_update` anywhere else. Catalogue rows are locked with
`db_locking.locked_row` or `db_locking.relock`, which take the catalogue
lock first; `locked_row`'s docstring gives the order every change takes its
locks in. A row is changed or deleted only once this transaction has locked
it.

A row an edit form shows is `models.base.Versioned`: every update adds one
to its `edit_version`, but one only to columns no form shows (flagged
`not_a_change` or `row_lock_only`, as an item's `search_text`, which its
aliases change). The form sends back the one it showed, and the domain
function, holding the row's lock, refuses a row changed since
(`changed_since_shown`, `db_locking.unchanged`), so a save never quietly
undoes another administrator's. Its `seen` argument is required; pass `None`
only where nothing was shown, as a command. A page parses what its form sent
with `web/forms.form_version` and shows a refusal as `RULES[CHANGED]`'s
alert, which `admin_page` answers 409. An administrator's own account is not
versioned: only they edit it.

A request's session comes from the app's request pool; a write in a
transaction of its own, or work after the reply, takes the side pool, and an
attempt counted against a rate limit the limits pool
(`db_sessions.Database`). Take a connection only from a pool after the ones
you hold: every test checks it, and one that breaks the order on purpose is
marked `breaks_pool_order`.

### Reads that must agree

A route that only reads, and whose reads must agree with one another, as
the catalogue's stock and the batches it says it shows must, takes
`db_sessions.SnapshotSession`: the request's session, reading one snapshot
(REPEATABLE READ, read only). A test's session cannot change its
isolation, so a test of the snapshot takes sessions that commit
(`own_committed`, or `make_client(own_sessions=True)`).

### The scan page

The scan page and the registration page are TypeScript, in `frontend/`
(docs/decisions/0010). The backend serves them at `/scan` and `/register`,
and answers 503 until they are built, unless Vite's dev server serves them.

Under `mise run dev` their scripts come from Vite's dev server, at
`VITE_DEV_SERVER` (from your shell or `.env`, else `http://localhost:`
and `INVENTORY_VITE_PORT`; the port is required), so edits show as you
save. A backend you run on its own does the same only if
`VITE_DEV_SERVER` is set. The page still comes from
the backend, so the device cookie and the API work as in production.

Without the dev server, the backend serves a build instead:

```sh
cd frontend
npm run build            # into frontend/dist, which the backend serves
```

A new build is picked up without restarting the backend when
`APP_ENV=development`. Each backend says where its pages' scripts come from
in its `frontend` log line at startup: `source=build` or `source=dev_server`.

The scan page's outbox and cart are kept in `localStorage`, each under a
key with a version (`frontend/src/shared/outbox.ts`, `scan/cart.ts`),
stamped with the build that wrote them. A change to what is kept, or to a
value a field may take, takes a new key, as a tab of the build before, open
during a deploy, drops what it cannot read from what it writes back;
`shared/outbox.test.ts` lists both for the outbox's key. The one exception is an
optional field whose loss leaves the entry as the build before would have
kept it, which that test lists apart. Builds that keep the same shape share
a key, so each batch is kept with the build that queued it, and is sent as
that build's, whichever tab sends it. Why a batch was refused, or waits, is
kept as a code with its data (`outbox.Reason`), and what a batch's summary
says as what it is made from, with its places named as when it was queued
(`outbox.Summary`), never in words: the page makes the words as it shows
them (`noteOf`, `batch.batchWords`), so they follow the language spoken and
can change without a new key. Only older keys hold words, which
`scan/older.ts` reads for their code and shows as kept.

A build reads and writes only its own keys. A tab of an older build sends
its own batches and lets the volunteer record the cart, saying nothing of
the newer build until then, and then asks for an upgrade, a reload; once
the server holds its batches back as too old, it asks at once
(`frontend/src/shared/build.ts`, docs/decisions/0010).

So the server keeps taking what the build before sends. Expand, then
contract: a field the batch endpoint will require is added as optional,
and made required only in a later deploy, after the one whose page sends
it. Better still, the server works the field out itself where it can, so
it never needs to be required. The same goes for any change that makes
the endpoint accept less: a narrower limit, a value dropped from a kind's
list, null no longer taken, or fields it does not name refused. That later
change also sets `OLDEST_BUILD` in
`backend/src/inventory_tng/scan/page_builds.py` to the id of the build the
earlier deploy served (DEPLOYMENT.md, Tightening what a batch accepts). A
batch whose `X-Inventory-Build` header names a build before that is then
answered 409 `build_too_old` and kept on the phone, where a 422 would have
it refused: queued by the sending tab's build, or a newer one, it waits
there while that tab asks for a reload; queued by an older build, it is
listed with what older builds left, to put back in the cart. One naming
no build, from another client of the API, gets the 422.

`backend/tests/batch_schema.json` keeps what the endpoint's schema
accepts, and `backend/tests/scan/test_batch_schema.py` fails on a change
that accepts less, naming each way. A change that only accepts more fails too,
until the file is written again, so the next change is measured against
what was deployed: `mise run regenerate-batch-schema` (from `backend/`:
`uv run python -m tests.helpers.batch_schema`, and `--check` only
compares). Over a change that accepts less, it writes nothing until
`OLDEST_BUILD` has moved, or `--tighten` says no older build's batch is
refused by it. A rule inside a validator (a pydantic model's or field's,
or the ledger's checks), a decimal's digits, and strict mode (`strict=True`,
which refuses `"5"` for a whole number) are not in the schema, so the
test cannot see them: tightening one takes the same care, by hand.

A newer build reads the saves and the cart an older one left only in the
parts every version kept, and removes an older key once nothing in it is
left to list (`frontend/src/scan/older.ts`). Into an empty cart, until it
learns of a newer build, it puts, without a tap, the oldest save that goes
back whole (nothing unread, nothing of an open package, no package
resized, no item or package retired or gone, no refusal). It lists the
rest for the volunteer to add to the cart or discard, naming an item
retired since from the catalogue's `retired_items`, and a package retired
with none left from its item's `retired_packages`, neither ever offered.
A count of a package no longer offered, or of one resized since, and
what was taken from or said of an open package are listed each on its
own line, saying why it cannot go back, and are left out when put back,
never kept loose, unseen or as an empty line; the status then repeats
each note as "item: note". Each of those lines, and one of an item retired
since, has a Discard of its own, which drops that line alone from what
the older build kept; the entry goes with its last listed line, unless
it says lines could not be read, when it stays, saying so, until its own
Discard. Add to cart or Discard, tapped on one another tab changed since
it was listed, does nothing, lists it again as it is now and says so
(`older.compared`); one going back without a tap is tried again as it is
now.

A cart gets its batch key with its first line, and Record sends it under
that key (`CartStore.change`), so every tab that records it, one of a
newer build that took it over included, sends the same key. A save keeps
its batch key while its lines go (`dropPart`). One put back keeps it, as
does a cart when a newer build reads it, and one without a key, left by a
build from before, gets one as it goes back. The cart is sent under its
key until emptied or discarded, as an older tab may be sending it still:
the server then takes it once, or, changed since, refuses it (409
`key_reused`), saying what it recorded under the key, to the request
holding it only. The page then asks for the catalogue and
stock at once, as its periodic refresh does, whichever tab sent it, and
the save stays listed with Dismiss, saying "Some items were not
recorded. Details below.", then what was recorded, headed as recorded in
another tab or, where this browser's outbox holds it recorded, already
recorded from this tab (`outbox.recordedHeading`), and, under "Not
recorded. Make any changes and record again.", what the volunteer changed
since the cart went back, line by line
(`frontend/src/scan/recorded-elsewhere.ts`): a cart put back under its key
keeps the lines it went back with (`changedFrom`), and Record sends them
(`changed_from`), which the server compares with nothing else. Without
them, as from a build before or a cart never put back, it changed
nothing: what differs is the other tab's change. What it holds more of,
or new, goes in an empty cart by itself, under a key made from the
save's, so added twice it is recorded once, and is said to be there until
recorded; while the cart has lines, "Add these to cart" waits, which
takes only an empty cart. Never what was
taken from or said of an open package. Under a line it holds less of, it
says an administrator will correct it: the server, refusing it, keeps
that difference (`stock/over_recorded.py`), and the administrators' home
page lists it under "Needs your action" as "Recorded more than moved"
until one corrects it, with adjustments that undo it on each side, or
marks it checked; an open package moved whole, now where it arrived, has its
count there confirmed, or corrected by an adjustment, instead. From a
server that says nothing of what it recorded, that heading goes over the
save's lines. Such a save never goes in a
cart whole again, here or in the older list, which offers only Discard:
what it held is recorded, and once more would record it twice (reason
`recorded_elsewhere`, which keeps what was recorded). Any other save
refused for its key goes back without it, as the server holds that key
for another batch. A key the outbox already holds, queued again (a cart
whose emptying the browser did not keep, or one a tab that had not heard
filled again), is not queued twice. Another batch under it is kept as
`recorded_elsewhere`, under a key of its own, unless the one under it
was refused for what it held (422): then it is sent (`Outbox.queue`).
Kept so, changed from the batch queued here first under that key, it is
sent under the cart's key once nothing under that key waits, only for the
server to say what it recorded and keep what it took off; the server
takes it only if nothing holds the key after all, and the one refused
(422) under the key then takes a key of its own.
Either way the page says nothing is sending and counts no save: the list
shows what became of it. An opening's open packages take keys made from
the cart's, so the same opening recorded again is the same batch
(`batch.openings`).

A refused save in the outbox's list lists its lines, in the words of the
older list, each item numbered as the refusal's "Line n" names it. While
the browser says it is offline, a batch waiting for an answer says so,
and the page follows the browser going on and off line.

The cart keeps the size each package was counted at, and sends that size,
so the server refuses a count of a package resized since (`package_resized`)
rather than counting it at the new size. Where the page's catalogue shows
another size, the count is cleared and the line says so until a count of
that package is typed or scanned again, even if its size changes back
(`frontend/src/scan/cart.ts`, `reconcile` and `sized`); a count of none of it
is left as it is. Until then Record refuses the cart, as the rest without
it would record less than was moved (`toBatch`). A count kept before the
cart kept sizes is taken as counted at the size shown.

The page's words never say "save": the inventory records what Record
sends, and the browser keeps the cart, which every change writes to
storage as it happens (docs/decisions/0010). Discard cart asks first, then
empties the cart as Record does, dropping its batch key and keeping who
and where, unless another tab changed the cart meanwhile.

The scan page needs a registered device, and some catalogue rows to choose
from. The camera works only over HTTPS or on `localhost`.

Both pages report their errors, three Web Vitals, the outbox's retries,
how long a batch took from its first line to Record, a tab learning that a
newer build is out, what an older build left in storage and what
volunteers did with it, a cart discarded, and what a save another tab
recorded first added put in the cart, through
`frontend/src/shared/telemetry.ts`, to `POST /api/telemetry`, which turns
them into `inventory.*` metrics (docs/decisions/0006). Its schema, in
`backend/src/inventory_tng/scan/page_telemetry_api.py`, lists all a page
may send: something new to report starts there.

#### On a phone

```sh
mise run phone
```

This makes and migrates this checkout's database (`mise run
dev-database`), then runs the app in its container (below) behind a
Cloudflare quick tunnel, which gives it a public
`https://….trycloudflare.com` address with a real certificate, and
prints a one-use registration code for it, with a QR code. Scan that
with the phone to register it, then go on to the scan page.
Pages come from the container's build, not Vite's dev server. The address
changes each time the tunnel starts, and anyone who has it reaches your app
while it runs, so stop it when you are done.

The pages call the API through types generated from its schema. After you
change an endpoint, regenerate both, and commit `frontend/src/api/`:

```sh
cd backend
uv run python -m devtools write-api-schema  # frontend/src/api/openapi.json
cd ../frontend
npm run api                                 # frontend/src/api/schema.d.ts
```

A backend test fails while the schema is out of date, and CI fails while the
types are. `npm run api` installs the generator from `frontend/tools/openapi/`
first, which has its own TypeScript 5 (docs/decisions/0010).

### Styles

Every page shares one stylesheet, `frontend/src/styles.css`, which Tailwind
builds (docs/decisions/0014). Under `mise run dev` it comes from Vite's dev
server and reloads on save; otherwise from the build. Without a build, pages
are plain HTML. The app container builds it into its image, so a class new
to a template shows there only after
`mise run compose -- up -d --build app`.
Administrator pages put their content in `<main class="admin page-width">`;
the scan and registration pages in `<main class="volunteer page-width">`,
with a label above its text box or list as `Field`
(`frontend/src/shared/Field.tsx`). Both
space their forms by gap, and a component styles what only it shows with
utilities. When you style a page:

- Write each class name whole. Tailwind finds them by reading the templates
  and the frontend as text, so one built from parts is never generated.
- Use the tokens in `@theme` (`bg-band`, `text-heading`, …), not colours of
  your own: the dark scheme gives each token its own value.
- Make markup that repeats a Jinja macro or a Preact component.
- A failure or refusal note on the scan page is in the alert's style
  (`role="alert"`, or `class="alert"` for notes shown with a list), in
  bold as a whole, with no "Sorry.", and the counts the page wrote in it
  in bold italic, never the numbers of a name or of words an older build
  kept (`AlertWords` in `scan/Notes.tsx`). A refusal's problems, after "Not
  recorded.", are not bold. A status that reports no problem is plain,
  but a save held for an upgrade is in a caution box like the upgrade
  banner's (`CAUTION_BOX`), with no `role="alert"`.
- Follow docs/decisions/0019 for links, buttons and lists. A button is
  secondary unless it is its form's main one, `class="primary"`, or the one
  a confirmation page asks for, `class="danger"`. A page's way back is
  `back`, a path and its words, in its context, which shows a link above
  its title. A retire form has `data-retire`, so a page with changes not
  yet saved asks before it retires.
- A list page reads a `View` from its query string (`View.read` in
  `web/listing.py`): its route takes `asked: AskedView`, the sort, order,
  page size and page as asked; a `Sorting` of the columns it sorts by, each
  a SQL expression (`Sort.of`) or, for rows built in Python, a key
  (`Sort.keyed`), its first one the page's own order; and its filters, as
  the address says them, those left blank dropped. `ordered` applies it to
  a select, after `count`, with the id last; `arranged` to a list. A list
  of groups pages the groups with `paged` and sorts each group's rows with
  `in_order`. In the template, `_listing.html`'s `sort_heading` for each
  sortable heading, `sizes` above the table, `filter_form` for the
  filters, `unfiltered_or` for the empty list and `pager` below. A form that
  acts on the list's rows sends the view in its action's query string
  (`view.query_string`), and its route reads the same `View` (a
  dependency) to go back to the list as it was, with `view.link()`.
- A list that grows past a screen, as items and volunteers do, is a
  `Listing` (`web/listing.py`), with a `View` of its own: search that
  forgives typos, with suggestions from its own `/suggest`; and "Current ·
  Retired · All". `Searched.read` takes the page's filters and what they
  pick.
- What administrator pages do in the browser beyond plain HTML is in
  `frontend/src/admin.ts`; each still works without it. No template has
  an inline script, a `<style>` or a `style` attribute: the
  Content-Security-Policy refuses them (docs/decisions/0010,
  `web/security_headers.py`). Give an element a class, or a `data-` attribute
  that `admin.ts` acts on, as the Print button's `data-print`.
- A page that prints a QR code puts a `.printable` in its `main`: the
  `print_controls` macro (`_print_controls.html`: the paper and a Print
  button), then a `.print-sheet` for each page, what prints
  (`frontend/src/styles.css`, Printing; as `devices/code_made.html` does, or
  `labels/labels_print.html` for twelve labels to a page).
- Write each table with the `table` macro in `_table.html` (`{% call
  table() %}` its rows `{% endcall %}`): one that cannot fit a phone
  scrolls in its own box, not the page. Text that is often long and unbroken,
  such as a browser's name, can wrap inside its cell, in a `wrap-anywhere`
  span; elsewhere long words wrap by themselves. The browser tests check
  each page they reach with a long word in everything that shows text.

### Registering a device

Administrator pages need someone signed in. To make the first administrator,
set `SETUP_TOKEN` in `.env` to any 32 characters or more, restart, and open
<http://127.0.0.1:8000/setup>. With `APP_ENV=development` the cookies work
over plain http.

Pages that record anything need a registered device (docs/decisions/0009).
Make a registration code on the Devices page, signed in, and enter its
phrase at <http://127.0.0.1:8000/register>, or scan its QR code with a phone.
`uv run python -m inventory_tng.manage registration-codes` does the same
from the command line.

### Mail

To see the mail the app sends, start Mailpit, which catches it all and sends
nothing on: `mise run compose -- --profile mail up -d mail`. Set these in `.env`
and restart, then read the mail at <http://localhost:8025>, or the port
`INVENTORY_MAILPIT_PORT` names (Local settings):

```sh
MAIL_HOST=localhost
MAIL_PORT=${INVENTORY_SMTP_PORT:-1025}
MAIL_SECURITY=none
MAIL_FROM=inventory@localhost
SITE_URL=http://127.0.0.1:8000
```

`MAIL_SECURITY=none` works only with `APP_ENV=development`. For the app
container, set `INVENTORY_APP_MAIL_HOST=mail` too, and `SITE_URL`'s port to
`INVENTORY_APP_PORT`.

### Running the app in a container

```sh
mise run ports-free -- INVENTORY_APP_PORT
mise run dev-database
mise run compose -- up -d --build --wait app  # its port: INVENTORY_APP_PORT
mise run compose -- logs -f app
```

This runs the backend the way production does, in a container beside the
database, for when you want to check that it behaves the same there. The
image builds the scan page too, and serves Brotli and gzip copies of its
files, as production will. It does not replace uv: the checks, new
migrations and your editor still use it. The container migrates this
checkout's database (`INVENTORY_DB_NAME`, which `mise run dev-database`
makes) when it starts, then serves with `--reload`. Each checkout's stack
has its own.
Your `backend/src`, `backend/devtools` and `backend/migrations` are mounted
into it, so it picks up your edits. Pass `--build` again after changing
dependencies or `frontend/`.

It takes only the settings `compose.yaml` lists under the app's
`environment:`, each from your shell or `.env`. A setting the app gains goes
in `.env.example` and in that list; a test checks that every one
`.env.example` names is there. The container sets its own `DATABASE_URL`,
`MIGRATION_DATABASE_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT` and `MAIL_HOST`,
because inside it `localhost` is the container, and its own
`VITE_DEV_SERVER` and `FRONTEND_DIST`, so it serves the bundle it built. To
send its telemetry to the dashboard, start the dashboard (Logging, below)
and set `INVENTORY_APP_OTLP_ENDPOINT=http://dashboard:18890`; to send mail
to Mailpit, `INVENTORY_APP_MAIL_HOST=mail`. It takes `INVENTORY_APP_PORT`
(Local settings), which is 8000, as the run command's port, unless setup
chose another. The client address it logs is
the container network's, not your browser's.

### Using compose directly

Compose on its own (`mise run compose -- up -d db`) gives this checkout's
PostgreSQL server. On a new volume it makes the two roles and
a database, `inventory`, the owner's. It makes no other database a
checkout's `.env` names (`INVENTORY_DB_NAME`), which the app's container
migrates as it starts: until `mise run dev-database` makes it, the
container stops, and in development says to run that. Nor does it check
the ports first, as the tasks do (Local settings): if one is taken, it
fails with the runtime's own message. For most jobs a task is the safer
way:

| Command | What it does | When |
| --- | --- | --- |
| `mise run dev-database` | Starts the server, makes this checkout's database if missing, migrates it | Before the app, here or in its container |
| `mise run start-database` | Starts the server, making its container again if its settings changed | Before the checks |
| `mise run reset-dev-database -- <name>` | Drops this checkout's database, makes it again empty and migrated | After a migration was edited ([Migrations](#migrations)) |
| `mise run compose -- up -d --build --wait app` | The app in its container | After `mise run dev-database` ([above](#running-the-app-in-a-container)) |
| `mise run remove-stale-stacks` | Removes the stacks of checkouts that are gone, once you confirm | After deleting a checkout ([Local settings](#local-settings)) |

### Podman's exec bug

Rootless Podman raises an `oom_score_adj` asked for below its own to its own
as a container starts, as a process without privileges may not lower it
far. Compose asks for 0. Podman's exec, which runs health checks too, still
asks for the lower one, and crun 1.31 and later apply it, so every exec fails
with
``write to `/proc/self/oom_score_adj`: Permission denied``. A health check
then never passes, and `compose up --wait` fails. So each service in
`compose.yaml` with a health check sets `oom_score_adj: 200`, the value
systemd gives a user's services, and a test checks that each does.
Podman's issue:
<https://github.com/podman-container-tools/podman/issues/30245>. Under
Docker it changes nothing that matters for development.

```sh
mise run reproduce-podman-exec-oom-bug            # present, fixed, or neither
mise run podman-exec-oom-workaround-still-needed  # fails once Podman fixes it
```

The first asks exec in two containers of its own, then removes them: one
asked for 0, as compose asks, and one for -1000, below the lowest value a
process without privileges may go back down to (the kernel's
`oom_score_adj_min`, which systemd's defaults set to 100). Present, if
both fail; present but not affecting compose here, if only -1000 fails;
fixed, if neither does. The second
fails when Podman has fixed the bug and `compose.yaml` still works around
it: then remove `oom_score_adj` from `compose.yaml`, both tasks, the test
and this section (inventory-tng-46w). Run the second after upgrading
Podman or crun. Neither is one of the checks, and both say "not applicable"
in CI, whose Docker runs as root, where the bug never shows.

## Phrases

The words people read go through a catalogue on each side
(docs/decisions/0022): Babel's on the server, for Python and the templates,
and Lingui's on the scan page. Each message's id is its English. The
operator commands' words are not in it yet (inventory-tng-8f4).

On the server (`backend/src/inventory_tng/core/phrases.py`):

- In a template, put the words in `{% trans %}`, with each value named:
  `{% trans name=place.name %}Start counting {{ name }}{% endtrans %}`. A
  plural takes `count`: `{% trans count=places | length %}{{ count }}
  place{% pluralize %}{{ count }} places{% endtrans %}`. A count is a
  plural even where English says both forms the same, as other languages
  may not. Values are escaped, and the lines of a message read as one.
  Words a template passes as a value, such as a macro's argument, are
  `_("Find items")`.
- In Python, name a phrase as a constant, said when it is shown:
  `RESET_NOTICE = lazy_gettext("Your account is reset: sign in.")`. A
  phrase with values, or a plural, is a function that says it with
  `gettext("To %(place)s", place=name)` or `ngettext("%(num)d place",
  "%(num)d places", n)`, which fills `%(num)d` with `n`. A table that
  holds such a phrase holds `lazy(function)`.
- Code that passes a phrase on, such as a field's problem or a page's
  alert, types it `Words` and leaves it lazy: the template says it.
  `str()` it only where a str must go, such as a response body, JSON or
  `isinstance` checks. A query takes it as `in_query(phrase)`, said when
  the query runs, so a list sorted by words sorts by those shown.
- A number people read is written as the locale writes it, digits
  grouped: `{{ size | number }}` in a template, `number(size)` in Python.
  A number in a link stays plain digits.
- Values are always filled, so a per cent sign in a message is `%%`; a
  test refuses a lone one.
- The app's name is `phrases.BRAND`, the same in every language: a
  message takes it as a value, `%(brand)s`, never as words of its own.
  Mail says such a message with `mail_messages.branded`, which fills it
  in; a template writes `{{ BRAND }}`.

On the scan page (`frontend/src/shared/i18n.ts`):

- Name a phrase with Lingui's `msg` macro, exported where it is used:
  ``export const REGISTERED = msg`This browser is registered.`;``. A plural
  or a value is ICU MessageFormat: `msg({ message: "{count, plural, one {#
  batch} other {# batches}}" })`.
- Say it with `say(REGISTERED)`, or `say(BATCHES, { count })`. A component
  that says a phrase renders again when the locale changes.
- Where a value is more than a string, such as a name in bold or a name
  kept apart from the page's own words, or the sentence has a link, say
  it with `sayParts`, which puts each such value where the message places
  it and makes `<0>…</0>` with the function given for it:
  `sayParts(REGISTER_IT, {}, [(inside) => <a href="/register">{inside}</a>])`.

On either side, a sentence is one message, any link's markup inside it,
never pieces glued together: `{name}: enter the count`, not the name and
then `: enter the count`. A test reads a phrase by its name, never by
repeating its words.

Where the same English means two things, give each a context from this
list, and add to the list rather than inventing one on the spot; a test
refuses any other. In Python, `pgettext("place", "To")` or
`lazy_pgettext`; in a template, `{% trans "place" %}To{% endtrans %}`; on
the page, `msg({ context: "place", message: "To" })`.

| Context | For | Such as |
| --- | --- | --- |
| `place` | Where something comes from or goes | From, To on the scan page |
| `mail header` | A mail's header | From, To in mail |
| `action` | Something to do, on a button or link | Open a package |
| `state` | How something is | An open package |

For a translator, a comment starting `Translators:` just before a message
goes into the catalogue with it: `# Translators: …` in Python, `{#
Translators: … #}` in a template, and `msg`'s `comment` on the page.

After adding or changing a phrase, write the catalogues again, and commit
them:

```sh
mise run extract-catalogues  # backend's locales/en, frontend's src/locales
mise run check-catalogues    # fails while either is not current
```

Each side has a test that renders its words in a pseudo-locale, every
letter accented, and fails on a word left in plain English:
`backend/tests/core/test_phrases.py` and `frontend/src/pseudo.test.tsx`. On
the scan page, every component is in its `COVERED`, with names that are
not English; add a part for a new component, or for a state of one that
says something no part shows. On the server, every phrase named as a
constant, alone or in a table such as `NOTICES`, is covered already; add
a phrase said with values to `SAID_WITH_VALUES`, a page to its entry in
`tests/helpers/swept_pages.py`'s `PAGES`, as `worded`, which fetches it
signed in with every name in the database not English, or as
`signed_out`, and a template no page there shows, or a branch of one, to
`COVERED`.

## Running the checks

```sh
mise run start-database
cd backend

# Optional: let the tools fix what they can. Neither changes behaviour.
uv run ruff check --fix
uv run ruff format

# Then check, in this order.
uv run ty check                  # types
uv run ruff check                # lint
uv run ruff format --check       # formatting
uv run pytest --cov              # tests, against the database started above
```

`mise run check-backend` and CI run the tests across processes, one per
core and at most 8: `uv run pytest -n auto --maxprocesses=8 --cov`. Each
process makes databases of its own, which takes a moment, so past 8 a run
is slower, and running a few tests is quicker without `-n`.
Connections, below, says how many runs fit at once.

The backend's code is grouped by feature and the frontend's by page, as
docs/decisions/0023 says, which also says where a new file goes.
`tests/codebase/test_import_directions.py` and Biome (`frontend/biome.json`)
refuse an import the wrong way, such as `core/` importing a feature or
`shared/` importing a page's folder.

For the frontend:

```sh
cd frontend
npm run fix                      # optional: Biome's fixes and formatting
npm run typecheck                # types
npm run lint                     # Biome: lint and formatting
npm run coverage                 # Vitest, with coverage (npm test: without)
```

Those are unit tests: Vitest runs the frontend's in a page it simulates
(happy-dom), with no layout. The browser tests, in
`frontend/browser-tests/`, are another kind: Playwright drives a real
browser engine against the whole app, built frontend and all, from a
database of their own (`backend/tests/browser_database.py`), at a phone's
width, with a fake camera in Chromium that shows a seeded label. They check
what only a real browser can, such as that no page scrolls sideways:

```sh
mise run check-browser           # builds, starts the app, runs them
```

A test fails on any Content-Security-Policy violation in a page it opens:
tests import `test` from `browser-tests/csp-violations.ts`, or
`own-rows.ts`, which extends it, never from Playwright, which Biome
refuses.

The tests run at once, in several workers, against that one database, so
none may change rows another reads: a test that changes data first makes the
rows it changes, such as an item to move (`frontend/browser-tests/own-rows.ts`).
Every test shares one administrator's session, signed in once, so what
another test confirmed in it holds: a test of what a session asks for first
takes an administrator of its own (`ownAdmin`, there too). The crawl of
every page (`phone-width.spec.ts`), which reads them all, runs in each
browser before any other test starts.

They run in Chromium; also in WebKit, Safari's engine, in CI and with
`PLAYWRIGHT_WEBKIT=1`. `mise run setup-browsers` installs any that is
missing, as after Playwright changes; `mise run check-browser` runs it.
Playwright's WebKit is built for Ubuntu and may not start on other systems
(installing it says what it lacks, as on Arch or CachyOS). There, run them in
WebKit in Playwright's own container instead:

```sh
mise run browser-webkit          # pulls the image (about 2 GB) the first time
```

Only the browser runs in the container, from Playwright's server, published
on `INVENTORY_WEBKIT_PORT` (3000); the tests and the app run here, and the
browser reaches the app through the tests' connection.
Playwright starts the backend itself, with a database of its own, on a port
chosen for the run, so runs from two clones do not meet.

CI runs each of these checks as a job named for its task, on pushes to `main`
and on pull requests, and builds the frontend. It calls each tool directly,
not through mise. A task named `check-…` is a check CI runs too, as a job of
the same name; one that only makes sense on your machine, such as setting it
up, is named otherwise. A test fails on a job not named for a task, or a
`check-…` task without a job.

Each test belongs to a category by what it needs to run. `tests/conftest.py`
marks the backend's `database` from their fixtures, never by hand: a test
whose fixtures reach `database_url` or `migrated_template` is one. A module
of tests of what is in a clone marks itself `clone`.

| Tests | Need | Run them |
| --- | --- | --- |
| Backend, `not database` | Python; CI runs them with no PostgreSQL | `mise run test-without-database` |
| Backend, `database` | PostgreSQL | `mise run check-backend`, with the rest |
| Backend, `clone` | a clone of the repository; skipped in the image | with the others |
| Frontend | Node | `mise run check-frontend` |
| Browser | PostgreSQL, Node, browsers (`mise run setup-browsers`) | `mise run check-browser` |

The backend's run from `backend/`. Start the database first, or with none
run only the first row; the other checks take about a second each. Then
`mise run fix` runs the fixes and `mise run check` runs every check, or one
side of them:

```sh
mise run fix-backend    # or: mise run fix-frontend
mise run check-backend  # or: mise run check-frontend
mise run check-browser  # the browser tests
mise run check-changed-lines  # both sides, then coverage of what you changed
mise run check-scripts  # shellcheck, on the scripts in mise-tasks/
mise run check-markdown # markdownlint (mise run fix-markdown fixes)
mise run check-toml     # taplo: lint and formatting (mise run fix-toml formats)
mise run check-catalogues  # both sides' catalogues are current (Phrases)
```

Each test run creates a database of its own on that server and drops it at the
end; a run that was killed before it could is dropped by a later one. It makes
the two roles first if the server lacks them. During the tests, `DATABASE_URL`
and `MIGRATION_DATABASE_URL` point nowhere, so nothing reaches your own data:
a test that needs the database takes the `database_url` fixture. A request
test whose route reads no row, such as an error page or a page that sends
someone signed out to sign in, takes `make_client_without_database` or
`make_app_without_database` instead, and runs without PostgreSQL: one whose
request reaches for it fails. Tests log in as the app; one that needs what
only the owner may do, such as `TRUNCATE`, takes `owner_session` or
`owner_engine`. Write tests in pytest style. `tests/conftest.py` holds
fixtures, hooks and what only they use; what tests share besides is in
`tests/helpers/`, which they import from, never from conftest.py or from one
another. A test reads a page's HTML with `tests/helpers/markup.py`, which
parses it with BeautifulSoup and lxml (a link's words and address, a button's
class), not by matching its tags as text, unless the exact markup is the
point, such as escaping or a no-break space, and says so.

Write a rule of more than one condition as a function of plain values,
such as a map of each place's parent, that returns the rule broken or
nothing, and test every way it can be broken without the database
(`tests/places/test_locations.py`). Test on PostgreSQL what only it can
show: that the function making a change refuses it by the rule's name,
once per rule; constraints, indexes and privileges; and, for each rule
that reads other rows, two changes at once, the second waiting, then
refused. Use the real database there, never a mocked session.

Tests run in a random order, the backend's with pytest-randomly and the
frontend's with Vitest's shuffle, so none can depend on another. Each run
prints its seed; to run that order again, pass it, in one process (no
`-n`): `uv run pytest --randomly-seed=<seed>`, or
`npm test -- --sequence.seed=<seed>`.

Every test leaves the database as it found it: the `session` fixture rolls
back; a test whose code commits takes the `committed` fixture, which deletes
the rows it made; one that must commit what is never deleted, such as the
ledger, takes `own_database`, a migrated database for it alone. A test, or
a module through its module-scoped fixtures, that leaves any row added,
changed or deleted fails, named by `leaves_the_database_as_it_was` in
conftest.py.
`tests/across/test_requests_at_once.py` and `tests/across/test_locking.py`
hold tests of requests at the same time, on connections that really
commit, marked by hand with what they are about (the first one's
docstring), apart from what they need: `uv run pytest -m concurrency`
runs only those, and `-m "not end_to_end"` skips the ones through the
whole app. Both are `database` tests too, which a test checks.

A test's own sessions write rows freely to set up what it tests, but the
app's requests on them meet the lock guard (`db_protection.protection`) as in
production: a route that changes a row it did not lock fails its test
(`tests/helpers/apps.py`).

`tests/across/test_query_counts.py` loads each administrator's page, and
the scan page's catalogue, with one row of what it lists and with eleven,
and fails if the second sends more statements: a page reading row by row.
It also fails unless the page, past its masthead, is longer with eleven,
and grows when the page's own `more` adds one row to the first. A new GET
route under `/admin` fails there until it has an entry in
`tests/helpers/swept_pages.py`'s `PAGES`, saying how to add one more of
what it lists (`more`) or why it lists nothing that grows (`uncounted`),
and where `test_phrases.py` reads its words or why it doesn't. Both read
worlds built once a module, each in a database of its own, each test in a
transaction rolled back after it. `tests/helpers/query_counts.py` counts a
request's statements for a test of its own.

### Where tests go

`backend/tests/` mirrors the source tree. Any folder under it with no
counterpart in the source has its own short `README.md` saying what it holds.

### Connections

Each checkout's tests, apps and browser tests use its own stack's
PostgreSQL, which allows 400 connections (`compose.yaml`), less 3 kept for
the superuser. A whole test run, measured over 8 processes, holds about 70
at once, all processes together; one process, and one database, held at most
about 21; a dev app up to 20, as does the browser tests' server. So a
checkout's checks and its app fit with room to spare, and so would three
checkouts sharing `inventory-tng` (Local settings). Each test database
allows twice the most measured on one (`TEST_CONNECTION_LIMIT` in
`tests/databases.py`), so a process that leaks connections is refused at its
own database, not the server's limit, and a test that fails for either limit
says which. Long before a leak reaches that limit, a test that ends with a
connection it took from any pool and kept errors at its teardown, named by
`keeps_its_connections` in conftest.py. Each checkout's development database
allows 30 (`backend/devtools/database.py`): one app, and a migration or seed
beside it, so it cannot starve the tests beside it.

### Measuring the catalogue

```sh
mise run measure-catalogue   # MEASURE_YEARS=10 MEASURE_PER_DAY=80 for more
```

It fills a database of its own with years of saves, sealed and open (five
years of 40 a day unless told otherwise), prints the plan of each ledger
query the scan page's catalogue makes, as run (`EXPLAIN (ANALYZE,
BUFFERS)`), and times `GET /api/catalogue`. It is not one of the checks:
run it after changing those queries (inventory-tng-kcz).

### Measuring the tests

```sh
mise run test-report         # about five minutes
```

It runs each suite, the backend's twice and each frontend test alone, and
writes `.local/test-report/report.md`: each suite's tests, time and
coverage, its slowest tests, how many tests cover nothing that no other test
does, the groups of tests that cover exactly the same, and the longest test
files. Beside it, a table per suite gives each test's seconds, what it
covers, what it alone covers (what removing it would lose) and its group.
Use it to find tests to merge or speed up; the module's docstring
(`backend/devtools/suite_report.py`) says how each part is measured.

### Coverage

`mise run check` and CI measure which code the tests run, and fail when:

- **the whole falls below its floor**: 94% of the backend's lines and
  branches, its developer tools among them (`fail_under` in
  `backend/pyproject.toml`), and the frontend's floors in
  `frontend/vite.config.ts`. Vitest raises those as coverage rises: commit
  that change with yours.
- **any line you added or changed is not run by a test**, or a branch on it
  is taken only one way (`[tool.diff_cover]` in `backend/pyproject.toml`).
  `mise run check-changed-lines` compares with `origin/main`, so run it
  before you push; CI compares with what `main` was before the push. For a
  line not worth a test, write `# pragma: no cover` (backend) or
  `/* v8 ignore next */` (frontend) with a comment saying why.

Only `uv run pytest --cov` and `npm run coverage` measure, so a test file
run alone is never held to the floors. Their reports
(`backend/coverage.xml`, `frontend/coverage/`) are not committed. Code that
only the type checker sees (`if TYPE_CHECKING:`), the API types generated
in `frontend/src/api/`, and the frontend's page entry files are left out.

## Dependency updates

Each kind of dependency is updated with its own tool. Make each update an
issue and a commit of its own, and run `mise run setup` and `mise run check`
after it.

- **Tools in `mise.toml`**: `mise outdated --bump` lists newer releases;
  `mise upgrade --bump` moves every pin, `mise upgrade --bump <tool>` one. CI
  installs the same pins.
- **Python packages**: `uv lock --upgrade` in `backend/` moves everything in
  `uv.lock`, or `uv lock --upgrade-package <name>` one. `pyproject.toml` gives
  each dependency a floor at a minor version only, such as `fastapi>=0.162`:
  `uv.lock` pins what is installed, and is always committed. `uv add` (or
  `uv add --dev` for one only tests and tools use, which the image leaves
  out) writes a patch too (`>=0.162.0`); trim it, as a test checks. Two
  exceptions: a `0.0.z` package keeps its patch, there being no minor to
  name, and a series released only as betas, such as OpenTelemetry's
  instrumentation, floors at the minor's first beta (`>=0.66b0`).
  A new anyascii, or a Python with a newer Unicode, fails
  `tests/models/test_search_text_fold_reference.py`, with the difference:
  the file's first line names both versions. If you take it, run
  `mise run regenerate-fold-reference` (from `backend/`:
  `uv run python -m tests.helpers.fold_reference`, and `--check` only
  compares). If a spelling changed, also add a migration that folds again
  the rows whose `folded_with` names the old version (docs/decisions/0021).
- **npm packages**: `npm outdated`, then `npm update` (or `npm install
  <name>@<version>`) in `frontend/`; commit `package-lock.json`.
- **GitHub Actions** in `.github/workflows/ci.yml`: by hand, to a release at
  least two weeks old.

uv's exact version is the pin in `mise.toml`, which CI reads too.
`backend/pyproject.toml` names it twice more, both by minor version:
`required-version`, a floor, and `uv_build`, the build backend, capped below
the next minor (`uv_build>=0.13,<0.14`). A patch release of uv needs only
`mise upgrade --bump uv`. A new minor needs the `uv_build` cap moved with it,
or the build uses an older backend than the uv running it. The floor stops a
uv older than it, not a newer one installed some other way, so use the uv
mise installs. If uv stops with `Required uv version … does not match the
running version`, its advice to run `uv self update` is wrong here: change
the pin in `mise.toml` or the floor instead.

## Documentation

README.md, CONTRIBUTING.md and DEPLOYMENT.md are for people setting up,
running and working on the app. Keep them:

- **Current.** They describe what the code on `main` needs, nothing more. A
  change that alters a step, a command, a setting or a behaviour a doc
  describes updates that doc in the same change.
- **Free of history.** Why something is the way it is, what it replaced and
  what was rejected go in a decision record under `docs/decisions/`. Link to
  it when a reader needs the reason.
- **Short and clear.** Steps a reader can follow in order, in plain words.
  Cut what the reader does not need to do the task.

Change an existing doc rather than adding one, and add a new file only when
an issue asks for it.

Record a decision in words, with who made it and when, not as a link to
where it was discussed or the letter of a choice made there. A test refuses
claude.ai artifact links, lettered choices and question codes in the
clone's files.

Lint what you write or change with `mise run check-markdown`.

## Logging

Use `debug` for detail you need when tracing a problem, `info` for what an
operator wants to see, `warning` for something unexpected that was handled,
and `error` for a failure, with its exception.

Log ids, which only an administrator can look up, rather than anything that
names a person:

| | In logs | In metric attributes |
| --- | --- | --- |
| Ids (volunteer, admin user, item, location, label, transaction), install numbers, admin usernames | yes | no |
| The client's address | only if `LOG_CLIENT_ADDRESS` is on (the default; turn it off in Kubernetes, where the ingress keeps it) | no |
| Volunteer names, emails, phone numbers, street addresses | no | no |
| Free text (notes, reasons, searches), request bodies, database error messages | no | no |
| Secrets (passwords, tokens, cookies, sessions, TOTP, connection strings) | never | never |

Metric attributes take values from a fixed list in code. For a database error,
log `describe_error(error)`: its class, code and constraint, never its message,
which can quote values.

As a safety net, fields are masked by name (docs/decisions/0008). Secrets are
masked always. Outside development (`APP_ENV`, which defaults to
`production`), so are personal data, fields named `sensitive_*`, and the
values database and validation errors quote in tracebacks. So name a field
for what it holds. In development, `LOG_REDACTION=on` masks those too, to
test it; how to see them in production for a while is in DEPLOYMENT.md.
`UNMASK_SECRETS=true` shows secrets too, for debugging; it works only with
`APP_ENV=development`, and the app refuses to start with it anywhere else.

Logs are colourised on a terminal. `LOG_LEVEL=DEBUG` turns on debug logs,
`LOG_LEVELS` sets single loggers' levels (`sqlalchemy.engine=INFO` shows SQL,
with its parameters hidden), and `LOG_FORMAT=json` prints the JSON that
production emits. Every request is logged once, health checks included;
`LOG_HEALTH_CHECKS=false` moves successful ones to `debug`. In tests, check
log lines with structlog's `capture_logs()`.

Exporting logs and metrics over OpenTelemetry stays off unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set (or a signal's own endpoint, such as
`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`). Export is OTLP over HTTP (port 4318),
not gRPC. Of the other standard variables, `OTEL_SERVICE_NAME`,
`OTEL_RESOURCE_ATTRIBUTES` and the SDK's timing settings apply, and
`OTEL_LOGS_EXPORTER=none` or `OTEL_METRICS_EXPORTER=none` stops one signal.
`OTEL_TRACES_EXPORTER=otlp` sends traces, which are off by default. Spans
leave through `telemetry.RedactingSpanExporter`: outside development, with
only the attributes in `KEPT_ATTRIBUTES` (add one there, with the reason,
to keep it); in development, whole, unless `TRACE_REDACTION=on`. Secrets in
query strings are masked either way, unless `UNMASK_SECRETS=true`. How to
see unredacted traces in production for a while is in DEPLOYMENT.md.
To see telemetry locally, start the dashboard, run the server with
`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:${INVENTORY_OTLP_PORT:-4318}` in
`.env`, and open <http://localhost:18888>, or the port
`INVENTORY_DASHBOARD_PORT` names (Local settings):

```sh
mise run compose -- --profile telemetry up -d dashboard
```
