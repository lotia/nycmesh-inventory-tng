"""Which decimal separator a page's number boxes take, from the browser's
Accept-Language (web/decimal_separator.py; inventory-tng-qnwz)."""

from collections.abc import Callable

import pytest
from babel.core import UnknownLocaleError
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from inventory_tng.web import decimal_separator
from inventory_tng.web.decimal_separator import languages, separator_for

from helpers.markup import read


@pytest.mark.parametrize(
    ("header", "separator"),
    [
        ("de", ","),
        ("de-DE", ","),
        ("en", "."),
        ("en-US,en;q=0.9", "."),
        ("fr", ","),
        # Swiss German writes a point.
        ("de-CH", "."),
        # Babel's best match: the language, where it has no such region.
        ("de-ZZ", ","),
        ("", "."),
        ("garbage", "."),
        ("!!, ;q=x", "."),
        ("*", "."),
        # The most wanted first, whatever the order written.
        ("en;q=0.5, de", ","),
        ("de;q=0, en", "."),
        ("xx, de", ","),
        # A parameter other than q leaves the weight as it was.
        ("en;level=1;q=0.5, de", ","),
    ],
)
def test_the_separator_is_the_best_matched_locales(header: str, separator: str) -> None:
    assert separator_for(header) == separator


def test_a_locale_without_latin_digits_data_takes_a_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where Babel has no decimal symbol for the locale it matched."""

    def unknown(*_: object, **__: object) -> str:
        raise UnknownLocaleError("de")

    monkeypatch.setattr(decimal_separator, "get_decimal_symbol", unknown)
    separator_for.cache_clear()
    try:
        assert separator_for("de") == "."
    finally:
        separator_for.cache_clear()


def test_languages_are_read_most_wanted_first() -> None:
    assert languages("en;q=0.5, de-DE, fr;q=0.9, *;q=0.1, it;q=0") == [
        "de_DE",
        "fr",
        "en",
    ]


def test_only_the_most_wanted_languages_are_matched() -> None:
    """A header of many tags is matched by its first few, as a browser
    sends a few and each costs a look through every locale."""
    many = ", ".join(f"x{at};q=0.9" for at in range(30))
    assert languages(f"{many}, de;q=0.5") == languages(many)
    assert len(languages(many)) == decimal_separator.MOST_LANGUAGES


@pytest.mark.parametrize(("language", "separator"), [("de", ","), ("en", ".")])
def test_a_page_says_its_separator_and_logs_it(
    make_client_without_database: Callable[..., TestClient],
    language: str,
    separator: str,
) -> None:
    """On its <html>, for its scripts; logged once, at debug."""
    with capture_logs() as logs:
        page = make_client_without_database().get(
            "/nowhere", headers={"Accept-Language": language}
        )
    assert read(page.text)["data-decimal"] == separator
    chosen = [line for line in logs if line["event"] == "decimal separator chosen"]
    assert [(line["log_level"], line["separator"]) for line in chosen] == [
        ("debug", separator)
    ]
