"""
Django settings for inventory_tng.

Every deployment-varying value is read from the environment so that the same
container image runs in local development, standard Kubernetes, and CodeNOW.
See .env.sample at the repository root for the full list, and
docs/deployment.md for how those values are supplied in each environment.
"""

import sys
from pathlib import Path
from typing import Any

from corsheaders.defaults import default_headers

from inventory_tng import debugging, refusals
from inventory_tng.environment import Env
from inventory_tng.hosts import allowed_hosts
from inventory_tng.logs import from_environment
from inventory_tng.telemetry import validate

# BASE_DIR is backend/src/ -- the directory holding manage.py.
BASE_DIR = Path(__file__).resolve().parent.parent

# An empty value means the same as an unset one; `Env` says why.
env = Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    DJANGO_EXTRA_ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    # Defaults are the ones a volunteer night needs; .env.sample says why.
    APPEND_BURST_RATE=(str, "20/min"),
    APPEND_SUSTAINED_RATE=(str, "300/hour"),
    CLIENT_REPORT_RATE=(str, "30/min"),
    NUM_PROXIES=(int, 2),
    # What a signed debug-tracing token is worth. `inventory_tng.debugging`
    # holds both numbers and the argument for each.
    DEBUG_TRACE_LIFETIME_SECONDS=(int, debugging.DEFAULT_LIFETIME_SECONDS),
    DEBUG_TRACE_RATE=(str, debugging.DEFAULT_RATE),
    LABEL_BASE_URL=(str, "https://inventory.nycmesh.net"),
)

# Read the repository-root .env when present -- the same file docker compose
# consumes, so local development has exactly one place to configure. Absent in
# Kubernetes, where the environment comes from ConfigMaps and Secrets instead.
REPO_ROOT = BASE_DIR.parent.parent
env_file = REPO_ROOT / ".env"
if env_file.exists():
    env.read_env(env_file)

# SECURITY: no default. A missing secret must fail loudly at boot rather than
# silently starting with a known-insecure key.
SECRET_KEY = env("DJANGO_SECRET_KEY")

DEBUG = env("DJANGO_DEBUG")

# DJANGO_EXTRA_ALLOWED_HOSTS is for addresses only the running deployment
# knows -- in Kubernetes, the pod's own, which is what a probe asks for and
# what nobody could have written in a values file. Why that matters, and what
# it costs when the list is wrong, is docs/deployment.md#health-checks; why the
# two lists need stripping before use is on `allowed_hosts`. Both are read
# through that one function, here and in inventory/tests/test_chart.py, so
# there is never a second answer.
ALLOWED_HOSTS: list[str] = allowed_hosts(env("DJANGO_ALLOWED_HOSTS"), env("DJANGO_EXTRA_ALLOWED_HOSTS"))

# Everything the process has to say, on standard output, in every environment.
# What the arrangement is and why it does not vary with DEBUG is on
# `logging_config`; the reasoning is decision 0021; where a deployment reads
# the result is docs/deployment.md#reading-the-logs.
# Read straight from the environment rather than through `env`, because
# gunicorn's configuration file reads the same five variables before Django
# exists and the two must not drift. `.env` has already been loaded above, so
# it is in `os.environ` by the time this runs.
#
# The line it hands back names the layout a terminal drawing settled on and
# what that layout drops. It goes to standard error, so that it cannot land in
# the middle of the record stream on standard output, and it is empty whenever
# there is nothing a reader would be surprised by.
LOGGING, _announcement = from_environment()

DEBUG_TRACE_LIFETIME_SECONDS = env("DEBUG_TRACE_LIFETIME_SECONDS")
DEBUG_TRACE_RATE = env("DEBUG_TRACE_RATE")

