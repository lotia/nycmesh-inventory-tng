"""Registering devices with a code, and refusing the ones that are not
(docs/decisions/0009)."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from inventory_tng import devices, registration_codes
from inventory_tng.devices import COOKIE, RegisteredDevice, RegistrationRejected
from inventory_tng.logs import configure_logging
from inventory_tng.models import CatalogueHistory, Device, RegistrationCode
from inventory_tng.settings import LogConfig, Settings

from helpers.concurrency import Committed
from helpers.devices import ADDRESS, new_device


def whoami(device: RegisteredDevice) -> dict[str, int]:
    return {"device_id": device.id}


@pytest.fixture
def app(make_app: Callable[..., FastAPI]) -> FastAPI:
    app = make_app()
    # A route that needs a device, as the ledger's will.
    app.add_api_route("/api/whoami", whoami)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Over https, as conftest's client is, with the whoami route."""
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def phrase(session: Session) -> str:
    _, phrase = devices.create_code(session, max_uses=3, lifetime=timedelta(days=1))
    return phrase


@pytest.fixture(params=["unknown", "revoked", "expired", "used_up"])
def refused(request: pytest.FixtureRequest, session: Session) -> tuple[str, str]:
    """A phrase that cannot register, and the reason it gives."""
    reason: str = request.param
    code, phrase = devices.create_code(session, max_uses=1, lifetime=timedelta(days=1))
    match reason:
        case "unknown":
            phrase = "not-a-real-phrase"
        case "revoked":
            devices.revoke_code(session, code.id)
        case "expired":
            code.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        case "used_up":
            devices.register(session, phrase, ADDRESS)
    session.flush()
    return reason, phrase


def device_history(session: Session, device_id: int) -> list[str]:
    return list(
        session.scalars(
            select(CatalogueHistory.action)
            .where(
                CatalogueHistory.table_name == "device",
                CatalogueHistory.row_id == device_id,
            )
            .order_by(CatalogueHistory.id)
        )
    )


@pytest.fixture
def committed_cookie(committed: Committed) -> tuple[int, str]:
    """A device committed to the database, for what runs in a transaction of
    its own, such as recording when it was last seen; deleted after the test
    (conftest's committed), so later tests don't find it."""
    with committed.session() as session, session.begin():
        device, token = new_device(session)
        device_id = device.id
    return device_id, token


def test_the_wordlist_is_plain_words() -> None:
    words = devices.words()
    assert len(words) > 7700
    assert all(word.isascii() and word.isalpha() and word.islower() for word in words)


def test_a_phrase_is_five_words_from_the_list() -> None:
    phrase = devices.new_phrase()
    assert len(phrase.split("-")) == devices.PHRASE_WORDS
    assert set(phrase.split("-")) <= set(devices.words())


@pytest.mark.parametrize(
    "typed", ["Abacus Tulip", "abacus-tulip", " abacus_tulip ", "ABACUS\ttulip"]
)
def test_phrases_ignore_case_and_separators(typed: str) -> None:
    assert devices.normalise_phrase(typed) == "abacus-tulip"


def test_registering_gives_a_device_its_own_id(session: Session, phrase: str) -> None:
    first, token = devices.register(session, phrase.upper().replace("-", " "), ADDRESS)
    second, other = devices.register(session, phrase, ADDRESS)
    assert first.id != second.id
    assert token != other
    # Only hashes are kept.
    assert first.token_hash == devices.digest(token)
    code = session.get(RegistrationCode, first.registration_code_id)
    assert code is not None
    assert code.uses == 2
    assert code.phrase_hash == devices.digest(phrase)
    session.refresh(first)
    assert first.first_seen_from == ip_address(ADDRESS)


def test_a_code_that_cannot_register_says_why(
    session: Session, refused: tuple[str, str]
) -> None:
    reason, phrase = refused
    with pytest.raises(RegistrationRejected) as rejected:
        devices.register(session, phrase, ADDRESS)
    assert rejected.value.reason == reason


