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
from django.urls import URLResolver, get_resolver, resolve, reverse
from rest_framework.permissions import SAFE_METHODS
from rest_framework.views import APIView

from inventory.permissions import (
    RecentlyAuthenticated,
    StaffWrites,
    administrators_only,
)
from inventory.tests import helpers
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
    for route in helpers.routes():
        view = route.view
        # DRF views only, which is what the `.cls` spelling used to select by
        # accident: DRF's permission machinery is what every assertion below
        # asks for, and Django's own views -- the admin's login redirect -- do
        # not have it. It stays narrow deliberately, and inventory-tng-2hbv is
        # where that was weighed rather than inherited: a capability is a name
        # the interface renders a control from, and the admin renders its own
        # controls from Django's permissions, so widening this would demand a
        # capability per admin view and teach /api/me a vocabulary none of its
        # callers speak. The audit that HAD to widen is the credential-free
        # one below, and it did.
        if view is None or not issubclass(view, APIView):
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
        for view in (route.view for route in helpers.routes())
        # DRF views only; see the note on the walk above.
        if view is not None and issubclass(view, APIView)
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
        # The fourth. It has to take no credential, because a device has
        # nothing to present until it has enrolled -- and what it writes holds
        # no person. The view says why the guard is the throttle rather than
        # the signature, and `inventory_tng.devices` says what the credential
        # is for and what it deliberately is not.
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
    belongs and everywhere else cites it.

    It used to be worse in a way worth recording, because the repair is what
    this test now is. It asked ``open_to_anybody``, which reads the permission
    classes a view NAMES, and it asked once per view rather than once per
    method. So a view that kept ``StaffWrites`` and dropped
    ``IsAuthenticated`` served every read to anybody and was invisible here --
    measured, not supposed, and the mutation in inventory-tng-2hbv's log shows
    the old question passing on exactly that view. Asking whether a request
    would be ADMITTED is the repair, and ``helpers.admits_anonymously`` is
    where it is argued.

    What remains uncovered is the other side of the mount: a route inside
    allauth's URLconf or the admin's that answers a stranger. The mount is
    asserted about instead, by the test below, and that test's docstring says
    plainly what that does and does not buy. It is a real gap, it is named,
    and it is not the shape that was leaking.
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
        "inventory.views.DeviceEnrolmentView": "nothing to present until it enrols; the throttle is the guard",
        "drf_spectacular.views.SpectacularAPIView": "describes this surface rather than being part of it",
        "drf_spectacular.views.SpectacularSwaggerSplitView": "renders that same description for a person",
        "django.views.generic.base.RedirectView": "sends the admin's own login form to allauth's; see the route",
    }
    assert all(argued.values()), "an entry with no reason is a name, and a name is not an argument"

    open_to_everybody = set()
    for route in helpers.routes():
        view = route.view
        if view is None:
            continue
        mounted = getattr(route.pattern.callback, "view_initkwargs", None) or {}
        # A view that is not DRF's has no permission layer to ask at all, so it
        # is open by construction rather than by declaration.
        if not issubclass(view, APIView):
            open_to_everybody.add(f"{view.__module__}.{view.__qualname__}")
            continue
        # ASKED PER METHOD, and of admission rather than of spelling.
        # `helpers.admits_anonymously` says why the old question was the wrong
        # one; asking it per method is the other half, because a view is open
        # if a stranger can reach ANY of what it offers -- and the class-level
        # question could not see a view that admits every GET and refuses
        # every POST, which is exactly the shape the defect took.
        offered = [name.upper() for name in view.http_method_names if hasattr(view, name)]
        if any(helpers.admits_anonymously(view, method, route.url, mounted) for method in offered):
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


