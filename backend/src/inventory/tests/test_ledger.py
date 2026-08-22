"""Tests for the stock ledger.

The ledger is append-only and its invariants are check constraints and
triggers, so these tests go through the ORM rather than through a serializer:
what they are asserting is that a writer who never passes the API -- the
admin, a fixture, the planned sheet importer -- is held to them too. See
docs/data-model.md, docs/decisions/0008-stock-ledger-transfer-graph.md and
docs/decisions/0016-invariants-for-every-writer.md.

For the ``# ty: ignore[unresolved-attribute]`` comment below, see
DEVELOPERS.md#typing.
"""

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from inventory.models import (
    Category,
    Item,
    Location,
    StockBalance,
    StockMovement,
    StockTransaction,
    Volunteer,
)
from inventory.serializers import CLOCK_SKEW
from inventory.views import KIND_SIDES

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
    # An adjustment, which is one of the two kinds that ask nothing of the two
    # sides (see stock_movement_matches_kind): under any other kind this row
    # would be refused for disagreeing with its kind, and would prove nothing
    # about stock_movement_has_a_side.
    with pytest.raises(IntegrityError):
        StockMovement.objects.create(
            transaction=transaction_for(volunteer, kind=StockTransaction.Kind.ADJUSTMENT),
            item=item,
            quantity=Decimal("1"),
        )


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
# A movement has the shape its kind says it has
#
# Recorded here rather than only through the batch endpoint because that is
# the whole point of migration 0008: the rule used to live in views.py alone,
# so the admin, a fixture and the planned sheet importer could write a receipt
# that drained a warehouse. These go through the ORM, past every serializer.
# --------------------------------------------------------------------------


def test_a_receipt_cannot_drain_a_warehouse(
    volunteer: Volunteer, item: Item, warehouse: Location, custody: Location
) -> None:
    """Stock arriving from outside leaves nowhere. Anything else is a transfer
    misfiled as a delivery, and the ledger cannot be edited to say so later.
    """
    with pytest.raises(IntegrityError, match="receipt"):
        StockMovement.objects.create(
            transaction=transaction_for(volunteer, kind=StockTransaction.Kind.RECEIPT),
            item=item,
            quantity=Decimal("25"),
            from_location=warehouse,
            to_location=custody,
        )


def test_stock_used_at_a_job_arrives_nowhere(
    volunteer: Volunteer, item: Item, warehouse: Location, custody: Location
) -> None:
    with pytest.raises(IntegrityError, match="does not arrive anywhere"):
        StockMovement.objects.create(
            transaction=transaction_for(volunteer, kind=StockTransaction.Kind.CONSUMPTION),
            item=item,
            quantity=Decimal("2"),
            from_location=custody,
            to_location=warehouse,
        )


def test_a_check_out_comes_out_of_somewhere(volunteer: Volunteer, item: Item, custody: Location) -> None:
    with pytest.raises(IntegrityError, match="from_location"):
        StockMovement.objects.create(
            transaction=transaction_for(volunteer, kind=StockTransaction.Kind.CHECKOUT),
            item=item,
            quantity=Decimal("1"),
            to_location=custody,
        )


