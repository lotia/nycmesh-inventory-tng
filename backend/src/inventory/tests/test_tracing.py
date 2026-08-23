"""Every function a request called, and what asking for that costs.

Two halves, and the second is the one that would rot quietly: that the covered
list is what `tracing` says it is, and that a class comes through a sweep with
its staticmethods, classmethods and properties still behaving as themselves --
which is the part a naive wrap gets wrong and which nothing else would notice.
"""

import logging
from typing import Any

import pytest

from inventory import tracing
from inventory_tng import debugging


@pytest.fixture(autouse=True)
def _switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module state, restored per test the way every other flag here is."""
    monkeypatch.setattr(tracing, "_at_debug", False)


def nested(recorded: Any) -> list[str]:
    return [span.name for span in recorded.get_finished_spans()]


# --------------------------------------------------------------------------
# What is covered, and what deliberately is not
# --------------------------------------------------------------------------


def test_the_models_and_the_serializers_are_left_alone() -> None:
    """Django builds a model with a metaclass and DRF calls a serializer once
    per field per row. Both are named in `tracing` as decisions rather than
    omissions, so this is the assertion that they stay decisions.
    """
    covered = {*tracing.COVERED, *tracing.COVERED_WHEN_A_WORKBOOK_IS_READ}

    assert "inventory.models" not in covered
    assert "inventory.serializers" not in covered


def test_the_importer_is_covered_but_not_at_boot() -> None:
    """Sweeping a module imports it, and `inventory/sheet/` reaches openpyxl.
    test_stage_sheet.py holds that line from the other side.
    """
    assert all(name.startswith("inventory.sheet.") for name in tracing.COVERED_WHEN_A_WORKBOOK_IS_READ)
    assert not any(name.startswith("inventory.sheet") for name in tracing.COVERED)


def test_and_every_command_that_reads_a_workbook_sweeps_them() -> None:
    """The assertion above says which modules; this says that anything reaching
    them has actually swept them, which is the half that was missing.

    `profile_sheet` exists to run all eight of these and produced no function
    span for any of them, because the sweep sat in the module where the
    importer meets the database and that command never goes there. Asserted
    through the import every such command makes rather than through one of
    them, so a fourth command inherits it.
    """
    import importlib

    from inventory.management.commands import _workbook

    assert _workbook is not None, "the import above is what performs the sweep"
    for name in tracing.COVERED_WHEN_A_WORKBOOK_IS_READ:
        swept = importlib.import_module(name)
        wrapped = [held for held in vars(swept).values() if callable(held) and getattr(held, tracing.MARK, False)]
        assert wrapped, f"{name} was not swept, so nothing in it can ever be a span"


def test_a_covered_module_really_did_get_wrapped() -> None:
    """`ready` has already run by the time a test does."""
    from inventory import views

    assert getattr(views.StockTransactionCreateView.post, tracing.MARK, False)


def test_a_name_another_module_imported_before_the_sweep_is_wrapped_too() -> None:
    """Wrapping the defining module's attribute is not the same as wrapping
    what the callers hold, and every caller here holds a copy.

    `views.py` opens `from inventory.labels import refusal_page, sheet` and
    `from inventory.permissions import ... recently_authenticated`, which binds
    the function objects into its own namespace at import time. Replacing
    `inventory.labels.sheet` afterwards left `LabelSheetView.get` calling the
    original, so two of the three modules in `COVERED` were swept and produced
    no span from any caller.

    `open_to_anybody` is the sharpest case: nothing inside its own module calls
    it, so before `rebind` there was no path on which it could ever be traced.
    """
    from inventory import permissions, views

    for held in (views.sheet, views.refusal_page, views.recently_authenticated, views.is_administrator):
        assert getattr(held, tracing.MARK, False), f"{held.__name__} in views is still the unwrapped original"
    assert getattr(permissions.open_to_anybody, tracing.MARK, False)


def test_and_sweeping_twice_does_not_wrap_a_wrapper() -> None:
    """`ready` runs once, but a test may call it, and a wrapper around a
    wrapper is a span inside itself for ever after.
    """
    from inventory import views

    assert tracing.sweep(views) == 0


# --------------------------------------------------------------------------
# What it records
# --------------------------------------------------------------------------


def test_a_call_is_a_span_named_for_what_ran(recorded: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_at_debug", True)

    def outer() -> int:
        return 41

    assert tracing.traced(outer, "inventory.probe.outer")() == 41
    assert nested(recorded) == ["inventory.probe.outer"]


def test_and_a_nested_call_appears_nested(recorded: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole reason this is spans rather than lines: a flat record cannot
    say that one call happened inside another.
    """
    monkeypatch.setattr(tracing, "_at_debug", True)

    def inner() -> int:
        return 1

    wrapped_inner = tracing.traced(inner, "inventory.probe.inner")

    def outer() -> int:
        return wrapped_inner() + 1

    wrapped_outer = tracing.traced(outer, "inventory.probe.outer")
    wrapped_outer()

    inner_span, outer_span = recorded.get_finished_spans()

    assert inner_span.name == "inventory.probe.inner"
    assert outer_span.name == "inventory.probe.outer"
    assert inner_span.parent.span_id == outer_span.context.span_id


def test_nothing_is_recorded_when_neither_switch_is_on(recorded: Any) -> None:
    wrapped = tracing.traced(lambda: 1, "inventory.probe.quiet")
    wrapped()

    assert nested(recorded) == []


def test_a_request_carrying_a_debug_token_turns_it_on_for_that_request(recorded: Any) -> None:
    """The second of the two switches `tracing` describes."""
    wrapped = tracing.traced(lambda: 1, "inventory.probe.asked")

    with debugging.asked(debugging.mint()):
        wrapped()

    assert nested(recorded) == ["inventory.probe.asked"]


# --------------------------------------------------------------------------
# What a class keeps when its methods are wrapped
# --------------------------------------------------------------------------


class Sample:
    """Stands in for the shapes a sweep meets in a covered module."""

    @staticmethod
    def counted() -> str:
        return "static"

    @classmethod
    def named(cls) -> str:
        return cls.__name__

    @property
    def held(self) -> str:
        return "property"

    def ordinary(self) -> str:
        return "method"


def test_a_swept_class_still_binds_its_methods_the_way_it_did() -> None:
    """A naive wrap turns a staticmethod into one that receives the class, and
    a property into a method nobody calls. Each is rebuilt as what it was.
    """
    tracing._sweep_class(Sample, __name__)
    sample = Sample()

    assert Sample.counted() == "static"
    assert Sample.named() == "Sample"
    assert sample.held == "property"
    assert sample.ordinary() == "method"


def test_and_every_one_of_them_is_recorded(recorded: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_at_debug", True)
    tracing._sweep_class(Sample, __name__)
    sample = Sample()

    Sample.counted()
    Sample.named()
    _ = sample.held
    sample.ordinary()

    assert len(nested(recorded)) == 4


# --------------------------------------------------------------------------
# The switch itself
# --------------------------------------------------------------------------


def test_the_global_switch_is_the_logger_level_rather_than_a_new_variable() -> None:
    """`DJANGO_LOG_LEVELS=inventory=DEBUG` already exists and already means
    "tell me more about this subsystem".
    """
    logging.getLogger("inventory").setLevel(logging.DEBUG)
    try:
        tracing.start()
        assert tracing.wanted() is True
    finally:
        logging.getLogger("inventory").setLevel(logging.NOTSET)

    tracing.start()

    assert tracing.wanted() is False
