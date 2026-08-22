"""A command a document tells you to type has to be one this repository has.

DEVELOPERS.md opens by promising that its commands work on a clean machine and
nothing held it to that. Half of the promise is checkable without running
anything at all: a `manage.py` subcommand, an `npm run` script, a file under
`scripts/` and a value of the deployment chart either exist or do not, and a
document naming one that does not is stale in the way a reader meets first --
they type it, it fails, and they have no way to tell whether the fault is
theirs. The other half, that the commands do what the document claims once they
run, is the `Setup instructions` job in `.github/workflows/ci.yml`.

Every failure below names the token and the line it was read from, because a
checker that says only "something is out of date" leaves the reader doing the
search by hand.

The corpus is the tracked Markdown, so a document added anywhere is read
without being listed here, and a scratch file in the working tree is not read
at all.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.core.management import get_commands

REPO_ROOT = Path(settings.REPO_ROOT)
DEPLOYMENT = REPO_ROOT / "docs" / "deployment.md"
VALUES = REPO_ROOT / "infra" / "helm" / "inventory-tng" / "values.yaml"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"

# The tracker's own README describes the tracker's installer. It is vendored
# rather than written here, and nothing in it is a promise this repository made.
VENDORED = (".beads/",)

# Anchored to one line each. The repository layout in DEVELOPERS.md draws
# `manage.py` on one line and the package beside it on the next, which a
# pattern spanning newlines reads as a subcommand called `inventory_tng`.
SUBCOMMAND = re.compile(r"manage\.py[ \t]+([a-z_][a-z0-9_]*)")
NPM_SCRIPT = re.compile(r"npm run[ \t]+([A-Za-z0-9:_-]+)")
SCRIPT_FILE = re.compile(r"\bscripts/([A-Za-z0-9._-]+)")

# A chart value is named either in backticks or as an argument to --set.
CHART_VALUE = re.compile(r"`([^`]+)`|--set[ \t]+'?([^\s'=]+)")


def documents() -> list[Path]:
    """Every tracked Markdown file that this repository is answerable for.

    Separated by NUL rather than by newline. Asked for a path holding a space
    or anything non-ASCII, git answers with the path wrapped in quotes and the
    awkward characters escaped, and splitting that on whitespace turns one
    document into fragments -- fragments that then fail to open, or worse open
    nothing and quietly shrink the corpus. `-z` is git's own answer to that:
    the raw bytes of each path, and no quoting at all.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    return [REPO_ROOT / path for path in listed if path and not path.startswith(VENDORED)]


def named(pattern: re.Pattern[str], paths: list[Path] | None = None) -> list[tuple[str, str]]:
    """Every match of `pattern`, each with the file and line it was read from."""
    found: list[tuple[str, str]] = []
    for document in paths if paths is not None else documents():
        for number, line in enumerate(document.read_text().split("\n"), start=1):
            for token in pattern.findall(line):
                found.append((token, f"{document.relative_to(REPO_ROOT)}:{number}"))
    return found


def chart_values() -> dict[str, Any]:
    """The chart's defaults, which are also the list of values it has."""
    parsed: dict[str, Any] = yaml.safe_load(VALUES.read_text())
    return parsed


def settable(token: str, top_level: set[str]) -> str | None:
    """The chart value `token` names, or None if it names something else.

    A document is full of dotted strings that are not chart values -- module
    paths, hostnames, file names. What tells them apart is the first segment:
    only the chart's own top-level keys count, so the list of what to look for
    comes from the chart rather than from a list here that could fall behind
    it. A quoted key (`ingress.annotations."cert-manager\\.io/..."`) is checked
    as far as the quote, which is the part the chart itself has to have.
    """
    for cut in ('"', "'", "="):
        token = token.split(cut)[0]
    token = token.rstrip(".,)")
    if "." not in token or token.split(".")[0] not in top_level:
        return None
    return token


def resolves(path: str, values: Any) -> bool:
    """Whether `path` names something in the chart's values."""
    node = values
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def complain(missing: list[str], what: str) -> None:
    """Fail naming every stale line, not only the first one found."""
    assert not missing, f"{what} that nothing in this repository has:\n" + "\n".join(missing)


def test_every_manage_py_subcommand_a_document_names_exists() -> None:
    available = get_commands()
    read = named(SUBCOMMAND)
    assert read, "no manage.py command was read out of any document"
    complain(
        [f"{where}: manage.py {token}" for token, where in read if token not in available],
        "manage.py subcommands",
    )


def test_every_npm_script_a_document_names_exists() -> None:
    available = set(json.loads(PACKAGE_JSON.read_text())["scripts"])
    read = named(NPM_SCRIPT)
    assert read, "no npm script was read out of any document"
    complain(
        [f"{where}: npm run {token}" for token, where in read if token not in available],
        "npm scripts",
    )


def test_every_scripts_file_a_document_names_exists() -> None:
    read = named(SCRIPT_FILE)
    assert read, "no scripts/ file was read out of any document"
    complain(
        [f"{where}: scripts/{token}" for token, where in read if not (REPO_ROOT / "scripts" / token).is_file()],
        "files under scripts/",
    )


def test_every_chart_value_the_deployment_document_names_exists() -> None:
    """The deployment document is the only one that names them, because the
    chart's knobs are documented in exactly one place.
    """
    values = chart_values()
    top_level = set(values)
    read = [
        (value, where)
        for match, where in named(CHART_VALUE, [DEPLOYMENT])
        for value in [settable(match[0] or match[1], top_level)]
        if value is not None
    ]
    assert read, "no chart value was read out of the deployment document"
    complain(
        [f"{where}: {value}" for value, where in read if not resolves(value, values)],
        "chart values",
    )


def test_the_documents_being_read_are_the_committed_ones() -> None:
    """An empty corpus is the failure mode of a checker like this one: it
    reports the all-clear having opened nothing. The `assert read` in each test
    above guards the same thing from the other end.
    """
    paths = documents()
    assert (REPO_ROOT / "DEVELOPERS.md") in paths
    assert (REPO_ROOT / "guides" / "volunteer.md") in paths
    assert all(path.is_file() for path in paths)
    assert not [path for path in paths if str(path.relative_to(REPO_ROOT)).startswith(VENDORED)]
