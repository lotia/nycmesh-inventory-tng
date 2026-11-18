"""mise run test-report (dev/suite_report.py), on suites' outputs made up
here: each command it runs is answered by a fake that writes what the real
tool would, in a clone of its own."""

import json
import re
import runpy
import subprocess
from pathlib import Path
from typing import IO

import pytest
from coverage import CoverageData

from inventory_tng.core import local_env
from inventory_tng.dev import suite_report

JUNIT = """<testsuites><testsuite>
<testcase classname="tests.test_a" name="test_one" time="1.5"/>
<testcase classname="tests.test_a" name="test_two[x]" time="0.25"/>
<testcase classname="tests.sub.test_b" name="test_three" time="3"/>
<testcase classname="tests.sub.test_b" name="test_four" time="0.5"/>
<testcase classname="tests.sub.test_b" name="test_skipped" time="0">
<skipped/></testcase>
</testsuite></testsuites>"""
# Each backend test's arcs, by its context; the first test's split between
# its setup and its call, and an arc no test ran but the import.
CONTEXTS = {
    "tests/test_a.py::test_one|setup": {(1, 2)},
    "tests/test_a.py::test_one|run": {(2, 3)},
    "tests/test_a.py::test_two[x]|run": {(1, 2), (2, 3)},
    "tests/sub/test_b.py::test_three|run": {(1, 2), (5, 6)},
    "": {(9, 10)},
}
# The frontend's tests: each one's seconds, and the statements and ways of
# the one branch it covers alone.
VITEST = {
    "sums > adds (1+1)": (0.012, {"0"}, {0}),
    "sums > adds again": (0.003, {"0"}, {0}),
    "subtracts": (0.002, {"0", "1"}, set()),
}
PLAYWRIGHT = {
    "config": {"projects": [{"name": "sign-in"}, {"name": "chromium"}]},
    "suites": [
        {
            "specs": [
                {
                    "file": "sign-in.setup.ts",
                    "title": "signs in",
                    "tests": [
                        {"projectName": "sign-in", "results": [{"duration": 900}]}
                    ],
                }
            ],
            "suites": [
                {
                    "specs": [
                        {
                            "file": "menu.spec.ts",
                            "title": "opens",
                            "tests": [
                                {
                                    "projectName": "chromium",
                                    "results": [{"duration": 1000}, {"duration": 500}],
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    ],
}


def contexts_data(path: Path) -> None:
    data = CoverageData(basename=str(path))
    for context, arcs in CONTEXTS.items():
        data.set_context(context)
        data.add_arcs({"/src/inventory_tng/a.py": arcs})
    data.write()


def visuite_report(frontend: Path, names: list[str], output: Path) -> None:
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
        for n in names
    ]
    skipped = {"ancestorTitles": [], "title": "skips", "status": "skipped"}
    output.write_text(
        json.dumps(
            {
                "numPassedTests": len(names),
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
    if "--cov-context" in command[-2]:
        contexts_data(Path(env["COVERAGE_FILE"]))
    if "--outputFile" in options:
        directory = Path(options["--coverage.reportsDirectory"])
        directory.mkdir(exist_ok=True)
        summary = {"total": {"lines": {"pct": 100}, "branches": {"pct": 99.22}}}
        (directory / "coverage-summary.json").write_text(json.dumps(summary))
        pattern = options.get("--testNamePattern", "")
        names = [n for n in VITEST if re.search(pattern, n)]
        visuite_report(cwd, names, Path(options["--outputFile"]))
    if "PLAYWRIGHT_JSON_OUTPUT_FILE" in env:
        Path(env["PLAYWRIGHT_JSON_OUTPUT_FILE"]).write_text(json.dumps(PLAYWRIGHT))
    return subprocess.CompletedProcess(command, 0)


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone of its own, with a test file on each side, every command
    answered by fake_run."""
    for name in ("backend", "frontend/src", "frontend/browser-tests"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "sub").mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "sub" / "test_b.py").write_text("a\nb\nc\n")
    (tmp_path / "frontend" / "src" / "a.test.ts").write_text("a\n")
    (tmp_path / "frontend" / "browser-tests" / "menu.spec.ts").write_text("a\nb\n")
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
    assert covers == {one: 2, two: 2, three: 2}
    assert own == {one: 0, two: 0, three: 1}
    assert same == {one: 1, two: 1}


def test_a_failed_run_stops_the_report_naming_its_output(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(suite_report, "OUT", clone)
    with pytest.raises(SystemExit, match=str(clone / "failed.log")):
        suite_report.run(["false"], clone, "failed.log")
    assert suite_report.run(["true"], clone, "passed.log") >= 0


def test_a_frontend_test_not_found_alone_stops_the_report(clone: Path) -> None:
    file = str(clone / "frontend" / "src" / "a.test.ts")
    suite_report.OUT.mkdir(parents=True)
    with pytest.raises(SystemExit, match="Found no test named 'adds' in "):
        suite_report.alone(file, "adds")


def test_the_report_and_each_suites_tests(clone: Path) -> None:
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("inventory_tng.dev.suite_report", run_name="__main__")
    out = clone / ".local" / "test-report"
    report = (out / "report.md").read_text()
    assert "| Backend | 4 | 0.0 s | 5 s | 99.42% of lines and branches |" in report
    assert (
        "| Frontend | 3 | 0.0 s | 0 s | 100% of lines, 99.22% of branches |" in report
    )
    assert "| Browser | 2 | 0.0 s | 2 s | not measured |" in report
    assert "| `tests/sub/test_b.py::test_three` | 3.00 |" in report
    assert "3 of 4 tests cover nothing no other test does. 2 tests, in 1" in report
    assert "2 of 3 tests cover nothing no other test does. 2 tests, in 1" in report
    assert "| 1 | 2 | 2 | `src/a.test.ts > sums > adds (1+1)` |" in report
    assert "| `backend/tests/sub/test_b.py` | 3 |" in report
    assert "| `menu.spec.ts > opens (chromium)` | 1.50 |" in report
    assert (out / "frontend.tsv").read_text().splitlines() == [
        "test\tseconds\tcovers\tcovers alone\tsame as group",
        "src/a.test.ts > subtracts\t0.002\t2\t1\t",
        "src/a.test.ts > sums > adds (1+1)\t0.012\t2\t0\t1",
        "src/a.test.ts > sums > adds again\t0.003\t2\t0\t1",
    ]
    assert (
        "tests/sub/test_b.py::test_four\t0.500\t\t\t\n"
        in (out / "backend.tsv").read_text()
    )
    assert (out / "backend-contexts.log").read_text() == "ran in backend\n"
