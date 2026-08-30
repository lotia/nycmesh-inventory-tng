"""Signing in: four ways to say who you are, none of which says what you may do.

[Decision 0013](../../../../docs/decisions/0013-administrator-sign-in.md) is
the argument and is not restated here. What these tests hold in place is its
point 5, on where identity ends and authority begins, and its point 3, that
the local password path is not a way round the second factor.

Two shapes recur:

- **A bare ``Client``**, never the fixture of the same name. That one arrives
  already signed in, which is exactly what every test here is about
  establishing.

- **No third party is ever dialled.** A callback is completed from a payload
  this file writes, because a test that reached Google would be testing
  Google's availability. What is worth proving on this side of that boundary
  is that the account the callback makes holds nothing.
"""

from typing import Any

import pyotp
import pytest
from allauth.account.adapter import get_adapter
from allauth.core import context
from allauth.mfa.models import Authenticator
from allauth.socialaccount.adapter import get_adapter as get_socialaccount_adapter
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from pytest_django.fixtures import Settings

from inventory.adapters import SocialAccountAdapter
from inventory.tests.helpers import (
    PROVIDER_PAYLOADS,
    PROVIDERS,
    activate_totp,
    sign_in_locally,
    start_local_sign_in,
)

pytestmark = pytest.mark.django_db

PASSWORD = "not-a-real-password-either"


@pytest.fixture(autouse=True)
def _the_second_factor_is_required(settings: Any) -> None:
    """This module is about the path where it IS, so it says so.

    Since inventory-tng-o1uj.3 that is a default rather than a rule, and
    `.env.sample` ships it OFF for local development -- so a contributor who
    followed the setup instructions has a `.env` that settings.py reads, and
    three tests here would fail on their machine while passing in CI, which
    teaches people that a red suite is normal. Pinned rather than assumed for
    that reason alone; what happens when it is off is
    inventory/tests/test_second_factor.py's subject, not this module's.
    """
    settings.REQUIRE_SECOND_FACTOR = True


@pytest.fixture
def _providers(settings: Any) -> None:
    """Every provider decision 0013 point 1 names, configured with fakes.

    Credentials only ever leave this process on a round trip nothing here
    makes, so a client id that is not a client id proves the wiring without
    proving anything about Google.
    """
    settings.SOCIALACCOUNT_PROVIDERS = PROVIDERS


@pytest.fixture
def local(administrator: User) -> User:
    """An administrator who signs in with a password and holds a TOTP key.

    The shape decision 0013 point 2 guarantees stays available whatever else a
    deployment configures, and the only one an automated test can complete.
    """
    administrator.set_password(PASSWORD)
    administrator.save()
    return administrator


def complete_provider_callback(provider_id: str, payload: dict[str, Any]) -> Client:
    """Finish a provider's callback without having made the round trip.

    allauth's own path from here is what a real callback runs: the provider
    maps its payload onto a ``SocialLogin``, ``complete_social_login`` decides
    whether that is somebody it knows, and -- automatic sign-up being off --
    stashes a pending signup for the visitor to confirm. The client returned
    is holding that session, so the confirmation below is an ordinary POST.
    """
    request = RequestFactory().get("/")
    SessionMiddleware(_nothing).process_request(request)
    MessageMiddleware(_nothing).process_request(request)
    request.user = AnonymousUser()
    with context.request_context(request):
        provider = get_socialaccount_adapter().get_provider(request, provider_id)
        sociallogin = provider.sociallogin_from_response(request, payload)
        complete_social_login(request, sociallogin)
    request.session.save()
    session_key = request.session.session_key
    assert session_key is not None
    client = Client()
    client.cookies["sessionid"] = session_key
    return client


def _nothing(request: HttpRequest) -> HttpResponse:
    """Stand-in for the rest of the stack, which these two never call."""
    raise AssertionError("the middleware under construction should not get this far")


def whoami(client: Client) -> dict[str, Any]:
    return client.get(reverse("me")).json()


