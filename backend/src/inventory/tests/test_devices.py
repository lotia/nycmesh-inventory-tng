"""What a device credential buys, and the one thing it must never stop buying.

`inventory_tng.devices` is the argument; these are the claims. Two of them are
load-bearing in a way the rest are not, and both are written so that the
optimisation which would break them fails here rather than in production:

- **Revocation acts on the NEXT REQUEST.** Every test of it revokes a device
  and then asks the API for something, rather than asserting anything about the
  token. A change that verified the signature and skipped the row -- "the
  signature already proved it" -- would leave every assertion about a token
  green and remove revocation entirely.
- **The signature is not the authorisation.** A perfectly good signature over
  an identifier with no row behind it is not an enrolled device, and the case
  below that says so is the one that catches the same mistake from the other
  side.
"""

from typing import Any

import pytest
from django.conf import settings as django_settings
from django.contrib import admin
from django.core import signing
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from inventory.models import Device, Volunteer
from inventory.permissions import (
    DEVICE_ENROLLED,
    DEVICE_REVOKED,
    DEVICE_UNKNOWN,
    NO_DEVICE,
    presented_device,
)
from inventory.throttling import AppendBurstThrottle, DeviceEnrolmentThrottle
from inventory_tng import debugging, devices, forwarded

pytestmark = pytest.mark.django_db

ENROL_URL = reverse("devices")
ME_URL = reverse("me")
VOLUNTEERS_URL = reverse("volunteers")


def enrol(client: Client, **extra: Any) -> tuple[str, str]:
    """Mint one, and hand back the token and the name inside it."""
    response = client.post(ENROL_URL, data={}, content_type="application/json", **extra)
    assert response.status_code == 201, response.content
    body = response.json()
    return body["token"], body["device"]


def carrying(token: str) -> dict[str, str]:
    """The header a device sends, as Django's test client wants it."""
    return {devices.HEADER: token}


def add_volunteer(client: Client, name: str, **extra: Any) -> Any:
    return client.post(VOLUNTEERS_URL, data={"display_name": name}, content_type="application/json", **extra)


# ---------------------------------------------------------------------------
# Minting
# ---------------------------------------------------------------------------


def test_a_device_is_handed_a_token_and_the_name_inside_it(client: Client) -> None:
    token, identifier = enrol(client)

    assert devices.presented(token) == identifier
    assert Device.objects.filter(identifier=identifier, revoked_at__isnull=True).exists()


def test_minting_answers_a_caller_with_no_session_at_all(client: Client) -> None:
    """It has to. A device has nothing to present until it has enrolled, so a
    credential obtainable only by somebody already carrying one would admit
    nobody at all."""
    client.logout()

    assert client.post(ENROL_URL, data={}, content_type="application/json").status_code == 201


def test_every_mint_records_where_it_was_asked_from(client: Client) -> None:
    """The cheapest thing on this design's list and the one most likely to be
    dropped. Fifty devices from one address in three minutes is not preventable
    on a flat network; this is what makes it one query and one bulk revoke."""
    _, identifier = enrol(client, REMOTE_ADDR="10.69.4.7")

    assert Device.objects.get(identifier=identifier).enrolled_from == "10.69.4.7"


def test_a_forged_forwarded_header_is_not_stored_as_an_address(client: Client, settings: Any) -> None:
    """`client_address` is allowed to hand back a string a caller invented --
    `forwarded.trusted` says why, and `DeviceEnrolmentView` says what storing
    one unchecked would cost. Nothing is recorded instead, which is what the
    nullable column already means.
    """
    settings.TRUSTED_PROXIES = forwarded.networks(["10.0.0.0/8"])

    response = client.post(
        ENROL_URL,
        data={},
        content_type="application/json",
        REMOTE_ADDR="10.0.0.1",
        headers={"X-Forwarded-For": "not-an-address"},
    )

    assert response.status_code == 201, response.content
    assert Device.objects.get(identifier=response.json()["device"]).enrolled_from is None


