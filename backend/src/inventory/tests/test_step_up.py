"""Tests for decision 0014 point 5: a second look before anything destructive.

Putting administrative capability in the volunteer app means script injected
into that app reaches the destructive operations too, from the browser of
somebody who legitimately holds them. That is the consequence decision 0014
records, and this is the mitigation it makes a requirement of the work rather
than a later hardening.

The line it draws is the point: every write reserved to an administrator asks
again -- editing the catalogue, merging volunteers, revoking labels, minting
new ones; appending to the ledger, which is what an administrator does most
often, does not.
"""

import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from inventory.models import Category, Item, Label, Location, StockTransaction, Volunteer
from inventory.tests.conftest import ADMINISTRATOR_PASSWORD
from inventory.tests.helpers import patch, post

pytestmark = pytest.mark.django_db


def refusal(response: Any) -> dict[str, Any]:
    assert response.status_code == 403, response.content
    return response.json()


# --------------------------------------------------------------------------
# What asks again
# --------------------------------------------------------------------------


def test_editing_the_catalogue_asks_again(stale: Client, item: Item) -> None:
    body = refusal(patch(stale, "item-detail", {"minimum_stock": "5"}, item.pk))

    assert body["code"] == "reauthentication_required"
    assert "sign in again" in body["detail"].lower()


def test_creating_a_catalogue_row_asks_again(stale: Client, category: Category) -> None:
    assert refusal(post(stale, "items", {"name": "New", "category": category.pk}))


def test_revoking_a_label_asks_again(stale: Client, item: Item) -> None:
    label = Label.objects.create(code="AB1234CD56", item=item)

    assert refusal(patch(stale, "label-resolve", {"revoked": True}, label.code))


def test_merging_a_volunteer_asks_again(stale: Client, volunteer: Volunteer) -> None:
    duplicate = Volunteer.objects.create(display_name="sean")

    assert refusal(patch(stale, "volunteer-detail", {"merged_into": volunteer.pk}, duplicate.pk))


# --------------------------------------------------------------------------
# What does not
# --------------------------------------------------------------------------


