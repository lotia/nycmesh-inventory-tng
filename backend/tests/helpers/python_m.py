"""Running a module as python -m does, inside a test."""

import runpy
import sys

import pytest


def run_as_python_m(monkeypatch: pytest.MonkeyPatch, module: str) -> object:
    """Run a fresh copy of `module` as __main__ and return its exit code.

    runpy warns if the module is already imported, here or by an earlier
    test, so it is dropped from sys.modules first and put back after."""
    monkeypatch.delitem(sys.modules, module, raising=False)
    with pytest.raises(SystemExit) as done:
        runpy.run_module(module, run_name="__main__")
    return done.value.code
