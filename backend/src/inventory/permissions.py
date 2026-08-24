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
from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.settings import api_settings
from rest_framework.throttling import BaseThrottle

from inventory_tng import postures

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


def client_address(request: Request) -> str:
    """The caller's address, or nothing where it cannot be trusted.

    THE READING IS DRF'S, borrowed from `BaseThrottle` rather than
    reimplemented: `NUM_PROXIES` hops counted back from the RIGHT of
    `X-Forwarded-For`, which is the part appended by proxies a request actually
    passed through. `.env.sample` is where that number's argument is written
    down, and a rate limit and a network rule reading it differently would be
    two definitions of one client.

    AND ONE THING ON TOP, which is not a second reading but a refusal to make
    the first one. DRF clamps: `addrs[-min(num_proxies, len(addrs))]`. So a
    header SHORTER than the configured hop count hands back an entry the
    caller wrote -- and with the shipped `NUM_PROXIES=2`,
    `curl -H 'X-Forwarded-For: 10.69.0.1'` was admitted by `mesh_only` from
    anywhere on the internet. That clamp is right for a throttle, where the
    cost of believing a forger is one bucket, and wrong for admission, where
    it is the roster. So a header too short for this deployment's proxies is
    treated as no address at all rather than as the caller's: `within` puts an
    empty string in no range. A request carrying no header is unaffected and
    still reads `REMOTE_ADDR`, which is what a development machine has.

    WHAT THIS DOES NOT DO, said here because the line above it is exactly the
    kind that gets read as "forgery closed". It counts entries, and entries are
    free: `X-Forwarded-For: 10.69.0.1,` -- one trailing comma -- is two entries
    and walks straight back through, measured. It stops the naive header and
    nothing cleverer, and it cannot do better, because no rule reading this
    header can tell how many proxies a request actually crossed. The sound
    version needs a list of addresses this deployment will believe a header
    from, which is a decision about the ingress rather than a line here, and it
    is `inventory-tng-3hgc`. Until then `mesh_only` is a demo posture and not
    an access control, which is what `inventory-tng-81f7` convenes to decide
    about anyway.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    hops = api_settings.NUM_PROXIES
    if forwarded is not None and hops and len(forwarded.split(",")) < hops:
        return ""
    return BaseThrottle().get_ident(request) or ""


def enrolled(request: Request) -> bool:
    """Whether this request carries a device token this deployment still honours.

    Two questions, and both have to answer yes: the signature, which
    `inventory_tng.postures` checks and which is what stops a token being
    invented, and the row, which is what `Device.revoked_at` acts on -- and the
    `Device` model is where the reason for that pair is written down.

    MEMOISED, exactly as `recently_authenticated` below is and for the same
    measured reason. `VolunteerAccess` is first in the default permission list
    so ``all()`` never short-circuits before it, ``CurrentUserView`` runs the
    whole list once per capability it reports, and ``enrolment_state`` asks
    again -- seven identical selects for one ``GET /api/me``, which were the
    only seven queries that request made. Stashed on the Django request
    underneath, because the capability probe wraps the DRF one per operation
    and they all share the one below.

    The model is imported here rather than at the top of the file: Django
    resolves ``DEFAULT_PERMISSION_CLASSES`` by importing this module, and this
    module's header says how early that is.
    """
    from inventory.models import Device

    underneath: Any = getattr(request, "_request", request)
    answer = getattr(underneath, "_enrolled_device", None)
    if answer is not None:
        return bool(answer)
    identifier = postures.presented_device(request.headers.get(postures.DEVICE_HEADER, ""))
    answer = bool(identifier) and Device.objects.filter(identifier=identifier, revoked_at__isnull=True).exists()
    underneath._enrolled_device = answer
    return answer


def enrolment_state(request: Request) -> str:
    """What this caller would have to do to be admitted, in one word.

    The vocabulary, and why ``/api/me`` answers this at all, is
    ``inventory_tng.postures.ENROLMENT_STATES``.

    ASKED IN THE ORDER ``VolunteerAccess`` ASKS IT, which is the correction
    this needed. That class admits anybody signed in before it looks at a
    device at all, and this branched on the posture alone -- so under an
    enrolling posture a signed-in administrator was answered ``code`` while
    the API was already answering their requests, and the client's gate
    replaced the whole application with an enrolment screen for somebody who
    needed nothing. Nothing is required of a caller the posture does not ask
    anything of.
    """
    posture = settings.VOLUNTEER_ACCESS
    if posture not in postures.ENROLLING or request.user.is_authenticated:
        return postures.NOT_REQUIRED
    if enrolled(request):
        return postures.ENROLLED
    return postures.ENROL_SELF if posture == postures.ENROLLED_SELF else postures.ENROL_WITH_CODE


class VolunteerAccess(BasePermission):
    """What a caller must have before this API answers them at all.

    PROVISIONAL, AND THE DEFAULT IS TODAY. Under ``VOLUNTEER_ACCESS=session``
    -- what a deployment that sets nothing gets -- this is exactly
    ``IsAuthenticated``, which is what stood here before and what decision 0012
    point 3 has not yet been implemented against. The other four values are the
    postures ``inventory-tng-81f7`` asks a room to choose between, and
    ``inventory-tng-81f7.4`` takes the loser out.

    Somebody signed in is admitted under every posture. An administrator has
    already proved more than any of these ask for, and a gate that locked one
    out of their own application while a device credential was being compared
    would be demonstrating something other than the posture.

    It is not ``AllowAny`` even when it admits everybody, and that is
    deliberate: ``open_to_anybody`` reads the classes a view names, so a view
    guarded by this stays a view that documents a 403 and stays inside the
    audit that decision 0012's consequence asks for.

    NO ``message``, and it is worth saying why rather than leaving the next
    reader to add one. The only caller this ever refuses is one with no
    session -- anybody signed in is admitted above -- and DRF answers exactly
    that caller with ``NotAuthenticated`` rather than ``PermissionDenied``,
    which carries no message of a permission's. A sentence here would be
    unreachable, and a sentence nothing can print is worse than none: it reads
    as a promise the API makes. What tells a client to offer enrolment is the
    ``enrolment`` field on ``/api/me``, which answers before anything has been
    refused.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        posture = settings.VOLUNTEER_ACCESS
        if posture == postures.OPEN:
            return True
        if request.user.is_authenticated:
            return True
        if posture in postures.ENROLLING:
            return enrolled(request)
        if posture == postures.MESH_ONLY:
            return postures.within(client_address(request), settings.VOLUNTEER_ACCESS_NETWORKS)
        return False


# The two endpoints decision 0012 point 3 opens to a volunteer, and the only
# things in this API that may be written without the staff flag. Named once so
# that they are one list rather than two identical literals, so that what these
# two opt out of is decided in one place -- both StaffWrites and the step-up of
# decision 0014 point 5 below -- and so that a test can assert nothing else is
# on it. What stands in for the credential they do not ask for is the rate
# limiting in inventory/throttling.py.
VOLUNTEER_APPEND = [VolunteerAccess]


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
