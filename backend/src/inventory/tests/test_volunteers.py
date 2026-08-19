"""Tests for the volunteer pick-list and self-registration.

The behaviour that matters here is not CRUD. The sheet this replaces holds 102
spellings for 65 people, and the endpoint's job is to make the next volunteer
find themselves rather than add themselves again -- so the tests are mostly
about what search finds, and who is offered as a choice at all.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from inventory.models import Volunteer
from inventory.serializers import VolunteerConflictSerializer
from inventory.tests.helpers import patch, post

pytestmark = pytest.mark.django_db

URL = reverse("volunteers")


def names(response: Any) -> list[str]:
    return [volunteer["display_name"] for volunteer in response.json()["results"]]


# --------------------------------------------------------------------------
# Finding somebody who is already there
# --------------------------------------------------------------------------


def test_the_list_offers_every_active_volunteer(client: Client, volunteer: Volunteer) -> None:
    Volunteer.objects.create(display_name="Olivia")
    assert names(client.get(URL)) == ["Olivia", "Sean"]


def test_search_finds_a_name_by_the_first_letters_typed(client: Client, volunteer: Volunteer) -> None:
    """Typing is how the picker is used, and two letters are not similar to
    anything -- which is why the search is not similarity alone.
    """
    Volunteer.objects.create(display_name="Olivia")
    assert names(client.get(URL, {"search": "Se"})) == ["Sean"]


def test_search_finds_a_name_that_was_spelled_differently(client: Client) -> None:
    """The point of the trigram index. Somebody typing Shaun must be shown the
    Sean already in the ledger, or they will add a second one.
    """
    Volunteer.objects.create(display_name="Sean McGinnis")
    assert names(client.get(URL, {"search": "Shaun McGinnis"})) == ["Sean McGinnis"]


def test_search_puts_the_closest_match_first(client: Client) -> None:
    Volunteer.objects.create(display_name="Sean")
    Volunteer.objects.create(display_name="Seanne Bartholomew")
    assert names(client.get(URL, {"search": "Sean"}))[0] == "Sean"


def test_search_finds_a_volunteer_by_the_email_address_they_typed(client: Client) -> None:
    """The field that exists to tell two Seans apart has to be searchable.

    A volunteer who types their own address and is shown nobody adds a
    duplicate -- the one outcome this endpoint exists to prevent, reached
    through the field added to prevent it.
    """
    Volunteer.objects.create(display_name="Sean McGinnis", email="sean@example.org")
    Volunteer.objects.create(display_name="Olivia")
    assert names(client.get(URL, {"search": "sean@example.org"})) == ["Sean McGinnis"]


def test_search_finds_a_volunteer_by_their_slack_id(client: Client) -> None:
    Volunteer.objects.create(display_name="Sean McGinnis", slack_id="U024BE7LH")
    assert names(client.get(URL, {"search": "U024BE7LH"})) == ["Sean McGinnis"]


def test_an_identifier_is_matched_however_it_was_capitalised(client: Client) -> None:
    """An address copied off a phone keyboard arrives however it arrives."""
    Volunteer.objects.create(display_name="Sean McGinnis", email="sean@example.org")
    assert names(client.get(URL, {"search": "Sean@Example.ORG"})) == ["Sean McGinnis"]


def test_part_of_an_address_finds_nobody(client: Client) -> None:
    """Matching an identifier is a deliberate act and a whole one.

    A substring search over identifiers would turn the pick-list into a
    directory anybody could walk a letter at a time -- and nobody types half
    an address looking for themselves.
    """
    Volunteer.objects.create(display_name="Sean McGinnis", email="sean@example.org")
    assert names(client.get(URL, {"search": "@example.org"})) == []


def test_an_identifier_on_somebody_the_list_will_not_show_finds_nobody(client: Client) -> None:
    """Widening the search does not widen who is offered.

    A merged duplicate keeps its address, and this is the search that comes
    back empty and sends the volunteer to self-registration -- where the 409
    of decision 0015 names the survivor.
    """
    survivor = Volunteer.objects.create(display_name="Sean McGinnis")
    Volunteer.objects.create(display_name="sean", email="sean@example.org", merged_into=survivor)
    assert names(client.get(URL, {"search": "sean@example.org"})) == []


def test_search_that_matches_nobody_is_an_empty_list_not_an_error(client: Client, volunteer: Volunteer) -> None:
    """An empty result is what tells the client to offer self-registration."""
    response = client.get(URL, {"search": "Zzyzx"})
    assert response.status_code == 200
    assert names(response) == []


# --------------------------------------------------------------------------
# Who is not a choice
# --------------------------------------------------------------------------


def test_a_merged_duplicate_is_not_offered(client: Client, volunteer: Volunteer) -> None:
    """A merge leaves the ledger untouched, so the duplicate keeps its past
    work; it just stops being something anyone can pick.
    """
    Volunteer.objects.create(display_name="Sean B", merged_into=volunteer)
    assert names(client.get(URL, {"search": "Sean"})) == ["Sean"]


def test_an_inactive_volunteer_is_not_offered(client: Client, volunteer: Volunteer) -> None:
    Volunteer.objects.create(display_name="Sean Retired", active=False)
    assert names(client.get(URL, {"search": "Sean"})) == ["Sean"]


# --------------------------------------------------------------------------
# Adding yourself
# --------------------------------------------------------------------------


def test_a_volunteer_can_add_themselves(client: Client) -> None:
    response = post(
        client,
        "volunteers",
        {"display_name": "Olivia", "email": "olivia@example.org"},
    )
    assert response.status_code == 201
    assert response.json()["display_name"] == "Olivia"
    assert Volunteer.objects.get(display_name="Olivia").active is True


def test_a_new_volunteer_needs_only_a_name(client: Client) -> None:
    """Most volunteers supply nothing else; 45% of the historical rows did not."""
    response = post(client, "volunteers", {"display_name": "Olivia"})
    assert response.status_code == 201


def test_a_name_of_spaces_is_not_a_name(client: Client) -> None:
    response = post(client, "volunteers", {"display_name": "   "})
    assert response.status_code == 400
    assert "display_name" in response.json()


def test_a_name_is_stored_without_its_surrounding_spaces(client: Client) -> None:
    post(client, "volunteers", {"display_name": "  Olivia  "})
    assert Volunteer.objects.filter(display_name="Olivia").exists()


def test_a_duplicate_email_is_reported_rather_than_raised(client: Client) -> None:
    """Two volunteers cannot share an address, and the clash is reported
    rather than raised as an integrity error the volunteer cannot act on.
    """
    Volunteer.objects.create(display_name="Olivia", email="olivia@example.org")
    response = post(
        client,
        "volunteers",
        {"display_name": "Olivia Again", "email": "olivia@example.org"},
    )
    assert response.status_code == 400
    assert "email" in response.json()


def test_a_duplicate_email_on_a_live_volunteer_stays_a_plain_rejection(client: Client) -> None:
    """The searcher could have found them, so there is nothing to point at
    that the pick-list does not already offer -- and naming somebody who is
    findable anyway would turn this endpoint into an address lookup.
    """
    Volunteer.objects.create(display_name="Olivia", email="olivia@example.org")
    response = post(
        client,
        "volunteers",
        {"display_name": "Olivia Again", "email": "olivia@example.org"},
    )
    assert response.status_code == 400
    assert "volunteer" not in response.json()


# --------------------------------------------------------------------------
# The address is held by somebody the list will not show
# --------------------------------------------------------------------------


def test_an_email_held_by_a_merged_record_offers_the_survivor(client: Client, volunteer: Volunteer) -> None:
    """The dead end this endpoint used to have: a 400 naming a record the API
    then refuses to show. The survivor of the merge is offered instead, so the
    volunteer has somewhere to go from the one screen self-registration exists
    for.
    """
    Volunteer.objects.create(display_name="Sean B", email="sean@example.org", merged_into=volunteer)
    response = post(
        client,
        "volunteers",
        {"display_name": "Sean", "email": "sean@example.org"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "volunteer_merged"
    assert body["field"] == "email"
    assert body["selectable"] is True
    assert body["volunteer"]["id"] == volunteer.pk
    assert body["volunteer"]["display_name"] == "Sean"
    assert Volunteer.objects.filter(display_name="Sean").count() == 1


def test_the_offered_survivor_is_one_the_pick_list_actually_shows(client: Client, volunteer: Volunteer) -> None:
    """The 409 is only useful if the client can act on it, so what it names has
    to be selectable by the same rule the list uses.
    """
    Volunteer.objects.create(display_name="Sean B", email="sean@example.org", merged_into=volunteer)
    body = post(
        client,
        "volunteers",
        {"display_name": "Sean", "email": "sean@example.org"},
    ).json()
    listed = [found["id"] for found in client.get(URL, {"search": "Sean"}).json()["results"]]
    assert body["volunteer"]["id"] in listed


def test_a_chain_of_merges_offers_the_end_of_it(client: Client, volunteer: Volunteer) -> None:
    """A duplicate merged into a record that was itself merged later. Only the
    record left standing is worth offering.
    """
    middle = Volunteer.objects.create(display_name="Sean M", merged_into=volunteer)
    Volunteer.objects.create(display_name="Sean B", email="sean@example.org", merged_into=middle)
    body = post(
        client,
        "volunteers",
        {"display_name": "Sean", "email": "sean@example.org"},
    ).json()
    assert body["volunteer"]["id"] == volunteer.pk


def test_a_merge_into_a_retired_record_says_so_rather_than_offering_it(client: Client) -> None:
    """Nothing here is pickable, so the client must not present it as a choice."""
    survivor = Volunteer.objects.create(display_name="Sean McGinnis", active=False)
    Volunteer.objects.create(display_name="Sean B", email="sean@example.org", merged_into=survivor)
    response = post(
        client,
        "volunteers",
        {"display_name": "Sean", "email": "sean@example.org"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "volunteer_merged"
    assert body["selectable"] is False
    assert body["volunteer"]["id"] == survivor.pk


def test_an_email_held_by_a_retired_record_names_that_record(client: Client) -> None:
    """No merge, so nothing survived it: the record holding the address is the
    record to talk about, and an administrator has to restore it.
    """
    retired = Volunteer.objects.create(display_name="Sean Retired", email="sean@example.org", active=False)
    response = post(
        client,
        "volunteers",
        {"display_name": "Sean", "email": "sean@example.org"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "volunteer_inactive"
    assert body["selectable"] is False
    assert body["volunteer"]["id"] == retired.pk
    assert Volunteer.objects.filter(display_name="Sean").count() == 0


def test_a_slack_id_held_by_a_merged_record_is_answered_the_same_way(client: Client, volunteer: Volunteer) -> None:
    """Both identifiers are unique where present, so both can strand somebody."""
    Volunteer.objects.create(display_name="Sean B", slack_id="U123", merged_into=volunteer)
    response = post(
        client,
        "volunteers",
        {"display_name": "Sean", "slack_id": "U123"},
    )
    assert response.status_code == 409
    assert response.json()["field"] == "slack_id"
    assert response.json()["volunteer"]["id"] == volunteer.pk


def test_a_merged_email_with_a_bad_name_is_still_a_plain_rejection(client: Client, volunteer: Volunteer) -> None:
    """The 409 answers one specific dead end. A submission that is invalid for
    other reasons has not reached it yet.
    """
    Volunteer.objects.create(display_name="Sean B", email="sean@example.org", merged_into=volunteer)
    response = post(
        client,
        "volunteers",
        {"display_name": "   ", "email": "sean@example.org"},
    )
    assert response.status_code == 400


def test_a_body_that_is_not_a_volunteer_at_all_is_still_a_plain_rejection(client: Client) -> None:
    """The conflict is looked for in the rejection DRF produced, so a payload
    that is not even a dictionary must fall through it rather than be picked
    apart as though it were one.
    """
    response = client.post(URL, data=["Sean"], content_type="application/json")
    assert response.status_code == 400


def test_a_merge_cycle_is_answered_rather_than_followed_forever(client: Client) -> None:
    """The model forbids merging a record into itself and nothing forbids a
    longer loop, so the walk has to end on its own.
    """
    first = Volunteer.objects.create(display_name="Sean One", email="sean@example.org")
    second = Volunteer.objects.create(display_name="Sean Two", merged_into=first)
    first.merged_into = second
    first.save()
    response = post(
        client,
        "volunteers",
        {"display_name": "Sean", "email": "sean@example.org"},
    )
    assert response.status_code == 409
    assert response.json()["selectable"] is False


def test_a_duplicate_slack_id_is_reported_rather_than_raised(client: Client) -> None:
    Volunteer.objects.create(display_name="Olivia", slack_id="U123")
    response = post(
        client,
        "volunteers",
        {"display_name": "Olivia Again", "slack_id": "U123"},
    )
    assert response.status_code == 400
    assert "slack_id" in response.json()


def test_two_volunteers_may_share_no_email_at_all(client: Client) -> None:
    """NULL rather than "": the partial index means absent values do not
    collide, and most volunteers supply nothing.
    """
    for name in ("Olivia", "Priya"):
        response = post(client, "volunteers", {"display_name": name})
        assert response.status_code == 201


@pytest.mark.parametrize("field", ["email", "slack_id"])
def test_an_empty_identifier_is_stored_as_no_identifier(client: Client, field: str) -> None:
    """A form submits "" for a field nobody filled in, and "" is a value.

    The partial unique indexes cover every non-NULL value, so storing "" would
    make the *second* volunteer who skipped the field a constraint violation --
    a 500 naming an index, for the most ordinary submission there is.
    """
    for name in ("Olivia", "Priya"):
        response = post(client, "volunteers", {"display_name": name, field: ""})
        assert response.status_code == 201, response.content
    assert Volunteer.objects.filter(**{f"{field}__isnull": True}).count() == 2


def test_two_volunteers_with_one_name_are_both_listed_once(client: Client) -> None:
    """Duplicate names are the point of this list, so the order must not be a
    tie: PostgreSQL may return tied rows in any order, and a paginated list
    that reorders between pages repeats one volunteer and hides another.
    """
    first = Volunteer.objects.create(display_name="Sean")
    second = Volunteer.objects.create(display_name="Sean")
    listed = [volunteer["id"] for volunteer in client.get(URL).json()["results"]]
    searched = [volunteer["id"] for volunteer in client.get(URL, {"search": "Sean"}).json()["results"]]
    assert listed == [first.pk, second.pk]
    assert searched == [first.pk, second.pk]


def test_the_pick_list_requires_authentication(client: Client) -> None:
    client.logout()
    assert client.get(URL).status_code in (401, 403)


def test_the_conflict_body_is_the_one_the_schema_documents(client: Client, volunteer: Volunteer) -> None:
    """The body is hand-built and the schema is declared separately; they must agree.

    A field added to one and not the other is invisible until a generated
    client goes looking for it.
    """
    Volunteer.objects.create(display_name="sean", email="sean@example.org", merged_into=volunteer)
    response = post(
        client,
        "volunteers",
        {"display_name": "New Sean", "email": "sean@example.org"},
    )
    assert response.status_code == 409, response.content
    assert set(response.json()) == set(VolunteerConflictSerializer().fields)


def test_a_second_clash_with_somebody_live_keeps_the_whole_rejection_plain(
    client: Client,
    volunteer: Volunteer,
) -> None:
    """One dead end and one fixable field is not a dead end.

    Answering with the merge would drop the email complaint from the response,
    so the volunteer would fix their Slack ID, resubmit, and meet a fresh 400 --
    exactly the hidden field decision 0015 says a 409 must never cause.
    """
    Volunteer.objects.create(display_name="Live", email="taken@example.org")
    Volunteer.objects.create(display_name="sean", slack_id="U123", merged_into=volunteer)
    response = post(
        client,
        "volunteers",
        {"display_name": "New", "email": "taken@example.org", "slack_id": "U123"},
    )
    assert response.status_code == 400, response.content
    assert "email" in response.json()


# --------------------------------------------------------------------------
# Repairing the list, which only an administrator may do
# --------------------------------------------------------------------------


def repair(client: Client, volunteer: Volunteer, body: dict[str, Any]) -> Any:
    """One administrator's edit to one volunteer's record."""
    return patch(client, "volunteer-detail", body, volunteer.pk)


def test_an_administrator_merges_a_duplicate(editor: Client, volunteer: Volunteer) -> None:
    """The operation the 102 spellings of 65 people need. Decision 0012 point 2."""
    duplicate = Volunteer.objects.create(display_name="sean")
    assert repair(editor, duplicate, {"merged_into": volunteer.pk}).status_code == 200
    duplicate.refresh_from_db()
    assert duplicate.merged_into == volunteer
    assert names(editor.get(URL)) == ["Sean"]


def test_a_volunteer_may_not_merge_anybody(client: Client, volunteer: Volunteer) -> None:
    duplicate = Volunteer.objects.create(display_name="sean")
    assert repair(client, duplicate, {"merged_into": volunteer.pk}).status_code == 403


def test_a_volunteer_cannot_be_merged_into_themselves(editor: Client, volunteer: Volunteer) -> None:
    assert repair(editor, volunteer, {"merged_into": volunteer.pk}).status_code == 400


def test_a_merge_points_at_somebody_the_list_still_offers(editor: Client, volunteer: Volunteer) -> None:
    """Otherwise a chain forms, and a reader has to walk it to find anybody."""
    already_merged = Volunteer.objects.create(display_name="s.", merged_into=volunteer)
    duplicate = Volunteer.objects.create(display_name="sean")
    assert repair(editor, duplicate, {"merged_into": already_merged.pk}).status_code == 400


def test_a_merge_into_a_retired_record_is_refused(editor: Client, volunteer: Volunteer) -> None:
    retired = Volunteer.objects.create(display_name="Gone", active=False)
    assert repair(editor, volunteer, {"merged_into": retired.pk}).status_code == 400


def test_an_administrator_restores_a_retired_record(editor: Client) -> None:
    """What the 409 in decision 0015 tells the volunteer to go and ask for."""
    retired = Volunteer.objects.create(display_name="Gone", email="gone@example.org", active=False)
    assert repair(editor, retired, {"active": True}).status_code == 200
    assert names(editor.get(URL)) == ["Gone"]


def test_a_merged_record_is_reachable_by_the_administrator_who_must_repair_it(
    editor: Client,
    client: Client,
    volunteer: Volunteer,
) -> None:
    """And by nobody else: the pick-list does not offer it, so nor does this."""
    duplicate = Volunteer.objects.create(display_name="sean", merged_into=volunteer)
    url = reverse("volunteer-detail", args=[duplicate.pk])
    assert editor.get(url).status_code == 200
    assert client.get(url).status_code == 404


def test_undoing_a_merge_is_a_merge_into_nobody(editor: Client, volunteer: Volunteer) -> None:
    duplicate = Volunteer.objects.create(display_name="sean", merged_into=volunteer)
    assert repair(editor, duplicate, {"merged_into": None}).status_code == 200
    duplicate.refresh_from_db()
    assert duplicate.merged_into is None
