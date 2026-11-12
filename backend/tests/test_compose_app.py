"""compose.yaml's app container takes the settings .env.example names, and
only those, listed one by one (inventory-tng-zr5), never the whole .env; and
its database's settings name no checkout's path, its init scripts coming
from configs (inventory-tng-0c2)."""

import re

import pytest

from inventory_tng.dev_database import OWNER, ROLES_SQL
from inventory_tng.local_env import CLONE

pytestmark = pytest.mark.clone

# A setting, set or commented out: `NAME=` or `# NAME=`.
SETTING = re.compile(r"^#? ?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
# The app's own environment: its keys, six spaces in.
LISTED = re.compile(r"^ {6}([A-Z][A-Z0-9_]*):", re.MULTILINE)


def service(name: str) -> str:
    """The service `name`, up to the next line as little indented."""
    after = (CLONE / "compose.yaml").read_text().split(f"\n  {name}:\n", 1)[1]
    return re.split(r"^ {0,2}\S", after, maxsplit=1, flags=re.MULTILINE)[0]


def test_the_app_takes_the_settings_env_example_names() -> None:
    named = set(SETTING.findall((CLONE / ".env.example").read_text()))
    # INVENTORY_ settings are compose's and mise's, for the host; the
    # container sets FRONTEND_DIST itself, to the bundle it built.
    for_the_app = {name for name in named if not name.startswith("INVENTORY_")}
    assert set(LISTED.findall(service("app"))) == for_the_app | {"FRONTEND_DIST"}


def test_the_app_is_not_given_the_whole_env() -> None:
    assert not re.search(r"^ {4}env_file:", service("app"), re.MULTILINE)


def test_the_database_has_only_its_named_volume() -> None:
    """A bind mount would put the path of the checkout that made the
    container in its hash, so every other checkout's start-database would
    find it differs."""
    after = service("db").split("\n    volumes:\n", 1)[1]
    volumes = re.split(r"^ {4}\S", after, maxsplit=1, flags=re.MULTILINE)[0]
    assert re.findall(r"^ +- *(.*)$", volumes, re.MULTILINE) == [
        "db:/var/lib/postgresql"
    ]


def test_the_database_init_scripts_are_the_roles_file_and_the_owners() -> None:
    """The roles first, from the one file dev_database runs too, then the
    database the server makes, made the owner's."""
    text = (CLONE / "compose.yaml").read_text()
    db = service("db")
    owners = ROLES_SQL.with_name("database-owner.sql")
    for name, script, file in (
        ("roles", "1-roles.sql", ROLES_SQL),
        ("database-owner", "2-database-owner.sql", owners),
    ):
        assert (
            f"- source: {name}\n        target: /docker-entrypoint-initdb.d/{script}\n"
            in db
        )
        assert f"\n  {name}:\n    file: ./{file.relative_to(CLONE)}\n" in text
    (made,) = re.findall(r"^ +POSTGRES_DB: (\S+)$", db, re.MULTILINE)
    assert f"ALTER DATABASE {made} OWNER TO {OWNER};" in owners.read_text()
