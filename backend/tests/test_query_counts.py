"""No page reads row by row (inventory-tng-du6): CONTRIBUTING.md, Running
the checks, says what this measures and what a new page needs here. And
every page names items and packages as shown (inventory-tng-ist.3)."""

import json
import re
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.core.db import lock_catalogue
from inventory_tng.models.catalogue import Item, ItemPackage

from helpers.pages import CATALOGUE, PAGES, Built, More, Page, Path, built, path
from helpers.queries import counting

# A world with one of what each page lists, and one with eleven, each built
# once for the module. Each holds every page's more, so a page's own more is
# checked alone, in the visit.


@pytest.fixture(scope="module")
def one(migrated_template: str) -> Iterator[Built]:
    with built(migrated_template, 1) as world:
        yield world


@pytest.fixture(scope="module")
def eleven(migrated_template: str) -> Iterator[Built]:
    with built(migrated_template, 11) as world:
        yield world


def test_every_admin_page_is_measured(
    make_app_without_database: Callable[..., FastAPI],
) -> None:
    """By both sweeps, here and test_phrases, or each says why not."""
    pages = {
        route.path
        for route in iter_route_contexts(make_app_without_database().routes)
        if "GET" in (route.methods or ()) and (route.path or "").startswith("/admin")
    }
    assert pages, "no admin pages found: the routes are read wrongly"
    unmeasured = pages - PAGES.keys()
    assert not unmeasured, f"add these to helpers.pages.PAGES: {unmeasured}"
    gone = PAGES.keys() - pages - {CATALOGUE}
    assert not gone, f"no such pages: {gone}"
    for route, page in PAGES.items():
        assert bool(page.more) != bool(page.uncounted), (
            f"{route}: give what it lists (more), or why it lists nothing that "
            "grows (uncounted), not both"
        )
        worded = bool(page.worded or page.signed_out)
        assert worded != bool(page.unworded), (
            f"{route}: give where its words are read (worded, signed_out), or "
            "why they aren't (unworded), not both"
        )
        assert page.more or not page.counted, (
            f"{route}: where it is counted (counted) is never read without what "
            "it lists (more)"
        )
    names = [name for page in PAGES.values() for name in page.worded]
    twice = {name for name in names if names.count(name) > 1}
    assert not twice, f"each worded page needs a name of its own: {twice}"
    assert set(names) >= SHOW_NO_ITEM, "SHOW_NO_ITEM names a place not worded"


def ways(route: str, page: Page) -> tuple[Path, ...]:
    """Each way a page is counted: none without a more."""
    if not page.more:
        return ()
    return page.counted or (route,)


# Each counted page, each way it is counted.
CASES = [
    pytest.param(
        route,
        where,
        # A route's second way and on, by a function, by its number.
        id=where if isinstance(where, str) else f"{route}{f' {n}' if n else ''}",
    )
    for route, page in sorted(PAGES.items())
    for n, where in enumerate(ways(route, page))
]


# What adds one more of what each counted page lists.
MORES: dict[str, More] = {
    route: page.more for route, page in PAGES.items() if page.more
}


@pytest.mark.parametrize(("route", "where"), CASES)
def test_a_page_reads_alike_for_one_row_or_eleven(
    route: str,
    where: Path,
    one: Built,
    eleven: Built,
) -> None:
    def read(client: TestClient, session: Session, url: str) -> tuple[int, str]:
        """The statements the page sent, and what it lists (listed)."""
        # Warmed first: what is read once and kept, such as the reading
        # rules (models.catalogue.RULES_INFO).
        ok(client, url)
        # As a request's own session would start: nothing loaded.
        session.flush()
        session.expire_all()
        with counting(session) as sent:
            response = ok(client, url)
        return len(sent), listed(response)

    device = route == CATALOGUE
    with one.visit(device=device) as (client, session):
        few, shown = read(client, session, path(where, one.world))
        # One more of what the page lists, by its own more, alone: the page
        # shows it, so what each world holds is what it lists.
        world = one.world.on(session)
        MORES[route](world)
        url = path(where, world)
        grown = ok(client, url)
    assert len(listed(grown)) > len(shown), (
        f"{route}: what its more adds is not shown at {url}"
    )
    with eleven.visit(device=device) as (client, session):
        many, longer = read(client, session, path(where, eleven.world))
    # Else the eleven-row world is not built of what the page lists.
    assert len(longer) > len(shown), f"{route} shows no more for eleven rows"
    assert many == few, f"{route}: {few} statements for one row, {many} for eleven"


