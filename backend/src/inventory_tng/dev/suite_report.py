"""How long each test suite takes, what it covers, and what each test
covers that no other does (CONTRIBUTING.md, Measuring the tests). From the
clone's root, with the database started:

    mise run test-report

It runs the backend's tests twice, timed as mise run check-backend runs
them, then with each test's coverage recorded apart (slower, and on
coverage's C tracer, which records it); the frontend's once, then each
test alone; and the browser tests. It writes, under .local/test-report/,
report.md, and a table of each suite's tests: seconds, what it covers,
what it alone covers, which is what removing it would lose, and the group
of tests that cover exactly the same. Each run's output is in its .log.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Collection, Hashable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

import structlog

from inventory_tng.core.local_env import CLONE

log = structlog.get_logger(__name__)

OUT = CLONE / ".local" / "test-report"
BACKEND = CLONE / "backend"
FRONTEND = CLONE / "frontend"
# As mise run check-backend runs them, but the floor: this measures, and
# reports whatever it finds.
PYTEST = ["uv", "run", "pytest", "-n", "auto", "--maxprocesses=8", "--cov"]
PYTEST += ["--cov-fail-under=0", "-q"]
# Vitest's coverage, with no floors and none raised (vite.config.ts).
VITEST = ["npx", "vitest", "run", "--coverage", "--reporter=json"]
VITEST += ["--coverage.thresholds.autoUpdate=false"] + [
    f"--coverage.thresholds.{kind}=0"
    for kind in ("statements", "branches", "functions", "lines")
]
# How many rows each list in report.md shows; the tables have them all.
SHOWN = 15
# A character that means something in a JavaScript regular expression.
REGEX_SPECIAL = re.compile(r"[.*+?^${}()|[\]\\/]")


@dataclass
class Suite:
    name: str
    wall: float
    # Each test's seconds, by its id.
    seconds: dict[str, float]
    coverage: str = ""
    # What each test covers, and covers alone; empty when not measured.
    covers: dict[str, int] = field(default_factory=dict)
    own: dict[str, int] = field(default_factory=dict)
    # Each test covering exactly what another does, and its group's number.
    same: dict[str, int] = field(default_factory=dict)


def run(command: Sequence[str], cwd: Path, log_name: str, **env: str) -> float:
    """Run `command`, its output into log_name's file under OUT; its
    seconds. Stop the report if it fails."""
    started = time.monotonic()
    with (OUT / log_name).open("a") as output:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=os.environ | env,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if done.returncode:
        log.error("test run failed", log=log_name, code=done.returncode)
        sys.exit(f"Failed: {' '.join(command)}; its output: {OUT / log_name}")
    return time.monotonic() - started


def own_and_same(
    covers: Mapping[str, Collection[Hashable]],
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """From what each test covers: how much, how much of it no other test
    does, and the groups of tests covering exactly the same, numbered from
    the largest."""
    counts = Counter(key for keys in covers.values() for key in keys)
    groups: dict[frozenset[Hashable], list[str]] = defaultdict(list)
    for test, keys in covers.items():
        if keys:
            groups[frozenset(keys)].append(test)
    return (
        {test: len(keys) for test, keys in covers.items()},
        {test: sum(counts[key] == 1 for key in keys) for test, keys in covers.items()},
        numbered(groups.values()),
    )


def numbered(groups: Iterable[list[str]]) -> dict[str, int]:
    """Each test in a group of more than one, by its group's number, the
    largest group first."""
    shared = sorted((sorted(g) for g in groups if len(g) > 1), key=lambda g: -len(g))
    return {test: number for number, g in enumerate(shared, 1) for test in g}


def backend_tests(junit: Path) -> dict[str, float]:
    """Each test pytest ran and did not skip, by its node id, and its
    seconds, setup and teardown included."""
    seconds = {}
    for case in ElementTree.parse(junit).iter("testcase"):
        if case.find("skipped") is None:
            module = case.get("classname", "").replace(".", "/")
            seconds[f"{module}.py::{case.get('name')}"] = float(case.get("time", 0))
    return seconds


def backend_contexts(data_file: Path) -> tuple[dict, dict, dict]:
    """own_and_same for coverage's data recorded with --cov-context=test:
    each test's arcs, its setup and teardown included. Counted in SQLite,
    and the groups by a digest of each test's arcs, as the arcs of every
    test together would fill the memory."""
    with closing(sqlite3.connect(data_file)) as db:
        db.executescript("""
            CREATE TEMP TABLE covered AS
              SELECT DISTINCT
                substr(context, 1, instr(context, '|') - 1) AS test,
                file_id, fromno, tono
              FROM arc JOIN context ON context.id = arc.context_id
              WHERE instr(context, '|') > 0;
            CREATE TEMP TABLE alone AS
              SELECT file_id, fromno, tono FROM covered
              GROUP BY file_id, fromno, tono HAVING count(*) = 1;
            CREATE INDEX temp.alone_arc ON alone (file_id, fromno, tono);
        """)
        covers, own = {}, {}
        for test, arcs, alone in db.execute("""
            SELECT test, count(*), count(alone.file_id)
            FROM covered LEFT JOIN alone USING (file_id, fromno, tono)
            GROUP BY test
        """):
            covers[test], own[test] = arcs, alone
        digests: dict[str, list[str]] = defaultdict(list)
        test, digest = None, hashlib.sha256()
        rows = db.execute("SELECT * FROM covered ORDER BY 1, 2, 3, 4")
        for row in [*rows, (None,)]:
            if row[0] != test:
                if test is not None:
                    digests[digest.hexdigest()].append(test)
                test, digest = row[0], hashlib.sha256()
            digest.update(repr(row[1:]).encode())
    return covers, own, numbered(digests.values())


def measure_backend() -> Suite:
    log.info("measuring suite", suite="backend")
    junit, totals = OUT / "backend-junit.xml", OUT / "backend-coverage.json"
    wall = run(
        [*PYTEST, f"--junitxml={junit}", f"--cov-report=json:{totals}"],
        BACKEND,
        "backend.log",
    )
    suite = Suite("Backend", wall, backend_tests(junit))
    percent = json.loads(totals.read_text())["totals"]["percent_covered"]
    suite.coverage = f"{percent:.2f}% of lines and branches"
    log.info("measuring each test's coverage", suite="backend")
    data = OUT / "backend-contexts.coverage"
    run(
        [*PYTEST, "--cov-context=test", "--cov-report="],
        BACKEND,
        "backend-contexts.log",
        COVERAGE_CORE="ctrace",
        COVERAGE_FILE=str(data),
    )
    suite.covers, suite.own, suite.same = backend_contexts(data)
    return suite


def vitest_tests(report: Mapping) -> dict[tuple[str, str], float]:
    """Each test Vitest's JSON report says passed, by its file and full
    name (its describes' titles and its own, as --testNamePattern reads
    them), and its seconds."""
    return {
        (file["name"], " > ".join([*test["ancestorTitles"], test["title"]])): (
            test["duration"] / 1000
        )
        for file in report["testResults"]
        for test in file["assertionResults"]
        if test["status"] == "passed"
    }


def frontend_id(file: str, name: str) -> str:
    return f"{Path(file).relative_to(FRONTEND)} > {name}"


def covered(coverage_map: Mapping) -> set[tuple[str, str, str, int]]:
    """Each statement, and each way of each branch, that a run of Vitest
    ran, from its JSON report's coverage."""
    keys = set()
    for path, file in coverage_map.items():
        keys |= {(path, "s", s, 0) for s, count in file["s"].items() if count}
        keys |= {
            (path, "b", b, way)
            for b, counts in file["b"].items()
            for way, count in enumerate(counts)
            if count
        }
    return keys


