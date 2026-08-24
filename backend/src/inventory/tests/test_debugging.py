"""Who may have a request recorded in full, and what it costs when they do.

The flag this gates is not a convenience. Recording a request in full means
every span it produces, and with function-level tracing behind it that is
arbitrary work asked for by whoever sent the request. Taking the W3C sampled
bit at face value would put that behind one header on a request that needs no
credential, which is why `inventory_tng.debugging` exists at all.

So the assertions come in pairs: an unsigned, tampered, expired or absent token
changes nothing about what is recorded, and a valid one changes it exactly
once, within a limit, and says in a metric that it did.
"""

import time
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.core import signing
from django.core.management import call_command
from django.test import Client, RequestFactory

from inventory_tng import debugging, telemetry

REPO_ROOT = Path(settings.REPO_ROOT)


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """What reached the metric, without an SDK behind it."""
    recorded: list[tuple[str, dict[str, Any]]] = []

    class Counter:
        def add(self, amount: int, attributes: dict[str, Any]) -> None:
            recorded.append((attributes["outcome"], attributes))

    monkeypatch.setattr(debugging, "COUNTER", Counter())
    return recorded


# --------------------------------------------------------------------------
# The token
# --------------------------------------------------------------------------


def test_a_token_this_process_minted_is_honoured() -> None:
    assert debugging.minted(debugging.mint())


def test_two_tokens_are_metered_apart() -> None:
    """Each carries an id of its own, so one volunteer exhausting an allowance
    does not silence the next one's.
    """
    assert debugging.minted(debugging.mint()) != debugging.minted(debugging.mint())


@pytest.mark.parametrize(
    "presented",
    ["", "   ", "not-a-token", "abc:1wyD81:tampered", "ef2645596a9132b7:1wyD81:3Q8l4zUrsBZtNwkw8tkzO_bv"],
)
def test_and_anything_else_is_not(presented: str) -> None:
    """One answer for every way a token can fail: there is nothing useful to
    do differently about any of them, and telling them apart is a way to learn
    which by trying.
    """
    assert debugging.minted(presented) == ""


def test_a_token_older_than_its_life_stops_working(settings: Any) -> None:
    """Expiry is the whole of the revocation for something with no store
    behind it, so it has to be the timestamp and not a promise.
    """
    settings.DEBUG_TRACE_LIFETIME_SECONDS = 1
    token = debugging.mint()
    assert debugging.minted(token)

    time.sleep(1.1)

    assert debugging.minted(token) == ""


def test_a_token_signed_with_another_key_is_refused(settings: Any) -> None:
    """Rotating DJANGO_SECRET_KEY revokes every token there is, which is the
    only revocation worth having here.
    """
    token = debugging.mint()
    settings.SECRET_KEY = "a-different-key-entirely"

    assert debugging.minted(token) == ""


def test_a_signature_from_somewhere_else_in_this_application_is_not_one(settings: Any) -> None:
    """The salt is what stops a value signed for another purpose, with the
    same key, being presented here.
    """
    elsewhere = signing.TimestampSigner(salt="something.else").sign("beef")

    assert debugging.minted(elsewhere) == ""


# --------------------------------------------------------------------------
# What presenting one costs
# --------------------------------------------------------------------------


def test_a_valid_token_authorises_and_is_counted(counted: list[Any]) -> None:
    assert debugging.authorised(debugging.mint()) is True
    assert counted == [("honoured", {"outcome": "honoured"})]


def test_an_invalid_one_does_not_and_is_counted_separately(counted: list[Any]) -> None:
    """Counted apart from the honoured ones, which is what makes a climb in
    the refusals visible at all.
    """
    assert debugging.authorised("not-a-token") is False
    assert counted == [("refused", {"outcome": "refused"})]


def test_no_token_at_all_is_the_ordinary_case_and_costs_nothing(counted: list[Any]) -> None:
    """Every request that is not being debugged comes through here. A metric
    per request would be a measure of traffic filed under debugging.
    """
    assert debugging.authorised("") is False
    assert counted == []


def test_a_valid_token_runs_out(settings: Any, counted: list[Any]) -> None:
    """A leaked link must not be able to cost unbounded telemetry."""
    settings.DEBUG_TRACE_RATE = "3/min"
    token = debugging.mint()

    honoured = [debugging.authorised(token) for _ in range(5)]

    assert honoured == [True, True, True, False, False]
    assert [outcome for outcome, _ in counted] == ["honoured"] * 3 + ["throttled"] * 2


