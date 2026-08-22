"""The WSGI entry point, in the shape Django documents:

https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventory_tng.settings")

application = get_wsgi_application()
