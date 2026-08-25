"""`django-environ`, with two changes to what a variable is taken to say.

A variable that says nothing is unset. The rule, what it is worth, and the one
place it makes a refusal stricter rather than softer are all in
docs/decisions/0022-an-empty-variable-is-an-unset-one.md. This is the half that
applies it to everything Django reads; `options.missing` is the predicate, and
lives there because a module that must import nothing outside the standard
library needs it too.

And a comma-separated variable is a list of values rather than of values with
spaces stuck to them, which is `entries` below.
"""

import os
from collections.abc import Iterable, MutableMapping
from typing import Any

import environ

from inventory_tng.options import missing


class Speaking:
    """A view of the environment in which a variable saying nothing is absent.

    Deliberately NOT a `MutableMapping`. Inheriting one would supply `pop`,
    `popitem`, `clear` and the rest built out of `__getitem__` -- which reports
    an empty variable as missing, so every one of them would inherit that and
    quietly refuse to remove a variable that is really there. `django-environ`
    reaches for exactly three operations, so exactly three are offered, and
    there is no fourth to be wrong about.

    A view rather than a copy: `Env.read_env` writes the `.env` file into this
    same environment and the settings are read from it afterwards.
    """

    def __init__(self, environment: MutableMapping[str, str]) -> None:
        self._environment = environment

    def __getitem__(self, name: str) -> str:
        value = self._environment[name]
        if missing(value):
            raise KeyError(name)
        # Stripped: decision 0022 says what a value carrying its file's
        # trailing newline costs, and why that is not a rare shape.
        return value.strip()

    def __contains__(self, name: str) -> bool:
        return not missing(self._environment.get(name))

    def setdefault(self, name: str, default: str) -> str:
        """Set only when the variable is genuinely absent.

        This is where the two halves of the rule pull against each other: a
        variable exported as empty is missing to a reader and present to a
        writer. Decision 0022 settles which way round that goes, and why.
        """
        if name in self._environment:
            return self._environment[name]
        self._environment[name] = default
        return default


def entries(values: Iterable[str]) -> list[str]:
    """What a comma-separated variable actually holds.

    `django-environ`'s list cast is `split(",")` and nothing else, so `"a, b"`
    -- the way anybody writes a list of two -- yields `" b"`. That matches
    nothing, for ever, and the space does not show in the file that caused it:
    it was a live production bug here in `DJANGO_ALLOWED_HOSTS`, and
    `CORS_ALLOWED_ORIGINS` carried the identical one a screen below in
    `settings.py`.

    Fixed at the mechanism rather than at either variable. Every list cast goes
    through `Env.parse_value` below, so a third one added later is trimmed
    without anybody remembering this, and `hosts.allowed_hosts` calls this
    directly for the list only a running deployment can supply.

    Blanks are dropped with the spaces. A trailing comma, or a value of `""`
    arriving as one empty element, would otherwise be an entry that matches
    nothing -- and for `ALLOWED_HOSTS` an empty pattern is worth being certain
    about rather than reasoning about.
    """
    return [value.strip() for value in values if value.strip()]


class Env(environ.Env):
    """`environ.Env`, reading through the view above, and trimming every list."""

    ENVIRON = Speaking(os.environ)

    @classmethod
    def parse_value(cls, value: Any, cast: Any) -> Any:
        """Every `list`-cast read, trimmed once, here.

        The cast is compared to `list` itself rather than to a list instance:
        `django-environ` spells "a list of some element type" as `[int]`, which
        casts each element and would trim a value this application never asks
        for. `(list, [])` is the only shape `settings.py` uses.
        """
        parsed = super().parse_value(value, cast)
        return entries(parsed) if cast is list else parsed
