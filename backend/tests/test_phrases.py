"""The server's words, read through Babel's catalogue (phrases.py,
docs/decisions/0022), in Python and in the templates."""

import importlib
import pkgutil
import re
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from babel.messages.catalog import Catalog, Message
from babel.support import LazyProxy, Translations

import inventory_tng
from inventory_tng import (
    accounts,
    activity,
    labels,
    over_recorded,
    reading_rules,
    units,
)
from inventory_tng.admin import admins as admin_admins
from inventory_tng.admin import common, counts, stock, used_up
from inventory_tng.admin import devices as admin_devices
from inventory_tng.admin import items as admin_items
from inventory_tng.admin import over_recorded as admin_over_recorded
from inventory_tng.admin import reading_rules as admin_reading_rules
from inventory_tng.admin import settings as admin_settings
from inventory_tng.admin import volunteers as admin_volunteers
from inventory_tng.pages import retired_holding, templates
from inventory_tng.phrases import (
    ENGLISH_PO,
    compiled,
    gettext,
    lazy_gettext,
    lazy_pgettext,
    ngettext,
    pgettext,
    read,
    speaking,
)
from inventory_tng.resets import RESET_NOTICE

# Each letter accented, so English left out of the catalogue shows plain.
ACCENTS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "àƀćďēƒĝĥĩĵķĺɱńōƥʠŕśţũʋŵẋŷźÀƁĆĎĒƑĜĤĨĴĶĹḾŃŌƤɊŔŚŢŨṼŴẊŶŹ",
)
# A message's placeholders, and a literal per cent, kept as they are.
PLACEHOLDER = re.compile(r"(%\(\w+\)[sd]|%%)")


def accented(message: str) -> str:
    parts = PLACEHOLDER.split(message)
    return "".join(
        part if index % 2 else part.translate(ACCENTS)
        for index, part in enumerate(parts)
    )


def lone_per_cents(message: str) -> list[str]:
    """Each per cent sign that is neither a placeholder nor `%%`."""
    return re.findall("%", PLACEHOLDER.sub("", message))


def forms(message: Message) -> tuple[str, ...]:
    """A message's English: one form, or a plural's forms."""
    return (message.id,) if isinstance(message.id, str) else tuple(message.id)


def pseudo_locale() -> Translations:
    """The English catalogue with every message accented, built by Babel."""
    catalogue = read(ENGLISH_PO)
    for message in catalogue:
        if not message.id:  # The header.
            continue
        said = tuple(accented(form) for form in forms(message))
        message.string = said[0] if isinstance(message.id, str) else said
    return compiled(catalogue)


PSEUDO = pseudo_locale()
SHELF = SimpleNamespace(id=7, name="Ωμέγα")

# What has moved into the catalogue, each as it is shown. Each part of
# moving the words (inventory-tng-oic) adds what it moved, until every
# page is here.
NAME = SHELF.name


def named_phrases() -> list[LazyProxy]:
    """Every phrase the server's Python names as a constant, alone or in a
    table (a dict, tuple or list, such as NOTICES), in every module."""
    found: list[LazyProxy] = []

    def gather(value: object, depth: int) -> None:
        if isinstance(value, LazyProxy):
            found.append(value)
        elif depth and isinstance(value, dict):
            for each in value.values():
                gather(each, depth - 1)
        elif depth and isinstance(value, (tuple, list)):
            for each in value:
                gather(each, depth - 1)

    for module in pkgutil.walk_packages(inventory_tng.__path__, "inventory_tng."):
        for value in vars(importlib.import_module(module.name)).values():
            gather(value, 3)
    return found


