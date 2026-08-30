"""An empty variable means the same as an absent one, for everything.

The defect this holds against is invisible in the file that causes it. A
developer clears a value in `.env` rather than deleting the line; a values file
blanks a chart setting; and the application then refuses to start with a
traceback naming neither. `.env.sample` ships several variables empty, so the
shape is one this project teaches.

The direction that matters most is that one's opposite: a setting with no
default gets *stricter*, not softer.

The last group is the other reading `Env` corrects, and it is invisible for the
same reason: a list written with a space after the comma. Held against `Env`
rather than against the two variables that had the bug, because what has to
stay true is about a variable somebody declares later.
"""

import os
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from inventory_tng.environment import Env, Speaking
from inventory_tng.options import missing


@pytest.mark.parametrize("value", ["", "   ", "\t", "\n", None])
def test_a_variable_that_says_nothing_is_missing(value: str | None) -> None:
    assert missing(value) is True


@pytest.mark.parametrize("value", ["0", "false", " x ", "INFO"])
def test_and_anything_that_says_something_is_not(value: str) -> None:
    """`0` and `false` say something. Only whitespace says nothing."""
    assert missing(value) is False


def test_a_cleared_value_falls_back_to_the_declared_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """`django-environ` applies a default only when a variable is ABSENT.

    Which is what made `NUM_PROXIES=` in a `.env`, or `numProxies: ""` in a
    values file, an `int("")` during settings import and a pod that would not
    start.
    """
    monkeypatch.setenv("A_COUNT", "")

    assert Env(A_COUNT=(int, 2))("A_COUNT") == 2


def test_whitespace_counts_as_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trailing space in a ConfigMap is not a value anybody meant to set."""
    monkeypatch.setenv("A_NAME", "   ")

    assert Env(A_NAME=(str, "fallback"))("A_NAME") == "fallback"


def test_a_value_that_is_set_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The leniency must not reach past the case it is for."""
    monkeypatch.setenv("A_COUNT", "9")

    assert Env(A_COUNT=(int, 2))("A_COUNT") == 9


def test_a_falsy_value_is_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """`0` and `false` are the answers somebody went out of their way to give.

    A leniency that swallowed them would be worse than the defect it fixes.
    """
    monkeypatch.setenv("A_COUNT", "0")
    monkeypatch.setenv("A_FLAG", "false")

    assert Env(A_COUNT=(int, 2))("A_COUNT") == 0
    assert Env(A_FLAG=(bool, True))("A_FLAG") is False


def test_a_setting_with_no_default_gets_stricter_rather_than_softer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of `DJANGO_SECRET_KEY` having no default is that a
    missing one stops the process rather than starting it insecurely. An empty
    one used to slip through and sign sessions with nothing.
    """
    monkeypatch.setenv("A_SECRET", "")

    with pytest.raises(ImproperlyConfigured):
        Env()("A_SECRET")


def test_an_empty_variable_does_not_appear_in_the_environment_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Membership has to agree with lookup, not only lookup.

    `read_env` decides whether to take a value from `.env` by asking whether
    one is there already, so a view that hid an empty variable from `[]` and
    not from `in` would answer the same question two ways.
    """
    monkeypatch.setenv("A_BLANK", "")
    monkeypatch.setenv("A_SET", "yes")
    view = Speaking(os.environ)

    assert "A_BLANK" not in view
    assert "A_SET" in view

    with pytest.raises(KeyError):
        view["A_BLANK"]


def test_the_view_offers_only_what_reads_it_actually_uses() -> None:
    """Not a `MutableMapping`, on purpose.

    `Speaking`'s own docstring says which three operations it offers and why
    inheriting a wider contract would have been worse than useless.
    """
    view = Speaking(os.environ)

    assert not hasattr(view, "pop")
    assert not hasattr(view, "clear")
    assert not hasattr(view, "popitem")