def test_a_transfer_names_both_ends(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    with pytest.raises(IntegrityError, match="to_location"):
        StockMovement.objects.create(
            transaction=transaction_for(volunteer, kind=StockTransaction.Kind.TRANSFER),
            item=item,
            quantity=Decimal("1"),
            from_location=warehouse,
        )


@pytest.mark.parametrize("kind", [StockTransaction.Kind.ADJUSTMENT, StockTransaction.Kind.COUNT])
def test_an_adjustment_and_a_count_may_go_either_way(
    volunteer: Volunteer, item: Item, warehouse: Location, kind: str
) -> None:
    """Reconciling the shelf against the system pushes stock in whichever
    direction the shelf says, so neither side is required and neither is
    forbidden. Decision 0011 section 6.
    """
    entry = transaction_for(volunteer, kind=kind)
    StockMovement.objects.create(transaction=entry, item=item, quantity=Decimal("3"), to_location=warehouse)
    StockMovement.objects.create(transaction=entry, item=item, quantity=Decimal("1"), from_location=warehouse)
    assert balance(item, warehouse) == Decimal("2")


@pytest.mark.parametrize("kind", [kind for kind, _ in StockTransaction.Kind.choices])
@pytest.mark.parametrize(
    "sides",
    [("from_location",), ("to_location",), ("from_location", "to_location")],
)
def test_the_database_permits_exactly_the_shapes_the_api_does(
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    custody: Location,
    kind: str,
    sides: tuple[str, ...],
) -> None:
    """The trigger and ``KIND_SIDES`` are the same table written twice.

    They have to be: the endpoint reports a bad line by index before anything
    is written, and the database refuses the write whoever attempted it. This
    walks every kind against every shape and asserts the two agree, so a rule
    changed in one place fails here rather than in a ledger nobody can edit.

    Every shape but one: a movement with neither side is refused for every
    kind by ``stock_movement_has_a_side``, which is a check constraint and a
    different rule, so KIND_SIDES has nothing to say about it.
    """
    places = {"from_location": warehouse, "to_location": custody}
    rule = KIND_SIDES.get(kind)
    api_accepts = rule is None or (
        all(side in sides for side in rule.required) and not any(side in sides for side in rule.forbidden)
    )

    try:
        with transaction.atomic():
            StockMovement.objects.create(
                transaction=transaction_for(volunteer, kind=kind),
                item=item,
                quantity=Decimal("1"),
                **{side: places[side] for side in sides},
            )
    except IntegrityError:
        database_accepts = False
    else:
        database_accepts = True

    assert database_accepts is api_accepts


def test_a_movement_whose_transaction_is_not_there_yet_is_refused(item: Item, warehouse: Location) -> None:
    """The kind decides the shape, so no kind cannot mean no rule.

    The foreign key is deferrable and ``loaddata`` defers constraint checks for
    a whole load, so a writer that never passes the API can offer a movement
    before its transaction exists. Reading no kind and accepting the row would
    let exactly the writers migration 0008 exists for past the one rule that
    cannot be re-checked afterwards, the ledger being append-only.
    """
    with (
        pytest.raises(IntegrityError, match="must exist before it does"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        # Deferring the foreign key is what a fixture load does for a whole
        # file, and is the only way to offer the row at all.
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
        cursor.execute(
            """
            INSERT INTO inventory_stockmovement
                (transaction_id, item_id, quantity, from_location_id, to_location_id)
            VALUES (%s, %s, %s, NULL, %s)
            """,
            [2147483647, item.pk, Decimal("1"), warehouse.pk],
        )


# --------------------------------------------------------------------------
# Who and when
# --------------------------------------------------------------------------


def test_a_batch_cannot_be_dated_in_the_future(volunteer: Volunteer) -> None:
    """The comment on ``StockTransaction.occurred_at`` says why this is worth
    a trigger.
    """
    with pytest.raises(IntegrityError, match="in the future"):
        transaction_for(volunteer, occurred_at=timezone.now() + datetime.timedelta(days=1))


def test_a_batch_written_up_afterwards_is_ordinary(volunteer: Volunteer) -> None:
    """Coming back from an install and recording yesterday is the normal case."""
    entry = transaction_for(volunteer, occurred_at=timezone.now() - datetime.timedelta(days=1))
    assert entry.pk is not None


def test_a_batch_dated_a_moment_ahead_is_believed(volunteer: Volunteer) -> None:
    """A phone's clock is its own. The API allows it a few minutes of drift
    (CLOCK_SKEW), so the database has to allow at least as much or it would
    refuse a batch the API had just accepted.
    """
    entry = transaction_for(volunteer, occurred_at=timezone.now() + datetime.timedelta(minutes=1))
    assert entry.pk is not None


def test_the_database_allows_every_moment_the_api_does(volunteer: Volunteer) -> None:
    """The allowance is written twice: CLOCK_SKEW here, an interval in the
    trigger.

    Decision 0016's consequences say which way round the pair works and what
    the other way costs. Widening CLOCK_SKEW alone fails here instead.
    """
    entry = transaction_for(volunteer, occurred_at=timezone.now() + CLOCK_SKEW)
    assert entry.pk is not None


@pytest.mark.parametrize("withdrawn", ["merged", "retired"])
def test_a_volunteer_the_list_no_longer_offers_cannot_be_the_actor(volunteer: Volunteer, withdrawn: str) -> None:
    """Recording new work against a duplicate starts the next generation of it."""
    actor = Volunteer.objects.create(display_name="Sean B")
    if withdrawn == "merged":
        actor.merged_into = volunteer
    else:
        actor.active = False
    actor.save()

    with pytest.raises(IntegrityError, match="merged or retired"):
        transaction_for(actor)


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


def test_stock_cannot_arrive_at_a_retired_location(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    """Decision 0019: a retired location stops being offered, not stops existing.

    Migration 0010 says why arrival is the direction refused. Enforced below
    the API so the admin and the sheet importer meet it too, which is decision
    0016's test.
    """
    retired = Location.objects.create(name="Decommissioned room", kind=Location.Kind.ROOM)
    Location.objects.filter(pk=retired.pk).update(active=False)
    batch = transaction_for(volunteer, StockTransaction.Kind.TRANSFER)

    # Matched on the message, like every other trigger case in this file. The
    # insert passes stock_movement_matches_kind too, and PostgreSQL fires
    # BEFORE ROW triggers in name order, so that one runs first -- a bare
    # IntegrityError here would stay green if this trigger were dropped.
    with pytest.raises(IntegrityError, match="retired, so stock cannot arrive"):
        StockMovement.objects.create(
            transaction=batch, item=item, quantity=Decimal("1"), from_location=warehouse, to_location=retired
        )


def test_stock_may_still_leave_a_retired_location(volunteer: Volunteer, item: Item, warehouse: Location) -> None:
    """The other half, and the one that makes decommissioning possible at all."""
    retired = Location.objects.create(name="Emptying room", kind=Location.Kind.ROOM)
    stocked = transaction_for(volunteer, StockTransaction.Kind.RECEIPT)
    StockMovement.objects.create(transaction=stocked, item=item, quantity=Decimal("2"), to_location=retired)
    Location.objects.filter(pk=retired.pk).update(active=False)
    batch = transaction_for(volunteer, StockTransaction.Kind.TRANSFER)

    StockMovement.objects.create(
        transaction=batch, item=item, quantity=Decimal("2"), from_location=retired, to_location=warehouse
    )

    # The balance, not the attribute create() just set in memory: that would
    # hold whether or not the row reached the database.
    assert balance(item, retired) == Decimal("0")
    assert balance(item, warehouse) == Decimal("2")