# --------------------------------------------------------------------------
# The local password path, and the second factor decision 0013 point 3
# requires of it.
# --------------------------------------------------------------------------


def test_the_local_path_is_available_with_no_provider_configured(settings: Any) -> None:
    """Point 2: the local path is there whatever else is configured.

    A fresh checkout and every deployment that has configured nothing land
    here, so the password form has to be what an unconfigured server offers --
    not an empty page of provider buttons.
    """
    settings.SOCIALACCOUNT_PROVIDERS = {}

    page = Client().get(reverse("account_login"))

    assert page.status_code == 200
    assert 'name="password"' in page.content.decode()


def test_a_password_alone_does_not_finish_the_local_sign_in(local: User) -> None:
    """The password is one factor of two, and one of two is none."""
    activate_totp(local)

    client = start_local_sign_in(local, PASSWORD)

    assert whoami(client)["authenticated"] is False


def test_the_local_path_signs_in_with_the_password_and_the_code(local: User) -> None:
    client = sign_in_locally(local, PASSWORD)

    assert whoami(client) == {
        "authenticated": True,
        "username": local.username,
        "administrator": True,
        "recently_authenticated": True,
        # Nothing presented, which is the ordinary case and never a refusal.
        "device": "none",
        "capabilities": {
            "append_stock": True,
            "add_volunteer": True,
            "edit_catalogue": True,
            "print_label": True,
            "revoke_label": True,
            "merge_volunteers": True,
        },
    }


@pytest.mark.usefixtures("_static_files_are_not_collected")
def test_the_wrong_code_leaves_the_session_unauthenticated(local: User) -> None:
    activate_totp(local)
    client = start_local_sign_in(local, PASSWORD)

    client.post(reverse("mfa_authenticate"), {"code": "000000"})

    assert whoami(client)["authenticated"] is False


def test_a_local_account_with_no_second_factor_reaches_nothing(local: User) -> None:
    """Point 3 is a requirement, so an account that has not met it is unfinished.

    allauth demands a code from anybody who has a key; nothing in it demands
    that a password-holder have one. This is that half, and without it the
    requirement is a sentence in a document.
    """
    client = start_local_sign_in(local, PASSWORD)

    refused = client.get(reverse("items"))

    assert refused.status_code == 403
    assert "second factor" in refused.json()["detail"]


def test_an_unfinished_session_can_still_be_told_what_state_it_is_in(local: User) -> None:
    """The endpoint whose job is to say so must not be the one that refuses.

    The single-page app fetches `/api/me` on load. If an unfinished session is
    refused there it has nothing to render but an error, and the one thing it
    cannot say is the one thing the volunteer needs to hear -- that they are
    signed in and have a second factor left to set up.
    """
    client = start_local_sign_in(local, PASSWORD)

    answer = whoami(client)

    assert answer["authenticated"] is True
    assert answer["capabilities"]["edit_catalogue"] is True


def test_an_unfinished_session_can_still_reach_the_index_and_its_csrf_cookie(local: User) -> None:
    """The activation form is a write, and a write needs the token the index sets."""
    client = start_local_sign_in(local, PASSWORD)

    index = client.get(reverse("api-root"))

    assert index.status_code == 200
    assert "csrftoken" in index.cookies


def test_a_probe_is_answered_whoever_is_half_signed_in(local: User) -> None:
    """The cluster's health check runs before authentication exists at all."""
    client = start_local_sign_in(local, PASSWORD)

    assert client.get(reverse("healthz")).status_code == 200


