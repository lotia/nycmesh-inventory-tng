"""The ceiling on a connect, what it refuses, and the URL's right to overrule it.

Three things are asserted and they pull against each other. There is always a
ceiling, because a deployment nobody configured is the one this protects; a
`DATABASE_URL` that states its own is obeyed, because a value somebody wrote
down being silently replaced is worse than either number; and neither source
may supply a figure the driver would read as no ceiling at all.

The figure itself is pinned across every file that quotes it. Four of them do,
and none of them can see the others, so a change made in one and forgotten in
the rest is exactly what this catches -- the pattern is `test_debugging.py`'s,
for the same reason.
"""

from typing import Any

import pytest
from django.conf import settings

from inventory_tng.database import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    MINIMUM_CONNECT_TIMEOUT_SECONDS,
    bounded,
    configured,
)
from inventory_tng.settings import env

URL = "postgres://inventory:inventory@localhost:5432/inventory_tng"


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process told where the database is and nothing else about it."""
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("DATABASE_CONNECT_TIMEOUT_SECONDS", raising=False)


def test_five_seconds_is_what_nobody_configuring_it_gets(unconfigured: None) -> None:
    """Read through `settings.py`'s own declaration, not a second one here."""
    assert configured(env)["OPTIONS"]["connect_timeout"] == 5
    assert DEFAULT_CONNECT_TIMEOUT_SECONDS == 5


def test_the_variable_moves_it(unconfigured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "8")

    assert configured(env)["OPTIONS"]["connect_timeout"] == 8


def test_a_variable_saying_nothing_is_a_variable_nobody_set(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decision 0022, which every other setting here is read under."""
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "   ")

    assert configured(env)["OPTIONS"]["connect_timeout"] == 5


def test_what_the_url_asked_for_is_still_asked_for(unconfigured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """`django-environ` renders a query string as OPTIONS, and this adds to it.

    A managed PostgreSQL is commonly reached with `sslmode` in the URL and
    nothing else configuring it, so replacing that dict rather than adding to
    it would turn TLS off on the deployments most likely to need it.
    """
    monkeypatch.setenv("DATABASE_URL", f"{URL}?sslmode=require")

    assert configured(env)["OPTIONS"] == {"sslmode": "require", "connect_timeout": 5}


def test_a_url_naming_its_own_ceiling_keeps_it(unconfigured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The particular instruction beats the general one, both ways round."""
    monkeypatch.setenv("DATABASE_URL", f"{URL}?connect_timeout=20")
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "9")

    assert configured(env)["OPTIONS"]["connect_timeout"] == 20


def test_nothing_else_about_the_connection_is_touched() -> None:
    config: dict[str, Any] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "inventory_tng",
        "HOST": "postgres",
        "PORT": 5432,
    }

    assert bounded(config, 5) == {**config, "OPTIONS": {"connect_timeout": 5}}


def test_a_database_that_is_not_postgresql_is_left_alone() -> None:
    """`connect_timeout` is libpq's word, and sqlite raises on a keyword it has not got.

    Nothing constrains `DATABASE_URL` to the postgres family -- the convention
    is a convention -- so a URL naming another engine has to pass through
    rather than be handed an option that engine cannot take.
    """
    config: dict[str, Any] = {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/inventory.db"}

    assert bounded(config, 5) == config


@pytest.mark.parametrize("refused", ["0", "-3", "1"])
def test_a_ceiling_the_driver_would_ignore_stops_the_process(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch, refused: str
) -> None:
    """The direction that quietly removes the bound rather than tightening it.

    All three are below the floor the constant argues, so all three describe a
    deployment whose operator believes they have a ceiling and has not got one.
    """
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", refused)

    with pytest.raises(ValueError, match="DATABASE_CONNECT_TIMEOUT_SECONDS"):
        configured(env)


def test_a_url_may_not_remove_the_ceiling_either(unconfigured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same refusal on the path that overrules the variable.

    `django-environ` converts a query parameter to an int only where every
    character is a digit, so a signed one arrives as a string -- and reaches
    the driver meaning the same "for ever" that nought does.
    """
    monkeypatch.setenv("DATABASE_URL", f"{URL}?connect_timeout=-3")

    with pytest.raises(ValueError, match="DATABASE_URL"):
        configured(env)


def test_a_ceiling_that_is_not_a_number_says_which_setting_it_was(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise this is `CrashLoopBackOff` over a bare `int()` traceback.

    A chart renders `5.5` as happily as `5`, so the value arrives and the
    process stops during settings import, with nothing in the message to say
    which of thirty variables was mistyped.
    """
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT_SECONDS", "5.5")

    with pytest.raises(ValueError, match="DATABASE_CONNECT_TIMEOUT_SECONDS"):
        configured(env)


def test_the_running_process_bounds_its_own_connection() -> None:
    """That `settings.py` calls any of the above, rather than only importing it.

    The figure is not asserted: a developer is free to put another one in
    `.env`, and a suite that failed on that would be asserting a preference
    rather than a behaviour.
    """
    assert "connect_timeout" in settings.DATABASES["default"]["OPTIONS"]


def test_every_file_that_ships_the_figure_ships_the_same_one() -> None:
    """Four files quote it and none can see the others.

    Changing the constant and the assertions above is a green suite over a
    `.env.sample` telling a volunteer something else, a compose stack running
    something else, and a chart shipping something else to a cluster.
    """
    figure = DEFAULT_CONNECT_TIMEOUT_SECONDS

    assert f"DATABASE_CONNECT_TIMEOUT_SECONDS={figure}" in (settings.REPO_ROOT / ".env.sample").read_text()
    assert f"DATABASE_CONNECT_TIMEOUT_SECONDS:-{figure}" in (settings.REPO_ROOT / "compose.yaml").read_text()
    assert (
        f"databaseConnectTimeoutSeconds: {figure}"
        in (settings.REPO_ROOT / "infra" / "helm" / "inventory-tng" / "values.yaml").read_text()
    )
    assert f"Default `{figure}`" in (settings.REPO_ROOT / "docs" / "deployment.md").read_text()


def test_the_floor_is_the_one_the_driver_imposes() -> None:
    """Named where the refusals are argued, and quoted to a deployer here."""
    assert MINIMUM_CONNECT_TIMEOUT_SECONDS == 2
    assert "anything under two stops the process" in (settings.REPO_ROOT / ".env.sample").read_text()
