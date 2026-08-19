"""
Django settings for inventory_tng.

Every deployment-varying value is read from the environment so that the same
container image runs in local development, standard Kubernetes, and CodeNOW.
See .env.sample at the repository root for the full list, and
docs/deployment.md for how those values are supplied in each environment.
"""

from pathlib import Path

import environ

# BASE_DIR is backend/src/ -- the directory holding manage.py.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    # Defaults are the ones a volunteer night needs; .env.sample says why.
    APPEND_BURST_RATE=(str, "20/min"),
    APPEND_SUSTAINED_RATE=(str, "300/hour"),
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

ALLOWED_HOSTS: list[str] = env("DJANGO_ALLOWED_HOSTS")

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
    # Edit history for catalogue records. Deliberately not applied to the stock
    # ledger, which is append-only and is its own history
    # (docs/decisions/0008-stock-ledger-transfer-graph.md).
    "simple_history",
    # Project
    "inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Without this, simple_history records *that* a catalogue record changed but
    # never *who* changed it: history_user is read from the request and is NULL
    # unless this middleware puts the request where the model can see it. Must
    # come after AuthenticationMiddleware, which is what sets request.user.
    "simple_history.middleware.HistoryRequestMiddleware",
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

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # drf-spectacular's, plus the throttled response. See inventory/throttling.py.
    "DEFAULT_SCHEMA_CLASS": "inventory.throttling.ThrottleAwareAutoSchema",
    "EXCEPTION_HANDLER": "inventory.throttling.exception_handler",
    # Deliberately no DEFAULT_THROTTLE_CLASSES: the endpoints that carry these
    # limits name them, because which endpoints take no credential is the
    # argument decision 0012 makes and not a default to be inherited quietly.
    # The keys are the throttles' scopes; a name that matches nothing raises at
    # the first request rather than skipping the limit.
    "DEFAULT_THROTTLE_RATES": {
        "append-burst": env("APPEND_BURST_RATE"),
        "append-sustained": env("APPEND_SUSTAINED_RATE"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "NYC Mesh Inventory API",
    "DESCRIPTION": "Inventory tracking for NYC Mesh. See docs/architecture.md.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
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

# Behind an ingress or proxy that terminates TLS.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
