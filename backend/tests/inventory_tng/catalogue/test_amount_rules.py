"""What an item's unit says of its amounts, here and on the scan page
(inventory-tng-xu6x)."""

import json
from decimal import Decimal
from typing import Any

import pytest

from devtools.write_amount_rules import TABLE
from inventory_tng.catalogue import amount_rules
from inventory_tng.catalogue.amount_rules import (
    COUNTED_WHOLE,
    LENGTHS,
    becomes_counted_whole,
    counted_whole,
    is_length,
    is_short_end,
    whole_enough,
)
from inventory_tng.models.catalogue import UNITS

VECTORS = TABLE.with_name("amount-rules-vectors.json")


def _vectors(group: str) -> list[dict[str, Any]]:
    if not VECTORS.exists():
        return []
    return json.loads(VECTORS.read_text(encoding="utf-8"))[group]


def test_every_unit_is_counted_whole_or_a_length() -> None:
    """A new unit is placed on purpose, never taken for a length."""
    assert sorted(COUNTED_WHOLE + LENGTHS) == sorted(UNITS)


@pytest.mark.parametrize(
    ("old", "new", "becomes"),
    [
        ("m", "each", True),
        ("ft", "each", True),
        ("each", "each", False),
        ("each", "m", False),
        ("m", "ft", False),
    ],
)
def test_a_switch_to_each_makes_an_item_counted_whole(
    old: str, new: str, becomes: bool
) -> None:
    assert becomes_counted_whole(old, new) is becomes


@pytest.mark.clone
def test_the_scan_pages_copy_is_current() -> None:
    committed = json.loads(TABLE.read_text(encoding="utf-8"))
    assert committed == amount_rules.table(), "Run `mise run regenerate-amount-rules`"


@pytest.mark.clone
@pytest.mark.parametrize("vector", _vectors("units"), ids=lambda v: v["unit"])
def test_the_server_and_the_scan_page_tell_units_alike(vector: dict[str, Any]) -> None:
    assert counted_whole(vector["unit"]) is vector["counted_whole"]
    assert is_length(vector["unit"]) is vector["length"]


@pytest.mark.clone
@pytest.mark.parametrize(
    "vector",
    _vectors("whole_enough"),
    ids=lambda v: f"{v['amount']} {v['unit']}",
)
def test_an_item_counted_each_moves_in_whole_units(vector: dict[str, Any]) -> None:
    assert whole_enough(vector["unit"], Decimal(vector["amount"])) is vector["whole"]


@pytest.mark.clone
@pytest.mark.parametrize(
    "vector",
    _vectors("short_ends"),
    ids=lambda v: f"{v['left']} below {v['short_below']}",
)
def test_the_server_and_the_scan_page_tell_short_ends_alike(
    vector: dict[str, Any],
) -> None:
    below = vector["short_below"]
    short_below = None if below is None else Decimal(below)
    assert is_short_end(Decimal(vector["left"]), short_below) is vector["short"]
