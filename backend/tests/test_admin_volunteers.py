"""Volunteers, holding stock and merging duplicates (admin/volunteers.py)."""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admin.volunteers import VOLUNTEERS
from inventory_tng.models import (
    Category,
    Item,
    LedgerTransaction,
    Location,
    Movement,
    Volunteer,
)

from helpers.rows import add, history, named, tag
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
            idempotency_key=uuid.uuid4(), kind="receipt", to_location_id=location.id
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
        ({"name": "ada"}, "already has that name or email"),
        ({"name": "Ben", "email": "ADA@example.org"}, "already has that name or email"),
        ({"name": "Ben", "email": "nope"}, "doesn't look like an email"),
    ):
        refused = admin_client.post(VOLUNTEERS, data=form)
        assert refused.status_code == 400
        assert problem in text(refused)


def test_renaming_renames_their_custody(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    path = f"{VOLUNTEERS}/{ada.id}"
    admin_client.post(f"{path}/custody")
    admin_client.post(path, data={"name": "Ada L", "email": ""})
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
    assert "still hold stock" in text(refused)
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
    assert "Stop them holding stock first." in text(refused)
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
    assert "merged into Ada" in text(admin_client.get(VOLUNTEERS))
    # The one others are merged into cannot be retired from under them.
    refused = admin_client.post(f"{VOLUNTEERS}/{ada.id}/retire")
    assert "Others are merged into them" in text(refused)
    # Bringing the duplicate back unmerges it.
    admin_client.post(f"{path}/restore")
    assert (dup.merged_into_id, dup.retired_at) == (None, None)


def test_merging_needs_a_current_other_volunteer(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    path = f"{VOLUNTEERS}/{ada.id}"
    for into in ("", str(ada.id), "999999"):
        refused = admin_client.post(f"{path}/merge", data={"into_id": into})
        assert "Pick who they duplicate." in text(refused)


def test_bringing_back_one_whose_name_is_taken_says_so(
    admin_client: TestClient, session: Session
) -> None:
    gone = add(session, Volunteer(name="Ada", retired_at=datetime.now(UTC)))
    add(session, Volunteer(name="ADA"))
    refused = admin_client.post(f"{VOLUNTEERS}/{gone.id}/restore")
    assert refused.status_code == 400
    assert "already has that name or email" in text(refused)
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
    assert "already has that name or email" in text(refused)
    assert custody_of(session, ada) is None


def test_saving_needs_a_free_name(admin_client: TestClient, session: Session) -> None:
    ada = add(session, Volunteer(name="Ada"))
    add(session, Volunteer(name="Ben"))
    path = f"{VOLUNTEERS}/{ada.id}"
    for name, problem in (("", "Give it a name"), ("ben", "already has that name")):
        refused = admin_client.post(path, data={"name": name})
        assert refused.status_code == 400
        assert problem in text(refused)
    assert ada.name == "Ada"


def test_ending_custody_they_do_not_have_is_not_found(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    assert admin_client.post(f"{VOLUNTEERS}/{ada.id}/custody/end").status_code == 404
    assert admin_client.post(f"{VOLUNTEERS}/0/custody/end").status_code == 404


def test_one_holding_stock_is_not_merged(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    dup = add(session, Volunteer(name="Ada L"))
    admin_client.post(f"{VOLUNTEERS}/{dup.id}/custody")
    refused = admin_client.post(
        f"{VOLUNTEERS}/{dup.id}/merge", data={"into_id": str(ada.id)}
    )
    assert "Stop them holding stock first." in text(refused)
    assert dup.merged_into_id is None
