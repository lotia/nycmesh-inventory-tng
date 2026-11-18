"""The operator's one entry point, python -m inventory_tng.manage
(manage.py; docs/decisions/0023)."""

import runpy
import subprocess
import sys

import pytest

from inventory_tng import manage


@pytest.mark.parametrize("name", manage.COMMANDS)
def test_every_listed_command_imports_and_shows_its_help(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as done:
        manage.main([name, "--help"])
    assert done.value.code == 0
    assert capsys.readouterr().out.startswith(
        f"usage: python -m inventory_tng.manage {name} [-h]"
    )


@pytest.mark.parametrize("argv", [["manage"], ["manage", "--help"]])
def test_with_no_command_it_lists_every_one(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """As DEPLOYMENT.md runs it."""
    monkeypatch.setattr(sys, "argv", argv)
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as done,
    ):
        runpy.run_module("inventory_tng.manage", run_name="__main__")
    assert done.value.code == 0
    out = capsys.readouterr().out
    for name, (_, line) in manage.COMMANDS.items():
        assert f"  {name}" in out
        assert line in out


def test_an_unknown_command_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as done:
        manage.main(["lockdown"])
    assert done.value.code == 2
    assert "invalid choice: 'lockdown'" in capsys.readouterr().err


def test_arguments_without_a_command_are_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Not the list of commands and success, as if they had run."""
    with pytest.raises(SystemExit) as done:
        manage.main(["--", "admins", "lockdown", "--expect", "3"])
    assert done.value.code == 2
    assert "required: command" in capsys.readouterr().err


# What a fresh interpreter has imported of the app, once manage is
# imported and again after one command's help.
IMPORTED = """
import contextlib, io, sys
def ours():
    return sorted(m for m in sys.modules if m.startswith("inventory_tng."))
from inventory_tng import manage
print(*ours())
with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):
    manage.main(["scan-settings", "--help"])
print(*(m for m, _ in manage.COMMANDS.values() if m in sys.modules))
"""


def test_it_imports_only_the_command_chosen() -> None:
    """In a fresh interpreter, as the other tests have imported them all."""
    shown = subprocess.run(
        [sys.executable, "-c", IMPORTED], capture_output=True, text=True, check=True
    )
    assert shown.stdout.splitlines() == [
        "inventory_tng.core inventory_tng.core.entry_point inventory_tng.manage",
        "inventory_tng.site_settings.command",
    ]
