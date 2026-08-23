"""That somebody can answer "why did that not work" without reproducing it.

The question inventory-tng-nb8.9 sets, and the reason this file is about
records rather than about coverage: a volunteer says an append did not work,
and the person answering has a collector and no phone. What has to be there is
the request, what it was refused for, and enough to find both again.

These read the stream a deployment writes, through the same configuration it
writes it with, so what is asserted is what a collector receives -- not what a
mock was told. Everything is held as JSON for the reason `test_logging` gives.
"""

import logging
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from inventory.models import Category, Item, Location, StockTransaction, Volunteer
from inventory.tests.helpers import applied, every_record, written_by

# The batch endpoint's own helpers rather than a second copy of them: the
# body shape is the serializer's, and two files maintaining it separately is
# one of them asserting an old shape after a change to the other.
from inventory.tests.test_batch_endpoint import batch, post
from inventory_tng.logs import logging_config

pytestmark = pytest.mark.django_db


def one_line(item: Item, warehouse: Location, custody: Location) -> dict[str, Any]:
    return {"item": item.pk, "quantity": "2", "from_location": warehouse.pk, "to_location": custody.pk}


# --------------------------------------------------------------------------
# Every request says it happened, and can be found again
# --------------------------------------------------------------------------


def test_every_request_writes_its_own_record(client: Client) -> None:
    """gunicorn's access line is written outside the instrumentation and can
    carry neither a route nor a trace id. This one is written inside it.
    """
    with applied(logging_config("INFO", "json")) as stream:
        client.get(reverse("healthz"))

    (finished,) = written_by(stream, "inventory.request")

    assert finished["event"] == "request finished"
    assert finished["status"] == 200
    assert finished["route"] == "api/healthz"
    assert finished["method"] == "GET"
    assert isinstance(finished["duration"], float)


def test_and_carries_an_id_every_other_record_of_that_request_shares(
    client: Client, volunteer: Volunteer, item: Item, warehouse: Location, custody: Location
) -> None:
    """The thing that makes "show me everything that happened while this went
    wrong" work when the trace was not sampled, which most are not.
    """
    with applied(logging_config("INFO", "json")) as stream:
        post(client, batch(volunteer, [one_line(item, warehouse, custody)]))

    recorded = every_record(stream)
    ids = {record["request_id"] for record in recorded}

    assert len(recorded) > 1, "the append writes one and the request writes another"
    assert len(ids) == 1
    assert next(iter(ids))


def test_and_does_not_leave_it_behind_for_the_next_request(client: Client) -> None:
    """A worker thread serves one request after another."""
    with applied(logging_config("INFO", "json")) as stream:
        client.get(reverse("healthz"))
        client.get(reverse("healthz"))
        logging.getLogger("inventory.afterwards").info("between requests")

    (stray,) = written_by(stream, "inventory.afterwards")
    ids = {record["request_id"] for record in written_by(stream, "inventory.request")}

    assert "request_id" not in stray
    assert len(ids) == 2, "two requests, two ids"


def test_an_administrator_is_a_surrogate_and_a_volunteer_is_nobody(editor: Client, client: Client) -> None:
    """A name on a record is the leak `redaction` exists to prevent, and an
    unauthenticated caller has no identifier here at all -- deliberately.
    """
    with applied(logging_config("INFO", "json")) as stream:
        editor.get(reverse("items"))

    (signed_in,) = written_by(stream, "inventory.request")

    assert isinstance(signed_in["user"], int)

    with applied(logging_config("INFO", "json")) as stream:
        client.logout()
        client.get(reverse("healthz"))

    (anonymous,) = written_by(stream, "inventory.request")

    assert "user" not in anonymous


# --------------------------------------------------------------------------
# A refused append, answered from the records alone
# --------------------------------------------------------------------------


def test_a_rejected_batch_says_what_was_wrong_and_not_what_was_typed(
    client: Client, volunteer: Volunteer, warehouse: Location, custody: Location
) -> None:
    """The acceptance criterion. Which fields, and how many lines -- never the
    messages, which DRF builds from the values submitted.
    """
    with applied(logging_config("INFO", "json")) as stream:
        response = post(
            client,
            batch(volunteer, [{"item": 987654, "quantity": "2", "from_location": warehouse.pk}]),
        )

    assert response.status_code == 400
    (rejected,) = written_by(stream, "inventory.views")

    assert rejected["event"] == "batch rejected"
    assert rejected["level"] == "warning"
    assert rejected["reason"] == ["movements"]
    assert rejected["lines"] == 1
    assert "987654" not in str(rejected), "the value submitted is what the response is for"


def test_a_batch_inconsistent_with_its_kind_says_which_kind_and_how_many(
    client: Client, volunteer: Volunteer, item: Item, warehouse: Location, custody: Location
) -> None:
    """A receipt brings stock in from outside, so a line that also takes it
    out of somewhere disagrees with the batch it is in.
    """
    with applied(logging_config("INFO", "json")) as stream:
        response = post(
            client,
            batch(
                volunteer,
                [one_line(item, warehouse, custody)],
                kind=StockTransaction.Kind.RECEIPT,
            ),
        )

    assert response.status_code == 409
    (inconsistent,) = written_by(stream, "inventory.views")

    assert inconsistent["event"] == "batch inconsistent"
    assert inconsistent["kind"] == StockTransaction.Kind.RECEIPT
    assert inconsistent["lines"] == 1


