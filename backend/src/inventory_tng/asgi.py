"""The ASGI entry point, in the shape Django documents:

https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

import django
from django.core.asgi import get_asgi_application

from inventory_tng.debugging import guarded_asgi
from inventory_tng.telemetry import start

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventory_tng.settings")

# The three lines below are in the order `wsgi.py` argues for, and are in that
# order for the reason it gives. Decision 0021 is the record.
#
# Here rather than in an app's `ready` hook, for the reasons on
# `inventory_tng.telemetry`. It does nothing when no collector is configured.
django.setup(set_prefix=False)
start()

# Wrapped for the reason `wsgi.py` gives, in the shape ASGI wants.
application = guarded_asgi(get_asgi_application())
