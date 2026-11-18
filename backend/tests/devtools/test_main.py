"""The developer's one entry point, python -m devtools (devtools/__main__.py;
docs/decisions/0023). It dispatches as manage does
(inventory_tng.core.entry_point): test_manage.py tests what the two share."""

import runpy
import subprocess
import sys

import pytest

from devtools.__main__ import COMMANDS, main
from inventory_tng.core.local_env import CLONE


@pytest.mark.parametrize("name", COMMANDS)
def test_every_listed_command_imports_and_shows_its_help(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as done:
        main([name, "--help"])
    assert done.value.code == 0
    assert capsys.readouterr().out.startswith(f"usage: python -m devtools {name} ")


def test_with_no_command_it_lists_every_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["devtools"])
    # A fresh copy, as python -m runs: runpy warns when the module is
    # already imported, and whether it is depends on what ran before.
    monkeypatch.delitem(sys.modules, "devtools.__main__", raising=False)
    with pytest.raises(SystemExit) as done:
        runpy.run_module("devtools", run_name="__main__")
    assert done.value.code == 0
    out = capsys.readouterr().out
    for name, (_, line) in COMMANDS.items():
        assert f"  {name}" in out
        assert line in out


# What a fresh interpreter has imported of the tools once the entry point
# is, and again after one command's help: not the seed, which database
# imports only to seed.
IMPORTED = """
import contextlib, io, sys
from devtools.__main__ import main
def ours():
    return sorted(m for m in sys.modules if m.startswith("devtools."))
print(*ours())
with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):
    main(["database", "--help"])
print(*ours())
"""


def test_it_imports_only_the_command_chosen() -> None:
    """In a fresh interpreter, from backend/ as the tasks run it."""
    shown = subprocess.run(
        [sys.executable, "-c", IMPORTED],
        capture_output=True,
        text=True,
        check=True,
        cwd=CLONE / "backend",
    )
    assert shown.stdout.splitlines() == [
        "devtools.__main__",
        "devtools.__main__ devtools.database",
    ]
