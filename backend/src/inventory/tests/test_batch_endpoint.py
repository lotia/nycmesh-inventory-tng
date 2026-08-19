"""Tests for POST /api/stock/transactions, the endpoint the project exists for.

These go through the API -- request in, response out -- rather than calling the
view, so they survive a refactoring of how the view is put together. The
behaviours under test are the ones decision 0011 argues for: one commit for the
whole batch, every bad line reported at once, and stock going negative recorded
rather than refused.
"""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    Category,
    Item,
    Location,
    StockMovement,
    StockTransaction,
    Volunteer,
)
from inventory.serializers import MAX_MOVEMENTS
from inventory.views import StockTransactionCreateView, _line_errors

pytestmark = pytest.mark.django_db

URL = reverse("stock-transactions")


@pytest.fixture
def client(client: Client) -> Client:
    """Authenticated, because the endpoint uses the project default.

    Which posture the deployed app actually takes is inventory-tng-0pj, and
    relaxing this is one line on the view.
    """
    client.force_login(User.objects.create_user(username="scanner", password="not-a-real-password"))
    return client


@pytest.fixture
def second_item(category: Category) -> Item:
    return Item.objects.create(name="Zip Ties", category=category)


def post(client: Client, body: dict[str, Any]) -> Any:
    return client.post(URL, data=body, content_type="application/json")


