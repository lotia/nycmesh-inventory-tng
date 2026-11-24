"""Quantities as pages show them and as forms send them back
(web/formatting.py)."""

from decimal import Decimal

import pytest

from inventory_tng.core.phrases import speaking, writing_numbers
from inventory_tng.web.formatting import amount, plain_amount

from helpers.locales import GERMAN


@pytest.mark.parametrize(
    ("stored", "read"),
    [
        ("1234.500", "1,234.5"),
        ("1000.000", "1,000"),
        ("3.000", "3"),
        ("0.001", "0.001"),
        ("-1234567.125", "-1,234,567.125"),
    ],
)
def test_an_amount_keeps_its_digits_grouped_as_the_locale_writes_them(
    stored: str, read: str
) -> None:
    """Every stored digit but trailing zeros, never rounded."""
    assert amount(Decimal(stored)) == read


def test_an_amount_takes_the_locales_decimal_sign() -> None:
    with speaking(GERMAN):
        assert amount(Decimal("1234.500")) == "1.234,5"


@pytest.mark.parametrize(
    ("stored", "read"),
    [("1500.000", "1500"), ("1.500", "1,5"), ("-1234567.125", "-1234567,125")],
)
def test_an_amount_on_a_comma_page_takes_its_comma_ungrouped(
    stored: str, read: str
) -> None:
    """In words as its boxes write it (Ali, 2026-11-23, inventory-tng-4mc3);
    a point page's as English writes it, and a printed label's always so."""
    with writing_numbers(","):
        assert amount(Decimal(stored)) == read
        assert plain_amount(Decimal("1500.5")) == "1500.5"
    with writing_numbers("."):
        assert amount(Decimal("1500.5")) == "1,500.5"


def test_an_amount_a_form_sends_back_is_plain_in_every_locale() -> None:
    with speaking(GERMAN):
        assert plain_amount(Decimal("1234.500")) == "1234.5"
    assert plain_amount(Decimal("1000.000")) == "1000"
    assert plain_amount(None) == amount(None) == ""
