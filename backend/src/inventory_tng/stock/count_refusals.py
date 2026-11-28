"""Why a count form was refused before it was saved, nothing written, and
the Refusals that logs and measures it, by reason (inventory-tng-w7xt);
and the rules that may refuse it as it is saved, which core/db_errors.py
logs and counts (inventory-tng-v8sq). The routes that refuse are
stock/count_routes.py's."""

from typing import Literal, Unpack

import structlog
from opentelemetry import metrics

from inventory_tng.core.refusals import Fields, refusals
from inventory_tng.core.rule_names import RuleName
from inventory_tng.stock.count_forms import (
    CLOSED_MEANWHILE,
)
from inventory_tng.stock.counts import COUNTABLE, WHOLE
from inventory_tng.stock.units_shown import SHOWN_OTHERWISE
from inventory_tng.web.forms import CHANGED

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# An open package found that its item does not follow once opened, and a
# form counted twice: said as rules are.
UNTRACKED = "open_package_untracked"
ONCE: RuleName = "movement_item_once"
# A form saved, sent again saying otherwise (stock/store_counts.py):
# nothing is written, and Counts says so (Ali, 2026-11-22,
# inventory-tng-53im, inventory-tng-kro9).
SENT_AGAIN: RuleName = "count_sent_otherwise"

# Why a count form was refused as it was read, before its save. A form
# that is not as the page sends it is not found, and not counted.
CountRefusal = Literal[
    "named_twice",
    "listed_elsewhere",
    "closed_meanwhile",
    "shown_otherwise",
    "nothing_typed",
]
# A package the form counts closed meanwhile. A box's own problem, such
# as no number, is counted as the page shows it (web/form_problems.py).
CLOSED: CountRefusal = "closed_meanwhile"

# The rules that may refuse a count as it is saved, a place retired since
# included, though answered as not found.
RULE_REFUSALS = (
    WHOLE,
    COUNTABLE,
    UNTRACKED,
    ONCE,
    CLOSED_MEANWHILE,
    CHANGED,
    SENT_AGAIN,
    SHOWN_OTHERWISE,
)


# Each reason a form is refused for is logged and counted once.
@refusals(
    log,
    meter.create_counter(
        "inventory.counts.refused",
        unit="{count}",
        description="Count forms refused, nothing written, by reason: one "
        "refused for several is counted under each",
    ),
)
def count_refused(
    event: str, *, reason: CountRefusal, **fields: Unpack[Fields]
) -> None: ...