def alone(file: str, name: str) -> set[tuple[str, str, str, int]]:
    """What the frontend's test covers, run alone: by its exact full name,
    as a regular expression, in its file."""
    pattern = "^" + REGEX_SPECIAL.sub(r"\\\g<0>", name) + "$"
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "report.json"
        run(
            [
                *VITEST,
                "--coverage.reporter=json-summary",
                f"--coverage.reportsDirectory={directory}",
                f"--outputFile={output}",
                f"--testNamePattern={pattern}",
                file,
            ],
            FRONTEND,
            "frontend-alone.log",
        )
        report = json.loads(output.read_text())
    if not report["numPassedTests"]:
        sys.exit(f"Found no test named {name!r} in {file}")
    return covered(report["coverageMap"])


def measure_frontend() -> Suite:
    log.info("measuring suite", suite="frontend")
    report, totals = OUT / "frontend.json", OUT / "frontend-coverage"
    wall = run(
        [
            *VITEST,
            "--coverage.reporter=json-summary",
            f"--coverage.reportsDirectory={totals}",
            f"--outputFile={report}",
        ],
        FRONTEND,
        "frontend.log",
    )
    tests = vitest_tests(json.loads(report.read_text()))
    suite = Suite("Frontend", wall, {frontend_id(*t): s for t, s in tests.items()})
    total = json.loads((totals / "coverage-summary.json").read_text())["total"]
    suite.coverage = (
        f"{total['lines']['pct']}% of lines, {total['branches']['pct']}% of branches"
    )
    log.info("measuring each test's coverage", suite="frontend", tests=len(tests))
    with ThreadPoolExecutor(os.cpu_count()) as pool:
        each = pool.map(lambda test: alone(*test), tests)
        suite.covers, suite.own, suite.same = own_and_same(
            dict(zip((frontend_id(*t) for t in tests), each, strict=True))
        )
    return suite


