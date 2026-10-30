import pytest

from inventory_tng.settings import Settings, SettingsError


@pytest.mark.parametrize(
    "scheme", ["postgres", "postgresql", "postgres+psycopg", "postgresql+psycopg"]
)
def test_accepts_the_usual_postgresql_schemes(scheme: str) -> None:
    settings = Settings.from_environ({"DATABASE_URL": f"{scheme}://u:p@db/x"})
    assert settings.database_url == "postgresql+psycopg://u:p@db/x"


def test_checks_a_url_given_directly_too() -> None:
    with pytest.raises(SettingsError, match="must be PostgreSQL"):
        Settings(database_url="sqlite:///x.db")


@pytest.mark.parametrize("environ", [{}, {"DATABASE_URL": ""}, {"DATABASE_URL": " "}])
def test_refuses_a_missing_or_empty_database_url(environ: dict[str, str]) -> None:
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        Settings.from_environ(environ)


@pytest.mark.parametrize(
    "value", ["not a url", "foo://x/y", "sqlite:///x.db", "postgresql+psycopg2://db/x"]
)
def test_refuses_anything_but_postgresql(value: str) -> None:
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        Settings.from_environ({"DATABASE_URL": value})
