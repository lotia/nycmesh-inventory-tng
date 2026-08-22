"""A command a document tells you to type has to be one this repository has, and the other way about.

DEVELOPERS.md opens by promising that its commands work on a clean machine and
nothing held it to that. Half of the promise is checkable without running
anything at all: a `manage.py` subcommand, an `npm run` script, a file under
`scripts/` and a value of the deployment chart either exist or do not, and a
document naming one that does not is stale in the way a reader meets first --
they type it, it fails, and they have no way to tell whether the fault is
theirs. The other half, that the commands do what the document claims once they
run, is the `Setup instructions` job in `.github/workflows/ci.yml`.

That direction is the cheap one, and on its own it is not the direction that
rots. A subcommand or a script *added* and never written up leaves every check
green and every document quietly incomplete, so the commands this repository
adds are also held against the documents -- with an allow-list, because a
couple of them genuinely want no write-up and saying which is a decision worth
recording rather than a gap worth living with.

One more thing is held together from here, and it is not a command: the line
that turns mise on. CI has to activate it the way the guide says to, and the
line was retyped rather than shared, so the two are compared.

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

from inventory.tests.charts import CHART, manifests

REPO_ROOT = Path(settings.REPO_ROOT)
DEPLOYMENT = REPO_ROOT / "docs" / "deployment.md"
VALUES = CHART / "values.yaml"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEVELOPERS = REPO_ROOT / "DEVELOPERS.md"
ALLOW = Path(__file__).with_name("undocumented.allow")

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

# Whatever a shell is told to evaluate to bring mise up, wherever it is
# written. See `test_ci_activates_mise_with_the_line_the_guide_prints`.
ACTIVATION = re.compile(r'eval "\$\([^"]*mise activate [^"]*\)"')

# A Kubernetes object as `kubectl` is told to address it: a kind, a slash, and
# the name the chart rendered. See
# `test_every_resource_the_deployment_document_addresses_is_one_the_chart_renders`.
ADDRESSED = re.compile(r"\b(?:deploy|deployment|job|svc|service|ingress)/([a-z0-9-]+)")


def documents() -> list[Path]:
    """Every tracked Markdown file that this repository is answerable for, once each.

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
    found = [REPO_ROOT / path for path in listed if path and not path.startswith(VENDORED)]
    # A symlink is skipped for the reason `scripts/check-docs.py` gives about
    # the same corpus. Three of these paths point at a fourth, so one stale
    # command in that file arrives as four failures naming four documents, of
    # which three cannot be edited.
    return [path for path in found if not path.is_symlink()]


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


def ours() -> list[str]:
    """Every command this repository adds, written the way a document would write it.

    Django's own subcommands are not ours to document and third-party ones are
    their package's, so only the `inventory` app's are asked about. The
    frontend's scripts are all ours: `package.json` holds nothing but them.
    """
    subcommands = [f"manage.py {name}" for name, app in get_commands().items() if app == "inventory"]
    scripts = [f"npm run {name}" for name in json.loads(PACKAGE_JSON.read_text())["scripts"]]
    return sorted(subcommands + scripts)


def written_as(command: str) -> re.Pattern[str]:
    """How a document would have to write `command` for it to count as named.

    `run` is optional because `npm test` and `npm run test` are the same
    script and the shorter one is what DEVELOPERS.md prints. The lookahead is
    what keeps `npm run lint:fix` from answering for `npm run lint`: a name
    that is the start of a longer one is not that longer one.
    """
    if command.startswith("npm run "):
        return re.compile(rf"npm[ \t]+(run[ \t]+)?{re.escape(command.removeprefix('npm run '))}(?![\w:-])")
    return re.compile(rf"manage\.py[ \t]+{re.escape(command.removeprefix('manage.py '))}(?![\w:-])")


def excused() -> list[str]:
    """The commands `undocumented.allow` says are right to leave unwritten."""
    return [line.strip() for line in ALLOW.read_text().split("\n") if line.strip() and not line.startswith("#")]


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


def test_every_command_this_repository_adds_is_named_by_some_document() -> None:
    """The direction the four checks above cannot look, and the one that rots.

    Each of them asks whether what a document names exists. None can ask
    whether what exists is named, so a subcommand or a script added and never
    written up costs nothing and is invisible -- which is how a set of
    documents stops describing its repository one addition at a time.
    """
    corpus = "\n".join(document.read_text() for document in documents())
    allowed = excused()
    complain(
        [command for command in ours() if command not in allowed and not written_as(command).search(corpus)],
        f"commands this repository has that no document names -- write each up, "
        f"or say in {ALLOW.name} why it wants no write-up; that list",
    )


def test_nothing_is_excused_from_that_which_needs_excusing() -> None:
    """An allow-list outliving what it allowed is the failure mode of one.

    Two ways it can: the command goes away, or somebody writes it up after all.
    Either leaves a line saying a decision was taken about something nobody is
    deciding about any more.
    """
    corpus = "\n".join(document.read_text() for document in documents())
    having = ours()
    stale = [
        f"{command}: {'no longer exists' if command not in having else 'is documented after all'}"
        for command in excused()
        if command not in having or written_as(command).search(corpus)
    ]
    assert not stale, f"{ALLOW.name} excuses what needs no excusing:\n" + "\n".join(stale)


def test_ci_activates_mise_with_the_line_the_guide_prints() -> None:
    """The `Setup instructions` job's whole claim rests on this one line.

    It is the job's own comment that says a run reaching for `mise exec --`
    would be a green tick over a command the guide does not print. Activation
    is the one setup step with no shared artifact behind it, so it was retyped
    -- and a line retyped is a line that can be edited in one place.
    """
    activations = sorted(set(ACTIVATION.findall(WORKFLOW.read_text())))
    assert activations, "no mise activation line was read out of the workflow"
    guide = DEVELOPERS.read_text()
    adrift = [line for line in activations if line not in guide]
    assert not adrift, "CI activates mise with a line DEVELOPERS.md does not print:\n" + "\n".join(adrift)


def rendered() -> set[str]:
    """Every object name the chart makes, for the release the document installs.

    Asked of helm rather than worked out from `_helpers.tpl`, because a rule
    reimplemented here is a second answer to the question and this test exists
    because the first answer drifted. Rendered by `charts.py`, for the same
    reason one step out: the release and the flags are one answer too.
    """
    return {document["metadata"]["name"] for document in manifests() if document.get("metadata", {}).get("name")}


def test_every_resource_the_deployment_document_addresses_is_one_the_chart_renders() -> None:
    """The gap that let four `kubectl` lines name a Deployment nobody had.

    The chart's names are not values, so the check above cannot see them, and
    twice now this document has addressed a resource the chart does not make --
    once before `inventory-tng-2cq` and once after it, in opposite directions.
    The names are read back off a real render so neither correction can be the
    last one that was right.
    """
    made = rendered()
    complain(
        [
            f"{where}: {kind_and_name}"
            for kind_and_name, where in named(ADDRESSED, [DEPLOYMENT])
            if kind_and_name not in made
        ],
        "Kubernetes objects the deployment document addresses",
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
    # AGENTS.md is read; the three names that are links to it are not, so one
    # command out of date in it is one failure. Asserted on a real path rather
    # than by restating the filter that produced this list.
    assert (REPO_ROOT / "AGENTS.md") in paths
    assert (REPO_ROOT / "CLAUDE.md") not in paths