# Each phrase said with values, by its function, the values not English.
SAID_WITH_VALUES: list[Callable[[], str]] = [
    lambda: accounts.username_problem("!") or "",
    lambda: accounts.name_up_to(9),
    lambda: accounts.password_problem("", "") or "",
    lambda: accounts.password_problem("." * 1000, "") or "",
    lambda: common.up_to(9),
    lambda: common.at_most_places(3),
    lambda: common.revoke_title(NAME),
    *(lambda said=said: said(NAME, NAME) for said in activity.WORDS.values()),
    *(lambda said=said: said(NAME) for said in admin_items.TAKEN_FIELDS.values()),
    *(lambda said=said: said(NAME) for said in admin_items.ALIAS_TAKEN.values()),
    lambda: admin_items.read_as_one(NAME),
    *(lambda said=said: said(1, NAME) for said in reading_rules.NAMED.values()),
    lambda: reading_rules.both(NAME, NAME),
    lambda: reading_rules.near(NAME),
    lambda: admin_reading_rules.reading_title(NAME),
    lambda: admin_volunteers.merge_title(NAME, NAME),
    lambda: admin_admins.link_title(NAME),
    lambda: admin_admins.reset_title(NAME),
    lambda: admin_devices.uses_problem(9),
    lambda: admin_devices.days_problem(9),
    lambda: admin_devices.revoke_many_title(1),
    lambda: admin_devices.revoke_many_title(3),
    *(lambda said=said: said(1, 9) for said in admin_settings.WHOLE.values()),
    lambda: admin_settings.whole_minutes(1, 9),
    lambda: used_up.close_title(NAME),
    lambda: counts.count_title(NAME),
    lambda: counts.moved_meanwhile(0),
    lambda: counts.moved_meanwhile(1),
    lambda: counts.moved_meanwhile(3),
    lambda: counts.sealed_how_many(NAME),
    lambda: counts.opened_left(NAME, "2026-11-15"),
    lambda: stock.recorded_more_view(2),
    lambda: admin_over_recorded.place_brought_back(NAME),
    lambda: admin_over_recorded.not_changed(NAME),
    lambda: admin_over_recorded.waiting_view(2),
    *(
        lambda said=said: said(NAME, NAME)
        for said in admin_over_recorded.RETIRED_EMPTY.values()
    ),
    lambda: over_recorded.times(2, NAME),
    lambda: over_recorded.from_an_open_package(NAME),
    lambda: over_recorded.sealed(NAME),
    lambda: over_recorded.opened(NAME),
    lambda: over_recorded.loose(NAME),
    lambda: over_recorded.held_words(NAME),
    lambda: over_recorded.adjusted_at(NAME),
    lambda: over_recorded.end_only(NAME),
    lambda: over_recorded.closed_on_day(NAME, NAME),
    lambda: over_recorded.counted_on(NAME, NAME),
    lambda: labels.package_label(NAME, NAME, NAME),
    lambda: labels.place_words(NAME, NAME),
    *(lambda said=said: said("") for said in units.NAMES.values()),
]

COVERED: dict[str, Callable[[], str]] = {
    "every phrase named as a constant": lambda: " ".join(
        str(phrase) for phrase in named_phrases()
    ),
    "every phrase said with values": lambda: " ".join(
        said() for said in SAID_WITH_VALUES
    ),
    "the reset notice": lambda: str(RESET_NOTICE),
    "one retired place holding stock": lambda: retired_holding(1),
    "retired places holding stock": lambda: retired_holding(3),
    "_retired_holding.html": lambda: templates.get_template(
        "_retired_holding.html"
    ).render(retired_places=[SHELF, SHELF], counts_path="/admin/counts"),
}


@pytest.mark.parametrize("part", COVERED)
def test_what_has_moved_says_nothing_in_plain_english(part: str) -> None:
    with speaking(PSEUDO):
        shown = COVERED[part]()
    words = re.sub(r"<[^>]*>", " ", shown)
    assert re.findall(r"[A-Za-z]{2,}", words) == []
    assert words.strip()


def test_english_is_the_catalogues_own_words() -> None:
    assert str(RESET_NOTICE) == "Your account is reset: sign in."
    assert retired_holding(1) == "1 retired place holds stock"
    assert retired_holding(3) == "3 retired places hold stock"


def test_a_lazy_phrase_is_said_in_the_locale_active_when_shown() -> None:
    with speaking(PSEUDO):
        assert str(RESET_NOTICE) == accented("Your account is reset: sign in.")
    assert str(RESET_NOTICE) == "Your account is reset: sign in."


def test_values_fill_their_placeholders_and_a_double_per_cent_is_one() -> None:
    assert gettext("%(n)s of %(m)s", n=1, m=2) == "1 of 2"
    assert gettext("Half: 50%%") == "Half: 50%"
    assert ngettext("%(num)d box", "%(num)d boxes", 2) == "2 boxes"
    assert ngettext("%(num)d %(what)s", "%(num)d %(what)ss", 1, what="box") == "1 box"
    assert str(lazy_gettext("Half: 50%%")) == "Half: 50%"


def test_a_context_tells_two_meanings_of_the_same_english_apart() -> None:
    catalogue = Catalog()
    catalogue.add("To", "Into", context="place")
    catalogue.add("To", "Recipient", context="mail")
    with speaking(compiled(catalogue)):
        assert pgettext("place", "To") == "Into"
        assert str(lazy_pgettext("mail", "To")) == "Recipient"
    assert pgettext("place", "To") == "To"


def test_a_templates_values_are_escaped_and_its_lines_read_as_one() -> None:
    said = templates.env.from_string(
        "{% trans name=name %}Start\n  counting {{ name }}{% endtrans %}"
    ).render(name="<b>")
    assert said == "Start counting &lt;b&gt;"


def test_speaking_ends_with_its_block_even_on_an_error() -> None:
    with pytest.raises(ValueError, match="stop"), speaking(PSEUDO):
        raise ValueError("stop")
    assert str(RESET_NOTICE) == "Your account is reset: sign in."


def test_a_lone_per_cent_is_found() -> None:
    assert lone_per_cents("Half: 50%") == ["%"]
    assert lone_per_cents("%(num)d of 50%%") == []


def test_no_message_has_a_lone_per_cent() -> None:
    # Every message is %-formatted when said, so a lone % would fail the page.
    catalogue = read(ENGLISH_PO)
    said = [form for message in catalogue if message.id for form in forms(message)]
    assert said
    assert [form for form in said if lone_per_cents(form)] == []
