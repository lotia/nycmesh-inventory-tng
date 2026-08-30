# NYC Mesh Inventory (inventory-tng)

Inventory tracking for [NYC Mesh](https://www.nycmesh.net/) — a PostgreSQL-backed
replacement for the Google Forms + Google Sheets system currently in use.

## Why this exists

The current system is a Google Form writing into a Google Sheet. Its QR-code
entry point — scan a code, act on that item — is genuinely good and heavily
used, so **QR scanning is a requirement here, not a nice-to-have**.

What fails is not its size — the sheet is small. The problems are structural:

- **It breaks on typing.** Items are matched by their display name, so typos and
  informal names silently match nothing, and those movements never reach a count.
- **One item per scan.** Most entries were part of a burst by one person filling
  the same form over and over. **Batching is the most-requested missing
  feature.**
- **Nobody can correct it properly.** A large share of entries are corrections
  dressed as movements — [decision 0008](docs/decisions/0008-stock-ledger-transfer-graph.md)
  says what the sheet gives volunteers instead, and
  [the classifiers brief](docs/briefs/sheet-classifiers.md) says how large a
  share, and under which rule.
- **It lives in a personal Google account**, so it cannot be edited by the
  people who depend on it and could be lost.

Each of these is measured rather than assumed. The counts live once, in
[decision 0008](docs/decisions/0008-stock-ledger-transfer-graph.md#context).
Anything that needs a rule before it can be counted — which notes are
corrections, which name a place — lives with that rule in
[the sheet classifiers brief](docs/briefs/sheet-classifiers.md).

## Status

**Early.** The repository skeleton, tooling, and deployment path are in place,
and the inventory data model is implemented
([docs/data-model.md](docs/data-model.md)). The QR flow
([decision 0011](docs/decisions/0011-qr-batch-scanning.md)) is built end to
end — printable labels, a camera that decodes them, the cart that holds a
batch, and the Save that writes it — behind a sign-in. The screens an
administrator needs, and the one thing the scanner still does over the network
rather than from a cache, are what is missing. What exists and what does not
are listed in [architecture.md](docs/architecture.md#not-yet-built).

## Documentation

Each topic is documented in exactly one place. Start with whichever fits you:

| If you want to… | Read |
| --- | --- |
| Move stock in or out | [guides/volunteer.md](guides/volunteer.md) |
| Keep the catalogue, the people and the labels | [guides/administrator.md](guides/administrator.md) |
| Try the app out, by following a session | [guides/worked-examples.md](guides/worked-examples.md) |
| Run this locally and make changes | [DEVELOPERS.md](DEVELOPERS.md) |
| Contribute as a volunteer | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Understand how it is put together | [docs/architecture.md](docs/architecture.md) |
| Deploy it | [docs/deployment.md](docs/deployment.md) |
| See what it is doing, and decide where that goes | [docs/observability.md](docs/observability.md) |
| Know why something was built this way | [docs/decisions/](docs/decisions/) |
| Work on this with an AI coding agent | [AGENTS.md](AGENTS.md) |

## Quickstart

You need **either [Docker](https://docs.docker.com/get-started/get-docker/) or
[Podman](https://podman.io/)** and nothing else — every toolchain this uses is
inside the images. The two are interchangeable throughout this project, and
every command below is the same word for word apart from the one it starts
with; what Podman asks for that Docker does not is
[Podman](DEVELOPERS.md#podman). Cloning needs no GitHub account either; the URL
below is the anonymous one.

### Start it

```bash
git clone https://github.com/lotia/nycmesh-inventory-tng.git
cd nycmesh-inventory-tng
cp .env.sample .env
```

Then, with Docker:

```bash
docker compose up --build -d
```

Or with Podman:

```bash
systemctl --user start podman.socket
podman compose up --build -d
```

That first line is the one thing Podman needs and Docker does not.
[Podman](DEVELOPERS.md#podman) says why, and how to stop typing it.

Keep `--build`. Without it a stale image from an earlier checkout is reused,
and the failure that follows names a missing file rather than the real cause.

`-d` puts the three services in the background so that the terminal stays
yours for the two commands below. `docker compose logs -f` (or `podman compose
logs -f`) follows them, and `down` in place of `up` stops them again.

### Put something in it

**Everything but the two probes and the API description needs an account**,
and the database starts empty, so these are not optional extras — without them
every page below is a blank list or a redirect to the sign-in form. With
Docker:

```bash
docker compose exec backend python manage.py seed_demo_data    # something to look at
docker compose exec backend python manage.py createsuperuser   # your login
```

Or with Podman:

```bash
podman compose exec backend python manage.py seed_demo_data    # something to look at
podman compose exec backend python manage.py createsuperuser   # your login
```

The first of those prints two label codes; they are the stickers to type into
the scanner.

### Sign in for the first time

Everything is reachable on **<http://localhost:8080>**, which is the one to
open: it serves the app and forwards the API, the admin and the sign-in pages
to Django, so signing in there returns you to the app afterwards.

Go to <http://localhost:8080/accounts/login/> and use the account
`createsuperuser` just made. The password is all it asks for here, because this
stack ships with `REQUIRE_SECOND_FACTOR=false` — a deployment defaults to the
opposite, and how administrators sign in is
[decision 0013](docs/decisions/0013-administrator-sign-in.md).
[Signing in](DEVELOPERS.md#signing-in) is how to turn it on locally and when
you would want to.

Once you are in:

| Service | URL | Needs an account |
| --- | --- | --- |
| Frontend — start here | <http://localhost:8080> | yes |
| Sign in | <http://localhost:8080/accounts/login/> | — |
| Django admin | <http://localhost:8080/admin/> | yes |
| API docs (OpenAPI) | <http://localhost:8080/api/docs> | no |
| API readiness probe | <http://localhost:8080/api/healthz> | no |
| API liveness probe | <http://localhost:8080/api/livez> | no |

Django is also published directly on <http://localhost:8000> for the times you
want to reach it without nginx in the way. Prefer port 8080: port 8000 has no
page at `/`, so signing in there lands on a 404.

This stack is one of three ways to run the project, and the other two are set
up differently: see [running it](DEVELOPERS.md#running-it). They cannot both
have port 8000, so bring this one `down` before following that guide's own
setup. For the full development environment — tests, linting, and day-to-day
commands — see [DEVELOPERS.md](DEVELOPERS.md).

## Deployment

The same two container images run everywhere. They deploy to standard Kubernetes
(NYC Mesh runs k3s on [Proxmox](https://www.proxmox.com/en/)) and to
[CodeNOW](https://www.codenow.com/) using one Helm chart, in
[`infra/helm/inventory-tng`](infra/helm/inventory-tng).

Full procedure, environment variables, and secrets:
[docs/deployment.md](docs/deployment.md).

## Issue tracking

Day-to-day work is tracked with [beads](https://github.com/steveyegge/beads)
(`bd ready` to see what is available). Volunteers are welcome to use GitHub
issues instead — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). NYC Mesh is a volunteer community network; this is meant to be
reusable by other community networks with the same problem.
