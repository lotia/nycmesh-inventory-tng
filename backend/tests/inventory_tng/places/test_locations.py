"""A location's rules of more than one condition (places/locations.py), as
functions of plain values: every way each can be broken, with no database.
That each domain function calls its rules, under the catalogue lock, is
models/test_models.py's and across/test_requests_at_once.py's to show, on
PostgreSQL."""

import pytest

from inventory_tng.places import locations
from inventory_tng.places.locations import Parent

STORE, AREA, VAN = Parent("store", True), Parent("area", True), Parent("vehicle", True)


@pytest.mark.parametrize(
    ("parent", "current", "broken"),
    [
        (None, True, None),
        (STORE, True, None),
        (AREA, True, None),
        (VAN, True, "location_area_parent"),
        (Parent("custody", True), True, "location_area_parent"),
        # The kind is checked first, as it would be wrong even if current.
        (Parent("vehicle", False), True, "location_area_parent"),
        (Parent("store", False), True, "location_parent_current"),
        (Parent("area", False), True, "location_parent_current"),
        # A retired area may sit in a retired place.
        (Parent("store", False), False, None),
    ],
)
def test_an_area_goes_in_a_current_store_or_area(
    parent: Parent | None, current: bool, broken: str | None
) -> None:
    assert locations.placing(parent, current=current) == broken


# A store (1) with a shelf (2) holding a bin (3), and another store (4).
TREE = {1: None, 2: 1, 3: 2, 4: None}


@pytest.mark.parametrize(
    ("moving", "under", "broken"),
    [
        (2, 2, "location_no_cycle"),  # Into itself.
        (2, 3, "location_no_cycle"),  # Into its own area.
        (1, 3, "location_no_cycle"),  # Further down.
        (3, 1, None),  # Up the tree.
        (2, 4, None),  # Into another store.
        (3, 4, None),
    ],
)
def test_the_tree_has_no_cycles(moving: int, under: int, broken: str | None) -> None:
    assert locations.no_cycle(TREE, moving, under) == broken


def test_a_cycle_already_there_ends_the_walk() -> None:
    """Never made, but the walk must not go round it for ever."""
    assert locations.no_cycle({1: 2, 2: 1, 3: None}, 3, 1) is None
