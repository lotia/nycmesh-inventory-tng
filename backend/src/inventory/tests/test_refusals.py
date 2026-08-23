"""What a refused request may write, and what it may not.

The defect these hold against arrived with the fix for the one
`inventory/tests/test_logging.py` describes. Giving `django.security` somewhere
to go made a refused `Host` visible; it also made it about 1.4 KB, nine frames
of traceback, on a path that is internet-facing, takes no credential and had
nothing rate limiting it. A host scanner is then a way to fill a node's disk.

`inventory_tng.refusals` is the argument for each half. These are the
assertions that the halves are true, and the first of them drives a real
request through a real Django rather than logging by hand -- because whether
Django still attaches the exception to that record is exactly the sort of thing
an upgrade changes underneath a filter that assumes it does.
"""

import logging
import logging.config
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from pytest_django.fixtures import Settings

from inventory.tests.helpers import applied
from inventory.tests.helpers import every_record as records
from inventory_tng import refusals
from inventory_tng.logs import from_environment, logging_config
from inventory_tng.options import DEFAULTS


def refuse(logger: str = "django.security.DisallowedHost", message: str = "Invalid HTTP_HOST header") -> None:
    """One refusal, logged the way Django logs one: at ERROR, with the exception."""
    try:
        raise ValueError(message)
    except ValueError as exception:
        logging.getLogger(logger).error(message, exc_info=exception)


# --------------------------------------------------------------------------
# The traceback, which says nothing the message does not
# --------------------------------------------------------------------------


def test_a_real_refused_host_is_named_and_carries_no_traceback(settings: Settings) -> None:
    """End to end, because both halves of this are Django's behaviour.

    That the hostname is in the message is what inventory-tng-adj needed and
    is the thing that must survive. That the nine frames behind it are the
    same three Django functions every time is why they need not.
    """
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["testserver"]

    with applied(logging_config("INFO", "json")) as stream:
        response = Client().get("/api/healthz/", headers={"host": "scanner.example"})

    assert response.status_code == 400
    # One refusal, one refusal record. The request writes its own alongside
    # it -- `inventory_tng.context` -- which is a different thing.
    refusals_written = [held for held in records(stream) if held["logger"].startswith("django.security.")]
    assert len(refusals_written) == 1
    refusal = refusals_written[0]

    assert refusal["logger"].startswith("django.security.")
    assert refusal["level"] == "error", "the volume was never in the level"
    assert "scanner.example" in refusal["event"], "the hostname is the whole point of the record"
    assert "exception" not in refusal, "nine frames of Django saying what the message already said"


def test_an_application_error_keeps_every_frame_it_had() -> None:
    """The narrowness is the point: this rations one family and nothing else.

    A traceback from `inventory.*` was written by somebody on purpose and is
    the failure the epic exists to make visible.
    """
    with applied(logging_config("INFO", "json")) as stream:
        refuse(logger="inventory.ledger", message="a real failure")

    assert "Traceback (most recent call last)" in records(stream)[0]["exception"]


def refusal(logger: str) -> logging.LogRecord:
    return logging.LogRecord(logger, logging.ERROR, "f", 1, "m", None, None)


def test_a_logger_merely_beginning_with_the_family_name_is_not_in_it() -> None:
    """`django.securityfoo` is not `django.security.Anything`."""
    bounded = refusals.Bounded(count=1, window=60)

    for name in ("django.securityfoo", "django.securit", "notdjango.security"):
        assert bounded.filter(refusal(name)) is True

    assert bounded.filter(refusal("django.security.X")) is True
    assert bounded.filter(refusal("django.security.X")) is False


def test_a_flood_on_one_logger_does_not_starve_another() -> None:
    """Django puts more than one thing under this family, and the two that
    matter -- a CSRF failure, a session it could not decode -- say somebody's
    session is broken. A host scanner must not be able to spend their window.
    """
    bounded = refusals.Bounded(count=2, window=60)

    scanning = [bounded.filter(refusal("django.security.DisallowedHost")) for _ in range(20)]

    assert scanning.count(True) == 2
    assert bounded.filter(refusal("django.security.SuspiciousSession")) is True


# --------------------------------------------------------------------------
# The rate, which is the half stripping the traceback does not touch
# --------------------------------------------------------------------------


def test_beyond_the_rate_a_refusal_is_counted_rather_than_written() -> None:
    """Seven times less of an unbounded number is still unbounded."""
    with applied(logging_config("INFO", "json", security_rate="3/min")) as stream:
        for _ in range(50):
            refuse()

    assert len(records(stream)) == 3


def a_minute_passes(bounded: refusals.Bounded, logger: str = "django.security.X") -> None:
    """Without a test that takes one."""
    bounded._windows[logger].opened -= bounded.window


