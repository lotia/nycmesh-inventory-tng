"""The server's words, read through Babel's catalogue (phrases.py,
docs/decisions/0022), in Python and in the templates."""

import re
from collections.abc import Callable
from io import BytesIO
from types import SimpleNamespace

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.support import Translations

from inventory_tng import phrases
from inventory_tng.pages import retired_holding, templates
from inventory_tng.phrases import (
    LOCALES,
    gettext,
    lazy_gettext,
    lazy_pgettext,
    ngettext,
    pgettext,
    speaking,
)
from inventory_tng.resets import RESET_NOTICE

ENGLISH_PO = LOCALES / "en" / "LC_MESSAGES" / "messages.po"
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


def pseudo_locale() -> Translations:
    """The English catalogue with every message accented, built by Babel."""
    with ENGLISH_PO.open("rb") as po:
        catalogue = read_po(po)
    for message in catalogue:
        if not message.id:  # The header.
            continue
        if isinstance(message.id, str):
            message.string = accented(message.id)
        else:
            message.string = tuple(accented(form) for form in message.id)
    compiled = BytesIO()
    write_mo(compiled, catalogue)
    compiled.seek(0)
    return Translations(compiled)


PSEUDO = pseudo_locale()
SHELF = SimpleNamespace(id=7, name="Ωμέγα")

# What has moved into the catalogue, each as it is shown. Each part of
# moving the words (inventory-tng-oic) adds what it moved, until every
# page is here.
COVERED: dict[str, Callable[[], str]] = {
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
    assert str(lazy_gettext("To %(place)s", place="Shelf")) == "To Shelf"


def test_a_context_tells_two_meanings_of_the_same_english_apart() -> None:
    contexts = BytesIO()
    catalogue = read_po(BytesIO(b'msgid ""\nmsgstr ""\n'))
    catalogue.add("To", "Into", context="place")
    catalogue.add("To", "Recipient", context="mail")
    write_mo(contexts, catalogue)
    contexts.seek(0)
    with speaking(Translations(contexts)):
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
    assert phrases.gettext("Your account is reset: sign in.") == str(RESET_NOTICE)
    assert str(RESET_NOTICE) == "Your account is reset: sign in."
