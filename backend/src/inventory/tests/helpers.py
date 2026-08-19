"""Request helpers shared by the API tests.

Here rather than in conftest.py because they are plain functions: pytest
collects fixtures from conftest, and a helper that is imported reads better at
the call site than one that is injected.
"""

from typing import Any

from django.test import Client
from django.urls import reverse


def post(client: Client, name: str, body: dict[str, Any], *args: Any) -> Any:
    return client.post(reverse(name, args=args), data=body, content_type="application/json")


def patch(client: Client, name: str, body: dict[str, Any], *args: Any) -> Any:
    return client.patch(reverse(name, args=args), data=body, content_type="application/json")
