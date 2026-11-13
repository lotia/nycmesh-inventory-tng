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
# make one commit (or jj change) for the issue,
# with a message that starts with "<id>: "
bd close <id>
bd sync
```

Then run `mise run check` on what you are about to push, every time, and
push only if it passes: there is no pull request to catch what it would
have. Push straight to `main`, where CI checks it again: `git push origin
main`, or with jj, `jj bookmark set main -r <change>` and `jj git push`.

### Labels

Label every issue you file (`bd create --labels a,b`, or `bd label add`
after): one or more areas, and each kind that applies. Use these, and add to
this list rather than inventing one on the spot.

| Label | For |
| --- | --- |
| `area:accounts` | Administrators, signing in, their mail |
| `area:catalogue` | Items, categories, places, volunteers, labels |
| `area:stock` | The ledger, counts, adjustments, stock views |
| `area:devices` | Registering devices, registration codes |
| `area:scan-page` | The volunteers' scan and register pages |
| `area:admin-ui` | The administrator pages' layout and look |
| `area:printing` | Printed QR codes and labels |
| `area:telemetry` | Logs, metrics, traces |
| `area:security` | Anything that changes what an attacker could do |
| `area:tooling` | Tests, mise tasks, CI, the dev setup |
| `area:deploy` | Images, Kubernetes, operating the app |
| `area:docs` | The project's own documents |
| `research` | Research or investigation comes first |
| `design-research` | Research into how the pages look and behave |
| `needs-decision` | Waits on a maintainer's choice or input; remove it once given |
| `manual-test` | Steps someone does by hand |
| `revisit` | An idea set aside, to look at again |
| `race` | A bug only requests at once can cause, unlikely but possible; P1, ahead of features |

To find them: `bd list --label needs-decision`, `bd list --label area:stock`,
`bd ready --label-any research,manual-test`, and `bd label list-all` for
every label in use.

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
container: `mise run compose -- exec app python -m inventory_tng.seed`.

### Migrations

Until the first production deploy, a change may edit an existing migration
instead of adding one, so the schema's history stays short. After a pull
that does, your local database no longer matches its migrations: rebuild
it, losing its data, with `mise run reset-dev-database -- <its name>`. It
drops only this checkout's database, which you name to confirm, then makes
and migrates it again, empty. Never rebuild with `mise run compose -- down
--volumes`: that removes the stack's volume, with every database in it,
and in `inventory-tng` every checkout's that shares it. Once production runs,
a migration is never edited: add a new one.

Migrations run as the owner, and every table they create gives the app
`SELECT`, `INSERT`, `UPDATE` and `DELETE` (migration 0001). A migration
that creates a table the app must not change revokes what it must not do
there, as 0002 does for history and for columns set only as a row is
made, 0004 for the ledger, and 0005 to 0007 for deleting a settings row.
`tests/test_database_role.py` lists those columns, so a new column the app
cannot change fails there until listed. What a migration writes is not in
the history. Migrations create no trigger, function or view: the rules are
the app's, and `tests/test_models.py` fails on one.

### History

Every row added, changed or deleted through the ORM, in a table with
history (see [The catalogue's rules](#the-catalogues-rules)), is written
to `catalogue_history` as the session flushes, with who did it
(`db.set_actor`). So write those tables through the ORM: `session.add`,
an object's attributes, `session.delete`. A statement
such as `update(Item)` is never recorded, and `tests/test_history.py`
fails on one. The app may only add to the history.

### The catalogue's rules

Each catalogue table is written only by its own module: `categories.py`,
`items.py` (items and their packages), `locations.py`, `volunteers.py` and
`labels.py`. Admin pages and the seed call those modules' functions, one
per kind of change, which take the catalogue lock, lock and read what the
rules need, and refuse a change that breaks one by raising `db.RuleBroken`
with the rule's name. `db.attempt` returns that name, and
`admin/common.py`'s `RULES` says it in words. A page reads rows with
`session.get` and leaves locking to those functions. The ledger is
written only by `ledger.insert_transaction`, which saves, counts and
adjustments all call, and which checks its rules the same way.
A rule about one row's own values is a check constraint, or, where
PostgreSQL cannot check it, the model's own `@validates`, as
`SiteSettings` checks the site's time zone.

Each model declares who writes its table, holding what, and whether it has
history, with `db.protection`; `db.py` gives the rules, which the flush and
`tests/test_write_owners.py` enforce. An owner is a domain module, never a
page's, as `adjustment_reasons.py` is; a settings row, made by its
migration, has none, and `scan_settings.change` is its one writer.
`items.Fields` names every column an item's save sets, each required, so
none is blanked by being left out. The ledger takes lines, not a mapping by
item, so none is lost: a transaction naming an item twice is refused
(`movement_item_once`), and its movements' unique index says so too.

Item and package names are unique ignoring case, spaces and punctuation
(`models.name_key`, decision 0021). A decimal point between digits counts,
a comma never does, as US number conventions have it: `30.5 m` and
`305 m` are two names, `1,000 ft` and `1000 ft` one. Where a comma is the
decimal mark this is wrong (`30,5 m` clashes with `305 m`); the app is
English-only and follows US conventions today (inventory-tng-g6u.1, under
inventory-tng-g6u).

### Locking a row

Lock rows with `db.locked` (a select) or `db.get_locked` (`Session.get`),
never `with_for_update` itself: they also read the rows again as locked,
which a plain locking read does not do for a row already loaded. `db.py`
says why; `tests/test_locking.py` fails on `with_for_update` anywhere else.
Catalogue rows are locked with `db.locked_row` or `db.relock`, which take
the catalogue lock first; `locked_row`'s docstring gives the order every
change takes its locks in. A row is changed or deleted only once this
transaction has locked it.

A row an edit form shows is `models.Versioned`: every update adds one to
its `edit_version`. The form sends back the one it showed, and the domain
function, holding the row's lock, refuses a row changed since
(`changed_since_shown`, `db.unchanged`), so a save never quietly undoes
another administrator's. Its `seen` argument is required; pass `None` only
where nothing was shown, as a command. A page parses what its form sent
with `admin/common.form_version` and shows a refusal as `RULES[CHANGED]`'s
alert, which `admin_page` answers 409. An administrator's own account is
not versioned: only they edit it.

A request's session comes from the app's request pool; a write in a
transaction of its own, or work after the reply, takes the side pool, and
an attempt counted against a rate limit the limits pool (`db.Database`). Take
a connection only from a pool after the ones you hold: every test checks it,
and one that breaks the order on purpose is marked `breaks_pool_order`.

### Reads that must agree

A route that only reads, and whose reads must agree with one another, as
the catalogue's stock and the batches it says it shows must, takes
`db.SnapshotSession`: the request's session, reading one snapshot
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
key with a version (`frontend/src/outbox.ts`, `cart.ts`), stamped with the
build that wrote them. A change to what is kept, or to a value a field may
take, takes a new key, as a tab of the build before, open during a deploy,
drops what it cannot read from what it writes back; `outbox.test.ts` lists
both for the outbox's key. A build reads and writes only its own keys: a
tab of an older build sends its own batches, then asks for a reload
(`frontend/src/build.ts`, docs/decisions/0010). A newer build lists the
saves and the cart an older one left, read only in the parts every version
kept, for the volunteer to put each back in the cart or discard it, and
removes an older key once nothing in it is left to list
(`frontend/src/older.ts`).

So the server keeps taking what the build before sends. Expand, then
contract: a field the batch endpoint will require is added as optional,
and made required only in a later deploy, after the one whose page sends
it. Better still, the server works the field out itself where it can, so
it never needs to be required. That later change also sets `OLDEST_BUILD` in
`backend/src/inventory_tng/page_builds.py` to the id of the build the
earlier deploy served (DEPLOYMENT.md, Making a field required). A batch
whose `X-Inventory-Build` header names a build before that is then
answered 409 `build_too_old` and kept on the phone, where a 422 would have
it refused; one naming none, from another client of the API, gets the
422. `backend/tests/test_page_builds.py` lists the fields the endpoint
requires, and fails when one is added.

The scan page needs a registered device, and some catalogue rows to choose
from. The camera works only over HTTPS or on `localhost`.

Both pages report their errors, three Web Vitals, the outbox's retries,
how long a batch took from its first line to saving, a tab learning that a
newer build is out, and what an older build left in storage and what
volunteers did with it, through
`frontend/src/telemetry.ts`, to `POST /api/telemetry`, which turns them into
`inventory.*` metrics (docs/decisions/0006). Its schema, in
`backend/src/inventory_tng/page_telemetry.py`, lists all a page may send:
something new to report starts there.

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
uv run python -m inventory_tng.openapi   # frontend/src/api/openapi.json
cd ../frontend
npm run api                              # frontend/src/api/schema.d.ts
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
(`frontend/src/Field.tsx`). Both
space their forms by gap, and a component styles what only it shows with
utilities. When you style a page:

- Write each class name whole. Tailwind finds them by reading the templates
  and the frontend as text, so one built from parts is never generated.
- Use the tokens in `@theme` (`bg-band`, `text-heading`, …), not colours of
  your own: the dark scheme gives each token its own value.
- Make markup that repeats a Jinja macro or a Preact component.
- Follow docs/decisions/0019 for links, buttons and lists. A button is
  secondary unless it is its form's main one, `class="primary"`, or the one
  a confirmation page asks for, `class="danger"`. A page's way back is
  `back`, a path and its words, in its context, which shows a link above
  its title. A retire form has `data-retire`, so a page with changes not
  yet saved asks before it retires.
- A list that grows past a screen, as items and volunteers do, is a
  `Listing` (`admin/listing.py`), drawn with `_listing.html`'s macros:
  search that forgives typos, with suggestions from its own `/suggest`;
  "Current · Retired · All"; and pages of 50.
- What administrator pages do in the browser beyond plain HTML is in
  `frontend/src/admin.ts`; each still works without it.
- A page that prints a QR code puts a `.printable` in its `main`: the
  `print_controls` macro (`_print_controls.html`: the paper and a Print
  button), then a `.print-sheet` for each page, what prints
  (`frontend/src/styles.css`, Printing; as `code_made.html` does, or
  `labels_print.html` for twelve labels to a page).
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
`uv run python -m inventory_tng.registration_codes` does the same from the
command line.

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
Your `backend/src` and `backend/migrations` are mounted into it, so it picks
up your edits. Pass `--build` again after changing dependencies or
`frontend/`.

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
browser engine against the whole app,
built frontend and all, from a database of their own
(`backend/tests/browser_database.py`), at a phone's width, with a fake
camera in Chromium that shows a seeded label. They check what
only a real browser can, such as that no page scrolls sideways:

```sh
mise run check-browser           # builds, starts the app, runs them
```

The tests run at once, in several workers, against that one database, so
none may change rows another reads: a test that changes data first makes
the rows it changes, such as an item to move
(`frontend/browser-tests/own.ts`).
Every test shares one administrator's session, signed in once, so what
another test confirmed in it holds: a test of what a session asks for
first takes an administrator of its own (`ownAdmin`, there too).
The crawl of every page (`phone-width.spec.ts`), which reads them all,
runs in each browser before any other test starts.

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
```

