"""What the tests of lines recorded with more than was moved share
(tests/stock/test_over_recorded_*.py)."""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import (
    AdjustmentReason,
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.stock import ledger, over_recorded
from inventory_tng.stock.ledger import Form

from helpers.markup import Element, read
from helpers.rows import tag
from helpers.sign_in import new_admin
from helpers.stock import (
    URL,
    Place,
    Spools,
    cut,
    length,
    received,
    retire,
    sealed_line,
)

PAGE = "/admin/stock/recorded-more"


def bagged(bags: ItemPackage, n: int) -> dict[str, object]:
    return {
        "item_id": bags.item_id,
        "quantity": str(50 * n),
        "package_id": bags.id,
        "package_size": "50",
    }


def checks(session: Session) -> list[OverRecorded]:
    session.expire_all()
    return list(session.scalars(select(OverRecorded).order_by(OverRecorded.id)))


def recorded_then_changed(
    client: TestClient, first: dict[str, Any], **changed: object
) -> dict[str, Any]:
    """`first` recorded, then sent again changed under its key, as a page
    sends a cart put back with `first`'s lines (changed_from, unless
    `changed` says otherwise): the 409."""
    assert client.post(URL, json=first).status_code == 201
    response = client.post(URL, json=first | {"changed_from": first["lines"]} | changed)
    assert response.status_code == 409
    return response.json()


def new_admin_id(session: Session) -> int:
    """A new administrator's id; stock.ada_id is admin_client's."""
    admin, _ = new_admin(session, f"ada-{tag()}", totp=False)
    return admin.id


def one_bag_waits(client: TestClient, place: Place, bags: ItemPackage) -> None:
    """Two bags recorded, then one: one bag waits. Through the API, as a
    page sends it; stock.raise_check raises a check on a written transaction
    directly."""
    first = place.batch(lines=[bagged(bags, 2)])
    recorded_then_changed(client, first, lines=[bagged(bags, 1)])


def mark_checked(client: TestClient, session: Session, **form: str) -> Response:
    """The one waiting marked checked, as seen, with `form`'s reason."""
    (check,) = checks(session)
    data = {"seen": over_recorded.seen(check)} | form
    return client.post(f"{PAGE}/{check.id}/checked", data=data)


def checked_forms(page: str) -> list[Element]:
    """Each Mark checked form on `page`."""
    return [
        form
        for form in read(page).all("form")
        if "/checked" in form.attrs.get("action", "")
    ]


def moved_whole(
    client: TestClient, spools: Spools, reported: str | None = None
) -> OpenPackage:
    """A spool's open package moved whole from the store to the van and
    recorded, then sent again without it: it waits, whole. With
    `reported`, the batch that moved it said that length was left on it."""
    made = spools.opened()
    whole = cut(made, "305") | {"whole": True}
    loose = {"item_id": spools.place.item.id, "quantity": "1"}
    lengths = [length(made, reported)] if reported else None
    recorded_then_changed(
        client,
        spools.place.batch(lines=[whole, loose], lengths=lengths),
        lines=[loose],
        lengths=None,
    )
    return made


def counted(
    admin_client: TestClient, session: Session, count: str, **form: str
) -> tuple[OverRecorded, Any]:
    """The one waiting, and the answer to its count sent as `count`."""
    (check,) = checks(session)
    data = {"seen": over_recorded.seen(check), "count": count} | form
    return check, admin_client.post(f"{PAGE}/{check.id}/count", data=data)


def adjustments(session: Session) -> list[tuple[object, ...]]:
    return [
        tuple(each)
        for each in session.execute(
            select(
                LedgerTransaction.from_location_id,
                LedgerTransaction.to_location_id,
                Movement.quantity,
                Movement.from_open_package_id,
                Movement.to_open_package_id,
                AdjustmentReason.name,
            )
            .join(Movement, Movement.transaction_id == LedgerTransaction.id)
            .join(AdjustmentReason, AdjustmentReason.id == LedgerTransaction.reason_id)
            .where(LedgerTransaction.kind == "adjustment")
        )
    ]


def from_emptied_store(client: TestClient, session: Session) -> Place:
    """Three radios received into the store and moved to the van, then sent
    again as one: two wait, and the store holds nothing, retired."""
    place = Place(session)
    received(session, place)
    recorded_then_changed(
        client, place.batch(), lines=[{"item_id": place.item.id, "quantity": "1"}]
    )
    retire(session, place.store)
    return place


def correct(client: TestClient, session: Session) -> Response:
    (check,) = checks(session)
    return client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )


def count_ids(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(LedgerTransaction.id)
            .where(LedgerTransaction.kind == "count")
            .order_by(LedgerTransaction.id)
        )
    )


def opening_raised(client: TestClient, spools: Spools) -> OverRecorded:
    """Both sealed spools in the store recorded opened, then one: one
    waits, sealed where it left, open (tracked) or loose where it
    arrived."""
    line = sealed_line(spools, "305")
    # A page sends a line for each tracked one opened, under its own key;
    # one line of them all for loose units.
    lines = (
        [line | {"open_package_key": str(uuid.uuid4())} for _ in range(2)]
        if spools.cable.track_opened
        else [line | {"quantity": "610"}]
    )
    opening = spools.place.batch(
        kind="open",
        from_location_id=spools.store.id,
        to_location_id=spools.store.id,
        lines=lines,
    )
    recorded_then_changed(client, opening, lines=[lines[0] | {"quantity": "305"}])
    (check,) = checks(spools.session)
    return check


def opened_into(session: Session, check: OverRecorded) -> Form:
    """The form its movement arrived in: the open package, or loose."""
    movement = session.get_one(Movement, check.movement_id)
    return Form(movement.to_package_id, movement.to_open_package_id)


def arrived_field(spools: Spools, into: Form) -> str:
    """The count form's field of the form an opening arrived in."""
    if into.open_package_id is not None:
        return f"opened_{into.open_package_id}"
    return f"loose_{spools.cable.id}"


def sealed_field(spools: Spools) -> str:
    return f"sealed_{spools.cable.id}_{spools.spool.id}"


def reason_adjustments(session: Session, reason: str) -> list[Movement]:
    """The lines of adjustments with `reason`."""
    return list(
        session.scalars(
            select(Movement)
            .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
            .join(AdjustmentReason, AdjustmentReason.id == LedgerTransaction.reason_id)
            .where(AdjustmentReason.name == reason)
        )
    )


def correct_check(session: Session, check: OverRecorded, seen: str, admin: int) -> None:
    """Corrected as the page does: its places, then it."""
    ledger.lock_places(session, over_recorded.places_of(session, check.id))
    over_recorded.correct(session, check.id, seen, admin)
