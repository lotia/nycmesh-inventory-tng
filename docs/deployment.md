# Deployment

inventory-tng deploys to standard Kubernetes and to
[CodeNOW](https://www.codenow.com/) from **one** set of artifacts: two container
images and one Helm chart. CodeNOW consumes a component's own Dockerfile and
Helm chart and deploys them to a Kubernetes cluster, so there is no second
deployment path to maintain and no CodeNOW-specific code in this repository.

For what the components are, see [architecture.md](architecture.md). For local
development — which uses `compose.yaml`, not this chart — see
[DEVELOPERS.md](../DEVELOPERS.md).

## Artifacts

| Artifact | Source | Contents |
| --- | --- | --- |
| Backend image | [`backend/Dockerfile`](../backend/Dockerfile) | Django + gunicorn, static files collected at build time |
| Frontend image | [`frontend/Dockerfile`](../frontend/Dockerfile) | Built SPA assets served by unprivileged nginx |
| Helm chart | [`infra/helm/inventory-tng`](../infra/helm/inventory-tng) | Deployments, Services, Ingress, migration Job |

Both images run as non-root. The backend runs with a read-only root filesystem.

## Environment variables

The backend reads all of these from the environment. Locally they come from
`.env` (see [`.env.sample`](../.env.sample)); in Kubernetes they come from the
chart and from a Secret.

| Variable | Required | Source in Kubernetes | Notes |
| --- | --- | --- | --- |
| `DJANGO_SECRET_KEY` | yes | Secret | No default. A missing value fails at boot by design. |
| `DATABASE_URL` | yes | Secret | `postgres://user:password@host:5432/dbname` |
| `DJANGO_DEBUG` | no | chart (`django.debug`) | Must be `false` outside development |
| `DJANGO_ALLOWED_HOSTS` | yes | chart (`django.allowedHosts`) | Comma-separated hostnames |
| `CORS_ALLOWED_ORIGINS` | no | chart | Normally empty: the frontend proxies to the backend, so production makes no cross-origin calls |

The frontend image takes one variable, `BACKEND_ORIGIN`, which the chart sets to
the backend Service. Nothing environment-specific is compiled into the
JavaScript bundle, so one image tag is valid in every environment.

## Secrets

Create the Secret before installing the chart. It is referenced by name
(`django.existingSecret`) and never rendered from `values.yaml`, so secrets stay
out of git and out of `helm get values` output.

```bash
kubectl create secret generic inventory-tng-secrets \
  --namespace inventory-tng \
  --from-literal=DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  --from-literal=DATABASE_URL='postgres://inventory:CHANGEME@postgres:5432/inventory_tng'
```

## Database

The chart does **not** deploy PostgreSQL. Point `DATABASE_URL` at a database
managed outside this chart — an operator, a managed service, or an existing
NYC Mesh instance — so that the application's release cycle never risks the data.

## Deploying to Kubernetes

NYC Mesh runs k3s on [Proxmox](https://www.proxmox.com/en/), which is standard
Kubernetes as far as this chart is concerned.

```bash
helm upgrade --install inventory-tng infra/helm/inventory-tng \
  --namespace inventory-tng --create-namespace \
  --set image.tag=v0.1.0 \
  --set ingress.host=inventory.nycmesh.net \
  --set django.allowedHosts=inventory.nycmesh.net
```

**TLS is not optional.** The chart enables it by default (`ingress.tls.enabled`)
and it must stay on: QR scanning stops working over plain HTTP, including on a
LAN address, and it fails silently rather than warning. Why is in
[decision 0011](decisions/0011-qr-batch-scanning.md#consequences).

The chart issues no certificate of its own; it references one by name
(`ingress.tls.secretName`, default `inventory-tng-tls`), so supply it before
installing — either create the Secret:

```bash
kubectl create secret tls inventory-tng-tls \
  --namespace inventory-tng --cert=fullchain.pem --key=privkey.pem
```

or, where cert-manager runs in the cluster, let it fill the same Secret in:

```bash
helm upgrade --install inventory-tng infra/helm/inventory-tng ... \
  --set ingress.annotations."cert-manager\.io/cluster-issuer"=letsencrypt-prod
```

Verify before and after:

```bash
helm lint infra/helm/inventory-tng
helm template test infra/helm/inventory-tng --set image.tag=v0.1.0   # inspect manifests
kubectl -n inventory-tng get pods
kubectl -n inventory-tng logs deploy/inventory-tng-backend
```

### Migrations

`manage.py migrate` runs as a Helm `pre-install,pre-upgrade` Job, not from a web
container. With multiple backend replicas, migrating from a starting web process
would let several replicas apply the same migration concurrently. The Job runs
once per release, before new pods roll out; if it fails, the release stops and
the old pods keep serving.

### First administrator

```bash
kubectl -n inventory-tng exec -it deploy/inventory-tng-backend -- \
  python manage.py createsuperuser
```

## Deploying to CodeNOW

NYC Mesh already has an instance at <https://nycmesh.codenow.com>, so nothing
needs standing up.

Register two components, each pointing at the artifacts already in this
repository:

| Component | Dockerfile | Build context |
| --- | --- | --- |
| `inventory-tng-backend` | `backend/Dockerfile` | `backend/` |
| `inventory-tng-frontend` | `frontend/Dockerfile` | `frontend/` |

Point the deployment configuration at `infra/helm/inventory-tng` and supply the
same values shown above. Because CodeNOW builds your Dockerfile and applies your
Helm chart, the environment variables, secrets, and migration behaviour
documented here apply unchanged.

## Health checks

`GET /api/healthz` returns `{"status": "ok"}` and executes a trivial query, so it
fails if the database is unreachable. The chart uses it for both liveness and
readiness probes on the backend.

## Rollback

```bash
helm rollback inventory-tng --namespace inventory-tng
```

This reverts the application. It does **not** revert database migrations —
write migrations to be backward compatible with the previous release, or plan
the rollback explicitly.
