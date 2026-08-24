"""Tests for the rate limit on the two endpoints that take no credential.

Why the limit exists is on inventory/throttling.py. What these assert is the
whole of its promise: appending stops, reading does not, the refusal says when
to come back, and the schema says so too.

The limits are moved around here rather than exercised at their real values: a
test that made twenty requests would assert the arithmetic of the default
instead of the behaviour of the limit.
"""

from typing import Any

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIRequestFactory

from inventory.models import Volunteer
from inventory.throttling import AppendBurstThrottle, AppendSustainedThrottle, AppendThrottle

pytestmark = pytest.mark.django_db

URL = reverse("volunteers")
BATCH_URL = reverse("stock-transactions")


@pytest.fixture
def limit(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Set one throttle's rate for the duration of a test.

    Assigning the rate the class does not otherwise carry: SimpleRateThrottle
    reads the configured one only when an instance has none.
    """

    def set_limit(throttle: type[AppendThrottle], rate: str) -> None:
        monkeypatch.setattr(throttle, "rate", rate, raising=False)

    return set_limit


def add(client: Client, name: str) -> Any:
    return client.post(URL, data={"display_name": name}, content_type="application/json")


def submit(client: Client, body: dict[str, Any]) -> Any:
    return client.post(BATCH_URL, data=body, content_type="application/json")


def test_appending_faster_than_the_limit_is_refused(client: Client, limit: Any) -> None:
    limit(AppendBurstThrottle, "2/min")

    assert add(client, "Sean").status_code == 201
    assert add(client, "Olga").status_code == 201
    refused = add(client, "Mikey")

    assert refused.status_code == 429
    assert Volunteer.objects.count() == 2, "the refused request must not have been recorded"


def test_the_refusal_says_when_to_come_back(client: Client, limit: Any) -> None:
    """A countdown a client can render, not a sentence it has to parse."""
    limit(AppendBurstThrottle, "1/min")
    add(client, "Sean")

    refused = add(client, "Olga")
    body = refused.json()

    assert body["code"] == "throttled"
    assert body["detail"]
    assert 0 < body["retry_after_seconds"] <= 60
    assert refused.headers["Retry-After"] == str(body["retry_after_seconds"])


def test_the_batch_endpoint_is_limited_before_it_reads_the_batch(client: Client, limit: Any) -> None:
    """A script sending rubbish is throttled on the same terms as a real cart.

    Limits are checked before the body is parsed, so the cheap way to make a
    lot of requests is not also the way around them.
    """
    limit(AppendBurstThrottle, "1/min")

    assert submit(client, {}).status_code == 400
    assert submit(client, {}).status_code == 429


def test_a_flood_slow_enough_to_pass_the_burst_limit_meets_the_hourly_one(client: Client, limit: Any) -> None:
    limit(AppendSustainedThrottle, "1/hour")

    assert add(client, "Sean").status_code == 201
    refused = add(client, "Olga")

    assert refused.status_code == 429
    assert refused.json()["retry_after_seconds"] > 60, "the hourly limit should be the one refusing"


def test_reading_the_pick_list_is_never_counted(client: Client, limit: Any) -> None:
    """Search runs as somebody types; a limit sized for submissions would break it."""
    limit(AppendBurstThrottle, "1/min")
    limit(AppendSustainedThrottle, "1/hour")

    for _ in range(4):
        assert client.get(URL).status_code == 200


def test_other_failures_keep_their_own_body(client: Client) -> None:
    """Only the throttled response is rewritten; DRF still answers for the rest."""
    response = add(client, "")

    assert response.status_code == 400
    assert "code" not in response.json()


def test_the_limits_come_from_the_environment() -> None:
    """A scope that names nothing in the settings would fail at the first request."""
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]

    assert AppendBurstThrottle().rate == rates["append-burst"]
    assert AppendSustainedThrottle().rate == rates["append-sustained"]


def operations() -> Any:
    return SchemaGenerator().get_schema(request=None, public=True)["paths"]


def test_the_schema_documents_the_throttled_response() -> None:
    paths = operations()

    for path in ("/api/volunteers", "/api/stock/transactions"):
        assert "429" in paths[path]["post"]["responses"], f"POST {path} can be throttled but does not say so"


def test_the_documented_body_is_the_one_that_is_sent() -> None:
    response = operations()["/api/volunteers"]["post"]["responses"]["429"]

    assert response["content"]["application/json"]["schema"]["$ref"].endswith("/Throttled")


def test_a_read_claims_it_can_be_throttled_only_where_something_counts_it() -> None:
    """The promise follows the throttle attached, never the method.

    Reads used to be exempt everywhere, so "a GET says nothing about 429" was
    a property of the whole API. It stopped being one when the pick-list took
    `AnonymousReadThrottle` -- provisional, off unless `ANONYMOUS_READ_RATE`
    names a rate, and `inventory-tng-81f7.1` is the argument for what it is
    actually for. The catalogue carries no read limit and must not claim one.
    """
    paths = operations()

    assert "429" in paths["/api/volunteers"]["get"]["responses"]
    assert "429" not in paths["/api/items"]["get"]["responses"]


@pytest.mark.parametrize(
    "forged",
    ["10.9.9.1", "10.9.9.1, 10.9.9.2", "evil, 10.9.9.1, 10.9.9.2, 10.9.9.3"],
)
def test_a_forged_forwarded_for_prefix_does_not_change_who_you_are(forged: str) -> None:
    """A forged prefix must not buy a fresh bucket; see .env.sample.

    Asserted against the throttle rather than through an endpoint because the
    address only identifies a caller once nobody is signed in, and the
    endpoints still require a session until decision 0012 is built.
    """
    real_client = "203.0.113.7"
    ingress = "10.42.0.1"
    request = APIRequestFactory().post(
        "/api/volunteers",
        headers={"x-forwarded-for": f"{forged}, {real_client}, {ingress}"},
        REMOTE_ADDR="10.42.0.9",
    )

    assert AppendBurstThrottle().get_ident(request) == real_client
