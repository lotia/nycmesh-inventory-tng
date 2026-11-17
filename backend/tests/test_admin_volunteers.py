"""Volunteers, holding stock and merging duplicates (admin/volunteers.py)."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.accounts import NOT_AN_EMAIL, name_up_to
from inventory_tng.admin.common import NAME_MAX, RULES
from inventory_tng.admin.volunteers import PICK_INTO, STILL_HOLD, TAKEN, VOLUNTEERS
from inventory_tng.local_time import DEFAULT_ZONE
from inventory_tng.models import (
    Category,
    Item,
    LedgerTransaction,
    Location,
    Movement,
    Volunteer,
)

from helpers.markup import button, forms, hidden_fields, links, options, read, row_links
from helpers.rows import add, history, named, shown, tag
from helpers.sign_in import text


def custody_of(session: Session, volunteer: Volunteer) -> Location | None:
    return session.scalar(
        select(Location).where(
            Location.volunteer_id == volunteer.id, Location.retired_at.is_(None)
        )
    )


def give(session: Session, location: Location) -> None:
    """One of something, received straight into `location`."""
    category = add(session, Category(name=f"Things {tag()}"))
    item = add(session, Item(name=f"Thing {tag()}", category_id=category.id))
    batch = add(
        session,
        LedgerTransaction(
            time_zone=DEFAULT_ZONE,
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=location.id,
        ),
    )
    add(session, Movement(transaction_id=batch.id, item_id=item.id, quantity=1))


def test_adding_a_volunteer(admin_client: TestClient, session: Session) -> None:
    admin_client.post(VOLUNTEERS, data={"name": "Ada", "email": "ada@example.org"})
    ada = named(session, Volunteer, "Ada")
    assert ada.email == "ada@example.org"
    assert (history(session, "volunteer", ada.id)[0].actor or "").startswith("admin:")
    assert "Ada" in text(admin_client.get(VOLUNTEERS))


def test_names_and_emails_are_unique_and_checked(
    admin_client: TestClient, session: Session
) -> None:
    add(session, Volunteer(name="Ada", email="ada@example.org"))
    for form, problem in (
        ({"name": "ada"}, TAKEN),
        ({"name": "Ben", "email": "ADA@example.org"}, TAKEN),
        ({"name": "Ben", "email": "nope"}, NOT_AN_EMAIL),
    ):
        refused = admin_client.post(VOLUNTEERS, data=form)
        assert refused.status_code == 400
        assert str(problem) in text(refused)


def test_renaming_renames_their_custody(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    path = f"{VOLUNTEERS}/{ada.id}"
    admin_client.post(f"{path}/custody")
    admin_client.post(path, data={"name": "Ada L", "email": "", "version": shown(ada)})
    custody = custody_of(session, ada)
    assert custody is not None
    assert (ada.name, custody.name) == ("Ada L", "Ada L")


def test_holding_stock_and_stopping(admin_client: TestClient, session: Session) -> None:
    ada = add(session, Volunteer(name="Ada"))
    path = f"{VOLUNTEERS}/{ada.id}"
    assert admin_client.post(f"{path}/custody").status_code == 303
    custody = custody_of(session, ada)
    assert custody is not None
    assert custody.kind == "custody"
    assert "yes" in text(admin_client.get(VOLUNTEERS))
    # Not while they hold something.
    give(session, custody)
    refused = admin_client.post(f"{path}/custody/end")
    assert str(STILL_HOLD) in text(refused)
    assert custody.retired_at is None


def test_stopping_with_nothing_held(admin_client: TestClient, session: Session) -> None:
    ada = add(session, Volunteer(name="Ada"))
    path = f"{VOLUNTEERS}/{ada.id}"
    admin_client.post(f"{path}/custody")
    assert admin_client.post(f"{path}/custody/end").status_code == 303
    assert custody_of(session, ada) is None


def test_retiring_needs_custody_stopped_first(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    path = f"{VOLUNTEERS}/{ada.id}"
    admin_client.post(f"{path}/custody")
    refused = admin_client.post(f"{path}/retire")
    assert str(RULES["location_custody_volunteer"]) in text(refused)
    admin_client.post(f"{path}/custody/end")
    assert admin_client.post(f"{path}/retire").status_code == 303
    assert ada.retired_at is not None
    assert admin_client.post(f"{path}/restore").status_code == 303
    assert ada.retired_at is None


def test_merging_a_duplicate(admin_client: TestClient, session: Session) -> None:
    ada = add(session, Volunteer(name="Ada"))
    dup = add(session, Volunteer(name="Ada Lovelace"))
    path = f"{VOLUNTEERS}/{dup.id}"
    assert (
        admin_client.post(f"{path}/merge", data={"into_id": str(ada.id)}).status_code
        == 303
    )
    assert (dup.merged_into_id, dup.retired_at is not None) == (ada.id, True)
    assert "merged into Ada" in text(
        admin_client.get(VOLUNTEERS, params={"show": "retired"})
    )
    # The one others are merged into cannot be retired from under them.
    refused = admin_client.post(f"{VOLUNTEERS}/{ada.id}/retire")
    assert "Others are merged into them" in text(refused)
    # Bringing the duplicate back unmerges it.
    admin_client.post(f"{path}/restore")
    assert (dup.merged_into_id, dup.retired_at) == (None, None)


def test_merging_asks_to_confirm_first(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    dup = add(session, Volunteer(name="Ada Lovelace"))
    path = f"{VOLUNTEERS}/{dup.id}"
    assert ("get", f"{path}/merge") in forms(admin_client.get(path).text)
    asked = admin_client.get(f"{path}/merge", params={"into_id": str(ada.id)})
    assert asked.status_code == 200
    words = " ".join(text(asked).split())
    assert "Ada Lovelace is retired as a duplicate of Ada" in words
    shown = read(asked.text)
    assert ("into_id", str(ada.id)) in hidden_fields(shown)
    assert "danger" in button(shown, "Merge").classes
    assert ("Keep them apart", path) in links(shown)
    assert dup.retired_at is None  # Only its button merges.
    for into in ("", str(dup.id), "999999"):
        refused = admin_client.get(f"{path}/merge", params={"into_id": into})
        assert str(PICK_INTO) in text(refused)
    admin_client.post(f"{path}/merge", data={"into_id": str(ada.id)})
    assert (
        admin_client.get(f"{path}/merge", params={"into_id": str(ada.id)}).status_code
        == 404
    )
    assert admin_client.get(f"{VOLUNTEERS}/999999/merge").status_code == 404


def test_merging_needs_a_current_other_volunteer(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    path = f"{VOLUNTEERS}/{ada.id}"
    for into in ("", str(ada.id), "999999"):
        refused = admin_client.post(f"{path}/merge", data={"into_id": into})
        assert str(PICK_INTO) in text(refused)


def test_merging_into_one_retired_since_the_page_asked_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """The page asks while Riley is current; a second tab retires Riley
    before Merge is pressed. Both at the same moment is
    test_requests_at_once.py's."""
    sam = add(session, Volunteer(name="Sam"))
    riley = add(session, Volunteer(name="Riley"))
    path = f"{VOLUNTEERS}/{sam.id}"
    into = {"into_id": str(riley.id)}
    offered = (str(riley.id), "Riley", False)
    assert offered in options(admin_client.get(path).text)
    assert admin_client.get(f"{path}/merge", params=into).status_code == 200
    assert admin_client.post(f"{VOLUNTEERS}/{riley.id}/retire").status_code == 303
    assert str(PICK_INTO) in text(admin_client.get(f"{path}/merge", params=into))
    before = history(session, "volunteer")
    refused = admin_client.post(f"{path}/merge", data=into)
    assert refused.status_code == 400
    assert str(PICK_INTO) in text(refused)
    assert str(riley.id) not in [value for value, _, _ in options(refused.text)]
    assert (sam.merged_into_id, sam.retired_at) == (None, None)
    assert history(session, "volunteer") == before


