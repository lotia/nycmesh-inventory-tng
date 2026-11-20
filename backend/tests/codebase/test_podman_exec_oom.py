"""Every compose.yaml service with a health check works around Podman's
exec bug (CONTRIBUTING.md, Podman's exec bug): Podman runs a health check as
an exec, so without it the service is never healthy under rootless Podman
with crun 1.31 and later (inventory-tng-46w)."""

import pytest

from helpers.compose import services

pytestmark = pytest.mark.clone


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
