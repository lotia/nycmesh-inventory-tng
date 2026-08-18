# NYC Mesh Inventory (inventory-tng)

Inventory tracking for [NYC Mesh](https://www.nycmesh.net/) — a PostgreSQL-backed
replacement for the Google Forms + Google Sheets system currently in use.

## Why this exists

The current system is a Google Form writing into a Google Sheet, searched with
full-text search and regular expressions. It has passed 15,000 rows and is now
very slow. Its QR-code entry point — scan a code, act on that item — is genuinely
good and heavily used, so **QR scanning is a requirement here, not a nice-to-have**.

This project keeps everything the current system does and adds what a spreadsheet
cannot give us:

- **Scanning several QR codes into a single transaction.** The current system
  handles one item per scan; batching is the most-requested missing feature.
- **Room to grow.** A real relational schema instead of columns in a sheet.
- **Scale.** Indexed queries rather than a linear scan over 15,000+ rows.

## Status

**Early setup.** The repository skeleton, tooling, and deployment path are in
place. The inventory data model and the QR flow are still being designed, so
there are no inventory features to use yet.

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
| Django admin | <http://localhost:8000/admin/> |

To create an admin login:

```bash
docker compose exec backend python manage.py createsuperuser
```

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