# Read here rather than where the values are used, so that a malformed one
# stops the process now instead of on the release that turns telemetry on.
# `validate` says why a Django system check would not do. The rate is read the
# same way and for the same reason: a typo in it would otherwise be found by
# the first volunteer to present a token, which is the worst moment there is.
validate()
refusals.rate(DEBUG_TRACE_RATE, "DEBUG_TRACE_RATE")
debugging.lifetime(DEBUG_TRACE_LIFETIME_SECONDS)
if _announcement:
    print(_announcement, file=sys.stderr)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Enables the PostgreSQL-specific index and field support the inventory
    # models rely on -- see docs/data-model.md.
    "django.contrib.postgres",
    # Third party
    "rest_framework",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    # Administrator sign-in: several ways to prove who you are, none of which
    # grants authority. See docs/decisions/0013-administrator-sign-in.md and
    # the settings block near the end of this file.
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.mfa",
    # Installed whether or not credentials are configured: a provider with no
    # client id is simply not offered (SOCIALACCOUNT_PROVIDERS below is built
    # from what the environment supplies), so adding one to a deployment is a
    # secret rather than a release.
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.slack",
    "allauth.socialaccount.providers.openid_connect",
    # Edit history for catalogue records. Deliberately not applied to the stock
    # ledger, which is append-only and is its own history
    # (docs/decisions/0008-stock-ledger-transfer-graph.md).
    "simple_history",
    # Project
    "inventory",
]

MIDDLEWARE = [
    # First, and the class says why that position is the point. What it binds,
    # and the one record no middleware can reach, are there too.
    "inventory_tng.context.RequestContext",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # See CONTENT_SECURITY_POLICY below. After the auth middleware, because a
    # header is added to whatever response the rest of the stack produced.
    "csp.middleware.CSPMiddleware",
    # Without this, simple_history records *that* a catalogue record changed but
    # never *who* changed it: history_user is read from the request and is NULL
    # unless this middleware puts the request where the model can see it. Must
    # come after AuthenticationMiddleware, which is what sets request.user.
    "simple_history.middleware.HistoryRequestMiddleware",
    # allauth requires this and refuses to start without it. Must come after
    # AuthenticationMiddleware: it publishes the request allauth's adapters
    # read, and closes a session whose user has gone away.
    "allauth.account.middleware.AccountMiddleware",
    # Decision 0013 point 3, enforced rather than documented. Must come after
    # allauth's, because it reads the record allauth writes of how this
    # session authenticated. See inventory/middleware.py.
    "inventory.middleware.RequireSecondFactor",
    # The same rule in the interface DRF cannot reach; see the class.
    "inventory.middleware.RequireSecondLookInTheAdmin",
]

# WhiteNoise serves the Django admin's own static files in the built image.
# In development, Django's staticfiles app does it and collectstatic has not
# run, so adding WhiteNoise here would only emit a missing-directory warning.
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "inventory_tng.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "inventory_tng.wsgi.application"
ASGI_APPLICATION = "inventory_tng.asgi.application"

# PostgreSQL via a single DATABASE_URL, which is what both Kubernetes Secrets
# and docker compose hand us most naturally.
DATABASES = {"default": env.db("DATABASE_URL")}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# Administrator sign-in. Decision 0013 is the argument; this is the wiring.
#
# The shape to hold on to while reading it: signing in establishes identity and
# never authority. Every path below ends at an ordinary Django ``User`` with no
# flags set, and the staff flag is granted afterwards by somebody who already
# has it. Nothing here can promote anyone.
# --------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    # Kept, and kept first: it is what the Django admin's own machinery and
    # every existing session already use.
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Where anything asking for a login sends people. One sign-in surface, so the
# Django admin's login is redirected here too -- see inventory_tng/urls.py.
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/"

# Username and password, not email. Decision 0012 point 5 settles what a
# volunteer is and is not, and decision 0013's last consequence settles what
# a provider's email address is worth; between them an email address is
# something an account may have rather than the name it answers to.
ACCOUNT_LOGIN_METHODS = {"username"}
# Email is on the list but not starred, so it is offered and never demanded:
# a provider that hands one over fills it in, one that hands over a relay
# address or nothing at all still gets somebody an account, and the account is
# the identity either way. Listing it is not optional even so -- allauth's
# social signup form raises at import if the field it may prefill is missing.
ACCOUNT_SIGNUP_FIELDS = ["username*", "email", "password1*", "password2*"]
# Nothing in this deployment sends mail, and nothing here needs it to: there
# is no self-service signup to confirm, and an address that arrives from a
# provider is decoration on a record whose identity is the ``User`` row.
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
# A signed-in visitor who lands on the login page is shown it rather than
# bounced onwards. Without this, a signed-in non-administrator following the
# admin's "please log in" redirect is sent straight back to the admin, which
# redirects to the login again -- a loop the browser, not the server, ends.
ACCOUNT_AUTHENTICATED_LOGIN_REDIRECTS = False

