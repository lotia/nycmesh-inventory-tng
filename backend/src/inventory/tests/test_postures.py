"""The five settings the access-posture demo is performed with, and every value of each.

PROVISIONAL, with the code they test. `inventory-tng-81f7.3` is the demo,
`inventory-tng-81f7` is the question it exists to make decidable, and
`inventory-tng-81f7.4` deletes this file along with the settings.

TWO PROPERTIES MATTER MORE THAN THE INDIVIDUAL CASES, and they are asserted
first below.

THE DEFAULTS ARE TODAY'S BEHAVIOUR. A checkout that sets none of these behaves
exactly as it did before any of this existed, which is what makes a spike
affordable: it is five values rather than a fork, and a reviewer does not have
to read the whole of it to know that nothing moved.

EVERY VARIANT IS A VALUE. A posture that needed a code change could not be
switched in front of a room, so it would never be compared -- which is the
requirement `inventory-tng-81f7.3` opens with. So each value is exercised by
setting it and asking the endpoint, in the same way a presenter would.
"""

from typing import Any

import pytest

# Aliased, because every test here takes pytest-django's `settings` fixture to
# move a posture with, and one of them asks what the module holds instead.
from django.conf import settings as configured
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings
from rest_framework.test import APIRequestFactory

from inventory.models import Device, Item, Location, StockMovement, StockTransaction, Volunteer
from inventory.permissions import client_address
from inventory.tests.helpers import post
from inventory.throttling import AppendBurstThrottle, EnrolmentThrottle
from inventory_tng import postures

pytestmark = pytest.mark.django_db

VOLUNTEERS = reverse("volunteers")
LOCATIONS = reverse("locations")
DEVICES = reverse("devices")
ME = reverse("me")


def rows(response: Any) -> list[dict[str, Any]]:
    return list(response.json()["results"])


def names(response: Any) -> list[str]:
    return [person["display_name"] for person in rows(response)]


def anonymous(client: Client) -> Client:
    client.logout()
    return client


@pytest.fixture
def enrolled_device(client: Client, settings: Settings) -> tuple[Client, str]:
    """A device that has enrolled itself, and the token it holds.

    Six tests opened with these same three lines. What each of them is about
    is what happens NEXT -- a revoked row, a second budget, a memoised query --
    so the arranging is the part worth saying once.
    """
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_SELF
    caller = anonymous(client)
    return caller, post(caller, "devices", {}).json()["token"]


# --------------------------------------------------------------------------
# Nothing moves for anybody who sets none of them
# --------------------------------------------------------------------------


def test_the_defaults_are_what_this_application_already_did(settings: Settings) -> None:
    """Read off the settings rather than restated, so a changed default fails here.

    Every one of these is the behaviour before this bead: a session to read,
    the whole volunteer record on the wire, no minimum search length, no read
    limit, and custody locations behind the same session as everything else.
    """
    assert settings.VOLUNTEER_ACCESS == postures.SESSION
    assert settings.ANONYMOUS_PAYLOAD == postures.FULL
    assert settings.SEARCH_MINIMUM == 0
    assert settings.ANONYMOUS_READ_RATE == ""
    assert settings.CUSTODY_VISIBILITY == postures.IDENTIFIED


def test_a_caller_with_no_session_still_reads_nothing_by_default(client: Client, volunteer: Volunteer) -> None:
    assert anonymous(client).get(VOLUNTEERS).status_code == 403


def test_a_session_still_reads_the_whole_record_by_default(client: Client, volunteer: Volunteer) -> None:
    volunteer.email = "sean@quartzmail.example"
    volunteer.slack_id = "U024BE7LH"
    volunteer.save()

    assert rows(client.get(VOLUNTEERS)) == [
        {
            "id": volunteer.pk,
            "display_name": "Sean",
            "email": "sean@quartzmail.example",
            "slack_id": "U024BE7LH",
        }
    ]


# --------------------------------------------------------------------------
# ANONYMOUS_PAYLOAD, and the mask it carries despite having measured it out
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("sean@quartzmail.example", "s•••@q••••.example"),
        ("s.whitfield@quartzmail.example", "s•••@q••••.example"),
        ("priya@heronpost.test", "p•••@h••••.test"),
        # Two labels either side of the last dot: the rule keeps the LAST one,
        # which is what was measured.
        ("noor@post.tidewater.example", "n•••@p••••.example"),
        # No dot at all, which Django's EmailValidator allowlists and so this
        # can be asked to mask: the mask keeps none either, because a dangling
        # separator on the end says nothing about anybody.
        ("admin@localhost", "a•••@l••••"),
    ],
)
def test_the_mask_is_the_rule_that_was_measured(address: str, expected: str) -> None:
    """local[0] + domain-initial + TLD, reproduced rather than improved on.

    The figures on inventory-tng-81f7 were measured against exactly this, so a
    mask that kept one more character would separate the demo's two Seans and
    quietly contradict the numbers the presenter is about to read out.
    """
    assert postures.masked(address) == expected