def test_a_recorded_batch_says_what_it_recorded_and_who_by_surrogate(
    client: Client, volunteer: Volunteer, item: Item, warehouse: Location, custody: Location
) -> None:
    with applied(logging_config("INFO", "json")) as stream:
        response = post(client, batch(volunteer, [one_line(item, warehouse, custody)]))

    assert response.status_code == 201
    (recorded,) = written_by(stream, "inventory.views")

    assert recorded["event"] == "batch recorded"
    assert recorded["lines"] == 1
    assert recorded["volunteer"] == volunteer.pk
    assert volunteer.display_name not in str(recorded), "the pick-list holds names; telemetry does not"


def test_a_replayed_batch_is_told_apart_from_a_recorded_one(
    client: Client, volunteer: Volunteer, item: Item, warehouse: Location, custody: Location
) -> None:
    """A client retrying is not a client succeeding twice, and a graph that
    could not tell them apart would report double the movement there was.
    """
    body = batch(volunteer, [one_line(item, warehouse, custody)], idempotency_key="a-key-the-cart-minted")
    post(client, body)

    with applied(logging_config("INFO", "json")) as stream:
        response = post(client, body)

    assert response.status_code == 200
    (replayed,) = written_by(stream, "inventory.views")

    assert replayed["event"] == "batch replayed"


# --------------------------------------------------------------------------
# The rest of what a person does
# --------------------------------------------------------------------------


def test_adding_somebody_to_the_pick_list_is_recorded_without_their_name(client: Client) -> None:
    with applied(logging_config("INFO", "json")) as stream:
        response = client.post(
            reverse("volunteers"),
            data={"display_name": "Ada Lovelace"},
            content_type="application/json",
        )

    assert response.status_code == 201
    (added,) = written_by(stream, "inventory.views")

    assert added["event"] == "volunteer added"
    assert "Ada" not in str(added)


def test_editing_a_catalogue_row_says_which_collection_and_which_fields(editor: Client, item: Item) -> None:
    with applied(logging_config("INFO", "json")) as stream:
        response = editor.patch(
            reverse("item-detail", args=[item.pk]),
            data={"name": "LiteBeam 5AC"},
            content_type="application/json",
        )

    assert response.status_code == 200
    (edited,) = written_by(stream, "inventory.views")

    assert edited["event"] == "row edited"
    assert edited["collection"] == "item"
    assert edited["reason"] == ["name"]
    assert "LiteBeam 5AC" not in str(edited), "which fields changed, not what to"


def test_adding_one_says_which_collection_too(editor: Client, category: Category) -> None:
    with applied(logging_config("INFO", "json")) as stream:
        response = editor.post(
            reverse("items"),
            data={"name": "Zip Ties", "category": category.pk},
            content_type="application/json",
        )

    assert response.status_code == 201
    (added,) = written_by(stream, "inventory.views")

    assert added["event"] == "row added"
    assert added["collection"] == "item"


def test_an_edit_names_only_fields_the_serializer_has(editor: Client, item: Item) -> None:
    """`reason` holds words chosen in this code, never words chosen by a caller.

    `sorted(request.data)` was the caller's keys. DRF ignores an unknown one,
    so a PATCH could carry text of any length and any content -- an address, a
    name -- through an allowlist that admits `reason` as a KEY and never looks
    at what is under it. `redaction.ALLOWED_LOG_KEYS` states the rule; this is
    the assertion of it.
    """
    with applied(logging_config("INFO", "json")) as stream:
        response = editor.patch(
            reverse("item-detail", args=[item.pk]),
            data={"name": "Anything", "ada.lovelace@example.invalid was here": 1},
            content_type="application/json",
        )

    assert response.status_code == 200, response.content
    (edited,) = [record for record in written_by(stream, "inventory.views") if record["event"] == "row edited"]

    assert edited["reason"] == ["name"], "a key the caller invented reached the record"


def test_a_refusal_that_is_not_about_a_field_names_no_message(client: Client) -> None:
    """The other shape of `ValidationError.detail`, asserted.

    Which two shapes there are, and what treating them alike cost, is written
    where the reading is done -- `VolunteerListCreateView.create`.
    """
    from rest_framework import serializers

    from inventory.views import VolunteerListCreateView

    # Raised past the serializer rather than by it, which is what produces the
    # list. DRF normalises anything a SERIALIZER refuses into a dict keyed by
    # field name -- `non_field_errors` included, which is its own word and was
    # always safe. The shape this is about arrives from a refusal made after
    # validation, where the detail is whatever was raised.
    def refuse(self: Any, serializer: Any) -> Any:
        raise serializers.ValidationError("Nobody called Ada Lovelace <ada@example.invalid> may be added.")

    view = VolunteerListCreateView
    original = view.perform_create
    view.perform_create = refuse
    try:
        with applied(logging_config("INFO", "json")) as stream:
            response = client.post(reverse("volunteers"), data={"display_name": "Ada"}, content_type="application/json")
    finally:
        view.perform_create = original

    assert response.status_code == 400, response.content
    (refused,) = [record for record in written_by(stream, "inventory.views") if record["event"] == "volunteer refused"]

    assert refused["reason"] == ["non_field"]
    assert "example.invalid" not in str(refused), "a validator's message carried a submitted value onto the record"