def test_the_rate_limit_counts_the_client_and_not_the_proxy(settings: Settings) -> None:
    """Otherwise every administrator shares one bucket behind the ingress.

    Without ALLAUTH_TRUSTED_PROXY_COUNT allauth falls back to REMOTE_ADDR,
    which is the last proxy. It is fed from NUM_PROXIES, the same variable
    the API's throttles count by, so the two cannot answer differently -- and
    one client burning the failed-login limit cannot lock everybody else out.
    """
    # The deployment docs/deployment.md describes: a browser, an ingress and
    # nginx. Pinned rather than inherited so this states the arrangement it is
    # about instead of whatever the environment happens to be configured for.
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 2
    request = RequestFactory().get(
        "/accounts/login/",
        headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"},
        REMOTE_ADDR="10.0.0.2",
    )

    assert get_adapter().get_client_ip(request) == "203.0.113.7"


def test_a_browser_in_that_state_is_shown_where_to_go(local: User) -> None:
    """The same refusal, said in the way a browser can act on."""
    client = start_local_sign_in(local, PASSWORD)

    bounced = client.get(reverse("items"), headers={"accept": "text/html"})

    assert bounced.status_code == 302
    assert bounced["Location"] == reverse("mfa_activate_totp")


def test_setting_up_totp_releases_the_session_and_leaves_recovery_codes(local: User) -> None:
    """And the recovery codes of point 3 arrive with it, not as a second chore.

    Driven through the page rather than the model on purpose: the codes are
    generated by the activation flow, so creating the key directly would prove
    the model can hold one and nothing about what a person gets.
    """
    client = start_local_sign_in(local, PASSWORD)
    # The form carries the secret it just generated; the code is computed from
    # that the way an authenticator app would.
    secret = client.get(reverse("mfa_activate_totp")).context["form"].secret

    client.post(reverse("mfa_activate_totp"), {"code": pyotp.TOTP(secret).now()})

    assert client.get(reverse("items")).status_code == 200
    assert Authenticator.objects.filter(user=local, type=Authenticator.Type.RECOVERY_CODES).exists()


def test_the_admin_sends_people_to_the_same_door(local: User) -> None:
    """Decision 0013: two sign-in surfaces exist and must agree.

    The admin's own password form knows nothing about providers or second
    factors, so what answers to its name is allauth's -- carrying the page the
    visitor was asking for, so they still land on it.
    """
    bounced = Client().get(reverse("admin:index"), follow=False)
    assert bounced.status_code == 302

    door = Client().get(reverse("admin:login"), {"next": "/admin/"})

    assert door.status_code == 302
    assert door["Location"] == f"{reverse('account_login')}?next=%2Fadmin%2F"


def test_local_accounts_are_issued_rather_than_registered() -> None:
    """Self-service registration would only manufacture accounts holding nothing.

    What decision 0013 point 2 guarantees instead is ACCOUNT_ADAPTER's comment
    in settings.py.
    """
    before = User.objects.count()

    page = Client().get(reverse("account_signup"))

    assert page.status_code == 200
    assert "account/signup_closed.html" in [template.name for template in page.templates]
    assert User.objects.count() == before


# --------------------------------------------------------------------------
# The provider paths. Nothing below reaches a third party.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider_id", "label"), [("google", "Google"), ("slack", "Slack"), ("oidc", "Single sign-on")]
)
@pytest.mark.usefixtures("_providers")
def test_every_configured_provider_is_offered(provider_id: str, label: str) -> None:
    """The name a person recognises, and the route that starts that provider.

    The generic OpenID Connect one is the reason both are asserted: what it is
    called and what its URL contains are a deployment's to choose, and the
    second is baked into the redirect URI registered with the provider.
    """
    page = Client().get(reverse("account_login")).content.decode()

    assert f'title="{label}"' in page
    assert f"{provider_id}/login/" in page


@pytest.mark.parametrize("label", ["Google", "Slack", "Single sign-on"])
def test_a_provider_with_no_credentials_is_not_offered(settings: Any, label: str) -> None:
    """A deployment configures a provider with a secret, not with a release.

    Which also means the absence of one is an ordinary state rather than a
    broken deployment: this is what every test above runs as.
    """
    settings.SOCIALACCOUNT_PROVIDERS = {}

    page = Client().get(reverse("account_login")).content.decode()

    assert f'title="{label}"' not in page


