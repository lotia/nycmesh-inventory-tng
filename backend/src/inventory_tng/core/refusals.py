"""A refusal, logged and counted together: a request or change refused,
nothing written, so measured and logged at once (CONTRIBUTING.md#logging,
inventory-tng-h3k0). Every refusal the app logs goes through a Refusals;
tests/codebase/test_refusals_counted.py fails on one logged by hand. Why
each kind has a counter of its own, `inventory.<things>.refused`, is in
docs/decisions/0006.

Each kind is declared by its call's signature, so `ty check` refuses a
reason not on its list before any test runs (Ali, 2026-11-27,
inventory-tng-vj5h):

    @refusals(log, counter)
    def refused(event: str, *, reason: Reason, **fields: Unpack[Fields]) -> None:
        "What is refused."

Its keyword-only parameters are the counter's attributes, each a Literal
whose values are its fixed list in code (AGENTS.md rule 7), or a str whose
list is only known as the app runs, given to refusals() by name, such as
the tables. The function's body is never run."""

import inspect
from collections.abc import Callable, Collection, Mapping
from types import UnionType
from typing import (
    Literal,
    NotRequired,
    TypeAliasType,
    TypedDict,
    Union,
    cast,
    get_args,
    get_origin,
)

from opentelemetry.metrics import Counter
from structlog.typing import FilteringBoundLogger

Level = Literal["debug", "info", "warning"]
# What an attribute whose value is not on its list, or left out, is
# counted as, so the count is never lost.
UNLISTED = "unlisted"


class Fields(TypedDict, extra_items=object):
    """What a refusal is called with besides its event and attributes: its
    log level, how many to count, and any fields to log, such as ids."""

    level: NotRequired[Level]
    count: NotRequired[int]


def _literal_values(annotation: object) -> tuple[str, ...] | None:
    """The values a Literal, an alias of one, or a union of them, allows;
    None for any other type."""
    if isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    origin = get_origin(annotation)
    if origin is Literal:
        return get_args(annotation)
    if origin in (Union, UnionType):
        parts = [_literal_values(part) for part in get_args(annotation)]
        if all(part is not None for part in parts):
            return tuple(value for part in parts if part for value in part)
    return None


def _attributes(
    shape: Callable[..., None], listed: Mapping[str, Collection[str]]
) -> dict[str, Collection[str]]:
    """Each attribute `shape` declares, by name, with its fixed list: its
    Literal's, or the one in `listed`. A declaration that says neither, or
    lists UNLISTED, which would count a bug as that value, is a bug,
    raised as the module is imported."""
    attributes: dict[str, Collection[str]] = {}
    for name, parameter in inspect.signature(shape).parameters.items():
        if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
            continue
        values = listed.get(name, _literal_values(parameter.annotation))
        if values is None:
            raise TypeError(f"{shape}: {name} is no Literal, nor listed")
        if UNLISTED in values:
            raise TypeError(f"{shape}: {name} lists {UNLISTED}")
        attributes[name] = values
    if unknown := listed.keys() - attributes.keys():
        raise TypeError(f"{shape}: no attribute {', '.join(sorted(unknown))}")
    return attributes


class Refusals[**P]:
    """Refusals of one kind, called as `shape` declares: each logged on
    `log`, the refusing module's, and added to `counter` by its
    attributes. The counter may also count what was not refused, as a
    sign-in's outcome does. Made by refusals()."""

    def __init__(
        self,
        log: FilteringBoundLogger,
        counter: Counter,
        shape: Callable[P, None],
        **listed: Collection[str],
    ) -> None:
        self._log = log
        self._counter = counter
        # Read as each is counted, not copied: Base.metadata.tables has
        # every table only once the models are imported.
        self._attributes = _attributes(shape, listed)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> None:
        """Log the event with its fields, and add `count` to the counter
        by those of them that are its attributes: more than one for a
        counter of what the refusal refused, such as units changed since
        shown.

        An attribute missing, or a value not in its list, is a bug, which
        ty check refuses where the value is written in the call. One only
        known as the app runs is counted as UNLISTED, so no count is
        lost, and logged as an error, rather than raised: the refusal
        still stands and is said, where raising would answer the request
        with a 500 instead (Ali, 2026-11-27, inventory-tng-vj5h)."""
        (event,) = cast(tuple[str], args)
        fields = cast(dict[str, object], kwargs)
        level = cast(Level, fields.pop("level", "info"))
        count = cast(int, fields.pop("count", 1))
        getattr(self._log, level)(event, **fields)
        measured: dict[str, str] = {}
        for name, values in self._attributes.items():
            value = fields.get(name)
            if isinstance(value, str) and value in values:
                measured[name] = value
            else:
                # Only the attribute's name: the value is not from its list.
                self._log.error(
                    "refusal counted as unlisted", refusal=event, attribute=name
                )
                measured[name] = UNLISTED
        self._counter.add(count, measured)


def refusals[**P](
    log: FilteringBoundLogger, counter: Counter, **listed: Collection[str]
) -> Callable[[Callable[P, None]], Refusals[P]]:
    """Declare a kind of refusal by the signature of the function it
    decorates (see this module's docstring): `listed`, the fixed list of
    each attribute only known as the app runs."""
    return lambda shape: Refusals(log, counter, shape, **listed)
