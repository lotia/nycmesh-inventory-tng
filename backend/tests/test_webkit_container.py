"""compose.yaml's webkit service (mise run browser-webkit) runs the
Playwright that frontend/package-lock.json pins: a client and server of
different versions refuse each other."""

import json

import pytest

from inventory_tng.local_env import CLONE

pytestmark = pytest.mark.clone


def test_the_webkit_server_is_the_tests_playwright() -> None:
    lock = json.loads((CLONE / "frontend" / "package-lock.json").read_text())
    version = lock["packages"]["node_modules/@playwright/test"]["version"]
    compose = (CLONE / "compose.yaml").read_text()
    assert f"image: mcr.microsoft.com/playwright:v{version}-noble\n" in compose
    assert f" playwright@{version}," in compose
