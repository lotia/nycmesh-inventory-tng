"""The repository's `.env` file (CONTRIBUTING.md, Local settings)."""

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
