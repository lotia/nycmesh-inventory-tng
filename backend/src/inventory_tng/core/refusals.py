"""A refusal, logged and counted together: a request or change refused,
nothing written, so measured and logged at once (CONTRIBUTING.md#logging,
inventory-tng-h3k0). Every refusal the app logs goes through a Refusals;
tests/codebase/test_refusals_counted.py fails on one logged by hand. Why
each kind has a counter of its own, `inventory.<things>.refused`, is in
docs/decisions/0006."""

from collections.abc import Collection
from typing import Literal

from opentelemetry.metrics import Counter
from structlog.typing import FilteringBoundLogger

Level = Literal["debug", "info", "warning"]


class Refusals:
    """Refusals of one kind: each logged on `log`, the refusing module's,
    and added to `counter` by the attributes named in `attributes`, each
    from its fixed list in code (AGENTS.md rule 7). The counter may also
    count what was not refused, as a sign-in's outcome does."""

    def __init__(
        self, log: FilteringBoundLogger, counter: Counter, **attributes: Collection[str]
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
        changed since shown.

        An attribute missing, or a value not in its list, is a bug. It is
        logged as an error and not counted, rather than raised: the
        refusal still stands and is said, where raising would answer the
        request with a 500 instead, and a test reading the counter sees
        it missing."""
        getattr(self._log, level)(event, **fields)
        measured: dict[str, str] = {}
        for name, values in self._attributes.items():
            value = fields.get(name)
            if not isinstance(value, str) or value not in values:
                # Only the attribute's name: the value is not from its list.
                self._log.error("refusal not counted", refusal=event, attribute=name)
                return
            measured[name] = value
        self._counter.add(count, measured)
