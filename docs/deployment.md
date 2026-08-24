# Deployment

inventory-tng deploys to standard Kubernetes and to
[CodeNOW](https://www.codenow.com/) from **one** set of artifacts: two container
images and one Helm chart. CodeNOW consumes a component's own Dockerfile and
Helm chart and deploys them to a Kubernetes cluster, so there is no second
deployment path to maintain and no CodeNOW-specific code in this repository.

For what the components are, see [architecture.md](architecture.md). For local
development — which uses `compose.yaml`, not this chart — see
[DEVELOPERS.md](../DEVELOPERS.md).

## Read this before you start

**This procedure has never been run through to the end against a cluster, and
three known defects stand in the way of the first person who does.** They are in
the chart rather than in this page, so
this page cannot fix them; what it can do is name them where you would
otherwise meet them as a symptom. Each is filed, and each is listed again at
the step it stops.

| What happens | Why | Filed as |
| --- | --- | --- |
| `ImagePullBackOff` on every pod, at the first install | No image has ever been published from this repository, there is no `v0.1.0` to pull, and the chart renders no `imagePullSecrets` for a private one | `inventory-tng-qe7` |
| `helm upgrade` blocks until it times out, in a namespace with a `ResourceQuota` | The migrate Job renders with no `resources`, and a quota on cpu or memory refuses a pod that declares none unless a `LimitRange` fills them in | `inventory-tng-v7g` |
| Every backend pod dies together, some minutes into a database failover that blackholes packets | Each readiness probe blocks in the driver until its worker is killed at 30 seconds, so probe traffic alone can occupy all three; liveness then misses its deadlines from the accept queue | `inventory-tng-39ng` |

The first stops an install outright. Until `inventory-tng-qe7` is done, treat
what follows as the procedure that will work rather than one that has.

**Only the rendering is proven.** CI has no cluster, so the furthest it can
follow this page is `helm lint` and the `helm template` lines printed further
down — those it runs on every push, along with a check that every chart value
quoted here is one the chart really has. Nothing that installs, applies or
execs has been tried for you, and the first time any of it runs will be when
you run it. What that leaves covered elsewhere is
[What CI proves](../DEVELOPERS.md#what-ci-proves).

## From an empty cluster to a first sign-in

Ten steps, in this order, each linking to the section that explains it. Steps 6
and 7 — install, then check — are the two you run again on every release; the
rest happen once for the life of an environment.

1. **Get a database.** This chart deploys none, for the reason
   [Database](#database) gives, so the URL of one is an input rather than an
   output.
2. **Have an image to pull.** Nothing publishes one yet, so this is a step
   somebody has to do by hand today — [Artifacts](#artifacts) says what to
   build and where the chart looks for it.
3. **Create the namespace**, because everything below is in it:

   ```bash
   kubectl create namespace inventory-tng
   ```

4. **Create the Secret** holding that URL and a signing key —
   [Secrets](#secrets). Nothing starts without it, deliberately: see
   [what has no default](#what-has-no-default).
5. **Supply a certificate** in the Secret the chart names —
   [the only route to the frontend pod](#the-only-route-to-the-frontend-pod).
   There is no switch that skips this, because the camera a volunteer scans
   with does not work without it.
6. **Install the chart** — [Deploying to Kubernetes](#deploying-to-kubernetes).
   The schema is migrated as part of this and before anything serves; what that
   means when it fails is [Migrations](#migrations).
7. **Check that it came up**: `kubectl -n inventory-tng get pods`, then
   `curl https://<your host>/api/healthz`, which answers only once the database
   is reachable — [health checks](#health-checks).
8. **Make the first administrator and enrol its second factor** —
   [First administrator](#first-administrator). This is the step people are
   most often stopped by, and it is the one that has to happen before the next.
9. **Restrict the administrative routes** —
   [Administrative access](#administrative-access). After step 8, never before:
   signing in needs `/accounts`, which is one of the paths this shuts.
10. **Add a sign-in provider**, if you want one —
    [the provider Secret](#the-provider-secret). Optional, changeable later, and
    it grants nobody anything by itself.

Every `kubectl` command below names a resource as it is rendered when the
release is called `inventory-tng`, as in step 6. The chart names a resource
`<release>-<component>` when the release is already called after the chart, and
`<release>-inventory-tng-<component>` when it is not — so a release under
another name renames all of them with it, and the commands below have to be
adjusted. A test renders the chart and holds these names against it, so they
cannot fall behind the chart the way they once did.

## Artifacts

| Artifact | Source | Contents |
| --- | --- | --- |
| Backend image | [`backend/Dockerfile`](../backend/Dockerfile) | Django + gunicorn, static files collected at build time |
| Frontend image | [`frontend/Dockerfile`](../frontend/Dockerfile) | Built SPA assets served by unprivileged nginx |
| Helm chart | [`infra/helm/inventory-tng`](../infra/helm/inventory-tng) | Deployments, Services, Ingress, migration Job |

Both images run as non-root. The backend runs with a read-only root filesystem.

### Nothing publishes them yet

The chart pulls `ghcr.io/lotia/nycmesh-inventory-tng-backend:<image.tag>` and
`-frontend:<image.tag>`. **No such image exists.** CI builds both on every push
and pushes neither, this repository carries no release tag, and the chart
renders no `imagePullSecrets`, so it could not authenticate to a private
registry even if one held them. Filed as `inventory-tng-qe7`; until that lands,
an install gets `ImagePullBackOff` on every pod.

What to do in the meantime is build and push them yourself, and point the chart
at wherever you put them:

```bash
docker build -t <your registry>/inventory-tng-backend:<tag> backend
docker build -t <your registry>/inventory-tng-frontend:<tag> frontend
docker push <your registry>/inventory-tng-backend:<tag>
docker push <your registry>/inventory-tng-frontend:<tag>
```

`image.registry` and `image.repository` are the two values that move the chart
onto that registry; the chart appends `-backend` and `-frontend` to the
repository itself. A private registry needs a pull Secret the chart cannot
reference, so either make the repository public or wait for
`inventory-tng-qe7`.

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

A value set to the empty string counts as unset, and takes its default —
emptying `django.numProxies` in a values file is a release that starts on `2`,
not a pod that will not start at all. The two Secret values above are the
exceptions: they have no default, so emptying one stops the process, which is
the whole reason they have none.

Everything else in the table below is a value the chart already carries, so a
release that supplies only those two starts. It starts answering to whatever
hostname `values.yaml` was last left naming, which is why
`django.allowedHosts` is marked required even though it is never missing.
Emptying it is the one way to make that marking bite, and it is refused by
`helm template` rather than at boot — not on Django's account but on the
ingress's, which would otherwise forward a hostname nothing answers to.
[Health checks](#health-checks) is where that lives.

| Variable | Required | Source in Kubernetes | Notes |
| --- | --- | --- | --- |
| `DJANGO_SECRET_KEY` | yes | Secret | No default. A missing value fails at boot by design, and so does an empty one — signing sessions with nothing is the failure the refusal exists to prevent |
| `DATABASE_URL` | yes | Secret | `postgres://user:password@host:5432/dbname`. No default, so an empty value stops the process exactly as a missing one does |
| `DJANGO_DEBUG` | no | chart (`django.debug`) | Must be `false` outside development |
| `DJANGO_LOG_LEVEL` | no | chart (`django.logLevel`) | How much the backend says, on standard output. Default `INFO`. Any level Python knows; one it does not know stops the process at boot rather than starting quietly at some other level. [Reading the logs](#reading-the-logs) is what to do with the output |
| `DJANGO_LOG_LEVELS` | no | chart (`django.logLevels`) | Comma-separated `logger=LEVEL` pairs laid over `DJANGO_LOG_LEVEL`, so that one subsystem can be raised without raising everything — `inventory.sheet=DEBUG`. Empty by default. Not every logger answers: Django decides whether to record a query from `DJANGO_DEBUG` rather than from a logger's level, so raising its SQL logger gets you nothing in a deployment however it is set |
| `DJANGO_LOG_FORMAT` | no | chart (`django.logFormat`) | `json` for a collector or `console` for a person. The record is identical either way and only the drawing differs. The chart sets `json`; the code's own default is `console`, so a process nobody configured is still readable |
| `DJANGO_SECURITY_LOG_RATE` | no | chart (`django.securityLogRate`) | How much a refused request may write, as `<count>/<period>`. Default and shipped value `10/min`, counted per logger and per gunicorn worker. Nought is refused rather than read as "write none". [Reading the logs](#reading-the-logs) says what these records contain and what they no longer contain |
| `DJANGO_ALLOWED_HOSTS` | yes | chart (`django.allowedHosts`) | Comma-separated hostnames. Two other things read it, and [health checks](#health-checks) says what they do with it |
| `DJANGO_EXTRA_ALLOWED_HOSTS` | no | chart (the downward API), not a knob | The pod's own address, added to the list above. Not something to set by hand — the chart fills it because nobody can know it in advance, and [health checks](#health-checks) says what it is for |
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
| `OTEL_EXPORTER_OTLP_ENDPOINT` | no | chart (`django.otlpEndpoint`) | Where traces and metrics go. Empty means the SDK is not started at all. See [Telemetry](#telemetry) before setting it |
| `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` | no | chart (`django.otelServiceName`, `django.otelResourceAttributes`) | What this service is called wherever its telemetry lands, and what else is attached to every span. Standard OpenTelemetry names |
| `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` | no | chart (`django.tracesSampler`, `django.tracesSamplerArg`) | The fraction of traces recorded. Code default `0.1`; the chart ships `1.0`. [Telemetry](#telemetry) says how to sample a subset instead |
| `TELEMETRY_PERSONAL_DATA` | no | chart (`django.personalData`) | `redacted`, which is what every configuration here ships, or `recorded`. A value that is neither stops the process. [observability.md](observability.md#recording-personal-data-on-purpose) is what it admits and what admitting it obliges of wherever the telemetry lands |
| `DEBUG_TRACE_LIFETIME_SECONDS` | no | chart (`django.debugTraceLifetimeSeconds`) | How long a token from `manage.py mint_debug_token` has one volunteer's requests recorded in full. Default `3600`. Rotating `DJANGO_SECRET_KEY` revokes every one that exists |
| `DEBUG_TRACE_RATE` | no | chart (`django.debugTraceRate`) | What one such token may cost, as `<count>/<period>`. Default `60/min`, counted per process like the append rates. [observability.md](observability.md#recording-one-volunteers-requests) is what a token authorises and what it does not |
| `LABEL_BASE_URL` | no | chart (`django.labelBaseUrl`) | The origin encoded into every printed QR code. It is on the stickers, not in the database, so changing it does not change the labels already on the shelves: move the app and keep a permanent redirect from the old host rather than reprinting. It must stay within what QR alphanumeric mode can carry and short enough to print at the module size the generator insists on — both are refused loudly rather than printed, see [`.env.sample`](../.env.sample) |

Every rate in that table is counted **per backend process**, because the
counters live in Django's default in-memory cache. Three gunicorn workers per
pod means a client can append three times the configured rate, multiplied again
by the replica count, so set a rate for one process and expect the deployment
to allow more.
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

The frontend image takes two variables. `BACKEND_ORIGIN`, which the chart sets
to the backend Service. And `COLLECTOR_ORIGIN` (`frontend.collectorOrigin`,
empty by default), where nginx forwards a browser's own spans — nothing posts
there without a signed debug token, and an unset value means the image's
default, which refuses the connection.
[observability.md](observability.md#recording-one-volunteers-requests) is what
that path is guarded by.

Nothing environment-specific is compiled into the JavaScript bundle either way,
so one image tag is valid in every environment.

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

**Keep your values in a file, not in `--set` flags.** Helm carries nothing
across an upgrade that is not either in the chart's defaults or given again, so
a release installed with four `--set` flags and upgraded with one silently
reverts the other three to the chart's values — which point at
`inventory.nycmesh.net`, whoever you are. That is a whole deployment answering
the wrong hostname, with no error anywhere. Write the file once:

```yaml
# my-values.yaml, kept wherever you keep deployment configuration
image:
  tag: v0.1.0
ingress:
  host: inventory.example.net
django:
  allowedHosts: inventory.example.net
  labelBaseUrl: https://inventory.example.net
```

```bash
helm upgrade --install inventory-tng infra/helm/inventory-tng \
  --namespace inventory-tng \
  --values my-values.yaml
```

The namespace is step 3's; `--create-namespace` is not used here because the
Secret in step 4 had to go somewhere first.

`django.labelBaseUrl` is in that file for a reason the others are not: it is
the origin printed inside every QR code, so a release that leaves it at the
chart default stickers your shelves with somebody else's hostname, and the
stickers are the one thing a later release cannot correct. Set it before you
print anything.

If you would rather not keep a file, `helm upgrade --reuse-values` carries the
previous release's values forward — but it also carries forward anything you
have since removed from the chart, so a file is the arrangement that stays
true.

**TLS is not optional**, and the chart offers no switch to turn it off. QR
scanning stops working over plain HTTP, including on a LAN address; why, and
what the app says to whoever hits it, is in
[decision 0011](decisions/0011-qr-batch-scanning.md#consequences).

**Nothing fails when the certificate is missing**, so do not wait to be told.
The chart checks only that one has been *named*: `ingress.tls.secretName`
carries a default, so a render with no Secret anywhere in the cluster succeeds,
and it is an explicitly emptied value — not an absent Secret — that is refused.
What a missing Secret gets you is ingress-nginx serving its own self-signed
fake certificate, a browser warning, and, the part that costs an evening, a
camera that never opens: a certificate the browser does not trust is not a
secure context. Supply one before you install; see below.

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
| `/api`, `/api/me`, `/api/healthz`, `/api/livez` | no | The index that hands the browser its CSRF token, who-am-I, and the two probes |
| `/api/client-failures`, `/api/debug-trace` | no | What a volunteer's browser could not handle, and the token check nginx makes before forwarding its spans — [decision 0012](decisions/0012-two-populations.md) argues the first |
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

Add it to the same values file rather than as flags, for the reason
[Deploying to Kubernetes](#deploying-to-kubernetes) gives — a later upgrade
without them takes the restriction off again, and nothing says so:

```yaml
ingress:
  administrative:
    enabled: true
    allowedSourceRanges:
      - 10.69.0.0/16
      - 199.170.132.0/24
```

```bash
helm upgrade --install inventory-tng infra/helm/inventory-tng \
  --namespace inventory-tng --values my-values.yaml
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

or, where cert-manager runs in the cluster, let it fill the same Secret in — in
the values file, beside everything else:

```yaml
ingress:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
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

Three things follow from that arrangement, and they are what an administrator
needs to predict what a release will do:

- **The Job is the same image as the web pods, with the same environment.** It
  reads `DATABASE_URL` out of the same Secret, so anything the Job can reach
  the pods can, and a Job that fails to connect has told you the pods would
  have too.
- **`helm upgrade` blocks on it, for five minutes.** The hook runs to
  completion before any new pod is created, so a command that returns
  successfully is a database whose schema matches the image about to serve it.
  One that fails leaves the previous release running and untouched. Five
  minutes is Helm's default `--timeout` and it applies to the whole release,
  hook included: a slow migration on a large table wants a longer one passed
  explicitly, and a `--timeout` that expires does not stop the Job.
- **A failure is four failures.** The Job carries `backoffLimit: 3`, so a
  migration that is broken rather than unlucky is attempted four times before
  the Job is failed. Each attempt is a fresh pod against the same database.
- **A Job per release survives its release.** Its name carries the revision
  number rather than being reused, so the migration that failed is still an
  object in the namespace afterwards and its logs can be read.
- **In a namespace with a `ResourceQuota`, it may never be admitted at all.**
  The Job renders with no `resources`, and a quota on cpu or memory refuses a
  pod that declares none unless a `LimitRange` supplies them. The symptom is a
  release that blocks until the timeout with no pod to read logs from.
  `inventory-tng-v7g`.

To watch one, or to read why a release stopped:

```bash
kubectl -n inventory-tng logs job/inventory-tng-migrate-1
```

with the trailing number being that release's revision, from `helm history`.

### When the *first* install is what failed

The paragraph above is about upgrades, and a first install behaves differently
in a way that catches everybody once. `helm upgrade --install` on a release
that has never had a successful deployment does not retry it — it answers:

```
Error: UPGRADE FAILED: "inventory-tng" has no deployed releases
```

There is no previous release to keep serving and nothing to roll back to, so
the failed revision simply sits there and blocks the name. Clear it and install
again:

```bash
helm uninstall inventory-tng --namespace inventory-tng
```

`uninstall` removes the release's objects; it does not touch the database,
which this chart does not own, so whatever the failed migration did to the
schema is still done. Read the migrate Job's logs before you try again: the
Job carries `helm.sh/hook-delete-policy: before-hook-creation`, so the next
attempt deletes it to make room for its own.

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
it lives.

`manage.py clearsessions` is not the command for this: it deletes only sessions
that have already expired, which is the opposite of the set that matters, and
it takes no flag that widens it. Delete them all, in a shell in the same pod:

```bash
kubectl -n inventory-tng exec -it deploy/inventory-tng-backend -- \
  python manage.py shell -c \
  'from django.contrib.sessions.models import Session; print(Session.objects.all().delete())'
```

Once, against the deployed database. Everybody signs in again, this time
through the door that asks for a code.

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

The backend has two probes, and it has two because the kubelet does two very
different things with the answers.

| Probe | Path | Answers | What it asks | What a failure does |
| --- | --- | --- | --- | --- |
| Readiness | `GET /api/healthz` | `{"status": "ok"}` | Runs a trivial query, so it fails while the database is unreachable | The pod stops receiving traffic 21–31 seconds later, and rejoins by itself when it passes again |
| Liveness | `GET /api/livez` | `{"status": "alive"}` | Nothing at all — no database, no cache, no disk | The kubelet kills the container 45–65 seconds later and Kubernetes starts a new one |

**The two words are different on purpose**, because the two answers mean
different things and a person with `curl` needs to know which they reached.
`ok` is the one that has been to the database. `alive` means only that a
process answered, so reading it as "this pod can serve" during an incident is
exactly the mistake the split exists to make visible.

**A liveness probe that asks about a dependency is the shape of an outage, not
a stricter check.** Killing a container repairs a process that has stopped
serving; it does nothing whatever for a database in the middle of a failover.
Point liveness at the query and a failover running past a minute takes out
every replica at once — they all ask the same database, so they all fail
together — and each restarts into a database still recovering, fails again, and
is backed off exponentially. A blip becomes a quarter of an hour with nothing
running, presented to whoever is paged as `CrashLoopBackOff` against a database
that is by then perfectly healthy. So the rule is that anything this process
depends on belongs on readiness, and liveness is left to answer the only
question a restart is a cure for.

**Both of those are ranges rather than figures, and the range is not slack in
the writing.** The kubelet starts each attempt on a fixed schedule and records
a failure only when that attempt returns, so where in the current period a pod
goes quiet decides how much of that period is wasted. Liveness is three
failures twenty seconds apart with five seconds allowed for each, which lands
between 45 and 65; readiness is three ten apart at a one-second deadline,
which lands between 21 and 31. Quote the top of each range when you are sizing
a failover window or writing an alert, because that is the one an incident
will find.

The chart writes out every field those come from, defaults included, so the
arithmetic can be read beside the probe instead of assembled from a table of
Kubernetes defaults somewhere else. `test_chart.py` compares each probe whole
against what this paragraph claims, so a field added, removed or retuned fails
the suite rather than making a sentence here quietly wrong.

**Two things the split gives up, neither of which is a bug to report.** They
are stated here because both look like faults when you meet them.

The first is that *Kubernetes never restarts a container for prolonged
unreadiness*. When liveness ran the query, a pod whose own database access had
wedged — a leaked socket, exhausted file descriptors, a stale DNS answer held
by one pod — failed liveness and was restarted into a working state. Now it
answers `/api/livez` for ever, fails readiness for ever, and sits out of the
Service with no restart and no `CrashLoopBackOff` to page on. Capacity quietly
drops by a replica. A pod that has been `0/1 Running` for a long time is that
case, and `kubectl delete pod` is the answer.

**Neither probe is reachable through the ingress at the moment you want it.**
Readiness gates the pod's membership of the Service as a whole rather than
per path, so a pod failing `/api/healthz` stops routing `/api/livez` at the
same instant — during a failover the site answers 503 for both, and for a
single unready pod there is no route to it at all. So the `curl` in step 7
answers the question only while nothing is wrong. Ask a particular pod
instead:

```bash
kubectl -n inventory-tng port-forward <pod> 8000:8000
curl -s localhost:8000/api/livez ; curl -s localhost:8000/api/healthz
```

`alive` with no `ok` is the whole diagnosis: the process is fine and its
database is not, which is a pod to leave alone.

The second is that "its siblings take the traffic" is only true of a fault one
pod has. Every replica asks the same database, so a failover fails readiness on
all of them within 30 seconds and the Service empties — the ingress answers 503
until the database is back. That is a worse minute than a single pod's and a far
better quarter of an hour than the crash loop it replaced, because nothing has
to recover from being killed: the pods are still there, still healthy, and
rejoin the moment the query succeeds.

**One known defect is left here**, and it is what stops this being the whole
answer: readiness itself can starve the process it is protecting. gunicorn runs
synchronous workers and the database connection has no `connect_timeout`, so a
failover that blackholes packets rather than refusing them leaves each readiness
probe blocked in the driver until the worker is killed at 30 seconds — long
enough that probe traffic alone can occupy every worker, and `/api/livez` then
misses its own deadlines from the accept queue. `inventory-tng-39ng` bounds the
connection, which is the fix that makes every request fail fast rather than only
the probes.

**A pod answers to its own address, and that is what makes any of this work.**
The kubelet dials a pod rather than the site, so a probe asks for
`<pod IP>:8000` — an address nobody could have listed in `django.allowedHosts`,
because it does not exist until the pod does. Without it Django answers 400,
readiness never passes, no pod joins the Service, and liveness kills each
container about fifty seconds in — the ten it waits before starting, then
three refusals twenty apart, and a 400 comes back at once rather than using
any of its five seconds — for ever. So the chart passes the address in through
the downward API, as
`DJANGO_EXTRA_ALLOWED_HOSTS`, and Django adds it to the list. Nothing about that depends on what you wrote in
`django.allowedHosts`, which is free to be several names, or `*`.

**What the probes cannot tell you is whether the ingress agrees with them.**
They reach the pod by its address and go green regardless, while nginx forwards
a browser's `Host` untouched — so an `ingress.host` that `django.allowedHosts`
does not cover gives you two Ready pods and a site that serves its shell and no
data. The refusal is at least visible now — Django's `DisallowedHost` security
logger names the rejected hostname in the backend's output, where before this it reached
nothing at all — but a release that has to be diagnosed from its logs is one
that should not have installed, so the chart refuses to render it instead,
naming both values. What that record contains, and how many of them a scanner
can make you write, is [Reading the logs](#reading-the-logs).

## Telemetry

Traces and metrics leave the backend over OTLP; logs do not, and
[Reading the logs](#reading-the-logs) is that half. Which arrangement this is,
and why the two halves differ, is
[decision 0021](decisions/0021-telemetry-over-otlp.md).

**No endpoint means no SDK.** `OTEL_EXPORTER_OTLP_ENDPOINT` empty is the
shipped default in the chart, so a release that says nothing starts no exporter,
no sampler and no instrumented cursor — it behaves exactly as it did before any
of this existed. Setting it is what turns telemetry on.

**What a collector receives**, without anybody writing an instrumentation: a
server span per request named for its route rather than its path, the database
queries beneath it as child spans, HTTP duration and count by route and status,
and database client duration.

**Where to send it is a decision this chart does not make**, and
[observability.md](observability.md#choosing-a-destination) is where the options
are weighed — including what it costs to run one on a cluster you host
yourself, which is a live possibility here.

**What it does not receive is decided by an allowlist**, and that is where to
look before pointing this at anything: the caller's address, the concrete URL
and the user agent are dropped before an exporter sees them, and one setting
re-admits them for as long as somebody needs to look.
[observability.md](observability.md#what-telemetry-may-carry) is what it may
carry, what it may not, and what turning that setting on obliges of wherever
the telemetry lands.

**How much is recorded** is `OTEL_TRACES_SAMPLER` and `OTEL_TRACES_SAMPLER_ARG`,
and `OTEL_SDK_DISABLED=true` turns the whole thing off during an incident.
[observability.md](observability.md#how-much-is-recorded) is what the values
mean, what this chart ships, and how to sample a subset instead.

**It starts in the worker**, from gunicorn's `post_fork` hook and from the WSGI
module. The reason usually given for that — an exporter's thread not surviving
`fork()`, so a master-initialised SDK exports nothing — no longer holds: the SDK
rebuilds its batch processor in the child, measured on 1.44.0. The hook stays for a
different reason, which [decision 0021](decisions/0021-telemetry-over-otlp.md)
gives along with the measurement.

## Reading the logs

The backend writes everything at `DJANGO_LOG_LEVEL` and above to **standard
output** and nowhere else. Nothing is mailed anywhere and no file is written, so
whatever collects a container's output is the whole of it:

```bash
kubectl -n inventory-tng logs deploy/inventory-tng-backend --follow
```

Two consequences worth knowing before an incident rather than during one.

**Nothing outlives the pod.** A container's output is kept by the node until
the pod is replaced, and a `CrashLoopBackOff` is a pod being replaced. Reaching
the run before the current one needs `--previous`, and the run before *that* is
gone. A cluster that anyone intends to debug wants a log collector shipping this
somewhere durable; standing one up is the cluster's business rather than this
chart's, and this chart deliberately does not render one.

**It is one format, including gunicorn's.** gunicorn logs its own access line
per request through handlers of its own, which would otherwise put plain text
beside the application's JSON and leave whatever parses the stream meeting a
line it was not written for. `backend/src/gunicorn.conf.py` points it at the
same arrangement, so there is one shape to parse.

**A refused request is bounded, and carries no traceback.** Django writes a
record for every request it refuses — a `Host` it does not answer to, a body too
large — and that path is reachable from the internet without a credential, so
what it costs is decided by whoever is scanning rather than by you. Two things
are done about it. The traceback is dropped, because those frames are the same
three Django functions whatever the request was and the part worth having — the
hostname that was refused — is in the message. And at most
`DJANGO_SECURITY_LOG_RATE` of them are written per period, per logger and per
gunicorn worker; the rest are counted, and the count arrives on the next one
that is written, so a flood is measured rather than hidden. Per logger, so that
a host scanner cannot spend the window belonging to the CSRF and session
failures Django files under the same family. `django.securityLogRate` in the chart
sets it, `10/min` is the shipped value, and raising it is what to do when a
deployment is genuinely refusing requests it ought to be answering.

**To read JSON yourself, draw it back into columns as you read it:**

```bash
kubectl -n inventory-tng logs -f deploy/inventory-tng-backend | scripts/pretty-logs
```

`scripts/pretty-logs` renders the same fields the process would have drawn for a
terminal, and does it in *your* terminal — so it can measure a width the writing
process never had. `DJANGO_LOG_LAYOUT` and `DJANGO_LOG_CONTEXT` steer it, and
are documented for development in [`.env.sample`](../.env.sample); they do
nothing to a deployment, which is drawing no columns.

## Rollback

```bash
helm rollback inventory-tng --namespace inventory-tng
```

This reverts the application. It does **not** revert database migrations —
write migrations to be backward compatible with the previous release, or plan
the rollback explicitly.