def test_bringing_back_one_whose_name_is_taken_says_so(
    admin_client: TestClient, session: Session
) -> None:
    gone = add(session, Volunteer(name="Ada", retired_at=datetime.now(UTC)))
    add(session, Volunteer(name="ADA"))
    refused = admin_client.post(f"{VOLUNTEERS}/{gone.id}/restore")
    assert refused.status_code == 400
    assert str(TAKEN) in text(refused)
    assert gone.retired_at is not None


def test_holding_stock_where_the_name_is_taken_says_so(
    admin_client: TestClient, session: Session
) -> None:
    """Their custody is named for them, so another current custody of that
    name, as only a database changed by hand could hold, refuses it."""
    ada = add(session, Volunteer(name="Ada"))
    ben = add(session, Volunteer(name="Ben"))
    add(session, Location(name="ada", kind="custody", volunteer_id=ben.id))
    refused = admin_client.post(f"{VOLUNTEERS}/{ada.id}/custody")
    assert refused.status_code == 400
    assert str(TAKEN) in text(refused)
    assert custody_of(session, ada) is None


def test_saving_needs_a_free_name(admin_client: TestClient, session: Session) -> None:
    ada = add(session, Volunteer(name="Ada"))
    add(session, Volunteer(name="Ben"))
    path = f"{VOLUNTEERS}/{ada.id}"
    for name, problem in (("", name_up_to(NAME_MAX)), ("ben", TAKEN)):
        refused = admin_client.post(path, data={"name": name, "version": shown(ada)})
        assert refused.status_code == 400
        assert str(problem) in text(refused)
    assert ada.name == "Ada"


