# 0010 — OpenAPI 3.1.1, kept current by a test

**Status:** accepted

## Context

The backend must publish a machine-readable API description, and it must stay
in step with the code. Both halves matter, and the second is the hard one: a
generated file that is committed and then forgotten describes an API that no
longer exists, which is worse than publishing nothing, because consumers
believe it.

`drf-spectacular` was already a dependency and already served `/api/schema`,
but nothing pinned the output format and nothing noticed when it drifted.

On the format itself, `drf-spectacular` can emit 3.0, 3.1 or 3.2:

- **3.0.3** is its default and is what most tooling was built for, but its
  schema dialect is a JSON Schema variant rather than JSON Schema itself.
- **3.1** aligns the dialect with JSON Schema 2020-12, which is the substantive
  modernisation, and is supported across the tooling ecosystem.
- **3.2** is newer still. Support in validators, documentation renderers and
  code generators lags a released specification by a long way.

## Decision

**Pin `OAS_VERSION` to `3.1.1`**, and make schema currency a test rather than a
convention.

1. The generated document is committed at `backend/openapi.yaml`, so anyone can
   read the API without cloning and running the project.
2. `backend/src/inventory/tests/test_api_schema.py` regenerates the schema and
   fails if the committed file differs. It runs inside the ordinary
   `uv run pytest`, so it fires locally and in CI identically — the same shape
   as the coverage threshold in [decision 0007](0007-test-coverage.md). No
   separate CI step exists to be skipped or to disagree with a local run.
3. A further test asserts that every operation documents a response body with a
   schema, so the spec cannot degrade into a list of paths.
4. `/api` returns an index of the available endpoints, so the API can be
   explored by fetching it rather than by reading the URL configuration.

3.2 is one settings value away when its tooling catches up, and the version
assertion in the tests is the thing that will make the change deliberate.

## Consequences

- Changing an endpoint or a payload without regenerating the schema fails the
  test run. The failure message carries the exact command to fix it.
- Consumers get a stable, readable artefact in the repository, and a rendered
  version at `/api/docs`.
- Choosing 3.1 over 3.0 means any consumer stuck on strictly-3.0 tooling has to
  upgrade. Given that no consumers exist yet, this is the cheapest moment to
  take that on, and 3.0's dialect divergence from JSON Schema is exactly the
  problem that would bite later.
- The committed file appears in every diff that changes the API surface. That
  is intended: an API change should be visible in review.
