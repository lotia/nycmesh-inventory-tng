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
| [0011](0011-qr-batch-scanning.md) | One cart, one transaction: QR scanning and the batch endpoint |
| [0012](0012-two-populations.md) | Volunteers append without signing in; administrators sign in |
| [0013](0013-administrator-sign-in.md) | Several ways for an administrator to sign in, one way to become one |
| [0014](0014-one-interface.md) | Administrator powers appear in the volunteer app, not a second one |
| [0015](0015-merged-identifier-conflict.md) | A taken identifier that nobody can see is a 409 naming who holds it |
| [0016](0016-invariants-for-every-writer.md) | Invariants belong to every writer; affordances belong to the API |
| [0017](0017-review-through-pull-requests.md) | Review happens in the pull request, not in the history |
| [0018](0018-occurred-at-is-the-server-clock.md) | `occurred_at` is the server's clock, not the client's |