# Local accounts are made by an administrator, in the admin. Self-service
# registration would only manufacture accounts with no permissions, which is
# spam with extra steps; the way in that decision 0013 point 2 guarantees is a
# password an administrator issued, not one a stranger chose.
ACCOUNT_ADAPTER = "inventory.adapters.AccountAdapter"

# How long a sign-in counts as recent enough to change something. Decision
# 0014 point 5's "prompt again, even inside a valid session" is this number
# being far shorter than a session: long enough that an administrator working
# through a list of corrections is asked once, short enough that a tab left
# open on a bench is not a standing authority. allauth's own default is 300.
ACCOUNT_REAUTHENTICATION_TIMEOUT = env.int("REAUTHENTICATION_TIMEOUT_SECONDS", default=900)

# Whose request a sign-in rate limit counts, and the same decision the API's
# throttles make: allauth reads X-Forwarded-For back this many hops, exactly as
# DRF does for NUM_PROXIES. Fed from the one variable so the two cannot answer
# differently -- and .env.sample is where the trust argument is written down.
# Without it allauth falls back to REMOTE_ADDR, which behind the deployment's
# proxies is the ingress: every administrator would share one bucket, and ten
# failed sign-ins from anywhere would lock all of them out.
ALLAUTH_TRUSTED_PROXY_COUNT = env.int("NUM_PROXIES")

# Decision 0013 point 5. Automatic sign-up is off, so arriving from a provider
# for the first time is a step somebody takes rather than something that
# happens to them, and the account it makes carries nothing.
SOCIALACCOUNT_AUTO_SIGNUP = False
SOCIALACCOUNT_ADAPTER = "inventory.adapters.SocialAccountAdapter"
# Both deliberately off, and they are the same decision twice: an address a
# provider hands over must not be enough to sign in as, or attach to, an
# account that already exists. Apple returns a relay address and Slack one the
# workspace controls, so treating either as proof of who somebody is would let
# whoever controls the address inherit an administrator's account.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False

# Decision 0013 point 3. TOTP and recovery codes are required on the local
# path -- inventory/middleware.py is what makes "required" true -- and a
# passkey is offered as an extra key rather than as a way round the first two.
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]
MFA_TOTP_ISSUER = "NYC Mesh Inventory"
# One step of tolerance either side, which is thirty seconds. A phone's clock
# drifts and a code typed as the period turns over is the commonest reason a
# correct one is refused; RFC 6238 puts one step as the most that should be
# allowed, and refusing an administrator their own key is its own kind of
# lockout.
MFA_TOTP_TOLERANCE = 1

