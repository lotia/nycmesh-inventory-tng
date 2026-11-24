"""How forms read a number typed in a box, in the box's decimal separator
(web/forms.py; inventory-tng-qnwz)."""

import json
from decimal import Decimal
from typing import Any

import pytest

from inventory_tng.core.local_env import CLONE
from inventory_tng.web.formatting import box_amount, plain_amount
from inventory_tng.web.forms import (
    ABOVE_ZERO,
    TOO_BIG,
    TYPE_A_NUMBER,
    ZERO_OR_MORE,
    Unreadable,
    at_most_places,
    quantity,
    read_amount,
    read_decimal,
)

CASES = CLONE / "frontend" / "src" / "shared" / "quantity-cases.json"
# The locales the shared cases are written for, by their decimal separator.
SEPARATORS = {"en": ".", "de": ","}


def _cases() -> list[tuple[str, str, dict[str, str]]]:
    """Each shared case, once for each locale; none outside a clone."""
    if not CASES.exists():
        return []
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    return [
        (case["typed"], locale, case[locale]) for case in cases for locale in SEPARATORS
    ]


@pytest.mark.clone
@pytest.mark.parametrize(
    ("typed", "locale", "expected"),
    _cases(),
)
def test_the_server_and_the_scan_page_read_a_box_alike(
    typed: str, locale: str, expected: dict[str, Any]
) -> None:
    """quantity-cases.json, which the scan page's tests read too."""
    read = read_decimal(typed, SEPARATORS[locale])
    if "refused" in expected:
        assert read == Unreadable(expected["refused"])
    else:
        assert isinstance(read, Decimal)
        assert read == Decimal(expected["amount"])
        # -0 reads as 0, so it is never shown as -0.
        assert plain_amount(read) == expected["amount"]


@pytest.mark.parametrize(
    ("typed", "positive", "parsed"),
    [
        ("", True, None),
        (" 2.5 ", True, Decimal("2.5")),
        ("0", False, Decimal(0)),
        ("0", True, ABOVE_ZERO),
        ("-0", True, ABOVE_ZERO),
        ("-1", False, ZERO_OR_MORE),
        ("1.2345", False, at_most_places(3)),
        ("1000000000", False, TOO_BIG),
        ("lots", False, TYPE_A_NUMBER),
        # NaN said "That number is too big." (inventory-tng-qnwz).
        ("NaN", False, TYPE_A_NUMBER),
        ("sNaN", False, TYPE_A_NUMBER),
        ("-NaN", False, TYPE_A_NUMBER),
        ("Infinity", False, TYPE_A_NUMBER),
        ("1e12", False, TYPE_A_NUMBER),
    ],
)
def test_quantities_typed(typed: str, positive: bool, parsed: object) -> None:
    assert quantity(typed, positive=positive) == parsed


def test_a_quantity_reads_the_boxs_separator() -> None:
    assert quantity("2,5", positive=True, separator=",") == Decimal("2.5")
    assert quantity("2.5", positive=True, separator=",") == str(TYPE_A_NUMBER)
    assert quantity("0,0001", positive=True, separator=",") == at_most_places(3)


def test_a_box_written_reads_back_as_it_was() -> None:
    """An unchanged box posts back valid, in either separator."""
    for separator in SEPARATORS.values():
        for stored in ("1234.500", "0.5", "3.000"):
            written = box_amount(Decimal(stored), separator)
            assert read_amount(written, separator) == Decimal(stored)
    assert box_amount(Decimal("1234.500"), ",") == "1234,5"
    assert box_amount(None, ",") == ""
