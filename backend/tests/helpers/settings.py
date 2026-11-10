"""Settings read from an environment a test gives."""

from inventory_tng.settings import Settings


def settings(**environ: str) -> Settings:
    """Settings.from_environ with `environ` and a database URL, which every
    Settings needs."""
    return Settings.from_environ({"DATABASE_URL": "postgresql://db/x", **environ})
