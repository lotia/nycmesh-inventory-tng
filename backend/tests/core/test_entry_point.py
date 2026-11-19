"""The two entry points that dispatch through entry_point.py: the
operator's, python -m inventory_tng.manage (manage.py), and the
developer's, python -m devtools (devtools/__main__.py; docs/decisions/
0023). Each test runs over both, as they cannot drift."""

import subprocess
import sys
from types import ModuleType

import pytest

from devtools import __main__ as devtools
from inventory_tng import manage
from inventory_tng.core.local_env import CLONE

ENTRY_POINTS = {"manage": manage, "devtools": devtools}
each_entry_point = pytest.mark.parametrize(
    "entry", ENTRY_POINTS.values(), ids=ENTRY_POINTS.keys()
)
EVERY_COMMAND = [
    pytest.param(entry, name, id=f"{key}-{name}")
    for key, entry in ENTRY_POINTS.items()
    for name in entry.COMMANDS
]
# The tasks run the developer's from backend/; the operator's runs anywhere.
BACKEND = CLONE / "backend"


@pytest.mark.parametrize(("entry", "name"), EVERY_COMMAND)
def test_every_listed_command_imports_and_shows_its_help(
    entry: ModuleType, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as done:
        entry.main([name, "--help"])
    assert done.value.code == 0
    assert capsys.readouterr().out.startswith(f"usage: {entry.PROG} {name} ")


@each_entry_point
def test_with_no_command_it_lists_every_one(
    entry: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert entry.main([]) == 0
    out = capsys.readouterr().out
    for name, (_, line) in entry.COMMANDS.items():
        assert f"  {name}" in out
        assert line in out


@each_entry_point
def test_help_lists_every_one(
    entry: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as done:
        entry.main(["--help"])
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert all(f"  {name}" in out for name in entry.COMMANDS)


@each_entry_point
def test_python_m_lists_every_one(entry: ModuleType) -> None:
    """For real, as DEPLOYMENT.md and the tasks run them: PROG is the
    command line itself."""
    _python, *arguments = entry.PROG.split()
    shown = subprocess.run(
        [sys.executable, *arguments],
        capture_output=True,
        text=True,
        check=True,
        cwd=BACKEND,
    )
    assert all(f"  {name}" in shown.stdout for name in entry.COMMANDS)


@each_entry_point
def test_an_unknown_command_is_refused(
    entry: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as done:
        entry.main(["lockdown"])
    assert done.value.code == 2
    assert "invalid choice: 'lockdown'" in capsys.readouterr().err


@each_entry_point
def test_arguments_without_a_command_are_refused(
    entry: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not the list of commands and success, as if they had run."""
    with pytest.raises(SystemExit) as done:
        entry.main(["--", "admins", "lockdown", "--expect", "3"])
    assert done.value.code == 2
    assert "required: command" in capsys.readouterr().err


# For each entry point in turn, in one fresh interpreter: its package's
# modules once it is imported, then which of its commands' modules, and of
# the others named, one command's help imported.
IMPORTED = """
import contextlib, importlib, io, sys
for module, package, name, others in zip(*[iter(sys.argv[1:])] * 4):
    entry = importlib.import_module(module)
    print(*sorted(m for m in sys.modules if m.startswith(package)))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.suppress(SystemExit):
        entry.main([name, "--help"])
    watched = [m for m, _ in entry.COMMANDS.values()] + others.split()
    print(*(m for m in watched if m in sys.modules))
"""


def test_each_imports_only_the_command_chosen() -> None:
    """In a fresh interpreter, as the other tests have imported them all.
    The developer's database command imports the seed only to seed."""
    shown = subprocess.run(
        [
            sys.executable,
            "-c",
            IMPORTED,
            *("inventory_tng.manage", "inventory_tng.", "scan-settings", ""),
            *("devtools.__main__", "devtools.", "database", "devtools.seed"),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=BACKEND,
    )
    assert shown.stdout.splitlines() == [
        "inventory_tng.core inventory_tng.core.entry_point inventory_tng.manage",
        "inventory_tng.site_settings.scan_settings_command",
        "devtools.__main__",
        "devtools.database",
    ]