def batch(volunteer: Volunteer, movements: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    return {
        "kind": StockTransaction.Kind.CHECKOUT,
        "actor": volunteer.pk,
        "movements": movements,
        **overrides,
    }


# --------------------------------------------------------------------------
# Recording a batch
# --------------------------------------------------------------------------


def test_one_request_records_one_transaction_and_every_line(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    second_item: Item,
    warehouse: Location,
    custody: Location,
) -> None:
    """The whole point: the old form carried one item, so people submitted it
    over and over. Here twelve scans are one act.
    """
    response = post(
        client,
        batch(
            volunteer,
            [
                {
                    "item": item.pk,
                    "quantity": "2",
                    "from_location": warehouse.pk,
                    "to_location": custody.pk,
                },
                {
                    "item": second_item.pk,
                    "quantity": "100",
                    "from_location": warehouse.pk,
                    "to_location": custody.pk,
                },
            ],
            job_reference="NN217",
        ),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["job_reference"] == "NN217"
    assert len(body["movements"]) == 2
    assert StockTransaction.objects.count() == 1
    assert StockMovement.objects.count() == 2


def test_occurred_at_defaults_so_a_client_need_not_send_one(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    response = post(client, batch(volunteer, [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}]))
    assert response.status_code == 201
    assert response.json()["occurred_at"] is not None


def test_the_endpoint_requires_authentication(client: Client, volunteer: Volunteer) -> None:
    client.logout()
    assert post(client, batch(volunteer, [])).status_code in (401, 403)


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_replaying_a_key_returns_the_first_transaction_and_writes_nothing(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """A phone in a basement will retry. The key is minted when the cart opens,
    so every retry of that cart carries it.
    """
    body = batch(
        volunteer,
        [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
        idempotency_key="cart-7QK2P9",
    )

    first = post(client, body)
    second = post(client, body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert StockTransaction.objects.count() == 1
    assert StockMovement.objects.count() == 1


def test_a_replay_is_matched_without_looking_at_the_body(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    second_item: Item,
    warehouse: Location,
) -> None:
    """No hash of the body. Two carts under one key is an invisible client bug,
    and turning it into an error the volunteer cannot act on helps nobody.

    The actor is still part of the match -- see the tests below -- so "the key
    alone" means the key and who sent it, never the scans it carried.
    """
    first = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            idempotency_key="cart-7QK2P9",
        ),
    )
    second = post(
        client,
        batch(
            volunteer,
            [{"item": second_item.pk, "quantity": "99", "from_location": warehouse.pk}],
            idempotency_key="cart-7QK2P9",
        ),
    )

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert StockMovement.objects.count() == 1


@pytest.mark.parametrize(
    ("sent", "resent"),
    [
        ("cart-7QK2P9", "cart-7QK2P9"),
        ("cart-7QK2P9", "  cart-7QK2P9  "),
        (12345, 12345),
    ],
)
def test_a_replay_is_matched_on_the_key_as_it_is_stored(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    sent: object,
    resent: object,
) -> None:
    """The write path normalises the key, so the lookup must agree with it.

    It trims whitespace and coerces a JSON number, so a retry matched on the
    raw request value would miss its own row, collide with the unique index,
    and surface as a 500 -- the mechanism that makes a retry safe failing
    precisely on retry.
    """
    line = [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}]
    first = post(client, batch(volunteer, line, idempotency_key=sent))
    second = post(client, batch(volunteer, line, idempotency_key=resent))

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert StockTransaction.objects.count() == 1


# --------------------------------------------------------------------------
# Rejection: nothing saved, everything reported
# --------------------------------------------------------------------------


def test_three_bad_lines_are_all_reported_by_index_and_nothing_is_written(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """Validation must not stop at the first failure. A volunteer fixing 24
    scans one rejection at a time would give up, and rightly.
    """
    response = post(
        client,
        batch(
            volunteer,
            [
                {"item": item.pk, "quantity": "1", "from_location": warehouse.pk},
                {"item": item.pk, "quantity": "0", "from_location": warehouse.pk},
                {"item": 9999, "quantity": "1", "from_location": warehouse.pk},
                {"item": item.pk, "quantity": "1"},
            ],
        ),
    )

    assert response.status_code == 400
    errors = response.json()["errors"]
    assert [error["index"] for error in errors] == [1, 2, 3]
    assert {error["field"] for error in errors} == {"quantity", "item", "non_field_errors"}
    assert StockTransaction.objects.count() == 0
    assert StockMovement.objects.count() == 0


def test_a_problem_with_the_batch_itself_carries_a_null_index(
    client: Client,
    item: Item,
    warehouse: Location,
) -> None:
    response = post(
        client,
        {
            "kind": StockTransaction.Kind.CHECKOUT,
            "actor": 9999,
            "movements": [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
        },
    )

    assert response.status_code == 400
    assert response.json()["errors"] == [
        {"index": None, "field": "actor", "detail": 'Invalid pk "9999" - object does not exist.'},
    ]


def test_an_empty_batch_is_rejected(client: Client, volunteer: Volunteer) -> None:
    """Nothing was scanned, so there is nothing to record."""
    response = post(client, batch(volunteer, []))
    assert response.status_code == 400
    assert response.json()["errors"]


def test_a_line_cannot_start_and_end_in_the_same_place(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    response = post(
        client,
        batch(
            volunteer,
            [
                {
                    "item": item.pk,
                    "quantity": "1",
                    "from_location": warehouse.pk,
                    "to_location": warehouse.pk,
                },
            ],
        ),
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["index"] == 0


# --------------------------------------------------------------------------
# A batch that does not add up to the kind it claims
# --------------------------------------------------------------------------


def test_a_checkout_whose_line_takes_stock_from_nowhere_is_a_conflict(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    custody: Location,
) -> None:
    """Each line is valid on its own; together they are not a check out."""
    response = post(
        client,
        batch(
            volunteer,
            [
                {"item": item.pk, "quantity": "1", "from_location": warehouse.pk},
                {"item": item.pk, "quantity": "1", "to_location": custody.pk},
            ],
        ),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["kind"] == StockTransaction.Kind.CHECKOUT
    assert body["inconsistent"] == [{"index": 1, "detail": "has no from_location"}]
    assert StockTransaction.objects.count() == 0


def test_a_transfer_needs_both_sides_on_every_line(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            kind=StockTransaction.Kind.TRANSFER,
        ),
    )
    assert response.status_code == 409
    assert response.json()["inconsistent"] == [{"index": 0, "detail": "has no to_location"}]


def test_a_receipt_brings_stock_in_from_outside(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """from_location is absent by design: the vendor is outside the system."""
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "305", "to_location": warehouse.pk}],
            kind=StockTransaction.Kind.RECEIPT,
        ),
    )
    assert response.status_code == 201


def test_a_count_constrains_no_side(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """A stock count is how a volunteer says the shelf disagrees with the
    system, so it is the one kind that must never be argued with.
    """
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "to_location": warehouse.pk}],
            kind=StockTransaction.Kind.COUNT,
        ),
    )
    assert response.status_code == 201


