from typing import Any, Literal, Unpack, cast

import pytest
import structlog
from opentelemetry.metrics import Counter
from structlog.testing import capture_logs

from inventory_tng.core.refusals import UNLISTED, Fields, refusals

log = structlog.get_logger(__name__)


class Added:
    """A counter that keeps what is added to it."""

    def __init__(self) -> None:
        self.added: list[tuple[int, dict[str, str]]] = []

    def add(self, amount: int, attributes: dict[str, str]) -> None:
        self.added.append((amount, attributes))


counter = Added()
counted = cast(Counter, counter)
Reason = Literal["rate_limited", "too_large"]
type Other = Literal["other"]


@refusals(log, counted, page=("scan", "register"))
def refused(
    event: str, *, reason: Reason, page: str, **fields: Unpack[Fields]
) -> None: ...


@pytest.fixture(autouse=True)
def nothing_added() -> None:
    counter.added.clear()


def test_logs_and_counts_a_refusal_by_its_attributes_only() -> None:
    with capture_logs() as logs:
        refused("report refused", reason="too_large", page="scan", item_id=4)
        refused(
            "report refused",
            level="warning",
            count=3,
            reason="rate_limited",
            page="register",
        )
    assert counter.added == [
        (1, {"reason": "too_large", "page": "scan"}),
        (3, {"reason": "rate_limited", "page": "register"}),
    ]
    assert logs == [
        {
            "event": "report refused",
            "reason": "too_large",
            "page": "scan",
            "item_id": 4,
            "log_level": "info",
        },
        {
            "event": "report refused",
            "reason": "rate_limited",
            "page": "register",
            "log_level": "warning",
        },
    ]


@pytest.mark.parametrize("page", ["free text", None])
def test_a_value_not_in_its_list_is_counted_as_unlisted(page: str | None) -> None:
    """A bug, but the refusal stands, logged as ever, and counted: raised,
    the request would be answered 500 instead. The value is never logged
    again (Ali, 2026-11-27, inventory-tng-vj5h)."""
    with capture_logs() as logs:
        refused("report refused", reason="too_large", page=cast(Any, page))
    assert counter.added == [(1, {"reason": "too_large", "page": UNLISTED})]
    assert logs[1:] == [
        {
            "event": "refusal counted as unlisted",
            "refusal": "report refused",
            "attribute": "page",
            "log_level": "error",
        },
    ]


def test_an_attribute_left_out_is_counted_as_unlisted() -> None:
    fields: dict[str, Any] = {"reason": "rate_limited"}
    with capture_logs() as logs:
        refused("report refused", **fields)
    assert counter.added == [(1, {"reason": "rate_limited", "page": UNLISTED})]
    assert logs[-1]["attribute"] == "page"


def test_a_kind_reads_its_values_from_its_literals() -> None:
    """A Literal, an alias of one, or a union of them; a kind of no
    attributes counts by none."""

    @refusals(log, counted)
    def by_union(
        event: str, *, reason: Reason | Other, **fields: Unpack[Fields]
    ) -> None: ...

    @refusals(log, counted)
    def by_none(event: str, **fields: Unpack[Fields]) -> None: ...

    by_union("refused", reason="other")
    by_none("refused")
    assert counter.added == [(1, {"reason": "other"}), (1, {})]


def test_an_attribute_of_no_fixed_list_is_refused_at_import() -> None:
    def free(event: str, *, reason: str, **fields: Unpack[Fields]) -> None: ...

    def partly(
        event: str, *, reason: Reason | str, **fields: Unpack[Fields]
    ) -> None: ...

    for shape in (free, partly):
        with pytest.raises(TypeError, match="reason is no Literal"):
            refusals(log, counted)(shape)
    with pytest.raises(TypeError, match="no attribute page"):
        refusals(log, counted, reason=("a",), page=("b",))(free)


def test_a_list_naming_unlisted_is_refused_at_import() -> None:
    """A value "unlisted" would count alike with a bug's."""

    def listing(
        event: str, *, reason: Literal["unlisted"], **fields: Unpack[Fields]
    ) -> None: ...

    def by_page(event: str, *, page: str, **fields: Unpack[Fields]) -> None: ...

    with pytest.raises(TypeError, match="reason lists unlisted"):
        refusals(log, counted)(listing)
    with pytest.raises(TypeError, match="page lists unlisted"):
        refusals(log, counted, page=(UNLISTED,))(by_page)
