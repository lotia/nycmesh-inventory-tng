"""Every compose.yaml service with a health check works around Podman's
exec bug (CONTRIBUTING.md, Podman's exec bug): Podman runs a health check as
an exec, so without it the service is never healthy under rootless Podman
with crun 1.31 and later (inventory-tng-46w)."""

import re

import pytest

from inventory_tng.local_env import CLONE

pytestmark = pytest.mark.clone

# A service: its name two spaces in, up to the next line as little indented.
SERVICE = re.compile(r"^  ([\w-]+):\n(.*?)(?=^ {0,2}\S|\Z)", re.MULTILINE | re.DOTALL)


def services() -> dict[str, str]:
    text = (CLONE / "compose.yaml").read_text()
    listed = text.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    return dict(SERVICE.findall(listed))


def test_compose_lists_its_services() -> None:
    assert {"db", "app", "webkit"} <= set(services())


def test_each_service_with_a_health_check_sets_oom_score_adj() -> None:
    checked = {
        name for name, body in services().items() if "\n    healthcheck:" in body
    }
    worked_around = {
        name
        for name, body in services().items()
        if "\n    oom_score_adj: 200\n" in body
    }
    assert checked
    assert checked - worked_around == set()