def test_and_the_next_token_still_has_its_own(settings: Any, counted: list[Any]) -> None:
    settings.DEBUG_TRACE_RATE = "1/min"
    spent = debugging.mint()

    assert debugging.authorised(spent) is True
    assert debugging.authorised(spent) is False
    assert debugging.authorised(debugging.mint()) is True


# --------------------------------------------------------------------------
# What the sampler does about it
# --------------------------------------------------------------------------


def sampled(sampler: Any, trace_id: int) -> bool:
    from opentelemetry.trace import SpanKind

    return sampler.should_sample(None, trace_id, "GET /api/items", SpanKind.SERVER).decision.is_sampled()


# A trace id the ratio sampler below drops, found by asking it. Fixed here so
# the assertions are about the token rather than about the dice.
DROPPED = 0xF0000000000000000000000000000000


def test_a_request_nobody_authorised_is_sampled_at_the_configured_rate() -> None:
    chosen = telemetry.sampler("parentbased_traceidratio", 0.0)

    assert sampled(chosen, DROPPED) is False


def test_and_one_that_proved_itself_is_recorded_whole() -> None:
    """The point of the token: the same request, the same rate, recorded --
    because somebody with a signed token asked for it.
    """
    chosen = telemetry.sampler("parentbased_traceidratio", 0.0)
    held = debugging._authorised.set(True)
    try:
        assert sampled(chosen, DROPPED) is True
    finally:
        debugging._authorised.reset(held)


def test_the_sampler_says_what_it_is() -> None:
    """A description a collector shows, and one somebody can recognise."""
    described = telemetry.sampler("parentbased_traceidratio", 0.25).get_description()

    assert described.startswith("Debuggable(")


# --------------------------------------------------------------------------
# Where the check happens, which is before Django
# --------------------------------------------------------------------------


def test_the_flag_is_set_before_the_application_is_called_and_cleared_after() -> None:
    """A Django middleware would run after the instrumentation's own, which is
    after the request's span has been created and sampled. Too late to decide.
    """
    seen: list[bool] = []

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        seen.append(debugging.debugging())
        return [b""]

    guarded = debugging.guarded(application)
    guarded({debugging.ENVIRON: debugging.mint()}, lambda status, headers: None)

    assert seen == [True]
    assert debugging.debugging() is False, "a flag that outlived its request would record somebody else's"


