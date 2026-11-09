from pathlib import Path

import pytest

from inventory_tng import settings
from inventory_tng.settings import (
    FrontendConfig,
    Settings,
    SettingsError,
    migration_settings,
)

from helpers.settings import from_environ

URL = "postgresql://u:p@db/x"


@pytest.mark.parametrize(
    "scheme", ["postgres", "postgresql", "postgres+psycopg", "postgresql+psycopg"]
)
def test_accepts_the_usual_postgresql_schemes(scheme: str) -> None:
    settings = from_environ({"DATABASE_URL": f"{scheme}://u:p@db/x"})
    assert settings.database_url == "postgresql+psycopg://u:p@db/x"


def test_checks_a_url_given_directly_too() -> None:
    with pytest.raises(SettingsError, match="must be PostgreSQL"):
        Settings(database_url="sqlite:///x.db")


@pytest.mark.parametrize("environ", [{}, {"DATABASE_URL": ""}, {"DATABASE_URL": " "}])
def test_refuses_a_missing_or_empty_database_url(environ: dict[str, str]) -> None:
    """The message says where the value is described, for dev and production."""
    with pytest.raises(
        SettingsError,
        match=r"^DATABASE_URL is not set: .*\.env\.example.*DEPLOYMENT\.md \(Settings\)",
    ):
        from_environ(environ)


def test_migrations_read_their_own_url_and_no_app_settings() -> None:
    """MIGRATION_DATABASE_URL, the owner's login (migrations/env.py): a
    migration needs no DATABASE_URL, and the app's other settings, even
    unusable ones, do not stop it."""
    environ = {
        "MIGRATION_DATABASE_URL": "postgresql://owner:p@db/x",
        "MAIL_HOST": "smtp.example.org",
        "LOG_FORMAT": "json",
    }
    settings = migration_settings(environ)
    assert settings.database_url == "postgresql+psycopg://owner:p@db/x"
    assert settings.logging.format == "json"
    assert settings.mail is None


@pytest.mark.parametrize(
    ("environ", "error"),
    [
        (
            {"DATABASE_URL": URL},
            r"MIGRATION_DATABASE_URL is not set: development's is in \.env\.example",
        ),
        ({"MIGRATION_DATABASE_URL": "sqlite:///x.db"}, "MIGRATION_DATABASE_URL must"),
    ],
)
def test_migrations_refuse_a_missing_or_wrong_url(
    environ: dict[str, str], error: str
) -> None:
    with pytest.raises(SettingsError, match=error):
        migration_settings(environ)


@pytest.mark.parametrize(
    "value", ["not a url", "foo://x/y", "sqlite:///x.db", "postgresql+psycopg2://db/x"]
)
def test_refuses_anything_but_postgresql(value: str) -> None:
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        from_environ({"DATABASE_URL": value})


@pytest.mark.parametrize(
    ("value", "expected"), [("", True), ("true", True), ("ON", True), ("0", False)]
)
def test_reads_whether_to_log_client_addresses(value: str, expected: bool) -> None:
    environ = {"DATABASE_URL": "postgresql://db/x", "LOG_CLIENT_ADDRESS": value}
    assert from_environ(environ).logging.client_address is expected


def test_refuses_an_unclear_client_address_setting() -> None:
    environ = {"DATABASE_URL": "postgresql://db/x", "LOG_CLIENT_ADDRESS": "maybe"}
    with pytest.raises(SettingsError, match="LOG_CLIENT_ADDRESS"):
        from_environ(environ)


def test_the_suite_finds_no_build() -> None:
    """Whether or not this clone has a frontend/dist (conftest's
    no_settings_from_the_shell): CI's backend job has none, so a test that
    read this one could pass here and fail there."""
    missing = settings.CLONE_DIST
    assert missing != settings.CLONE / "frontend" / "dist"
    assert not missing.exists()
    assert Settings(database_url=URL).frontend.dist == missing
    assert from_environ({"DATABASE_URL": URL}).frontend.dist == missing


def test_reads_where_the_bundle_is(tmp_path: Path) -> None:
    environ = {"DATABASE_URL": "postgresql://db/x", "FRONTEND_DIST": str(tmp_path)}
    assert from_environ(environ).frontend.dist == tmp_path


def test_the_dev_server_is_for_development_only() -> None:
    environ = {
        "DATABASE_URL": "postgresql://db/x",
        "VITE_DEV_SERVER": "http://localhost:5173",
    }
    with pytest.raises(SettingsError, match="VITE_DEV_SERVER"):
        from_environ(environ)
    settings = from_environ(environ | {"APP_ENV": "development"})
    assert settings.frontend.dev_server == "http://localhost:5173"


@pytest.mark.parametrize(
    "value",
    [
        "localhost:5173",
        "ftp://x:1",
        "http://x:1/static/",
        "http://u:pw@x:1",
        "http://x:1?q",
        "http://x:1#f",
    ],
)
def test_refuses_a_dev_server_that_is_not_an_origin(value: str) -> None:
    with pytest.raises(SettingsError, match="must look like"):
        FrontendConfig(dev_server=value)


@pytest.mark.parametrize(
    "value",
    ["http://localhost", "http://x:y", "http://x:80", "https://x:443", "http://x:0"],
)
def test_a_dev_server_needs_a_port(value: str) -> None:
    with pytest.raises(SettingsError, match="needs a port"):
        FrontendConfig(dev_server=value)


def test_a_dev_server_keeps_only_its_origin() -> None:
    assert FrontendConfig(dev_server="http://[::1]:5173/").dev_server == (
        "http://[::1]:5173"
    )


@pytest.mark.parametrize(
    ("value", "development"),
    [("", False), ("production", False), ("Development", True)],
)
def test_app_env_decides_development(value: str, development: bool) -> None:
    settings = from_environ({"DATABASE_URL": "postgresql://db/x", "APP_ENV": value})
    assert settings.development is development


def test_a_setup_token_is_long_and_never_shown() -> None:
    token = "s" * 40
    settings = from_environ({"DATABASE_URL": URL, "SETUP_TOKEN": f" {token} "})
    assert settings.setup_token == token
    assert token not in repr(settings)
    assert from_environ({"DATABASE_URL": URL}).setup_token is None
    with pytest.raises(SettingsError, match="SETUP_TOKEN must be at least 32"):
        from_environ({"DATABASE_URL": URL, "SETUP_TOKEN": "short"})


def test_totp_keys_are_needed_outside_development(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="TOTP_KEYS_DIR is needed"):
        Settings.from_environ({"DATABASE_URL": URL})
    with pytest.raises(SettingsError, match="holds no key"):
        Settings.from_environ({"DATABASE_URL": URL, "TOTP_KEYS_DIR": str(tmp_path)})
    assert from_environ({"DATABASE_URL": URL}).totp_keys is not None