@pytest.mark.parametrize("provider_id", list(PROVIDER_PAYLOADS))
@pytest.mark.usefixtures("_providers")
def test_a_new_account_from_a_provider_holds_nothing(provider_id: str) -> None:
    """Point 5, and the reason this bead exists.

    Proving the sign-in worked is not the point; proving it bought nothing is.
    """
    client = complete_provider_callback(provider_id, PROVIDER_PAYLOADS[provider_id])

    client.post(reverse("socialaccount_signup"), {"username": "newcomer"})

    newcomer = User.objects.get(username="newcomer")
    assert newcomer.is_staff is False
    assert newcomer.is_superuser is False
    assert newcomer.get_all_permissions() == set()
    assert SocialAccount.objects.filter(user=newcomer, provider=provider_id).exists()
    assert whoami(client)["administrator"] is False


@pytest.mark.usefixtures("_providers")
def test_a_new_account_from_a_provider_may_read_but_not_change(item: Any) -> None:
    """What "no permissions" means at the endpoints, rather than on the row.

    Decision 0012 gives a session the catalogue to read; decision 0013 says
    signing in is all this session bought. Both at once is the state a new
    arrival is actually in.
    """
    client = complete_provider_callback("google", PROVIDER_PAYLOADS["google"])
    client.post(reverse("socialaccount_signup"), {"username": "newcomer"})

    assert client.get(reverse("items")).status_code == 200
    assert (
        client.patch(
            reverse("item-detail", args=[item.pk]),
            data={"name": "Renamed"},
            content_type="application/json",
        ).status_code
        == 403
    )
    assert whoami(client)["capabilities"]["edit_catalogue"] is False


@pytest.mark.usefixtures("_providers")
def test_a_provider_cannot_hand_over_the_staff_flag() -> None:
    """The payload is the provider's; these two fields are not.

    Nothing in allauth reads ``is_staff`` out of ``extra_data``, which is
    exactly why this is worth pinning: the adapter clears it on every save, so
    a future mapping that did read it could not promote anybody.
    """
    payload = dict(PROVIDER_PAYLOADS["google"], is_staff=True, is_superuser=True)
    client = complete_provider_callback("google", payload)

    client.post(reverse("socialaccount_signup"), {"username": "newcomer"})

    assert User.objects.filter(username="newcomer", is_staff=False, is_superuser=False).exists()


@pytest.mark.usefixtures("_providers")
def test_a_provider_email_does_not_reach_an_account_that_already_exists(administrator: User) -> None:
    """Decision 0013's last consequence: an email address is not identity.

    A workspace controls the address it hands over and Apple hands over a
    relay, so treating one as proof would let whoever controls the address
    arrive as the administrator who happens to use it.
    """
    administrator.email = PROVIDER_PAYLOADS["google"]["email"]
    administrator.save()

    client = complete_provider_callback("google", PROVIDER_PAYLOADS["google"])
    client.post(reverse("socialaccount_signup"), {"username": "newcomer"})

    assert not SocialAccount.objects.filter(user=administrator).exists()
    assert whoami(client)["administrator"] is False


# --------------------------------------------------------------------------
# The one way to become an administrator.
# --------------------------------------------------------------------------


@pytest.mark.usefixtures("_static_files_are_not_collected")
@pytest.mark.usefixtures("_providers")
def test_only_an_existing_administrator_can_reach_the_grant(local: User) -> None:
    """Point 5's "an existing administrator grants the staff flag", as a boundary.

    The page that carries the flag is Django's own, and it is behind the same
    staff check everything else is, so the newcomer cannot reach the control
    that would promote them.
    """
    newcomer_client = complete_provider_callback("google", PROVIDER_PAYLOADS["google"])
    newcomer_client.post(reverse("socialaccount_signup"), {"username": "newcomer"})
    newcomer = User.objects.get(username="newcomer")

    refused = newcomer_client.get(reverse("admin:auth_user_change", args=[newcomer.pk]))
    assert refused.status_code == 302

    granting = sign_in_locally(local, PASSWORD)
    page = granting.get(reverse("admin:auth_user_change", args=[newcomer.pk]))

    assert page.status_code == 200
    assert 'name="is_staff"' in page.content.decode()


