"""What makes decision 0013 point 3's "required" a fact rather than a wish.

allauth already asks for a TOTP code from anybody who has set one up: that is
its authenticate stage, and it is the whole of the second factor for an
account that has one. What it does not do is insist that a local account has
one in the first place, and an account with a password and no second factor is
exactly the case point 3 is about -- one stolen password and the system that
records the organisation's stock is somebody else's.

So a session that got here by typing a password, on an account with no TOTP
authenticator, is not finished. It can reach allauth's own pages, where it can
set one up or sign out, and nothing else.

Three things this deliberately keys on, none of them the obvious one:

- **How this session authenticated, not whether the account has a password.**
  A password is a way in that this account may keep for the day a provider is
  unreachable (point 2); using it is what obliges the second factor. Somebody
  who arrived through Google inherits whatever Google enforced, which is the
  rest of point 3.

- **Whether a TOTP authenticator exists, not whether one was used.** Where one
  exists, allauth's stage has already demanded a code before this session
  became authenticated at all. Asking again here would refuse the session that
  just passed.

- **allauth's pages by their views, not by their URLs.** The exemption follows
  wherever inventory_tng/urls.py mounts allauth, so moving it cannot silently
  lock the only escape route.

One thing this is not, and it matters at the cutover: it keys on positive
evidence, so a session with no authentication records at all -- one made by
``django.contrib.auth.login``, by ``force_login`` in a test, or simply one
that already existed before this shipped -- is waved through. It holds
password sessions that allauth made, not every session that ever was. Sessions
have to be flushed when this is deployed, or an administrator already signed in
keeps a password-only session for as long as it lives; docs/deployment.md says
so beside the release step.

The endpoints that ask nothing of anybody are exempt too, for the same reason
they are open at all: the index hands out the CSRF cookie the activation form
needs, the probes run before authentication exists, and ``/api/me`` exists
precisely so a client can be told what state it is in. Refusing the endpoint
whose job is to say "you have not finished signing in" would leave the
single-page app with nothing to render but an error.

Which endpoints those are is ``permissions.open_to_anybody``'s to answer and
is deliberately not listed here. This paragraph named three of them until one
more arrived and made it four, which is the point: the exemption is a class,
not a list, and a list kept in prose beside the code that does not read it is
one somebody eventually trusts. The enumeration that fails when it stops being
true is in inventory/tests/test_capabilities.py, and what it does not yet
reach is inventory-tng-2hbv.
"""

from collections.abc import Callable
from urllib.parse import quote

import structlog
from allauth.account.authentication import get_authentication_records
from allauth.mfa.models import Authenticator
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from rest_framework.permissions import SAFE_METHODS

from inventory import telemetry
from inventory.permissions import open_to_anybody, recently_authenticated

# Named for the module. Both refusals here are about who the caller is rather
# than about what they sent, which is what `inventory.telemetry`'s REFUSALS
# counts -- so a rise in either is visible without reading a log.
log = structlog.get_logger(__name__)

# The value allauth records for a username-and-password sign-in. Its social,
# MFA and login-by-code steps record their own names, so this identifies the
# local path and nothing else.
PASSWORD = "password"

# Said to a client that is not a browser navigating. Names what is missing
# rather than reporting a bare refusal, so a caller can tell "you have not
# finished signing in" from "this is not yours".
UNFINISHED = "Sign-in is not complete: this account signed in with a password and has no second factor set up yet."


class RequireSecondFactor:
    """Hold a password-only session at allauth's door until it has a TOTP key."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if self._is_unfinished(request) and not self._is_exempt(request):
            # A browser navigating is shown where to go. Anything else -- the
            # single-page app's own fetches, a script -- is told, because
            # following a redirect would hand it a page of HTML where it
            # asked for JSON and it would report the wrong problem.
            log.warning("sign-in unfinished", reason="no second factor")
            telemetry.REFUSALS.add(1, {"reason": "no_second_factor"})
            if "text/html" in request.headers.get("Accept", ""):
                return redirect("mfa_activate_totp")
            return JsonResponse({"detail": UNFINISHED}, status=403)
        return self.get_response(request)

    @staticmethod
    def _is_unfinished(request: HttpRequest) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        methods = {record.get("method") for record in get_authentication_records(request)}
        if PASSWORD not in methods:
            return False
        return not is_mfa_enabled(user, [Authenticator.Type.TOTP])

    @staticmethod
    def _is_exempt(request: HttpRequest) -> bool:
        """allauth's own pages, and the static files they are built from.

        Static first because Django serves it outside the URL configuration
        while ``DEBUG`` is on, so resolving the path would fail and the
        activation page would arrive with no styling at the one moment it has
        to be usable.
        """
        if request.path.startswith(settings.STATIC_URL):
            return True
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return False
        if match.func.__module__.startswith("allauth."):
            return True
        # Asked of the view rather than listed by path, so an endpoint opened
        # up in views.py is reachable here without anybody remembering to add
        # it. See inventory/permissions.py.
        view = getattr(match.func, "cls", None)
        return view is not None and open_to_anybody(view())


class RequireSecondLookInTheAdmin:
    """The step-up, applied to the interface DRF's permissions cannot reach.

    Decision 0014 point 5 covers merging volunteers, revoking labels and
    editing the catalogue. `RecentlyAuthenticated` holds those in the API, but
    decision 0014 point 4 keeps the Django admin complete on purpose -- so the
    same operations are reachable there, on the same origin, by the same
    session. The threat that decision records is script injected into the
    volunteer app acting from an administrator's own browser; that script can
    read the CSRF token and post to /admin exactly as easily as to /api, and
    the network restriction of decision 0013 point 6 does not help because it
    is that administrator's browser doing the posting.

    Only writes, and only under the admin. A GET is the admin being read, which
    is what somebody does before deciding to change anything.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if self._is_a_change_in_the_admin(request):
            # Where allauth asks again, told to come back here afterwards.
            return redirect(f"{reverse('account_reauthenticate')}?next={quote(request.get_full_path())}")
        return self.get_response(request)

    @staticmethod
    def _is_a_change_in_the_admin(request: HttpRequest) -> bool:
        if request.method in SAFE_METHODS or not request.path.startswith(reverse("admin:index")):
            return False
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            # Not signed in at all is the admin's own problem, and it has a
            # better answer for it than a redirect from here.
            return False
        return not recently_authenticated(request)