def test_a_receipt_cannot_also_drain_a_location(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    custody: Location,
) -> None:
    """A receipt is stock arriving from outside the system.

    Naming a source as well drains a shelf that was never involved, and the
    ledger is append-only, so it stands until somebody notices.
    """
    response = post(
        client,
        batch(
            volunteer,
            [
                {
                    "item": item.pk,
                    "quantity": "100",
                    "from_location": warehouse.pk,
                    "to_location": custody.pk,
                },
            ],
            kind=StockTransaction.Kind.RECEIPT,
        ),
    )

    assert response.status_code == 409
    assert response.json()["inconsistent"] == [{"index": 0, "detail": "has a from_location"}]
    assert StockTransaction.objects.count() == 0


def test_consumption_cannot_arrive_anywhere(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    custody: Location,
) -> None:
    """Hardware fitted at an install has left the system."""
    response = post(
        client,
        batch(
            volunteer,
            [
                {
                    "item": item.pk,
                    "quantity": "1",
                    "from_location": warehouse.pk,
                    "to_location": custody.pk,
                },
            ],
            kind=StockTransaction.Kind.CONSUMPTION,
        ),
    )
    assert response.status_code == 409
    assert response.json()["inconsistent"] == [{"index": 0, "detail": "has a to_location"}]


def test_one_line_can_be_wrong_in_both_directions_at_once(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """A receipt with a source and no destination fails both halves, and the
    volunteer is told both rather than made to fix one and post again.
    """
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            kind=StockTransaction.Kind.RECEIPT,
        ),
    )
    assert response.status_code == 409
    assert response.json()["inconsistent"] == [
        {"index": 0, "detail": "has no to_location"},
        {"index": 0, "detail": "has a from_location"},
    ]


@pytest.mark.parametrize(
    ("kind", "sides"),
    [
        (StockTransaction.Kind.CHECKOUT, ("from_location", "to_location")),
        (StockTransaction.Kind.CHECKIN, ("from_location", "to_location")),
        (StockTransaction.Kind.CONSUMPTION, ("from_location",)),
        (StockTransaction.Kind.RECEIPT, ("to_location",)),
        (StockTransaction.Kind.TRANSFER, ("from_location", "to_location")),
    ],
)
def test_each_kind_accepts_the_shape_it_is_for(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    custody: Location,
    kind: str,
    sides: tuple[str, ...],
) -> None:
    """The permitted shape of every kind, pinned.

    Forbidding a side is not the default: a check out into somebody's custody
    names both, and that is the ordinary case. Adding a side to the wrong
    tuple would break one of these while every rejection test stayed green.
    """
    places = {"from_location": warehouse.pk, "to_location": custody.pk}
    movement = {"item": item.pk, "quantity": "1"} | {side: places[side] for side in sides}
    response = post(client, batch(volunteer, [movement], kind=kind))
    assert response.status_code == 201, response.content


# --------------------------------------------------------------------------
# Going negative
# --------------------------------------------------------------------------


def test_stock_going_negative_is_recorded_and_warned_about(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    custody: Location,
) -> None:
    """Refusing this is the mistake the sheet makes, and is why so much of that
    ledger is faked corrections. The shelf is the authority.
    """
    response = post(
        client,
        batch(
            volunteer,
            [
                {
                    "item": item.pk,
                    "quantity": "3",
                    "from_location": warehouse.pk,
                    "to_location": custody.pk,
                },
            ],
        ),
    )

    assert response.status_code == 201
    warnings = response.json()["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["item"] == item.pk
    assert warnings[0]["location"] == warehouse.pk
    assert Decimal(warnings[0]["balance"]) == Decimal("-3")
    assert StockMovement.objects.count() == 1


def test_a_batch_within_stock_warns_about_nothing(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    custody: Location,
) -> None:
    post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "10", "to_location": warehouse.pk}],
            kind=StockTransaction.Kind.RECEIPT,
        ),
    )
    response = post(
        client,
        batch(
            volunteer,
            [
                {
                    "item": item.pk,
                    "quantity": "4",
                    "from_location": warehouse.pk,
                    "to_location": custody.pk,
                },
            ],
        ),
    )
    assert response.status_code == 201
    assert response.json()["warnings"] == []


