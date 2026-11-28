"""A refusal, logged and counted together: a request or change refused,
nothing written, so measured and logged at once (CONTRIBUTING.md#logging,
inventory-tng-h3k0). Every refusal the app logs goes through a Refusals;
tests/codebase/test_refusals_counted.py fails on one logged by hand.

Each kind of refusal has its own counter, named for what is refused, as
`inventory.<things>.refused` with a `reason`: what is refused differs in
its unit (a request, a form, a report), and a total across kinds would
mean nothing, so OpenTelemetry's naming conventions give each its own
name, and attributes only what is worth summing across."""

from collections.abc import Collection
from typing import Literal

from opentelemetry.metrics import Counter

Level = Literal["debug", "info", "warning"]


class Refusals:
    """Refusals of one kind: each logged on `log`, the refusing module's,
    and added to `counter` by the attributes named in `attributes`, each
    from its fixed list in code (AGENTS.md rule 7). The counter may also
    count what was not refused, as a sign-in's outcome does."""

    def __init__(
        self, log: object, counter: Counter, **attributes: Collection[str]
    ) -> None:
        self._log = log
        self._counter = counter
        # Read as each is counted, not copied: Base.metadata.tables has
        # every table only once the models are imported.
        self._attributes = attributes

    def __call__(
        self, event: str, *, level: Level = "info", count: int = 1, **fields: object
    ) -> None:
        """Log `event` with `fields`, and add `count` to the counter by
        those of them that are its attributes, every one given: more than
        one for a counter of what the refusal refused, such as units
        changed since shown. A value not in its list is a bug, and raised,
        before anything is logged or counted."""
        measured: dict[str, str] = {}
        for name, values in self._attributes.items():
            value = fields[name]
            if not isinstance(value, str) or value not in values:
                raise ValueError(f"{name}={value!r} is not one of its list")
            measured[name] = value
        self._counter.add(count, measured)
        getattr(self._log, level)(event, **fields)
