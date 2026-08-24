"""Tests for GET /api/me, whose own docstring says what it answers.

Decision 0014 point 3 makes this the answer the interface renders its
administrative controls from, so what matters here is that the answer tracks
the endpoints rather than restating them: a capability is reported true
exactly when the operation behind it would be permitted.

The last two are about the URL table those answers are audited against rather
than about the endpoint, and are here because this is where the walks over it
are.
"""

from collections import Counter

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import URLPattern, get_resolver, reverse
from rest_framework.permissions import SAFE_METHODS

from inventory.permissions import (
    RecentlyAuthenticated,
    StaffWrites,
    administrators_only,
    open_to_anybody,
)
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
    permission list, so they fail together. Decision 0012 requires a new
    credential-free endpoint to be argued against the record, and that needs
    something which does not.
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
        # The third, and the only one that writes no row: it records a failure
        # a volunteer's browser could not handle. Argued against decision 0012
        # in that record, under "A third endpoint, and what made it arguable".
        ("ClientFailureView", "POST"),
        # The fourth, and it is PROVISIONAL rather than argued: it exists only
        # while `VOLUNTEER_ACCESS` names an enrolling posture, so that
        # inventory-tng-81f7 can be argued from friction people have felt.
        # inventory-tng-81f7.4 removes it with the setting, and this line with
        # it. The view's own docstring carries the argument for the meantime.
        ("DeviceEnrolmentView", "POST"),
    }, "a write was opened to volunteers; argue it against decision 0012 before widening this"


def test_every_endpoint_asking_nothing_of_its_caller_is_one_the_record_argued() -> None:
    """The audit above sees writes only, which is how ``/api/livez`` slipped past it.

    ``method not in SAFE_METHODS`` is a filter over methods, so a view written
    ``permission_classes = [AllowAny]`` with nothing but a GET on it is not
    merely permitted there -- it is never looked at. inventory-tng-uq6 added
    exactly that and nothing in this repository said a word. Decision 0012's
    consequences are about what is reachable without a credential, and reading
    is most of what this API does; inventory-tng-81f7 is the open question of
    what such a caller may learn about a person, and it is open because the
    read surface was never the half anybody was watching.

    So this asks the view-level question instead and holds the whole answer
    against a list, each entry saying where its case was made rather than
    making it here. A line added to that list is the moment somebody has to
    write the next one, and inventory-tng-gnhl is about to ask for several.

    WHAT THIS DOES NOT COVER, stated here because this is the only place it
    belongs and everywhere else cites it. It is not the whole of decision
    0012's consequence, and neither that record nor DEVELOPERS.md claims it
    is. Two gaps, both measured, both inventory-tng-2hbv. ``open_to_anybody``
    reads the permission classes a view names rather than asking what an
    anonymous request would be admitted to, so a view that keeps
    ``StaffWrites`` and drops ``IsAuthenticated`` serves every read to
    anybody and is invisible here. And this walk reads the routes this table
    declares itself, so the patterns behind ``accounts/`` are out of its
    sight -- among them the sign-in and sign-up pages, which answer anybody
    by design and are nobody's decision to make twice.
    """
    # Keyed by where the class is defined, not by its bare name: half of these
    # are third-party and all of them are called something generic, so a
    # second HealthCheckView mounted beside the first would be one member of
    # this set and ship unargued. Every other consumer keys on the class
    # itself -- api.py on `self.view`, middleware.py on the resolved one.
    argued = {
        "inventory.views.ApiRootView": "the index, and the CSRF cookie a browser needs before it can sign in",
        "inventory.views.HealthCheckView": "the readiness probe; decision 0012 point 3's posture covered this one",
        "inventory.views.LivenessCheckView": "the second probe, argued in decision 0012 under 'A fourth'",
        "inventory.views.CurrentUserView": "says what the caller is, so demanding a session makes every load a failure",
        "inventory.views.ClientFailureView": "argued in decision 0012; the view's own docstring cites the passage",
        "inventory.views.DebugTraceVerifyView": "the signed token it is handed is the credential; see the view",
        "inventory.views.DeviceEnrolmentView": (
            "provisional: it hands a device the credential VOLUNTEER_ACCESS asks for, so it cannot ask for "
            "one, and it refuses under every posture that does not. inventory-tng-81f7.4 removes it"
        ),
        "drf_spectacular.views.SpectacularAPIView": "describes this surface rather than being part of it",
        "drf_spectacular.views.SpectacularSwaggerSplitView": "renders that same description for a person",
        "django.views.generic.base.RedirectView": "sends the admin's own login form to allauth's; see the route",
    }
    assert all(argued.values()), "an entry with no reason is a name, and a name is not an argument"

    open_to_everybody = set()
    for pattern in urls.urlpatterns:
        callback = pattern.callback
        # `cls` first, because that is what the other consumers here read, and
        # `view_class` after it, because a view that is not DRF's has only
        # that one -- and one such is in this table. An `include()` mount has
        # no callback at all and is another URLconf's to audit, which is where
        # allauth's names go; see the docstring.
        view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
        if view is None:
            continue
        # Built the way the URLconf builds it, from the attribute Django's own
        # `as_view` sets on every view function. `as_view(permission_classes=...)`
        # is legal and DRF applies it per request, so a route the table opened
        # on purpose reads as closed if the class is instantiated bare.
        mounted = getattr(callback, "view_initkwargs", None) or {}
        # A view that is not DRF's has no permission layer to ask at all, so
        # it is open by construction rather than by declaration.
        if not hasattr(view, "get_permissions") or open_to_anybody(view(**mounted)):
            open_to_everybody.add(f"{view.__module__}.{view.__qualname__}")

    # Two assertions, because the two directions call for opposite edits and
    # one sentence cannot say both. Tightening the read surface is planned
    # work -- inventory-tng-81f7 -- so the second is not the unlikely one.
    unargued = sorted(open_to_everybody - set(argued))
    assert not unargued, (
        f"{unargued} answer a caller holding no credential at all: argue each against "
        "decision 0012 and name it above with that argument, or give it a permission class"
    )
    stale = sorted(set(argued) - open_to_everybody)
    assert not stale, f"{stale} no longer answer such a caller; delete the line rather than restore the endpoint"


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