def test_a_replay_still_reports_that_stock_is_negative(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """The retry exists because the first response was lost.

    A volunteer who never saw "the shelf is negative" still needs telling, so
    the replay reads the balances again rather than answering with silence.
    """
    body = batch(
        volunteer,
        [{"item": item.pk, "quantity": "3", "from_location": warehouse.pk}],
        idempotency_key="cart-OVERDRAWN",
    )
    assert post(client, body).json()["warnings"]
    assert post(client, body).json()["warnings"]
    assert StockMovement.objects.count() == 1


def test_a_blank_idempotency_key_is_rejected_rather_than_stored(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """An empty string is not "no key": the unique index covers every non-NULL
    value, so a second blank batch would collide with the first.
    """
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            idempotency_key="",
        ),
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "idempotency_key"


def test_a_null_idempotency_key_records_an_ordinary_batch(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            idempotency_key=None,
        ),
    )
    assert response.status_code == 201


def test_a_key_alone_does_not_fetch_somebody_elses_batch(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """Matching without hashing the body means not comparing bodies, not
    accepting anything at all that carries a key. A bare key is not a retry of
    a cart -- it does not even say who is retrying.
    """
    post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            idempotency_key="cart-SHARED",
            note="private note",
        ),
    )
    response = post(client, {"idempotency_key": "cart-SHARED"})

    assert response.status_code == 400
    assert "private note" not in response.content.decode()


# --------------------------------------------------------------------------
# Two retries arriving at once
# --------------------------------------------------------------------------


