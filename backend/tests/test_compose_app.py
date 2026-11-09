"""compose.yaml's app container takes the settings .env.example names, and
only those, listed one by one (inventory-tng-zr5), never the whole .env."""

import re

import pytest

from inventory_tng.local_env import CLONE

pytestmark = pytest.mark.clone

# A setting, set or commented out: `NAME=` or `# NAME=`.
SETTING = re.compile(r"^#? ?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
# The app's own environment: its keys, six spaces in.
LISTED = re.compile(r"^ {6}([A-Z][A-Z0-9_]*):", re.MULTILINE)


def app_service() -> str:
    """The app service, up to the next line as little indented."""
    after = (CLONE / "compose.yaml").read_text().split("\n  app:\n", 1)[1]
    return re.split(r"^ {0,2}\S", after, maxsplit=1, flags=re.MULTILINE)[0]


def test_the_app_takes_the_settings_env_example_names() -> None:
    named = set(SETTING.findall((CLONE / ".env.example").read_text()))
    # INVENTORY_ settings are compose's and mise's, for the host; the
    # container sets FRONTEND_DIST itself, to the bundle it built.
    for_the_app = {name for name in named if not name.startswith("INVENTORY_")}
    assert set(LISTED.findall(app_service())) == for_the_app | {"FRONTEND_DIST"}


def test_the_app_is_not_given_the_whole_env() -> None:
    assert not re.search(r"^ {4}env_file:", app_service(), re.MULTILINE)
