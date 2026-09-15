# Local development

[The guide](../DEVELOPERS.md) is the shortest route from a clean machine to a
running application, and stops there. This page is the rest of running one on
your own machine: the two other ways to run it, the camera from a phone,
signing in, and what to do when something does not start.

## Running it, one way at a time

The guide runs everything in Docker, which is the quickstart. The two ways
below are for when you are editing code and want the servers to reload, and
for when you want nothing at all installed on your host; the bootstrap script
the guide has you run prepares the first of them.

**Run one way at a time.** The Docker stack and the native servers both put
Django on port 8000 — one in a container, one on your machine — so whichever
starts second fails to bind. `docker compose down` before starting the native
servers, or stop those before bringing the stack up. PostgreSQL is not a clash:
both use the same compose service, and starting it twice starts it once.

## Option B — native, with only PostgreSQL in Docker

Best for day-to-day development: both servers hot-reload.

```bash
docker compose up -d postgres     # database only
```

Then, in one terminal:

```bash
cd backend
uv sync                                    # create .venv from uv.lock
uv run python src/manage.py migrate
uv run python src/manage.py runserver      # http://localhost:8000
```

And in another:

```bash
cd frontend
npm install
npm run dev                                # http://localhost:5173
```

The Vite dev server proxies Django's paths
([which ones](architecture.md#shape)), so the browser talks to a single
origin and you will not hit CORS locally.

## Option C — devcontainer

[`.devcontainer/devcontainer.json`](../.devcontainer/devcontainer.json) gives you
the toolchain with nothing installed on your host. Open the repo in VS Code and
choose *Reopen in Container*, or run `devcontainer up --workspace-folder .`.

## Using the camera from a phone

Scanning a code at a shelf is what this application is for, and the camera is
the one feature you cannot exercise on your own machine in the way a volunteer
will. This is how to get it working against a checkout, and it is opt-in: if
you are not touching the scanner you never need any of it, and `compose up`
behaves exactly as [the guide](../DEVELOPERS.md#running-it) describes.

**One command, after finding your machine's address on the network:**

```bash
ip addr                          # Linux
ipconfig getifaddr en0           # macOS, on wi-fi

TLS_HOST=<that address> podman compose --profile tls up -d --build
```

Then open `https://<that address>:8443` on the phone. Note the **s** and the
port: this is a second way in, beside the plain `:8080` one, and only this one
gives the camera.

Put `TLS_HOST` in your `.env` to stop typing it. Two other settings matter and
are described in [`.env.sample`](../.env.sample): `DJANGO_ALLOWED_HOSTS` needs your
address adding or Django answers `400 DisallowedHost`, and `NUM_PROXIES` should
be `2` while this profile is up, because it adds a second proxy in front of the
one the frontend already runs.

**Your browser will warn you, and that is the expected outcome.** Nothing has
gone wrong. The certificate was made by a container on your own machine a
moment ago, for an address you typed yourself, and no browser has any reason to
trust it. Decision 0028 chose this deliberately: the alternative is a
per-device trust ritual, and the audience for a local stack is developers rather
than volunteers.

What you will see, and what to press:

| Where | What it says | What to press |
| --- | --- | --- |
| Safari, iOS and macOS | **This Connection Is Not Private** | **Show Details**, then **visit this website**, then **Visit Website** |
| Chrome and Edge | **Your connection is not private**, `NET::ERR_CERT_AUTHORITY_INVALID` | **Advanced**, then **Proceed to … (unsafe)** |
| Firefox | **Warning: Potential Security Risk Ahead** | **Advanced…**, then **Accept the Risk and Continue** |

You are asked once per device, and the certificate is kept in a volume, so
`compose down` and up again does not ask you a second time. It is regenerated —
and you are asked again — when your machine's address changes or the
certificate is close to expiring, and the container says so in its log when
that happens.

If the warning is not one of the above and offers you no way through, the
certificate does not cover the address you dialled: check that `TLS_HOST` is
what is in the browser's bar. That is the failure
`infra/tls/certificate.sh` regenerates to avoid, and it says why there.

**If the phone cannot connect at all**, the stack can be perfectly healthy and
still be unreachable, because the request now arrives from another machine for
the first time. In order of how
often it is the answer:

- **A firewall on your own machine.** This is the common one, and it is easy to
  misdiagnose because `curl` from the machine itself still works — that traffic
  goes over the loopback interface and never meets the rules. Allow the port
  from your own network rather than from everywhere: on `ufw` that is
  `sudo ufw allow from <your network>/24 to any port 8443 proto tcp`. A blanket
  `allow 8443` opens a stack holding seeded data on every network you ever join.
- **The phone is not on the same network.** Cellular, or a guest SSID that is
  not the one this machine is on.
- **Client isolation on the access point.** Common on guest and some ISP
  networks; it blocks device-to-device traffic outright, so nothing you change
  here will help. The tell is that plain `http://<address>:8080` fails too. A
  tunnel is the way through, and decision 0028 point 7 says why it is the
  documented alternative rather than the default.

To tell a TLS problem from a network one, try `http://<address>:8080` first —
plain HTTP, no certificate in the way. If that fails as well, the problem is
not TLS.

**Somebody who wants no warning at all is not served yet, and that is worth
saying plainly.** Decision 0028 keeps a local certificate authority as the
route for anybody demonstrating the app to people who should not be taught to
dismiss security dialogs. Nothing here implements it: there is no flag and no
second profile, and the only way to get such a certificate today is to type
`openssl` commands, which is exactly what this is meant to spare you. It is
filed rather than hidden — until it exists, the choices are to accept the
warning above, or to use a tunnel, which decision 0028 point 7 describes and
which gives a publicly-trusted name at the price of an account and the traffic
leaving your machine.

## Signing in

Everybody signs in, for now. These answer without a session — the index
`/api`, the two probes `/api/healthz` and `/api/livez`, `/api/me`, the two
credential-free reports `/api/client-failures` and `/api/debug-trace`, and the
API's own description at `/api/schema` and `/api/docs` — and every other one
needs one, the two a volunteer writes to included. The list is deliberately
not a count: it was written as one and was wrong twice running. It is also a
convenience rather than the authority — that is an audit in
`backend/src/inventory/tests/test_capabilities.py`, which asks of every
endpoint and every method whether an anonymous request would be admitted, and
holds the answer against a list carrying the argument for each entry. Whether
an endpoint may join them at all is
[decision 0012](decisions/0012-two-populations.md). So make an
account before you expect either half of the app to answer, and sign in at
`/accounts/login/` on whichever address you are using — that page and the rest
of `/accounts` answer anybody, because a sign-in form has to. `/api/schema`
and `/api/docs` are
readable by anyone who can reach the port, which is why a deployment puts a
network boundary in front of
them: [which paths are restricted](deployment.md#which-paths-are-restricted).

That is a gap rather than the design.
[Decision 0012](decisions/0012-two-populations.md) settles that the two
populations are told apart by what they may do and not by a credential, and
[what is not built yet](architecture.md#not-yet-built) is where the
distance between that and today is recorded.

**A local checkout does not ask for a second factor**, and that is a choice
this repository made rather than the code's own default.
`REQUIRE_SECOND_FACTOR` is `false` in both `.env.sample` and `compose.yaml`, so
a password is the whole of signing in here; a deployment that configures
nothing gets the opposite. It is an operator's decision in every environment,
and the argument is the amendment on
[decision 0013](decisions/0013-administrator-sign-in.md#amendment-2026-08-30--the-requirement-is-a-default-not-a-rule).

Turn it on in your `.env` when you are working on the sign-in flow itself —
enrolment, the TOTP challenge, the reauthentication prompt — because with it
off none of those is on the path anybody walks. Then the first sign-in of a new
account stops and asks for an authenticator app before it reaches anything, so
have a phone or any other TOTP application ready.

Which providers a deployment offers besides the local one is configuration; the
variables are in [deployment](deployment.md#environment-variables).

**Enrol once, not once per database.** Wiping the database is an ordinary thing
to do while developing, and with the requirement on it takes the account and
its authenticator with it. Rather than enrolling again each time, use the login
the integration suite's seed makes — the command is in
[Common tasks](working-in-the-code.md#common-tasks), and what its acknowledgement flag is for is
[Integration tests](testing.md#integration-tests).

The part worth knowing here is that **its TOTP secret is fixed rather than
generated**. Scan it into a phone once and the same codes keep working after
every `down -v`, because each run writes the same secret back. Running it again
replaces the login rather than duplicating it.

Never give a deployment this account.

## If you had this stack running before August 2026

Run `docker compose down -v` once. The database volume now mounts
`/var/lib/postgresql` rather than `/var/lib/postgresql/data`, because postgres
18 keeps its data in a major-version subdirectory. An older volume is not
migrated: postgres would silently start an empty cluster beside it, and you
would wonder where your local data went.

## Troubleshooting

**`mise: command not found`, or `uv`/`npm`/`helm: command not found`.** You have
installed mise but not activated it, which is by far the commonest way a first
run stops. [Activate mise, then open a new shell](../DEVELOPERS.md#activate-mise-then-open-a-new-shell)
is the fix, and it is one line plus a new terminal.

**`django.core.exceptions.ImproperlyConfigured: Set the DJANGO_SECRET_KEY
environment variable`.** You have no `.env`. Run `cp .env.sample .env`. The
settings module deliberately has no fallback secret, so a misconfigured
deployment fails at boot instead of running with a known-public key.

**`connection refused` on the database port.** PostgreSQL is not running.
Start it with `docker compose up -d postgres`.

**Frontend loads but every API call 404s.** You are on the Vite dev server
(port 5173) with no backend running. Start Django, or use
<http://localhost:8080> from the Docker stack.

**`Bind for 0.0.0.0:5432 failed: port is already allocated`**, or
`address already in use`. A PostgreSQL of somebody else's — a system service, or
another project's stack — already holds the port this one wants to publish, and
that is the ordinary state of a laptop that has done any database work before.
Either stop theirs, or move ours, which takes **two** settings changed
together:

```bash
POSTGRES_PORT=5433
DATABASE_URL=postgres://inventory:inventory@localhost:5433/inventory_tng
```

Put both in `.env`. `POSTGRES_PORT` is what
[`compose.yaml`](../compose.yaml) publishes the container on and `DATABASE_URL` is
where Django looks, so changing one alone points your Django at the stranger's
cluster — it will connect, fail to authenticate or migrate a database that is
not yours, and none of the errors will say why.
[`.env.sample`](../.env.sample) carries the same pairing.

**Port 8000, 8080 or 5173 already in use.** Two of the three ways to
[run it](../DEVELOPERS.md#running-it) are up at once; bring one down. Failing that, something
else on the machine holds the port and has to be stopped — those three are not
configurable here, because both dev servers and the guides' addresses assume
them.

