"""The words people read, through gettext (docs/decisions/0022): Babel's
catalogue, read from its PO file as the app starts, for Python and for the
templates alike, which web/template_phrases.py gives it to.

Each phrase's English is its message id. Python names one as a constant,
`lazy_gettext("...")`, said in the locale active when it is shown; a plural,
or one made with values, as a function that calls `ngettext` or `gettext`
with them. A template says its words in `{% trans %}`. Either way a message
formats its values as `%(name)s`, always, so a literal per cent is `%%`.
A context (`pgettext`) only where the same English means two things, from
the fixed list in CONTRIBUTING.md (Phrases). A number people read is
written as the locale writes it (`number`, a template's `| number`), a
plural's count too, and a list of names as it joins them (`joined`); on
a page whose number boxes take a comma, with that comma and no grouping
(`writing_numbers`).
"""

from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Literal

from babel import Locale, localedata
from babel.lists import format_list
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.numbers import format_decimal, get_decimal_symbol
from babel.support import LazyProxy, Translations
from sqlalchemy import BindParameter, String, bindparam

# The app's name: a name, not a phrase, so the same in every language, and
# never in the catalogue. Messages take it as %(brand)s. Authenticator apps
# show it too (authenticator_totp.ISSUER), so it must not change with the locale.
BRAND = "NYC Mesh Inventory"
# Between the names of a place's path, as a count form and a printed label
# name a place that is part of another (the scan page's search.ts too).
PATH_BETWEEN = " \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} "
ENGLISH_PO = (
    Path(__file__).parents[1] / "locales" / "en" / "LC_MESSAGES" / "messages.po"
)


def read(path: Path) -> Catalog:
    """A PO catalogue, as Babel reads it."""
    with path.open("rb") as po:
        return read_po(po)


def compiled(catalogue: Catalog) -> Translations:
    """`catalogue`, compiled in memory by Babel, as gettext reads it."""
    mo = BytesIO()
    write_mo(mo, catalogue)
    mo.seek(0)
    return Translations(mo)


# A decimal separator: the one a page's number boxes take
# (web/decimal_separator.py), which its words' numbers follow.
Separator = Literal[".", ","]
# The decimal separator numbers in words are written with now: "." as the
# words' locale writes, unless `writing_numbers` gives ",".
_separator: ContextVar[Separator] = ContextVar("separator", default=".")

ENGLISH = compiled(read(ENGLISH_PO))
_active = ENGLISH


def active_catalogue() -> Translations:
    """The catalogue said in now: English, unless `speaking` gives another."""
    return _active


@contextmanager
def speaking(translations: Translations) -> Generator[None]:
    """Say everything in `translations` until the block ends, as the
    pseudo-locale's tests do. Only English is spoken otherwise."""
    global _active
    before, _active = _active, translations
    try:
        yield
    finally:
        _active = before


@contextmanager
def writing_numbers(separator: Separator) -> Generator[None]:
    """Write numbers in words with `separator` until the block ends: with
    ",", as a page whose boxes take one does (Ali, 2026-11-23,
    inventory-tng-4mc3), "1500" and "1,5", not grouped, so a number in a
    sentence reads as its box would. One request's or task's own, never
    another's."""
    token = _separator.set(separator)
    try:
        yield
    finally:
        _separator.reset(token)


def gettext(message: str, **values: object) -> str:
    """`message` in the active locale, its placeholders filled."""
    return _active.gettext(message) % values


def pgettext(context: str, message: str, **values: object) -> str:
    """`message`, as `context` means it, in the active locale."""
    # Babel types it loosely; gettext's always gives a str.
    return str(_active.pgettext(context, message)) % values


def ngettext(singular: str, plural: str, num: int, **values: object) -> str:
    """The form for `num` in the active locale, with `%(num)s` filled with
    it as the locale writes it."""
    return _active.ngettext(singular, plural, num) % ({"num": number(num)} | values)


def number(value: int | Decimal) -> str:
    """`value` as the active locale writes it, such as 1,000 in English and
    1.000 in German: its catalogue's language, English if it names none
    Babel knows (a pseudo-locale's, such as en_XA). A decimal keeps every
    digit it has, never rounded. Within `writing_numbers(",")`, ungrouped
    and with a comma: 1500 and 1,5."""
    locale = _number_locale(_active.info().get("language"))
    comma = _separator.get() == ","
    written = format_decimal(
        value, locale=locale, decimal_quantization=False, group_separator=not comma
    )
    return written.replace(get_decimal_symbol(locale), ",") if comma else written


def joined(names: Sequence[str]) -> str:
    """Names joined as the active locale joins a list, such as "A, B and C"
    in English: in its catalogue's language, as `number` writes."""
    return format_list(
        list(names), locale=_number_locale(_active.info().get("language"))
    )


@cache
def _number_locale(language: str | None) -> Locale:
    """The locale `number` and `joined` write in for a catalogue's
    `language`, parsed once: English if Babel knows no such language."""
    return Locale.parse(language if language and localedata.exists(language) else "en")


# A phrase as code holds it: said already, or a lazy one said when shown.
Words = str | LazyProxy


def lazy(say: Callable[..., str], *values: object) -> LazyProxy:
    """What `say(*values)` says, said in the locale active when it is shown,
    not when named: a phrase with values or a plural that a table holds."""
    return LazyProxy(say, *values, enable_cache=False)


def lazy_gettext(message: str) -> LazyProxy:
    """`message`, said in the locale active when it is shown, not when named."""
    return lazy(gettext, message)


def lazy_pgettext(context: str, message: str) -> LazyProxy:
    """`message`, as `context` means it, said when shown."""
    return lazy(pgettext, context, message)


def in_query(phrase: Words) -> BindParameter[str]:
    """`phrase` as a value in a query, such as words a list sorts by: said
    in the locale active when the query runs, not when it is written."""
    return bindparam(None, callable_=lambda: str(phrase), type_=String())