def test_a_forwarded_address_this_deployment_believes_is_the_one_recorded(client: Client, settings: Any) -> None:
    settings.TRUSTED_PROXIES = forwarded.networks(["10.0.0.0/8"])

    response = client.post(
        ENROL_URL,
        data={},
        content_type="application/json",
        REMOTE_ADDR="10.0.0.1",
        headers={"X-Forwarded-For": "198.51.100.9"},
    )

    assert Device.objects.get(identifier=response.json()["device"]).enrolled_from == "198.51.100.9"


def test_two_devices_are_two_rows_with_two_names(client: Client) -> None:
    first, one = enrol(client)
    second, two = enrol(client)

    assert one != two
    assert first != second
    assert Device.objects.count() == 2


# ---------------------------------------------------------------------------
# What signing buys, and what it does not
# ---------------------------------------------------------------------------


def test_a_token_cannot_be_invented(client: Client) -> None:
    _, identifier = enrol(client)

    assert devices.presented(identifier) == ""
    assert devices.presented(f"{identifier}:forged") == ""
    assert devices.presented("") == ""


def test_a_debug_token_cannot_be_presented_as_a_device(client: Client) -> None:
    """The salts are what keep the two apart, and both name their own."""
    assert devices.presented(debugging.mint()) == ""
    assert debugging.minted(devices.mint(devices.new_identifier())) == ""


def test_a_signature_over_a_name_with_no_row_is_not_an_enrolled_device(rf: Any) -> None:
    """THE SIGNATURE IS NOT THE AUTHORISATION.

    Signed by this server, with this server's key and this token's own salt --
    everything a check of the signature alone would ask for -- and there is no
    row. Nothing is enrolled here, and a reading that stopped at the signature
    would say otherwise.
    """
    invented = devices.mint(devices.new_identifier())
    request = rf.get("/api/items", headers={devices.HEADER: invented})

    assert presented_device(request)[0] == DEVICE_UNKNOWN


def test_a_rotated_key_stops_honouring_every_token_at_once(client: Client, settings: Any) -> None:
    """The mass-revocation lever, and it is free.

    And the other half of the same setting: rotating WITH the old key in
    `SECRET_KEY_FALLBACKS` signs nobody out, which is what an ordinary
    hygiene rotation wants. `docs/deployment.md` is where a deployer is told
    which of the two they are choosing.
    """
    token, identifier = enrol(client)
    was = settings.SECRET_KEY

    settings.SECRET_KEY = "a-different-key-entirely"
    settings.SECRET_KEY_FALLBACKS = []
    assert devices.presented(token) == ""

    settings.SECRET_KEY_FALLBACKS = [was]
    assert devices.presented(token) == identifier


# ---------------------------------------------------------------------------
# Revocation, which is the point of the row
# ---------------------------------------------------------------------------


def test_revoking_a_device_refuses_its_very_next_request(client: Client) -> None:
    """THE TEST THIS WHOLE DESIGN EXISTS FOR.

    Asserted on the request rather than on the token, deliberately: a change
    that skipped the select because the signature had already verified would
    pass every assertion about a token and would have removed revocation.
    """
    token, identifier = enrol(client)

    assert client.get(VOLUNTEERS_URL, headers=carrying(token)).status_code == 200

    Device.objects.filter(identifier=identifier).update(revoked_at=timezone.now())

    refused = client.get(VOLUNTEERS_URL, headers=carrying(token))
    assert refused.status_code == 403


def test_a_revoked_device_is_refused_on_the_writes_a_volunteer_makes(client: Client) -> None:
    """The two endpoints decision 0012 opens name their own permissions, so
    they are the place a default would not have reached."""
    token, identifier = enrol(client)
    assert add_volunteer(client, "Ada Lovelace", headers=carrying(token)).status_code == 201

    Device.objects.filter(identifier=identifier).update(revoked_at=timezone.now())

    assert add_volunteer(client, "Grace Hopper", headers=carrying(token)).status_code == 403
    assert not Volunteer.objects.filter(display_name="Grace Hopper").exists()


