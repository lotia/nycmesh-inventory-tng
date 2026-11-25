"""Quantities as pages show them and as forms send them back
(web/formatting.py)."""

from decimal import Decimal

import pytest

from inventory_tng.core.phrases import speaking, writing_numbers
from inventory_tng.web.formatting import amount, plain_amount, unit_after

from helpers.locales import GERMAN


@pytest.mark.parametrize(
    ("stored", "read"),
    [
        ("1234.500", "1234.5"),
        ("1000.000", "1000"),
        ("3.000", "3"),
        ("0.001", "0.001"),
        ("-1234567.125", "-1234567.125"),
    ],
)
def test_an_amount_keeps_its_digits_ungrouped(stored: str, read: str) -> None:
    """Every stored digit but trailing zeros, never rounded, and never
    grouped, as a box takes it (Ali, 2026-11-24, inventory-tng-ua04)."""
    assert amount(Decimal(stored)) == read


def test_an_amount_takes_the_pages_separator_whatever_the_locale() -> None:
    with speaking(GERMAN):
        assert amount(Decimal("1234.500")) == "1234.5"
        with writing_numbers(","):
            assert amount(Decimal("1234.500")) == "1234,5"


@pytest.mark.parametrize(
    ("stored", "read"),
    [("1500.000", "1500"), ("1.500", "1,5"), ("-1234567.125", "-1234567,125")],
)
def test_an_amount_on_a_comma_page_takes_its_comma_ungrouped(
    stored: str, read: str
) -> None:
    """In words as its boxes write it (Ali, 2026-11-23, inventory-tng-4mc3);
    a point page's with a point, and a printed label's always so."""
    with writing_numbers(","):
        assert amount(Decimal(stored)) == read
        assert plain_amount(Decimal("1500.5")) == "1500.5"
    with writing_numbers("."):
        assert amount(Decimal("1500.5")) == "1500.5"


def test_an_amount_a_form_sends_back_is_plain_in_every_locale() -> None:
    with speaking(GERMAN):
        assert plain_amount(Decimal("1234.500")) == "1234.5"
    assert plain_amount(Decimal("1000.000")) == "1000"
    assert plain_amount(None) == amount(None) == ""


def test_a_unit_follows_an_amount_unless_counted_each() -> None:
    assert (unit_after("each"), unit_after("m")) == ("", " m")
