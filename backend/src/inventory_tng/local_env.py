"""The repository's `.env` file (CONTRIBUTING.md, Local settings).

The task scripts read a setting as the backend does, with:

    uv run python -m inventory_tng.local_env KEY [--file PATH]
"""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv
from dotenv.main import DotEnv

# backend/src/inventory_tng/ -> the repository root, in a clone.
CLONE = Path(__file__).resolve().parents[3]
ENV_FILE = CLONE / ".env"


def load_local_env() -> None:
    """Values already set in the environment win. Without a `.env`, as in a
    container, this does nothing."""
    load_dotenv(ENV_FILE, override=False)


def local_env_values(path: Path | None = None) -> dict[str, str]:
    """The file's values (the repository's `.env` unless told another),
    without touching the environment. The tests read only the database
    settings from it, so a developer's LOG_* settings cannot change what they
    see; mise-tasks/compose reads INVENTORY_COMPOSE. References such as
    ${INVENTORY_DB_PORT} take the shell's value first, as load_local_env()
    does."""
    values = DotEnv(path or ENV_FILE, override=False).dict()
    return {key: value for key, value in values.items() if value}


def setting(key: str, path: Path | None = None) -> str:
    """A setting as the backend reads it: the environment's value if it is
    set at all, even empty; else the file's (the repository's `.env` unless
    told another); else empty."""
    if key in os.environ:
        return os.environ[key]
    return local_env_values(path).get(key, "")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m inventory_tng.local_env",
        description="Print a setting as the backend reads it, for mise-tasks/.",
    )
    parser.add_argument("key")
    parser.add_argument("--file", type=Path, help="a .env other than the clone's")
    args = parser.parse_args(argv)
    print(setting(args.key, args.file))


if __name__ == "__main__":
    main()
