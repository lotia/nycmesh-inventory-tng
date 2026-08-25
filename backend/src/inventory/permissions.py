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

from inventory_tng import devices

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


#: This request carries no device token at all, which is the ordinary case and
#: is never a refusal. Attribution is offered, not demanded --
#: `inventory_tng.devices` says why.
NO_DEVICE = "none"
#: A token this deployment honours, and a row that is still live.
DEVICE_ENROLLED = "enrolled"
#: A token that verifies against a row somebody has cut off. The one state that
#: refuses, and it wants a screen of its own rather than a wall.
DEVICE_REVOKED = "revoked"
#: A token that does not verify, or verifies to a row that is not there any
#: more. Told apart from `NO_DEVICE` so a client can throw away what it is
#: holding and enrol again instead of presenting a dead string for ever.
DEVICE_UNKNOWN = "unknown"

DEVICE_STATES = (NO_DEVICE, DEVICE_ENROLLED, DEVICE_REVOKED, DEVICE_UNKNOWN)


def presented_device(request: Request | HttpRequest) -> tuple[str, str]:
    """This request's device, as a state and the identifier it was carried under.

    TWO QUESTIONS, AND BOTH ARE ASKED EVERY TIME. The signature, which
    `inventory_tng.devices` checks and which is what stops a token being
    invented; and then the row, which is what `Device.revoked_at` acts on. The
    second is not an optimisation to be skipped later on the grounds that the
    first already proved something -- skipping it removes revocation entirely
    and fails no assertion about a token, which is why the test that holds this
    revokes a device and asks for the next request rather than inspecting what
    it is carrying.

    The identifier comes back even for a revoked or unknown device, because it
    is what the log line and the metric are worth anything for: "this device
    was refused" is only useful if it says which.

    MEMOISED on the underlying request, exactly as `recently_authenticated`
    below is and for the reason given there -- `CurrentUserView` runs the whole
    permission list once per capability it reports, and this would otherwise be
    one identical select per capability for a request that carries a token.
    Stashed on the Django request, because the capability probe wraps the DRF
    one per operation and they all share the one underneath.

    The model is imported here rather than at the top of the file, for the
    reason this module's header gives about how early Django reads it.
    """
    from inventory.models import Device

    underneath: Any = getattr(request, "_request", request)
    answered = getattr(underneath, "_presented_device", None)
    if answered is not None:
        return answered

    carried = request.headers.get(devices.HEADER, "").strip()
    identifier = devices.presented_on(request)
    if not identifier:
        # Nothing at all is the ordinary case. A string that will not verify is
        # `DEVICE_UNKNOWN`, so a client holding something this server has
        # stopped honouring -- a key rotated away from, a mangled copy -- is
        # told to throw it away rather than presenting it for ever.
        answered = (DEVICE_UNKNOWN, "") if carried else (NO_DEVICE, "")
    else:
        # `order_by()` because `Meta.ordering` would otherwise sort a
        # unique-index lookup of one row.
        row = Device.objects.filter(identifier=identifier).order_by().only("revoked_at").first()
        if row is None:
            answered = (DEVICE_UNKNOWN, identifier)
        elif row.revoked_at is not None:
            answered = (DEVICE_REVOKED, identifier)
        else:
            answered = (DEVICE_ENROLLED, identifier)

    underneath._presented_device = answered
    return answered


class DeviceNotRevoked(BasePermission):
    """A device somebody has cut off stops being answered, on its next request.

    This is the whole of what revocation buys, and it is deliberately the only
    thing a device credential decides. Carrying no token is not a refusal --
    the network does admission (`inventory-tng-2jzx`) and this is attribution
    -- so the state that refuses is `DEVICE_REVOKED` and nothing else.

    ITS OWN REASON ON THE WIRE. "You may not" and "this device was removed"
    are the same status code and want opposite screens: the first is a wall
    and the second is a button that enrols again. `code` is what carries that
    distinction through DRF, and a client reading it does not have to match on
    prose.

    THE SURFACES THAT STILL ANSWER A REVOKED DEVICE are the ones that name
    `AllowAny` for themselves -- the index, the two probes, `/api/me`, the
    failure report and the debug-trace check. That is deliberate rather than
    an oversight: `/api/me` is how a client finds out it has been cut off, and
    refusing the endpoint that says so would leave it guessing from a 403 on
    something else. None of them writes anything a device could be blamed for.
    """

    message = "This device has been removed. Enrol again to carry on."
    code = "device_revoked"

    def has_permission(self, request: Request, view: APIView) -> bool:
        state, _ = presented_device(request)
        return state != DEVICE_REVOKED


# The two endpoints decision 0012 point 3 opens to a volunteer, and the only
# things in this API that may be written without the staff flag. Named once so
# that they are one list rather than two identical literals, so that what these
# two opt out of is decided in one place -- both StaffWrites and the step-up of
# decision 0014 point 5 below -- and so that a test can assert nothing else is
# on it. What stands in for the credential they do not ask for is the rate
# limiting in inventory/throttling.py.
#
# `DeviceNotRevoked` rides along for the reason it is on the project default
# too: these two are the writes, so they are the last place a device somebody
# has cut off should go on being answered.
VOLUNTEER_APPEND = [IsAuthenticated, DeviceNotRevoked]


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

    Deliberately not a count, still. It carried one until this sentence
    replaced it, and the count was wrong by five: two changes in a row
    incremented it instead of recounting, which is what a number nothing
    checks does. What is checked now is the enumeration itself, in
    inventory/tests/test_capabilities.py, where every view this is true of is
    held against a list saying why each is on it. A count written back in here
    would be a second tally of that list, kept by hand, which is how the first
    one went wrong.

    This reads the classes a view names, which is narrower than asking what a
    request would be admitted to. That audit's docstring is where the
    difference is written down, and inventory-tng-2hbv is closing it.

    One predicate rather than an ``AllowAny`` check repeated wherever the
    question comes up -- the schema asks it to decide whether an operation can
    answer 403, and RequireSecondFactor asks it to decide what an unfinished
    session may still reach. Those two must agree: an endpoint that documents
    no refusal and then refuses is a lie in the contract.
    """
    return all(isinstance(permission, AllowAny) for permission in view.get_permissions())
