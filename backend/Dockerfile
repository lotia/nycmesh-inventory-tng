# Backend image: Django + DRF served by gunicorn.
# Built identically for local compose, standard Kubernetes, and CodeNOW --
# see docs/deployment.md.

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile in their own layer so application
# edits do not invalidate the (slow) dependency install.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ ./src/


FROM docker.io/library/python:3.14-slim-bookworm AS runtime

# Static files are collected at build time and served by whitenoise, so the
# runtime image needs no nginx sidecar and no shared volume.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=inventory_tng.settings

RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

USER app
WORKDIR /app/src

# collectstatic needs settings to import, but the real secret and database are
# only present at run time. These values are used for this build step alone.
#
# THE DIRECTORY IS CREATED FIRST, and that is load-bearing rather than tidy.
# Without it this image builds cleanly and then answers every rendered page
# with 500. The reason is on STORAGES in settings.py; do not remove this line
# without reading it.
RUN mkdir -p staticfiles && \
    DJANGO_SECRET_KEY=build-time-only \
    DATABASE_URL=postgres://build:build@localhost:5432/build \
    python manage.py collectstatic --noinput

EXPOSE 8000

# Migrations are NOT run here -- they run as a separate step so that scaling
# to multiple replicas cannot race. See docs/deployment.md.
# -c is what puts gunicorn's own access and error logs through the same
# arrangement as the application's, so one stream carries one format. The two
# logfile flags stay because they are what turns the access log ON at all;
# where its records then go is the config file's business.
CMD ["gunicorn", "inventory_tng.wsgi:application", \
     "-c", "gunicorn.conf.py", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
