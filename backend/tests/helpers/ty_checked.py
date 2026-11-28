"""A small file written for a test, checked with `ty check` as the app's
own code is, for tests that prove ty refuses a call."""

import subprocess
from pathlib import Path

from ty import find_ty_bin

BACKEND = Path(__file__).parents[2]


def refused_lines(source: str, folder: Path) -> list[int]:
    """The lines of `source`, written to a file in `folder`, that ty
    refuses, each once, in order."""
    checked = folder / "checked.py"
    checked.write_text(source)
    found = subprocess.run(
        [find_ty_bin(), "check", "--output-format", "concise", str(checked)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    return sorted(
        {
            int(line.split(":")[1])
            for line in found.stdout.splitlines()
            if line.startswith(str(checked))
        }
    )
