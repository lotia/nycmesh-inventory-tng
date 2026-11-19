"""The templates' words: Jinja's i18n extension, reading the catalogue
core/phrases.py holds, with a plural's count written as the locale
writes it (docs/decisions/0022)."""

from collections.abc import Callable
from typing import Any, cast

from jinja2 import Environment, pass_context
from jinja2.runtime import Context

from inventory_tng.core.phrases import active_catalogue, number


def install(environment: Environment) -> None:
    """Give `environment` Jinja's i18n extension, reading the active
    catalogue: newstyle, so a message's values are escaped and filled as in
    Python, and trimmed, so a message's lines read as one."""
    environment.add_extension("jinja2.ext.i18n")
    environment.policies["ext.i18n.trimmed"] = True
    # The extension adds this method, which the type checker cannot see.
    environment.install_gettext_callables(  # ty: ignore[unresolved-attribute]
        lambda message: active_catalogue().gettext(message),
        lambda singular, plural, num: active_catalogue().ngettext(
            singular, plural, num
        ),
        newstyle=True,
        pgettext=lambda context, message: active_catalogue().pgettext(context, message),
        npgettext=lambda context, singular, plural, num: active_catalogue().npgettext(
            context, singular, plural, num
        ),
    )
    # Wrapped after install: the count is written outside Jinja's newstyle
    # wrapper, which still escapes and fills the values.
    callables = cast(dict[str, Any], environment.globals)
    callables["ngettext"] = _counts_written(callables["ngettext"])


def _counts_written(say_plural: Callable[..., str]) -> Callable[..., str]:
    """Jinja's ngettext, `say_plural`, writing the count as the locale does,
    as `num` and under the name the template gave it (`{% trans
    count=... %}`), while the form is still chosen by the number."""

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