def test_guessing_is_rate_limited_per_address(session: Session, phrase: str) -> None:
    for _ in range(devices.ATTEMPTS.amount):
        with pytest.raises(RegistrationRejected):
            devices.register(session, "wrong-guess", ADDRESS)
    with pytest.raises(RegistrationRejected) as rejected:
        devices.register(session, phrase, ADDRESS)
    assert rejected.value.reason == "rate_limited"
    # Another address is not held up.
    devices.register(session, phrase, "192.0.2.11")


def test_neither_phrase_nor_cookie_is_logged(session: Session, phrase: str) -> None:
    with structlog.testing.capture_logs() as logs:
        _, token = devices.register(session, phrase, ADDRESS)
    assert logs
    for line in logs:
        assert phrase not in str(line)
        assert token not in str(line)


def test_the_endpoint_sets_a_cookie_only_the_server_can_read(
    client: TestClient, phrase: str
) -> None:
    response = client.post("/api/devices", json={"code": phrase})
    assert response.status_code == 201
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{COOKIE}=")
    for attribute in ("httponly", "secure", "samesite=lax", "max-age=34560000"):
        assert attribute in cookie
    assert client.get("/api/whoami").json() == response.json()


def test_development_lets_the_cookie_travel_over_http(
    make_app: Callable[..., FastAPI], phrase: str
) -> None:
    with TestClient(make_app(development=True)) as client:
        response = client.post("/api/devices", json={"code": phrase})
    assert "secure" not in response.headers["set-cookie"].lower()


def test_the_endpoint_says_why_a_code_is_refused(
    client: TestClient, refused: tuple[str, str]
) -> None:
    reason, phrase = refused
    response = client.post("/api/devices", json={"code": phrase})
    assert (response.status_code, response.json()) == (400, {"reason": reason})


def test_good_codes_do_not_use_up_the_limit(session: Session) -> None:
    """A room of phones behind one address can all register."""
    code, phrase = devices.create_code(session, max_uses=50, lifetime=timedelta(days=1))
    for _ in range(devices.ATTEMPTS.amount * 2):
        devices.register(session, phrase, ADDRESS)
    assert code.uses == devices.ATTEMPTS.amount * 2


@pytest.mark.usefixtures("restore_logging")
def test_the_request_line_carries_the_device(
    client: TestClient, phrase: str, capsys: pytest.CaptureFixture[str]
) -> None:
    device_id = client.post("/api/devices", json={"code": phrase}).json()["device_id"]
    configure_logging(
        Settings(database_url="postgresql://db/x", logging=LogConfig(format="json"))
    )
    client.get("/api/whoami")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    (request,) = [line for line in lines if line["event"] == "request"]
    assert request["device_id"] == device_id


def test_last_seen_is_not_history(
    client: TestClient, session: Session, phrase: str
) -> None:
    device_id = client.post("/api/devices", json={"code": phrase}).json()["device_id"]
    change = update(Device).where(Device.id == device_id)
    session.execute(change.values(last_seen_at=datetime.now(UTC) - timedelta(days=2)))
    session.execute(change.values(label="Store tablet"))
    assert device_history(session, device_id) == ["insert", "update"]


def test_too_many_attempts_are_a_429(client: TestClient) -> None:
    for _ in range(devices.ATTEMPTS.amount):
        client.post("/api/devices", json={"code": "wrong-guess"})
    response = client.post("/api/devices", json={"code": "wrong-guess"})
    assert (response.status_code, response.json()) == (429, {"reason": "rate_limited"})


def test_an_unregistered_device_is_refused(client: TestClient) -> None:
    response = client.get("/api/whoami")
    assert (response.status_code, response.json()) == (403, {"reason": "unregistered"})
    client.cookies.set(COOKIE, "made-up")
    response = client.get("/api/whoami")
    assert response.request.headers["cookie"] == f"{COOKIE}=made-up"
    assert response.json() == {"reason": "unregistered"}


