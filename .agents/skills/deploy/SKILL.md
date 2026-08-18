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

- **Migrations run only in the Helm pre-upgrade Job**, never from a web
  container. Multiple backend replicas would otherwise race on schema changes.
- **Nothing environment-specific is baked into the frontend bundle.** nginx
  resolves `BACKEND_ORIGIN` at container start. Preserve this.
- **Both images run as non-root**, and the backend runs with a read-only root
  filesystem. Anything needing to write needs an explicit volume.
- **The chart does not deploy PostgreSQL.** The database is managed outside the
  application's release cycle, on purpose.

## Verifying

`helm lint` and `helm template` both run without a cluster — use them:

```bash
helm lint infra/helm/inventory-tng
helm template test infra/helm/inventory-tng --set image.tag=v0.1.0
```

CodeNOW consumes the same Dockerfiles and the same chart, so there is no
separate deployment path to test.
