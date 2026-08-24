"""Who may do what, where one endpoint serves both populations.

Decision 0012 splits this API's audience in two: volunteers append without
signing in, and every operation that edits what is already recorded belongs to
somebody identified. Decision 0014 point 2 then puts the second set in this
API rather than in a second application, so a single catalogue endpoint is now
read by one population and written by the other, and the split has to be
expressible on the view itself.
"""

from typing import TYPE_CHECKING, Any

from allauth.account.internal.flows.reauthentication import did_recently_authenticate
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission, IsAuthenticated
from rest_framework.request import Request

if TYPE_CHECKING:
    from django.http import HttpRequest

    # Not imported at runtime. Django resolves DEFAULT_PERMISSION_CLASSES by
    # importing this module, and it does so while rest_framework.views is
    # still initialising, so naming that module here at import time is a
    # circular import. Annotations are evaluated lazily (PEP 649), so the name
    # is only ever needed by a type checker.
    from rest_framework.views import APIView


def is_administrator(user: object) -> bool:
    """Whether this caller holds the staff flag.

    Read with ``getattr`` and stated once. Django types a request's user as the
    union of a base model that carries no such flag and ``AnonymousUser``, so
    every reader of it needs this dance; doing it here means the views do not.
    """
    return bool(getattr(user, "is_staff", False))


class StaffWrites(BasePermission):
    """Reads are left to whatever else guards the endpoint; writes need staff.

    This is the project's default alongside ``IsAuthenticated``, so a write is
    reserved unless a view says otherwise -- decision 0012's "opening an
    endpoint up is a deliberate act", applied to writing as well as to reading.
    The two endpoints a volunteer needs opt out by naming their permissions;
    everything else is closed without anybody having to remember.

    A volunteer reaching a write gets a refusal rather than a hidden button
    (decision 0014 point 2), so the message says what is missing rather than
    pretending the endpoint is not there. Which operations carry this is
    declared in the schema; see ``PolicyAwareAutoSchema`` in inventory/api.py.
    """

    message = "This operation is reserved for administrators."

    def has_permission(self, request: Request, view: APIView) -> bool:
        return request.method in SAFE_METHODS or is_administrator(request.user)


# The two endpoints decision 0012 point 3 opens to a volunteer, and the only
# things in this API that may be written without the staff flag. Named once so
# that they are one list rather than two identical literals, so that what these
# two opt out of is decided in one place -- both StaffWrites and the step-up of
# decision 0014 point 5 below -- and so that a test can assert nothing else is
# on it. What stands in for the credential they do not ask for is the rate
# limiting in inventory/throttling.py.
VOLUNTEER_APPEND = [IsAuthenticated]


def recently_authenticated(request: Request | HttpRequest) -> bool:
    """Whether this session proved who it was recently enough to change things.

    Memoised on the underlying request, which is what makes ``/api/me``
    affordable: allauth answers by asking, per authenticator type, whether the
    user has one -- three queries -- and that endpoint asks the question once
    per capability it reports. Nine capabilities plus its own answer was
    thirty-one identical selects for one page load.

    Takes either request: allauth reads only the session and the user, which
    both carry, and the admin middleware has only Django's.

    Stashed on the Django request rather than the DRF one because the
    capability probe wraps the DRF request in its own object per operation
    (``CapabilityProbe``), and they all share the one underneath.
    """
    underneath: Any = getattr(request, "_request", request)
    answer = getattr(underneath, "_recently_authenticated", None)
    if answer is None:
        answer = did_recently_authenticate(request)
        underneath._recently_authenticated = answer
    return bool(answer)


class RecentlyAuthenticated(BasePermission):
    """A destructive operation asks who you are again, inside a valid session.

    Decision 0014 point 5, and its consequences say why this is a requirement
    of that decision rather than general good practice. Every write reserved
    to an administrator prompts again -- editing the catalogue, merging
    volunteers, revoking labels, and minting the codes a sheet of stickers is
    printed from. Appending to the ledger deliberately does not, which is
    point 5's own line.

    Paired with StaffWrites in the project default, so the set that needs a
    second look is exactly the set that needs the staff flag, and the two
    endpoints a volunteer writes to opt out of both at once -- see
    VOLUNTEER_APPEND.

    One limit, stated because it is not obvious: allauth answers "yes" for an
    account that has neither a usable password nor a second factor -- one that
    only ever arrives through a provider -- because there is nothing here to
    ask it. Such an account inherits whatever its provider enforces, which is
    the position decision 0013 point 3 already takes.
    """

    message = "Sign in again to make this change."
    # DRF passes this into the PermissionDenied it raises, so the refusal
    # carries it without anybody having to read the sentence back. See
    # inventory/api.py.
    code = "reauthentication_required"

    def has_permission(self, request: Request, view: APIView) -> bool:
        # A PREDICATE AND NOTHING ELSE. What a stale session costs is recorded
        # where the refusal actually happens -- `inventory.api.exception_handler`
        # -- and not here, because this is not asked only by a request being
        # refused. `CurrentUserView` runs every permission class against a
        # probe to answer what a caller MAY do, so a record written here made
        # one `GET /api/me` by a stale administrator emit four refusals for
        # operations nobody attempted, on every page load.
        return request.method in SAFE_METHODS or recently_authenticated(request)


def administrators_only(view: APIView, method: str) -> bool:
    """Whether this view refuses ``method`` to anybody but an administrator.

    Asked of a view *instance*, because a view may build its permission list
    rather than declare it.

    The one answer to that question. Three things ask it -- the schema, which
    says so in the operation's description; the capability map, whose audit
    walks every route looking for operations no capability names; and the
    audit's own assertion -- and asking it three ways is how one of them comes
    to disagree. Written as a predicate over the permissions the view actually
    builds, so a second staff-gating class would be added here and be seen
    everywhere at once.

    RecentlyAuthenticated is deliberately not one: it asks *when* this session
    last proved who it was, not who they are, and an administrator whose
    session went stale is refused an operation that is still theirs. Adding it
    here would put "requires an administrator" on the description of an
    operation for a different reason than the sentence gives.
    """
    if method.upper() in SAFE_METHODS:
        return False
    return any(isinstance(permission, StaffWrites) for permission in view.get_permissions())


def open_to_anybody(view: APIView) -> bool:
    """Whether this view asks nothing at all of the caller.

    True of every endpoint that has to answer before anybody has signed in, or
    finished signing in: the index that hands out the CSRF cookie, the two
    checks the cluster probes, ``/api/me``, whose whole job is to say what the
    caller is, and the credential-free reports and descriptions beside them.

    Deliberately not a count. It carried one until this sentence replaced it,
    and the count was wrong by five: two changes in a row incremented it
    instead of recounting, which is what a number nothing checks does. The
    enumeration belongs in a test that fails when it stops being true, and
    that is inventory-tng-lb95.

    One predicate rather than an ``AllowAny`` check repeated wherever the
    question comes up -- the schema asks it to decide whether an operation can
    answer 403, and RequireSecondFactor asks it to decide what an unfinished
    session may still reach. Those two must agree: an endpoint that documents
    no refusal and then refuses is a lie in the contract.
    """
    return all(isinstance(permission, AllowAny) for permission in view.get_permissions())