Each test run creates a database of its own on that server and drops it at
the end; a run that was killed before it could is dropped by a later one.
It makes the two roles first if the server lacks them. During the tests,
`DATABASE_URL` and `MIGRATION_DATABASE_URL` point nowhere, so nothing
reaches your own data: a test that needs the database takes the
`database_url` fixture. A request test whose route reads no row, such as
an error page or a page that sends someone signed out to sign in, takes
`make_client_without_database` or
`make_app_without_database` instead, and runs without PostgreSQL: one whose
request reaches for it fails. Tests log in as the app; one that needs what
only the owner may do, such as `TRUNCATE`, takes `owner_session` or
`owner_engine`. Write tests in pytest style. `tests/conftest.py` holds
fixtures, hooks and what only they use; what tests share besides is in
`tests/helpers/`, which they import from, never from conftest.py or from
one another.

Write a rule of more than one condition as a function of plain values,
such as a map of each place's parent, that returns the rule broken or
nothing, and test every way it can be broken without the database
(`tests/test_catalogue_rules.py`). Test on PostgreSQL what only it can
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
`tests/test_requests_at_once.py` and `tests/test_locking.py` hold tests of
requests at the same time, on connections that really commit, marked by
hand with what they are about (the first one's docstring), apart from what
they need: `uv run pytest -m concurrency` runs only those, and
`-m "not end_to_end"` skips the ones through the whole app. Both are
`database` tests too, which a test checks.

A test's own sessions write rows freely to set up what it tests, but the
app's requests on them meet the lock guard (`db.protection`) as in
production: a route that changes a row it did not lock fails its test
(`tests/helpers/apps.py`).

`tests/test_query_counts.py` loads each administrator's page, and the scan
page's catalogue, with one row of what it lists and with eleven, and fails
if the second sends more statements: a page reading row by row. A new GET
route under `/admin` fails there until it has an entry in `PAGES`, saying
how to add one more of what it lists, or in `EXEMPT`, saying why it lists
nothing that grows. `tests/helpers/queries.py` counts a request's
statements for a test of its own.

### Connections

Each checkout's tests, apps and browser tests use its own stack's
PostgreSQL, which allows 400 connections (`compose.yaml`), less 3 kept for
the superuser. A whole test run, measured over 8 processes, holds under 60
at once, all processes together; one process held at most about 24, and
one database under 20; a dev app
up to 20, as does the browser tests' server. So a checkout's checks and its
app fit with room to spare, and so would three checkouts sharing
`inventory-tng` (Local settings). Each test database allows 40
(`tests/databases.py`), so a process that leaks connections is refused at
its own database, not the server's limit, and a test that fails for either
limit says which. Long before a leak reaches that limit, a test that ends
with a connection it took from any pool and kept errors at its teardown,
named by `keeps_its_connections` in conftest.py. Each checkout's development
database allows 30 (`dev_database.py`): one app, and a migration or seed
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

### Coverage

`mise run check` and CI measure which code the tests run, and fail when:

- **the whole falls below its floor**: 94% of the backend's lines and
  branches (`fail_under` in `backend/pyproject.toml`), and the frontend's
  floors in `frontend/vite.config.ts`. Vitest raises those as coverage
  rises: commit that change with yours.
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
  `uv.lock` pins what is installed, and is always committed. `uv add` writes
  a patch too (`>=0.162.0`); trim it, as a test checks. Two exceptions: a
  `0.0.z` package keeps its patch, there being no minor to name, and a series
  released only as betas, such as OpenTelemetry's instrumentation, floors at
  the minor's first beta (`>=0.66b0`).
  A new anyascii, or a Python with a newer Unicode, fails
  `tests/test_fold_reference.py`, with the difference: the file's first
  line names both versions. If you take it, run
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
