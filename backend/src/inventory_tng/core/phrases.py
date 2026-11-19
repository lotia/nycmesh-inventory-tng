"""The words people read, through gettext (docs/decisions/0022): Babel's
catalogue, read from its PO file as the app starts, for Python and for the
templates alike, where Jinja's i18n extension reads it.

Each phrase's English is its message id. Python names one as a constant,
`lazy_gettext("...")`, said in the locale active when it is shown; a plural,
or one made with values, as a function that calls `ngettext` or `gettext`
with them. A template says its words in `{% trans %}`. Either way a message
formats its values as `%(name)s`, always, so a literal per cent is `%%`.
A context (`pgettext`) only where the same English means two things, from
the fixed list in CONTRIBUTING.md (Phrases). A number people read is
written as the locale writes it (`number`, a template's `| number`), a
plural's count too.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from decimal import Decimal
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from babel import Locale, localedata
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.numbers import format_decimal
from babel.support import LazyProxy, Translations
from sqlalchemy import BindParameter, String, bindparam

if TYPE_CHECKING:  # Only templates need Jinja: rules read phrases without it.
    from jinja2 import Environment
    from jinja2.runtime import Context

# The app's name: a name, not a phrase, so the same in every language, and
# never in the catalogue. Messages take it as %(brand)s. Authenticator apps
# show it too (authenticator_totp.ISSUER), so it must not change with the locale.
BRAND = "NYC Mesh Inventory"
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


ENGLISH = compiled(read(ENGLISH_PO))
_active = ENGLISH


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
    digit it has, never rounded."""
    locale = _number_locale(_active.info().get("language"))
    return format_decimal(value, locale=locale, decimal_quantization=False)


@cache
def _number_locale(language: str | None) -> Locale:
    """The locale `number` writes in for a catalogue's `language`, parsed
    once: English if Babel knows no such language."""
    return Locale.parse(language if language and localedata.exists(language) else "en")


def _counts_written(say_plural: Callable[..., str]) -> Callable[..., str]:
    """Jinja's ngettext, `say_plural`, writing the count as the locale does,
    as `num` and under the name the template gave it (`{% trans
    count=... %}`), while the form is still chosen by the number."""
    # Here, not at the top: rules read phrases without loading Jinja.
    from jinja2 import pass_context

    @pass_context
    def say(
        context: Context, singular: str, plural: str, num: int, /, **values: object
    ) -> str:
        # Positional only, as Jinja passes them: a template's values may
        # take any name, plural or context too.
        written = number(num)
        values = {
            name: written if type(value) is int and value == num else value
            for name, value in values.items()
        }
        return say_plural(context, singular, plural, num, **({"num": written} | values))

    return say


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


def install(environment: Environment) -> None:
    """Give `environment` Jinja's i18n extension, reading the active
    catalogue: newstyle, so a message's values are escaped and filled as in
    Python, and trimmed, so a message's lines read as one."""
    environment.add_extension("jinja2.ext.i18n")
    environment.policies["ext.i18n.trimmed"] = True
    # The extension adds this method, which the type checker cannot see.
    environment.install_gettext_callables(  # ty: ignore[unresolved-attribute]
        lambda message: _active.gettext(message),
        lambda singular, plural, num: _active.ngettext(singular, plural, num),
        newstyle=True,
        pgettext=lambda context, message: _active.pgettext(context, message),
        npgettext=lambda context, singular, plural, num: _active.npgettext(
            context, singular, plural, num
        ),
    )
    # Wrapped after install: the count is written outside Jinja's newstyle
    # wrapper, which still escapes and fills the values.
    callables = cast(dict[str, Any], environment.globals)
    callables["ngettext"] = _counts_written(callables["ngettext"])
