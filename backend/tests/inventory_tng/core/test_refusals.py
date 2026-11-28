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
def test_a_value_not_in_its_list_is_logged_as_an_error_not_counted(
    reason: str | None,
) -> None:
    """A bug, but the refusal stands, logged as ever: raised, the request
    would be answered 500 instead. The value is never logged again."""
    refused, counter = refusals(reason=("rate_limited",))
    with capture_logs() as logs:
        refused("report refused", reason=reason)
    assert counter.added == []
    assert logs == [
        {"event": "report refused", "reason": reason, "log_level": "info"},
        {
            "event": "refusal not counted",
            "refusal": "report refused",
            "attribute": "reason",
            "log_level": "error",
        },
    ]


def test_an_attribute_left_out_is_not_counted() -> None:
    refused, counter = refusals(reason=("rate_limited",), page=("scan",))
    with capture_logs() as logs:
        refused("report refused", reason="rate_limited")
    assert counter.added == []
    assert logs[-1]["attribute"] == "page"
