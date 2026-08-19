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
| `CORS_ALLOWED_ORIGINS` | no | chart (`django.corsAllowedOrigins`) | Normally empty: nginx proxies Django's paths, so the browser sees one origin. Setting it grants cross-origin *reads* to an unauthenticated client and nothing more — the session cookie is not sent cross-origin and writes have no trusted-origin list, so it does not make a frontend on a second hostname work |
| `NUM_PROXIES` | no | chart (`django.numProxies`) | Proxies between the browser and Django; the default `2` matches the deployed chain of ingress then the frontend's nginx. It decides whose request a rate limit counts against, so it must match reality — see [`.env.sample`](../.env.sample) for which direction is dangerous |
| `APPEND_BURST_RATE` | no | chart (`django.appendBurstRate`) | How fast one client may append. What each rate is for, and why the defaults are what they are, is in [`.env.sample`](../.env.sample) |
| `APPEND_SUSTAINED_RATE` | no | chart (`django.appendSustainedRate`) | The same limit over an hour, for a flood paced to stay under the burst rate |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | no | provider Secret | Offers Google sign-in. Absent, or half set, means it is not offered |
| `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | no | provider Secret | Offers Slack sign-in, the strongest signal that somebody is actually involved in NYC Mesh |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_SERVER_URL` | no | provider Secret | Offers a generic OpenID Connect provider. `OIDC_SERVER_URL` is the issuer, the URL whose `/.well-known/openid-configuration` describes the rest. All three are needed |
| `OIDC_NAME` | no | provider Secret | What the button for that provider says. Default `Single sign-on` |
| `OIDC_PROVIDER_ID` | no | provider Secret | Appears in that provider's callback URL, so it must match what was registered with it. Default `oidc` |
| `LABEL_BASE_URL` | no | chart (`django.labelBaseUrl`) | The origin encoded into every printed QR code. It is on the stickers, not in the database, so changing it does not change the labels already on the shelves: move the app and keep a permanent redirect from the old host rather than reprinting. It must stay within what QR alphanumeric mode can carry and short enough to print at the module size the generator insists on — both are refused loudly rather than printed, see [`.env.sample`](../.env.sample) |

Both rates are counted **per backend process**, because the counters live in
Django's default in-memory cache. Three gunicorn workers per pod means a client
can append three times the configured rate, multiplied again by the replica
count, so set the rate for one process and expect the deployment to allow more.
Making it exact would take a cache shared between processes, which is not
configured today — a bound that is loose is still a bound, and what it defends
is [decision 0012](decisions/0012-two-populations.md).

None of the provider variables grants anybody anything.
[Decision 0013](decisions/0013-administrator-sign-in.md) point 5 is the rule:
a new account arriving from any provider holds no permissions until an existing
administrator grants it the staff flag, in the admin. Configuring a provider
adds a way to prove who you are and nothing else — and the local username and
password path is retained whatever else is set, because it is the way in when
a provider is unreachable or an account is lost.

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

### The provider Secret

A second Secret, named by `django.providerSecret` and **optional**. Every key
in it becomes a backend environment variable, so which sign-in providers a
deployment offers is what is in this Secret rather than anything in the chart
or the image. Create it only if you are configuring one:

```bash
kubectl create secret generic inventory-tng-providers \
  --namespace inventory-tng \
  --from-literal=GOOGLE_CLIENT_ID='...' \
  --from-literal=GOOGLE_CLIENT_SECRET='...'
```

If it does not exist the deployment starts anyway and offers the local
username-and-password path. Adding a provider later is `kubectl create secret`
and a restart of the backend pods, not a release.

Register the redirect URI with each provider as this deployment's origin
followed by `/accounts/<provider>/login/callback/` — for the generic OpenID
Connect one, `/accounts/oidc/<OIDC_PROVIDER_ID>/login/callback/`.

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

**TLS is not optional**, and the chart offers no switch to turn it off. QR
scanning stops working over plain HTTP, including on a LAN address, and it
fails silently rather than warning — why is in
[decision 0011](decisions/0011-qr-batch-scanning.md#consequences). Rendering
the chart without a certificate fails rather than quietly producing an ingress
nobody can scan from — see below for supplying one.

If something in front of the ingress terminates TLS — a load balancer, a
service mesh — set `ingress.tls.terminatedElsewhere=true`. That stops the
Ingress asking for a certificate; it does not make plain HTTP to the browser
supported, because the camera still needs a secure context.

### Administrative access

[Decision 0013](decisions/0013-administrator-sign-in.md) restricts the
administrative routes — `/admin`, `/accounts`, and the administrative
operations of the API — to a network volunteers do not need: the mesh, a VPN,
or an identity-aware proxy. Administrators are few and their locations
predictable, so this costs them very little, and it is the one place a network
boundary fits without excluding a volunteer on a phone wherever the stock
happens to be.

Express it at the ingress or in front of it. Nothing in the application can
detect its absence, so it is a precondition somebody has to honour rather than
a check that fires — the same shape as the requirement below.

**The ingress must be the only route to the frontend pod.** TLS terminates
there, so it is the ingress that tells Django which scheme the browser used,
via `X-Forwarded-Proto`; nginx in the frontend image forwards that header on
and Django trusts it (`SECURE_PROXY_SSL_HEADER`). That single header decides
whether HSTS is sent, whether session and CSRF cookies are marked secure, and
whether a write passes the CSRF origin check. Anything that can reach the pod
without passing through the ingress — an in-cluster client, a
`kubectl port-forward` — can therefore claim `https` and be believed. Keep the
frontend Service internal and let the ingress overwrite the header, which
ingress-nginx does by default.

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

That account signs in at `/accounts/login/` — the Django admin's own login form
redirects there — and is asked to set up an authenticator app before it can
reach anything else, which is
[decision 0013](decisions/0013-administrator-sign-in.md) point 3. Everybody
after them signs in however they like and is granted the staff flag by this
account, in the admin under **Users**; that grant is the only thing that makes
an administrator, and no provider can do it.

**Flush the sessions on the release that first brings sign-in in.** The second
factor is required of sessions allauth itself created — see `RequireSecondFactor`
in `backend/src/inventory/middleware.py` — so anybody already signed in through
the Django admin's old login form keeps a password-only session for as long as
it lives. Once, against the deployed database:

```bash
python manage.py clearsessions --all 2>/dev/null || \
  python -c "import django;django.setup();from django.contrib.sessions.models import Session;Session.objects.all().delete()"
```

Everybody signs in again, this time through the door that asks for a code.

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
