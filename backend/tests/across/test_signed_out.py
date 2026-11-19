"""Every administrator page and form sends one not signed in to sign in,
whether or not its route takes the signed-in administrator: app.py includes
every administrator router but the sign-in and reset ones under the guard
(guards.admin_signed_in)."""

import re
from collections.abc import Callable
from typing import cast

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from inventory_tng.admins import paths, reset_routes, sign_in_routes
from inventory_tng.admins.guards import admin_signed_in
from inventory_tng.web.paths import ADMIN

from helpers.swept_pages import PAGES

# Open to anyone, as they are how one signs in.
OPEN = {path for path, page in PAGES.items() if page.signed_out}


def test_every_admin_route_sends_one_signed_out_to_sign_in(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    client = make_client_without_database()
    asked = []
    for route in iter_route_contexts(cast(FastAPI, client.app).routes):
        path = route.path or ""
        if not path.startswith(ADMIN) or path in OPEN:
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in sorted(set(route.methods or ()) - {"HEAD"}):
            location = client.request(method, url).headers.get("location", "")
            assert location.startswith(paths.SIGN_IN), f"{method} {path}"
            asked.append(path)
    assert len(asked) > 100, "too few admin routes found: the routes are read wrongly"


def test_only_the_sign_in_and_reset_pages_are_outside_the_guard(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    """One signed out or not yet set up with TOTP must reach these, so the
    guard, which sends the second to set TOTP up, must not stand before them."""
    client = make_client_without_database()
    open_modules = {sign_in_routes.__name__, reset_routes.__name__}
    for route in iter_route_contexts(cast(FastAPI, client.app).routes):
        if route.endpoint is None:
            continue
        guarded = admin_signed_in in (
            d.dependency for d in getattr(route, "dependencies", ())
        )
        if route.endpoint.__module__ in open_modules:
            assert not guarded, route.path
        elif (route.path or "").startswith(ADMIN):
            assert guarded, route.path