def test_ending_custody_they_do_not_have_is_not_found(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    assert admin_client.post(f"{VOLUNTEERS}/{ada.id}/custody/end").status_code == 404
    assert admin_client.post(f"{VOLUNTEERS}/0/custody/end").status_code == 404


@pytest.mark.parametrize("action", ["custody", "retire", "merge", "restore"])
def test_an_unknown_volunteer_is_not_found(
    admin_client: TestClient, action: str
) -> None:
    assert admin_client.post(f"{VOLUNTEERS}/0/{action}").status_code == 404


@pytest.mark.parametrize("action", ["custody", "merge"])
def test_a_retired_volunteer_is_not_found(
    admin_client: TestClient, session: Session, action: str
) -> None:
    """Neither given custody nor merged, whoever the merge names."""
    ada = add(session, Volunteer(name="Ada", retired_at=datetime.now(UTC)))
    path = f"{VOLUNTEERS}/{ada.id}/{action}"
    assert admin_client.post(path, data={"into_id": "0"}).status_code == 404


def test_one_holding_stock_is_not_merged(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    dup = add(session, Volunteer(name="Ada L"))
    admin_client.post(f"{VOLUNTEERS}/{dup.id}/custody")
    refused = admin_client.post(
        f"{VOLUNTEERS}/{dup.id}/merge", data={"into_id": str(ada.id)}
    )
    assert str(RULES["location_custody_volunteer"]) in text(refused)
    assert dup.merged_into_id is None


def test_volunteers_are_found_by_a_name_typed_roughly(
    admin_client: TestClient, session: Session
) -> None:
    sam = add(session, Volunteer(name="Samantha Okonkwo"))
    add(session, Volunteer(name="Bo"))
    offered = admin_client.get(f"{VOLUNTEERS}/suggest", params={"q": "okonkow"})
    assert offered.json()[0] == {
        "name": sam.name,
        "hint": "",
        "href": f"{VOLUNTEERS}?pick={sam.id}",
    }
    found = links(admin_client.get(VOLUNTEERS, params={"q": "okonkow"}).text)
    assert (sam.name, f"{VOLUNTEERS}/{sam.id}") in found
    assert "Bo" not in [words for words, _ in found]


def volunteers_named(page: str) -> list[str]:
    """The volunteers the list shows, in its order."""
    return row_links(page, VOLUNTEERS)


def test_volunteers_sort_filter_and_show_25_50_100_or_all(
    admin_client: TestClient, session: Session
) -> None:
    """By name unless a heading sorts them; by holding stock or not; in
    pages of 25, 50, 100 or all (Ali, 2026-11-15, inventory-tng-qwb)."""
    mark = tag()
    for name in ("Bravo", "Alpha", "Charlie"):
        add(session, Volunteer(name=f"Sorted {mark} {name}"))
    bravo = named(session, Volunteer, f"Sorted {mark} Bravo")
    assert admin_client.post(f"{VOLUNTEERS}/{bravo.id}/custody").status_code == 303

    def listed(**query: str) -> list[str]:
        params = {"q": f"Sorted {mark}", "per": "all"} | query
        page = admin_client.get(VOLUNTEERS, params=params).text
        return [n.rsplit(" ", 1)[1] for n in volunteers_named(page)]

    assert listed(sort="name") == ["Alpha", "Bravo", "Charlie"]
    assert listed(sort="name", order="desc") == ["Charlie", "Bravo", "Alpha"]
    assert listed(sort="holds") == ["Bravo", "Alpha", "Charlie"]
    assert listed(sort="holds", order="asc") == ["Alpha", "Charlie", "Bravo"]
    assert listed(holds="yes") == ["Bravo"]
    assert listed(holds="no", sort="name") == ["Alpha", "Charlie"]
    assert listed(holds="maybe", sort="name") == ["Alpha", "Bravo", "Charlie"]
    page = read(admin_client.get(VOLUNTEERS, params={"holds": "yes"}).text)
    assert ("yes", "Holds stock", True) in options(page)
    assert ("Clear", VOLUNTEERS) in links(page)
    assert ("Holds stock", f"{VOLUNTEERS}?holds=yes&sort=holds") in links(page)
    other = tag()
    for number in range(27):
        add(session, Volunteer(name=f"Sized {other} {number:03}"))
    sized = {"q": f"Sized {other}"}
    assert len(volunteers_named(admin_client.get(VOLUNTEERS, params=sized).text)) == 27
    small = admin_client.get(VOLUNTEERS, params=sized | {"per": "25"})
    assert len(volunteers_named(small.text)) == 25
    assert "Page 1 of 2" in text(small)
    # Suggestions come from the filter.
    offered = admin_client.get(
        f"{VOLUNTEERS}/suggest", params={"q": f"Sorted {mark}", "holds": "yes"}
    ).json()
    assert [each["name"] for each in offered] == [bravo.name]
