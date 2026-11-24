"""The decimal separator a request's number boxes take, "." or ",": the
one the locale its browser asks for first writes, as Babel best matches
its Accept-Language, so a phone whose decimal keypad offers only a comma
can type 2,5 (Ali, 2026-11-23, inventory-tng-qnwz). Nothing is stored,
and the words stay English. Each page says it in its <html data-decimal>
(admin_base.html, frontend_page.html), which the pages' scripts read
(frontend/src/shared/quantity.ts), and its forms read the boxes in it
(web/forms.quantity). Its numbers in words follow it too
(web/numbers_in_words.py)."""

from functools import cache, lru_cache

import structlog
from babel.core import UnknownLocaleError, negotiate_locale
from babel.localedata import locale_identifiers
from babel.numbers import get_decimal_symbol
from starlette.requests import Request

from inventory_tng.web.formatting import Separator

log = structlog.get_logger(__name__)

# Where a request's separator is kept, in its state, once chosen.
_CHOSEN = "decimal_separator"
# The most language tags of a header matched: a browser sends a few, and
# matching each costs a look through every locale Babel knows.
MOST_LANGUAGES = 10


def languages(header: str) -> list[str]:
    """The language tags an Accept-Language header asks for, most wanted
    first, as Babel writes them ("de_DE"): those it refuses (q=0) and "*"
    left out, ties in the order given, MOST_LANGUAGES at most."""
    ranked: list[tuple[float, int, str]] = []
    for at, part in enumerate(header.split(",")):
        tag, *parameters = (bit.strip() for bit in part.split(";"))
        weight = 1.0
        for parameter in parameters:
            name, _, value = parameter.partition("=")
            if name.strip().lower() == "q":
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0.0
        # Not above 0 leaves NaN out too.
        if tag and tag != "*" and weight > 0:
            ranked.append((-weight, at, tag.replace("-", "_")))
    return [tag for _, _, tag in sorted(ranked)][:MOST_LANGUAGES]


@cache
def _known() -> list[str]:
    """Every locale Babel has data for."""
    return locale_identifiers()


# Headers repeat, but anyone can send any: only so many are kept.
@lru_cache(maxsize=256)
def separator_for(header: str) -> Separator:
    """The decimal separator of the locale Babel best matches to an
    Accept-Language `header`, in Latin digits: "," where it writes one, else
    "."; "." for none it knows."""
    best = negotiate_locale(languages(header), _known(), sep="_")
    if best is None:
        return "."
    try:
        symbol = get_decimal_symbol(best, numbering_system="latn")
    except UnknownLocaleError, ValueError:
        return "."
    return "," if symbol == "," else "."


def decimal_separator(request: Request) -> Separator:
    """The decimal separator this request's number boxes take
    (separator_for its Accept-Language), chosen once a request."""
    state = request.scope.setdefault("state", {})
    if _CHOSEN not in state:
        state[_CHOSEN] = separator_for(request.headers.get("accept-language", ""))
        log.debug("decimal separator chosen", separator=state[_CHOSEN])
    return state[_CHOSEN]
