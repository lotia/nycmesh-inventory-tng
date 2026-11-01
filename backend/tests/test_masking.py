import json
import logging
from dataclasses import dataclass

import pytest
import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import exc

from inventory_tng.logs import configure_logging
from inventory_tng.masking import MASK, Masker, scrub_database_message
from inventory_tng.server import create_server
from inventory_tng.settings import LogConfig, SettingsError

# Written out, not read from masking.py, so shrinking a list fails a test.
SECRETS = {
    "password": "hunter2-password",
    "admin_password": "hunter2-admin",
    "api_key": "key-123",
    "apiKey": "key-456",
    "authorization": "Bearer abc",
    "cookie": "sid=abc",
    "set_cookie": "sid=def",
    "session_id": "sess-1",
    "sessionToken": "sess-2",
    "csrf_token": "csrf-1",
    "client_secret": "shh-1",
    "totp_code": "123456",
    "otp": "654321",
    "database_url": "postgresql://u:pw@db/x",
    "dsn": "postgresql://u:pw2@db/x",
    "credentials": "user:pw3",
    "private_key": "-----BEGIN-----",
    "PGPASSWORD": "pg-pw",
    "DBPassword": "db-pw",
    "HTTPAuthorization": "Basic xyz",
    "pwd": "pwd-1",
    "jwt": "eyJ.jwt",
    "user_pass": "pass-1",
    "registration_phrase": "abacus-abdomen-tulip",
}
PERSONAL = {
    "email": "jane@example.org",
    "volunteer_email": "jane2@example.org",
    "phone": "+1 555 0100",
    "phone_number": "+1 555 0101",
    "name": "Jane Doe",
    "volunteer_name": "Jane Roe",
    "fullName": "Jane Poe",
    "address": "1 Main St",
    "street_address": "2 Main St",
    "sensitive_body": '{"note": "free text"}',
    "sensitiveQuery": "free text 2",
}
EMAIL = PERSONAL["email"]
KEPT = {
    "volunteer_id": 7,
    "username": "admin1",
    "hostname": "db",
    "footprint": 3,
    "database_name": "inventory",
    "table_name": "item",
    "bypass": True,
}


def lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def configure(**environ: str) -> None:
    configure_logging(LogConfig.from_environ({"LOG_FORMAT": "json", **environ}))


