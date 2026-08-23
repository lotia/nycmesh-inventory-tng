"""Every function this application called, in the order it called them.

Ali's requirement: at DEBUG, a record should name every single function that
ran. docs/observability.md is what that gives a reader, which switches turn it
on, and why it is spans rather than lines. This is the machinery, and the parts
of it that are not visible from using it.

WHAT IS COVERED IS A LIST, not a rule -- `COVERED` below, and
`COVERED_WHEN_A_WORKBOOK_IS_READ` beneath it. The document says which two modules are
deliberately absent; what it does not say is that the sweep is what would break
on them. Wrapping a method in `models.py` reaches through the metaclass that
built the class, and wrapping a serializer's would put this on the path DRF
takes once per field per row.

The global switch is read ONCE, when the application starts. It is a property
of how the process was configured rather than of a request, and asking the
logging module per call would put a dictionary lookup on the path this file
exists to keep cheap.

WHAT IT COSTS, measured rather than asserted, on a call that does nothing:

    the call itself         28 ns
    wrapped, switched off  123 ns   (+95, a flag and a contextvar read)
    wrapped, switched on  3755 ns   (+3727, the tracer and the context attach)

The guard is deliberately before the tracer rather than inside it, and those
two figures are the reason: starting a span the SDK will not export costs
thirty times what refusing to start one does, and with no collector configured
it buys nothing at all. Ninety-five nanoseconds against a domain function is
noise; four microseconds against a hundred of them per request is not.
"""

import functools
import inspect
import logging
import sys
from collections.abc import Callable
from typing import Any

from opentelemetry import trace

from inventory_tng.debugging import debugging

# The modules whose callables are wrapped, stated rather than discovered. A
# module added here is a decision; a module added to the package is not.
COVERED = (
    "inventory.views",
    "inventory.labels",
    "inventory.permissions",
)

# The sheet rules, swept when a workbook is about to be read rather than at
# boot. Sweeping imports, and `inventory/sheet/` reaches openpyxl -- which
# every gunicorn worker would then pay for to serve requests that never see a
# spreadsheet. test_stage_sheet.py holds that line, and this stays on the
# right side of it.
#
# SWEPT FROM `_workbook.py`, and the name of that file is the point. Naming
# these "the importer's" and sweeping them where the importer meets the
# database left `profile_sheet` -- the one command whose entire job is running
# all eight of these -- producing no function spans at all, silently, because
# it reaches the rules without going through the importer. `_workbook.py` is
# what every command that reads an export goes through, and it already pays
# the import cost, so coverage is a property of reading a workbook rather than
# of which command happened to be written first.
COVERED_WHEN_A_WORKBOOK_IS_READ = (
    "inventory.sheet.batches",
    "inventory.sheet.corrections",
    "inventory.sheet.items",
    "inventory.sheet.jobs",
    "inventory.sheet.locations",
    "inventory.sheet.people",
    "inventory.sheet.returns",
    "inventory.sheet.workbook",
)

# Set once when the application starts. Module state because the alternative
# is asking the logging module on every call in the covered packages, and this
# is a property of how the process was configured rather than of a request.
_at_debug = False

# What has already been wrapped, so that a second sweep -- `ready()` runs once,
# but a test may call it -- does not wrap a wrapper.
MARK = "_inventory_traced"

# The instrumentation scope these spans arrive under, which is what tells them
# apart from the ones Django's and psycopg's instrumentations produce.
TRACER = "inventory.tracing"


def wanted() -> bool:
    """Whether this call should be recorded, asked once per wrapped call."""
    return _at_debug or debugging()


def traced(call: Callable[..., Any], name: str) -> Callable[..., Any]:
    """One callable, wrapped so that running it is a span.

    The name is passed in rather than read from the callable, because a bound
    method's `__qualname__` says which class it is on and a module-level
    function's does not -- and the caller doing the sweep knows both.
    """

    @functools.wraps(call)
    def recording(*arguments: Any, **named: Any) -> Any:
        if not wanted():
            return call(*arguments, **named)
        # Asked for per recorded call rather than held at module scope. A
        # tracer obtained before a provider is set is a proxy that caches the
        # first real one it resolves, so one held here would keep whichever
        # provider happened to exist when this module was imported -- which in
        # a deployment is none, and in a test suite is the previous test's.
        # Only reached when something is being recorded, where it is lost in
        # the microseconds the span itself costs.
        with trace.get_tracer(TRACER).start_as_current_span(name):
            return call(*arguments, **named)

    setattr(recording, MARK, True)
    return recording