def test_the_refusal_says_it_is_the_device_and_not_the_person(client: Client) -> None:
    """A wall and a "this device was removed" want opposite screens, and they
    are the same status code. The code is what tells them apart without a
    client matching on prose."""
    token, identifier = enrol(client)
    Device.objects.filter(identifier=identifier).update(revoked_at=timezone.now())

    body = client.get(VOLUNTEERS_URL, headers=carrying(token)).json()

    assert body["code"] == "device_revoked"
    assert "device" in body["detail"].lower()


def test_enrolling_again_after_a_revocation_is_a_new_row_and_works(client: Client) -> None:
    """Which is the ceiling, said out loud: this weakly guards against an
    in-network bad actor, and a determined one re-mints."""
    _, identifier = enrol(client)
    Device.objects.filter(identifier=identifier).update(revoked_at=timezone.now())

    again, second = enrol(client)

    assert second != identifier
    assert client.get(VOLUNTEERS_URL, headers=carrying(again)).status_code == 200


def test_carrying_nothing_at_all_is_never_a_refusal(client: Client) -> None:
    """Attribution is offered, not demanded. The network does admission (0030)."""
    assert client.get(VOLUNTEERS_URL).status_code == 200
    assert add_volunteer(client, "Ada Lovelace").status_code == 201


def test_a_token_this_server_does_not_honour_is_not_a_refusal_either(client: Client) -> None:
    """It is a client holding something stale, and telling it so is `/api/me`'s
    job rather than a 403 on whatever it happened to ask for next."""
    assert client.get(VOLUNTEERS_URL, headers=carrying("not-a-token")).status_code == 200


# ---------------------------------------------------------------------------
# What /api/me says about it
# ---------------------------------------------------------------------------


def test_me_says_nothing_was_presented_when_nothing_was(client: Client) -> None:
    assert client.get(ME_URL).json()["device"] == NO_DEVICE


def test_me_says_a_string_it_cannot_read_is_not_a_device(client: Client) -> None:
    """Told apart from carrying nothing, so a client holding something this
    server has stopped honouring throws it away instead of presenting it for
    ever."""
    assert client.get(ME_URL, headers=carrying("garbage")).json()["device"] == DEVICE_UNKNOWN


def test_me_says_enrolled_and_then_says_revoked(client: Client) -> None:
    """And answers a revoked device at all, which is the point of it being one
    of the endpoints that names `AllowAny` for itself: this is how a client
    finds out, and refusing it would leave it guessing from a 403 elsewhere."""
    token, identifier = enrol(client)
    assert client.get(ME_URL, headers=carrying(token)).json()["device"] == DEVICE_ENROLLED

    Device.objects.filter(identifier=identifier).update(revoked_at=timezone.now())

    answered = client.get(ME_URL, headers=carrying(token))
    assert answered.status_code == 200
    assert answered.json()["device"] == DEVICE_REVOKED


def test_the_row_is_read_once_however_many_times_it_is_asked(client: Client) -> None:
    """`/api/me` runs the whole permission list once per capability it reports
    and then asks again for the field, so an unmemoised read is one identical
    select per capability.

    Stated as the DIFFERENCE a token makes rather than as a total, because the
    total is Django's session and allauth's and would move for reasons that
    have nothing to do with this. One more query than the same request made
    without a token is the claim.
    """
    token, _ = enrol(client)

    with CaptureQueriesContext(connection) as bare:
        assert client.get(ME_URL).status_code == 200
    with CaptureQueriesContext(connection) as presenting:
        assert client.get(ME_URL, headers=carrying(token)).status_code == 200

    assert len(presenting) == len(bare) + 1


# ---------------------------------------------------------------------------
# What constrains minting, which is the guard the signature is not
# ---------------------------------------------------------------------------