def test_the_mask_leaves_an_absent_address_absent() -> None:
    assert postures.masked(None) is None


def test_an_address_the_rule_cannot_describe_is_masked_to_nothing() -> None:
    """A row edited underneath the EmailField. Answering with the value would
    be the one outcome a mask exists to prevent."""
    assert postures.masked("not-an-address") == "•••@••••"


def test_masked_gives_two_people_the_identical_string(client: Client, settings: Settings) -> None:
    """Act two, in one assertion. This is the whole argument against masking."""
    settings.ANONYMOUS_PAYLOAD = postures.MASKED
    settings.VOLUNTEER_ACCESS = postures.OPEN
    Volunteer.objects.create(display_name="Sean Whitfield", email="sean@quartzmail.example")
    Volunteer.objects.create(display_name="Sean Whitfield", email="s.whitfield@quartzmail.example")

    shown = rows(anonymous(client).get(VOLUNTEERS, {"search": "Sean"}))

    assert [person["display_name"] for person in shown] == ["Sean Whitfield", "Sean Whitfield"]
    assert {person["email"] for person in shown} == {"s•••@q••••.example"}


def test_masked_drops_the_slack_id_as_well(client: Client, settings: Settings, volunteer: Volunteer) -> None:
    """`VolunteerSerializer.to_representation` says why both go, not one."""
    settings.ANONYMOUS_PAYLOAD = postures.MASKED
    settings.VOLUNTEER_ACCESS = postures.OPEN
    volunteer.slack_id = "U024BE7LH"
    volunteer.save()

    assert "slack_id" not in rows(anonymous(client).get(VOLUNTEERS))[0]


