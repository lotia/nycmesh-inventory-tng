"""The ASGI entry point, in the shape Django documents:

https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

from inventory_tng.telemetry import start

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventory_tng.settings")

application = get_asgi_application()

# Here rather than in an app's `ready` hook, for the reasons on
# `inventory_tng.telemetry`. It does nothing when no collector is configured.
start()
