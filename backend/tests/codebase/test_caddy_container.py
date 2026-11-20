"""compose.yaml's caddy service (mise run phone-lan) publishes only the
app container, which serves the build, and only on the address the task
sets, else 127.0.0.1: never Vite's dev server or the database."""

import re

import pytest

from inventory_tng.core.local_env import CLONE

from helpers.compose import services

pytestmark = pytest.mark.clone


def test_caddy_publishes_one_port_on_the_local_address_or_loopback() -> None:
    caddy = services()["caddy"]
    assert "    profiles: [lan]\n" in caddy
    assert re.findall(r'^ +- "(.*:\d+)"$', caddy, re.MULTILINE) == [
        "${INVENTORY_LAN_ADDRESS:-127.0.0.1}:${INVENTORY_LAN_PORT:-8443}:8443"
    ]


def test_caddy_sends_on_only_to_the_app_container() -> None:
    text = (CLONE / "compose.yaml").read_text()
    assert re.findall(r"^ +reverse_proxy (.*)$", text, re.MULTILINE) == ["app:8000"]
    assert 'VITE_DEV_SERVER: ""' in services()["app"]
