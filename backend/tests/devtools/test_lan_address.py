"""devtools/lan_address.py: the address mise run phone-lan serves on, the
setting's or the routed one, never one on the internet."""

import pytest

from devtools import lan_address
from devtools.__main__ import main as run_devtools

KEY = lan_address.KEY


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The routed address, as the test sets it; no setting in the shell or
    .env."""
    address = ["192.168.1.20"]
    monkeypatch.setattr(lan_address, "routed_address", lambda: address[0])
    monkeypatch.setattr(lan_address, "setting", lambda _: "")
    return address


def test_it_prints_the_routed_address(
    routed: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_devtools(["lan-address"]) == 0
    assert capsys.readouterr().out == f"{routed[0]}\n"


@pytest.mark.usefixtures("routed")
def test_the_setting_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan_address, "setting", lambda key: {KEY: "10.0.0.5"}[key])
    assert lan_address.lan_address() == "10.0.0.5"


@pytest.mark.parametrize(
    ("address", "refusal"),
    [
        ("8.8.8.8", "8.8.8.8 is not a local network's address"),
        ("0.0.0.0", "0.0.0.0 is not a local network's address"),
        ("fd00::1", f"{KEY} must be an IPv4 address"),
        ("laptop.local", f"{KEY} must be an IPv4 address"),
    ],
)
def test_an_address_on_the_internet_or_not_ipv4_is_refused(
    routed: list[str], address: str, refusal: str
) -> None:
    routed[0] = address
    with pytest.raises(SystemExit, match=refusal):
        lan_address.lan_address()


def test_the_routed_address_is_the_socket_s_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connected, the socket's own address is the route's; here, with the
    connection stubbed out, none."""
    monkeypatch.setattr(lan_address.socket.socket, "connect", lambda *_: None)
    assert lan_address.routed_address() == "0.0.0.0"


def test_a_machine_with_no_route_says_to_set_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(*_: object) -> None:
        raise OSError("Network is unreachable")

    monkeypatch.setattr(lan_address.socket.socket, "connect", unreachable)
    with pytest.raises(
        SystemExit, match=f"No network to find an address on; set {KEY}"
    ):
        lan_address.routed_address()
