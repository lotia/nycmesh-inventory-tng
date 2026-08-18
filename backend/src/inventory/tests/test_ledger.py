"""Tests for the stock ledger.

The ledger is append-only and its invariants are database constraints, so
these tests go through the database rather than through model validation. See
docs/data-model.md and docs/decisions/0008-stock-ledger-transfer-graph.md.

For the ``# ty: ignore[unresolved-attribute]`` comment below, see
DEVELOPERS.md#typing.
"""

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection

from inventory.models import (
    Category,
    Item,
    Location,
    StockBalance,
    StockMovement,
    StockTransaction,
    Volunteer,
)

pytestmark = pytest.mark.django_db


def transaction_for(actor: Volunteer, kind: str = StockTransaction.Kind.CHECKOUT, **kwargs: object) -> StockTransaction:
    return StockTransaction.objects.create(actor=actor, kind=kind, **kwargs)


def balance(item: Item, location: Location) -> Decimal:
    row = StockBalance.objects.filter(item=item, location=location).first()
    return row.quantity if row else Decimal("0")


# --------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------


def test_quantity_must_be_positive(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    """Direction is which side the location sits on, never the sign."""
    with pytest.raises(IntegrityError):
        StockMovement.objects.create(
            transaction=transaction_for(volunteer),
            item=item,
            quantity=Decimal("-1"),
            from_location=warehouse,
        )


def test_a_movement_needs_at_least_one_side(volunteer: Volunteer, item: Item) -> None:
    with pytest.raises(IntegrityError):
        StockMovement.objects.create(transaction=transaction_for(volunteer), item=item, quantity=Decimal("1"))


def test_a_movement_cannot_start_and_end_in_the_same_place(
    volunteer: Volunteer, item: Item, warehouse: Location
) -> None:
    with pytest.raises(IntegrityError):
        StockMovement.objects.create(
            transaction=transaction_for(volunteer),
            item=item,
            quantity=Decimal("1"),
            from_location=warehouse,
            to_location=warehouse,
        )


def test_idempotency_key_is_unique_when_present(volunteer: Volunteer) -> None:
    """A phone retrying in a basement must not double-post a batch."""
    transaction_for(volunteer, idempotency_key="abc123")
    with pytest.raises(IntegrityError):
        transaction_for(volunteer, idempotency_key="abc123")


def test_transactions_without_an_idempotency_key_do_not_collide(volunteer: Volunteer) -> None:
    transaction_for(volunteer)
    transaction_for(volunteer)
    assert StockTransaction.objects.count() == 2


# --------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------


def test_a_movement_cannot_be_edited(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    movement = StockMovement.objects.create(
        transaction=transaction_for(volunteer),
        item=item,
        quantity=Decimal("3"),
        from_location=warehouse,
    )
    movement.quantity = Decimal("4")
    with pytest.raises(IntegrityError, match="append-only"):
        movement.save()


def test_a_movement_cannot_be_deleted(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    movement = StockMovement.objects.create(
        transaction=transaction_for(volunteer),
        item=item,
        quantity=Decimal("3"),
        from_location=warehouse,
    )
    with pytest.raises(IntegrityError, match="append-only"):
        movement.delete()


def test_a_transaction_cannot_be_edited(volunteer: Volunteer) -> None:
    entry = transaction_for(volunteer)
    entry.note = "second thoughts"
    with pytest.raises(IntegrityError, match="append-only"):
        entry.save()


@pytest.mark.parametrize("table", ["inventory_stockmovement", "inventory_stocktransaction"])
def test_the_ledger_cannot_be_truncated(table: str) -> None:
    """TRUNCATE does not fire row triggers, so it needs one of its own.

    ``manage.py flush`` truncates, and without this the append-only guarantee
    would have a hole big enough to empty the ledger through. The guard is a
    statement trigger, so it refuses the statement whether or not there are
    rows to lose -- which is why this needs no ledger data.
    """
    with pytest.raises(IntegrityError, match="append-only"), connection.cursor() as cursor:
        # CASCADE because that is what `manage.py flush` emits.
        cursor.execute(f"TRUNCATE {table} CASCADE")


def test_a_correction_is_a_new_movement(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    """18.7% of the old ledger was corrections. They are entries, not edits."""
    receipt = transaction_for(volunteer, kind=StockTransaction.Kind.RECEIPT)
    StockMovement.objects.create(transaction=receipt, item=item, quantity=Decimal("25"), to_location=warehouse)

    count = transaction_for(volunteer, kind=StockTransaction.Kind.COUNT, reason="cycle_count")
    StockMovement.objects.create(transaction=count, item=item, quantity=Decimal("3"), from_location=warehouse)

    assert balance(item, warehouse) == Decimal("22")
    assert StockMovement.objects.count() == 2


# --------------------------------------------------------------------------
# Balances
# --------------------------------------------------------------------------


def test_balance_is_zero_when_nothing_has_moved(item: Item, warehouse: Location) -> None:
    assert balance(item, warehouse) == Decimal("0")


def test_receipt_credits_the_destination(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    StockMovement.objects.create(
        transaction=transaction_for(volunteer, kind=StockTransaction.Kind.RECEIPT),
        item=item,
        quantity=Decimal("50"),
        to_location=warehouse,
    )
    assert balance(item, warehouse) == Decimal("50")


def test_custody_round_trip(volunteer: Volunteer, item: Item, warehouse: Location, custody: Location) -> None:
    """Check out, bring some back. The decision recorded in ADR 0008."""
    StockMovement.objects.create(
        transaction=transaction_for(volunteer, kind=StockTransaction.Kind.RECEIPT),
        item=item,
        quantity=Decimal("10"),
        to_location=warehouse,
    )
    StockMovement.objects.create(
        transaction=transaction_for(volunteer, kind=StockTransaction.Kind.CHECKOUT),
        item=item,
        quantity=Decimal("3"),
        from_location=warehouse,
        to_location=custody,
    )
    assert balance(item, warehouse) == Decimal("7")
    assert balance(item, custody) == Decimal("3")

    StockMovement.objects.create(
        transaction=transaction_for(volunteer, kind=StockTransaction.Kind.CHECKIN),
        item=item,
        quantity=Decimal("1"),
        from_location=custody,
        to_location=warehouse,
    )
    assert balance(item, warehouse) == Decimal("8")
    assert balance(item, custody) == Decimal("2")


def test_consumption_leaves_the_system_and_records_the_job(
    volunteer: Volunteer, item: Item, warehouse: Location, custody: Location
) -> None:
    """The question the old system could not answer: what went into NN217."""
    StockMovement.objects.create(
        transaction=transaction_for(volunteer, kind=StockTransaction.Kind.RECEIPT),
        item=item,
        quantity=Decimal("5"),
        to_location=custody,
    )
    install = transaction_for(volunteer, kind=StockTransaction.Kind.CONSUMPTION, job_reference="NN217")
    StockMovement.objects.create(transaction=install, item=item, quantity=Decimal("2"), from_location=custody)

    assert balance(item, custody) == Decimal("3")
    consumed = StockMovement.objects.filter(transaction__job_reference="NN217")
    assert consumed.get().quantity == Decimal("2")


def test_one_transaction_carries_many_movements(
    volunteer: Volunteer, warehouse: Location, custody: Location, category: Category
) -> None:
    """The batch feature. The old form allowed one item per submission."""
    items = [Item.objects.create(name=f"Item {n}", category=category) for n in range(24)]
    batch = transaction_for(volunteer, kind=StockTransaction.Kind.CHECKOUT)
    for item in items:
        StockMovement.objects.create(
            transaction=batch,
            item=item,
            quantity=Decimal("1"),
            from_location=warehouse,
            to_location=custody,
        )
    assert batch.movements.count() == 24  # ty: ignore[unresolved-attribute]
    assert all(balance(item, custody) == Decimal("1") for item in items)


def test_balances_are_measured_not_counted(volunteer: Volunteer, warehouse: Location, category: Category) -> None:
    """Cable is cut, so a balance has to hold a fraction."""
    cable = Item.objects.create(
        name="ToughCable",
        category=category,
        unit_of_measure=Item.UnitOfMeasure.METRE,
    )
    StockMovement.objects.create(
        transaction=transaction_for(volunteer, kind=StockTransaction.Kind.RECEIPT),
        item=cable,
        quantity=Decimal("305.000"),
        to_location=warehouse,
    )
    StockMovement.objects.create(
        transaction=transaction_for(volunteer, kind=StockTransaction.Kind.CHECKOUT),
        item=cable,
        quantity=Decimal("12.500"),
        from_location=warehouse,
    )
    assert balance(cable, warehouse) == Decimal("292.500")


def test_str_methods(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    entry = transaction_for(volunteer, kind=StockTransaction.Kind.CHECKOUT)
    assert "Check out" in str(entry)
    assert "Sean" in str(entry)
    movement = StockMovement.objects.create(
        transaction=entry, item=item, quantity=Decimal("2"), from_location=warehouse
    )
    assert "LiteBeam" in str(movement)
    assert "131 Broome" in str(movement)
    assert str(StockBalance.objects.get(item=item, location=warehouse)).startswith("-2")
