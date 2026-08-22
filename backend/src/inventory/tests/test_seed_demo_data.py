"""Tests for the demo seed.

Its whole job is that a developer who has just migrated sees something, so the
first case asks the API the app asks and requires an answer with rows in it.
The rest are the two ways it could do harm -- an account nobody made, and
invented stock in a ledger that already holds real stock -- and the states a
developer's own database can already be in when it runs.
"""

import io
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from inventory.management.commands.seed_demo_data import CHECKED_OUT, HEADING, LABELS, PACKER, RECEIVED, SHELF, STORE
from inventory.models import Item, Location, StockMovement, StockTransaction, Volunteer
from inventory.tests import reports

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _a_development_server(settings: Any) -> None:
    """The command refuses unless DEBUG is on, and the test runner turns it off.

    Every case below is about what the command does when it agrees to run, so
    the condition is met once here; the one case about the refusal itself turns
    it back off.
    """
    settings.DEBUG = True


def run() -> str:
    """Seed, returning what the command printed."""
    out = io.StringIO()
    call_command("seed_demo_data", stdout=out)
    return out.getvalue()


def counts(printed: str) -> dict[str, int]:
    """The report's own figures, read back off the lines it printed.

    Through `reports.counts_in`, which is what the other two suites over a
    printed section read one with. A reader of its own here would be a third
    answer to one question: this one used to split on whitespace, which drops
    any row whose label is two words rather than reporting it.
    """
    lines = printed.split("\n")
    section = lines[lines.index(HEADING) + 1 :]
    return dict(reports.counts_in(section[: section.index("")]))


def test_every_list_the_app_reads_on_load_comes_back_with_rows_in_it(client: Client) -> None:
    """The reason this command exists: after it, no screen is empty.

    The three requests are the ones the volunteer app makes before anybody
    touches anything -- the catalogue, the pick-list, and the label map a scan
    resolves against -- and at least one catalogue row has stock behind it,
    because a list of zeroes is the emptiness this was meant to fix.
    """
    run()

    items = client.get(reverse("items")).json()["results"]
    assert client.get(reverse("volunteers")).json()["results"]
    assert client.get(reverse("labels")).json()
    assert [row for row in items if row["balances"]]


def test_it_creates_no_login() -> None:
    """The difference from seed_integration_data, and the reason this one needs
    no acknowledgement flag: it publishes no credential, so there is nothing to
    acknowledge.
    """
    run()

    assert not User.objects.exists()


def test_it_refuses_to_run_on_a_server_that_is_not_a_development_one(settings: Any) -> None:
    """It ships inside the backend image, and the catalogue half writes
    unconditionally into whatever database DATABASE_URL names.
    """
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DEBUG is off"):
        run()

    assert not Item.objects.exists()
    assert not Location.objects.exists()
    assert not Volunteer.objects.exists()


def test_running_it_again_adds_nothing() -> None:
    """A developer re-running the bootstrap script must not get a second
    catalogue and a second delivery.
    """
    run()

    assert set(counts(run()).values()) == {0}
    assert Volunteer.objects.filter(display_name=PACKER).count() == 1
    assert Location.objects.filter(name=SHELF).count() == 1
    assert StockTransaction.objects.count() == 2  # the receipt and the check out, once each


def test_a_retired_row_wearing_one_of_these_names_is_brought_back() -> None:
    """The name is taken, so no second row can be made alongside, and nothing
    the app lists shows a retired one. Stepping over it would seed a hole.
    """
    retired = Location.objects.create(name=STORE, kind=Location.Kind.WAREHOUSE, active=False)

    run()

    retired.refresh_from_db()
    assert retired.active


@pytest.mark.parametrize("settled", ["merged", "retired"])
def test_a_second_run_after_the_demo_volunteer_is_settled_posts_no_second_delivery(
    settled: str,
    volunteer: Volunteer,
) -> None:
    """The exact sequence the two documents invite, and an append-only ledger.

    `bootstrap-dev.sh`, then merge or retire `Demo Volunteer` -- which is the
    first thing `guides/administrator.md` teaches -- then `bootstrap-dev.sh`
    again, which DEVELOPERS.md says to run as often as you like. The seed then
    mints a fresh volunteer of that name, so a lookup scoped to the actor finds
    nothing and writes the delivery a second time, into two tables that refuse
    UPDATE and DELETE.
    """
    run()
    packer = Volunteer.objects.selectable().get(display_name=PACKER)
    if settled == "merged":
        packer.merged_into = volunteer
    else:
        packer.active = False
    packer.save()

    printed = run()

    assert StockTransaction.objects.count() == 2
    assert StockMovement.objects.count() == len(RECEIVED) + len(CHECKED_OUT)
    assert counts(printed)["transactions"] == 0
    assert counts(printed)["movements"] == 0


def test_a_retired_volunteer_of_that_name_is_stepped_over_rather_than_revived() -> None:
    """A display name is not unique, so the row that already holds it may be
    somebody else's, and the ledger refuses an actor the pick-list has dropped.
    """
    retired = Volunteer.objects.create(display_name=PACKER, active=False)

    run()

    assert Volunteer.objects.selectable().filter(display_name=PACKER).exclude(pk=retired.pk).exists()
    retired.refresh_from_db()
    assert not retired.active


def test_it_declines_to_invent_stock_on_top_of_a_ledger_it_did_not_write(
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """The ledger refuses UPDATE and DELETE, so a demo movement posted into a
    real one could never be taken out again.
    """
    theirs = StockTransaction.objects.create(actor=volunteer, kind=StockTransaction.Kind.RECEIPT)
    StockMovement.objects.create(transaction=theirs, item=item, quantity=Decimal(1), to_location=warehouse)

    printed = run()

    assert StockTransaction.objects.count() == 1
    assert "no stock was invented" in printed
    # And the half that is reversible still happened, so the run is not simply
    # refused: a developer with a ledger of their own still gets a catalogue.
    assert Item.objects.filter(name="LiteBeam AC Gen2").exists()


def test_the_codes_it_prints_are_the_labels_it_made(client: Client) -> None:
    """The command prints them for somebody to scan or type, so a code that
    drifted from the row it names would read as a scanner that finds nothing.
    """
    printed = run()

    for code, *_ in LABELS:
        assert code in printed
        assert client.get(reverse("label-resolve", args=[code])).status_code == 200