def test_and_it_is_cleared_even_when_the_request_fails() -> None:
    def exploding(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        raise RuntimeError("the failure a deployment has to be able to see")

    with pytest.raises(RuntimeError):
        debugging.guarded(exploding)({debugging.ENVIRON: debugging.mint()}, lambda status, headers: None)

    assert debugging.debugging() is False


def test_a_request_carrying_no_token_leaves_it_off() -> None:
    seen: list[bool] = []

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        seen.append(debugging.debugging())
        return [b""]

    debugging.guarded(application)(RequestFactory().get("/api/items").environ, lambda status, headers: None)

    assert seen == [False]


def served_asgi(scope: dict[str, Any]) -> list[bool]:
    """One ASGI request, and what the flag was during and after it.

    Driven with `asyncio.run` rather than an async test, so that asserting
    about the one entry point nothing here serves does not put a plugin in
    every contributor's environment.
    """
    import asyncio

    seen: list[bool] = []

    async def application(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen.append(debugging.debugging())

    async def serving() -> None:
        await debugging.guarded_asgi(application)(scope, None, None)
        seen.append(debugging.debugging())

    asyncio.run(serving())
    return seen


def test_the_other_shape_of_application_is_guarded_the_same_way() -> None:
    """Nothing here serves ASGI today, which is exactly why this is asserted:
    an entry point that silently lacks the guard the other one has is found by
    somebody switching servers during an incident.
    """
    scope = {"type": "http", "headers": [(b"x-debug-trace", debugging.mint().encode())]}

    assert served_asgi(scope) == [True, False]


def test_and_a_scope_that_is_not_a_request_carries_no_token() -> None:
    """A lifespan or a websocket scope has no request to record."""
    assert served_asgi({"type": "lifespan"}) == [False, False]


# --------------------------------------------------------------------------
# Minting one, and the settings behind it
# --------------------------------------------------------------------------


def test_the_command_prints_a_token_that_works() -> None:
    written = StringIO()

    call_command("mint_debug_token", stdout=written)
    token = written.getvalue().splitlines()[0]

    assert debugging.minted(token)
    assert debugging.HEADER in written.getvalue()


def test_and_that_it_says_it_handed_one_out() -> None:
    """Somebody now holds, for an hour, the ability to raise what this server
    records about them. A capability granted with nothing said about it is one
    nobody can account for afterwards -- and standard output is where the token
    goes, not where a record of it having been minted survives.
    """
    from inventory.tests.helpers import applied
    from inventory_tng.logs import logging_config

    with applied(logging_config("INFO", "json")) as stream:
        call_command("mint_debug_token", stdout=StringIO())

    assert "mint_debug_token" in stream.getvalue()


def test_a_rate_the_process_cannot_read_stops_it(settings: Any) -> None:
    """Refused the way every rate in this application is -- and named, so that
    somebody who mistyped one of the two knows which.
    """
    settings.DEBUG_TRACE_RATE = "sixty a minute"

    with pytest.raises(ValueError, match="DEBUG_TRACE_RATE"):
        debugging.allowance()


def test_no_configuration_here_makes_a_token_long_lived() -> None:
    """An hour is long enough to reproduce something over a call and short
    enough that a link in a chat channel is not a standing authority.
    """
    assert debugging.DEFAULT_LIFETIME_SECONDS == 3600
    assert "DEBUG_TRACE_LIFETIME_SECONDS=3600" in (REPO_ROOT / ".env.sample").read_text()
    assert 'debugTraceLifetimeSeconds: "3600"' in (REPO_ROOT / "infra/helm/inventory-tng/values.yaml").read_text()


def test_an_operator_switching_sampling_off_switches_it_off_for_tokens_too() -> None:
    """A token beats a rate, because a rate is a rate. It does not beat
    somebody deciding, during an incident, that this server records nothing.
    """
    chosen = telemetry.sampler("always_off", 1.0)
    held = debugging._authorised.set(True)
    try:
        assert sampled(chosen, DROPPED) is False
    finally:
        debugging._authorised.reset(held)


def test_a_token_minted_under_a_rotated_key_is_refused_despite_a_fallback(settings: Any) -> None:
    """`signer` says why the fallbacks are pinned out of this one."""
    token = debugging.mint()
    settings.SECRET_KEY = "a-brand-new-key"
    settings.SECRET_KEY_FALLBACKS = ["dev-only-not-for-deployment-change-me"]

    assert debugging.minted(token) == ""


@pytest.mark.parametrize("held", [0, -1])
def test_a_lifetime_that_mints_expired_tokens_stops_the_process(held: int) -> None:
    """It would otherwise be indistinguishable from somebody forging one."""
    with pytest.raises(ValueError, match="DEBUG_TRACE_LIFETIME_SECONDS"):
        debugging.lifetime(held)


def test_the_header_a_browser_has_to_send_is_one_the_preflight_admits() -> None:
    """A cross-origin request carrying a header CORS does not name is
    cancelled rather than stripped, so the documented browser flow would never
    have worked -- only the curl beside it.
    """
    assert debugging.HEADER.lower() in settings.CORS_ALLOW_HEADERS


# --------------------------------------------------------------------------
# The path a browser posts its spans to
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_signed_post_is_the_only_one_nginx_is_told_to_forward(client: Client) -> None:
    """nginx cannot check a Django signature, so it asks. Without this the
    collector's ingest path would be a write anybody could make.
    """
    from django.urls import reverse

    where = reverse("debug-trace")

    assert client.get(where, headers={debugging.HEADER.lower(): debugging.mint()}).status_code == 204
    assert client.get(where).status_code == 403
    assert client.get(where, headers={debugging.HEADER.lower(): "not-a-token"}).status_code == 403


@pytest.mark.django_db
def test_and_answering_it_does_not_spend_the_token_s_allowance(settings: Any) -> None:
    """The allowance bounds how much tracing one token may make this server
    do. A browser's exporter asking whether it may post is not that, and
    charging it would spend what the traced requests need.

    DRIVEN THROUGH THE WSGI APPLICATION, because that is what charges. This
    test read 204 five times through Django's `Client` and concluded the
    allowance was untouched -- and it was, in that arrangement, because the
    test client never goes near `inventory_tng.wsgi.application`. In a
    deployment the wrapper charged every one of them. `spends_the_allowance`
    says what that cost.
    """
    from django.test import RequestFactory
    from django.urls import reverse

    from inventory_tng import wsgi

    settings.DEBUG_TRACE_RATE = "5/min"
    token = debugging.mint()
    asking = RequestFactory().get(reverse("debug-trace"), headers={debugging.HEADER.lower(): token})

    for _ in range(5):
        wsgi.application(asking.environ, lambda status, headers: None)

    assert debugging.authorised(token) is True, "the allowance is untouched"
    assert debugging.authorised(token) is True
