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
    response = client.post(
        URL,
        data={"display_name": "Olivia", "email": "olivia@example.org"},
        content_type="application/json",
    )
    assert response.status_code == 201
    assert response.json()["display_name"] == "Olivia"
    assert Volunteer.objects.get(display_name="Olivia").active is True


def test_a_new_volunteer_needs_only_a_name(client: Client) -> None:
    """Most volunteers supply nothing else; 45% of the historical rows did not."""
    response = client.post(URL, data={"display_name": "Olivia"}, content_type="application/json")
    assert response.status_code == 201


def test_a_name_of_spaces_is_not_a_name(client: Client) -> None:
    response = client.post(URL, data={"display_name": "   "}, content_type="application/json")
    assert response.status_code == 400
    assert "display_name" in response.json()


def test_a_name_is_stored_without_its_surrounding_spaces(client: Client) -> None:
    client.post(URL, data={"display_name": "  Olivia  "}, content_type="application/json")
    assert Volunteer.objects.filter(display_name="Olivia").exists()


def test_a_duplicate_email_is_reported_rather_than_raised(client: Client) -> None:
    """Two volunteers cannot share an address, and the clash is reported
    rather than raised as an integrity error the volunteer cannot act on.
    """
    Volunteer.objects.create(display_name="Olivia", email="olivia@example.org")
    response = client.post(
        URL,
        data={"display_name": "Olivia Again", "email": "olivia@example.org"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "email" in response.json()


def test_a_duplicate_slack_id_is_reported_rather_than_raised(client: Client) -> None:
    Volunteer.objects.create(display_name="Olivia", slack_id="U123")
    response = client.post(
        URL,
        data={"display_name": "Olivia Again", "slack_id": "U123"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "slack_id" in response.json()


def test_two_volunteers_may_share_no_email_at_all(client: Client) -> None:
    """NULL rather than "": the partial index means absent values do not
    collide, and most volunteers supply nothing.
    """
    for name in ("Olivia", "Priya"):
        response = client.post(URL, data={"display_name": name}, content_type="application/json")
        assert response.status_code == 201


@pytest.mark.parametrize("field", ["email", "slack_id"])
def test_an_empty_identifier_is_stored_as_no_identifier(client: Client, field: str) -> None:
    """A form submits "" for a field nobody filled in, and "" is a value.

    The partial unique indexes cover every non-NULL value, so storing "" would
    make the *second* volunteer who skipped the field a constraint violation --
    a 500 naming an index, for the most ordinary submission there is.
    """
    for name in ("Olivia", "Priya"):
        response = client.post(URL, data={"display_name": name, field: ""}, content_type="application/json")
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