# Provider credentials, all optional. A provider whose client id or secret is
# absent is not offered, so this application boots with nothing configured and
# decision 0013 point 2's local password path available -- which is also what
# every test and every fresh checkout runs as.
#
# The empty defaults are not defaults for a secret in the sense
# DJANGO_SECRET_KEY means: absent here says "this deployment does not offer
# this provider", which is a legitimate configuration rather than a value
# nobody chose. A half-configured provider is simply not offered either, so a
# typo in one of a pair cannot start a deployment that half works.
GOOGLE_CLIENT_ID = env.str("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env.str("GOOGLE_CLIENT_SECRET", default="")
SLACK_CLIENT_ID = env.str("SLACK_CLIENT_ID", default="")
SLACK_CLIENT_SECRET = env.str("SLACK_CLIENT_SECRET", default="")
OIDC_CLIENT_ID = env.str("OIDC_CLIENT_ID", default="")
OIDC_CLIENT_SECRET = env.str("OIDC_CLIENT_SECRET", default="")
OIDC_SERVER_URL = env.str("OIDC_SERVER_URL", default="")
# What the button says and what the callback URL contains. Both are named
# rather than derived because a deployment's identity provider has a name its
# administrators recognise, and because the provider id is baked into the
# redirect URI registered with that provider and so must not drift.
OIDC_NAME = env.str("OIDC_NAME", default="Single sign-on")
OIDC_PROVIDER_ID = env.str("OIDC_PROVIDER_ID", default="oidc")

SOCIALACCOUNT_PROVIDERS: dict[str, Any] = {}

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    SOCIALACCOUNT_PROVIDERS["google"] = {
        "APPS": [{"client_id": GOOGLE_CLIENT_ID, "secret": GOOGLE_CLIENT_SECRET}],
        "SCOPE": ["profile", "email"],
        # Offline access would have this server hold a refresh token it has no
        # use for: nothing here calls Google on anybody's behalf after the
        # sign-in that identified them.
        "AUTH_PARAMS": {"access_type": "online"},
    }

if SLACK_CLIENT_ID and SLACK_CLIENT_SECRET:
    SOCIALACCOUNT_PROVIDERS["slack"] = {
        "APPS": [{"client_id": SLACK_CLIENT_ID, "secret": SLACK_CLIENT_SECRET}],
    }

if OIDC_CLIENT_ID and OIDC_CLIENT_SECRET and OIDC_SERVER_URL:
    SOCIALACCOUNT_PROVIDERS["openid_connect"] = {
        "APPS": [
            {
                "provider_id": OIDC_PROVIDER_ID,
                "name": OIDC_NAME,
                "client_id": OIDC_CLIENT_ID,
                "secret": OIDC_CLIENT_SECRET,
                "settings": {"server_url": OIDC_SERVER_URL},
            }
        ],
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The manifest storage requires collectstatic to have run, which is true in the
# built image but not in a development checkout -- hence the DEBUG split.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

# Where a scanned label sends the phone. .env.sample says why this is the one
# setting whose old value is stuck to a shelf, and what it may contain;
# inventory/labels.py is what builds the payload from it.
LABEL_BASE_URL = env("LABEL_BASE_URL")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Closed by default in three directions: a session to read anything, the
    # staff flag to change anything, and a recent sign-in to change anything
    # destructive. Decision 0012 makes opening an
    # endpoint up a deliberate act, and a write is the half that cannot be
    # taken back -- so the two endpoints a volunteer needs name their own
    # permissions and everything else is reserved without anybody remembering.
    # See inventory/permissions.py.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "inventory.permissions.StaffWrites",
        "inventory.permissions.RecentlyAuthenticated",
    ],
    # JSON only, in both directions, on every endpoint. A form encoding cannot
    # carry the shapes this API uses -- an array of objects on the batch
    # endpoint, a specification blob on an item -- and where it can carry one it
    # misreads it: DRF reads a key missing from a form body as `false` for a
    # boolean, so a form-encoded create arrives with `active=false` and retires
    # the row an administrator has just added. Advertising a request shape that
    # either fails or lies is worse than refusing it with a 415, which is the
    # call StockTransactionCreateView already made for itself.
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # drf-spectacular's, plus the refusals a view's own policy can produce.
    # See inventory/api.py.
    "DEFAULT_SCHEMA_CLASS": "inventory.api.PolicyAwareAutoSchema",
    "EXCEPTION_HANDLER": "inventory.api.exception_handler",
    # Deliberately no DEFAULT_THROTTLE_CLASSES: the endpoints that carry these
    # limits name them, because which endpoints take no credential is the
    # argument decision 0012 makes and not a default to be inherited quietly.
    # The keys are the throttles' scopes; a name that matches nothing raises at
    # the first request rather than skipping the limit.
    # Whose request a rate limit counts. .env.sample says why the number
    # matters and which way it is dangerous to get wrong.
    "NUM_PROXIES": env.int("NUM_PROXIES"),
    "DEFAULT_THROTTLE_RATES": {
        "append-burst": env("APPEND_BURST_RATE"),
        "append-sustained": env("APPEND_SUSTAINED_RATE"),
        "report": env("CLIENT_REPORT_RATE"),
    },
}

