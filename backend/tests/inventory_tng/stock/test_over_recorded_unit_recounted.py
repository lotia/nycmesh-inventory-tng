"""A line recorded with more than was moved, before its item's unit was
corrected by a guided recount (inventory-tng-qety), as a batch sent again
under its key after the recount says: the recount wrote all of it off,
in a unit wrong from the start, so no check is raised, which a
correction would write back."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.models.stock import Movement
from inventory_tng.stock import over_recorded

from helpers.over_recorded import checks
from helpers.stock import Place, received, recount


def _ten(session: Session, place: Place) -> None:
    """10 of the cable received, in its unit as it is."""
    cable = place.cable
    received(
        session, place, [{"item_id": cable.id, "quantity": "10", "unit": cable.unit}]
    )


def _lines(session: Session) -> list[int]:
    """The cable's movements, in order."""
    return list(session.scalars(select(Movement.id).order_by(Movement.id)))


def test_a_line_before_the_recount_raises_no_check(session: Session) -> None:
    place = Place(session)
    _ten(session, place)
    (before,) = _lines(session)
    recount(session, place.cable)
    with capture_logs() as logs:
        over_recorded.raise_checks(session, {before: Decimal(4)})
    assert checks(session) == []
    (said,) = [each for each in logs if each["event"].startswith("over recorded")]
    assert said["event"] == "over recorded not raised: recounted since"
    assert said["movement_ids"] == [before]


def test_a_line_after_the_recount_raises_its_check(session: Session) -> None:
    place = Place(session)
    _ten(session, place)
    recount(session, place.cable)
    _ten(session, place)
    after = _lines(session)[-1]
    over_recorded.raise_checks(session, {after: Decimal(4)})
    assert [(check.movement_id, check.quantity) for check in checks(session)] == [
        (after, Decimal(4))
    ]