def test_appending_to_the_ledger_never_asks_again(
    stale: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """The thing an administrator does most often, and the thing that cannot be
    used to destroy anything: the ledger is append-only.
    """
    response = post(
        stale,
        "stock-transactions",
        {
            "kind": StockTransaction.Kind.RECEIPT,
            "actor": volunteer.pk,
            "movements": [{"item": item.pk, "quantity": "3", "to_location": warehouse.pk}],
        },
    )

    assert response.status_code == 201, response.content


def test_adding_a_volunteer_never_asks_again(stale: Client) -> None:
    """The other endpoint a volunteer writes to, and the same reasoning."""
    assert post(stale, "volunteers", {"display_name": "Olivia"}).status_code == 201


def test_reading_never_asks_again(stale: Client, item: Item) -> None:
    assert stale.get(reverse("items")).status_code == 200
    assert stale.get(reverse("item-detail", args=[item.pk])).status_code == 200


# --------------------------------------------------------------------------
# Which refusal it is
# --------------------------------------------------------------------------


def test_a_volunteer_is_refused_for_the_other_reason(client: Client, item: Item) -> None:
    """Two refusals share a status code and mean opposite things.

    A control to hide, and a prompt to show somebody who is entitled to the
    thing. A client that could not tell them apart would either hide a control
    from an administrator or offer one to a volunteer.
    """
    body = refusal(patch(client, "item-detail", {"minimum_stock": "5"}, item.pk))

    assert body["code"] == "forbidden"
    assert "administrators" in body["detail"]


def test_a_caller_with_no_session_is_told_in_the_same_field(client: Client, item: Item) -> None:
    """The third 403, which DRF renders from a different exception.

    Session authentication offers no challenge header, so "you did not say who
    you are" arrives with this status too. A code on some 403s and not on
    others is a field a client cannot branch on, so it carries one as well.
    """
    client.logout()

    assert refusal(patch(client, "item-detail", {"minimum_stock": "5"}, item.pk))["code"] == "forbidden"


def test_signing_in_again_is_enough_to_proceed(stale: Client, item: Item) -> None:
    """The prompt has somewhere to go, and coming back from it works."""
    assert refusal(patch(stale, "item-detail", {"minimum_stock": "5"}, item.pk))

    stale.post(reverse("account_reauthenticate"), {"password": ADMINISTRATOR_PASSWORD})

    assert patch(stale, "item-detail", {"minimum_stock": "5"}, item.pk).status_code == 200


# --------------------------------------------------------------------------
# What the browser is allowed to load
# --------------------------------------------------------------------------


def directives(response: Any) -> dict[str, str]:
    """The policy, as a map of directive to its value."""
    header = response["Content-Security-Policy"]
    return {part.split(" ", 1)[0]: part.split(" ", 1)[1] for part in header.split("; ") if " " in part}


def test_the_app_is_served_with_a_policy(client: Client) -> None:
    """A requirement of decision 0014, not general good practice.

    That decision puts administrative capability in the same application a
    volunteer uses and records the cost: script injected into it reaches the
    destructive operations too. This is what narrows how it gets there.
    """
    assert client.get(reverse("api-root")).has_header("Content-Security-Policy")


def test_no_script_may_be_inline_and_none_may_be_evaluated(client: Client) -> None:
    """The directive that actually stops injected script running.

    Read as a set of sources rather than as a string, because
    ``'wasm-unsafe-eval'`` -- which the test below is about -- contains
    ``unsafe-eval`` as a substring and means something else entirely.
    """
    sources = set(directives(client.get(reverse("api-root")))["script-src"].split())

    assert "'unsafe-inline'" not in sources
    assert "'unsafe-eval'" not in sources


def test_nothing_may_be_fetched_from_anywhere_else(client: Client) -> None:
    """The other half: a signed-in browser must not post the estate elsewhere."""
    policy = directives(client.get(reverse("api-root")))

    assert policy["connect-src"] == "'self'"
    assert policy["default-src"] == "'self'"
    assert policy["object-src"] == "'none'"
    assert policy["frame-ancestors"] == "'none'"


def test_style_is_the_one_exception_and_only_style(client: Client) -> None:
    """Emotion, which MUI renders through, injects style elements at runtime.

    Recorded rather than hidden: inline style cannot execute, and a nonce is
    the way out when somebody configures emotion's cache with one.
    """
    policy = directives(client.get(reverse("api-root")))

    assert "'unsafe-inline'" in policy["style-src"]
    assert [name for name, value in policy.items() if "'unsafe-inline'" in value] == ["style-src"]


def test_the_decoder_may_compile_its_webassembly(client: Client) -> None:
    """A policy that stops the camera reading anything is not a mitigation.

    Chromium refuses ``WebAssembly.instantiate`` under ``script-src`` without
    ``'wasm-unsafe-eval'``, however the binary was fetched, and the label
    decoder is WebAssembly (decision 0011 section 2). It is not an eval: no
    string becomes script.
    """
    sources = set(directives(client.get(reverse("api-root")))["script-src"].split())

    assert "'wasm-unsafe-eval'" in sources


NGINX_TEMPLATE = Path(settings.BASE_DIR).parent.parent / "frontend" / "nginx.conf.template"


def test_the_app_itself_is_served_with_the_same_policy() -> None:
    """The policy above covers what Django serves, which is not the app.

    ``index.html`` is nginx's, and a policy is enforced against the document
    that carries it -- so the volunteer app, which is the thing decision 0014
    is worried about, gets its policy from frontend/nginx.conf.template. The
    two are written out separately because neither server can read the other's
    configuration, and this is what stops them drifting.
    """
    header = re.search(
        r'add_header\s+Content-Security-Policy\s+"([^"]*)"',
        NGINX_TEMPLATE.read_text(),
    )
    assert header is not None, f"no Content-Security-Policy in {NGINX_TEMPLATE}"

    served = {part.split(" ", 1)[0]: part.split(" ", 1)[1] for part in header.group(1).split("; ")}
    declared = {name: " ".join(values) for name, values in settings.CONTENT_SECURITY_POLICY["DIRECTIVES"].items()}
    assert served == declared


@pytest.mark.usefixtures("_static_files_are_not_collected")
def test_the_documentation_page_loads_nothing_from_a_cdn(editor: Client) -> None:
    """A page that fetched its script from jsdelivr would render blank here.

    drf-spectacular's default. Serving Swagger UI's assets from this origin is
    the same argument decision 0011 section 2 makes for the label decoder, and
    under this policy it is also the difference between a page and a blank one.
    """
    page = editor.get(reverse("docs")).content.decode()

    assert "cdn.jsdelivr.net" not in page
    assert "/static/drf_spectacular_sidecar/" in page


@pytest.mark.usefixtures("_static_files_are_not_collected")
def test_the_documentation_page_needs_no_exception_from_the_policy(editor: Client) -> None:
    """Swagger UI normally boots from an inline block; the split view does not.

    So the one page that would have needed 'unsafe-inline' for script does not
    get it, and the policy is the same sentence everywhere.
    """
    page = editor.get(reverse("docs"))

    assert "'unsafe-inline'" not in directives(page)["script-src"]
    assert "<script>" not in page.content.decode()


# --------------------------------------------------------------------------
# The other interface decision 0014 point 5 covers
# --------------------------------------------------------------------------


def test_the_admin_asks_again_before_a_change(stale: Client, category: Category) -> None:
    """Decision 0014 point 4 keeps the admin complete, so it holds the same powers.

    The threat point 5 records is script in the volunteer app acting from an
    administrator's own browser. That script can read the CSRF token and post
    to /admin exactly as easily as to /api, and the network restriction of
    decision 0013 point 6 does not help, because it is that administrator's
    browser doing the posting.
    """
    response = stale.post(
        reverse("admin:inventory_category_change", args=[category.pk]),
        {"name": "Something else", "parent": ""},
    )

    assert response.status_code == 302
    assert reverse("account_reauthenticate") in response["Location"]
    category.refresh_from_db()
    assert category.name != "Something else"


@pytest.mark.usefixtures("_static_files_are_not_collected")
def test_the_admin_can_still_be_read(stale: Client, category: Category) -> None:
    """Reading is what somebody does before deciding to change anything."""
    assert stale.get(reverse("admin:inventory_category_changelist")).status_code == 200


def test_a_recent_session_changes_things_in_the_admin(editor: Client, category: Category) -> None:
    assert (
        editor.post(
            reverse("admin:inventory_category_change", args=[category.pk]),
            {"name": "Radios and antennas", "parent": ""},
        ).status_code
        == 302
    )
    category.refresh_from_db()
    assert category.name == "Radios and antennas"


def test_the_prompt_knows_where_to_go_back_to(stale: Client, category: Category) -> None:
    """An interruption that forgets what it interrupted is a worse one."""
    where = reverse("admin:inventory_category_change", args=[category.pk])

    sent = stale.post(where, {"name": "Something else", "parent": ""})["Location"]

    assert quote(where) in sent