def test_every_mount_this_urlconf_makes_is_one_the_record_argued() -> None:
    """What is mounted, rather than what is behind it.

    The walk above reads the routes this table declares itself, so the 43
    patterns behind ``accounts/`` and the 139 behind ``admin/`` are out of its
    sight: 20 routes audited of 203 that resolve. ``inventory-tng-2hbv``.

    THE ANSWER IS NOT TO WALK THEM, and ``helpers.routes`` carries the reason.
    The project owner's framing, and it is the right boundary: a dependency's
    sub-paths are its own business beyond the point where this repository
    mounts it.

    So the assertion is made AT the mount. Adding an `include()` is a
    deliberate act, rare, and this repository's own -- it is exactly the kind
    of thing that should need a sentence. A new one fails here until somebody
    writes why it is there and what it exposes.

    WHAT THIS DELIBERATELY DOES NOT CATCH, so that nobody reads it as more
    than it is: a route appearing behind an existing mount, in a dependency
    upgrade, that answers an anonymous caller. Both mounts here already do so
    by design -- a sign-in form must -- so the useful question is not "is
    anything open" but "did this upgrade open something that acts", and
    answering that means driving requests at a dependency's routes and reading
    what a refusal looks like in somebody else's framework. That is a larger
    piece of work than this issue, and pretending otherwise here would be
    worse than saying so.
    """
    mounted = {
        "admin/": "Django's own admin, which decision 0014 point 4 keeps complete; its own auth guards it",
        "accounts/": "allauth's sign-in surface, which must answer somebody with no session -- decision 0013",
    }
    assert all(mounted.values()), "an entry with no reason is a name, and a name is not an argument"

    found = {str(pattern.pattern) for pattern in urls.urlpatterns if isinstance(pattern, URLResolver)}

    unargued = sorted(found - set(mounted))
    assert not unargued, (
        f"{unargued} mount another URLconf into this one, and nothing here says what that exposes: "
        "argue each and name it above, or take the mount out"
    )
    stale = sorted(set(mounted) - found)
    assert not stale, f"{stale} are no longer mounted; delete the line rather than restore the mount"


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
    for route in helpers.routes():
        view = route.view
        # DRF views only; the audit above says why that stays narrow.
        if view is None or not issubclass(view, APIView):
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
    here. That is the test below, and it is why both exist: this one asks
    whether two registrations LOOK the same, and that one asks whether each
    one is the registration that actually answers.
    """
    # Only what this table declares itself, which is what the shared walk
    # yields: an `include()` mount is another URLconf's to keep unique, and
    # Django lets two of them share one prefix and falls through, so a repeated
    # prefix is not a repeated route.
    declared = helpers.routes()

    resolver = get_resolver()
    colliding = sorted({route.name for route in declared if len(resolver.reverse_dict.getlist(route.name)) > 1})
    assert not colliding, f"more than one route answers to reverse() by these names: {colliding}"

    counted = Counter(route.route for route in declared)
    repeated = sorted(route for route, count in counted.items() if count > 1)
    assert not repeated, f"registered more than once in urlpatterns: {repeated}"

    # The walk finding nothing would pass both assertions above it.
    assert len(counted) > 10, f"only {sorted(counted)} were found; the walk has stopped finding routes"


def test_every_registration_is_the_one_that_answers_its_own_url() -> None:
    """The property the comparison above cannot express.

    Two registrations spelled differently are two entries that compare
    unequal, so counting them finds nothing -- and one of them is dead, because
    only one can answer. Measured against this checkout when
    inventory-tng-s047 was filed, all three of these passed the test above:

        path("api/items/<int:id>", ...)   beside <int:pk>
        path("api/labels/<slug:code>", ...) beside <str:code>
        re_path(r"^api/schema$", ...)     beside path("api/schema", ...)

    Asking instead whether each registration is the one that ANSWERS its own
    URL catches all three, and would have caught the original ``api/schema``
    duplicate on its own: two ``as_view()`` calls are two distinct callables,
    so the loser resolves to the winner's and is visibly dead.

    Over the shared walk rather than a seventh of its own, which is the other
    half of that issue.
    """
    for route in helpers.routes():
        answered = resolve(route.url).func

        assert answered == route.pattern.callback, (
            f"{route.name} reverses to {route.url}, which is answered by a different callable -- so this "
            "registration is dead and something else is serving its URL. Two registrations spelled "
            "differently is how that happens."
        )
