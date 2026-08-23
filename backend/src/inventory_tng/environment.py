"""`django-environ`, with one change: a variable that says nothing is unset.

The rule, what it is worth, and the one place it makes a refusal stricter
rather than softer are all in
docs/decisions/0022-an-empty-variable-is-an-unset-one.md. This is the half that
applies it to everything Django reads; `options.missing` is the predicate, and
lives there because a module that must import nothing outside the standard
library needs it too.
"""

import os
from collections.abc import MutableMapping

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


class Env(environ.Env):
    """`environ.Env`, reading through the view above."""

    ENVIRON = Speaking(os.environ)
