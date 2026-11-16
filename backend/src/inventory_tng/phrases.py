"""The words people read, through gettext (docs/decisions/0022): Babel's
catalogue, read from its PO file as the app starts, for Python and for the
templates alike, where Jinja's i18n extension reads it.

Each phrase's English is its message id. Python names one as a constant,
`lazy_gettext("...")`, said in the locale active when it is shown; a plural,
or one made with values, as a function that calls `ngettext` or `gettext`
with them. A template says its words in `{% trans %}`. Either way a message
formats its values as `%(name)s`, always, so a literal per cent is `%%`.
A context (`pgettext`) only where the same English means two things, from
the fixed list in CONTRIBUTING.md (Phrases).
"""

from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.support import LazyProxy, Translations
from jinja2 import Environment

ENGLISH_PO = Path(__file__).parent / "locales" / "en" / "LC_MESSAGES" / "messages.po"


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
    """The form for `num` in the active locale, with `%(num)d` filled."""
    return _active.ngettext(singular, plural, num) % ({"num": num} | values)


def lazy_gettext(message: str, **values: object) -> LazyProxy:
    """`message`, said in the locale active when it is shown, not when named."""
    return LazyProxy(gettext, message, enable_cache=False, **values)


def lazy_pgettext(context: str, message: str, **values: object) -> LazyProxy:
    """`message`, as `context` means it, said when shown."""
    return LazyProxy(pgettext, context, message, enable_cache=False, **values)


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