def test_names_only_sends_the_name_and_the_id_and_nothing_else(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    settings.ANONYMOUS_PAYLOAD = postures.NAMES_ONLY
    settings.VOLUNTEER_ACCESS = postures.OPEN
    volunteer.email = "sean@quartzmail.example"
    volunteer.slack_id = "U024BE7LH"
    volunteer.save()

    assert rows(anonymous(client).get(VOLUNTEERS)) == [{"id": volunteer.pk, "display_name": "Sean"}]


def test_typing_a_whole_address_still_finds_the_person_who_typed_it(client: Client, settings: Settings) -> None:
    """Act three's second half: the address goes IN and only a name comes back.

    `VolunteerFilter` matches identifiers exactly and always did, so narrowing
    the payload needs no change to search at all -- which is the sentence the
    demo turns into four seconds of somebody typing.
    """
    settings.ANONYMOUS_PAYLOAD = postures.NAMES_ONLY
    settings.VOLUNTEER_ACCESS = postures.OPEN
    Volunteer.objects.create(display_name="Priya Raman", email="priya@heronpost.test")
    Volunteer.objects.create(display_name="Priya Raman", email="raman@tidewater.example")

    found = rows(anonymous(client).get(VOLUNTEERS, {"search": "priya@heronpost.test"}))

    assert found == [{"id": found[0]["id"], "display_name": "Priya Raman"}]


def test_an_administrator_keeps_the_whole_record_under_every_payload(
    editor: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """Two representations keyed on the caller, not a field taken off the model.

    Merging duplicates is an administrator's operation and needs the
    identifiers that tell two people apart.
    """
    settings.ANONYMOUS_PAYLOAD = postures.NAMES_ONLY
    volunteer.email = "sean@quartzmail.example"
    volunteer.save()

    assert rows(editor.get(VOLUNTEERS))[0]["email"] == "sean@quartzmail.example"


def test_one_row_read_by_id_is_narrowed_the_same_way(client: Client, settings: Settings, volunteer: Volunteer) -> None:
    """A collection that withheld an address while the detail endpoint served
    it would be a policy with a URL that skips it."""
    settings.ANONYMOUS_PAYLOAD = postures.NAMES_ONLY
    settings.VOLUNTEER_ACCESS = postures.OPEN
    volunteer.email = "sean@quartzmail.example"
    volunteer.save()

    body = anonymous(client).get(reverse("volunteer-detail", args=[volunteer.pk])).json()

    assert "email" not in body
    assert body["display_name"] == "Sean"


def test_adding_yourself_answers_in_the_shape_this_posture_sends(client: Client, settings: Settings) -> None:
    settings.ANONYMOUS_PAYLOAD = postures.NAMES_ONLY
    settings.VOLUNTEER_ACCESS = postures.OPEN

    created = post(anonymous(client), "volunteers", {"display_name": "Wren Xu", "email": "wren@northmail.test"})

    assert created.status_code == 201
    assert "email" not in created.json()
    assert Volunteer.objects.get(display_name="Wren Xu").email == "wren@northmail.test"


# --------------------------------------------------------------------------
# VOLUNTEER_ACCESS
# --------------------------------------------------------------------------


def test_open_answers_a_caller_who_presents_nothing(client: Client, settings: Settings, volunteer: Volunteer) -> None:
    settings.VOLUNTEER_ACCESS = postures.OPEN

    assert anonymous(client).get(VOLUNTEERS).status_code == 200


@pytest.mark.parametrize("posture", [postures.ENROLLED_SELF, postures.ENROLLED_CODE])
def test_an_enrolling_posture_refuses_a_device_that_has_not(client: Client, settings: Settings, posture: str) -> None:
    settings.VOLUNTEER_ACCESS = posture
    settings.VOLUNTEER_ACCESS_CODE = "grand-street"

    assert anonymous(client).get(VOLUNTEERS).status_code == 403


def test_enrolled_self_lets_any_device_mint_its_own_credential(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """Act four: from the volunteer's side this costs nothing, and from an
    attacker's side it costs one extra line."""
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_SELF
    caller = anonymous(client)

    minted = post(caller, "devices", {})

    assert minted.status_code == 201
    assert caller.get(VOLUNTEERS, headers={postures.DEVICE_HEADER: minted.json()["token"]}).status_code == 200


def test_enrolled_code_refuses_a_device_that_has_not_been_in_the_room(client: Client, settings: Settings) -> None:
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_CODE
    settings.VOLUNTEER_ACCESS_CODE = "grand-street"

    assert post(anonymous(client), "devices", {}).status_code == 403
    assert post(anonymous(client), "devices", {"code": "saratoga"}).status_code == 403


def test_enrolled_code_admits_a_device_that_has(client: Client, settings: Settings, volunteer: Volunteer) -> None:
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_CODE
    settings.VOLUNTEER_ACCESS_CODE = "grand-street"
    caller = anonymous(client)

    minted = post(caller, "devices", {"code": "grand-street"})

    assert minted.status_code == 201
    assert caller.get(VOLUNTEERS, headers={postures.DEVICE_HEADER: minted.json()["token"]}).status_code == 200


def test_a_revoked_device_stops_being_answered(enrolled_device: tuple[Client, str], volunteer: Volunteer) -> None:
    """The whole reason a row exists rather than a bare signature: act four
    claims an enrolled posture can cut off ONE device, and this is that claim."""
    caller, token = enrolled_device
    Device.objects.update(revoked_at="2026-08-24T12:00:00Z")

    assert caller.get(VOLUNTEERS, headers={postures.DEVICE_HEADER: token}).status_code == 403


def test_the_device_header_is_one_the_preflight_admits() -> None:
    """The third header to need this and the third identical reason, which
    ``CORS_ALLOW_HEADERS`` in inventory_tng/settings.py states once."""
    assert postures.DEVICE_HEADER.lower() in configured.CORS_ALLOW_HEADERS


def test_a_token_this_deployment_did_not_sign_is_not_a_device(client: Client, settings: Settings) -> None:
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_SELF

    assert anonymous(client).get(VOLUNTEERS, headers={postures.DEVICE_HEADER: "made-up"}).status_code == 403


def test_a_signature_over_a_device_that_was_never_enrolled_is_not_one(client: Client, settings: Settings) -> None:
    """The signature and the row are both asked. Rotating the key is not the
    only revocation this offers, and neither half stands alone."""
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_SELF
    forged = postures.mint_device_token("0" * 32)

    assert anonymous(client).get(VOLUNTEERS, headers={postures.DEVICE_HEADER: forged}).status_code == 403


def test_nothing_enrols_where_no_posture_asks_for_one(client: Client) -> None:
    """Under the default posture the enrolment endpoint refuses outright, for
    the reason `DeviceEnrolmentView` gives."""
    assert post(anonymous(client), "devices", {}).status_code == 403


def appended(caller: Client, token: str, name: str) -> Any:
    """A volunteer's own write, made the way an enrolled device makes it.

    Its own helper because the two tests below need the SAME caller to make
    both kinds of request: a throttle's bucket is the scope and the client, so
    two clients would show two buckets whatever the scopes were.
    """
    return caller.post(
        VOLUNTEERS,
        data={"display_name": name},
        content_type="application/json",
        headers={postures.DEVICE_HEADER: token},
    )


def test_a_room_mistyping_the_code_does_not_spend_a_batch_s_allowance(
    enrolled_device: tuple[Client, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`EnrolmentThrottle` in inventory/throttling.py argues the separation.
    This is the thing it buys: an exhausted enrolment budget leaves the append
    budget whole."""
    caller, token = enrolled_device
    monkeypatch.setattr(EnrolmentThrottle, "rate", "1/min", raising=False)

    assert post(caller, "devices", {}).status_code == 429

    assert appended(caller, token, "Wren Xu").status_code == 201


def test_a_hub_filling_its_append_allowance_can_still_enrol_a_phone(
    enrolled_device: tuple[Client, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction of the same separation."""
    caller, token = enrolled_device
    monkeypatch.setattr(AppendBurstThrottle, "rate", "1/min", raising=False)

    assert appended(caller, token, "Wren Xu").status_code == 201
    assert appended(caller, token, "Noor Haddad").status_code == 429

    assert post(caller, "devices", {}).status_code == 201


def test_signing_in_admits_you_under_every_posture(editor: Client, settings: Settings, volunteer: Volunteer) -> None:
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_CODE
    settings.VOLUNTEER_ACCESS_CODE = "grand-street"

    assert editor.get(VOLUNTEERS).status_code == 200


def test_mesh_only_answers_inside_the_range_and_refuses_outside_it(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """Stated as the CIDR strings `.env.sample` documents, which is also the
    regression: while the setting held parsed networks, the obvious spelling of
    it -- in a test, or in a shell -- raised TypeError inside the permission
    class and answered 500 across the whole API."""
    settings.VOLUNTEER_ACCESS = postures.MESH_ONLY
    settings.VOLUNTEER_ACCESS_NETWORKS = ["10.69.0.0/16"]
    caller = anonymous(client)

    assert caller.get(VOLUNTEERS, REMOTE_ADDR="10.69.4.7").status_code == 200
    assert caller.get(VOLUNTEERS, REMOTE_ADDR="198.51.100.9").status_code == 403


def test_the_client_address_is_the_one_drfs_throttles_count() -> None:
    """A forged prefix must not buy admission. `.env.sample` is the argument
    for NUM_PROXIES, and a network rule reading the header differently from a
    rate limit would be two definitions of one caller."""
    request = APIRequestFactory().get(
        VOLUNTEERS,
        headers={"x-forwarded-for": "10.69.0.1, 203.0.113.7, 10.42.0.1"},
        REMOTE_ADDR="10.42.0.9",
    )

    assert client_address(request) == "203.0.113.7"


def test_a_header_too_short_for_this_deployments_proxies_is_no_address_at_all() -> None:
    """The clamp DRF applies, and why admission will not live with it, is
    ``client_address`` in inventory/permissions.py. This is that refusal."""
    request = APIRequestFactory().get(
        VOLUNTEERS,
        headers={"x-forwarded-for": "10.69.0.1"},
        REMOTE_ADDR="198.51.100.9",
    )

    assert client_address(request) == ""


def test_a_request_carrying_no_forwarded_header_still_reads_its_own_address() -> None:
    """The case with no header at all is untouched, which is what the two
    mesh_only tests above are asserting through."""
    request = APIRequestFactory().get(VOLUNTEERS, REMOTE_ADDR="10.69.4.7")

    assert client_address(request) == "10.69.4.7"


def test_mesh_only_refuses_a_caller_who_wrote_the_whole_forwarded_header(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """``curl -H 'X-Forwarded-For: 10.69.0.1'`` from anywhere at all, which one
    forged entry bought under the shipped NUM_PROXIES=2: the posture that exists
    to answer the mesh answered the internet."""
    settings.VOLUNTEER_ACCESS = postures.MESH_ONLY
    settings.VOLUNTEER_ACCESS_NETWORKS = ["10.69.0.0/16"]

    refused = anonymous(client).get(
        VOLUNTEERS,
        headers={"x-forwarded-for": "10.69.0.1"},
        REMOTE_ADDR="198.51.100.9",
    )

    assert refused.status_code == 403


def test_the_guard_counts_entries_and_entries_are_free() -> None:
    """Recorded rather than fixed, and `client_address` says why it cannot be
    fixed here: one trailing comma is two entries and the caller's own value
    comes back. `inventory-tng-3hgc` is the sound version. This test is what
    tells whoever lands it that this line moved."""
    request = APIRequestFactory().get(
        VOLUNTEERS,
        headers={"x-forwarded-for": "10.69.0.1,"},
        REMOTE_ADDR="198.51.100.9",
    )

    assert client_address(request) == "10.69.0.1"


def test_an_address_that_will_not_parse_is_in_no_range() -> None:
    assert postures.within("not-an-address", postures.networks(postures.OPEN, ["10.69.0.0/16"])) is False


def test_a_range_substituted_after_the_process_booted_admits_nobody() -> None:
    """Boot has already refused a list like this, so reaching ``within`` with
    one means the setting was replaced afterwards. Dropping the entry rather
    than raising leaves the posture refusing, which is the safe reading."""
    assert postures.within("10.69.0.1", ["not-a-range"]) is False


# --------------------------------------------------------------------------
# /api/me: telling "enrol first" from "not allowed"
# --------------------------------------------------------------------------


def test_a_deployment_that_asks_for_no_device_says_so(client: Client) -> None:
    assert anonymous(client).get(ME).json()["enrolment"] == postures.NOT_REQUIRED


@pytest.mark.parametrize(
    ("posture", "expected"),
    [(postures.ENROLLED_SELF, postures.ENROL_SELF), (postures.ENROLLED_CODE, postures.ENROL_WITH_CODE)],
)
def test_a_device_that_has_not_enrolled_is_told_what_it_would_take(
    client: Client, settings: Settings, posture: str, expected: str
) -> None:
    settings.VOLUNTEER_ACCESS = posture
    settings.VOLUNTEER_ACCESS_CODE = "grand-street"

    assert anonymous(client).get(ME).json()["enrolment"] == expected


def test_a_device_that_has_enrolled_is_told_that_instead(enrolled_device: tuple[Client, str]) -> None:
    caller, token = enrolled_device

    answer = caller.get(ME, headers={postures.DEVICE_HEADER: token}).json()

    assert answer["enrolment"] == postures.ENROLLED


def test_somebody_signed_in_is_never_asked_to_enrol(editor: Client, settings: Settings) -> None:
    """``enrolment_state`` in inventory/permissions.py is the argument, and the
    cost of the other order was a whole application replaced by an enrolment
    screen for an administrator the API was already answering."""
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_CODE
    settings.VOLUNTEER_ACCESS_CODE = "grand-street"

    assert editor.get(ME).json()["enrolment"] == postures.NOT_REQUIRED


def test_one_page_load_asks_the_device_table_once(
    enrolled_device: tuple[Client, str], django_assert_num_queries: Any
) -> None:
    """Seven identical selects for one page load, and they were the only seven
    queries it made. What is memoised where, and why there, is ``enrolled`` in
    inventory/permissions.py."""
    caller, token = enrolled_device

    with django_assert_num_queries(1):
        caller.get(ME, headers={postures.DEVICE_HEADER: token})


def test_the_client_is_told_how_much_it_has_to_type(client: Client, settings: Settings) -> None:
    """The second thing a client cannot infer from an answer; ``CurrentUserView``
    in inventory/views.py says what it does about it."""
    settings.SEARCH_MINIMUM = 3

    assert client.get(ME).json()["search_minimum"] == 3


def test_a_deployment_that_sets_nothing_says_nought(client: Client) -> None:
    assert client.get(ME).json()["search_minimum"] == 0


# --------------------------------------------------------------------------
# SEARCH_MINIMUM, which is a usability decision and never a defence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("minimum", [0, 1, 2, 3])
def test_the_list_answers_once_enough_has_been_typed(
    client: Client, settings: Settings, volunteer: Volunteer, minimum: int
) -> None:
    settings.SEARCH_MINIMUM = minimum

    assert names(client.get(VOLUNTEERS, {"search": "Sean"})) == ["Sean"]


@pytest.mark.parametrize(("minimum", "expected"), [(0, ["Sean"]), (1, []), (2, []), (3, [])])
def test_the_bare_collection_is_withheld_wherever_a_minimum_is_set(
    client: Client, settings: Settings, volunteer: Volunteer, minimum: int, expected: list[str]
) -> None:
    """ "Do not show the list until somebody types" is the whole of what this
    buys, and at nought it is today's first paint."""
    settings.SEARCH_MINIMUM = minimum

    assert names(client.get(VOLUNTEERS)) == expected


def test_a_search_shorter_than_the_minimum_finds_nobody(client: Client, settings: Settings) -> None:
    settings.SEARCH_MINIMUM = 3
    Volunteer.objects.create(display_name="Sean")

    assert names(client.get(VOLUNTEERS, {"search": "Se"})) == []


def test_the_withdrawn_rows_are_not_a_search_and_are_not_narrowed(editor: Client, settings: Settings) -> None:
    """``WithdrawnRows`` is a different question from the pick-list, and says so
    itself: an administrator repairing a mistaken merge has nothing to type into
    a search box. Narrowing it closed the route decision 0014 point 1 provides,
    silently."""
    settings.SEARCH_MINIMUM = 3
    Volunteer.objects.create(display_name="Sean", active=False)

    assert names(editor.get(VOLUNTEERS, {"withdrawn": "true"})) == ["Sean"]


def test_the_ordinary_collection_under_another_spelling_is_still_narrowed(
    editor: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """`?withdrawn=false` is what a generated client sends for the default, so
    it is the pick-list and not the withdrawn listing. Exempted along with it,
    it handed back the whole first page with nothing typed."""
    settings.SEARCH_MINIMUM = 3

    assert names(editor.get(VOLUNTEERS, {"withdrawn": "false"})) == []


# --------------------------------------------------------------------------
# ANONYMOUS_READ_RATE, which is for the membership oracle and not the roster
# --------------------------------------------------------------------------


def test_reads_are_not_counted_at_all_by_default(client: Client, settings: Settings, volunteer: Volunteer) -> None:
    settings.VOLUNTEER_ACCESS = postures.OPEN
    caller = anonymous(client)

    assert all(caller.get(VOLUNTEERS).status_code == 200 for _ in range(6))


def test_a_rate_stops_a_caller_asking_the_membership_question_all_day(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """One request per address checked is what a limit converts into hours of
    obvious traffic; inventory-tng-81f7.1 is why it is sized for that and not
    for the roster."""
    settings.VOLUNTEER_ACCESS = postures.OPEN
    settings.ANONYMOUS_READ_RATE = "3/min"
    caller = anonymous(client)

    answers = [caller.get(VOLUNTEERS, {"search": f"someone{index}@northmail.test"}).status_code for index in range(4)]

    assert answers == [200, 200, 200, 429]


def test_a_read_limit_does_not_count_somebody_signed_in(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    settings.ANONYMOUS_READ_RATE = "1/min"

    assert all(client.get(VOLUNTEERS).status_code == 200 for _ in range(3))


def test_a_refused_read_does_not_tell_anybody_their_work_was_lost(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """A refused read must not promise anything about what the caller holds.

    Why the body carries two sentences rather than one is ``exception_handler``
    in inventory/api.py, and the cost of the wrong one is a re-scanned cart.
    """
    settings.VOLUNTEER_ACCESS = postures.OPEN
    settings.ANONYMOUS_READ_RATE = "1/min"
    caller = anonymous(client)
    caller.get(VOLUNTEERS)

    refused = caller.get(VOLUNTEERS)

    assert refused.status_code == 429
    assert "saved" not in refused.json()["detail"]
    assert refused.json()["retry_after_seconds"] > 0


def test_a_refused_submission_still_says_nothing_was_saved(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The half that has to keep working, asserted beside the half that
    changed."""
    monkeypatch.setattr(AppendBurstThrottle, "rate", "1/min", raising=False)
    post(client, "volunteers", {"display_name": "Wren Xu"})

    refused = post(client, "volunteers", {"display_name": "Noor Haddad"})

    assert refused.status_code == 429
    assert "Nothing was saved" in refused.json()["detail"]


def test_the_label_sheet_is_counted_where_a_deployment_counts_reads(client: Client, settings: Settings) -> None:
    """``LabelSheetView`` says why it is the endpoint that most needs this. It
    did not need it while a session was required, and four of the five postures
    do not require one."""
    settings.VOLUNTEER_ACCESS = postures.OPEN
    settings.ANONYMOUS_READ_RATE = "1/min"
    caller = anonymous(client)
    sheet = reverse("label-sheet")

    assert caller.get(sheet, {"code": "ZZZZZZZZZZ"}).status_code == 200
    assert caller.get(sheet, {"code": "ZZZZZZZZZZ"}).status_code == 429


def test_typing_in_the_pick_list_does_not_spend_a_print_run(
    client: Client, settings: Settings, volunteer: Volunteer
) -> None:
    """`LabelSheetThrottle` is the argument: a request per keystroke must not
    empty what forty QR encodes need."""
    settings.VOLUNTEER_ACCESS = postures.OPEN
    settings.ANONYMOUS_READ_RATE = "1/min"
    caller = anonymous(client)
    caller.get(VOLUNTEERS)

    assert caller.get(reverse("label-sheet"), {"code": "ZZZZZZZZZZ"}).status_code == 200


def test_a_read_limit_does_not_reach_the_append_path(client: Client, settings: Settings) -> None:
    """The two are opposites on purpose: this counts safe methods and the
    append throttles count nothing else."""
    settings.VOLUNTEER_ACCESS = postures.OPEN
    settings.ANONYMOUS_READ_RATE = "1/min"
    caller = anonymous(client)
    caller.get(VOLUNTEERS)

    assert post(caller, "volunteers", {"display_name": "Wren Xu"}).status_code == 201


# --------------------------------------------------------------------------
# CUSTODY_VISIBILITY
# --------------------------------------------------------------------------


def test_a_custody_location_is_withheld_from_an_anonymous_caller_by_default(
    client: Client, settings: Settings, custody: Location, warehouse: Location
) -> None:
    settings.VOLUNTEER_ACCESS = postures.OPEN

    assert [place["name"] for place in rows(anonymous(client).get(LOCATIONS))] == ["131 Broome"]


def test_the_same_row_is_withheld_when_it_is_asked_for_by_id(
    client: Client, settings: Settings, custody: Location
) -> None:
    settings.VOLUNTEER_ACCESS = postures.OPEN

    assert anonymous(client).get(reverse("location-detail", args=[custody.pk])).status_code == 404


def test_anonymous_custody_discloses_who_is_holding_stock(
    client: Client, settings: Settings, custody: Location, warehouse: Location, volunteer: Volunteer
) -> None:
    """The reserve act, and the most alarming thing in the deck: three
    reasonable requests joined into a named person's address and how much
    hardware is in their home."""
    settings.VOLUNTEER_ACCESS = postures.OPEN
    settings.CUSTODY_VISIBILITY = postures.ANONYMOUS

    shown = rows(anonymous(client).get(LOCATIONS))

    assert sorted(place["name"] for place in shown) == ["131 Broome", "Sean"]
    assert [place["held_by"] for place in shown if place["name"] == "Sean"] == [volunteer.pk]


def test_somebody_signed_in_still_sees_every_location(client: Client, custody: Location, warehouse: Location) -> None:
    assert len(rows(client.get(LOCATIONS))) == 2


# --------------------------------------------------------------------------
# What a bad value does, which is stop the process rather than be obeyed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "value", "allowed"),
    [
        ("ANONYMOUS_PAYLOAD", "blurred", postures.ANONYMOUS_PAYLOAD_VALUES),
        ("VOLUNTEER_ACCESS", "nobody", postures.VOLUNTEER_ACCESS_VALUES),
        ("CUSTODY_VISIBILITY", "hidden", postures.CUSTODY_VISIBILITY_VALUES),
    ],
)
def test_a_word_this_setting_does_not_know_stops_the_process(
    setting: str, value: str, allowed: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match=setting):
        postures.chosen(setting, value, allowed)


def test_a_search_minimum_outside_the_four_is_refused() -> None:
    with pytest.raises(ValueError, match="SEARCH_MINIMUM"):
        postures.search_minimum(4)


def test_every_value_the_settings_offer_is_accepted() -> None:
    """Guards the three refusals above against passing for the wrong reason."""
    for value in postures.ANONYMOUS_PAYLOAD_VALUES:
        assert postures.chosen("ANONYMOUS_PAYLOAD", value, postures.ANONYMOUS_PAYLOAD_VALUES) == value
    for minimum in postures.SEARCH_MINIMUM_VALUES:
        assert postures.search_minimum(minimum) == minimum


def test_an_enrolment_code_that_admits_nobody_is_refused_at_boot() -> None:
    """`postures.enrolment_code` says what an empty one would look like."""
    with pytest.raises(ValueError, match="VOLUNTEER_ACCESS_CODE"):
        postures.enrolment_code(postures.ENROLLED_CODE, "  ")


def test_an_enrolment_code_is_only_demanded_by_the_posture_that_uses_one() -> None:
    assert postures.enrolment_code(postures.OPEN, "") == ""
    assert postures.enrolment_code(postures.ENROLLED_CODE, " grand-street ") == "grand-street"


def test_a_range_that_will_not_parse_stops_the_process() -> None:
    with pytest.raises(ValueError, match="VOLUNTEER_ACCESS_NETWORKS"):
        postures.networks(postures.MESH_ONLY, ["10.69.0.0/64"])


def test_mesh_only_with_no_ranges_stops_the_process() -> None:
    with pytest.raises(ValueError, match="VOLUNTEER_ACCESS_NETWORKS"):
        postures.networks(postures.MESH_ONLY, [])


def test_ranges_are_only_demanded_by_the_posture_that_uses_them() -> None:
    assert postures.networks(postures.OPEN, []) == []


def test_an_empty_code_matches_nothing_that_was_offered() -> None:
    """The second guard rather than the only one: boot refuses the arrangement
    outright, and this is what would happen if it ever did not."""
    assert postures.code_matches("", "") is False
    assert postures.code_matches("grand-street", "") is False


# --------------------------------------------------------------------------
# The audits the rest of this repository keeps, asked about the new surface
# --------------------------------------------------------------------------


def test_the_schema_still_describes_a_guarded_pick_list(schema: Any) -> None:
    """`VolunteerAccess` is never `AllowAny`, so the committed document does
    not move when a deployment chooses a posture -- see the class."""
    assert "403" in schema["paths"]["/api/volunteers"]["get"]["responses"]


def test_enrolling_a_device_is_described_in_the_committed_schema(schema: Any) -> None:
    """Including the refusal, which is this one endpoint's own to declare.

    Nothing guards it, so `PolicyAwareAutoSchema` derives no 403 for it and the
    view names one itself -- see `DeviceEnrolmentView`.
    """
    responses = schema["paths"]["/api/devices"]["post"]["responses"]

    assert "201" in responses
    assert "403" in responses


def test_an_enrolled_device_may_still_not_edit_the_catalogue(enrolled_device: tuple[Client, str], item: Item) -> None:
    """A device credential answers "may this caller read", never "who is
    this". Every write an administrator owns is still an administrator's."""
    caller, token = enrolled_device

    refused = caller.patch(
        reverse("item-detail", args=[item.pk]),
        data={"name": "Something else"},
        content_type="application/json",
        headers={postures.DEVICE_HEADER: token},
    )

    assert refused.status_code == 403


def test_appending_still_works_for_whoever_the_posture_admits(
    client: Client, settings: Settings, volunteer: Volunteer, item: Item, warehouse: Location
) -> None:
    """The endpoint this project exists for, under the posture that asks most
    of a device: the friction is the enrolment and nothing after it."""
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_CODE
    settings.VOLUNTEER_ACCESS_CODE = "grand-street"
    caller = anonymous(client)
    token = post(caller, "devices", {"code": "grand-street"}).json()["token"]

    recorded = caller.post(
        reverse("stock-transactions"),
        data={
            "actor": volunteer.pk,
            "kind": StockTransaction.Kind.RECEIPT,
            "movements": [{"item": item.pk, "quantity": "3", "to_location": warehouse.pk}],
        },
        content_type="application/json",
        headers={postures.DEVICE_HEADER: token},
    )

    assert recorded.status_code == 201
    assert StockMovement.objects.count() == 1


def test_an_administrator_can_revoke_one_device_without_touching_the_others(
    settings: Settings, client: Client, administrator: User
) -> None:
    """The row is what makes act four's claim true, and the admin is where it
    is acted on."""
    settings.VOLUNTEER_ACCESS = postures.ENROLLED_SELF
    caller = anonymous(client)
    first = post(caller, "devices", {}).json()["token"]
    second = post(caller, "devices", {}).json()["token"]
    Device.objects.filter(identifier=postures.presented_device(first)).update(revoked_at="2026-08-24T12:00:00Z")

    assert caller.get(ME, headers={postures.DEVICE_HEADER: first}).json()["enrolment"] == postures.ENROL_SELF
    assert caller.get(ME, headers={postures.DEVICE_HEADER: second}).json()["enrolment"] == postures.ENROLLED
