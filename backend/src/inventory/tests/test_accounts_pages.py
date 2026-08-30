"""The pages a person meets before they meet anything else.

inventory-tng-u1am: every page under `/accounts/` rendered as bare
browser-default HTML -- a "Menu:" bullet list, a serif heading, an unstyled
password box -- because allauth ships its templates deliberately unstyled and
this project had never put a frame around them. It had looked like that since
the day allauth was added.

It matters more than it looks. This is the first screen anybody sees, README
sends a new contributor here before anything else, and it is the one surface
where the application asks for a credential -- which is exactly where looking
broken costs the most.

What is asserted here is the arrangement rather than the appearance. Whether
the result is handsome is not a thing a test can hold; whether every page asks
for the stylesheet, whether the override is reached at all, and whether the way
out is still on the page are, and each of them is a way this could regress to
what it was without anybody noticing.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from inventory.tests.conftest import ADMINISTRATOR_PASSWORD as PASSWORD
from inventory.tests.helpers import start_local_sign_in

# Every test here renders a page, which needs a session table -- except the two
# at the bottom, which only look at the filesystem and say so.
pytestmark = pytest.mark.django_db

# Where the two stylesheets live before collectstatic moves them. The theme is
# generated -- frontend/scripts/theme-css.ts -- and the frame is written by
# hand; both are served from the same place for the same reason.
STATIC = Path(settings.BASE_DIR) / "inventory" / "static" / "accounts"

# Reached without signing in, which is most of what matters: these are the
# pages somebody meets when they have nothing yet.
OPEN_TO_ANYBODY = ["account_login", "account_signup", "account_reset_password"]
# Reached mid sign-in, which is where the unstyled prompt in the original
# report actually came from.
PART_WAY_IN = ["mfa_activate_totp", "account_reauthenticate", "account_logout"]


def asked_for(page: str, stylesheet: str) -> bool:
    """Whether the page links that stylesheet, hashed or not.

    A plain substring would be an assertion that only holds in the arrangement
    these tests happen to run in. Where collectstatic HAS run, `{% static %}`
    resolves to `accounts/theme.<hash>.css` -- so a test written against the
    bare name could never pass against what a deployment actually serves,
    while looking exactly like one that could.
    """
    stem, dot, extension = stylesheet.rpartition(".")
    return re.search(rf"accounts/{re.escape(stem)}(\.[0-9a-f]+)?{re.escape(dot + extension)}", page) is not None


@pytest.mark.parametrize("name", OPEN_TO_ANYBODY)
def test_a_page_reached_with_nothing_is_styled(name: str) -> None:
    page = Client().get(reverse(name)).content.decode()

    assert asked_for(page, "theme.css"), (
        f"{name} asks for no theme stylesheet, so it renders as browser-default HTML -- which is the "
        "state u1am was filed about"
    )
    assert asked_for(page, "accounts.css")


@pytest.mark.parametrize("name", PART_WAY_IN)
def test_a_page_reached_part_way_in_is_styled(name: str, administrator: User) -> None:
    """Including the one in the original screenshot.

    These are reached through the middleware that holds an unfinished session,
    so a change that exempted them from the layout rather than from the
    requirement would show up here and nowhere else.
    """
    page = start_local_sign_in(administrator, PASSWORD).get(reverse(name)).content.decode()

    assert asked_for(page, "theme.css"), f"{name} is served without the stylesheet the others get"


def test_the_override_is_reached_rather_than_allauth_s_own() -> None:
    """`inventory` is listed AFTER `allauth` in INSTALLED_APPS.

    So an `allauth/layouts/base.html` placed in the app's own template
    directory would lose to the file it was written to replace, silently and
    with no error anywhere. The project directory in `TEMPLATES["DIRS"]` is
    what makes the override win, and this is what fails if somebody tidies it
    away -- the wordmark appears in no template allauth ships.
    """
    page = Client().get(reverse("account_login")).content.decode()

    assert "NYC Mesh Inventory" in page
    assert "Menu:" not in page, (
        "allauth's own layout is being rendered, so TEMPLATES['DIRS'] no longer reaches the override"
    )


def test_the_way_out_is_still_on_the_page(administrator: User) -> None:
    """The nav is not decoration.

    On a page reached mid sign-in it is the only way to sign out or to reach
    the other allauth pages, and a frame that dropped it while making things
    tidier would strand somebody on the enrolment screen.
    """
    page = start_local_sign_in(administrator, PASSWORD).get(reverse("mfa_activate_totp")).content.decode()

    assert reverse("account_logout") in page


@pytest.mark.django_db(transaction=False)
@pytest.mark.parametrize("stylesheet", ["theme.css", "accounts.css"])
def test_the_stylesheet_is_there_to_be_collected(stylesheet: str) -> None:
    """A page asking for a file nothing serves is the o1uj.1 failure again.

    These live under an installed app's `static/`, so `collectstatic` finds
    them the same way it finds the admin's own CSS, and WhiteNoise serves them
    for the same reason. Asserted because "the template names it" and "the file
    exists" are separately true, and only the pair of them is a styled page.
    """
    assert (STATIC / stylesheet).is_file(), (
        f"{stylesheet} is missing from {STATIC}, so every /accounts/ page requests a stylesheet that "
        "404s and renders unstyled"
    )
