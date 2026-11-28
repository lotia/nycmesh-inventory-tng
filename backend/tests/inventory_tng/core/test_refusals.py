from typing import cast

import pytest
import structlog
from opentelemetry.metrics import Counter
from structlog.testing import capture_logs

from inventory_tng.core.refusals import Refusals

log = structlog.get_logger(__name__)


class Added:
    """A counter that keeps what is added to it."""

    def __init__(self) -> None:
        self.added: list[tuple[int, dict[str, str]]] = []

    def add(self, amount: int, attributes: dict[str, str]) -> None:
        self.added.append((amount, attributes))


def refusals(**attributes: tuple[str, ...]) -> tuple[Refusals, Added]:
    counter = Added()
    return Refusals(log, cast(Counter, counter), **attributes), counter


def test_logs_and_counts_a_refusal_by_its_attributes_only() -> None:
    refused, counter = refusals(reason=("rate_limited", "too_large"))
    with capture_logs() as logs:
        refused("report refused", reason="too_large", item_id=4)
        refused("report refused", level="warning", count=3, reason="rate_limited")
    assert counter.added == [
        (1, {"reason": "too_large"}),
        (3, {"reason": "rate_limited"}),
    ]
    assert logs == [
        {
            "event": "report refused",
            "reason": "too_large",
            "item_id": 4,
            "log_level": "info",
        },
        {"event": "report refused", "reason": "rate_limited", "log_level": "warning"},
    ]


@pytest.mark.parametrize("reason", ["free text", None])
def test_a_value_not_in_its_list_is_neither_logged_nor_counted(
    reason: str | None,
) -> None:
    refused, counter = refusals(reason=("rate_limited",))
    with capture_logs() as logs, pytest.raises(ValueError, match="reason="):
        refused("report refused", reason=reason)
    assert (counter.added, logs) == ([], [])


def test_every_attribute_is_given() -> None:
    refused, counter = refusals(reason=("rate_limited",), page=("scan",))
    with pytest.raises(KeyError, match="page"):
        refused("report refused", reason="rate_limited")
    assert counter.added == []