# What the browser is allowed to load and execute.
#
# A requirement of decision 0014, not general good practice. That decision puts
# administrative capability in the same application a volunteer uses, and its
# consequences record what injected script would then reach.
# Re-authentication (inventory/permissions.py, RecentlyAuthenticated) narrows
# what such a script can reach; this narrows how it gets there in the first
# place.
#
# Everything is this origin's own, which the single-origin arrangement in
# docs/architecture.md makes possible: no API on another host to connect to,
# and nothing loaded from a CDN -- Swagger UI's assets are served from here
# (SPECTACULAR_SETTINGS above) for exactly this reason.
#
# No page is an exception. /api/docs came close -- Swagger UI is normally
# booted from an inline block -- and is served by the split view instead, which
# puts that block in a file. See inventory_tng/urls.py.
#
# This covers what Django serves: /api, /admin, /accounts and /static. The
# single-page app's own document is served by nginx and carries the same policy
# from frontend/nginx.conf.template, which is where the volunteer app -- the
# thing decision 0014 is actually worried about -- gets it. The two are written
# out separately because neither server can read the other's configuration;
# they have to agree, and a change to one is a change to both.
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        # No inline script and no eval. This is the directive that matters, and
        # the reason nothing here needs 'unsafe-inline' for it: the app is a
        # bundle, and the Django admin's own scripts are files too.
        #
        # 'wasm-unsafe-eval' is not an eval: it is the one permission
        # WebAssembly compilation needs, and Chromium refuses
        # `WebAssembly.instantiate` without it however the binary was fetched.
        # The label decoder is WebAssembly (decision 0011 section 2), so
        # omitting this would leave the camera reading nothing on half the
        # browsers a volunteer opens this on.
        "script-src": ["'self'", "'wasm-unsafe-eval'"],
        # 'unsafe-inline' only here, and only because emotion -- which MUI
        # renders through -- injects style elements at runtime. A nonce is the
        # alternative and needs emotion's cache configured with one; worth
        # doing, and not worth blocking this on. Inline style cannot execute.
        "style-src": ["'self'", "'unsafe-inline'"],
        # data: for the label sheet's inline symbols and the admin's own icons.
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'"],
        # The API is this origin. A signed-in administrator's browser posting
        # to anywhere else is the exfiltration half of the risk above.
        "connect-src": ["'self'"],
        # Workers, if the decoder ever spawns one -- served from here (decision
        # 0011 section 2 makes self-hosting it non-optional), so 'self' is
        # enough. What lets the decoder's WebAssembly *compile* is
        # 'wasm-unsafe-eval' on script-src above, not this.
        "worker-src": ["'self'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'none'"],
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "NYC Mesh Inventory API",
    "DESCRIPTION": "Inventory tracking for NYC Mesh. See docs/architecture.md.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Swagger UI's own assets, served from this origin rather than from
    # jsdelivr, which is drf-spectacular's default. Two reasons, and the second
    # is the load-bearing one: the same self-contained-image argument decision
    # 0011 section 2 makes for the label decoder, and the Content-Security-
    # Policy below, under which a page fetching script from a CDN renders
    # blank. `drf-spectacular-sidecar` is where the files come from.
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    # OpenAPI 3.1 aligns the schema dialect with JSON Schema 2020-12. See
    # docs/decisions/0010-openapi-version.md for why not 3.0 and not yet 3.2.
    "OAS_VERSION": "3.1.1",
    # Three different things the API exposes are called "kind". drf-spectacular
    # names a schema component after the field, so left alone it resolves the
    # collision with a hashed name like `Kind946Enum`, which is both
    # meaningless to a client author and liable to change as the schema grows,
    # churning the committed document for no reason. The hint is taken as an
    # import path, which cannot reach a class nested inside a model, hence the
    # module-level aliases in inventory.models.
    "ENUM_NAME_OVERRIDES": {
        "LocationKindEnum": "inventory.models.LOCATION_KIND_CHOICES",
        "TransactionKindEnum": "inventory.models.TRANSACTION_KIND_CHOICES",
        "LabelKindEnum": ["item", "location"],
    },
}

# Cross-origin reads only, and normally unused: both the dev server and nginx
# proxy Django's paths, so the browser sees one origin. See .env.sample.
CORS_ALLOWED_ORIGINS: list[str] = env("CORS_ALLOWED_ORIGINS")

# django-cors-headers' defaults do not include the W3C trace headers, so a
# cross-origin request carrying one is refused at the preflight -- not stripped,
# cancelled -- and a trace that starts in the browser cannot reach Django at
# all. The dev server on one port and Django on another are exactly that pair.
#
# The third is `inventory_tng.debugging`'s, and it is here for the same reason
# and was missed for the same one: the header a volunteer's browser carries to
# have their request recorded is a header the preflight has to name, or the
# only way to present a token is by hand with curl.
CORS_ALLOW_HEADERS = (*default_headers, "traceparent", "tracestate", debugging.HEADER.lower())

# Behind an ingress or proxy that terminates TLS.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
