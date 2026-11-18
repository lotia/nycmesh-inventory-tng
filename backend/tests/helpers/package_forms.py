"""What the tests of sealed and open packages share
(tests/test_package_forms_*.py)."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models import Item
from inventory_tng.stock.balance import by_form
from inventory_tng.stock.ledger import Form

CABLE, OTHER = 1, 2
SPOOL, BOX = 10, 11
OPEN, GONE, ELSEWHERE, FRESH = 20, 21, 22, 23
SEALED, SEALED_BOX = Form(package_id=SPOOL), Form(package_id=BOX)


def open_form(open_id: int) -> Form:
    return Form(open_package_id=open_id)


def quantities_by_form(
    session: Session, item: Item
) -> dict[tuple[int, int | None, int | None], Decimal]:
    rows = session.execute(
        select(
            by_form.c.location_id,
            by_form.c.package_id,
            by_form.c.open_package_id,
            by_form.c.quantity,
        ).where(by_form.c.item_id == item.id, by_form.c.quantity != 0)
    )
    return {(place, package, opened): amount for place, package, opened, amount in rows}