@pytest.mark.usefixtures("restore_logging")
def test_production_masks_every_secret_and_personal_field(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure()
    structlog.get_logger("inventory_tng.test").info(
        "everything",
        **SECRETS,
        **PERSONAL,
        **KEPT,
        headers={"Authorization": "Bearer nested", "accept": "*/*"},
        people=[{"email": "listed@example.org"}],
    )
    (line,) = lines(capsys)
    output = json.dumps(line)
    for value in [*SECRETS.values(), *PERSONAL.values(), "nested", "listed@"]:
        assert value not in output
    assert all(line[key] == MASK for key in [*SECRETS, *PERSONAL])
    assert {key: line[key] for key in KEPT} == KEPT
    assert line["headers"] == {"Authorization": MASK, "accept": "*/*"}


def test_production_is_the_default() -> None:
    assert Masker.from_config(LogConfig.from_environ({})) == Masker()


@pytest.mark.usefixtures("restore_logging")
def test_development_keeps_personal_data_but_masks_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(APP_ENV="development")
    structlog.get_logger("inventory_tng.test").info("dev", **SECRETS, **PERSONAL)
    (line,) = lines(capsys)
    assert {key: line[key] for key in PERSONAL} == PERSONAL
    assert all(line[key] == MASK for key in SECRETS)


@pytest.mark.usefixtures("restore_logging")
def test_unmask_secrets_in_development_shows_them_and_says_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(APP_ENV="development", UNMASK_SECRETS="true")
    structlog.get_logger("inventory_tng.test").info("dev", **SECRETS)
    warning, line = lines(capsys)
    assert warning["level"] == "warning"
    assert warning["log_unmasked"] is True
    assert {key: line[key] for key in SECRETS} == SECRETS


@pytest.mark.parametrize("environ", [{}, {"APP_ENV": "production"}])
def test_unmask_secrets_is_refused_outside_development(
    environ: dict[str, str],
) -> None:
    with pytest.raises(SettingsError, match="UNMASK_SECRETS"):
        LogConfig.from_environ({**environ, "UNMASK_SECRETS": "true"})


def test_refuses_an_unknown_environment() -> None:
    with pytest.raises(SettingsError, match="APP_ENV"):
        LogConfig.from_environ({"APP_ENV": "staging"})


@pytest.mark.usefixtures("restore_logging")
def test_the_standard_librarys_lines_mask_context_too(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure()
    with structlog.contextvars.bound_contextvars(volunteer_email="ctx@example.org"):
        logging.getLogger("some.library").warning("theirs")
    (line,) = lines(capsys)
    assert line["volunteer_email"] == MASK


@pytest.mark.parametrize("log_format", ["json", "console"])
@pytest.mark.usefixtures("restore_logging")
def test_production_scrubs_database_values_from_tracebacks(
    capsys: pytest.CaptureFixture[str],
    database_error: exc.DBAPIError,
    log_format: str,
) -> None:
    configure(LOG_FORMAT=log_format)
    structlog.get_logger("inventory_tng.test").error("failed", exc_info=database_error)
    output = capsys.readouterr().out
    assert "jane@example.org" not in output
    assert "InvalidTextRepresentation" in output
    assert MASK in output


@pytest.mark.usefixtures("restore_logging")
def test_development_keeps_tracebacks_whole(
    capsys: pytest.CaptureFixture[str], database_error: exc.DBAPIError
) -> None:
    configure(APP_ENV="development")
    structlog.get_logger("inventory_tng.test").error("failed", exc_info=database_error)
    assert "jane@example.org" in capsys.readouterr().out


def test_other_exceptions_are_left_alone() -> None:
    error = ValueError('not a "database" error')
    assert Masker().exception_message(str(error), error) == str(error)


@dataclass
class Person:
    email: str
    volunteer_id: int


class PersonModel(BaseModel):
    phone: str
    volunteer_id: int


@pytest.mark.usefixtures("restore_logging")
def test_objects_are_masked_by_their_field_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure()
    structlog.get_logger("inventory_tng.test").info(
        "objects",
        volunteer=Person("obj@example.org", 7),
        model=PersonModel(phone="+1 555 0199", volunteer_id=8),
    )
    (line,) = lines(capsys)
    assert line["volunteer"] == {"email": MASK, "volunteer_id": 7}
    assert line["model"] == {"phone": MASK, "volunteer_id": 8}


@pytest.mark.usefixtures("restore_logging")
def test_a_librarys_exception_outside_except_keeps_its_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The standard library passes exc_info as a tuple: it must stay one."""
    configure()
    try:
        raise ValueError("the logged one")
    except ValueError as caught:
        error = caught
    try:
        raise KeyError("the current one")
    except KeyError:
        logging.getLogger("asyncio").error(
            "never retrieved", exc_info=(type(error), error, error.__traceback__)
        )
    (line,) = lines(capsys)
    assert "the logged one" in str(line["exception"])
    assert "the current one" not in str(line["exception"])


@pytest.mark.usefixtures("restore_logging")
def test_scrubs_database_errors_inside_exception_groups(
    capsys: pytest.CaptureFixture[str], database_error: exc.DBAPIError
) -> None:
    configure()
    try:
        raise ExceptionGroup("tasks", [database_error])
    except ExceptionGroup:
        structlog.get_logger("inventory_tng.test").exception("failed")
    output = capsys.readouterr().out
    assert "jane@example.org" not in output
    assert "InvalidTextRepresentation" in output


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            (
                'duplicate key value violates unique constraint "uq_volunteer_email"\n'
                "DETAIL:  Key (email)=(jane@example.org) already exists.\n"
                "LINE 1: SELECT 'it''s jane'"
            ),
            (
                f'duplicate key value violates unique constraint "{MASK}"\n'
                f"DETAIL:  {MASK}\n"
                f"LINE 1: SELECT '{MASK}'"
            ),
        ),
        ('syntax: "a"jane@x.org"', f'syntax: "{MASK}"'),
        ('syntax: "Jane', f'syntax: "{MASK}'),
        ("no quotes", "no quotes"),
    ],
)
def test_scrubs_detail_lines_and_quoted_values(message: str, expected: str) -> None:
    assert scrub_database_message(message) == expected


@pytest.mark.usefixtures("restore_logging")
def test_scrubs_pydantics_input_values(capsys: pytest.CaptureFixture[str]) -> None:
    configure()
    try:
        # Through a dict, because a traceback shows this line, and code lines
        # are not scrubbed.
        PersonModel.model_validate({"phone": "+1", "volunteer_id": EMAIL})
    except ValidationError:
        structlog.get_logger("inventory_tng.test").exception("invalid")
    output = capsys.readouterr().out
    assert "jane@example.org" not in output
    assert f"input_value={MASK}" in output


@pytest.mark.usefixtures("restore_logging")
def test_the_server_announces_unmasked_secrets(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    for name, value in {
        "DATABASE_URL": database_url,
        "LOG_FORMAT": "json",
        "APP_ENV": "development",
        "UNMASK_SECRETS": "true",
    }.items():
        monkeypatch.setenv(name, value)
    create_server()
    starting = [line for line in lines(capsys) if line["event"] == "starting"]
    assert [(line["level"], line["log_unmasked"]) for line in starting] == [
        ("warning", True)
    ]