def test_minting_is_metered_out_of_a_bucket_of_its_own(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fifty calls buy fifty buckets and every signature checks out, so this is
    the thing standing in the way."""
    monkeypatch.setattr(DeviceEnrolmentThrottle, "rate", "2/min", raising=False)

    assert client.post(ENROL_URL, data={}, content_type="application/json").status_code == 201
    assert client.post(ENROL_URL, data={}, content_type="application/json").status_code == 201
    assert client.post(ENROL_URL, data={}, content_type="application/json").status_code == 429


def test_a_room_enrolling_cannot_spend_what_a_volunteers_batch_needs(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason it is a bucket of its own rather than a share of the append
    allowance. `inventory.throttling.ReportThrottle` made the same argument."""
    monkeypatch.setattr(DeviceEnrolmentThrottle, "rate", "1/min", raising=False)
    enrol(client)

    assert client.post(ENROL_URL, data={}, content_type="application/json").status_code == 429
    assert add_volunteer(client, "Ada Lovelace").status_code == 201


def test_two_devices_are_two_appending_buckets(client: Client, rf: Any) -> None:
    """Two browsers, two buckets. `AppendThrottle.get_ident` says what that is
    worth and what does not reach it yet.

    Asked of the throttle rather than through the API for that second reason:
    nothing is anonymous, so there is no request that would take this path.
    """
    first, one = enrol(client)
    second, two = enrol(client)
    throttle = AppendBurstThrottle()

    assert throttle.get_ident(rf.post("/api/volunteers", headers=carrying(first))) == f"device:{one}"
    assert throttle.get_ident(rf.post("/api/volunteers", headers=carrying(second))) == f"device:{two}"


def test_a_device_that_presents_nothing_falls_back_to_the_address(client: Client, rf: Any) -> None:
    """What everybody got before, and what a browser that has not enrolled
    still gets."""
    throttle = AppendBurstThrottle()

    assert throttle.get_ident(rf.post("/api/volunteers", REMOTE_ADDR="10.69.4.7")) == "10.69.4.7"


def test_a_revoked_device_does_not_keep_a_bucket_of_its_own(client: Client, rf: Any) -> None:
    """`AppendThrottle.get_ident` carries the argument for the fallback."""
    token, identifier = enrol(client)
    Device.objects.filter(identifier=identifier).update(revoked_at=timezone.now())
    throttle = AppendBurstThrottle()

    ident = throttle.get_ident(rf.post("/api/volunteers", REMOTE_ADDR="10.69.4.7", headers=carrying(token)))
    assert ident == "10.69.4.7"


# ---------------------------------------------------------------------------
# The row, and the one screen that acts on it
# ---------------------------------------------------------------------------


def test_a_device_row_cannot_be_deleted_or_added_in_the_admin(client: Client, editor: Client) -> None:
    """Deleting a revoked row would un-revoke that device, which `DeviceAdmin`
    argues at length. The last line here is what says the revocation survived
    the two refusals above rather than being asserted about in the abstract."""
    token, identifier = enrol(client)
    Device.objects.filter(identifier=identifier).update(revoked_at=timezone.now())
    row = Device.objects.get(identifier=identifier)
    model_admin = admin.site.get_model_admin(Device)

    assert not model_admin.has_delete_permission(editor.request().wsgi_request, row)
    assert not model_admin.has_add_permission(editor.request().wsgi_request)
    assert client.get(VOLUNTEERS_URL, headers=carrying(token)).status_code == 403


def test_a_device_says_which_state_it_is_in(client: Client) -> None:
    _, identifier = enrol(client)
    row = Device.objects.get(identifier=identifier)

    assert row.is_active is True
    assert identifier[:8] in str(row)
    assert "enrolled" in str(row)

    row.revoked_at = timezone.now()
    row.save()

    assert row.is_active is False
    assert "revoked" in str(row)


def test_the_device_header_is_one_a_cross_origin_browser_may_send() -> None:
    """Same origin today, so this bites nowhere -- and a header dropped by a
    preflight is the failure that is hardest to see: enrolment appears to work
    and every request after it arrives anonymous. Held beside the debug header,
    which is named there for the same reason."""
    assert devices.HEADER.lower() in django_settings.CORS_ALLOW_HEADERS
    assert debugging.HEADER.lower() in django_settings.CORS_ALLOW_HEADERS


def test_the_signer_is_not_a_timestamp_signer() -> None:
    """The row is the lifetime; `inventory_tng.devices` says why a stamp beside
    it would be decoration."""
    assert not isinstance(devices.signer(), signing.TimestampSigner)