def wrappable(held: Any, module: str) -> bool:
    """Whether this is one of the module's own plain functions.

    Three things it refuses, each for its own reason. Anything imported from
    somewhere else, because the module that defines it is where it should be
    wrapped and wrapping it twice would nest a span inside itself. Anything
    already wrapped. And anything that is not a plain function -- a class, a
    partial, a C builtin -- because what a decorator does to those is not one
    answer.
    """
    return (
        inspect.isfunction(held)
        and getattr(held, "__module__", None) == module
        and not getattr(held, MARK, False)
        and not held.__name__.startswith("__")
    )


def sweep(module: Any, replaced: dict[Any, Any] | None = None) -> int:
    """Wrap what one module defines, and say how many that was.

    Module-level functions, and the methods of classes the module defines.
    `staticmethod` and `classmethod` are rebuilt around the wrapped function
    rather than wrapped as descriptors, which is what stops a bound call
    arriving with its own class as an argument; a `property` is wrapped through
    its getter, so reading it is a span and setting it is left alone.

    `replaced` collects original-to-wrapper for `rebind`, which is the half
    that makes a wrapper reachable from anywhere but this module.
    """
    wrapped = 0
    for name, held in vars(module).items():
        if wrappable(held, module.__name__):
            wrapper = traced(held, f"{module.__name__}.{name}")
            setattr(module, name, wrapper)
            if replaced is not None:
                replaced[held] = wrapper
            wrapped += 1
        elif inspect.isclass(held) and getattr(held, "__module__", None) == module.__name__:
            wrapped += _sweep_class(held, module.__name__, replaced)
    return wrapped


def _sweep_class(owner: type, module: str, replaced: dict[Any, Any] | None = None) -> int:
    """The methods a class defines itself, and none it inherits."""
    wrapped = 0
    for name, held in list(vars(owner).items()):
        described = f"{module}.{owner.__name__}.{name}"
        if wrappable(held, module):
            wrapper = traced(held, described)
            setattr(owner, name, wrapper)
            if replaced is not None:
                replaced[held] = wrapper
            wrapped += 1
        elif isinstance(held, staticmethod | classmethod) and wrappable(held.__func__, module):
            setattr(owner, name, type(held)(traced(held.__func__, described)))
            wrapped += 1
        elif isinstance(held, property) and held.fget is not None and wrappable(held.fget, module):
            setattr(owner, name, held.getter(traced(held.fget, described)))
            wrapped += 1
    return wrapped


def rebind(replaced: dict[Any, Any]) -> int:
    """Point names bound before the sweep at the wrappers, and count them.

    THE HALF WITHOUT WHICH THE SWEEP IS DECORATIVE. `from inventory.labels
    import sheet` copies the function object into the importing module's
    namespace, so replacing the attribute on the module that DEFINES it leaves
    that name pointing at the original -- and every cross-module call in this
    application is written that way. `inventory.labels` and
    `inventory.permissions` were in `COVERED`, were swept, and produced no span
    at all, because the only callers of what they define are views holding
    copies taken at import time.

    It also removes an accident that was doing the covering by luck.
    `inventory/middleware.py` is imported at the first request, which is after
    `ready()`, so it picked the wrappers up off the module attribute and was
    traced; anything imported before `ready()` was not. Whether a module was
    covered depended on when it happened to be imported.

    Only this application's own modules, and only names bound to a function
    this sweep has just replaced -- identity, not name, so a module defining
    something of its own by the same name is untouched.
    """
    rebound = 0
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith("inventory"):
            continue
        for attribute, held in list(vars(module).items()):
            if not inspect.isfunction(held):
                continue
            wrapper = replaced.get(held)
            if wrapper is not None and wrapper is not held:
                setattr(module, attribute, wrapper)
                rebound += 1
    return rebound


def cover(*modules: str) -> int:
    """Wrap the named modules, importing each, and say how many that came to.

    Swept first and rebound afterwards, in that order and not interleaved: a
    module covered later in the list may hold a name from one covered earlier,
    and rebinding as each was swept would leave that one pointing at whatever
    existed at the time.
    """
    import importlib

    replaced: dict[Any, Any] = {}
    wrapped = sum(sweep(importlib.import_module(name), replaced) for name in modules)
    rebind(replaced)
    return wrapped


def start() -> int:
    """Settle the global switch and wrap what is covered at boot.

    Called from the app's `ready`, which is after Django has configured
    logging and before anything is served. Returns how many callables were
    wrapped, which is what a test asserts against so that a module falling out
    of `COVERED` is visible rather than silent.
    """
    global _at_debug
    _at_debug = logging.getLogger("inventory").isEnabledFor(logging.DEBUG)
    return cover(*COVERED)
