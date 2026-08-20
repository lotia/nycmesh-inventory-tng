"""The names GitHub reports this workflow's jobs under, one per line.

Used by repo-settings.sh to decide which checks `main` should require. Read out
of the workflow rather than listed beside it, so that "the required checks are
the jobs in ci.yml" is a thing the script can check rather than a thing somebody
has to remember.

A check is named by the job's ``name:``, falling back to the job's key. A matrix
job produces one check per combination, named ``Name (value)`` -- or
``Name (a, b)`` for several axes, in the order the axes are declared.

Usage: ci-check-names.py <workflow.yml>

Deliberately parses the small subset of YAML this file uses rather than
importing a parser: it runs from a bash script that has no virtualenv, and the
workflow is ours.
"""

from __future__ import annotations

import itertools
import re
import sys


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse(path: str) -> list[str]:
    lines = [ln.rstrip("\n") for ln in open(path) if ln.strip() and not ln.lstrip().startswith("#")]

    # Everything under `jobs:`, which is the only block this cares about.
    try:
        start = next(i for i, ln in enumerate(lines) if re.match(r"^jobs:\s*$", ln))
    except StopIteration:
        return []

    body = []
    for line in lines[start + 1 :]:
        if indent_of(line) == 0:
            break
        body.append(line)
    if not body:
        return []

    job_indent = min(indent_of(ln) for ln in body)

    names: list[str] = []
    current: list[str] = []

    def flush(block: list[str]) -> None:
        if not block:
            return
        key = block[0].strip().rstrip(":")
        name = key
        axes: list[list[str]] = []

        for i, line in enumerate(block[1:], start=1):
            stripped = line.strip()
            depth = indent_of(line)

            if depth == job_indent + 2 and stripped.startswith("name:"):
                name = stripped[len("name:") :].strip().strip("'\"")

            # matrix axes: `component: [backend, frontend]`, or a `-` list
            if stripped == "matrix:":
                matrix_indent = depth
                for axis_line in block[i + 1 :]:
                    if indent_of(axis_line) <= matrix_indent:
                        break
                    axis = axis_line.strip()
                    inline = re.match(r"^([\w-]+):\s*\[(.*)\]\s*$", axis)
                    if inline:
                        values = [v.strip().strip("'\"") for v in inline.group(2).split(",")]
                        axes.append([v for v in values if v])

        if axes:
            for combination in itertools.product(*axes):
                names.append(f"{name} ({', '.join(combination)})")
        else:
            names.append(name)

    for line in body:
        if indent_of(line) == job_indent and line.strip().endswith(":"):
            flush(current)
            current = [line]
        elif current:
            current.append(line)
    flush(current)

    return names


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ci-check-names.py <workflow.yml>")
    for name in parse(sys.argv[1]):
        print(name)


if __name__ == "__main__":
    main()
