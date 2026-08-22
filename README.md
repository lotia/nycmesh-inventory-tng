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
| Run this locally and make changes | [DEVELOPERS.md](DEVELOPERS.md) |
| Contribute as a volunteer | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Understand how it is put together | [docs/architecture.md](docs/architecture.md) |
| Deploy it | [docs/deployment.md](docs/deployment.md) |
| Know why something was built this way | [docs/decisions/](docs/decisions/) |
| Work on this with an AI coding agent | [AGENTS.md](AGENTS.md) |

## Quickstart

You need [Docker](https://docs.docker.com/get-started/get-docker/) and
[mise](https://mise.jdx.dev/getting-started.html).

```bash
git clone git@github.com:lotia/nycmesh-inventory-tng.git
cd nycmesh-inventory-tng
cp .env.sample .env
docker compose up --build
```

That brings up PostgreSQL, the Django API, and the frontend together:

| Service | URL |
| --- | --- |
| Frontend | <http://localhost:8080> |
| API health check | <http://localhost:8000/api/healthz> |
| API docs (OpenAPI) | <http://localhost:8000/api/docs> |
| Sign in | <http://localhost:8000/accounts/login/> |
| Django admin | <http://localhost:8000/admin/> |

That database starts empty, so every one of those pages is a blank list. To put
an invented catalogue, a warehouse, two volunteers and some stock in it:

```bash
docker compose exec backend python manage.py seed_demo_data
```

To create an admin login:

```bash
docker compose exec backend python manage.py createsuperuser
```

Signing in with it the first time asks you to set up an authenticator app: a
password on its own is not a way into this system, and how administrators sign
in is
[decision 0013](docs/decisions/0013-administrator-sign-in.md).

For the full development environment — running the services outside Docker, tests,
linting, and day-to-day commands — see [DEVELOPERS.md](DEVELOPERS.md).

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