def test_a_session_too_old_to_change_things_says_so(stale: Client, item: Item) -> None:
    """`inventory.api.exception_handler` says why the refusal is written there."""
    with applied(logging_config("INFO", "json")) as stream:
        response = stale.patch(
            reverse("item-detail", args=[item.pk]),
            data={"name": "Anything"},
            content_type="application/json",
        )

    assert response.status_code == 403
    (refused,) = written_by(stream, "inventory.api")

    assert refused["event"] == "asked to sign in again"
    assert refused["route"] == "api/items/<int:pk>"


def test_and_asking_what_a_stale_session_may_do_refuses_nothing(stale: Client) -> None:
    """The finding that moved the record off the permission class.

    `/api/me` answers what a caller MAY do by running every permission class
    against a probe, so a class that wrote a record turned one page load by a
    stale administrator into four refusals of operations nobody attempted --
    and `inventory.refusals{reason=stale_session}` counted every one of them.
    A predicate asked hypothetically has to be able to answer without leaving
    a mark.
    """
    with applied(logging_config("INFO", "json")) as stream:
        response = stale.get(reverse("me"))

    assert response.status_code == 200
    assert response.json()["capabilities"]["edit_catalogue"] is False, "the probe did refuse, hypothetically"
    assert written_by(stream, "inventory.api") == [], "a question nobody asked was recorded as a refusal"


# --------------------------------------------------------------------------
# The runs nobody is watching
# --------------------------------------------------------------------------


def test_a_command_says_it_ran_and_what_it_counted(settings: Any) -> None:
    """`_telemetry.running` says what this is for."""
    import io

    from django.core.management import call_command

    # The command refuses to run outside a development server, which is
    # `_seeding`'s own guard and nothing to do with this.
    settings.DEBUG = True

    with applied(logging_config("INFO", "json")) as stream:
        call_command("seed_demo_data", stdout=io.StringIO())

    started, finished = written_by(stream, "inventory.commands")

    assert started["event"] == "command started"
    assert finished["event"] == "command finished"
    assert finished["command"] == "seed_demo_data"
    assert isinstance(finished["duration"], float)
    assert finished["counted"], "the figures it printed, without printing them twice"


def test_and_the_counter_it_keeps_can_actually_leave_the_process(
    substituted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half `_telemetry.running` was missing, and the reason it says so.

    Nothing starts an SDK in a process running a command -- `telemetry.start`
    is imported by the three things that serve -- so `inventory.command_runs`
    was added to a proxy instrument that discards, and the counter the whole
    module exists for had no series anywhere. The log half worked, which is
    why nothing noticed.

    Asserted against a reader rather than against a call, because "start was
    called" would have passed on a `PeriodicExportingMetricReader` whose first
    tick comes long after a command has exited.
    """
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from inventory.management.commands import _telemetry

    assert substituted is not None, "the fixture has substituted the exporters"
    reader = InMemoryMetricReader()
    monkeypatch.setattr("opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader", lambda *a, **k: reader)

    try:
        with _telemetry.running("a command nobody watched"):
            pass
    finally:
        PsycopgInstrumentor().uninstrument()
        DjangoInstrumentor().uninstrument()

    gathered = reader.get_metrics_data()
    counted = {
        (metric.name, (point.attributes or {}).get("outcome"))
        for resource in (gathered.resource_metrics if gathered else [])
        for scope in resource.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
    }

    assert ("inventory.command_runs", "finished") in counted, f"nothing left the process; got {counted}"


def test_and_says_so_when_it_does_not_finish() -> None:
    from inventory.management.commands import _telemetry

    with (
        applied(logging_config("INFO", "json")) as stream,
        pytest.raises(RuntimeError),
        _telemetry.running("a_command_that_fails"),
    ):
        raise RuntimeError("the failure a Job has to be able to report")

    failed = written_by(stream, "inventory.commands")[-1]

    assert failed["event"] == "command failed"
    assert "the failure a Job has to be able to report" in failed["exception"]


def test_a_request_nothing_resolved_still_says_which_request_it_was(client: Client) -> None:
    """A 404 never reaches `process_view`, so the route stays what it was
    bound as -- a word rather than the path somebody typed, which would make
    every wrong URL a series of its own.
    """
    with applied(logging_config("INFO", "json")) as stream:
        response = client.get("/api/nothing-here")

    assert response.status_code == 404
    (finished,) = written_by(stream, "inventory.request")

    assert finished["route"] == "unresolved"
    assert finished["request_id"]