def test_the_next_one_written_says_how_many_were_not() -> None:
    """The half that makes the limit honest rather than merely quiet.

    `refusals` argues why a dropped record is counted; this is the assertion
    that the count arrives.
    """
    config = logging_config("INFO", "json", security_rate="2/min")

    with applied(config) as stream:
        for _ in range(40):
            refuse()
        bounded = logging.getLogger("django").handlers[0].filters[0]
        assert isinstance(bounded, refusals.Bounded)
        a_minute_passes(bounded, "django.security.DisallowedHost")
        refuse()

    written = records(stream)
    assert len(written) == 3
    assert "suppressed" not in written[0]
    assert written[2]["suppressed"] == 38
    assert written[2]["suppressed_since"], "an instant, because the summary can be long after the flood"
    assert written[2]["suppressed_since"] <= written[2]["timestamp"], "counting began before it was reported"


def test_the_count_is_reported_once_and_then_starts_again() -> None:
    bounded = refusals.Bounded(count=1, window=60)
    record = refusal("django.security.X")

    assert bounded.filter(record) is True
    assert bounded.filter(record) is False
    assert bounded.filter(record) is False

    a_minute_passes(bounded)
    assert bounded.filter(record) is True
    assert record.__dict__["suppressed"] == 2

    a_minute_passes(bounded)
    del record.__dict__["suppressed"]
    assert bounded.filter(record) is True
    assert "suppressed" not in record.__dict__, "nothing was held back, so nothing is claimed"


def test_the_two_extra_keys_survive_the_allowlist_that_drops_everything_else() -> None:
    """`ExtraAdder` is deny-by-default, so a key nobody added to it vanishes.

    Which is right, and is why this is asserted rather than assumed: the
    suppression count reaching the record is the only reason the limit is
    honest, and it fails silently if the two names are not on that list.
    """
    with applied(logging_config("INFO", "json", security_rate="1/min")) as stream:
        logging.getLogger("django.security.X").error("refused", extra={"suppressed": 7, "invented": "no"})

    written = records(stream)[0]

    assert written["suppressed"] == 7
    assert "invented" not in written


# --------------------------------------------------------------------------
# The setting, refused the way every other one in this application is
# --------------------------------------------------------------------------


def test_a_rate_is_read_from_the_environment() -> None:
    config, _ = from_environment({"DJANGO_SECURITY_LOG_RATE": "4/hour"})

    assert config["filters"]["refusals"]["count"] == 4
    assert config["filters"]["refusals"]["window"] == 3600


def test_a_cleared_rate_is_the_default_one() -> None:
    """Decision 0022: emptying a variable is the same as deleting the line."""
    config, _ = from_environment({"DJANGO_SECURITY_LOG_RATE": "  "})

    assert (config["filters"]["refusals"]["count"], config["filters"]["refusals"]["window"]) == refusals.rate(
        DEFAULTS["DJANGO_SECURITY_LOG_RATE"]
    )


@pytest.mark.parametrize(
    "written",
    ["10", "10/fortnight", "ten/min", "/min", "10/", "10 per minute"],
)
def test_a_rate_this_does_not_understand_stops_the_process(written: str) -> None:
    with pytest.raises(ValueError, match="DJANGO_SECURITY_LOG_RATE"):
        refusals.rate(written)


@pytest.mark.parametrize("written", ["0/min", "-1/hour"])
def test_and_so_does_one_that_would_write_nothing_at_all(written: str) -> None:
    """The refusal `refusals.rate` argues for: there is no way to spell
    "write none", because the quiet that produces is indistinguishable from
    nothing having gone wrong.
    """
    with pytest.raises(ValueError, match="none at all"):
        refusals.rate(written)


@pytest.mark.parametrize(
    ("written", "expected"),
    [("1/s", (1, 1)), (" 25 / MIN ", (25, 60)), ("300/hour", (300, 3600)), ("9000/day", (9000, 86400))],
)
def test_the_rates_it_does_understand(written: str, expected: tuple[int, int]) -> None:
    """The same shape as the throttle rates in .env.sample, deliberately."""
    assert refusals.rate(written) == expected


# --------------------------------------------------------------------------
# The files that have to agree about it
# --------------------------------------------------------------------------

REPO_ROOT = Path(settings.REPO_ROOT)


def test_the_shipped_configurations_name_the_variable() -> None:
    """A limit nobody can find is one that gets worked around at the worst
    moment. Both files a deployment is configured from mention it.
    """
    assert "DJANGO_SECURITY_LOG_RATE=" in (REPO_ROOT / ".env.sample").read_text()
    assert "DJANGO_SECURITY_LOG_RATE: ${DJANGO_SECURITY_LOG_RATE:-" in (REPO_ROOT / "compose.yaml").read_text()
    assert "DJANGO_SECURITY_LOG_RATE" in (REPO_ROOT / "infra/helm/inventory-tng/templates/_helpers.tpl").read_text()
