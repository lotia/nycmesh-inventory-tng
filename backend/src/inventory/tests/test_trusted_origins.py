"""The list Django consults when the origin it computed is not the one claimed.

`inventory-tng-o1uj.6` was a write refused because nginx handed Django a host
without its port, so Django rebuilt an origin the browser had not sent and
refused every POST, PUT, PATCH and DELETE in the application. Correcting the
proxy fixed the arrangement this repository ships. It did nothing for any other
-- an ingress that rewrites the host, a tunnel, a name a pod does not know
itself by -- and there was no configuration to answer those with, because the
setting Django provides for exactly this was not read anywhere.

So what is asserted here is that the knob exists, reaches Django, is empty in
everything shipped, and is rendered by the chart. Whether a given deployment
needs it is that deployment's business; having no way to say so was the defect.
"""

import importlib

import pytest
from django.conf import settings
from environ import Env

from inventory.tests.charts import rendered
from inventory_tng.environment import entries

SETTING = "CSRF_TRUSTED_ORIGINS"
# The two files that decide what a stack actually runs with, as opposed to what
# the code would fall back to.
SHIPPED = ("compose.yaml", ".env.sample")


def test_nothing_is_admitted_that_django_did_not_work_out_for_itself() -> None:
    """The resting state, which every shipped configuration agrees with."""
    assert settings.CSRF_TRUSTED_ORIGINS == [], (
        "nothing this repository ships needs an origin admitted on top of the one Django computes, so the "
        "default is empty; a non-empty default would widen what is accepted for every deployment at once"
    )


def test_what_the_environment_says_is_what_the_setting_becomes() -> None:
    """Declared is not the same as read, and only this tells them apart.

    A schema entry with nothing consuming it leaves Django's own empty default
    in place, so every other assertion in this module still passes while the
    variable does nothing at all -- which is precisely the state the repository
    was in before this landed. Setting it and re-reading the module is what
    distinguishes the two.

    The module is re-read rather than `override_settings`, because what is
    under test is the reading. `django.conf.settings` holds values captured at
    setup and is deliberately not consulted here; the reload at the end puts
    the module back for whatever runs next.
    """
    origins = "https://ingress.invalid,http://localhost:9999"
    module = importlib.import_module("inventory_tng.settings")

    try:
        with pytest.MonkeyPatch.context() as patched:
            patched.setenv(SETTING, origins)
            read = importlib.reload(module).CSRF_TRUSTED_ORIGINS
    finally:
        importlib.reload(module)

    assert read == ["https://ingress.invalid", "http://localhost:9999"], (
        f"{SETTING} is declared but nothing consumes it, so Django keeps its own empty default and a "
        f"deployment that sets the variable is quietly ignored; the module read {read}"
    )


@pytest.mark.parametrize("shipped", SHIPPED)
def test_every_shipped_configuration_says_so_rather_than_implying_it(shipped: str) -> None:
    """Decision 0021 point 11's pattern, applied to this.

    The safe value belongs in the code and the intended one in every file, so
    that what runs is what somebody wrote down rather than what the code fell
    back to. Here the two happen to agree, which is exactly when it is easiest
    to leave the file silent and hardest to notice later that it was.
    """
    text = (settings.REPO_ROOT / shipped).read_text()

    assert SETTING in text, (
        f"{shipped} does not mention {SETTING}, so its value is whatever the code defaults to rather than "
        "something this repository chose. The next person to meet a refused write reads these files"
    )


def test_the_chart_renders_it_and_leaves_it_empty() -> None:
    """A cluster is the one place that plausibly needs this, so the chart carries it."""
    supplied = rendered()

    assert SETTING in supplied, (
        f"the chart does not put {SETTING} in the backend's environment, so the one kind of deployment that "
        "actually meets a rewritten host has no way to answer it without editing the chart"
    )
    assert entries(Env.parse_value(supplied[SETTING]["value"], list)) == [], (
        "the chart ships a non-empty trusted-origin list, which widens what every release accepts rather "
        "than what one release needed"
    )


def test_what_a_deployment_sets_is_what_django_would_read() -> None:
    """The chart's value and Django's parsing, held to one answer.

    Rendered and then parsed the way the pod would, rather than compared as
    strings: a list separated one way in the chart and split another way by
    `Env` is a bug that string equality cannot see. The same reasoning
    `hosts.py` gives about ALLOWED_HOSTS, for the same reason.
    """
    # Escaped, because `helm --set` reads a bare comma as its own list
    # separator and would refuse the value outright. What reaches the chart,
    # and the pod, is one string with a real comma in it -- which is the shape
    # an operator setting two origins actually produces.
    origins = r"https://inventory.nycmesh.net\, http://localhost:8080"
    supplied = rendered(**{"django.csrfTrustedOrigins": origins})

    read = entries(Env.parse_value(supplied[SETTING]["value"], list))

    assert read == ["https://inventory.nycmesh.net", "http://localhost:8080"], (
        f"the chart renders {SETTING} in a shape Django does not split back into the origins that were "
        f"asked for; it read {read}"
    )
