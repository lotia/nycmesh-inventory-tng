---
name: deploy
description: Use when working on inventory-tng container images, the Helm chart in infra/helm/, Kubernetes manifests, CodeNOW deployment, or CI workflows that build and publish images.
---

# Deployment work

The full procedure, environment variables, secrets, and CodeNOW setup are
documented once, in
[docs/deployment.md](../../../docs/deployment.md). Read it first and do not
restate any of it elsewhere.

## What to keep in step

Deployment configuration spans four files. A change to one usually needs the
others, and CI will not catch a mismatch:

| Change | Also update |
| --- | --- |
| New backend environment variable | `.env.sample`, `compose.yaml`, `infra/helm/inventory-tng/values.yaml`, the `_helpers.tpl` env block, and the variable table in `docs/deployment.md` |
| New service or port | `compose.yaml`, chart templates, `docs/architecture.md` |
| Changed image entrypoint or command | Both the Dockerfile and the chart |

## Constraints that are load-bearing

These properties of the deployment are deliberate, and a change to the chart or
the images has to preserve them. Each is explained once, where it belongs:

- **The ingress is the only route to the frontend pod**, and the administrative
  routes are reachable only from a network volunteers do not need —
  [deployment.md](../../../docs/deployment.md#administrative-access). Neither is
  something the application can check, so the chart is where they hold or fail.

- **Migrations run only in the Helm pre-install/pre-upgrade Job**, never from a
  web container —
  [deployment.md](../../../docs/deployment.md#migrations).
- **Nothing environment-specific is baked into the frontend bundle**; nginx
  resolves `BACKEND_ORIGIN` at container start —
  [architecture.md](../../../docs/architecture.md#shape),
  [deployment.md](../../../docs/deployment.md#environment-variables).
- **Both images run as non-root** and the backend's root filesystem is read-only
  ([deployment.md](../../../docs/deployment.md#artifacts)), so anything needing
  to write needs an explicit volume.
- **The chart does not deploy PostgreSQL** —
  [deployment.md](../../../docs/deployment.md#database).

## Verifying

`helm lint` and `helm template` both run without a cluster, so run them on every
chart change — the commands are in
[DEVELOPERS.md](../../../DEVELOPERS.md#deployment-chart). They are the whole
test: CodeNOW builds the same Dockerfiles and applies the same chart, so there
is no second deployment path to exercise
([deployment.md](../../../docs/deployment.md#deploying-to-codenow)).