def test_revoking_a_device_refuses_its_next_request(
    client: TestClient, session: Session, phrase: str
) -> None:
    """The cookie alone is never enough: the row is read on every request."""
    device_id = client.post("/api/devices", json={"code": phrase}).json()["device_id"]
    assert client.get("/api/whoami").status_code == 200
    assert devices.revoke_device(session, device_id)
    response = client.get("/api/whoami")
    assert (response.status_code, response.json()) == (403, {"reason": "revoked"})


def test_revoking_a_code_leaves_its_devices_registered(
    client: TestClient, session: Session, phrase: str
) -> None:
    device_id = client.post("/api/devices", json={"code": phrase}).json()["device_id"]
    device = session.get(Device, device_id)
    assert device is not None
    assert devices.revoke_code(session, device.registration_code_id)
    assert client.get("/api/whoami").status_code == 200
    rejected = client.post("/api/devices", json={"code": phrase})
    assert rejected.json() == {"reason": "revoked"}


def test_a_codes_devices_show_how_long_before_revocation_they_came(
    session: Session,
) -> None:
    code, phrase = devices.create_code(session, max_uses=5, lifetime=timedelta(days=1))
    early, _ = devices.register(session, phrase, ADDRESS)
    late, _ = devices.register(session, phrase, ADDRESS)
    now = datetime.now(UTC)
    for device, minutes in ((early, 60), (late, 3)):
        session.execute(
            update(Device)
            .where(Device.id == device.id)
            .values(created_at=now - timedelta(minutes=minutes))
        )
    standing = devices.devices_registered_with(session, code.id)
    assert standing is not None
    assert [b for _, b in standing] == [None, None]
    assert devices.revoke_code(session, code.id)
    session.expire_all()
    listed = devices.devices_registered_with(session, code.id)
    assert listed is not None
    assert [device.id for device, _ in listed] == [late.id, early.id]
    (_, late_before), (_, early_before) = listed
    assert late_before is not None
    assert early_before is not None
    assert late_before < timedelta(minutes=4) < timedelta(minutes=59) < early_before


def test_last_seen_moves_at_most_hourly_even_when_nothing_is_saved(
    client: TestClient,
    engine: Engine,
    session: Session,
    committed_cookie: tuple[int, str],
) -> None:
    device_id, token = committed_cookie
    long_ago = datetime.now(UTC) - timedelta(days=2)
    with Session(engine) as other, other.begin():
        other.execute(
            update(Device).where(Device.id == device_id).values(last_seen_at=long_ago)
        )
    client.cookies.set(COOKIE, token)
    assert client.get("/api/whoami").status_code == 200
    with Session(engine) as other:
        seen = other.scalar(select(Device.last_seen_at).where(Device.id == device_id))
    assert seen is not None
    assert seen > long_ago + timedelta(days=1)


def test_revocations_are_in_the_history(session: Session, phrase: str) -> None:
    device, _ = devices.register(session, phrase, ADDRESS)
    devices.revoke_device(session, device.id)
    assert device_history(session, device.id) == ["insert", "update"]


@pytest.mark.parametrize("bad", [["--uses", "0"], ["--days", "-1"]])
def test_the_command_line_refuses_codes_that_could_never_register(
    bad: list[str],
) -> None:
    with pytest.raises(SystemExit):
        registration_codes.main(["create", *bad])


def test_the_command_line_says_when_a_code_does_not_exist(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert registration_codes.main(["devices", "999999"]) == 1


@pytest.mark.usefixtures("committed")
def test_the_command_line_makes_a_code_that_registers(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    session: Session,
) -> None:
    """The command commits its code; `committed` deletes it after, once
    `session`, set up after it, has rolled back and let go of the code."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert (
        registration_codes.main(
            ["create", "--uses", "1", "--base-url", "https://inventory.example/"]
        )
        == 0
    )
    out = capsys.readouterr().out
    phrase = next(
        line.removeprefix("Phrase: ") for line in out.splitlines() if "Phrase:" in line
    )
    assert f"https://inventory.example/register?code={phrase}" in out
    device, _ = devices.register(session, phrase, ADDRESS)
    assert device.id