def test_a_racing_retry_returns_the_transaction_the_winner_recorded(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both requests look up the key, both find nothing, both try to write.

    The unique index decides it. The loser must not answer with an error: the
    volunteer's batch is safely recorded, which is all they wanted to know.
    Simulated by blinding the lookup once, because two real requests cannot be
    interleaved from inside one test.
    """
    body = batch(
        volunteer,
        [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
        idempotency_key="cart-RACED",
    )
    winner = post(client, body)

    honest = StockTransactionCreateView._already_recorded
    blinded = iter([True])

    def blind_once(actor: Volunteer, key: str | None) -> StockTransaction | None:
        return None if next(blinded, False) else honest(actor, key)

    monkeypatch.setattr(StockTransactionCreateView, "_already_recorded", staticmethod(blind_once))
    loser = post(client, body)

    assert loser.status_code == 200
    assert loser.json()["id"] == winner.json()["id"]
    assert StockTransaction.objects.count() == 1


def test_one_volunteers_key_cannot_swallow_anothers_batch(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    second_item: Item,
    warehouse: Location,
) -> None:
    """The key is minted by the client, so two people can mint the same one.

    Matching across everybody would answer the second volunteer with the
    first's transaction and drop their scans on the floor -- silently, since
    a 200 carrying somebody else's batch looks exactly like a successful
    retry.
    """
    olivia = Volunteer.objects.create(display_name="Olivia")
    shared = "cart-7QK2P9"
    first = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            idempotency_key=shared,
            note="private note",
        ),
    )
    second = post(
        client,
        batch(
            olivia,
            [{"item": second_item.pk, "quantity": "9", "from_location": warehouse.pk}],
            idempotency_key=shared,
        ),
    )

    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["actor"] == olivia.pk
    assert "private note" not in second.content.decode()
    assert StockTransaction.objects.count() == 2
    assert StockMovement.objects.count() == 2


def test_an_integrity_error_that_is_not_the_key_is_not_swallowed(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry path absorbs one specific collision. Reporting any other
    integrity failure as a recorded batch would tell a volunteer their scans
    were saved when nothing was written.
    """

    def fail(batch_data: dict[str, Any]) -> None:
        raise IntegrityError("some other constraint")

    monkeypatch.setattr(StockTransactionCreateView, "_record", staticmethod(fail))
    with pytest.raises(IntegrityError):
        post(
            client,
            batch(
                volunteer,
                [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
                idempotency_key="cart-DOOMED",
            ),
        )


def test_an_unexpected_error_shape_is_still_a_readable_rejection() -> None:
    """A 400 the volunteer can read beats a 500 they cannot."""
    assert _line_errors("movements went wrong") == [
        {"index": None, "field": "movements", "detail": "movements went wrong"},
    ]


# --------------------------------------------------------------------------
# What the ledger will not accept
# --------------------------------------------------------------------------


def test_a_batch_cannot_have_happened_in_the_future(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """Append-only means a wrong timestamp can never be corrected, only
    compensated -- and it is the key every recent-activity view sorts by.
    """
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            occurred_at=(timezone.now() + datetime.timedelta(days=1)).isoformat(),
        ),
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "occurred_at"


def test_a_batch_recorded_after_the_fact_is_ordinary(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """Coming back from an install and writing up yesterday is the normal case."""
    response = post(
        client,
        batch(
            volunteer,
            [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}],
            occurred_at=(timezone.now() - datetime.timedelta(days=1)).isoformat(),
        ),
    )
    assert response.status_code == 201


def test_a_merged_volunteer_cannot_be_the_actor(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """Merging duplicates is the point; recording against a retired record
    would start the next generation of them.
    """
    duplicate = Volunteer.objects.create(display_name="Sean B", merged_into=volunteer)
    response = post(
        client,
        batch(volunteer, [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}], actor=duplicate.pk),
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "actor"


def test_an_inactive_volunteer_cannot_be_the_actor(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    retired = Volunteer.objects.create(display_name="Gone", active=False)
    response = post(
        client,
        batch(volunteer, [{"item": item.pk, "quantity": "1", "from_location": warehouse.pk}], actor=retired.pk),
    )
    assert response.status_code == 400


def test_an_absurdly_large_batch_is_refused_before_anything_is_written(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """A real cart is a couple of dozen scans. The cap only stops one request
    opening an unbounded write transaction against an append-only ledger.
    """
    line = {"item": item.pk, "quantity": "1", "from_location": warehouse.pk}
    response = post(client, batch(volunteer, [line] * (MAX_MOVEMENTS + 1)))
    assert response.status_code == 400
    assert StockTransaction.objects.count() == 0


def test_the_batch_is_json_not_a_form(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """A form encoding cannot carry an array of objects, so advertising it
    would publish a request shape guaranteed to fail.
    """
    response = client.post(URL, data={"kind": "checkout", "actor": volunteer.pk})
    assert response.status_code == 415


def test_recorded_lines_read_back_in_the_order_they_were_sent(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    second_item: Item,
    warehouse: Location,
) -> None:
    """The 400 and 409 bodies address lines by their submitted index, which
    teaches the client to correlate by position. Reading back must agree.
    """
    response = post(
        client,
        batch(
            volunteer,
            [
                {"item": second_item.pk, "quantity": "5", "from_location": warehouse.pk},
                {"item": item.pk, "quantity": "1", "from_location": warehouse.pk},
                {"item": second_item.pk, "quantity": "7", "from_location": warehouse.pk},
            ],
        ),
    )
    assert [line["quantity"] for line in response.json()["movements"]] == ["5.000", "1.000", "7.000"]


def test_a_body_that_is_not_an_object_is_a_readable_rejection(client: Client) -> None:
    """DRF names the problem better than a coerced empty body would: a list
    gets "expected a dictionary" rather than a list of missing fields.
    """
    response = client.post(URL, data=[], content_type="application/json")
    assert response.status_code == 400
    assert response.json()["errors"]
