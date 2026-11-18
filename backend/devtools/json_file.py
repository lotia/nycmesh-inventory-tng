"""What write-api-schema and write-unit-table share: a command that writes
one JSON file the frontend reads, laid out the same way, to its own path
or to the one given.
"""

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def write(
    argv: Sequence[str],
    *,
    prog: str,
    description: str | None,
    default: Path,
    content: Callable[[], Any],
) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", type=Path, default=default)
    path = parser.parse_args(argv).path
    text = json.dumps(content(), indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")
    return 0
