# Decision records

Short notes on choices that a future reader would otherwise have to reverse
engineer — particularly the places where this project deliberately diverges from
[MeshDB](https://github.com/nycmeshnet/meshdb), whose architecture it otherwise
mirrors.

Add one when a decision is not obvious from the code, and would prompt someone
to ask "why is it like this?". Number them sequentially.

| # | Decision |
| --- | --- |
| [0001](0001-monorepo.md) | Backend and frontend in one repository |
| [0002](0002-frontend-stack.md) | Vite + React SPA instead of meshforms |
| [0003](0003-django-version.md) | Django 6.1 rather than 5.2 LTS |
| [0004](0004-python-tooling.md) | Astral toolchain: uv, ruff, ty |
| [0005](0005-psycopg3.md) | psycopg 3 instead of psycopg2 |
| [0006](0006-frontend-tooling.md) | Biome instead of ESLint + Prettier |
| [0007](0007-test-coverage.md) | Coverage thresholds enforced in the test command |
| [0008](0008-stock-ledger-transfer-graph.md) | Stock as a double-entry transfer graph |
| [0009](0009-type-annotations-required.md) | Type annotations required, `Any` permitted |
| [0010](0010-openapi-version.md) | OpenAPI 3.1.1, kept current by a test |