@pytest.mark.usefixtures("_providers")
def test_the_grant_is_what_turns_the_account_into_an_administrator(item: Any) -> None:
    """The other half: nothing changes about the session, only the flag.

    Which is the whole of decision 0013 point 5 in one assertion -- the same
    account, the same provider, the same session, refused before the grant and
    accepted after it.
    """
    client = complete_provider_callback("google", PROVIDER_PAYLOADS["google"])
    client.post(reverse("socialaccount_signup"), {"username": "newcomer"})
    rename = {"name": "Renamed"}
    detail = reverse("item-detail", args=[item.pk])
    assert client.patch(detail, data=rename, content_type="application/json").status_code == 403

    User.objects.filter(username="newcomer").update(is_staff=True)

    assert client.patch(detail, data=rename, content_type="application/json").status_code == 200
    assert whoami(client)["administrator"] is True


@pytest.mark.usefixtures("_providers")
def test_a_provider_account_is_not_asked_for_a_second_factor(local: User) -> None:
    """Point 3's other half: an arrival from a provider inherits what it enforced.

    The middleware keys on how this session authenticated rather than on
    whether the account could hold a password, so somebody who came through
    Google is not held at a door they have no key for.
    """
    activate_totp(local)
    client = complete_provider_callback("google", PROVIDER_PAYLOADS["google"])

    client.post(reverse("socialaccount_signup"), {"username": "newcomer"})

    assert client.get(reverse("items")).status_code == 200


def test_an_unfinished_session_can_still_load_the_page_it_is_sent_to(local: User) -> None:
    """The activation page is Django's own, styled by Django's own static files.

    Refusing those would send somebody to a page they cannot read at the one
    moment they have to act on it -- and while ``DEBUG`` is on Django serves
    them outside the URL configuration, so they are exempt by path rather than
    by view.
    """
    client = start_local_sign_in(local, PASSWORD)

    served = client.get(f"{settings.STATIC_URL}admin/css/base.css", headers={"accept": "text/html"})

    assert served.status_code != 403
    assert served.status_code != 302


def test_an_unfinished_session_reaches_nothing_at_a_path_that_is_nobodys(local: User) -> None:
    """A path the URL configuration does not know is not an escape hatch.

    The exemption is "this is one of allauth's own pages", which an
    unresolvable path is not; answering it would be deciding a policy question
    from the absence of a route.
    """
    client = start_local_sign_in(local, PASSWORD)

    assert client.get("/no-such-page").status_code == 403


@pytest.mark.usefixtures("_providers")
def test_the_authority_flags_are_cleared_even_when_something_else_set_them() -> None:
    """The guard in the adapter, exercised where a payload cannot reach.

    Nothing in allauth maps ``is_staff`` out of a provider's response today,
    which is why the mapping is not what this project relies on. The adapter
    clears the flags on the way to the database, so an account arriving from a
    provider holds nothing whatever built it.
    """
    request = RequestFactory().get("/")
    SessionMiddleware(_nothing).process_request(request)
    with context.request_context(request):
        provider = get_socialaccount_adapter().get_provider(request, "google")
        sociallogin = provider.sociallogin_from_response(request, PROVIDER_PAYLOADS["google"])
        sociallogin.user.username = "newcomer"
        sociallogin.user.is_staff = True
        sociallogin.user.is_superuser = True

        saved = SocialAccountAdapter().save_user(request, sociallogin)

    assert saved.is_staff is False
    assert saved.is_superuser is False
    assert User.objects.get(username="newcomer").is_staff is False
