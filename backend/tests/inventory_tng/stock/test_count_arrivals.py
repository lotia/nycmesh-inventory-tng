"""What a count form's save does with the moves from areas gone from it
(stock/count_arrivals.py, inventory-tng-ygk5)."""

from decimal import Decimal

from inventory_tng.stock.count_arrivals import (
    Fix,
    Held,
    Opening,
    arrived_at,
    as_held_at,
    fixed,
    fixed_lines,
    held_aside,
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
    assert fixed_lines(fixes, {}) == {4: [(6, LOOSE, Decimal(2))]}


def test_what_arrived_is_what_is_held_of_it_as_fixed() -> None:
    aside = {2: {(5, LOOSE): Decimal(3), (5, SEALED): Decimal(10)}}
    lines = {2: [(5, LOOSE, Decimal(-2))], 4: [(5, SEALED, Decimal(5))]}
    assert arrived_at(aside, lines) == {(2, 5): Decimal(11), (4, 5): Decimal(5)}


def test_what_arrived_is_set_aside_as_held_and_never_beyond() -> None:
    """Two spools set aside sealed where one is still sealed: the other
    from the newest opening of that package there, a package's worth, an
    open package cut to 4 or loose units; none from another package's.
    Loose beyond what the place holds loose is not set aside, nor an open
    package's arrival beyond what is left on it; what was opened loose
    comes after the loose that arrived (inventory-tng-nlog)."""
    first, newest, other = (Form(open_package_id=n) for n in (1, 2, 3))
    aside = {
        (5, SEALED): Decimal(20),
        (5, LOOSE): Decimal(3),
        (5, first): Decimal(8),
    }
    held = {
        (5, SEALED): Decimal(10),
        (5, LOOSE): Decimal(1),
        (5, first): Decimal(6),
        (5, newest): Decimal(4),
        (5, other): Decimal(50),
    }
    openings = [
        Opening(5, 8, other, Decimal(10)),
        Opening(5, 7, newest, Decimal(10)),
        Opening(5, 7, LOOSE, Decimal(10)),
    ]
    assert as_held_at(aside, held, openings, {}) == {
        (5, LOOSE): [Held(LOOSE, Decimal(3), Decimal(1))],
        (5, first): [Held(first, Decimal(8), Decimal(6))],
        (5, SEALED): [
            Held(SEALED, Decimal(10), Decimal(10)),
            Held(newest, Decimal(10), Decimal(4)),
        ],
    }
    assert as_held_at({(5, SEALED): Decimal(30)}, held, openings, {}) == {
        (5, SEALED): [
            Held(SEALED, Decimal(10), Decimal(10)),
            Held(newest, Decimal(10), Decimal(4)),
            Held(LOOSE, Decimal(10), Decimal(1)),
        ]
    }
    assert as_held_at({(5, SEALED): Decimal(40)}, held, openings, {})[(5, SEALED)][
        -1
    ] == Held(SEALED, Decimal(10), Decimal(0))


def test_what_arrived_in_an_open_package_since_loosened_is_set_aside_loose() -> None:
    """An open package moved whole to the place, and a spool opened there
    into another, each since made loose units as following was turned off
    (open_packages.close_unfollowed): set aside from those loose units,
    as much as each loosening made, never beyond what the place holds
    loose; one cut before it was loosened stands for all that arrived in
    it, as an open package cut does (Ali, 2026-11-23, inventory-tng-j89e)."""
    moved, opened = Form(open_package_id=1), Form(open_package_id=2)
    aside = {(5, moved): Decimal(250), (5, SEALED): Decimal(10)}
    held = {(5, LOOSE): Decimal(258)}
    openings = [Opening(5, 7, opened, Decimal(10))]
    loosened = {(5, 1): Decimal(250), (5, 2): Decimal(8)}
    assert as_held_at(aside, held, openings, loosened) == {
        (5, moved): [Held(LOOSE, Decimal(250), Decimal(250))],
        (5, SEALED): [Held(LOOSE, Decimal(10), Decimal(8))],
    }
    # Less loose there than was loosened: only what is there.
    fewer = as_held_at(aside, {(5, LOOSE): Decimal(100)}, openings, loosened)
    assert fewer[(5, moved)] == [Held(LOOSE, Decimal(250), Decimal(100))]


def test_a_fix_of_less_is_lost_from_the_forms_it_is_held_in() -> None:
    """Two spools arrived at place 2, one still sealed and one opened and
    cut to 4: a spool fixed less from each of two areas comes off the
    sealed one, then the open one, only what is left on it; a third
    spool, gone from there, writes nothing."""
    opened = Form(open_package_id=1)
    arrivals = {
        2: {
            (5, SEALED): [
                Held(SEALED, Decimal(10), Decimal(10)),
                Held(opened, Decimal(10), Decimal(4)),
                Held(SEALED, Decimal(10), Decimal(0)),
            ]
        }
    }
    one = [Fix(1, 2, 5, Decimal(-10), SEALED)]
    assert fixed_lines(one, arrivals) == {2: [(5, SEALED, Decimal(-10))]}
    two = [*one, Fix(3, 2, 5, Decimal(-20), SEALED)]
    assert fixed_lines(two, arrivals) == {
        2: [(5, SEALED, Decimal(-10)), (5, opened, Decimal(-4))]
    }
    assert held_aside(arrivals) == {
        2: {(5, SEALED): Decimal(10), (5, opened): Decimal(4)}
    }
