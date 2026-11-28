"""Why a count form was refused before it was saved, nothing written, and
the Refusals that logs and measures it, by reason (inventory-tng-w7xt);
and the rules that may refuse it as it is saved, which core/db_errors.py
logs and counts (inventory-tng-v8sq). The routes that refuse are
stock/count_routes.py's."""

from typing import Literal, get_args

import structlog
from opentelemetry import metrics

from inventory_tng.core.refusals import Refusals
from inventory_tng.stock.count_forms import (
    CLOSED_MEANWHILE,
    TYPE_IT_HERE,
    CountForm,
)
from inventory_tng.stock.counts import COUNTABLE, WHOLE
from inventory_tng.stock.units_shown import SHOWN_OTHERWISE
from inventory_tng.web.forms import CHANGED

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# An open package found that its item does not follow once opened, and a
# form counted twice: said as rules are.
UNTRACKED = "open_package_untracked"
ONCE = "movement_item_once"
# A form saved, sent again saying otherwise (stock/store_counts.py):
# nothing is written, and Counts says so (Ali, 2026-11-22,
# inventory-tng-53im, inventory-tng-kro9).
SENT_AGAIN = "count_sent_otherwise"

# Why a count form was refused as it was read, before its save. A form
# that is not as the page sends it is not found, and not counted.
CountRefusal = Literal[
    "named_twice",
    "listed_elsewhere",
    "closed_meanwhile",
    "box_problem",
    "shown_otherwise",
    "nothing_typed",
]
# Why a count form is refused for its boxes, as logged too: a package it
# counts closed meanwhile, or a box's own problem, such as no number
# (inventory-tng-0k3a). An item named twice and something else here
# listed elsewhere are logged on their own.
CLOSED: CountRefusal = "closed_meanwhile"
BOX_PROBLEM: CountRefusal = "box_problem"

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
count_refused = Refusals(
    log,
    meter.create_counter(
        "inventory.counts.refused",
        unit="{count}",
        description="Count forms refused, nothing written, by reason: one "
        "refused for several is counted under each",
    ),
    reason=get_args(CountRefusal),
)


def box_refusals(sent: CountForm) -> list[CountRefusal]:
    """Why read_count's problems refuse the count, from CLOSED and
    BOX_PROBLEM, leaving out the boxes of an item named twice and the line
    something else here should be typed on."""
    reasons: list[CountRefusal] = [CLOSED] if CLOSED_MEANWHILE in sent.problems else []
    if any(
        field != CLOSED_MEANWHILE
        and field not in sent.twice
        and words is not TYPE_IT_HERE
        for field, words in sent.problems.items()
    ):
        reasons.append(BOX_PROBLEM)
    return reasons
