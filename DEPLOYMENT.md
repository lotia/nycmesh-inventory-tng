# Deployment

How to run the app on a server. To run it on your own computer, see
[CONTRIBUTING.md](CONTRIBUTING.md#running-the-app).

There is no production container image or Helm chart yet
(inventory-tng-8i4.18). Until there is, the app runs from a checkout.

## What it needs

- PostgreSQL. Development and the tests use 18.
- [uv](https://docs.astral.sh/uv/), which installs the Python version the
  backend asks for.
- Node, at the version in `mise.toml`, to build the scan page. It is needed
  only to build, not to run.
- HTTPS in front of it, from a reverse proxy or ingress. Outside development
  the device and sign-in cookies are `Secure`: over plain http a browser
  never keeps them, so no device stays registered and nobody stays signed
  in.

## Settings

Set these in the environment. Only `DATABASE_URL` and `TOTP_KEYS_DIR` are
required, and for migrations only `MIGRATION_DATABASE_URL`.

- `DATABASE_URL`: `postgresql://user:password@host:port/database`, logging
  in as the app's role (Database roles, below).
- `MIGRATION_DATABASE_URL`: the same, logging in as the owner. Only
  migrations read it; the app never needs it.
- `TOTP_KEYS_DIR`: the folder holding the keys that seal administrators'
  authenticators (TOTP keys, below).
- `APP_ENV`: leave unset in production. `development` is for local work
  only: it stops masking personal data in logs and redacting traces, lets the
  cookie travel over http, re-reads the scan page's build on every page, and
  allows `UNMASK_SECRETS`, `TRACE_REDACTION=off` and `VITE_DEV_SERVER`.
- `SETUP_TOKEN`: lets the first administrator set up (below); at least 32
  characters. Remove it once they have.
- `RECOVERY_TOKEN`: lets a locked-out administrator back in (below); at
  least 32 characters. Remove it once they are.
- `MAIL_HOST`, and with it `MAIL_FROM` and `SITE_URL`: mail to
  administrators (below). Without `MAIL_HOST`, none is sent.
- `FORWARDED_ALLOW_IPS`: behind a proxy, the proxy's address, so the client
  address and scheme come from its headers.
- `LOG_FORMAT`: `json` for a log collector; the default is `console`.
- `LOG_LEVEL`, `LOG_LEVELS`: `INFO` by default.
- `LOG_CLIENT_ADDRESS`: `false` when the proxy logs client addresses itself.
- `LOG_HEALTH_CHECKS`: `false` logs successful health checks at debug.
- `OTEL_EXPORTER_OTLP_ENDPOINT`: sends logs and metrics over OTLP/HTTP; off
  when unset. `OTEL_TRACES_EXPORTER=otlp` sends traces too, redacted
  (below).
- `TRACE_REDACTION_OFF_UNTIL`, `REDACTION_OFF_MAX_HOURS`: traces unredacted
  for a while (Debugging with unredacted traces, below).
- `FRONTEND_DIST`: the folder holding the compressed scan page (below). It
  defaults to the checkout's `frontend/dist`.
- `VITE_DEV_SERVER`: never in production; the app refuses to start with it.

What each logging setting does is in
[CONTRIBUTING.md](CONTRIBUTING.md#logging).

### Database roles

The app and its migrations log in as two roles, so that the app can read
and add to the stock ledger and the history but never change or empty
them, and can change the settings but never delete them:

- `inventory_owner` owns the database and everything in it, and runs the
  migrations. On CloudNativePG it is the Cluster's `bootstrap.initdb.owner`,
  with the database as `bootstrap.initdb.database`; its Secret goes only to
  whatever migrates.
- `inventory_app` is the app's login: a managed role (`managed.roles`, with
  `login: true` and a `passwordSecret`), with no other attributes. The name
  is fixed: migrations grant to it. Its Secret goes to the app.

Making the two roles is the only step that needs a superuser. The migrations
grant `inventory_app` what it may do, so nobody grants anything by hand.
Roles belong to the whole PostgreSQL server: two environments sharing one
server would share `inventory_app` too, so give each its own server, as each
CloudNativePG Cluster is.

The app checks its login as it starts and refuses to start, logging
`database login refused`, if it:

- could `UPDATE`, `DELETE` or `TRUNCATE` the ledger or the history;
- could `DELETE` or `TRUNCATE` a settings table;
- cannot read and add to the ledger and the history;
- owns any table.

As the owner or a superuser, it would. It also refuses to start if it cannot
check, logging `database login not checked`: the database is out of
reach, or not yet migrated, so the ledger is not there to check.

The app writes the history (`catalogue_history`: who changed what, and
when) itself. Changes made by migrations, or by SQL run by hand as either
role, are not in it.

### Debugging with unredacted traces

Traces leave redacted: only the span attributes on an allow-list, no query
text, query string or client address, and errors without their messages
(docs/decisions/0008). To see more for a while:

1. Set `TRACE_REDACTION_OFF_UNTIL` to a time at most
   `REDACTION_OFF_MAX_HOURS` (default 6) hours away, such as
   `2026-11-07T18:00Z`, and restart. The app refuses a time further off.
2. While it is off, each process's starting line is a warning saying until
   when, each warns at most hourly while unredacted spans leave, and every
   span carries `inventory.redaction=off`. Secrets stay masked.
3. Past the time, redaction comes back on by itself. Remove the setting at
   the next deploy.

A collector in between may keep its own redaction; relax or route it on
purpose if the unredacted spans are to reach you.

## Installing, migrating and starting

`UV_NO_DEV=1` keeps the development tools out: every `uv` command below
would otherwise install them.

Build the scan page into a new folder for each version, for example one
named after its commit, with a Brotli and a gzip copy beside each file.
`servestatic` reports success even when compressing fails, so check that it
wrote the copies:

```sh
export UV_NO_DEV=1
cd frontend
npm ci
npm run build
cd ../backend
uv sync --locked
uv run servestatic ../frontend/dist /srv/inventory/static-<commit> --compress --no-zstd
test -n "$(find /srv/inventory/static-<commit>/assets -name '*.br' -print -quit)"
```

Then point `FRONTEND_DIST` at the new folder, migrate and restart:

```sh
uv run alembic upgrade head    # before starting a new version, as the owner
uv run python -m inventory_tng --host 0.0.0.0 --port 8000 --workers 2
```

The app reads the scan page's build once, at startup, so it serves a new
build only after a restart. Delete the previous version's folder once the
restarted app is serving. Until there is a build, `/scan` and `/register`
answer 503 and the app logs a warning. Its `frontend` log line at startup
says which folder it serves.

`GET /api/healthz` answers 200 when the app can reach the database, and 503
when it cannot. Use it as a readiness check, not a liveness check: a
database outage once it runs should take the app out of rotation, not
restart it.

The limits on guessing registration phrases, passwords and codes are kept
in the database (the `rate_limit` table), so every pod and worker counts
against the same limit.

### Database connections

Each process (one per pod, or each of `--workers`) holds at most 20 connections
to PostgreSQL, in three pools, none of whose connections ever waits for another
from a pool that waits on it: 15 for requests (10, and 5 more under load); 3 for
recording when a device or administrator was last seen and for work after the
reply (such as a reset email); and 2 for attempts counted against a rate limit.
At most 15 requests use the database at once; the rest wait their turn, holding
nothing, and one that waits 10 seconds answers 503, as does one that waits that
long to count an attempt against a rate limit; one that waits that long to
record when it was last seen goes on without. Each is counted as
`db.client.connection.timeouts`, by pool, and `db.client.connection.count` shows
each pool's connections in use. An error logged as "connection taken out of pool
order" is a bug that could let the pools wait on themselves: report it.

Any connection, the migrations' and commands' too, waits at most 10 seconds
for a lock: a request answers 503 (recording when it was last seen is
skipped instead), and a migration stuck behind the running app's work fails
rather than hold up every query after it, so run it again.

Count the total before scaling: processes × 20, plus one for a migration,
must stay under PostgreSQL's `max_connections` (100 by default, less 3 kept
for superusers and CloudNativePG's own).

## Backups

CloudNativePG backs the database up to an object store: base backups, on a
`ScheduledBackup`, and the write-ahead log as it is written, so it can be
restored to any moment. A backup is a copy of everything, not only the
inventory: administrators' usernames, emails and password hashes, their
sealed TOTP keys, volunteers' names, devices' first addresses, sign-in
sessions' browsers, and the database roles' password hashes.

- **Encrypted at rest.** Turn on the object store's server-side encryption
  for both the base backups and the write-ahead log (`encryption: AES256`,
  or `aws:kms` with a key of the deployment's own), or use a bucket that
  encrypts everything by default.
- **Few can read or restore it.** The cluster's credentials may write to its
  bucket and path only. Reading the bucket, and so restoring, is for the
  operators who run the cluster, through credentials kept in the cluster
  namespace's Secrets, never on a laptop. Turn on the bucket's object
  versioning or lock, so that the cluster's own credentials cannot delete
  what it wrote.
- **Never with the TOTP keys.** The `TOTP_KEYS_DIR` Secret is backed up
  apart (TOTP keys, below): a backup alone cannot make anyone's codes.
- **No copies by hand.** Restore into a new cluster (`bootstrap.recovery`)
  rather than download a dump. If a dump is ever needed, encrypt it as it is
  made, for example `pg_dump ... | age -r <recipient>`, and delete it once
  used.

Before a deploy that migrates a database holding people's data, such as the
test environment once volunteers use it, take an on-demand backup
(`kubectl cnpg backup <cluster>`) and wait for it to complete. A migration
that goes wrong is undone by restoring that backup into a new cluster, not
by downgrading.

### If a backup leaks

1. Rotate what the backup lets someone use straight away: every database
   role's password (their hashes are in it), then roll out.
2. If the `TOTP_KEYS_DIR` Secret may have leaked with it, rotate the TOTP key
   and reseal (TOTP keys, below), and every administrator replaces their
   authenticator.
3. Every administrator, from their account page, changes their password
   (argon2 hashes are slow, but can be guessed offline) and makes new
   recovery codes. Their old codes' hashes cannot be guessed back, but new
   ones cost nothing. Session, device and reset-link tokens are stored only
   as hashes of random values, so nobody signs in or registers with them.
4. Tell the people whose personal data it held: administrators, and the
   volunteers named in it.

## The first administrator

Administrators do everything else in the browser, so the first one sets up
there too. Make a token, set it as `SETUP_TOKEN` and restart:

```sh
uv run python -c "import secrets; print(secrets.token_urlsafe())"
```

Give it to the first administrator, who opens `/setup`, types it, and
chooses a username, a password and an authenticator app. Once any
administrator exists, `/setup` is gone for good; remove `SETUP_TOKEN` and
restart. Later administrators are invited from the administrator pages
(docs/decisions/0012).

## A locked-out administrator

Another administrator can reset a locked-out one's sign-in, which shows a
link to pass on, and one with an email can reset their own password. When
neither can, make a token as above, set it as `RECOVERY_TOKEN` and restart.
On Kubernetes, put it in the deployment's Secret and roll out; nobody needs
to exec into a pod.

Give it to them. They open `/recover`, type it and their username, and
choose a new password. Their authenticators are removed: while TOTP is
required they set up a new one at sign-in; while it is optional, until they
add one, the password is all that protects the account. Each token works
once, so make a new one for another try. `/recover` exists only
while `RECOVERY_TOKEN` is set: remove it and restart afterwards. Anyone who
can read the token's Secret can already read `DATABASE_URL`, so it trusts
nobody new.

## TOTP keys

Each authenticator's key is stored sealed (AES-GCM), so a copy of the
database or a backup cannot make anyone's codes (docs/decisions/0017). The
keys that seal them live outside the database, in `TOTP_KEYS_DIR`: one file
per version, named `1`, `2` and so on, each holding a key. The highest
version seals; every version present opens.

Make a key and put it in a Secret, mounted as a folder into the app's pods
only:

```sh
uv run python -m inventory_tng.totp_keys new > 1
kubectl create secret generic inventory-totp-keys --from-file=1
```

Back the Secret up apart from the database's backups, and keep it as
carefully: the two together can make codes.

**Rotating.** Add the next version to the Secret, roll out, then seal every
authenticator with it:

```sh
kubectl exec deploy/inventory -- python -m inventory_tng.totp_keys reseal
```

It says how many it resealed and how many it could not read. Once none is
left on the old version, remove that version from the Secret and roll out
again.

**If a key is lost.** The app starts with whatever versions are present, and
logs at startup how many authenticators it cannot read. Those take no code,
and each one's administrator still signs in with their password and a
recovery code, or a reset link (A locked-out administrator, above), then
removes it and adds a new one on their account page. A key that may have
leaked is rotated as above.

## Mail

The app mails administrators reset links they ask for. It tells them when
they are invited, when another administrator resets their sign-in or
brings their account back, when the recovery token is used for them, and
when their password changes or a recovery code is used. It sends through a
provider's authenticated SMTP, such as Mailgun's or Gmail's:

- `MAIL_HOST`, such as `smtp.mailgun.org`.
- `MAIL_FROM`: the sender, such as `inventory@nycmesh.net`. Its domain needs
  the SPF, DKIM and DMARC records the provider asks for, or mail lands in
  spam.
- `MAIL_USERNAME`, `MAIL_PASSWORD`: the provider's SMTP login. Gmail needs an
  app password, on an account with 2-step verification.
- `MAIL_SECURITY`: `starttls` (the default, port 587) or `tls` (port 465).
- `MAIL_PORT`: only if the provider uses another.
- `SITE_URL`: where people reach the site, over https, such as
  `https://inventory.nycmesh.net`. Links in mail start with it, never with
  what a request says its host is.

Mail is sent after each reply, on port 587 or 465: check the host lets
those out. At most three reset links an hour go to any one account. A
failure is logged as `mail not sent` and counted in `inventory.mail.sent`;
nobody is shown it.

## Registering devices

Every phone or computer that records anything must be registered.
Administrators make registration codes, and revoke codes and devices, on the
Devices page. Links in its QR codes start with `SITE_URL` when mail is set
up; otherwise with the address the administrator reached the site at, so
behind a proxy set `FORWARDED_ALLOW_IPS`, or the link may say http.

If no administrator can, the operator can make one on the server:
`uv run python -m inventory_tng.registration_codes create --uses 5 --days 1
--base-url https://<your host>` (`--help` lists the rest).

## Scan page settings

Administrators set how the scan page behaves on the Settings page; the
settings live in the database (docs/decisions/0011). If no administrator
can, the operator can on the server: `uv run python -m
inventory_tng.scan_settings set --name-check-minutes 20`.