def ok(client: TestClient, url: str) -> Response:
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    return response


def is_json(response: Response) -> bool:
    return response.headers["content-type"].startswith("application/json")


def listed(response: Response) -> str:
    """A page without its masthead, whose badges count what other pages
    list (_masthead.html). A reply in JSON has none."""
    if is_json(response):
        return response.text
    page, cut = re.subn(
        r'<header class="masthead">.*?</header>', "", response.text, flags=re.DOTALL
    )
    assert cut == 1, f"{response.url}: no masthead to cut, or more than one"
    return page


# Pages that name no item or package, and why the one that does names them
# as typed.
NAMES_NO_ITEM = {
    "/admin/account",
    "/admin/volunteers",
    "/admin/volunteers/suggest",
    "/admin/volunteers/{volunteer_id}",
    "/admin/locations",
    "/admin/locations/{location_id}",
    "/admin/volunteers/{volunteer_id}/merge",
    "/admin/unit-readings",
    "/admin/unit-readings/{rule_id:int}",
    "/admin/devices",
    "/admin/devices/all",
    "/admin/devices/{device_id:int}",
    "/admin/devices/{device_id:int}/revoke",
    "/admin/devices/codes/{code_id}",
    "/admin/devices/codes/{code_id}/revoke",
    "/admin/admins",
    "/admin/admins/{admin_id}",
    "/admin/admins/{admin_id}/reset",
    "/admin/settings",
    "/admin/authenticators/new",
    "/admin/categories",
    # Counts list places, and what was counted: none yet.
    "/admin/counts",
}
# Its names are evidence of how a unit was typed.
NAMES_AS_TYPED = {"/admin/unit-readings/seed"}


# Places words are read (worded) that show no item or package to check.
SHOW_NO_ITEM = {"stock, filtered to nothing", "a retired category"}
# Each counted page, each way it is counted, and each other place its words
# are read, by that name. Left out too: forms read signed out, which name
# nothing, and the redirect and the form only another page sends one to.
NAMED = CASES + [
    pytest.param(route, where, id=name)
    for route, page in sorted(PAGES.items())
    for name, where in page.worded.items()
    if name not in SHOW_NO_ITEM and where not in ways(route, page)
]


@pytest.mark.parametrize(("route", "where"), NAMED)
def test_every_page_names_items_and_packages_as_shown(
    route: str,
    where: Path,
    one: Built,
) -> None:
    """Each name ending in `1,000ft`, as typed, is read as `1000 ft` with a
    no-break space (models.names.KeyedName.name_shown), on every page and the
    scan page's catalogue; only a form's field holds it as typed (Ali,
    2026-11-15, inventory-tng-ist.3)."""
    with one.visit(device=route == CATALOGUE) as (client, session):
        lock_catalogue(session)
        for row in [
            *session.scalars(select(Item)),
            *session.scalars(select(ItemPackage)),
        ]:
            row.name = f"{row.name} 1,000ft"
        session.flush()
        url = path(where, one.world)
        response = client.get(url)
    assert response.status_code == 200, url
    if is_json(response):
        reply = response.json()
        # What is searched holds names as typed, matched, never shown.
        for item in reply.get("items", []) if isinstance(reply, dict) else []:
            item.pop("search_text")
        page = json.dumps(reply, ensure_ascii=False)
    else:
        # A form's fields, and under a name's how its units typed are read
        # (_units_read.html), as typed.
        page = re.sub(r' value="[^"]*"', "", response.text)
        page = re.sub(r'<div class="hint">.*?</div>', "", page, flags=re.DOTALL)
    if route in NAMES_AS_TYPED:
        return
    assert "1,000ft" not in page, url
    if route not in NAMES_NO_ITEM:
        assert "1000\u00a0ft" in page, url
