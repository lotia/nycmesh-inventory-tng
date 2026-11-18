"""mise run test-report (devtools/suite_report.py), on suites' outputs made up
here: each command it runs is answered by a fake that writes what the real
tool would, in a clone of its own."""

import json
import re
import subprocess
from pathlib import Path
from typing import IO

import pytest
from coverage import CoverageData

from devtools import suite_report
from devtools.__main__ import main as run_devtools
from inventory_tng.core import local_env

JUNIT = """<testsuites><testsuite>
<testcase classname="tests.test_a" name="test_one" time="1.5"/>
<testcase classname="tests.test_a" name="test_two[x]" time="0.25"/>
<testcase classname="tests.sub.test_b" name="test_three" time="3"/>
<testcase classname="tests.sub.test_b" name="test_four" time="0.5"/>
<testcase classname="tests.sub.test_b" name="test_skipped" time="0">
<skipped/></testcase>
</testsuite></testsuites>"""
# Each backend test's arcs, by its context; the first test's split between
# its setup and its call, a test skipped after its setup, and an arc no test
# ran but the import.
CONTEXTS = {
    "tests/test_a.py::test_one|setup": {(1, 2)},
    "tests/test_a.py::test_one|run": {(2, 3)},
    "tests/test_a.py::test_two[x]|run": {(1, 2), (2, 3)},
    "tests/sub/test_b.py::test_three|run": {(1, 2), (5, 6)},
    "tests/sub/test_b.py::test_skipped|setup": {(7, 8)},
    "": {(9, 10)},
}
# The frontend's tests: each one's seconds, and the statements and ways of
# the one branch it covers alone. Two tests share the second name.
VITEST = {
    "sums > adds (1+1)": (0.012, {"0"}, {0}),
    "sums > adds again": (0.003, {"0"}, {0}),
    "subtracts": (0.002, {"0", "1"}, set()),
}
TWICE = "sums > adds again"
# A file's suite, and one of its describes; two tests share a title in two
# describes, and one was skipped.
PLAYWRIGHT = {
    "suites": [
        {
            "title": "sign-in.setup.ts",
            "specs": [
                {
                    "title": "signs in",
                    "tests": [
                        {
                            "projectName": "sign-in",
                            "status": "expected",
                            "results": [{"duration": 900}],
                        }
                    ],
                }
            ],
        },
        {
            "title": "menu.spec.ts",
            "specs": [
                {
                    "title": "opens",
                    "tests": [
                        {
                            "projectName": "chromium",
                            "status": "flaky",
                            "results": [{"duration": 1000}, {"duration": 500}],
                        },
                        {"projectName": "webkit", "status": "skipped", "results": []},
                    ],
                }
            ],
            "suites": [
                {
                    "title": "dark",
                    "specs": [
                        {
                            "title": "opens",
                            "tests": [
                                {
                                    "projectName": "chromium",
                                    "status": "expected",
                                    "results": [{"duration": 250}],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    ],
}


def contexts_data(path: Path) -> None:
    data = CoverageData(basename=str(path))
    for context, arcs in CONTEXTS.items():
        data.set_context(context)
        data.add_arcs({"/src/inventory_tng/a.py": arcs})
    data.write()


def vitest_report(frontend: Path, names: list[str], output: Path) -> None:
    """Vitest's JSON report of the tests named, their coverage together."""
    file = str(frontend / "src" / "a.test.ts")
    # How many of them ran each statement and each way, as Vitest counts.
    together = {
        "s": {s: sum(s in VITEST[n][1] for n in names) for s in ("0", "1")},
        "b": {"0": [sum(w in VITEST[n][2] for n in names) for w in (0, 1)]},
    }
    results = [
        {
            "ancestorTitles": n.split(" > ")[:-1],
            "title": n.split(" > ")[-1],
            "status": "passed",
            "duration": VITEST[n][0] * 1000,
        }
        for n in [*names, *[TWICE] * (TWICE in names)]
    ]
    skipped = {"ancestorTitles": [], "title": "skips", "status": "skipped"}
    output.write_text(
        json.dumps(
            {
                "numPassedTests": len(results),
                "testResults": [
                    {"name": file, "assertionResults": [*results, skipped]}
                ],
                "coverageMap": {file.replace(".test", ""): together},
            }
        )
    )


def fake_run(
    command: list[str], *, cwd: Path, env: dict[str, str], stdout: IO[str], **_: object
) -> subprocess.CompletedProcess[str]:
    """What each command run writes, as the real tools would."""
    stdout.write(f"ran in {cwd.name}\n")
    options = dict(a.split("=", 1) for a in command if "=" in a)
    if "--junitxml" in options:
        Path(options["--junitxml"]).write_text(JUNIT)
        totals = {"totals": {"percent_covered": 99.4211}}
        Path(options["--cov-report"].removeprefix("json:")).write_text(
            json.dumps(totals)
        )
    if "--cov-context" in options:
        contexts_data(Path(env["COVERAGE_FILE"]))
    if "--outputFile" in options:
        directory = Path(options["--coverage.reportsDirectory"])
        directory.mkdir(exist_ok=True)
        summary = {"total": {"lines": {"pct": 100}, "branches": {"pct": 99.22}}}
        (directory / "coverage-summary.json").write_text(json.dumps(summary))
        pattern = options.get("--testNamePattern", "")
        names = [n for n in VITEST if re.search(pattern, n)]
        vitest_report(cwd, names, Path(options["--outputFile"]))
    if "PLAYWRIGHT_JSON_OUTPUT_FILE" in env:
        Path(env["PLAYWRIGHT_JSON_OUTPUT_FILE"]).write_text(json.dumps(PLAYWRIGHT))
    return subprocess.CompletedProcess(command, 0)


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone of its own, with a test file on each side, every command
    answered by fake_run."""
    for name, text in (
        ("backend/tests/sub/test_b.py", "a\nb\nc\n"),
        ("frontend/src/a.test.ts", "a\n"),
        ("frontend/browser-tests/menu.spec.ts", "a\nb\n"),
    ):
        (tmp_path / name).parent.mkdir(parents=True)
        (tmp_path / name).write_text(text)
    monkeypatch.setattr(local_env, "CLONE", tmp_path)
    monkeypatch.setattr(suite_report, "CLONE", tmp_path)
    monkeypatch.setattr(suite_report, "OUT", tmp_path / ".local" / "test-report")
    monkeypatch.setattr(suite_report, "BACKEND", tmp_path / "backend")
    monkeypatch.setattr(suite_report, "FRONTEND", tmp_path / "frontend")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return tmp_path


def test_what_each_test_alone_covers_and_tests_covering_the_same() -> None:
    covers, own, same = suite_report.own_and_same(
        {
            "a": {1, 2},
            "b": {1, 2},
            "c": {2, 3},
            "d": {4},
            "e": {4},
            "f": {4},
            "nothing": set(),
        }
    )
    assert covers == {"a": 2, "b": 2, "c": 2, "d": 1, "e": 1, "f": 1, "nothing": 0}
    assert own == {"a": 0, "b": 0, "c": 1, "d": 0, "e": 0, "f": 0, "nothing": 0}
    # The largest group first; a test covering nothing is in none.
    assert same == {"d": 1, "e": 1, "f": 1, "a": 2, "b": 2}


def test_backend_tests_by_their_node_ids_from_coverage_contexts(
    tmp_path: Path,
) -> None:
    (tmp_path / "junit.xml").write_text(JUNIT)
    assert suite_report.backend_tests(tmp_path / "junit.xml") == {
        "tests/test_a.py::test_one": 1.5,
        "tests/test_a.py::test_two[x]": 0.25,
        "tests/sub/test_b.py::test_three": 3.0,
        "tests/sub/test_b.py::test_four": 0.5,
    }
    contexts_data(tmp_path / "data")
    covers, own, same = suite_report.backend_contexts(tmp_path / "data")
    # A test's setup counts as its own; the import, as no test's.
    one, two, three = "tests/test_a.py::test_one", "tests/test_a.py::test_two[x]", "tests/sub/test_b.py::test_three"  # fmt: skip
    skipped = "tests/sub/test_b.py::test_skipped"
    assert covers == {one: 2, two: 2, three: 2, skipped: 1}
    assert own == {one: 0, two: 0, three: 1, skipped: 1}
    assert same == {one: 1, two: 1}


def test_a_failed_run_stops_the_report_naming_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(suite_report, "OUT", tmp_path)
    with pytest.raises(SystemExit, match=str(tmp_path / "failed.log")):
        suite_report.run(["false"], tmp_path, "failed.log")
    assert suite_report.run(["true"], tmp_path, "passed.log") >= 0


def test_backend_tests_matching_no_coverage_context_stop_the_report(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(suite_report, "backend_tests", lambda _: {"other": 1.0})
    suite_report.OUT.mkdir(parents=True)
    with pytest.raises(SystemExit, match="matches a coverage context"):
        suite_report.measure_backend()


def test_a_frontend_test_not_found_alone_stops_the_report(clone: Path) -> None:
    file = str(clone / "frontend" / "src" / "a.test.ts")
    (suite_report.OUT / "frontend-alone").mkdir(parents=True)
    with pytest.raises(SystemExit, match="Found no test named 'adds' in "):
        suite_report.alone(1, file, "adds")


def test_the_report_and_each_suites_tests(clone: Path) -> None:
    assert run_devtools(["test-report"]) == 0
    out = clone / ".local" / "test-report"
    report = (out / "report.md").read_text()
    assert "| Backend | 4 | 0.0 s | 5 s | 99.42% of lines and branches |" in report
    assert (
        "| Frontend | 4 | 0.0 s | 0 s | 100% of lines, 99.22% of branches |" in report
    )
    assert "| Browser | 3 | 0.0 s | 3 s | not measured |" in report
    assert "| `tests/sub/test_b.py::test_three` | 3.00 |" in report
    assert "3 of 4 tests cover nothing no other test does. 2 tests, in 1" in report
    assert "3 of 4 tests cover nothing no other test does. 3 tests, in 1" in report
    assert "| 1 | 3 | 2 | `src/a.test.ts > sums > adds (1+1)` |" in report
    assert "| `backend/tests/sub/test_b.py` | 3 |" in report
    assert "| `menu.spec.ts > opens (chromium)` | 1.50 |" in report
    assert "| `menu.spec.ts > dark > opens (chromium)` | 0.25 |" in report
    assert (out / "frontend.tsv").read_text().splitlines() == [
        "test\tseconds\tcovers\tcovers alone\tsame as group",
        "src/a.test.ts > subtracts\t0.002\t2\t1\t",
        "src/a.test.ts > sums > adds (1+1)\t0.012\t2\t0\t1",
        "src/a.test.ts > sums > adds again\t0.003\t2\t0\t1",
        "src/a.test.ts > sums > adds again (2)\t0.003\t2\t0\t1",
    ]
    assert (
        "tests/sub/test_b.py::test_four\t0.500\t\t\t\n"
        in (out / "backend.tsv").read_text()
    )
    assert (out / "backend-contexts.log").read_text() == "ran in backend\n"
    # Each name run alone once, its output in a log of its own.
    assert len(list((out / "frontend-alone").iterdir())) == 3
