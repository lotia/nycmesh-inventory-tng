# Deployment

inventory-tng deploys to standard Kubernetes and to
[CodeNOW](https://www.codenow.com/) from **one** set of artifacts: two container
images and one Helm chart. CodeNOW consumes a component's own Dockerfile and
Helm chart and deploys them to a Kubernetes cluster, so there is no second
deployment path to maintain and no CodeNOW-specific code in this repository.

For what the components are, see [architecture.md](architecture.md). For local
development — which uses `compose.yaml`, not this chart — see
[DEVELOPERS.md](../DEVELOPERS.md).

## From an empty cluster to a first sign-in

Eight steps, in this order, each linking to the section that explains it. Only
the last two are ever repeated; the rest happen once for the life of an
environment.

1. **Get a database.** This chart deploys none, for the reason
   [Database](#database) gives, so the URL of one is an input rather than an
   output.
2. **Create the Secret** holding that URL and a signing key —
   [Secrets](#secrets). Nothing starts without it, deliberately: see
   [what has no default](#what-has-no-default).
3. **Supply a certificate** in the Secret the chart names —
   [the only route to the frontend pod](#the-only-route-to-the-frontend-pod).
   There is no switch that skips this, because the camera a volunteer scans
   with does not work without it.
4. **Install the chart** — [Deploying to Kubernetes](#deploying-to-kubernetes).
   The schema is migrated as part of this and before anything serves; what that
   means when it fails is [Migrations](#migrations).
5. **Check that it came up**: `kubectl -n inventory-tng get pods`, then
   `curl https://<your host>/api/healthz`, which answers only once the database
   is reachable — [health checks](#health-checks).
6. **Make the first administrator and enrol its second factor** —
   [First administrator](#first-administrator). This is the step people are
   most often stopped by, and it is the one that has to happen before the next.
7. **Restrict the administrative routes** —
   [Administrative access](#administrative-access). After step 6, never before:
   signing in needs `/accounts`, which is one of the paths this shuts.
8. **Add a sign-in provider**, if you want one —
   [the provider Secret](#the-provider-secret). Optional, changeable later, and
   it grants nobody anything by itself.

Every `kubectl` command below names a resource as it is rendered when the
release is called `inventory-tng`, as in step 4. The chart builds those names as
`<release>-inventory-tng-<component>`, so a release under another name renames
them with it.

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

### What has no default

Two, and they are the two nothing else can supply for you.
`DJANGO_SECRET_KEY` signs every session and every password-reset link, so any
value shipped with the software would be a published key signing real sessions
— Django refuses to start rather than fall back to one. `DATABASE_URL` decides
*which* data this deployment is, and a default could only point somewhere
plausible and wrong. Both fail at boot, which is the intended behaviour and not
a rough edge: a pod that will not start is a deployment somebody fixes in the
first minute, where one running on a guessed value is found much later.

Everything else in the table below is a value the chart already carries, so a
release that supplies only those two starts. It starts answering to whatever
hostname `values.yaml` was last left naming, which is why
`django.allowedHosts` is marked required even though it is never missing.

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
| `REAUTHENTICATION_TIMEOUT_SECONDS` | no | chart (`django.reauthenticationTimeoutSeconds`) | How long a sign-in counts as recent enough to make an administrative change; after it, the API answers those writes with `reauthentication_required` and the app offers the sign-in form again. Default `900`. Why there is a second prompt inside a valid session at all is [decision 0014](decisions/0014-one-interface.md) point 5 |
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
scanning stops working over plain HTTP, including on a LAN address; why, and
what the app says to whoever hits it, is in
[decision 0011](decisions/0011-qr-batch-scanning.md#consequences). Rendering
the chart without a certificate fails rather than quietly producing an ingress
nobody can scan from — see below for supplying one.

If something in front of the ingress terminates TLS — a load balancer, a
service mesh — set `ingress.tls.terminatedElsewhere=true`. That stops the
Ingress asking for a certificate; it does not make plain HTTP to the browser
supported, because the camera still needs a secure context.

### Administrative access

[Decision 0013](decisions/0013-administrator-sign-in.md) point 6 restricts the
routes only an administrator uses to a network volunteers do not need: the mesh,
a VPN, or an identity-aware proxy. Administrators are few and their locations
predictable, so this costs them very little, and it is the one place a network
boundary fits without excluding a volunteer on a phone wherever the stock
happens to be.

**It is off unless a deployer turns it on, and nothing in the application can
tell.** The restriction decides whose packets arrive, not what a request says,
so a deployment that skips it behaves exactly like one that honoured it —
right up until somebody who should not have reached `/admin` does. That is why
it is stated here as a precondition rather than checked in code, the same shape
as the requirement below.

While it is off, `/admin` and `/accounts` are reachable from anywhere the
ingress is, and the only thing between an anonymous request and the
administrative surface is a password plus the second factor
[decision 0013](decisions/0013-administrator-sign-in.md) point 3 requires.
That is a real defence and it is not this one. Turning it on is the difference
between one credential and one credential that can only be offered from a
network you control.

#### Which paths are restricted

| Path | Restricted | Why |
| --- | --- | --- |
| `/admin` | yes | Django admin: everything that edits, merges, revokes or corrects |
| `/accounts` | yes | Every way in — the local password form, the providers, the second factors |
| `/api/labels/sheet` | yes | A print run of stickers; a volunteer scans labels and never prints them |
| `/api/schema`, `/api/docs` | yes | The description of the whole surface, both halves, and no volunteer flow fetches either |
| `/api`, `/api/me`, `/api/healthz` | no | The index that hands the browser its CSRF token, who-am-I, and the probe |
| `/api/stock/transactions`, `/api/volunteers` | no | The two appends [decision 0012](decisions/0012-two-populations.md) exists to keep open |
| `/api/items`, `/api/locations`, `/api/categories`, `/api/labels`, `/api/labels/{code}` | no | A volunteer reads all of these; the cached label map is what makes a scan resolve from a basement |
| `/`, `/assets`, `/S/{code}` | no | The volunteer app and the URL printed on every sticker |

**The administrative operations of `/api` are deliberately not on that list, and
cannot be.** They are not separate paths: `POST /api/items` and
`GET /api/items` are the same path, as are the `PATCH` that merges volunteers
and the `GET` that lists them for a pick-list. A Kubernetes `Ingress` matches on
host and path and has no way to say "this method only", so restricting the
administrative half by path would take the volunteer half with it — the reads
the app makes on load, and the label map a phone needs before it can resolve a
scan. Those operations stay reachable at the network and remain gated where
they already are: a session, plus the staff flag, in
`backend/src/inventory/permissions.py`. Anything that wants a network boundary
around them too needs a method-aware proxy in front of this ingress, which this
chart does not attempt.

Two things follow from the list that are worth saying out loud. Restricting
`/accounts` means an administrator must be on the permitted network **to sign in
at all**, which is the intent rather than a side effect; the provider round trip
still works, because the redirect back to `/accounts/<provider>/login/callback/`
is made by that administrator's own browser. And path prefixes are matched per
segment, so a controller that over-matches would restrict more than this list,
never less — it fails closed.

#### Turning it on

The chart renders a second Ingress for those paths, carrying whichever
restriction the deployer names. A second resource rather than a second rule,
because an allow-list is an annotation and annotations apply to every path of
the Ingress carrying them.

```bash
helm upgrade --install inventory-tng infra/helm/inventory-tng ... \
  --set ingress.administrative.enabled=true \
  --set 'ingress.administrative.allowedSourceRanges={10.69.0.0/16,199.170.132.0/24}'
```

The same switch takes the other two shapes of the boundary, in
`ingress.administrative.annotations`, which land on that Ingress and on nothing
else:

- **A VPN** is not a separate case — put the pool it hands out in
  `allowedSourceRanges`.
- **An identity-aware proxy**, with ingress-nginx and oauth2-proxy or
  equivalent:

  ```yaml
  ingress:
    administrative:
      enabled: true
      annotations:
        nginx.ingress.kubernetes.io/auth-url: https://auth.example.net/oauth2/auth
        nginx.ingress.kubernetes.io/auth-signin: https://auth.example.net/oauth2/start?rd=$escaped_request_uri
  ```

- **A mesh or Traefik middleware**, named the same way —
  `traefik.ingress.kubernetes.io/router.middlewares: admin-ipallowlist@kubernetescrd`,
  with the middleware itself owned outside this chart.

Either the ranges or the annotations must be set; `enabled: true` with neither
fails at render time rather than producing an Ingress that carries no
restriction and looks as though it does. `ingress.administrative.paths` is the
list in the table above, and
`ingress.administrative.sourceRangeAnnotation` is which annotation carries the
CIDRs — ingress-nginx's by default, since every controller spells it
differently.

Four things this cannot do for you:

- **The allow-list is only as true as the client IP the controller sees.**
  Behind a cloud load balancer that does not preserve it, every request appears
  to come from the load balancer and the allow-list either admits everybody or
  nobody. Configure the controller for the chain in front of it
  (`use-forwarded-headers`, or PROXY protocol) before trusting this.
- **Do the first superuser first.** Creating it needs `kubectl exec`, which is
  unaffected, but signing that account in needs `/accounts` — so either turn
  this on from a machine already inside the permitted range, or bootstrap the
  account and its second factor before you switch it on. It is off by default
  for exactly this reason: a default-on with an empty allow-list would lock a
  first-time deployer out of the admin they need.
- **Annotations are not copied from `ingress.annotations`.** The administrative
  Ingress names the same TLS Secret but carries none of the other Ingress's
  annotations, because a cert-manager issuer on both would leave two
  Certificates contending for one Secret. Repeat anything else you need in
  `ingress.administrative.annotations`.
- **It is not a substitute for the requirement below.** An in-cluster client or
  a `kubectl port-forward` reaches the frontend pod without passing any
  ingress, administrative or not.

Confirm what was rendered before applying it:

```bash
helm lint infra/helm/inventory-tng
helm template test infra/helm/inventory-tng --set image.tag=v0.1.0 \
  --set ingress.administrative.enabled=true \
  --set 'ingress.administrative.allowedSourceRanges={10.69.0.0/16}' \
  | grep -E 'name:.*-admin|whitelist|^ +- path:'
```

and afterwards, that a request from outside the range is refused:

```bash
curl -o /dev/null -w '%{http_code}\n' https://inventory.nycmesh.net/admin/   # expect 403
curl -o /dev/null -w '%{http_code}\n' https://inventory.nycmesh.net/api      # expect 200
```

#### The only route to the frontend pod

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
kubectl -n inventory-tng logs deploy/inventory-tng-inventory-tng-backend
```

### Migrations

`manage.py migrate` runs as a Helm `pre-install,pre-upgrade` Job, not from a web
container. With multiple backend replicas, migrating from a starting web process
would let several replicas apply the same migration concurrently. The Job runs
once per release, before new pods roll out; if it fails, the release stops and
the old pods keep serving.

Three things follow from that arrangement, and they are what an administrator
needs to predict what a release will do:

- **The Job is the same image as the web pods, with the same environment.** It
  reads `DATABASE_URL` out of the same Secret, so anything the Job can reach
  the pods can, and a Job that fails to connect has told you the pods would
  have too.
- **`helm upgrade` blocks on it.** The hook runs to completion before any new
  pod is created, so a command that returns successfully is a database whose
  schema matches the image about to serve it. One that fails leaves the
  previous release running and untouched.
- **A Job per release survives its release.** Its name carries the revision
  number rather than being reused, so the migration that failed is still an
  object in the namespace afterwards and its logs can be read.

To watch one, or to read why a release stopped:

```bash
kubectl -n inventory-tng logs job/inventory-tng-inventory-tng-migrate-1
```

with the trailing number being that release's revision, from `helm history`.

### First administrator

```bash
kubectl -n inventory-tng exec -it deploy/inventory-tng-inventory-tng-backend -- \
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