def test_a_dotenv_file_does_not_overrule_a_variable_cleared_in_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Speaking.setdefault` says why this needs an override of its own.

    Without one, `OTEL_EXPORTER_OTLP_ENDPOINT= manage.py runserver` quietly
    takes the endpoint from `.env` regardless.
    """
    monkeypatch.setenv("A_CLEARED", "")
    monkeypatch.setenv("A_SET", "from-shell")
    view = Speaking(os.environ)

    assert view.setdefault("A_CLEARED", "from-dotenv") == ""
    assert os.environ["A_CLEARED"] == "", "the shell said nothing, deliberately"
    assert view.setdefault("A_SET", "from-dotenv") == "from-shell"
    assert view.setdefault("A_ABSENT", "from-dotenv") == "from-dotenv"


def test_surrounding_whitespace_is_not_part_of_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kubectl create secret --from-file` leaves the file's trailing newline
    in the value, and decision 0022 says what that then costs.
    """
    monkeypatch.setenv("A_SECRET", "s3cret\n")

    assert Env()("A_SECRET") == "s3cret"


def test_the_space_after_a_comma_is_not_part_of_a_list_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """`django-environ` splits on commas and does nothing else.

    So `"a, b"` -- the way anybody writes a list of two -- was `[" b"]` for the
    second entry, matching nothing for ever, and the space did not show in the
    file that caused it.
    """
    monkeypatch.setenv("A_LIST", "first, second ,third")

    assert Env(A_LIST=(list, []))("A_LIST") == ["first", "second", "third"]


def test_a_trailing_comma_adds_no_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry matching nothing is a line in a list that means nothing."""
    monkeypatch.setenv("A_LIST", "first, ,second,")

    assert Env(A_LIST=(list, []))("A_LIST") == ["first", "second"]


def test_an_origin_written_with_a_space_is_still_that_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """The variable this was reported against, under its own name.

    `Env` never sees a variable's name, so this runs what the test above it
    runs. It is here because the report was about origins and somebody looking
    for that should find it; what pins the real declaration is the last test in
    this file.
    """
    monkeypatch.setenv("CSRF_TRUSTED_ORIGINS", "http://localhost:5173, https://inventory.nycmesh.net")

    assert Env(CSRF_TRUSTED_ORIGINS=(list, []))("CSRF_TRUSTED_ORIGINS") == [
        "http://localhost:5173",
        "https://inventory.nycmesh.net",
    ]


def test_a_cast_that_is_not_a_list_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """`entries` is for the `(list, [])` shape and no other.

    A `dict` cast splits on commas too, and trimming its halves here would be
    this repository deciding something about a variable it does not have.
    """
    monkeypatch.setenv("A_MAP", "one=1,two=2")

    assert Env(A_MAP=(dict, {}))("A_MAP") == {"one": "1", "two": "2"}


def test_every_list_the_settings_declare_is_read_through_that_one_place() -> None:
    """The criterion is the mechanism rather than the variable that had the bug.

    Read off the source, because what has to hold is about a declaration
    somebody adds later: a list declared on `env` is trimmed by
    `Env.parse_value` without their knowing it happened.

    WHAT THIS CANNOT SEE, said because the assertion reads as though it could.
    A list parsed somewhere else entirely -- `os.environ["X"].split(",")` --
    has no declaration here, so it is not in `declared` and nothing below looks
    at it. What is held is that every declared list is read back through `env`
    and therefore through the trim, which is the shape `settings.py` uses and
    the one a new variable is copied from.
    """
    source = (Path(settings.BASE_DIR) / "inventory_tng" / "settings.py").read_text()
    declared = re.findall(r"^\s*(\w+)=\(list, \[\]\),$", source, re.MULTILINE)

    assert "CSRF_TRUSTED_ORIGINS" in declared
    assert "DJANGO_ALLOWED_HOSTS" in declared
    for name in declared:
        assert f'env("{name}")' in source, name
