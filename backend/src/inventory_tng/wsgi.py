"""The WSGI entry point, in the shape Django documents:

https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

import django
from django.core.wsgi import get_wsgi_application

from inventory_tng.telemetry import start

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventory_tng.settings")

# Settings, then instrument, then build the application. The order is the whole
# of this file, and it is load-bearing rather than tidy: instrumenting Django
# adds a middleware to a list the handler reads once, as it is constructed, so
# doing it afterwards leaves a server with no instrumentation at all and no
# sign of it. Instrumenting before `django.setup` is the other way to get it
# wrong. Decision 0021 has both halves and what the second one cost.
#
# Here rather than in an app's `ready` hook, for the reasons on
# `inventory_tng.telemetry`. It does nothing when no collector is configured.
django.setup(set_prefix=False)
start()

application = get_wsgi_application()
