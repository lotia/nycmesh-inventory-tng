"""Tests for GET /api/me: what the caller is, and what this server will let them do.

Decision 0014 point 3 makes this the answer the interface renders its
administrative controls from, so what matters here is that the answer tracks
the endpoints rather than restating them: a capability is reported true
exactly when the operation behind it would be permitted.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from rest_framework.permissions import SAFE_METHODS

from inventory.permissions import RecentlyAuthenticated, StaffWrites, administrators_only
from inventory.tests.helpers import post
from inventory.views import CAPABILITIES
from inventory_tng import urls

pytestmark = pytest.mark.django_db


def me(client: Client) -> dict:
    response = client.get(reverse("me"))
    assert response.status_code == 200, response.content
    return response.json()


def test_a_caller_with_no_session_gets_an_answer_not_a_refusal(client: Client) -> None:
    """The volunteer app calls this on load and never signs in (decision 0012).

    Handling an error to learn that a session is an ordinary one would make
    every load of the app a failure path.
    """
    client.logout()
    body = me(client)
    assert body["authenticated"] is False
    assert body["username"] is None
    assert body["administrator"] is False


def test_an_administrator_is_named_and_declared(client: Client, administrator: User) -> None:
    client.force_login(administrator)
    body = me(client)
    assert body["authenticated"] is True
    assert body["username"] == administrator.get_username()
    assert body["administrator"] is True


def test_an_ordinary_session_is_not_an_administrator(client: Client) -> None:
    """The conftest client is signed in, and holds no staff flag."""
    body = me(client)
    assert body["authenticated"] is True
    assert body["administrator"] is False


def test_every_capability_is_reported_every_time(client: Client) -> None:
    """A missing one would be indistinguishable from one this server never had."""
    assert set(me(client)["capabilities"]) == set(CAPABILITIES)


def test_editing_the_catalogue_is_offered_only_to_an_administrator(
    client: Client,
    editor: Client,
) -> None:
    assert me(client)["capabilities"]["edit_catalogue"] is False
    assert me(client)["capabilities"]["revoke_label"] is False
    assert me(editor)["capabilities"]["edit_catalogue"] is True
    assert me(editor)["capabilities"]["revoke_label"] is True


def test_a_volunteers_own_operations_are_offered_to_an_ordinary_session(client: Client) -> None:
    assert me(client)["capabilities"]["append_stock"] is True
    assert me(client)["capabilities"]["add_volunteer"] is True


@pytest.mark.parametrize("capability", sorted(CAPABILITIES))
def test_nothing_is_offered_to_a_caller_with_no_session(client: Client, capability: str) -> None:
    """Every endpoint but the index and the health check still requires one.

    This is the assertion that will change when decision 0012 point 3 is
    implemented and the two volunteer endpoints stop asking for a session --
    and it will change here without anybody editing the answer, because the
    answer is the endpoints' own permission classes.
    """
    client.logout()
    assert me(client)["capabilities"][capability] is False


def test_the_answer_agrees_with_the_endpoint_behind_it(client: Client) -> None:
    """The claim under test: reporting a capability is asking the real question."""
    assert me(client)["capabilities"]["edit_catalogue"] is False
    refused = post(client, "items", {"name": "x"})
    assert refused.status_code == 403


def test_no_administrators_endpoint_is_missing_from_the_answer() -> None:
    """The map is a vocabulary, and this is what stops it becoming a stale list.

    Decision 0014 point 3 promises the interface renders its controls from this
    answer. A new endpoint guarded by StaffWrites and named by no capability
    would be a control the server permits and the interface never offers, and
    nothing about /api/me itself would notice.
    """
    named = {(operation.view, operation.method) for operations in CAPABILITIES.values() for operation in operations}
    for pattern in urls.urlpatterns:
        view = getattr(pattern.callback, "cls", None)
        if view is None:
            continue
        # By method, not by view: a capability naming a view's PATCH says
        # nothing about a DELETE added to it later. `http_method_names` is
        # what a view *allows*, so it is narrowed to the ones it handles the
        # way Django's own _allowed_methods narrows it.
        for method in (name.upper() for name in view.http_method_names if hasattr(view, name)):
            if not administrators_only(view(), method):
                continue
            assert (view, method) in named, (
                f"{method} {view.__name__} is reserved for administrators and no capability names it"
            )


def test_nothing_but_the_two_volunteer_writes_is_open_to_a_volunteer() -> None:
    """The audit above runs in the safe direction; this one runs in the other.

    A new view written ``permission_classes = [IsAuthenticated]`` on a POST
    opens a write to anybody with a session, and every consumer of
    ``administrators_only`` agrees that it is fine -- they all read the same
    permission list, so they fail together. Decision 0012's "any new endpoint
    reachable without a credential has to be argued against this record" needs
    something that does not.
    """
    open_writes = {
        (view.__name__, method)
        for view in (getattr(pattern.callback, "cls", None) for pattern in urls.urlpatterns)
        if view is not None
        for method in (name.upper() for name in view.http_method_names if hasattr(view, name))
        if method not in SAFE_METHODS and not administrators_only(view(), method)
    }
    assert open_writes == {
        ("VolunteerListCreateView", "POST"),
        ("StockTransactionCreateView", "POST"),
    }, "a write was opened to volunteers; argue it against decision 0012 before widening this"


def test_a_session_that_must_sign_in_again_is_told_which_no_it_is(stale: Client) -> None:
    """A capability says what the caller may do *now*, so a false one is ambiguous.

    The interface has to tell "this is not yours" from "not until you sign in
    again": the first is a control to hide and the second is a prompt to show
    somebody who is entitled to it. Decision 0014 points 3 and 5 together.
    """
    answer = me(stale)

    assert answer["administrator"] is True
    assert answer["recently_authenticated"] is False
    assert answer["capabilities"]["edit_catalogue"] is False
    # Appending is deliberately not affected, so it stays true.
    assert answer["capabilities"]["append_stock"] is True


def test_a_session_that_just_signed_in_is_recent_enough(editor: Client) -> None:
    answer = me(editor)

    assert answer["recently_authenticated"] is True
    assert answer["capabilities"]["edit_catalogue"] is True


def test_the_staff_flag_and_a_recent_sign_in_guard_exactly_the_same_operations() -> None:
    """The client infers "entitled but stale" from two fields, and that only works
    while the two permissions are co-extensive.

    StaleSession offers to restore *every* hidden control when the session is
    an administrator's and no longer recent. A view guarded by StaffWrites but
    not RecentlyAuthenticated would make that offer a lie -- signing in again
    would not bring the control back -- and every existing test would pass.
    """
    for pattern in urls.urlpatterns:
        view = getattr(pattern.callback, "cls", None)
        if view is None:
            continue
        guards = {type(permission) for permission in view().get_permissions()}
        assert (StaffWrites in guards) == (RecentlyAuthenticated in guards), (
            f"{view.__name__} is guarded by one of the pair and not the other"
        )


def test_the_prompt_points_at_a_route_that_exists() -> None:
    """frontend/src/admin/StepUp.tsx builds this path by hand.

    Nothing else ties the two together, so moving allauth's mount would 404 an
    administrator in the middle of an edit rather than failing a build.
    """
    assert reverse("account_reauthenticate") == "/accounts/reauthenticate/"
