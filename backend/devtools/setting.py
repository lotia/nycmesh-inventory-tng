"""Print a setting as the backend reads it (inventory_tng.core.local_env),
for the task scripts, which run no Python of their own:

    uv run python -m devtools setting KEY [--file PATH]
"""

import argparse
from collections.abc import Sequence
from pathlib import Path

from inventory_tng.core.local_env import setting


def main(argv: Sequence[str], prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Print a setting as the backend reads it, for mise-tasks/.",
    )
    parser.add_argument("key")
    parser.add_argument("--file", type=Path, help="a .env other than the clone's")
    args = parser.parse_args(argv)
    print(setting(args.key, args.file))
    return 0
