"""The application, and the one thing it does when it starts.

`ready` runs after Django has configured logging and before anything is
served, which is exactly the moment `inventory.tracing` needs: it reads the
log level to settle its own switch, and it rewrites the callables of the
modules it covers, which has to happen before a request reaches one of them.
"""

from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"

    def ready(self) -> None:
        # Imported here rather than at module scope: an AppConfig is imported
        # while the registry is still being populated, and this reaches the
        # modules it wraps.
        from inventory import tracing

        tracing.start()
