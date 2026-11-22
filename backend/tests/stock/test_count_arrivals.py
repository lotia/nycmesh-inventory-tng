"""What a count form's save does with the moves from areas gone from it
(stock/count_arrivals.py, inventory-tng-ygk5)."""

from decimal import Decimal

from inventory_tng.stock.count_arrivals import (
    Fix,
    arrived_at,
    fixed,
    fixed_lines,
    set_aside,
)
from inventory_tng.stock.ledger import LOOSE, Form
from inventory_tng.stock.moved_out import MovedOut

SEALED = Form(package_id=7)


def test_what_is_set_aside_is_by_place_item_and_form() -> None:
    moves = [
        MovedOut(1, 2, 5, Decimal(1)),
        MovedOut(3, 2, 5, Decimal(2)),
        MovedOut(1, 2, 5, Decimal(10), SEALED),
        MovedOut(1, None, 5, Decimal(4)),
    ]
    assert set_aside(moves) == {2: {(5, LOOSE): Decimal(3), (5, SEALED): Decimal(10)}}


def test_a_move_that_arrived_in_two_forms_is_fixed_in_each() -> None:
    """Each form's box against what arrived in that form; one as moved, or
    of no form that arrived, is left alone."""
    moves = [
        MovedOut(1, 2, 5, Decimal(1)),
        MovedOut(1, 2, 5, Decimal(10), SEALED),
        MovedOut(1, None, 5, Decimal(4)),
    ]
    typed = {
        (1, 5, LOOSE): Decimal(1),
        (1, 5, SEALED): Decimal(0),
        (1, 5, Form(package_id=8)): Decimal(3),
    }
    assert fixed(moves, {2: typed}) == [Fix(1, 2, 5, Decimal(-10), SEALED)]


def test_fixes_at_a_place_are_summed_by_item_and_form() -> None:
    """Less from one area, as much more from another: nothing to write."""
    fixes = [
        Fix(1, 2, 5, Decimal(-1), LOOSE),
        Fix(3, 2, 5, Decimal(1), LOOSE),
        Fix(1, 4, 5, Decimal(-1), LOOSE),
        Fix(3, 4, 5, Decimal(1), LOOSE),
        Fix(3, 4, 6, Decimal(2), LOOSE),
    ]
    assert fixed_lines(fixes) == {4: [(6, LOOSE, Decimal(2))]}


def test_what_arrived_is_what_was_moved_as_fixed() -> None:
    moves = [
        MovedOut(1, 2, 5, Decimal(1)),
        MovedOut(1, 2, 5, Decimal(10), SEALED),
        MovedOut(3, 2, 5, Decimal(2)),
        MovedOut(1, None, 5, Decimal(4)),
    ]
    fixes = [Fix(3, 2, 5, Decimal(-2), LOOSE)]
    assert arrived_at(set_aside(moves), fixes) == {(2, 5): Decimal(11)}
