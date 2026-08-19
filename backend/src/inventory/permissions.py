"""Who may do what, where one endpoint serves both populations.

Decision 0012 splits this API's audience in two: volunteers append without
signing in, and every operation that edits what is already recorded belongs to
somebody identified. Decision 0014 point 2 then puts the second set in this
API rather than in a second application, so a single catalogue endpoint is now
read by one population and written by the other, and the split has to be
expressible on the view itself.
"""

from typing import TYPE_CHECKING

from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.request import Request

if TYPE_CHECKING:
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
# that they are one list rather than two identical literals, so that a later
# addition to the default -- the step-up of decision 0014 point 5, on
# inventory-tng-oji -- reaches them, and so that a test can assert nothing else
# is on it. What stands in for the credential they do not ask for is the rate
# limiting in inventory/throttling.py.
VOLUNTEER_APPEND = [IsAuthenticated]


def administrators_only(view: APIView, method: str) -> bool:
    """Whether this view refuses ``method`` to anybody but an administrator.

    Asked of a view *instance*, because a view may build its permission list
    rather than declare it.

    The one answer to that question. Three things ask it -- the schema, which
    says so in the operation's description; the capability map, whose audit
    walks every route looking for operations no capability names; and the
    audit's own assertion -- and asking it three ways is how one of them comes
    to disagree. Written as a predicate over the permissions the view actually
    builds, so a second staff-gating class (the step-up of decision 0014 point
    5, on inventory-tng-oji) is added here and is seen everywhere at once.
    """
    if method.upper() in SAFE_METHODS:
        return False
    return any(isinstance(permission, StaffWrites) for permission in view.get_permissions())