def playwright_tests(suite: Mapping) -> dict[str, float]:
    """Each test in Playwright's JSON report, by its file, title and
    project, and its seconds over its tries."""
    seconds = {}
    for spec in suite.get("specs", []):
        for test in spec["tests"]:
            name = f"{spec['file']} > {spec['title']} ({test['projectName']})"
            seconds[name] = sum(r["duration"] for r in test["results"]) / 1000
    for inner in suite.get("suites", []):
        seconds |= playwright_tests(inner)
    return seconds


def measure_browser() -> Suite:
    log.info("measuring suite", suite="browser")
    run(["npm", "run", "build"], FRONTEND, "browser.log")
    report = OUT / "browser.json"
    wall = run(
        ["npx", "playwright", "test", "--reporter=json"],
        FRONTEND,
        "browser.log",
        PLAYWRIGHT_JSON_OUTPUT_FILE=str(report),
    )
    tests = playwright_tests({"suites": json.loads(report.read_text())["suites"]})
    return Suite("Browser", wall, tests)


def file_lines() -> dict[str, int]:
    """Each test file's lines, by its path from the clone."""
    files = [
        *BACKEND.glob("tests/**/test_*.py"),
        *FRONTEND.glob("src/**/*.test.ts*"),
        *FRONTEND.glob("browser-tests/*.spec.ts"),
    ]
    return {str(f.relative_to(CLONE)): len(f.read_text().splitlines()) for f in files}


def table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    return lines + ["| " + " | ".join(map(str, row)) + " |" for row in rows]


def report(suites: Sequence[Suite], files: Mapping[str, int], date: str) -> str:
    """report.md: each suite's totals, its slowest tests, its tests that
    cover nothing alone and those that cover the same, and the longest
    test files."""
    lines = [
        "# Test report",
        "",
        (
            f"Measured on {date} by `mise run test-report`. Each suite's"
            " table, one row per test, is beside this file."
        ),
        "",
        *table(
            ["Suite", "Tests", "Wall", "Test time", "Coverage"],
            (
                (s.name, len(s.seconds), f"{s.wall:.1f} s",
                 f"{sum(s.seconds.values()):.0f} s", s.coverage or "not measured")
                for s in suites
            ),
        ),
    ]  # fmt: skip
    for suite in suites:
        slowest = sorted(suite.seconds.items(), key=lambda t: -t[1])[:SHOWN]
        lines += ["", f"## {suite.name}", "", "Slowest tests:", ""]
        lines += table(
            ["Test", "Seconds"], ((f"`{t}`", f"{s:.2f}") for t, s in slowest)
        )
        if not suite.covers:
            continue
        nothing = sum(not suite.own.get(test) for test in suite.seconds)
        groups = Counter(suite.same.values())
        lines += [
            "",
            (
                f"{nothing} of {len(suite.seconds)} tests cover nothing no"
                f" other test does. {len(suite.same)} tests, in {len(groups)}"
                " groups, cover exactly what another test covers; the"
                " largest groups:"
            ),
            "",
        ]
        examples = {}
        for test, number in sorted(suite.same.items()):
            examples.setdefault(number, test)
        lines += table(
            ["Group", "Tests", "Each covers", "One of them"],
            (
                (n, groups[n], suite.covers[examples[n]], f"`{examples[n]}`")
                for n in sorted(groups)[:SHOWN]
            ),
        )
    longest = sorted(files.items(), key=lambda f: -f[1])[:SHOWN]
    lines += ["", "## Longest test files", ""]
    lines += table(["File", "Lines"], ((f"`{f}`", n) for f, n in longest))
    return "\n".join(lines) + "\n"


def write_tests(suite: Suite, path: Path) -> None:
    """The suite's tests, one per row, tab-separated."""
    header = "test\tseconds\tcovers\tcovers alone\tsame as group\n"
    rows = (
        f"{test}\t{seconds:.3f}\t{suite.covers.get(test, '')}"
        f"\t{suite.own.get(test, '')}\t{suite.same.get(test, '')}\n"
        for test, seconds in sorted(suite.seconds.items())
    )
    path.write_text(header + "".join(rows))


def main() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    suites = [measure_backend(), measure_frontend(), measure_browser()]
    for suite in suites:
        write_tests(suite, OUT / f"{suite.name.lower()}.tsv")
    (OUT / "report.md").write_text(
        report(suites, file_lines(), time.strftime("%Y-%m-%d"))
    )
    log.info("wrote test report", path=str(OUT / "report.md"))


if __name__ == "__main__":
    main()
