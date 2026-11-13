"""A file the tests compare with what the code makes now, which a command
writes again, or with --check only compares (fold_reference,
batch_schema)."""

import difflib
from pathlib import Path


def difference(path: Path, now: str) -> str:
    """How `path` differs from `now`: nothing if it doesn't."""
    was = path.read_text(encoding="utf-8") if path.exists() else ""
    return "".join(
        difflib.unified_diff(
            was.splitlines(keepends=True),
            now.splitlines(keepends=True),
            "committed",
            "now",
        )
    )


def check(path: Path, now: str) -> int:
    """--check: print how `path` differs from `now` and return 1, or say it
    is up to date."""
    found = difference(path, now)
    if found:
        print(found, end="")
        return 1
    print(f"{path.name} is up to date")
    return 0


def write(path: Path, now: str) -> int:
    path.write_text(now, encoding="utf-8")
    print(f"Wrote {path}")
    return 0
