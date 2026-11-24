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
    THOUSANDS,
    TOO_BIG,
    ZERO_OR_MORE,
    Unreadable,
    at_most_places,
    quantity,
    read_amount,
    read_decimal,
    type_a_number,
    use_separator,
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


@pytest.mark.clone
@pytest.mark.parametrize(
    ("typed", "locale", "expected"),
    _cases(),
)
def test_a_box_says_why_it_refused_a_shared_case(
    typed: str, locale: str, expected: dict[str, Any]
) -> None:
    """What the server says of each refusal: the separator to use, named,
    or thousands without one (Ali, 2026-11-23). A blank box is no refusal
    of quantity's."""
    if "refused" not in expected or not typed.strip():
        return
    separator = SEPARATORS[locale]
    words = {
        "number": type_a_number(separator),
        "separator": use_separator(separator),
        "thousands": str(THOUSANDS),
        "places": at_most_places(3),
    }
    assert (
        quantity(typed, positive=False, separator=separator)
        == (words[expected["refused"]])
    )


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
        ("lots", False, type_a_number()),
        # NaN said "That number is too big." (inventory-tng-qnwz).
        ("NaN", False, type_a_number()),
        ("sNaN", False, type_a_number()),
        ("-NaN", False, type_a_number()),
        ("Infinity", False, type_a_number()),
        ("1e12", False, type_a_number()),
    ],
)
def test_quantities_typed(typed: str, positive: bool, parsed: object) -> None:
    assert quantity(typed, positive=positive) == parsed


def test_a_quantity_reads_the_boxs_separator() -> None:
    assert quantity("2,5", positive=True, separator=",") == Decimal("2.5")
    assert quantity("2.5", positive=True, separator=",") == use_separator(",")
    assert quantity("0,0001", positive=True, separator=",") == at_most_places(3)


@pytest.mark.parametrize(
    ("separator", "typed", "said"),
    [
        (",", "1.5", "Use “,” for decimals, such as 0,5."),
        (".", "1,5", "Use “.” for decimals, such as 0.5."),
        (",", "1.000", "Type thousands without a separator: 1000."),
        (".", "1,000", "Type thousands without a separator: 1000."),
        (",", "lots", "Type a number, such as 12 or 0,5."),
        (".", "lots", "Type a number, such as 12 or 0.5."),
    ],
)
def test_a_refusal_names_the_separator_to_use(
    separator: str, typed: str, said: str
) -> None:
    assert quantity(typed, positive=True, separator=separator) == said


def test_a_box_written_reads_back_as_it_was() -> None:
    """An unchanged box posts back valid, in either separator."""
    for separator in SEPARATORS.values():
        for stored in ("1234.500", "0.5", "3.000"):
            written = box_amount(Decimal(stored), separator)
            assert read_amount(written, separator) == Decimal(stored)
    assert box_amount(Decimal("1234.500"), ",") == "1234,5"
    assert box_amount(None, ",") == ""