# --------------------------------------------------------------------------
# The table those audits walk
# --------------------------------------------------------------------------


def test_every_route_is_registered_exactly_once() -> None:
    """Here because the audits above are what a repeated registration deceives.

    A route listed twice is visited twice by every one of them: the audit that
    gathers a set of open writes reads the two entries as one, and the two
    written as plain loops re-run an identical assertion. Django hides the
    repeat as well -- it resolves the first registration and ``reverse()``
    answers the last, which agree exactly while the entries are identical. So
    nothing fails. The one walk that counts rather than gathers,
    ``test_telemetry.py``'s "more than ten routes were reached", had room to
    absorb it and went from 22 to 21 when the repeat was deleted; the next one
    written closer to the number would be off by one for a reason nobody would
    look for. ``api/schema`` was registered twice from 9e2e86a until
    inventory-tng-qdbm.

    Both halves of the property, because a repeat is not always a copy. The
    names half asks the resolver rather than this list, because the namespace
    ``reverse()`` reads is wider than the list: ``allauth`` mounts its own
    names into it unprefixed, which is how the test above reverses one of them
    bare. A name of ours colliding with one of theirs would send decision
    0013's redirect to a view it never meant, and counting only this list
    would not see it.

    What it cannot see is a second registration spelled differently --
    ``<int:id>`` beside ``<int:pk>``, or a ``re_path`` for a URL a ``path``
    already answers -- which is dead in the same way and compares unequal
    here. Catching that means reversing each registration and resolving it
    back, over a walk the audits here would share rather than one more of
    their own; inventory-tng-s047, which counts them.
    """
    # Only what this table declares itself: an `include()` mount is another
    # URLconf's to keep unique, and Django lets two of them share one prefix
    # and falls through, so a repeated prefix is not a repeated route.
    declared = [pattern for pattern in urls.urlpatterns if isinstance(pattern, URLPattern)]

    resolver = get_resolver()
    colliding = sorted(
        {
            pattern.name
            for pattern in declared
            if pattern.name is not None and len(resolver.reverse_dict.getlist(pattern.name)) > 1
        }
    )
    assert not colliding, f"more than one route answers to reverse() by these names: {colliding}"

    routes = Counter(str(pattern.pattern) for pattern in declared)
    repeated = sorted(route for route, count in routes.items() if count > 1)
    assert not repeated, f"registered more than once in urlpatterns: {repeated}"

    # The walk finding nothing would pass both assertions above it.
    assert len(routes) > 10, f"only {sorted(routes)} were found; the walk has stopped finding routes"
