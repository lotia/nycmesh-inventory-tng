"""Every administrator page and form sends one not signed in to sign in,
whether its route takes the signed-in administrator or only lists the guard
in its dependencies (guards.py)."""

import re
from collections.abc import Callable
from typing import cast

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from inventory_tng.admins import paths
from inventory_tng.stock.paths import COUNTS
from inventory_tng.web.paths import ADMIN

# Open to anyone, as they are how one signs in; and the count form's GET,
# which reads nothing and sends on to a count page that asks.
OPEN = {paths.SIGN_IN, paths.FORGOT, paths.RESET, f"{COUNTS}/place"}


def test_every_admin_route_sends_one_signed_out_to_sign_in(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    client = make_client_without_database()
    asked = []
    for route in iter_route_contexts(cast(FastAPI, client.app).routes):
        path = route.path or ""
        if not path.startswith(ADMIN) or path in OPEN:
            continue
        for method in sorted(set(route.methods or ()) - {"HEAD"}):
            url = re.sub(r"\{[^}]+\}", "1", path)
            location = client.request(method, url).headers.get("location", "")
            assert location.startswith(paths.SIGN_IN), f"{method} {path}"
            asked.append(path)
    assert len(asked) > 100, "too few admin routes found: the routes are read wrongly"
