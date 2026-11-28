"""A counter or histogram measured at once, as it measures no write, with
why (CONTRIBUTING.md#logging). Every one the app makes is declared where
it is made: a write's is a WriteMetric (core/write_metrics.py), a
refusal's is given to refusals() (core/refusals.py), and any other is
given here with why it is neither. tests/codebase/test_metrics_declared.py
fails on one made bare, so a write's metric cannot be made as a plain
counter unseen (Ali, 2026-11-27, inventory-tng-8xs0):

    printed = at_once("nothing written", meter.create_counter("inventory.labels.printed"))
"""

from typing import Literal

from opentelemetry.metrics import Counter, Histogram

# Why a metric is measured at once, not once committed.
Why = Literal[
    # What a page or browser reports about itself, as a page's errors or
    # a Content-Security-Policy violation.
    "browser report",
    # A failure, which commits nothing.
    "failure",
    # What writes no row, as labels laid out, a batch sent again or mail.
    "nothing written",
    # Given to a WriteMetric or refusals() too, and added by hand only for
    # what neither measures, as a reminder shown or the scan page's own
    # refusals.
    "shared",
]


def at_once[I: Counter | Histogram](why: Why, instrument: I, /) -> I:
    """`instrument`, declared measured at once for `why`."""
    del why  # Read by people, and by `ty check` against its list.
    return instrument
